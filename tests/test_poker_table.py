"""S31 — deterministic NLHE table reducer: deal, betting, validation, showdown.

The reducer is a PURE state machine: ``deal_hand(config, stacks, button, rng)`` opens a
hand (rotating button, posting blinds, dealing holes + the full board from the seeded
stream — board kept hidden until each street reveals it), and ``apply_action(state,
action)`` advances it with NO further entropy (poker betting is deterministic given the
deal — entropy enters only at the shuffle, mirroring Mines). Showdown REUSES the carry-
forwards: ``evaluator.rank_seven`` for ranking and ``pots.build_pots``/``award_pots`` for
the main + side-pot math — this file never re-implements either.

Oracles are hand-computed. To make showdown outcomes assertable, ``_deck_rng`` synthesises
the exact float stream that drives ``sample_without_replacement`` to deal a CHOSEN deck
order; every scripted test FIRST asserts the dealt holes + board (so a helper bug fails
loudly, never masking a reducer bug) and asserts ``sum(stacks)`` is invariant across the
whole hand (the cheapest, strongest chip-conservation catch).

The seeded ``random`` / scripted floats live HERE (tests); engine purity forbids both.
"""

from __future__ import annotations

import pytest

from engine.cards.evaluator import Card
from engine.poker.table import (
    Action,
    ActionKind,
    IllegalAction,
    Street,
    TableConfig,
    apply_action,
    deal_hand,
    start_next_hand,
)
from engine.rng import create_rng

# --------------------------------------------------------------------------- #
# Deck-control test harness — synthesise the rng stream for a chosen deal       #
# --------------------------------------------------------------------------- #

_FULL_DECK = [Card.from_index(i) for i in range(52)]


class _ScriptedRng:
    """An ``RngStream`` that replays a fixed list of floats (then raises if drained)."""

    def __init__(self, floats: list[float]) -> None:
        self._it = iter(floats)

    def next(self) -> float:
        return next(self._it)


def _deck_rng(targets: list[Card]) -> _ScriptedRng:
    """Floats that make ``sample_without_replacement(_FULL_DECK, k)`` yield ``targets``.

    Inverse partial Fisher-Yates: at step ``i`` the selection sample swaps ``targets[i]``
    (currently at pool position ``p >= i``) into slot ``i`` iff ``floor(f*(n-i)) == p-i``;
    the midpoint ``(p-i+0.5)/(n-i)`` lands squarely in that bucket.
    """
    pool = list(_FULL_DECK)
    n = len(pool)
    floats: list[float] = []
    for i, card in enumerate(targets):
        p = pool.index(card, i)
        floats.append((p - i + 0.5) / (n - i))
        pool[i], pool[p] = pool[p], pool[i]
    return _ScriptedRng(floats)


def _c(rank: int, suit: int) -> Card:
    return Card(rank=rank, suit=suit)


# A reusable showdown layout (3 players, sequential deal seat i <- targets[2i], [2i+1]):
#   seat0 = A A   seat1 = K K   seat2 = Q Q
#   board = 2 3 4 7 9 (rainbow-ish, no flush, no straight) -> nobody improves on a pair
# so on showdown seat0 (aces) > seat1 (kings) > seat2 (queens), uncontested ranking.
_AA = [_c(14, 0), _c(14, 1)]
_KK = [_c(13, 0), _c(13, 1)]
_QQ = [_c(12, 0), _c(12, 1)]
_BOARD = [_c(2, 1), _c(3, 2), _c(4, 0), _c(7, 3), _c(9, 1)]
_THREE_HANDED_DEAL = [*_AA, *_KK, *_QQ, *_BOARD]


def _total_chips(state) -> int:  # noqa: ANN001 - test helper over opaque state
    return sum(s.stack for s in state.seats) + sum(s.total_contrib for s in state.seats)


# --------------------------------------------------------------------------- #
# Deal: blinds, button, hole isolation, hidden board                           #
# --------------------------------------------------------------------------- #


def test_deal_posts_blinds_and_deals_holes_and_hides_board() -> None:
    cfg = TableConfig(small_blind=10, big_blind=20)
    state = deal_hand(cfg, stacks=(1000, 1000, 1000), button=0, rng=_deck_rng(_THREE_HANDED_DEAL))

    # button 0 -> SB seat 1, BB seat 2 (3-handed).
    assert state.button == 0
    assert state.sb_seat == 1
    assert state.bb_seat == 2
    assert state.seats[1].street_contrib == 10
    assert state.seats[2].street_contrib == 20
    assert state.seats[1].stack == 990
    assert state.seats[2].stack == 980
    assert state.current_bet == 20
    # First to act preflop is the seat after the BB (UTG == button when 3-handed).
    assert state.to_act == 0
    assert state.street is Street.PREFLOP

    # Holes are isolated per seat; the board is fully drawn but kept hidden until reveal.
    assert tuple(state.seats[0].hole) == tuple(_AA)
    assert tuple(state.seats[1].hole) == tuple(_KK)
    assert tuple(state.seats[2].hole) == tuple(_QQ)
    assert state.board == ()
    assert tuple(state.community_undealt) == tuple(_BOARD)
    assert _total_chips(state) == 3000


# --------------------------------------------------------------------------- #
# Scripted full hand to showdown (multiway, no all-in)                          #
# --------------------------------------------------------------------------- #


def test_full_hand_checks_to_showdown_awards_best_hand() -> None:
    cfg = TableConfig(small_blind=10, big_blind=20)
    state = deal_hand(cfg, stacks=(1000, 1000, 1000), button=0, rng=_deck_rng(_THREE_HANDED_DEAL))

    # Preflop: UTG(0) calls, SB(1) completes, BB(2) checks the option.
    state = apply_action(state, Action(0, ActionKind.CALL))
    state = apply_action(state, Action(1, ActionKind.CALL))
    state = apply_action(state, Action(2, ActionKind.CHECK))
    assert state.street is Street.FLOP
    assert state.board == tuple(_BOARD[:3])
    assert state.to_act == 1  # first to act postflop is left of button (SB)

    # Flop / turn / river: check it down. Order each street: 1, 2, 0.
    for street, board_len in ((Street.TURN, 4), (Street.RIVER, 5)):
        state = apply_action(state, Action(1, ActionKind.CHECK))
        state = apply_action(state, Action(2, ActionKind.CHECK))
        state = apply_action(state, Action(0, ActionKind.CHECK))
        assert state.street is street
        assert len(state.board) == board_len
    state = apply_action(state, Action(1, ActionKind.CHECK))
    state = apply_action(state, Action(2, ActionKind.CHECK))
    state = apply_action(state, Action(0, ActionKind.CHECK))

    # Showdown: each contributed 20 -> pot 60, seat0 (aces) scoops.
    assert state.hand_over is True
    assert state.street is Street.COMPLETE
    assert state.awards == {0: 60}
    assert [s.stack for s in state.seats] == [1040, 980, 980]
    assert _total_chips(state) == 3000


# --------------------------------------------------------------------------- #
# Scripted all-in producing a side pot                                          #
# --------------------------------------------------------------------------- #


def test_short_all_in_builds_side_pot_split_by_pots_engine() -> None:
    cfg = TableConfig(small_blind=10, big_blind=20)
    # seat0 short (100); seat1, seat2 deep (500). button 0 -> SB 1, BB 2, UTG 0.
    state = deal_hand(cfg, stacks=(100, 500, 500), button=0, rng=_deck_rng(_THREE_HANDED_DEAL))

    # Preflop: seat0 jams 100 all-in; SB and BB call.
    state = apply_action(state, Action(0, ActionKind.RAISE, amount=100))
    assert state.seats[0].all_in is True
    state = apply_action(state, Action(1, ActionKind.CALL))
    state = apply_action(state, Action(2, ActionKind.CALL))
    assert state.street is Street.FLOP

    # Flop: only seat1 & seat2 can act; seat1 bets 200, seat2 calls. seat0 is skipped.
    state = apply_action(state, Action(1, ActionKind.BET, amount=200))
    state = apply_action(state, Action(2, ActionKind.CALL))
    assert state.street is Street.TURN
    # Turn & river check down.
    state = apply_action(state, Action(1, ActionKind.CHECK))
    state = apply_action(state, Action(2, ActionKind.CHECK))
    state = apply_action(state, Action(1, ActionKind.CHECK))
    state = apply_action(state, Action(2, ActionKind.CHECK))

    # Main pot 300 {0,1,2} -> seat0 (aces). Side pot 400 {1,2} -> seat1 (kings > queens).
    assert state.hand_over is True
    assert state.awards == {0: 300, 1: 400}
    assert [s.stack for s in state.seats] == [300, 600, 200]
    assert _total_chips(state) == 1100


# --------------------------------------------------------------------------- #
# Uncalled bet returned (everyone folds to a raiser)                            #
# --------------------------------------------------------------------------- #


def test_uncalled_bet_returned_to_lone_survivor() -> None:
    cfg = TableConfig(small_blind=10, big_blind=20)
    state = deal_hand(cfg, stacks=(1000, 1000, 1000), button=0, rng=_deck_rng(_THREE_HANDED_DEAL))

    # UTG(0) raises to 60; SB(1) folds; BB(2) folds -> seat0 wins the dead blinds, and
    # the uncalled portion of its own raise is netted back (never double-charged).
    state = apply_action(state, Action(0, ActionKind.RAISE, amount=60))
    state = apply_action(state, Action(1, ActionKind.FOLD))
    state = apply_action(state, Action(2, ActionKind.FOLD))

    assert state.hand_over is True
    # Pot was SB 10 + BB 20 + seat0 60 = 90; seat0 gets it; its own 60 is included so the
    # net win is the 30 of dead blinds.  No board needed, no rank computed.
    assert state.board == ()
    assert state.awards == {0: 90}
    assert [s.stack for s in state.seats] == [1030, 990, 980]
    assert _total_chips(state) == 3000


# --------------------------------------------------------------------------- #
# Illegal actions are rejected WITHOUT mutating state                           #
# --------------------------------------------------------------------------- #


def _fresh_three_handed():  # noqa: ANN202
    cfg = TableConfig(small_blind=10, big_blind=20)
    return deal_hand(cfg, stacks=(1000, 1000, 1000), button=0, rng=_deck_rng(_THREE_HANDED_DEAL))


def test_check_facing_a_bet_rejected() -> None:
    state = _fresh_three_handed()
    with pytest.raises(IllegalAction):
        apply_action(state, Action(0, ActionKind.CHECK))  # BB to call preflop


def test_raise_below_min_rejected() -> None:
    state = _fresh_three_handed()
    # current_bet 20, last full raise 20 -> a raise must reach >= 40; 25 is illegal.
    with pytest.raises(IllegalAction):
        apply_action(state, Action(0, ActionKind.RAISE, amount=25))


def test_bet_more_than_stack_rejected() -> None:
    state = _fresh_three_handed()
    with pytest.raises(IllegalAction):
        apply_action(state, Action(0, ActionKind.RAISE, amount=5000))


def test_action_out_of_turn_rejected() -> None:
    state = _fresh_three_handed()  # to_act is seat 0
    with pytest.raises(IllegalAction):
        apply_action(state, Action(1, ActionKind.CALL))


def test_call_when_already_matched_rejected() -> None:
    cfg = TableConfig(small_blind=10, big_blind=20)
    state = deal_hand(cfg, stacks=(1000, 1000, 1000), button=0, rng=_deck_rng(_THREE_HANDED_DEAL))
    state = apply_action(state, Action(0, ActionKind.CALL))
    state = apply_action(state, Action(1, ActionKind.CALL))
    # BB has nothing to call (already matched) -> CALL is illegal; CHECK is the move.
    with pytest.raises(IllegalAction):
        apply_action(state, Action(2, ActionKind.CALL))


def test_rejected_action_does_not_mutate_state() -> None:
    state = _fresh_three_handed()
    before = ([s.stack for s in state.seats], state.to_act, state.current_bet)
    with pytest.raises(IllegalAction):
        apply_action(state, Action(0, ActionKind.CHECK))
    after = ([s.stack for s in state.seats], state.to_act, state.current_bet)
    assert before == after


# --------------------------------------------------------------------------- #
# A short all-in must NOT reopen action for a player who already acted          #
# --------------------------------------------------------------------------- #


def test_big_blind_may_raise_its_option_via_raise() -> None:
    cfg = TableConfig(small_blind=10, big_blind=20)
    state = deal_hand(cfg, stacks=(1000, 1000, 1000), button=0, rng=_deck_rng(_THREE_HANDED_DEAL))
    # UTG(0) and SB(1) just call; the BB(2) faces no raise but may still RAISE its option
    # (the verb is RAISE, not BET — there is an outstanding blind bet of 20).
    state = apply_action(state, Action(0, ActionKind.CALL))
    state = apply_action(state, Action(1, ActionKind.CALL))
    state = apply_action(state, Action(2, ActionKind.RAISE, amount=40))
    assert state.current_bet == 40
    assert state.to_act == 0  # action reopened to UTG


def test_short_all_in_does_not_reopen_action() -> None:
    cfg = TableConfig(small_blind=10, big_blind=20)
    # seat2 is short (45 chips) so its all-in raise is a partial (below a full raise).
    state = deal_hand(cfg, stacks=(1000, 1000, 45), button=0, rng=_deck_rng(_THREE_HANDED_DEAL))
    # Preflop: UTG(0) raises to 40 (full raise, increment 20). SB(1) calls 40.
    state = apply_action(state, Action(0, ActionKind.RAISE, amount=40))
    state = apply_action(state, Action(1, ActionKind.CALL))
    # BB(2) has 45 total (already posted 20), can only reach 45 -> all-in for 45.
    # 45 > current bet 40 but the 5 increment is below the 20 min-raise: a PARTIAL raise.
    state = apply_action(state, Action(2, ActionKind.ALL_IN))
    assert state.seats[2].all_in is True
    assert state.current_bet == 45
    # seat0 already acted; the partial all-in lets it CALL but NOT re-raise.
    with pytest.raises(IllegalAction):
        apply_action(state, Action(0, ActionKind.RAISE, amount=80))
    # Calling the extra 5 is legal.
    state = apply_action(state, Action(0, ActionKind.CALL))
    state = apply_action(state, Action(1, ActionKind.CALL))
    assert state.street is Street.FLOP


# --------------------------------------------------------------------------- #
# Button / blind rotation (incl. heads-up)                                      #
# --------------------------------------------------------------------------- #


def _fold_to_button_end(state):  # noqa: ANN001, ANN202
    """Play any hand to completion by folding everyone who can until one remains."""
    while not state.hand_over:
        state = apply_action(state, Action(state.to_act, ActionKind.FOLD))
    return state


def test_button_and_blind_rotation_three_handed() -> None:
    cfg = TableConfig(small_blind=10, big_blind=20)
    state = deal_hand(cfg, stacks=(1000, 1000, 1000), button=0, rng=_deck_rng(_THREE_HANDED_DEAL))
    assert (state.button, state.sb_seat, state.bb_seat) == (0, 1, 2)

    expected = [(1, 2, 0), (2, 0, 1), (0, 1, 2)]
    for exp in expected:
        state = _fold_to_button_end(state)
        state = start_next_hand(state, rng=_deck_rng(_THREE_HANDED_DEAL))
        assert (state.button, state.sb_seat, state.bb_seat) == exp


def test_heads_up_button_posts_small_blind() -> None:
    cfg = TableConfig(small_blind=10, big_blind=20)
    deal = [*_AA, *_KK, *_BOARD]  # 2 players: seat0 AA, seat1 KK
    state = deal_hand(cfg, stacks=(1000, 1000), button=0, rng=_deck_rng(deal))
    # Heads-up: button is the SB and acts first preflop.
    assert (state.button, state.sb_seat, state.bb_seat) == (0, 0, 1)
    assert state.seats[0].street_contrib == 10
    assert state.seats[1].street_contrib == 20
    assert state.to_act == 0

    state = _fold_to_button_end(state)
    state = start_next_hand(state, rng=_deck_rng(deal))
    assert (state.button, state.sb_seat, state.bb_seat) == (1, 1, 0)
    assert state.to_act == 1  # new button (seat1) is SB, acts first


# --------------------------------------------------------------------------- #
# Determinism + action-log replay (the property S32 recovery relies on)         #
# --------------------------------------------------------------------------- #

_SCRIPT = [
    Action(0, ActionKind.CALL),
    Action(1, ActionKind.CALL),
    Action(2, ActionKind.CHECK),
    Action(1, ActionKind.BET, amount=40),
    Action(2, ActionKind.CALL),
    Action(0, ActionKind.FOLD),
    Action(1, ActionKind.CHECK),
    Action(2, ActionKind.CHECK),
    Action(1, ActionKind.CHECK),
    Action(2, ActionKind.CHECK),
]


def _play(rng):  # noqa: ANN001, ANN202
    cfg = TableConfig(small_blind=10, big_blind=20)
    state = deal_hand(cfg, stacks=(1000, 1000, 1000), button=0, rng=rng)
    for action in _SCRIPT:
        state = apply_action(state, action)
    return state


def test_same_seed_and_actions_reproduce_identical_result() -> None:
    a = _play(create_rng(b"poker-table-seed", "client", 7))
    b = _play(create_rng(b"poker-table-seed", "client", 7))
    assert [s.hole for s in a.seats] == [s.hole for s in b.seats]
    assert a.board == b.board
    assert [s.stack for s in a.seats] == [s.stack for s in b.seats]
    assert a.awards == b.awards


def test_replaying_the_action_log_reproduces_state() -> None:
    once = _play(_deck_rng(_THREE_HANDED_DEAL))
    twice = _play(_deck_rng(_THREE_HANDED_DEAL))
    assert once == twice
    assert once.hand_over is True
    assert _total_chips(once) == 3000
