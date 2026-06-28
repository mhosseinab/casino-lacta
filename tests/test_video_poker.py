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

from engine.cards.evaluator import Card, HandCategory, HandRank, rank_five

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
