"""S32 — the pure per-seat projection ``engine.poker.table.public_view``.

The load-bearing redaction lives in the ENGINE as a WHITELIST: given the full
``TableState`` and a viewer seat, the projection returns ONLY public table state plus
the viewer's own hole cards. It NEVER includes another seat's ``hole`` (except a
legitimate showdown reveal of a non-folded seat) and NEVER includes the hidden
``community_undealt`` board-to-come. Unit-testable without any WS plumbing — leak-proof
by construction, the same way ``StatefulGame.public_view`` keeps redaction in one place.

These tests drive the projection directly over dealt states (the ``_deck_rng`` harness
from ``test_poker_table`` synthesises a chosen deal so holes are known oracles).
"""

from __future__ import annotations

from engine.cards.evaluator import Card
from engine.poker.table import (
    Action,
    ActionKind,
    Street,
    TableConfig,
    apply_action,
    deal_hand,
    public_view,
)

_FULL_DECK = [Card.from_index(i) for i in range(52)]


class _ScriptedRng:
    """An ``RngStream`` that replays a fixed list of floats (then raises if drained)."""

    def __init__(self, floats: list[float]) -> None:
        self._it = iter(floats)

    def next(self) -> float:
        return next(self._it)


def _deck_rng(targets: list[Card]) -> _ScriptedRng:
    """Floats that make ``sample_without_replacement(_FULL_DECK, k)`` yield ``targets``."""
    pool = list(_FULL_DECK)
    n = len(pool)
    floats: list[float] = []
    for i, card in enumerate(targets):
        p = pool.index(card, i)
        floats.append((p - i + 0.5) / (n - i))
        pool[i], pool[p] = pool[p], pool[i]
    return _ScriptedRng(floats)


def _card(c: Card) -> dict[str, int]:
    return {"rank": c.rank, "suit": c.suit}


# Three seats, holes then the five-card board (the deal order deal_hand consumes).
_S0 = [Card(14, 0), Card(14, 1)]  # seat 0: AA
_S1 = [Card(13, 0), Card(13, 1)]  # seat 1: KK
_S2 = [Card(2, 0), Card(2, 1)]    # seat 2: 22
_BOARD = [Card(7, 2), Card(8, 2), Card(9, 3), Card(10, 0), Card(11, 1)]
_DEAL = [*_S0, *_S1, *_S2, *_BOARD]
_HOLES = {0: _S0, 1: _S1, 2: _S2}
_CONFIG = TableConfig(small_blind=50, big_blind=100)


def _dealt_state():
    return deal_hand(_CONFIG, (10_000, 10_000, 10_000), button=0, rng=_deck_rng(_DEAL))


# --------------------------------------------------------------------------- #
# Pre-showdown: each viewer sees ONLY its own hole; no other hole, no undealt.  #
# --------------------------------------------------------------------------- #
def test_viewer_sees_only_own_hole_no_others_no_undealt() -> None:
    state = _dealt_state()
    for viewer in (0, 1, 2):
        view = public_view(state, viewer)
        by_seat = {s["seatId"]: s for s in view["seats"]}
        # The viewer's own hole is present and correct.
        assert by_seat[viewer]["hole"] == [_card(c) for c in _HOLES[viewer]]
        # EVERY other seat's hole is absent (whitelist — the key is simply not there).
        for other in (0, 1, 2):
            if other != viewer:
                assert "hole" not in by_seat[other]
        # The hidden board-to-come never appears under any key/spelling.
        assert "community_undealt" not in view
        assert "communityUndealt" not in view
        # Pre-flop: no community cards revealed yet.
        assert view["board"] == []


def test_spectator_view_has_no_holes_pre_showdown() -> None:
    view = public_view(_dealt_state(), None)
    assert all("hole" not in s for s in view["seats"])


def test_public_view_is_a_whitelist_no_undealt_anywhere() -> None:
    """No published key, anywhere in the projection, ever carries the undealt board."""
    state = _dealt_state()
    import json

    blob = json.dumps(public_view(state, 0))
    # The undealt cards are real Card objects in state; none of their rank/suit pairs
    # should be reachable via the projection (the board-to-come is never whitelisted).
    assert "community_undealt" not in blob


# --------------------------------------------------------------------------- #
# Showdown: non-folded holes go public; a FOLDED seat's hole NEVER does.        #
# --------------------------------------------------------------------------- #
def _multiway_showdown_state():
    """Drive a 3-handed hand to a true showdown with seat 0 folded (button folds
    pre-flop; sb + bb check down to the river)."""
    state = _dealt_state()
    # Pre-flop first to act is the button (seat 0, 3-handed) — it folds.
    state = apply_action(state, Action(0, ActionKind.FOLD))
    # Seat 1 (sb) owes the big blind → calls; seat 2 (bb) checks → flop.
    state = apply_action(state, Action(1, ActionKind.CALL))
    state = apply_action(state, Action(2, ActionKind.CHECK))
    # Flop, turn, river: both remaining seats check each street.
    for _street in (Street.FLOP, Street.TURN, Street.RIVER):
        state = apply_action(state, Action(1, ActionKind.CHECK))
        state = apply_action(state, Action(2, ActionKind.CHECK))
    assert state.hand_over
    return state


def test_showdown_reveals_non_folded_holes_but_never_the_folded_seat() -> None:
    state = _multiway_showdown_state()
    # From seat 2's view (a contestant): it sees seat 1's hole (a real showdown) but
    # NEVER seat 0's — seat 0 folded, so its cards are mucked, not revealed.
    view = public_view(state, 2)
    by_seat = {s["seatId"]: s for s in view["seats"]}
    assert by_seat[2]["hole"] == [_card(c) for c in _HOLES[2]]  # own
    assert by_seat[1]["hole"] == [_card(c) for c in _HOLES[1]]  # contestant revealed
    assert "hole" not in by_seat[0]  # folded seat — mucked, never revealed
    # Even the spectator view never sees the folded seat's hole.
    spectator = {s["seatId"]: s for s in public_view(state, None)["seats"]}
    assert "hole" not in spectator[0]
    assert spectator[1]["hole"] == [_card(c) for c in _HOLES[1]]


def test_uncontested_win_never_reveals_the_winner_hole_to_others() -> None:
    """Everyone folds to one seat (no showdown): the winner does NOT show its hand."""
    state = _dealt_state()
    state = apply_action(state, Action(0, ActionKind.FOLD))  # button folds
    state = apply_action(state, Action(1, ActionKind.FOLD))  # sb folds → seat 2 wins
    assert state.hand_over
    # Seat 0 (an opponent) must NOT see the lone winner's (seat 2) hole.
    by_seat = {s["seatId"]: s for s in public_view(state, 0)["seats"]}
    assert "hole" not in by_seat[2]
    # The winner still sees its own hole.
    own = {s["seatId"]: s for s in public_view(state, 2)["seats"]}
    assert own[2]["hole"] == [_card(c) for c in _HOLES[2]]
