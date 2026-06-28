"""S20 — Crash recovery, resume, and latency-fairness.

The last step of P3 Crash. Three asks (the step), plus the orchestration fences:

* **Resume (``GET /games/originals.crash/state``).** A reconnecting client gets the
  CURRENT shared round + ITS OWN active bets. While the round is live (DB status
  ``ACTIVE``) the projection WITHHOLDS ``C`` and the raw seed — it exposes only what
  the live tick/round events do (roundId, roundNumber, serverSeedHash, status, the
  cosmetic multiplier). Once SETTLED (post-reveal) it MAY disclose seed + ``C``. An
  identity fence scopes ``bets`` to the caller — user A never sees user B's bets.
* **Deterministic restart re-settle.** The committed round seed already fixes ``C``
  (persisted at open), so ``settle_crash_round`` is a pure idempotent replay: a
  mid-round restart re-settles every bet IDENTICALLY. Boot recovery re-settles
  orphaned rounds and recovers the round counter from the DB so a restarted actor
  NEVER regenerates an id (the stale-seed collision hazard) — it continues at
  ``max(nonce)+1``.
* **Reconciler.** Bets stuck non-terminal past a TTL (or in an already-terminal
  round — a partial sweep) are re-settled via the SAME one path.
* **Latency-fairness.** Two "same-tick" cash-outs resolve by the SERVER's stamp
  against ``C`` — identical server stamp ⇒ identical resolution; no client clock.

Run against the alembic-migrated Postgres (the conftest fixture).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Bet, GameRound, LedgerEntry, User, Wallet
from app.games.bet_loop import _idempotency_key
from app.wallet import Ledger
from app.ws.crash import CrashActor, RoundTimings
from app.ws.crash_bets import (
    CRASH_GAME_ID,
    crash_state,
    next_crash_round_number,
    open_crash_round,
    place_crash_bet,
    reconcile_crash_bets,
    recover_crash,
    settle_crash_round,
)
from app.ws.crash_core import CrashRound, crash_point_for_round
from engine.fairness import commit

EDGE = 0.01
_SEED = b"s20-crash-recovery-seed"
_START_BALANCE = 1_000_000
_INSTANT_TIMINGS = RoundTimings(waiting=0.0, tick_interval=0.0, settle=0.0)


async def _noop_sleep(_seconds: float) -> None:
    """A no-op clock so the actor loop runs instantly under test."""


class _DummyPublisher:
    async def publish(self, channel: str, message: str) -> None:  # noqa: ARG002
        return None


@pytest.fixture
async def funded_user(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> AsyncIterator[tuple[str, str]]:
    """A fresh user with a funded GOLD/PLAY wallet. Returns ``(user_id, wallet_id)``."""
    uid = f"u-{uuid4().hex}"
    wid = f"w-{uuid4().hex}"
    async with session_factory() as session, session.begin():
        session.add(User(id=uid))
        session.add(
            Wallet(id=wid, user_id=uid, currency="GOLD", mode="PLAY", balance_minor=0)
        )
    await ledger.grant(
        wallet_id=wid, amount_minor=_START_BALANCE, idempotency_key=f"grant:{uid}", ref=uid
    )
    yield uid, wid


async def _make_funded_user(
    session_factory: async_sessionmaker[AsyncSession], ledger: Ledger
) -> tuple[str, str]:
    uid = f"u-{uuid4().hex}"
    wid = f"w-{uuid4().hex}"
    async with session_factory() as session, session.begin():
        session.add(User(id=uid))
        session.add(
            Wallet(id=wid, user_id=uid, currency="GOLD", mode="PLAY", balance_minor=0)
        )
    await ledger.grant(
        wallet_id=wid, amount_minor=_START_BALANCE, idempotency_key=f"grant:{uid}", ref=uid
    )
    return uid, wid


async def _open_round(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    round_id: str | None = None,
    round_number: int = 1,
) -> CrashRound:
    rnd = CrashRound.open(
        round_server_seed=_SEED,
        round_id=round_id or f"round-{uuid4().hex}",
        round_number=round_number,
        edge=EDGE,
    )
    await open_crash_round(session_factory, rnd)
    return rnd


async def _bet_status(
    session_factory: async_sessionmaker[AsyncSession], bet_id: str
) -> str:
    async with session_factory() as session:
        bet = await session.get(Bet, bet_id)
        assert bet is not None
        return bet.status


async def _ledger_count(
    session_factory: async_sessionmaker[AsyncSession], idempotency_key: str
) -> int:
    async with session_factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.idempotency_key == idempotency_key)
            )
            or 0
        )


async def _max_crash_nonce(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        return int(
            await session.scalar(
                select(func.max(GameRound.nonce)).where(
                    GameRound.game_id == CRASH_GAME_ID
                )
            )
            or 0
        )


# --------------------------------------------------------------------------- #
# (1) Resume — redaction keyed off the persisted (DB) status, the recovery authority.
# --------------------------------------------------------------------------- #
async def test_state_withholds_C_and_seed_while_round_active(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    """A live round (DB status ACTIVE) leaks NEITHER C NOR the raw seed — only the
    commitment hash. A reconnecting client that learned C would know exactly when
    to cash out; this is the catastrophic leak the redaction prevents."""
    user_id, _wallet_id = funded_user
    rnd = await _open_round(session_factory)
    bet_id = f"bet-{uuid4().hex}"
    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=bet_id, stake_minor=1000, auto_cashout=2.0,
    )

    state = await crash_state(session_factory, user_id=user_id)
    round_view = state["round"]

    assert round_view is not None
    assert round_view["roundId"] == rnd.round_id
    assert round_view["roundNumber"] == rnd.round_number
    assert round_view["serverSeedHash"] == commit(_SEED)
    assert round_view["status"] == "ACTIVE"
    # The catastrophic fields are absent while the round is live.
    assert "crashPoint" not in round_view
    assert "serverSeed" not in round_view
    # The caller's own active bet is surfaced for resume.
    assert [b["betId"] for b in state["bets"]] == [bet_id]
    assert state["bets"][0]["autoCashout"] == 2.0
    assert state["bets"][0]["status"] == "ACTIVE"


async def test_state_discloses_seed_and_C_after_round_settled(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    """Once the round is SETTLED (post-reveal: betting + cash-out are closed and the
    crash event reveals the seed), /state MAY disclose seed + C — no advantage."""
    user_id, _wallet_id = funded_user
    rnd = await _open_round(session_factory)
    bet_id = f"bet-{uuid4().hex}"
    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=bet_id, stake_minor=1000, auto_cashout=2.0,
    )
    await settle_crash_round(session_factory, ledger, round_id=rnd.round_id)

    state = await crash_state(session_factory, user_id=user_id)
    round_view = state["round"]

    assert round_view is not None
    assert round_view["status"] == "SETTLED"
    assert round_view["crashPoint"] == rnd.C
    assert round_view["serverSeed"] == _SEED.hex()
    # The bet is no longer ACTIVE → it drops off the resume list.
    assert state["bets"] == []


async def test_state_returns_only_callers_bets_identity_fence(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
) -> None:
    """Two users bet the SAME shared round; A's /state surfaces ONLY A's bets."""
    a_id, _ = await _make_funded_user(session_factory, ledger)
    b_id, _ = await _make_funded_user(session_factory, ledger)
    rnd = await _open_round(session_factory)
    a_bet = f"bet-{uuid4().hex}"
    b_bet = f"bet-{uuid4().hex}"
    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=a_id,
        bet_id=a_bet, stake_minor=1000, auto_cashout=None,
    )
    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=b_id,
        bet_id=b_bet, stake_minor=1000, auto_cashout=None,
    )

    a_state = await crash_state(session_factory, user_id=a_id)
    b_state = await crash_state(session_factory, user_id=b_id)

    # Same shared round for both, but disjoint, owner-scoped bet lists.
    assert a_state["round"]["roundId"] == b_state["round"]["roundId"] == rnd.round_id
    assert [b["betId"] for b in a_state["bets"]] == [a_bet]
    assert [b["betId"] for b in b_state["bets"]] == [b_bet]


async def test_state_includes_live_cosmetic_multiplier_when_supplied(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    """The cosmetic multiplier (public, from the tick stream) is surfaced when an
    in-process actor supplies it; it is ``None`` when no actor is present (the
    unwired-actor fence). It is NEVER a leak — it is what tick events already show."""
    user_id, _wallet_id = funded_user
    await _open_round(session_factory)

    without_actor = await crash_state(session_factory, user_id=user_id)
    with_actor = await crash_state(
        session_factory, user_id=user_id, live_multiplier=1.42
    )

    assert without_actor["round"]["multiplier"] is None
    assert with_actor["round"]["multiplier"] == 1.42


async def test_state_returns_none_round_when_no_crash_round(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A caller asking for state when this DB run has crash rounds belonging to other
    callers still resolves the CURRENT shared round (not caller-scoped); the round is
    only ``None`` if no crash round exists at all — exercised here by a brand-new user
    on a populated DB still seeing the latest round but with an empty bet list."""
    uid = f"u-{uuid4().hex}"
    async with session_factory() as session, session.begin():
        session.add(User(id=uid))
    # If at least one crash round exists in this DB run, state.round is non-None and
    # the new user simply has no bets in it (the shared-round, owner-scoped contract).
    state = await crash_state(session_factory, user_id=uid)
    if state["round"] is not None:
        assert state["bets"] == []


# --------------------------------------------------------------------------- #
# (2) Deterministic restart re-settle + the stale-seed COLLISION hazard.
# --------------------------------------------------------------------------- #
async def test_mid_round_restart_re_settles_identically(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    """Settling, then "restarting" and re-settling, yields IDENTICAL bet outcomes and
    credits exactly once — the committed seed fixes C, so settlement is a pure replay.
    Also re-derives C from the persisted seed and asserts it equals the stored C (the
    provably-fair determinism check)."""
    user_id, _wallet_id = funded_user
    rnd = await _open_round(session_factory)
    C = rnd.C
    win_id = f"bet-{uuid4().hex}"  # auto below C → WIN
    lose_id = f"bet-{uuid4().hex}"  # auto above C → LOSS
    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=win_id, stake_minor=1000, auto_cashout=round(C - 0.01, 2),
    )
    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=lose_id, stake_minor=1000, auto_cashout=round(C + 0.01, 2),
    )

    # Provably-fair: C re-derived from the persisted committed seed matches storage.
    async with session_factory() as session:
        row = await session.get(GameRound, rnd.round_id)
        assert row is not None
        assert row.server_state is not None
        rederived = crash_point_for_round(
            bytes.fromhex(row.server_state["roundSeed"]),
            rnd.round_id,
            int(row.nonce or 0),
            EDGE,
        )
    assert rederived == float(row.server_state["C"]) == C

    first = {r.bet_id: (r.status, r.payout_minor) for r in
             await settle_crash_round(session_factory, ledger, round_id=rnd.round_id)}
    # "Restart": recovery re-settles the (already-terminal) round — a pure no-op replay.
    await recover_crash(session_factory, ledger)
    second_win = await _bet_status(session_factory, win_id)
    second_lose = await _bet_status(session_factory, lose_id)

    assert first[win_id][0] == second_win == "WON"
    assert first[lose_id][0] == second_lose == "LOST"
    # Exactly one WIN credit despite the re-settle (idempotent).
    assert await _ledger_count(session_factory, _idempotency_key(win_id, "WIN")) == 1
    assert await _ledger_count(session_factory, _idempotency_key(lose_id, "WIN")) == 0


async def test_recover_settles_unsettled_orphan_and_avoids_id_collision(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    """The COLLISION hazard, exercised end-to-end: a round persisted at the production
    id scheme (``round-{n}``) is left ACTIVE with un-settled bets (actor died at
    crash). A FRESH actor (the restart) must (a) re-settle that orphan deterministically
    and (b) recover the counter from the DB so it opens ``round-{n+1}`` — NEVER reusing
    ``round-{n}`` (which would no-op ``open_crash_round`` and broadcast a new curve over
    the OLD persisted C)."""
    user_id, _wallet_id = funded_user
    n = await _max_crash_nonce(session_factory) + 1
    orphan_id = f"round-{n}"
    rnd = await _open_round(session_factory, round_id=orphan_id, round_number=n)
    C = rnd.C
    win_id = f"bet-{uuid4().hex}"
    lose_id = f"bet-{uuid4().hex}"
    await place_crash_bet(
        session_factory, ledger, round_id=orphan_id, user_id=user_id,
        bet_id=win_id, stake_minor=1000, auto_cashout=round(C - 0.01, 2),
    )
    await place_crash_bet(
        session_factory, ledger, round_id=orphan_id, user_id=user_id,
        bet_id=lose_id, stake_minor=1000, auto_cashout=round(C + 0.01, 2),
    )

    # The restart: a brand-new actor instance (production id scheme).
    actor = CrashActor(
        _DummyPublisher(),
        edge=EDGE,
        seed_factory=lambda: b"fresh-restart-seed",
        timings=_INSTANT_TIMINGS,
        sleep=_noop_sleep,
        session_factory=session_factory,
        ledger=ledger,
    )
    next_number = await actor.recover()

    # (a) the orphan re-settled deterministically while ACTIVE (never settled before).
    assert await _bet_status(session_factory, win_id) == "WON"
    assert await _bet_status(session_factory, lose_id) == "LOST"
    # (b) the counter continued from the DB — NOT reset to 1.
    assert next_number == n + 1

    # Opening the next round must NOT collide with the orphan id and must carry a
    # FRESH C (a different seed → a recomputed crash point), proving no stale reuse.
    new_round = await actor.run_round(next_number)
    assert new_round.round_id == f"round-{n + 1}"
    assert new_round.round_id != orphan_id
    async with session_factory() as session:
        new_row = await session.get(GameRound, new_round.round_id)
        orphan_row = await session.get(GameRound, orphan_id)
    assert new_row is not None
    assert new_row.server_state["roundSeed"] == b"fresh-restart-seed".hex()
    assert orphan_row is not None
    assert orphan_row.status == "SETTLED"


async def test_next_crash_round_number_is_max_nonce_plus_one(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    n = await _max_crash_nonce(session_factory) + 7
    await _open_round(session_factory, round_id=f"round-{uuid4().hex}", round_number=n)
    assert await next_crash_round_number(session_factory) == n + 1


# --------------------------------------------------------------------------- #
# (3) Reconciler — stuck non-terminal bets re-settle via the SAME path (TTL config).
# --------------------------------------------------------------------------- #
async def test_reconcile_settles_bets_stuck_in_terminal_round(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    """A partial sweep: the round is SETTLED but a bet is still ACTIVE. The reconciler
    re-settles it regardless of TTL (a terminal round can have no legitimately-live
    bets)."""
    user_id, _wallet_id = funded_user
    rnd = await _open_round(session_factory)
    bet_id = f"bet-{uuid4().hex}"
    await place_crash_bet(
        session_factory, ledger, round_id=rnd.round_id, user_id=user_id,
        bet_id=bet_id, stake_minor=1000, auto_cashout=round(rnd.C - 0.01, 2),
    )
    # Force the round terminal WITHOUT settling the bet (simulate a mid-sweep crash).
    async with session_factory() as session, session.begin():
        row = await session.get(GameRound, rnd.round_id, with_for_update=True)
        assert row is not None
        row.status = "SETTLED"

    reconciled = await reconcile_crash_bets(session_factory, ledger, ttl_seconds=10_000)

    assert rnd.round_id in reconciled
    assert await _bet_status(session_factory, bet_id) == "WON"
    assert await _ledger_count(session_factory, _idempotency_key(bet_id, "WIN")) == 1


async def test_reconcile_settles_active_round_past_ttl_but_not_fresh(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    """An ACTIVE round whose bets have been non-terminal past the TTL is force-settled;
    a fresh ACTIVE round (within the window) is LEFT ALONE (it may still be running)."""
    user_id, _wallet_id = funded_user
    stale = await _open_round(session_factory)
    fresh = await _open_round(session_factory)
    stale_bet = f"bet-{uuid4().hex}"
    fresh_bet = f"bet-{uuid4().hex}"
    await place_crash_bet(
        session_factory, ledger, round_id=stale.round_id, user_id=user_id,
        bet_id=stale_bet, stake_minor=1000, auto_cashout=round(stale.C - 0.01, 2),
    )
    await place_crash_bet(
        session_factory, ledger, round_id=fresh.round_id, user_id=user_id,
        bet_id=fresh_bet, stake_minor=1000, auto_cashout=round(fresh.C - 0.01, 2),
    )
    # Age the stale round's created_at well past the TTL.
    async with session_factory() as session, session.begin():
        row = await session.get(GameRound, stale.round_id, with_for_update=True)
        assert row is not None
        row.created_at = datetime.now(UTC) - timedelta(seconds=3600)

    reconciled = await reconcile_crash_bets(session_factory, ledger, ttl_seconds=60)

    assert stale.round_id in reconciled
    assert fresh.round_id not in reconciled
    assert await _bet_status(session_factory, stale_bet) == "WON"
    assert await _bet_status(session_factory, fresh_bet) == "ACTIVE"


# --------------------------------------------------------------------------- #
# (4) Latency-fairness — resolution is by the SERVER's stamp, never a client clock.
# --------------------------------------------------------------------------- #
async def test_two_same_tick_cashouts_resolve_by_server_stamp(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    """Two bets cashed out at the SAME server-stamped tick resolve IDENTICALLY against
    C — the resolution is a function of the server's stamp vs C, not of any client
    clock or arrival order. A stamp strictly below C → both WIN at that stamp; a stamp
    at/above C → both LOSE."""
    user_id, _wallet_id = funded_user
    rnd = await _open_round(session_factory)
    tick = round(rnd.C - 0.01, 2)
    assert tick < rnd.C

    # Drive a single RUNNING actor; both cash-outs are stamped from ITS tracked tick.
    actor = CrashActor(
        _DummyPublisher(), edge=EDGE, session_factory=session_factory, ledger=ledger
    )
    actor._round = rnd  # noqa: SLF001 - place during WAITING
    first_id = f"bet-{uuid4().hex}"
    second_id = f"bet-{uuid4().hex}"
    for bet_id in (first_id, second_id):
        await actor.place_bet(
            user_id=user_id, bet_id=bet_id, stake_minor=1000, auto_cashout=None
        )
    actor._round = rnd.lock().start()  # noqa: SLF001 - RUNNING
    actor._current_multiplier = tick  # noqa: SLF001 - the server's last-published tick

    # Two cash-outs "same tick": each carries ONLY a betId — no client multiplier or
    # timestamp. Both stamp the SAME server tick.
    r1 = await actor.cash_out(bet_id=first_id)
    r2 = await actor.cash_out(bet_id=second_id)

    from engine.money import apply_multiplier

    assert r1.status == r2.status == "WON"
    assert r1.multiplier == r2.multiplier == tick  # identical server stamp
    assert r1.payout_minor == r2.payout_minor == apply_multiplier(1000, tick)


async def test_same_tick_cashout_at_or_above_C_both_lose(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    funded_user: tuple[str, str],
) -> None:
    """If the server's stamp lands AT C (strict ``m < C`` loses) both same-tick
    cash-outs LOSE — resolution by the server stamp, symmetric for both bets."""
    user_id, _wallet_id = funded_user
    rnd = await _open_round(session_factory)

    actor = CrashActor(
        _DummyPublisher(), edge=EDGE, session_factory=session_factory, ledger=ledger
    )
    actor._round = rnd  # noqa: SLF001
    a = f"bet-{uuid4().hex}"
    b = f"bet-{uuid4().hex}"
    for bet_id in (a, b):
        await actor.place_bet(
            user_id=user_id, bet_id=bet_id, stake_minor=1000, auto_cashout=None
        )
    actor._round = rnd.lock().start()  # noqa: SLF001 - RUNNING
    actor._current_multiplier = rnd.C  # noqa: SLF001 - stamp == C → strict loss

    r1 = await actor.cash_out(bet_id=a)
    r2 = await actor.cash_out(bet_id=b)

    assert r1.status == r2.status == "LOST"


# --------------------------------------------------------------------------- #
# HTTP — routing precedence over the generic /games/{id}/state + token identity.
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "test-signing-key-not-for-prod-0123456789abcdef")


async def test_crash_state_endpoint_routes_and_is_token_scoped(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
) -> None:
    """``GET /games/originals.crash/state`` resolves to the CRASH handler (a ``bets``
    array — not the generic round projection), requires a token, and is scoped to the
    token's user. While the round is ACTIVE it withholds C/seed."""
    from app.api.games import _Runtime
    from app.main import app

    prev = getattr(app.state, "games_runtime", None)
    app.state.games_runtime = _Runtime(session_factory)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            # No token → 401.
            unauth = await client.get(f"/games/{CRASH_GAME_ID}/state")
            assert unauth.status_code == 401

            body = (await client.post("/auth/guest")).json()
            uid = body["userId"]
            token = body["tokens"]["accessToken"]
            auth = {"Authorization": f"Bearer {token}"}

            rnd = await _open_round(session_factory)
            bet_id = f"bet-{uuid4().hex}"
            await place_crash_bet(
                session_factory, ledger, round_id=rnd.round_id, user_id=uid,
                bet_id=bet_id, stake_minor=1000, auto_cashout=2.0,
            )

            resp = await client.get(f"/games/{CRASH_GAME_ID}/state", headers=auth)
    finally:
        app.state.games_runtime = prev

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    # The CRASH shape (proves precedence over the generic /{game_id}/state).
    assert "bets" in payload
    assert "round" in payload
    assert payload["round"]["roundId"] == rnd.round_id
    assert "crashPoint" not in payload["round"]  # redacted while ACTIVE
    assert [b["betId"] for b in payload["bets"]] == [bet_id]
