"""S29 — best-5-of-7 hand evaluator (the CARRY-FORWARD extension of S28's core).

``rank_seven`` is a thin wrapper over the shared 5-card ``rank_five`` — the best 5-of-7
is just ``max(rank_five(combo) for combo in combinations(seven, 5))``. There is exactly
ONE ranking implementation (``rank_five``); this suite proves the 7-card *selection* on
top of it, never a second ranking code path.

The assertions are layered by strength (weakest last), so the suite is non-vacuous even
though the implementation literally IS ``max(rank_five(...))``:

* **Known hands with hand-computed expected categories AND tiebreak tuples** — an
  independent oracle (does not call the implementation). The cases deliberately include
  holdings where the best 5 is NOT the "obvious" top-5 ranks (two trips, a six-card run
  that beats the wheel, a wheel that beats high-card, a flush over a genuine A-high
  straight, three pairs).
* **Property invariants that survive a reimplementation** — the returned rank is ``>=``
  every 5-subset's rank AND equals *some* 5-subset's rank. Together these define "best of
  the 21" without hard-coding ``max``. Plus total-order/transitivity on random deals.
* **The brute-force ``== max(...)`` cross-check** — redundant (it re-runs the spec), kept
  only as a belt-and-braces check; never the sole assertion on a random deal.

The seeded ``random.Random`` lives HERE (a test), never in the engine — engine purity
forbids ``random``; entropy in production enters only via the seeded ``RngStream``.
"""

from __future__ import annotations

import random
from itertools import combinations

import pytest

from engine.cards.evaluator import (
    Card,
    HandCategory,
    HandRank,
    rank_five,
    rank_seven,
)

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


# --------------------------------------------------------------------------- #
# Known hands — independent oracle (category + exact tiebreak), incl. the       #
# cases where the best 5 is NOT the obvious top-5 ranks.                        #
# --------------------------------------------------------------------------- #

# Each row: (label, 7 card codes, expected category, expected tiebreak tuple).
_KNOWN_SEVENS = [
    # Royal flush = ace-high straight flush (the top straight flush); two junk cards.
    (
        "royal flush",
        ("As", "Ks", "Qs", "Js", "Ts", "2h", "3d"),
        HandCategory.STRAIGHT_FLUSH,
        (14,),
    ),
    # Wheel straight flush — A-2-3-4-5 of one suit ranks as the 5-high straight flush.
    (
        "wheel straight flush",
        ("Ah", "2h", "3h", "4h", "5h", "Kd", "Qc"),
        HandCategory.STRAIGHT_FLUSH,
        (5,),
    ),
    # Quads from 7: 8888 + the best remaining kicker (the ace).
    (
        "four of a kind",
        ("8s", "8h", "8d", "8c", "Ah", "Kd", "2c"),
        HandCategory.FOUR_OF_A_KIND,
        (8, 14),
    ),
    # Two trips -> full house must take the HIGHER trip and the higher leftover pair:
    # KKK + QQ beats QQQ + KK because (13, 12) > (12, 13).
    (
        "two trips make the higher full house",
        ("Ks", "Kh", "Kd", "Qs", "Qh", "Qd", "2c"),
        HandCategory.FULL_HOUSE,
        (13, 12),
    ),
    # Flush over a GENUINE ace-high straight: As Ks Qs Js 9s is a flush; As Ks Qs Js Td
    # is a real A-high straight. FLUSH (5) > STRAIGHT (4), so the flush wins even though
    # the straight is the higher-looking line. (No royal: no Ts in spades.)
    (
        "flush beats a real ace-high straight",
        ("As", "Ks", "Qs", "Js", "9s", "Td", "Th"),
        HandCategory.FLUSH,
        (14, 13, 12, 11, 9),
    ),
    # Six-card run A-2-3-4-5-6: the best straight is the 6-HIGH (6-5-4-3-2), NOT the
    # wheel — guards against the ace-low trap. Suits mixed so it is not a straight flush.
    (
        "six-card run beats the wheel",
        ("Ah", "2s", "3h", "4s", "5h", "6s", "Kd"),
        HandCategory.STRAIGHT,
        (6,),
    ),
    # Wheel where the 5 highest ranks are NOT the best 5: A-2-3-4-5 is a 5-high straight
    # that beats the high-card hand A-K-Q-5-4 the naive "top 5 ranks" would pick.
    (
        "wheel beats high card",
        ("Ah", "2s", "3h", "4s", "5h", "Kd", "Qc"),
        HandCategory.STRAIGHT,
        (5,),
    ),
    # Three pairs -> best two pair is the top two (AA, KK) with the highest leftover as
    # kicker (a Q), dropping the third pair entirely.
    (
        "three pairs -> best two pair + kicker",
        ("As", "Ah", "Ks", "Kh", "Qs", "Qh", "Jd"),
        HandCategory.TWO_PAIR,
        (14, 13, 12),
    ),
    # High card: no pair, no straight, no flush. Best 5 = the five highest ranks.
    (
        "high card",
        ("2s", "5h", "9d", "Jc", "Ks", "7h", "4d"),
        HandCategory.HIGH_CARD,
        (13, 11, 9, 7, 5),
    ),
]


@pytest.mark.parametrize(
    ("label", "codes", "category", "tiebreak"),
    _KNOWN_SEVENS,
    ids=[row[0] for row in _KNOWN_SEVENS],
)
def test_known_seven_card_hands(
    label: str,
    codes: tuple[str, ...],
    category: HandCategory,
    tiebreak: tuple[int, ...],
) -> None:
    result = rank_seven(hand(*codes))
    assert result == HandRank(category, tiebreak), label


# --------------------------------------------------------------------------- #
# Property invariants over many random 7-card deals (non-vacuous: they define   #
# "best of the 21" without hard-coding max).                                    #
# --------------------------------------------------------------------------- #

_DECK = [Card.from_index(i) for i in range(52)]


def _random_sevens(seed: int, count: int) -> list[list[Card]]:
    rng = random.Random(seed)
    return [rng.sample(_DECK, 7) for _ in range(count)]


@pytest.mark.parametrize("seven", _random_sevens(seed=20260628, count=400))
def test_best_five_is_the_maximum_over_all_subsets(seven: list[Card]) -> None:
    result = rank_seven(seven)
    subset_ranks = [rank_five(list(combo)) for combo in combinations(seven, 5)]

    # The chosen rank dominates every 5-card subset ...
    for sub in subset_ranks:
        assert result >= sub
    # ... AND is actually achievable by one of them (it is a real best-5, not a
    # synthesised score). Together these two define the maximum without naming `max`.
    assert result in subset_ranks
    # Redundant belt-and-braces cross-check against the literal spec.
    assert result == max(subset_ranks)


def test_total_order_and_transitivity_over_random_deals() -> None:
    ranks = [rank_seven(s) for s in _random_sevens(seed=777, count=120)]

    # Sorting a list of HandRanks must be consistent (total order); verify the sorted
    # sequence is non-decreasing and transitivity holds on every adjacent-plus-one trio.
    ordered = sorted(ranks)
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        assert earlier <= later
    for a, b, c in zip(ordered, ordered[1:], ordered[2:], strict=False):
        if a <= b and b <= c:
            assert a <= c


def test_category_monotonicity_holds_across_deals() -> None:
    # A higher overall rank can never have a strictly lower category — the dataclass's
    # field order (category first) guarantees this; assert it on real deals.
    ranks = sorted(rank_seven(s) for s in _random_sevens(seed=4242, count=200))
    for earlier, later in zip(ranks, ranks[1:], strict=False):
        assert earlier.category <= later.category


# --------------------------------------------------------------------------- #
# Ties / showdown comparison — the contract S30 (side pots) consumes.           #
# --------------------------------------------------------------------------- #

def test_same_category_different_kicker_compares_correctly() -> None:
    # Both players make a pair of aces on a shared idea, but one has a king kicker.
    higher = rank_seven(hand("As", "Ah", "Kd", "9c", "7h", "5s", "3d"))
    lower = rank_seven(hand("Ac", "Ad", "Qd", "9h", "7s", "5h", "3c"))
    assert higher.category == lower.category == HandCategory.ONE_PAIR
    assert higher > lower


def test_identical_best_five_compare_equal() -> None:
    # Two seven-card holdings whose best 5 is the same flush (the extra two cards differ
    # and are irrelevant) must rank EQUAL — a split pot.
    a = rank_seven(hand("As", "Ks", "Qs", "Js", "9s", "2h", "3d"))
    b = rank_seven(hand("As", "Ks", "Qs", "Js", "9s", "4c", "6h"))
    assert a == b


def test_board_plays_the_board_is_a_tie() -> None:
    # A community board strong enough that both players "play the board": the same five
    # community cards are each player's best 5, so their hole cards never matter.
    board = ["Ah", "Kh", "Qh", "Jh", "Th"]  # a made royal flush on the board itself
    p1 = rank_seven(hand(*board, "2s", "3d"))
    p2 = rank_seven(hand(*board, "4c", "5c"))
    assert p1 == p2
    assert p1 == HandRank(HandCategory.STRAIGHT_FLUSH, (14,))


# --------------------------------------------------------------------------- #
# Input fence — exactly 7 distinct cards, clear ValueError.                     #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("size", [0, 5, 6, 8])
def test_wrong_card_count_raises(size: int) -> None:
    cards = _DECK[:size]
    with pytest.raises(ValueError, match="exactly 7"):
        rank_seven(cards)


def test_duplicate_cards_raise() -> None:
    dupe = hand("As", "As", "Ks", "Qs", "Js", "Ts", "9s")
    with pytest.raises(ValueError, match="distinct"):
        rank_seven(dupe)
