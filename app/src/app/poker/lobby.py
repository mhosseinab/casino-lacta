"""Poker lobby — matchmaking, buy-in, leave, and rake (the ONLY place poker moves money).

This is the money/seat seam between the realtime table actor (``app.ws.poker``, S32/S34 —
which moves NO money) and the double-entry ledger (``app.wallet.ledger``, S3). It owns three
ledger touch-points and one matchmaking surface, and nothing else:

- **BUY-IN** — ``buy_in`` debits the player wallet (player → house) and records the seat's
  starting stack. A buy-in is chips leaving the wallet into play; the house account is the
  escrow counterparty (every movement has a real counterparty — spec §2.2).
- **LEAVE** — ``leave`` credits the seat's REMAINING stack back (house → player) and vacates
  the seat. The seat keeps its ``stackMinor`` after vacating so an idempotent re-send
  recomputes the identical amount + key and collapses on the ledger fence.
- **RAKE** — ``take_rake`` is the play-money economy SINK: a configurable cut of the pot,
  debited from the pot winner's wallet (player → house) via a balanced two-row entry.
  Rake-free is a config toggle that books nothing.
- **MATCHMAKING** — ``list_tables`` / ``join`` seat a player at a table for the requested
  stakes (find one with a free seat at those stakes, else open a new one), then buy in.

★ IDEMPOTENCY (poker). Every money op is deduped by the ledger's UNIQUE ``idempotency_key``
constraint (DIFFERENT from the bet loop's ``hash(betId, opType)`` — a poker round holds many
bets, so a single ``betId`` cannot key it). A re-send constructs the SAME key, so the op
applies exactly once and the re-send returns the ORIGINAL
:class:`~app.wallet.ledger.LedgerResult` (``replayed=True``). The key shape is op-dependent:

- **RAKE** keys on ``hash(tableId, handNo, seat, opType)`` — rake is genuinely once-per-hand,
  per-seat, and a hand has exactly one occupant per seat, so the hand number already
  disambiguates every rake op.
- **BUYIN / LEAVE** key on ``hash(tableId, seat, occupancyId, opType)`` (see
  :func:`poker_seat_op_key`). These are LIFECYCLE events that span the matchmaking timeline: a
  seat is vacated and later re-taken by a DIFFERENT player. Keying them on ``handNo`` would let
  two distinct occupants of one seat (at the same, never-advanced ``handNo``) collide — the
  second player's BUYIN would idempotently REPLAY the first's debit and seat them with a full
  stack having paid NOTHING (a money-printing exploit). So buy-in/leave fold in a
  monotonic-per-seat ``occupancyId`` (minted on each new sit-down, REUSED by a legitimate retry
  of the SAME occupant), guaranteeing two different occupants of one seat can never share a key
  while a genuine re-send still collapses to the original.

Recoverable buy-in saga. The seat (carrying its ``occupancyId``) is reserved BEFORE the debit;
the debit is a SEPARATE transaction (the ledger owns its own). A crash between them is healed
by a retry: it reuses the same reserved ``occupancyId`` → the debit replays (never double-
charges) → the seat is already recorded. An ``InsufficientFunds`` debit rolls the reservation
back (seat vacated, stack zeroed) so a player who could not pay holds no seat and no
creditable stack.

Iron rules honoured: money is integer minor units (rake is integer floor math, never a
float); credits move ONLY through the ledger (the seat roster in ``PokerTable.seats`` is a
chip count, NOT a wallet balance — no projection is ever mutated here); double-entry +
idempotency are delegated to the ledger.

Scoped seams (intentional, deferred — NOT bugs):
- Rake is a STANDALONE economy-sink debit attributed to the pot winner's seat. Wiring the
  hand result → per-seat stack → rake into one conservation-closed game loop (so buy-ins,
  leaves, and rake net out exactly) is a later step; here each op is an independent balanced
  ledger movement (so ``Σ entries == 0`` holds regardless).
- The lobby roster reuses ``PokerTable.seats`` (JSONB). ``app.ws.poker.PostgresStateStore``
  also checkpoints into that column; co-using ONE ``table_id`` across both is a known seam
  tension out of S33's scope (the actor's starting stacks come FROM this roster).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import PokerTable
from app.wallet import InsufficientFunds, Ledger, LedgerResult

# Op types for the poker idempotency key (the only valid third hash component besides the
# table/hand/seat identity). Kept as constants so a typo can't silently mint a fresh key.
OP_BUYIN = "BUYIN"
OP_LEAVE = "LEAVE"
OP_RAKE = "RAKE"

_DEFAULT_MAX_SEATS = 6
_BPS_DENOMINATOR = 10_000  # basis points: rate_bps / 10_000 = fraction


class LobbyError(Exception):
    """Base class for lobby-domain failures."""


class TableFull(LobbyError):
    """No free seat at the requested stakes and a new table could not seat the player."""


class SeatTaken(LobbyError):
    """The requested seat is already occupied by a different user."""


class NotSeated(LobbyError):
    """``leave`` was called for a seat the user does not occupy."""


# --------------------------------------------------------------------------- config / value types
@dataclass(frozen=True)
class Stakes:
    """A table's blind structure, in integer minor units. Value object → comparable, so two
    join requests at the same stakes match the same pool of tables."""

    small_blind_minor: int
    big_blind_minor: int

    def as_json(self) -> dict[str, int]:
        return {"smallBlindMinor": self.small_blind_minor, "bigBlindMinor": self.big_blind_minor}

    @classmethod
    def try_from_json(cls, data: dict[str, int] | None) -> Stakes | None:
        """Parse a ``PokerTable.stakes`` blob into lobby stakes, or ``None`` if the row is
        not lobby-shaped (the column is co-owned with ``app.ws.poker``'s checkpoint, which
        writes ``{smallBlind, bigBlind}`` — a documented seam tension; we ignore those rows)."""
        if not data or "smallBlindMinor" not in data or "bigBlindMinor" not in data:
            return None
        return cls(
            small_blind_minor=int(data["smallBlindMinor"]),
            big_blind_minor=int(data["bigBlindMinor"]),
        )


@dataclass(frozen=True)
class RakeConfig:
    """Rake POLICY — CONFIG, never a literal buried in logic (rate, cap, rake-free toggle).

    ``rate_bps`` is basis points (500 = 5%); ``cap_minor`` caps the rake per pot in minor
    units (0 = uncapped). ``rake_free`` short-circuits to zero (the play-money rake-free
    option). Rake is integer floor math — the truncated fraction is NOT taken (floored
    toward the player), mirroring the engine's floor-on-payout discipline; no float ever.
    """

    rate_bps: int
    cap_minor: int
    rake_free: bool = False

    def rake_for(self, pot_minor: int) -> int:
        """The rake for a pot, in minor units: ``floor(pot * rate_bps / 10_000)`` then cap."""
        if self.rake_free or self.rate_bps <= 0 or pot_minor <= 0:
            return 0
        raw = (pot_minor * self.rate_bps) // _BPS_DENOMINATOR  # integer floor
        if self.cap_minor > 0:
            return min(raw, self.cap_minor)
        return raw


# A sensible default; a rake-free table is ``RakeConfig(0, 0, rake_free=True)``.
DEFAULT_RAKE = RakeConfig(rate_bps=500, cap_minor=300)


@dataclass(frozen=True)
class SeatAssignment:
    """A player's seat at a table after a buy-in: the seat, its monotonic-per-seat occupancy
    id (the disambiguator in the buy-in/leave idempotency key), its starting stack, and the
    underlying balanced ledger op."""

    table_id: str
    seat: int
    user_id: str
    occupancy_id: int
    stack_minor: int
    ledger: LedgerResult


@dataclass(frozen=True)
class RakeResult:
    """The outcome of a rake op: the amount taken and its ledger op (``None`` when rake-free
    or the computed rake is zero — nothing is booked)."""

    rake_minor: int
    ledger: LedgerResult | None


@dataclass(frozen=True)
class TableInfo:
    """A lobby listing: a table id, its stakes, and the seats currently occupied."""

    table_id: str
    stakes: Stakes
    occupied_seats: tuple[int, ...]
    max_seats: int


# --------------------------------------------------------------------------- the lobby
def poker_op_key(table_id: str, hand_no: int, seat: int, op_type: str) -> str:
    """The hand-scoped poker idempotency key — ``hash(tableId, handNo, seat, opType)`` (spec
    §2.2 / S33). Used for **RAKE**: a hand has one occupant per seat, so the hand number alone
    disambiguates every rake op. Deterministic in its four fields, so a re-send collapses onto
    the SAME key and the ledger's UNIQUE fence applies it exactly once. Distinct from the bet
    loop's ``hash(betId, opType)`` (a poker round holds many bets — no single ``betId``)."""
    return hashlib.sha256(f"{table_id}:{hand_no}:{seat}:{op_type}".encode()).hexdigest()


def poker_seat_op_key(table_id: str, seat: int, occupancy_id: int, op_type: str) -> str:
    """The OCCUPANCY-scoped idempotency key for a seat's lifecycle ops — ``hash(tableId, seat,
    occupancyId, opType)``. Used for **BUYIN / LEAVE**.

    Buy-in/leave span the matchmaking timeline (a vacated seat is later re-taken by a DIFFERENT
    player), so they must NOT key on the hand number: two occupants of one seat at the same,
    never-advanced ``handNo`` would collide and the second's BUYIN would idempotently REPLAY
    the first's debit → a seat funded for free. Folding in a monotonic-per-seat ``occupancyId``
    (minted on each new sit-down, REUSED by a legitimate retry of the SAME occupant) makes the
    key unique per occupancy: a re-send still collapses to the original, but a new occupant
    always gets a distinct key. Deterministic (no clock/random)."""
    return hashlib.sha256(
        f"{table_id}:seat:{seat}:occ:{occupancy_id}:{op_type}".encode()
    ).hexdigest()


class Lobby:
    """Server-authoritative matchmaking + the three poker money ops, bound to a session
    factory and the double-entry :class:`~app.wallet.ledger.Ledger`.

    Seat assignment and buy-in/leave/rake are decided server-side; the client sends only
    intent. The seat ROSTER (who sits where + each seat's chip stack) lives in the
    ``PokerTable.seats`` JSONB column — a chip count, not a wallet balance — while every
    credit movement goes through the ledger (the single owner of money). ``rake`` and
    ``max_seats`` are config, injected here.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        ledger: Ledger,
        *,
        rake: RakeConfig = DEFAULT_RAKE,
        max_seats: int = _DEFAULT_MAX_SEATS,
    ) -> None:
        self._sf = session_factory
        self._ledger = ledger
        self._rake = rake
        self._max_seats = max_seats

    # -- matchmaking ------------------------------------------------------- #
    async def list_tables(self, *, stakes: Stakes | None = None) -> list[TableInfo]:
        """All tables (optionally filtered to one stakes level) with their occupied seats."""
        async with self._sf() as session:
            rows = (await session.scalars(select(PokerTable))).all()
        out: list[TableInfo] = []
        for row in rows:
            row_stakes = Stakes.try_from_json(row.stakes)
            if row_stakes is None:
                continue  # foreign-shaped row (WS checkpoint) — not a lobby table
            if stakes is not None and row_stakes != stakes:
                continue
            out.append(
                TableInfo(
                    table_id=row.id,
                    stakes=row_stakes,
                    occupied_seats=tuple(sorted(_occupied(row.seats))),
                    max_seats=self._max_seats,
                )
            )
        return out

    async def join(
        self, *, user_id: str, wallet_id: str, stakes: Stakes, buy_in_minor: int
    ) -> SeatAssignment:
        """Seat a player at a table for ``stakes`` and buy in. Matchmaking: reuse the seat
        the user already holds (idempotent — re-buy collapses on the ledger fence), else take
        the lowest free seat at an existing matching table, else open a new table. Raises
        :class:`TableFull` only if every matching table is full and a fresh table cannot be
        opened (never, with a positive ``max_seats``)."""
        table_id, seat, hand_no = await self._assign_seat(user_id, stakes)
        return await self.buy_in(
            table_id=table_id,
            hand_no=hand_no,
            seat=seat,
            user_id=user_id,
            wallet_id=wallet_id,
            amount_minor=buy_in_minor,
        )

    async def _assign_seat(self, user_id: str, stakes: Stakes) -> tuple[str, int, int]:
        """Pick ``(table_id, seat, hand_no)`` for a join: existing seat for the user, else the
        lowest free seat at a matching table, else a brand-new table. Serialized per matching
        table row under ``FOR UPDATE`` so two joins never claim the same seat."""
        async with self._sf() as session, session.begin():
            rows = (await session.scalars(select(PokerTable).with_for_update())).all()
            matching = [row for row in rows if Stakes.try_from_json(row.stakes) == stakes]
            for row in matching:
                roster = dict(row.seats or {})
                for seat_str, info in roster.items():
                    if not info.get("vacated", False) and info.get("userId") == user_id:
                        return row.id, int(seat_str), row.hand_no  # already seated → reuse
            for row in matching:
                free = _lowest_free_seat(row.seats, self._max_seats)
                if free is not None:
                    return row.id, free, row.hand_no
            # No table with a free seat → open a new one (empty roster, hand 0).
            new_id = f"poker-{stakes.small_blind_minor}-{stakes.big_blind_minor}-{_short_id()}"
            session.add(PokerTable(id=new_id, stakes=stakes.as_json(), seats={}, hand_no=0))
        return new_id, 0, 0

    # -- buy-in ------------------------------------------------------------ #
    async def buy_in(
        self,
        *,
        table_id: str,
        hand_no: int,
        seat: int,
        user_id: str,
        wallet_id: str,
        amount_minor: int,
    ) -> SeatAssignment:
        """Debit the buy-in (player → house) ONCE and seat the player with that starting stack.

        Three steps, each its own transaction, ordered so EVERY failure direction is safe (fail
        toward the house, never toward the player):

        1. **Reserve** the seat with an ``occupancyId`` and a ZERO stack — occupied (blocks the
           seat) but NOT yet creditable. This persists the id BEFORE the debit, so the buy-in
           key is occupancy-unique: a re-send by the SAME occupant reuses the id and replays the
           ORIGINAL debit (no second movement), while a DIFFERENT player taking the same
           (vacated) seat mints a new id and is genuinely debited.
        2. **Debit** the stake. ``InsufficientFunds`` rolls the reservation back (vacated, stack
           0) — no stranded unpaid seat.
        3. **Finalize** the seat to its real starting stack only AFTER the debit confirms.

        A crash between (1) and (2) leaves a zero-stack seat → a ``leave`` credits nothing; a
        crash between (2) and (3) leaves a paid-but-zero-stack seat (player's chips stranded in
        house escrow, the SAFE direction). Either heals on a retry: the same ``occupancyId`` is
        reused → the debit replays (never double-charges) → finalize sets the stack."""
        occupancy_id = await self._reserve_seat(table_id, seat, user_id, wallet_id)
        key = poker_seat_op_key(table_id, seat, occupancy_id, OP_BUYIN)
        try:
            result = await self._ledger.debit(
                wallet_id=wallet_id,
                amount_minor=amount_minor,
                idempotency_key=key,
                ref=f"poker:buyin:{table_id}:{hand_no}:{seat}:{occupancy_id}",
            )
        except InsufficientFunds:
            await self._release_unpaid_seat(table_id, seat, occupancy_id)
            raise
        await self._finalize_seat(table_id, seat, occupancy_id, amount_minor)
        return SeatAssignment(
            table_id=table_id,
            seat=seat,
            user_id=user_id,
            occupancy_id=occupancy_id,
            stack_minor=amount_minor,
            ledger=result,
        )

    async def _reserve_seat(
        self, table_id: str, seat: int, user_id: str, wallet_id: str
    ) -> int:
        """Reserve the seat for this occupant (creating the table row if absent) with a ZERO,
        not-yet-creditable stack, and return its occupancy id. A seat currently held by the SAME
        user reuses its id (a re-send / a recovery retry); an empty or vacated seat mints a
        strictly greater id than the prior occupant's, so two distinct sit-downs at one seat
        never share a buy-in/leave key. A seat held by a DIFFERENT user raises :class:`SeatTaken`.
        Persisting BEFORE the debit (with a zero stack) is what makes the buy-in saga both
        recoverable and safe — an unconfirmed reservation can never be left for a credit."""
        async with self._sf() as session, session.begin():
            row = await session.get(PokerTable, table_id, with_for_update=True)
            if row is None:
                row = PokerTable(id=table_id, stakes=None, seats={}, hand_no=0)
                session.add(row)
            roster = dict(row.seats or {})
            existing = roster.get(str(seat))
            if existing is not None and not existing.get("vacated", False):
                if existing.get("userId") != user_id:
                    raise SeatTaken(f"seat {seat} at table {table_id} is occupied")
                return int(existing["occupancyId"])  # same occupant → reuse id (re-send)
            occupancy_id = int(existing["occupancyId"]) + 1 if existing is not None else 0
            roster[str(seat)] = {
                "userId": user_id,
                "walletId": wallet_id,
                "stackMinor": 0,  # not creditable until the debit confirms (finalize)
                "vacated": False,
                "paid": False,  # set once at finalize; leave credits only a paid seat
                "occupancyId": occupancy_id,
            }
            row.seats = roster
            return occupancy_id

    async def _release_unpaid_seat(self, table_id: str, seat: int, occupancy_id: int) -> None:
        """Roll back a seat reserved for a buy-in whose debit FAILED (``InsufficientFunds``):
        vacate it and ZERO its stack — so it frees up and can never be ``leave``-d for an
        un-bought credit — while RETAINING its occupancy id so the next occupant still mints a
        strictly greater one."""
        async with self._sf() as session, session.begin():
            row = await session.get(PokerTable, table_id, with_for_update=True)
            if row is None:  # pragma: no cover - reservation just proved the row exists
                return
            roster = dict(row.seats or {})
            info = roster.get(str(seat))
            if info is None or int(info.get("occupancyId", -1)) != occupancy_id:  # pragma: no cover
                return
            info = dict(info)
            info["vacated"] = True
            info["stackMinor"] = 0
            roster[str(seat)] = info
            row.seats = roster

    async def _finalize_seat(
        self, table_id: str, seat: int, occupancy_id: int, stack_minor: int
    ) -> None:
        """Set the seat's real starting stack AFTER its buy-in debit confirms (step 3 of the
        saga) — the point at which the seat becomes creditable (``paid``). Fires EXACTLY ONCE:
        an already-paid seat is a no-op, so a replayed buy-in cannot resurrect a stack that
        ``set_stack`` has since diminished (the credited amount stays the in-play stack, never
        the original buy-in). Guarded to the exact occupancy so a stale finalize can never
        touch a seat a different occupant now holds."""
        async with self._sf() as session, session.begin():
            row = await session.get(PokerTable, table_id, with_for_update=True)
            if row is None:  # pragma: no cover - reservation just proved the row exists
                return
            roster = dict(row.seats or {})
            info = roster.get(str(seat))
            if (
                info is None
                or info.get("vacated", False)
                or info.get("paid", False)  # already finalized → no-op (don't clobber the stack)
                or int(info.get("occupancyId", -1)) != occupancy_id
            ):
                return
            info = dict(info)
            info["paid"] = True
            info["stackMinor"] = stack_minor
            roster[str(seat)] = info
            row.seats = roster

    async def set_stack(self, *, table_id: str, seat: int, stack_minor: int) -> None:
        """Adjust a seat's chip stack (the hand-result hook: wins/losses change the stack a
        later ``leave`` returns). A chip count, NOT a wallet balance — no ledger movement."""
        async with self._sf() as session, session.begin():
            row = await session.get(PokerTable, table_id, with_for_update=True)
            if row is None:
                raise NotSeated(f"no table {table_id}")
            roster = dict(row.seats or {})
            info = roster.get(str(seat))
            if info is None:
                raise NotSeated(f"seat {seat} at table {table_id} is empty")
            info = dict(info)
            info["stackMinor"] = stack_minor
            roster[str(seat)] = info
            row.seats = roster

    # -- leave ------------------------------------------------------------- #
    async def leave(
        self, *, table_id: str, hand_no: int, seat: int, user_id: str
    ) -> LedgerResult | None:
        """Credit the seat's REMAINING stack back (house → player) ONCE and vacate the seat.

        The seat is marked ``vacated`` but KEEPS its ``stackMinor`` AND its ``occupancyId``, so
        a re-send recomputes the identical credit amount + occupancy-scoped key and collapses on
        the ledger fence (``replayed=True``) — never a second credit, and never colliding with a
        DIFFERENT occupant of the same seat. A busted player (remaining stack ``0``) is routine:
        the seat is vacated and NO ledger row is booked (returns ``None`` — the ledger only
        moves positive amounts). ``hand_no`` is retained for the audit ``ref`` only; the key is
        occupancy-scoped (lifecycle, not per-hand)."""
        async with self._sf() as session:
            row = await session.get(PokerTable, table_id)
            if row is None:
                raise NotSeated(f"no table {table_id}")
            info = dict(row.seats or {}).get(str(seat))
        if info is None:
            raise NotSeated(f"seat {seat} at table {table_id} is empty")
        if info.get("userId") != user_id:
            raise NotSeated(f"user {user_id} does not occupy seat {seat} at table {table_id}")

        occupancy_id = int(info.get("occupancyId", 0))
        # An UNPAID reservation (buy-in debit not confirmed) is never creditable — vacate it
        # without a credit (also covers a busted player's zero stack via the check below).
        remaining = int(info.get("stackMinor", 0)) if info.get("paid", False) else 0
        if remaining <= 0:
            await self._vacate_seat(table_id, seat)
            return None
        key = poker_seat_op_key(table_id, seat, occupancy_id, OP_LEAVE)
        result = await self._ledger.credit(
            wallet_id=str(info["walletId"]),
            amount_minor=remaining,
            idempotency_key=key,
            ref=f"poker:leave:{table_id}:{hand_no}:{seat}:{occupancy_id}",
        )
        await self._vacate_seat(table_id, seat)
        return result

    async def _vacate_seat(self, table_id: str, seat: int) -> None:
        """Mark the seat vacated, RETAINING its stack so a replayed leave stays recomputable."""
        async with self._sf() as session, session.begin():
            row = await session.get(PokerTable, table_id, with_for_update=True)
            if row is None:  # pragma: no cover - leave already proved the row exists
                return
            roster = dict(row.seats or {})
            info = roster.get(str(seat))
            if info is None:  # pragma: no cover
                return
            info = dict(info)
            info["vacated"] = True
            roster[str(seat)] = info
            row.seats = roster

    # -- rake -------------------------------------------------------------- #
    async def take_rake(
        self, *, table_id: str, hand_no: int, seat: int, wallet_id: str, pot_minor: int
    ) -> RakeResult:
        """Book the configured rake on a pot as an economy SINK (player → house), ONCE.

        The rake is ``floor(pot * rate) capped`` in minor units, debited from the pot winner's
        wallet at ``seat``. A rake-free config (or a zero computed rake) books NOTHING and
        returns ``ledger=None``. A re-send replays the ORIGINAL debit (``replayed=True``)."""
        rake_minor = self._rake.rake_for(pot_minor)
        if rake_minor <= 0:
            return RakeResult(rake_minor=0, ledger=None)
        key = poker_op_key(table_id, hand_no, seat, OP_RAKE)
        result = await self._ledger.debit(
            wallet_id=wallet_id,
            amount_minor=rake_minor,
            idempotency_key=key,
            ref=f"poker:rake:{table_id}:{hand_no}:{seat}",
        )
        return RakeResult(rake_minor=rake_minor, ledger=result)


# --------------------------------------------------------------------------- roster helpers
def _occupied(seats: dict[str, object] | None) -> list[int]:
    """Seat ids of CURRENTLY seated (non-vacated) players."""
    out: list[int] = []
    for seat_str, info in (seats or {}).items():
        if isinstance(info, dict) and not info.get("vacated", False):
            out.append(int(seat_str))
    return out


def _lowest_free_seat(seats: dict[str, object] | None, max_seats: int) -> int | None:
    """The lowest seat index not currently occupied, or ``None`` if the table is full."""
    taken = set(_occupied(seats))
    for seat in range(max_seats):
        if seat not in taken:
            return seat
    return None


def _short_id() -> str:
    """A short unique suffix for a new table id (non-cryptographic; uniqueness only)."""
    from uuid import uuid4

    return uuid4().hex[:12]
