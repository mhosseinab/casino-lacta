"""S28 — Video Poker (9/6 Jacks-or-Better) + the shared 5-card evaluator.

Three gates (spec §B.5):

* **Hand-rank correctness** — the shared ``engine.cards.evaluator`` ranks every
  category (high card → royal flush), with tiebreaks and the ace's dual role
  (wheel A-2-3-4-5 = the *lowest* straight, A-K-Q-J-T = ace-high, royal = ace-high
  straight flush). The evaluator is GAME-AGNOSTIC: it knows "one pair of Js", never
  "jacks-or-better" — the minimum paying pair is a video-poker *paytable* concern.
  This is the CARRY-FORWARD core S29 extends to best-5-of-7 (``max`` over C(7,5)).
* **RTP under the optimal-for-fixtures strategy** — for curated deals the optimal
  hold's expected return is computed by EXACT enumeration of the C(47, d) draws from
  the 47 unseen cards (discards are NOT returned to the deck). Exact, no Monte-Carlo
  tolerance fragility. A coarse ``rtp_heavy`` sampled-MC band sanity-checks a
  documented heuristic strategy (exact optimal 99.54% is not MC-verifiable in pure
  Python — see ``test_rtp_heavy_*``).
* **Determinism** — the 10-card commit + the draw are a pure function of the seed.

The strategy / EV / optimal-hold helpers live HERE, not in the engine: the engine
decides outcomes (deal → draw → evaluate); it never picks holds.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from engine.cards.evaluator import Card, HandCategory, HandRank, rank_five
from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.table.video_poker import DEFAULT_PAYTABLE, score
from engine.types import GameConfig, InvalidBetInput, RngStream, StatefulGame

GAME_ID = "table.video_poker"
SERVER_SEED = b"video-poker-server-seed"
CLIENT_SEED = "player-client-seed"


def _game() -> StatefulGame:
    return cast("StatefulGame", load_game(GAME_ID))


def _cfg() -> GameConfig:
    return default_config(GAME_ID)

# --------------------------------------------------------------------------- #
# Card construction helpers (rank 2..14: J=11 Q=12 K=13 A=14; suit 0..3)        #
# --------------------------------------------------------------------------- #

_RANK_CHARS = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "T": 10,
    "J": 11, "Q": 12, "K": 13, "A": 14,
}
_SUIT_CHARS = {"s": 0, "h": 1, "d": 2, "c": 3}


def card(code: str) -> Card:
    """``card("As")`` → the ace of spades. Two chars: rank then suit."""
    return Card(rank=_RANK_CHARS[code[0]], suit=_SUIT_CHARS[code[1]])


def hand(*codes: str) -> list[Card]:
    return [card(c) for c in codes]


def rank(*codes: str) -> HandRank:
    return rank_five(hand(*codes))


# --------------------------------------------------------------------------- #
# Hand-rank category recognition                                               #
# --------------------------------------------------------------------------- #


def test_each_category_is_recognised() -> None:
    assert rank("As", "Ks", "Qs", "Js", "Ts").category is HandCategory.STRAIGHT_FLUSH
    assert rank("9s", "8s", "7s", "6s", "5s").category is HandCategory.STRAIGHT_FLUSH
    assert rank("9s", "9h", "9d", "9c", "2s").category is HandCategory.FOUR_OF_A_KIND
    assert rank("9s", "9h", "9d", "2c", "2s").category is HandCategory.FULL_HOUSE
    assert rank("As", "9s", "7s", "5s", "3s").category is HandCategory.FLUSH
    assert rank("9s", "8h", "7d", "6c", "5s").category is HandCategory.STRAIGHT
    assert rank("9s", "9h", "9d", "5c", "2s").category is HandCategory.THREE_OF_A_KIND
    assert rank("9s", "9h", "5d", "5c", "2s").category is HandCategory.TWO_PAIR
    assert rank("9s", "9h", "5d", "3c", "2s").category is HandCategory.ONE_PAIR
    assert rank("As", "Jh", "9d", "5c", "3s").category is HandCategory.HIGH_CARD


def test_category_strict_ordering() -> None:
    """The 9 categories form a strict total order (royal is the top straight flush)."""
    ladder = [
        rank("As", "Jh", "9d", "5c", "3s"),   # high card
        rank("9s", "9h", "5d", "3c", "2s"),   # one pair
        rank("9s", "9h", "5d", "5c", "2s"),   # two pair
        rank("9s", "9h", "9d", "5c", "2s"),   # trips
        rank("9s", "8h", "7d", "6c", "5s"),   # straight
        rank("As", "9s", "7s", "5s", "3s"),   # flush
        rank("9s", "9h", "9d", "2c", "2s"),   # full house
        rank("9s", "9h", "9d", "9c", "2s"),   # quads
        rank("9s", "8s", "7s", "6s", "5s"),   # straight flush
    ]
    assert ladder == sorted(ladder)
    assert len(set(ladder)) == len(ladder)


# --------------------------------------------------------------------------- #
# Tiebreaks within a category                                                  #
# --------------------------------------------------------------------------- #


def test_high_card_tiebreak_by_each_rank() -> None:
    assert rank("As", "Kh", "Qd", "Jc", "9s") > rank("As", "Kh", "Qd", "Jc", "8s")
    assert rank("Ah", "Qd", "9c", "5s", "3h") > rank("Kh", "Qd", "9c", "5s", "3h")


def test_one_pair_tiebreak_pair_then_kickers() -> None:
    assert rank("Ks", "Kh", "5d", "3c", "2s") > rank("Qs", "Qh", "Ad", "Kc", "Js")
    # same pair → kicker decides
    assert rank("9s", "9h", "Ad", "3c", "2s") > rank("9s", "9h", "Kd", "3c", "2s")


def test_two_pair_tiebreak_high_then_low_then_kicker() -> None:
    assert rank("Ks", "Kh", "2d", "2c", "5s") > rank("Qs", "Qh", "Jd", "Jc", "As")
    assert rank("Ks", "Kh", "5d", "5c", "9s") > rank("Ks", "Kh", "3d", "3c", "As")
    assert rank("Ks", "Kh", "5d", "5c", "As") > rank("Ks", "Kh", "5d", "5c", "9s")


def test_trips_and_full_house_and_quads_tiebreaks() -> None:
    assert rank("Ks", "Kh", "Kd", "3c", "2s") > rank("Qs", "Qh", "Qd", "Ac", "Ks")
    assert rank("Ks", "Kh", "Kd", "2c", "2s") > rank("Qs", "Qh", "Qd", "Ac", "As")
    assert rank("Ks", "Kh", "Kd", "Kc", "2s") > rank("Qs", "Qh", "Qd", "Qc", "As")
    # quads same rank → kicker
    assert rank("Ks", "Kh", "Kd", "Kc", "As") > rank("Ks", "Kh", "Kd", "Kc", "Qs")


# --------------------------------------------------------------------------- #
# The ace's dual role                                                          #
# --------------------------------------------------------------------------- #


def test_wheel_is_the_lowest_straight() -> None:
    wheel = rank("As", "2h", "3d", "4c", "5s")
    assert wheel.category is HandCategory.STRAIGHT
    # wheel ranks as 5-high, below a 6-high straight …
    assert rank("6s", "5h", "4d", "3c", "2s") > wheel
    # … and the ace is LOW here: a wheel loses to any higher straight.
    assert rank("Ts", "9h", "8d", "7c", "6s") > wheel


def test_broadway_is_ace_high_straight() -> None:
    broadway = rank("As", "Kh", "Qd", "Jc", "Ts")
    assert broadway.category is HandCategory.STRAIGHT
    assert broadway > rank("Ks", "Qh", "Jd", "Tc", "9s")
    assert broadway > rank("As", "2h", "3d", "4c", "5s")  # broadway > wheel


def test_royal_and_wheel_straight_flush() -> None:
    royal = rank("As", "Ks", "Qs", "Js", "Ts")
    steel_wheel = rank("As", "2s", "3s", "4s", "5s")
    assert royal.category is HandCategory.STRAIGHT_FLUSH
    assert steel_wheel.category is HandCategory.STRAIGHT_FLUSH
    # royal is the highest straight flush; the steel wheel is the lowest.
    assert royal > rank("Ks", "Qs", "Js", "Ts", "9s") > steel_wheel


def test_card_from_index_round_trips_all_52() -> None:
    seen = {Card.from_index(i) for i in range(52)}
    assert len(seen) == 52
    for i in range(52):
        c = Card.from_index(i)
        assert 2 <= c.rank <= 14
        assert 0 <= c.suit <= 3


# --------------------------------------------------------------------------- #
# The paytable mapping — score() is the pure paytable layer (no rng)            #
# --------------------------------------------------------------------------- #
#
# score() is tested directly so the paytable tiers are pinned WITHOUT forcing
# specific cards out of the seeded stream. The 9/6 Jacks-or-Better full-pay
# schedule (per unit bet, max-coin-equivalent royal): RF 800, SF 50, 4oak 25,
# FH 9, flush 6, straight 4, trips 3, two pair 2, jacks-or-better 1, else 0.


def _score(*codes: str) -> tuple[str | None, float]:
    key, mult = score(hand(*codes), DEFAULT_PAYTABLE, 11)
    return key, mult


def test_paytable_full_pay_9_6_schedule() -> None:
    assert _score("As", "Ks", "Qs", "Js", "Ts") == ("ROYAL_FLUSH", 800.0)
    assert _score("9s", "8s", "7s", "6s", "5s") == ("STRAIGHT_FLUSH", 50.0)
    assert _score("As", "2s", "3s", "4s", "5s") == ("STRAIGHT_FLUSH", 50.0)  # steel wheel
    assert _score("9s", "9h", "9d", "9c", "2s") == ("FOUR_OF_A_KIND", 25.0)
    assert _score("9s", "9h", "9d", "2c", "2s") == ("FULL_HOUSE", 9.0)
    assert _score("As", "9s", "7s", "5s", "3s") == ("FLUSH", 6.0)
    assert _score("9s", "8h", "7d", "6c", "5s") == ("STRAIGHT", 4.0)
    assert _score("As", "2h", "3d", "4c", "5s") == ("STRAIGHT", 4.0)  # wheel
    assert _score("9s", "9h", "9d", "5c", "2s") == ("THREE_OF_A_KIND", 3.0)
    assert _score("9s", "9h", "5d", "5c", "2s") == ("TWO_PAIR", 2.0)


def test_paytable_jacks_or_better_qualifies_only_at_or_above_jacks() -> None:
    assert _score("Js", "Jh", "5d", "3c", "2s") == ("JACKS_OR_BETTER", 1.0)
    assert _score("Qs", "Qh", "5d", "3c", "2s") == ("JACKS_OR_BETTER", 1.0)
    assert _score("As", "Ah", "5d", "3c", "2s") == ("JACKS_OR_BETTER", 1.0)
    # A pair below Jacks does NOT pay (the "jacks-or-better" rule is a paytable
    # concern, NOT baked into the game-agnostic evaluator).
    assert _score("Ts", "Th", "5d", "3c", "2s") == (None, 0.0)
    assert _score("2s", "2h", "5d", "3c", "9s") == (None, 0.0)
    # No pair → high card pays nothing.
    assert _score("As", "Kh", "9d", "5c", "3s") == (None, 0.0)


def test_min_pair_rank_is_configurable() -> None:
    """A 'Tens or better' variant pays a pair of tens; 'Kings or better' does not pay Js."""
    assert score(hand("Ts", "Th", "5d", "3c", "2s"), DEFAULT_PAYTABLE, 10) == (
        "JACKS_OR_BETTER",
        1.0,
    )
    assert score(hand("Js", "Jh", "5d", "3c", "2s"), DEFAULT_PAYTABLE, 13) == (None, 0.0)


# --------------------------------------------------------------------------- #
# Deterministic-stream helpers for end-to-end game tests                        #
# --------------------------------------------------------------------------- #


def idx(code: str) -> int:
    """Card code → deck index (inverse of ``Card.from_index``)."""
    c = card(code)
    return (c.rank - 2) * 4 + c.suit


@dataclass
class _FakeRng:
    """A deterministic stub stream yielding preset floats (drives a chosen deal)."""

    values: list[float]
    i: int = field(default=0)

    def next(self) -> float:
        v = self.values[self.i]
        self.i += 1
        return v


def floats_for_sample(targets: Sequence[int], population_size: int = 52) -> list[float]:
    """The floats that make ``sample_without_replacement`` emit ``targets`` in order.

    Mirrors the engine's partial Fisher-Yates: at step ``i`` it picks index
    ``i + floor(f*(n-i))`` from the live pool, so to land on a target we solve for the
    mid-bucket float at the target's current pool position. Lets a test force an exact
    committed deck through the real engine sampler.
    """
    pool = list(range(population_size))
    out: list[float] = []
    n = population_size
    for i, target in enumerate(targets):
        j = pool.index(target, i)
        out.append((j - i + 0.5) / (n - i))
        pool[i], pool[j] = pool[j], pool[i]
    return out


# --------------------------------------------------------------------------- #
# The video-poker game — deal / hold / draw / evaluate (StatefulGame)           #
# --------------------------------------------------------------------------- #


def test_init_commits_ten_distinct_cards() -> None:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 1)
    state = _game().init({}, rng, _cfg())
    assert state["status"] == "ACTIVE"
    committed = list(state["dealt"]) + list(state["pool"])
    assert len(committed) == 10
    assert len(set(committed)) == 10  # all distinct (drawn without replacement)
    assert all(0 <= c < 52 for c in committed)
    # init consumes exactly 10 draws from the stream (the committed deck).
    assert rng.cursor == 10


def test_draw_replaces_only_discarded_positions() -> None:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 2)
    state = _game().init({}, rng, _cfg())
    dealt = list(state["dealt"])
    pool = list(state["pool"])
    # hold positions 0 and 1; positions 2,3,4 are redrawn from the committed pool.
    state2, outcome = _game().step(state, {"op": "draw", "holds": [0, 1]}, rng)
    assert outcome is not None
    final = list(state2["final"])
    assert final[0] == dealt[0]
    assert final[1] == dealt[1]
    assert final[2] == pool[0]
    assert final[3] == pool[1]
    assert final[4] == pool[2]


def test_hold_all_five_is_a_pat_hand_no_replacement() -> None:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 3)
    state = _game().init({}, rng, _cfg())
    dealt = list(state["dealt"])
    state2, outcome = _game().step(state, {"op": "draw", "holds": [0, 1, 2, 3, 4]}, rng)
    assert outcome is not None
    assert list(state2["final"]) == dealt
    # the engine's multiplier equals the pure paytable score of the pat hand.
    _, expected = score([Card.from_index(i) for i in dealt], DEFAULT_PAYTABLE, 11)
    assert outcome.multiplier == expected


def test_discard_all_five_draws_a_fresh_hand() -> None:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 4)
    state = _game().init({}, rng, _cfg())
    pool = list(state["pool"])
    state2, outcome = _game().step(state, {"op": "draw", "holds": []}, rng)
    assert outcome is not None
    assert list(state2["final"]) == pool  # all five replaced, in pool order


def test_forced_deal_pays_the_evaluated_hand() -> None:
    """End-to-end through the REAL engine sampler: force a dealt four-to-royal + an
    off card, hold the four royal cards, and complete the royal from the committed pool.
    Proves deal → hold/draw → evaluate → paytable multiplier."""
    # committed deck (draw order): As Ks Qs Js 2h | Ts ... (5 fillers)
    committed = ["As", "Ks", "Qs", "Js", "2h", "Ts", "3h", "4h", "6c", "7d"]
    rng = _FakeRng(floats_for_sample([idx(c) for c in committed]))
    state = _game().init({}, cast("RngStream", rng), _cfg())
    assert [Card.from_index(i) for i in state["dealt"]] == hand(*committed[:5])
    # hold the four royal spades (positions 0-3), discard the 2h (position 4).
    state2, outcome = _game().step(
        state, {"op": "draw", "holds": [0, 1, 2, 3]}, cast("RngStream", rng)
    )
    assert outcome is not None
    assert [Card.from_index(i) for i in state2["final"]] == hand("As", "Ks", "Qs", "Js", "Ts")
    assert outcome.detail["handKey"] == "ROYAL_FLUSH"
    assert outcome.multiplier == 800.0
    assert outcome.detail["status"] == "CASHED_OUT"


def test_status_cashed_out_on_a_paying_hand_lost_otherwise() -> None:
    # forced pat full house → CASHED_OUT with the FH multiplier.
    fh = ["9s", "9h", "9d", "2c", "2s", "3h", "4h", "6c", "7d", "8d"]
    rng = _FakeRng(floats_for_sample([idx(c) for c in fh]))
    state = _game().init({}, cast("RngStream", rng), _cfg())
    _, win = _game().step(state, {"op": "draw", "holds": [0, 1, 2, 3, 4]}, cast("RngStream", rng))
    assert win is not None
    assert win.detail["status"] == "CASHED_OUT"
    assert win.multiplier == 9.0
    # forced pat garbage (no pair, no draw) → LOST with zero multiplier.
    junk = ["As", "Kh", "9d", "5c", "3s", "2h", "4h", "6c", "7d", "8d"]
    rng2 = _FakeRng(floats_for_sample([idx(c) for c in junk]))
    state2 = _game().init({}, cast("RngStream", rng2), _cfg())
    _, lose = _game().step(
        state2, {"op": "draw", "holds": [0, 1, 2, 3, 4]}, cast("RngStream", rng2)
    )
    assert lose is not None
    assert lose.detail["status"] == "LOST"
    assert lose.multiplier == 0.0


# --------------------------------------------------------------------------- #
# Redaction (public_view) + the state machine + input fence                     #
# --------------------------------------------------------------------------- #


def test_public_view_hides_pool_while_active_reveals_at_terminal() -> None:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 5)
    state = _game().init({}, rng, _cfg())
    view = _game().public_view(state)
    assert view["status"] == "ACTIVE"
    assert view["dealtCards"] == list(state["dealt"])  # dealt 5 are public
    # The whitelist IS the redaction contract: while ACTIVE the only keys are status +
    # the dealt 5, so the committed replacement pool (the secret upcoming draws) cannot
    # leak through any field. (Asserting key-equality, not substring absence, makes this
    # non-vacuous — a leaked pool would add a key and fail.)
    assert set(view) == {"status", "dealtCards"}
    state2, _ = _game().step(state, {"op": "draw", "holds": [0]}, rng)
    term = _game().public_view(state2)
    assert term["status"] in {"CASHED_OUT", "LOST"}
    assert term["finalCards"] == list(state2["final"])
    assert "handCategory" in term


def test_terminal_round_rejects_further_actions() -> None:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 6)
    state = _game().init({}, rng, _cfg())
    state2, _ = _game().step(state, {"op": "draw", "holds": [0, 1]}, rng)
    with pytest.raises(InvalidBetInput):
        _game().step(state2, {"op": "draw", "holds": [0]}, rng)


def test_unknown_op_rejected() -> None:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 7)
    state = _game().init({}, rng, _cfg())
    with pytest.raises(InvalidBetInput):
        _game().step(state, {"op": "cashout"}, rng)


@pytest.mark.parametrize(
    "holds",
    [
        [0, 1, 5],        # position out of range
        [0, 0, 1],        # duplicate position
        [-1],             # negative
        "01",             # not a list
        [True, 1],        # bool masquerading as int
        [1.0],            # float position
    ],
)
def test_bad_holds_rejected(holds: Any) -> None:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 8)
    state = _game().init({}, rng, _cfg())
    with pytest.raises(InvalidBetInput):
        _game().step(state, {"op": "draw", "holds": holds}, rng)


def test_validate_input_accepts_empty() -> None:
    assert _game().validate_input({}, _cfg()) is None


# --------------------------------------------------------------------------- #
# Determinism                                                                   #
# --------------------------------------------------------------------------- #


def _run(nonce: int, holds: list[int]) -> tuple[list[int], float]:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    state = _game().init({}, rng, _cfg())
    state2, outcome = _game().step(state, {"op": "draw", "holds": holds}, rng)
    assert outcome is not None
    return list(state2["final"]), outcome.multiplier


def test_same_seed_same_holds_same_result() -> None:
    a = _run(42, [0, 2, 4])
    b = _run(42, [0, 2, 4])
    assert a == b


def test_distinct_nonces_diverge() -> None:
    deals = {tuple(_run(n, [0, 1, 2, 3, 4])[0]) for n in range(40)}
    assert len(deals) > 1


# --------------------------------------------------------------------------- #
# RTP under the optimal-for-fixtures strategy — EXACT enumeration (no MC).       #
# --------------------------------------------------------------------------- #
#
# For a curated deal the optimal hold's expected return is the mean paytable
# multiplier over EVERY possible draw: enumerate the C(47, d) ways to fill the d
# discarded slots from the 47 unseen cards (discards are NOT returned to the deck).
# Exact — no Monte-Carlo tolerance fragility. We assert the figure against an
# independently hand-derived value, and that the optimal hold beats alternatives.


def _remaining_indices(dealt: Sequence[int]) -> list[int]:
    held = set(dealt)
    return [i for i in range(52) if i not in held]


def hand_ev(dealt: Sequence[int], hold_positions: Sequence[int]) -> float:
    """Exact expected paytable multiplier of holding ``hold_positions`` from ``dealt``."""
    from itertools import combinations as _combos

    kept = [dealt[p] for p in hold_positions]
    discard_count = 5 - len(kept)
    pool = _remaining_indices(dealt)
    if discard_count == 0:
        cards = [Card.from_index(i) for i in dealt]
        return score(cards, DEFAULT_PAYTABLE, 11)[1]
    total = 0.0
    n = 0
    for combo in _combos(pool, discard_count):
        cards = [Card.from_index(i) for i in (*kept, *combo)]
        total += score(cards, DEFAULT_PAYTABLE, 11)[1]
        n += 1
    return total / n


def test_four_to_a_royal_ev_matches_hand_derived_value() -> None:
    """Dealt As Ks Qs Js 2h; hold the four royal spades, draw 1 from 47.
    47 unseen split into: 1 Ts (royal 800), 8 other spades (flush 6), 12 high pairs
    (A/K/Q/J → 1), 3 off-suit tens (straight 4), 23 blanks (0)."""
    dealt = [idx(c) for c in ("As", "Ks", "Qs", "Js", "2h")]
    ev = hand_ev(dealt, [0, 1, 2, 3])
    expected = (1 * 800 + 8 * 6 + 12 * 1 + 3 * 4) / 47  # = 872/47
    assert ev == pytest.approx(expected, rel=1e-12)
    # holding the four royal cards is optimal vs. obvious alternatives:
    assert ev > hand_ev(dealt, [0, 1, 2, 3, 4])  # > the pat A-high junk (EV 0)
    assert ev > hand_ev(dealt, [0, 1, 2])         # > holding only three to the royal


def test_made_flush_is_pat_with_ev_exactly_six() -> None:
    dealt = [idx(c) for c in ("As", "9s", "7s", "5s", "3s")]
    assert hand_ev(dealt, [0, 1, 2, 3, 4]) == 6.0
    # discarding any card strictly lowers EV (drawing rarely re-pairs into >6 EV).
    assert hand_ev(dealt, [0, 1, 2, 3, 4]) > hand_ev(dealt, [0, 1, 2, 3])


def test_high_pair_hold_beats_kicker_chasing() -> None:
    """Dealt JJ + three blanks: holding the pair (draw 3) beats holding pair+kicker."""
    dealt = [idx(c) for c in ("Js", "Jh", "8d", "5c", "2s")]
    pair_only = hand_ev(dealt, [0, 1])
    assert pair_only > 1.0  # a guaranteed JoB (1.0) plus upside from trips/quads/2pair
    assert pair_only > hand_ev(dealt, [0, 1, 2])  # the 8 kicker only hurts


# --------------------------------------------------------------------------- #
# Coarse sampled-MC sanity band under a documented heuristic strategy.          #
# --------------------------------------------------------------------------- #
#
# The exact optimal-play RTP (published 99.54% for 9/6 JoB) is NOT verifiable by
# Monte-Carlo in pure Python: per-deal optimal EV is C(52,5) ≈ 2.6M rankings, and a
# sampled-draw MC floors out near ±0.7% tol at n=1e7 because the royal flush (800x,
# p≈2.5e-5) dominates variance. So this is a COARSE SANITY BAND on a documented
# heuristic strategy — NOT a tight gate (the exact gate is the fixture EVs above).
# numpy/random are test-only; the engine stays pure.

import random as _random  # noqa: E402  (test-only entropy; never in engine/)


def _heuristic_hold(dealt_cards: list[Card]) -> list[int]:
    """A documented SIMPLE Jacks-or-Better hold heuristic (positions to keep).

    Deliberately basic — it omits several positive-EV holds (4-to-royal over a pat
    straight, 3-to-royal, suited high cards, inside straight flushes), so its realised
    RTP lands ~0.977, a couple of points under the 0.9954 optimal value. That is fine:
    this drives only the COARSE sanity band below, NOT the exact paytable gate (which
    is the per-fixture EV assertions above)."""
    from collections import Counter as _Counter

    ranks = [c.rank for c in dealt_cards]
    suits = [c.suit for c in dealt_cards]
    rc = _Counter(ranks)
    sc = _Counter(suits)
    hr = rank_five(dealt_cards)
    cat = hr.category
    # Pat made hands worth keeping whole.
    if cat >= HandCategory.STRAIGHT:
        return [0, 1, 2, 3, 4]
    # Trips / two pair / a paying (jacks-or-better) pair → hold the matched ranks.
    if cat == HandCategory.THREE_OF_A_KIND or cat == HandCategory.TWO_PAIR:
        keep_ranks = {r for r, n in rc.items() if n >= 2}
        return [i for i, c in enumerate(dealt_cards) if c.rank in keep_ranks]
    # 4 to a flush.
    flush_suit = next((s for s, n in sc.items() if n == 4), None)
    if flush_suit is not None:
        return [i for i, c in enumerate(dealt_cards) if c.suit == flush_suit]
    if cat == HandCategory.ONE_PAIR:
        keep_ranks = {r for r, n in rc.items() if n == 2}
        return [i for i, c in enumerate(dealt_cards) if c.rank in keep_ranks]
    # 4 to an outside straight.
    distinct = sorted(set(ranks))
    if len(distinct) == 5 and distinct[3] - distinct[0] == 3 and distinct[0] >= 3:
        keep = set(distinct[:4]) if (distinct[4] - distinct[1] != 3) else set(distinct[1:])
        return [i for i, c in enumerate(dealt_cards) if c.rank in keep]
    # Otherwise hold high cards (J+), else discard everything.
    highs = [i for i, c in enumerate(dealt_cards) if c.rank >= 11]
    return highs


@pytest.mark.rtp_heavy
def test_rtp_heavy_sanity_band_under_heuristic() -> None:
    rng = _random.Random(20260628)
    n = 1_000_000
    total = 0.0
    deck = list(range(52))
    for _ in range(n):
        rng.shuffle(deck)
        dealt = deck[:5]
        cards = [Card.from_index(i) for i in dealt]
        holds = set(_heuristic_hold(cards))
        discarded = [p for p in range(5) if p not in holds]
        final = list(dealt)
        for j, pos in enumerate(discarded):
            final[pos] = deck[5 + j]
        total += score([Card.from_index(i) for i in final], DEFAULT_PAYTABLE, 11)[1]
    rtp = total / n
    # COARSE band only: this SIMPLE heuristic lands ~0.977 (a couple of points under the
    # 0.9954 optimal value it leaves on the table — see _heuristic_hold). We assert the
    # full-game return is merely in a sane neighbourhood of the optimal value, proving the
    # paytable produces a realistic ~unity RTP — NOT that it equals 0.9954 (unreachable by
    # pure-Python MC). The exact paytable→EV gate is the per-fixture assertions above.
    assert 0.96 < rtp < 1.00, f"heuristic RTP {rtp:.5f} outside sanity band"
