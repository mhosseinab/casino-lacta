"""Deterministic No-Limit Hold'em table reducer — pure outcome logic, no networking.

A cash NLHE hand modelled as a small, consumer-shaped state machine (the seam S32's WS
actor and S33's rake consume):

* :func:`deal_hand` opens a hand from ``(config, stacks, button, rng)`` — rotates nothing
  (the caller fixes the button; :func:`start_next_hand` is the rotate-and-redeal helper),
  posts the blinds, and deals hole cards + the **full** five-card board from the injected
  :class:`~engine.types.RngStream`. The board is drawn up front but stored HIDDEN
  (``community_undealt``) and revealed one street at a time, so the deal is a pure function
  of the seed (verifier-grade determinism) while no unseen card is ever public.
* :func:`apply_action` advances the hand by one player action and consumes **no** entropy —
  poker betting is deterministic given the deal (entropy enters only at the shuffle, exactly
  as Mines commits its layout at ``init`` and ignores ``rng`` in ``step``). It validates the
  action and, on anything illegal, raises :class:`IllegalAction` WITHOUT mutating state.

Why ``(state, action)`` and not the generic ``(state, action, rng)``: there is no
per-action randomness in hold'em, so threading a live stream through every check/call would
be noise. The single rng consumer is :func:`deal_hand`; S32 recovers a table by replaying
``deal_hand`` from the committed seed then replaying the action log — both reproduce state
exactly (see the determinism + replay tests).

Reuse, never re-implement (the carry-forwards): showdown ranks hands with
:func:`engine.cards.evaluator.rank_seven` and builds/awards pots with
:func:`engine.poker.pots.build_pots` / :func:`~engine.poker.pots.award_pots`. This module
owns NO ranking and NO side-pot math. Uncalled bets need no special netting: an over-bet
forms a lone-eligible top pot that ``award_pots`` returns to its sole contributor; the only
short-circuit is the ``<= 1`` survivor case (everyone folded), which pushes the whole pot to
the lone seat with no board and no rank computed.

Redaction-ready (for S32): every hidden datum lives in a clearly separable field — each
seat's :attr:`Seat.hole` and the table's :attr:`TableState.community_undealt`. A per-seat
public projection redacts exactly those; nothing else in the state is secret.

Pure: stdlib + engine only; integer minor units throughout; no IO, clock, or ``random``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import Any

from engine.cards.evaluator import Card, rank_seven
from engine.poker.pots import award_pots, build_pots
from engine.sampling import sample_without_replacement
from engine.types import RngStream

_HOLE_CARDS = 2
_BOARD_CARDS = 5
_FULL_DECK: tuple[Card, ...] = tuple(Card.from_index(i) for i in range(52))
# Community cards revealed by the END of each street (preflop none, flop 3, turn 4, river 5).
_BOARD_BY_STREET = {0: 0, 1: 3, 2: 4, 3: 5}


class IllegalAction(ValueError):
    """A player action that violates the rules (wrong turn, illegal size, …).

    Raised by :func:`apply_action` BEFORE any state change, so a rejected action leaves the
    table untouched. Subclasses ``ValueError`` so generic boundary handling still catches it.
    """


class Street(IntEnum):
    """Betting rounds in order; ``COMPLETE`` is the terminal (post-showdown) phase."""

    PREFLOP = 0
    FLOP = 1
    TURN = 2
    RIVER = 3
    COMPLETE = 4


class ActionKind(Enum):
    """The legal action verbs. ``BET``/``RAISE`` carry ``amount`` = the *to-total* the
    actor's street contribution should reach; the others ignore ``amount``."""

    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"  # open a street (only when there is no bet to call yet)
    RAISE = "raise"  # increase an existing bet
    ALL_IN = "all_in"  # commit the entire remaining stack


@dataclass(frozen=True)
class Action:
    """One seat's action. ``amount`` is the *to-total* street contribution for BET/RAISE."""

    seat: int
    kind: ActionKind
    amount: int = 0


@dataclass(frozen=True)
class TableConfig:
    """Blind structure — CONFIG, never literals. (Antes/straddles are out of scope here.)"""

    small_blind: int
    big_blind: int


@dataclass(frozen=True)
class Seat:
    """One seat's per-hand state. ``hole`` is the seat's SECRET datum (S32 redacts it)."""

    seat_id: int
    stack: int
    hole: tuple[Card, ...] = ()
    street_contrib: int = 0  # chips committed this betting round
    total_contrib: int = 0  # chips committed across the whole hand (pot share)
    folded: bool = False
    all_in: bool = False
    acted: bool = False  # has acted since the last full bet/raise they must answer


@dataclass(frozen=True)
class TableState:
    """Immutable snapshot of a hand. The reducer returns a fresh state every step."""

    config: TableConfig
    button: int
    sb_seat: int
    bb_seat: int
    seats: tuple[Seat, ...]
    board: tuple[Card, ...]  # revealed community cards (public)
    community_undealt: tuple[Card, ...]  # board to come — HIDDEN until revealed (S32 redacts)
    street: Street
    to_act: int | None  # seat to act, or None when the hand is over
    current_bet: int  # the street's amount to match
    last_full_raise: int  # size of the last full raise (the min-raise increment basis)
    hand_over: bool = False
    awards: dict[int, int] = field(default_factory=dict)  # seat -> chips pushed at showdown


# --------------------------------------------------------------------------- #
# Seat-order helpers                                                            #
# --------------------------------------------------------------------------- #


def _order_from(n: int, start: int) -> list[int]:
    """Seat ids clockwise starting at ``start`` (inclusive)."""
    return [(start + i) % n for i in range(n)]


def _can_act(seat: Seat) -> bool:
    return not seat.folded and not seat.all_in


def _needs_to_act(seat: Seat, current_bet: int) -> bool:
    """A seat owes an action: it can act AND has either not acted or is short of the bet."""
    return _can_act(seat) and (not seat.acted or seat.street_contrib < current_bet)


def _first_can_act_from(seats: tuple[Seat, ...], start: int) -> int | None:
    """First seat clockwise from ``start`` (inclusive) that can still act, else None."""
    n = len(seats)
    for sid in _order_from(n, start):
        if _can_act(seats[sid]):
            return sid
    return None


def _next_to_act(state: TableState, after: int) -> int | None:
    """First seat strictly after ``after`` (clockwise) that still owes an action, else None."""
    n = len(state.seats)
    for i in range(1, n):
        sid = (after + i) % n
        if _needs_to_act(state.seats[sid], state.current_bet):
            return sid
    return None


def _replace_seat(state: TableState, seat: Seat) -> tuple[Seat, ...]:
    return tuple(seat if s.seat_id == seat.seat_id else s for s in state.seats)


# --------------------------------------------------------------------------- #
# Dealing                                                                       #
# --------------------------------------------------------------------------- #


def _blind_positions(button: int, n: int) -> tuple[int, int]:
    """Return ``(sb_seat, bb_seat)``. Heads-up: the button posts the small blind."""
    if n == 2:
        return button, (button + 1) % n
    return (button + 1) % n, (button + 2) % n


def deal_hand(
    config: TableConfig,
    stacks: tuple[int, ...],
    button: int,
    rng: RngStream,
) -> TableState:
    """Open a hand: post blinds, deal holes + the full (hidden) board, set first to act.

    ``stacks`` are per-seat starting stacks in minor units (seat id == index). The board is
    drawn up front from ``rng`` so the deal is a pure function of the seed, but withheld in
    ``community_undealt`` until each street reveals it. Cards are dealt sequentially —
    seat ``i`` takes the ``2i``/``2i+1`` draws, the board the last five — which (the deck is
    already shuffled) is equivalent to round-robin dealing and keeps the order auditable.
    """
    n = len(stacks)
    if n < 2:
        raise ValueError("a hand needs at least 2 seats")
    if not 0 <= button < n:
        raise ValueError(f"button {button} out of range [0, {n})")

    need = _HOLE_CARDS * n + _BOARD_CARDS
    drawn = sample_without_replacement(rng, _FULL_DECK, need)
    holes = [tuple(drawn[_HOLE_CARDS * i : _HOLE_CARDS * i + _HOLE_CARDS]) for i in range(n)]
    board_to_come = tuple(drawn[_HOLE_CARDS * n :])

    sb_seat, bb_seat = _blind_positions(button, n)
    seats: list[Seat] = []
    for sid in range(n):
        post = 0
        if sid == sb_seat:
            post = min(config.small_blind, stacks[sid])
        elif sid == bb_seat:
            post = min(config.big_blind, stacks[sid])
        seats.append(
            Seat(
                seat_id=sid,
                stack=stacks[sid] - post,
                hole=holes[sid],
                street_contrib=post,
                total_contrib=post,
                all_in=stacks[sid] - post == 0 and post > 0,
            )
        )

    current_bet = max(s.street_contrib for s in seats)
    state = TableState(
        config=config,
        button=button,
        sb_seat=sb_seat,
        bb_seat=bb_seat,
        seats=tuple(seats),
        board=(),
        community_undealt=board_to_come,
        street=Street.PREFLOP,
        to_act=None,
        current_bet=current_bet,
        last_full_raise=config.big_blind,
    )
    # First to act preflop is the seat after the big blind.
    to_act = _first_can_act_from(state.seats, (bb_seat + 1) % n)
    return replace(state, to_act=to_act)


def start_next_hand(state: TableState, rng: RngStream) -> TableState:
    """Carry stacks forward, rotate the button one seat, and deal the next hand."""
    n = len(state.seats)
    stacks = tuple(s.stack for s in state.seats)
    return deal_hand(state.config, stacks, (state.button + 1) % n, rng)


# --------------------------------------------------------------------------- #
# Applying an action                                                            #
# --------------------------------------------------------------------------- #


def apply_action(state: TableState, action: Action) -> TableState:
    """Validate + apply one action, returning the next state. Illegal → ``IllegalAction``."""
    if state.hand_over or state.to_act is None:
        raise IllegalAction("the hand is over")
    if action.seat != state.to_act:
        raise IllegalAction(f"seat {action.seat} cannot act; it is seat {state.to_act}'s turn")

    seat = state.seats[action.seat]
    if action.kind is ActionKind.FOLD:
        updated = replace(seat, folded=True, acted=True)
        return _progress(replace(state, seats=_replace_seat(state, updated)))
    if action.kind is ActionKind.CHECK:
        return _apply_check(state, seat)
    if action.kind is ActionKind.CALL:
        return _apply_call(state, seat)
    if action.kind is ActionKind.ALL_IN:
        return _apply_all_in(state, seat)
    if action.kind in (ActionKind.BET, ActionKind.RAISE):
        return _apply_bet_or_raise(state, seat, action)
    raise IllegalAction(f"unknown action kind {action.kind!r}")


def _apply_check(state: TableState, seat: Seat) -> TableState:
    if seat.street_contrib != state.current_bet:
        raise IllegalAction("cannot check facing a bet")
    updated = replace(seat, acted=True)
    return _progress(replace(state, seats=_replace_seat(state, updated)))


def _apply_call(state: TableState, seat: Seat) -> TableState:
    owed = state.current_bet - seat.street_contrib
    if owed <= 0:
        raise IllegalAction("nothing to call")
    pay = min(owed, seat.stack)  # call for less when short -> all-in
    updated = replace(
        seat,
        stack=seat.stack - pay,
        street_contrib=seat.street_contrib + pay,
        total_contrib=seat.total_contrib + pay,
        acted=True,
        all_in=seat.stack - pay == 0,
    )
    return _progress(replace(state, seats=_replace_seat(state, updated)))


def _apply_all_in(state: TableState, seat: Seat) -> TableState:
    if seat.stack == 0:
        raise IllegalAction("no chips to put all-in")
    target = seat.street_contrib + seat.stack
    if target <= state.current_bet:
        # All-in that does not exceed the bet: a (short) call, never reopens action.
        return _apply_call(state, seat)
    # Going all-in for more than the bet IS a raise — only legal if action is open to us.
    if seat.acted:
        raise IllegalAction("action was not reopened; you may only call or fold")
    return _commit_aggressive(state, seat, target)


def _apply_bet_or_raise(state: TableState, seat: Seat, action: Action) -> TableState:
    if seat.acted:
        # A short all-in raised the bet but did not reopen action — call or fold only.
        raise IllegalAction("action was not reopened; you may only call or fold")
    is_open = state.current_bet == 0  # no bet exists this street yet (BET opens; else RAISE)
    if action.kind is ActionKind.BET and not is_open:
        raise IllegalAction("cannot bet facing a bet; raise instead")
    if action.kind is ActionKind.RAISE and is_open:
        raise IllegalAction("cannot raise with no bet to raise; bet instead")
    if action.amount <= state.current_bet:
        raise IllegalAction("a bet/raise must exceed the current bet")
    if action.amount - seat.street_contrib > seat.stack:
        raise IllegalAction("cannot wager more than the stack")
    return _commit_aggressive(state, seat, action.amount)


def _commit_aggressive(state: TableState, seat: Seat, target: int) -> TableState:
    """Commit a bet/raise/all-in raise to ``target`` (a to-total street contribution)."""
    added = target - seat.street_contrib
    is_all_in = added == seat.stack
    increment = target - state.current_bet
    full_raise = increment >= state.last_full_raise
    if not full_raise and not is_all_in:
        raise IllegalAction(
            f"raise to {target} is below the minimum (+{state.last_full_raise})"
        )

    updated = replace(
        seat,
        stack=seat.stack - added,
        street_contrib=target,
        total_contrib=seat.total_contrib + added,
        acted=True,
        all_in=is_all_in,
    )
    seats = _replace_seat(state, updated)
    if full_raise:
        # A full raise reopens the action: everyone else who can act owes a response.
        seats = tuple(
            replace(s, acted=False) if s.seat_id != seat.seat_id and _can_act(s) else s
            for s in seats
        )
        return _progress(
            replace(state, seats=seats, current_bet=target, last_full_raise=increment)
        )
    # Partial (short) all-in: the bet rises but the min-raise basis and others' duty to
    # *re-raise* do not — already-acted seats may only call or fold (they stay acted=True).
    return _progress(replace(state, seats=seats, current_bet=target))


# --------------------------------------------------------------------------- #
# Progression: next actor / next street / showdown                             #
# --------------------------------------------------------------------------- #


def _progress(state: TableState) -> TableState:
    """After an action: end the hand, advance the actor, or close the betting round."""
    live = [s for s in state.seats if not s.folded]
    if len(live) == 1:
        return _award_uncontested(state, live[0])

    assert state.to_act is not None
    nxt = _next_to_act(state, state.to_act)
    if nxt is not None:
        return replace(state, to_act=nxt)
    return _close_betting_round(state)


def _close_betting_round(state: TableState) -> TableState:
    if state.street is Street.RIVER:
        return _showdown(state)
    can_act = [s for s in state.seats if _can_act(s)]
    if len(can_act) < 2:
        # No further betting possible (all but one are all-in): run the board out.
        return _showdown(_reveal_to(state, Street.RIVER))
    return _open_next_street(state)


def _reveal_to(state: TableState, street: Street) -> TableState:
    """Reveal community cards up to (and including) ``street`` and set ``state.street``."""
    target = _BOARD_BY_STREET[int(street)]
    need = target - len(state.board)
    board = state.board + state.community_undealt[:need]
    return replace(
        state,
        board=board,
        community_undealt=state.community_undealt[need:],
        street=street,
    )


def _open_next_street(state: TableState) -> TableState:
    state = _reveal_to(state, Street(int(state.street) + 1))
    seats = tuple(
        replace(s, street_contrib=0, acted=False) if _can_act(s) else replace(s, street_contrib=0)
        for s in state.seats
    )
    state = replace(
        state,
        seats=seats,
        current_bet=0,
        last_full_raise=state.config.big_blind,
    )
    to_act = _first_can_act_from(state.seats, (state.button + 1) % len(state.seats))
    return replace(state, to_act=to_act)


def _award_uncontested(state: TableState, winner: Seat) -> TableState:
    """Everyone folded: push the whole pot (incl. the winner's own uncalled chips) to it."""
    pot = sum(s.total_contrib for s in state.seats)
    updated = replace(winner, stack=winner.stack + pot)
    settled = tuple(
        replace(s, street_contrib=0, total_contrib=0) for s in _replace_seat(state, updated)
    )
    return replace(
        state,
        seats=settled,
        to_act=None,
        street=Street.COMPLETE,
        hand_over=True,
        awards={winner.seat_id: pot},
    )


# --------------------------------------------------------------------------- #
# Per-seat redaction — the CLIENT-SAFE projection (S32's load-bearing invariant) #
# --------------------------------------------------------------------------- #


def _card_view(card: Card) -> dict[str, int]:
    """Serialise one card to its public, JSON-safe shape."""
    return {"rank": card.rank, "suit": card.suit}


def public_view(state: TableState, viewer_seat: int | None) -> dict[str, Any]:
    """The CLIENT-SAFE projection of a hand for ``viewer_seat`` (a WHITELIST).

    Returns ONLY public table state (blinds, button, the *revealed* board, pot,
    per-seat chip/position facts, whose turn, awards) plus the viewer's OWN hole
    cards. It is a positive whitelist — a field is public only because it is named
    here — so the two secrets can never leak by omission:

    * another seat's :attr:`Seat.hole` is included ONLY for the viewer itself, or as
      a legitimate **showdown reveal** of a non-folded seat once a real (≥2 survivors)
      showdown has happened. A FOLDED seat's hole is NEVER revealed (it is mucked),
      and the lone winner of an uncontested pot does not show.
    * :attr:`TableState.community_undealt` (the board-to-come) is NEVER projected.

    ``viewer_seat is None`` is the spectator/public projection (own-hole reveal is
    disabled; showdown reveals still apply). Pure: stdlib + engine only — this is the
    SAME projection the verifier and the WS actor consume, never re-implemented.
    """
    live = [s for s in state.seats if not s.folded]
    showdown_reveal = state.hand_over and len(live) >= 2  # a true (contested) showdown
    seats: list[dict[str, Any]] = []
    for s in state.seats:
        seat_view: dict[str, Any] = {
            "seatId": s.seat_id,
            "stack": s.stack,
            "streetContrib": s.street_contrib,
            "totalContrib": s.total_contrib,
            "folded": s.folded,
            "allIn": s.all_in,
            "acted": s.acted,
        }
        is_own = viewer_seat is not None and s.seat_id == viewer_seat
        if is_own or (showdown_reveal and not s.folded):
            seat_view["hole"] = [_card_view(c) for c in s.hole]
        seats.append(seat_view)
    return {
        "config": {
            "smallBlind": state.config.small_blind,
            "bigBlind": state.config.big_blind,
        },
        "button": state.button,
        "sbSeat": state.sb_seat,
        "bbSeat": state.bb_seat,
        "street": int(state.street),
        "toAct": state.to_act,
        "currentBet": state.current_bet,
        "lastFullRaise": state.last_full_raise,
        "handOver": state.hand_over,
        "board": [_card_view(c) for c in state.board],  # revealed community cards only
        "pot": sum(s.total_contrib for s in state.seats),
        "awards": dict(state.awards),
        "seats": seats,
    }


def _showdown(state: TableState) -> TableState:
    """Build pots from total contributions and award them by best 7-card hand."""
    contributions = {s.seat_id: s.total_contrib for s in state.seats if s.total_contrib > 0}
    folded = {s.seat_id for s in state.seats if s.folded}
    pots = build_pots(contributions, folded)
    ranks = {
        s.seat_id: rank_seven([*s.hole, *state.board])
        for s in state.seats
        if not s.folded
    }
    awards = award_pots(pots, ranks)
    seats = tuple(
        replace(s, stack=s.stack + awards.get(s.seat_id, 0), street_contrib=0, total_contrib=0)
        for s in state.seats
    )
    return replace(
        state,
        seats=seats,
        to_act=None,
        street=Street.COMPLETE,
        hand_over=True,
        awards=awards,
    )
