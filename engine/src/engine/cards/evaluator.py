"""The shared 5-card hand-rank evaluator — the CARRY-FORWARD core (S28 → S29).

A single, GAME-AGNOSTIC ranking of any 5 cards into a *totally ordered*
:class:`HandRank`. The order is ``(category, tiebreak)`` where ``category`` is an
:class:`IntEnum` (``HIGH_CARD`` … ``STRAIGHT_FLUSH``) and ``tiebreak`` is a tuple of
ranks in descending significance — so two hands compare with the natural tuple order
and ``HandRank`` is directly sortable.

**Designed to be shared (the whole point of the carry-forward).** ``rank_five`` is
the only ranking logic. S29's best-5-of-7 evaluator is a thin wrapper —
``max(rank_five(combo) for combo in combinations(seven, 5))`` — that reuses this core
verbatim, never re-implementing it. Keeping the evaluator game-agnostic is load-bearing:
it knows "one pair of Jacks", NOT "jacks-or-better" — the minimum *paying* pair is a
video-poker *paytable* concern, applied on top in ``engine.table.video_poker``.

**The ace's dual role.** An ace ranks high (14) by default, but the *wheel*
``A-2-3-4-5`` is the LOWEST straight (5-high) — the ace plays low only to complete
that one straight. ``A-K-Q-J-T`` is the ace-high straight; the ace-high straight flush
is the royal flush (the top straight flush).

Pure: stdlib only (``collections.Counter``); no IO, clock, or randomness. Card ranks
are ``2..14`` (``J=11, Q=12, K=13, A=14``); suits are ``0..3`` (suit identity never
affects rank — only "all five share a suit").
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum

# Rank constants for readers; the integer values double as comparison keys.
_ACE = 14
_LOW_ACE_WHEEL = (_ACE, 5, 4, 3, 2)  # the only straight where the ace plays low
_WHEEL_HIGH = 5  # a wheel ranks as a 5-high straight
_HAND_SIZE = 5


class HandCategory(IntEnum):
    """The nine poker hand categories, ordered weakest → strongest.

    Royal flush is NOT a separate category — it is the ace-high ``STRAIGHT_FLUSH``
    (``tiebreak == (14,)``); games that pay it specially (video poker) detect that
    from the rank, keeping the evaluator game-agnostic.
    """

    HIGH_CARD = 0
    ONE_PAIR = 1
    TWO_PAIR = 2
    THREE_OF_A_KIND = 3
    STRAIGHT = 4
    FLUSH = 5
    FULL_HOUSE = 6
    FOUR_OF_A_KIND = 7
    STRAIGHT_FLUSH = 8


@dataclass(frozen=True, order=True)
class Card:
    """One playing card. ``rank`` ∈ ``2..14`` (``J=11 Q=12 K=13 A=14``); ``suit`` ∈ ``0..3``."""

    rank: int
    suit: int

    @classmethod
    def from_index(cls, index: int) -> Card:
        """Map a deck index ``0..51`` to a card: ``rank = index // 4 + 2``, ``suit = index % 4``.

        This is the canonical index↔card bijection the deck-driven games deal through
        (``sample_without_replacement(rng, range(52), …)`` yields indices); it makes
        the dealt cards a pure function of the seed.
        """
        if not 0 <= index < 52:
            raise ValueError(f"card index {index} out of range [0, 51]")
        return cls(rank=index // 4 + 2, suit=index % 4)


@dataclass(frozen=True, order=True)
class HandRank:
    """A totally ordered hand strength: compare by ``category`` then ``tiebreak``.

    ``tiebreak`` lists the decisive ranks in descending importance (e.g. a full house
    is ``(trip_rank, pair_rank)``; a one-pair hand is ``(pair_rank, *kickers_desc)``),
    so the dataclass's field-wise ordering is exactly poker tiebreak order. Sortable and
    hashable — S29's best-5-of-7 is just ``max`` over the C(7,5) ``rank_five`` results.
    """

    category: HandCategory
    tiebreak: tuple[int, ...]


def _straight_high(distinct_desc: list[int]) -> int | None:
    """Return the straight's high card if the 5 distinct ranks form a straight, else None.

    Handles the wheel (``A-2-3-4-5``) as a 5-high straight (ace low); every other
    straight's high card is simply its top rank.
    """
    if len(distinct_desc) != _HAND_SIZE:
        return None
    if tuple(distinct_desc) == _LOW_ACE_WHEEL:
        return _WHEEL_HIGH
    if distinct_desc[0] - distinct_desc[-1] == 4:
        return distinct_desc[0]
    return None


def rank_five(cards: Sequence[Card]) -> HandRank:
    """Rank exactly 5 cards into a totally ordered :class:`HandRank`.

    Pure and game-agnostic — the single ranking core reused everywhere. Raises
    ``ValueError`` unless given 5 cards.
    """
    if len(cards) != _HAND_SIZE:
        raise ValueError(f"rank_five needs exactly 5 cards, got {len(cards)}")

    ranks_desc = sorted((c.rank for c in cards), reverse=True)
    is_flush = len({c.suit for c in cards}) == 1
    counts = Counter(ranks_desc)
    # Groups ordered by (multiplicity, rank), both descending: the strongest group
    # (e.g. the trips of a full house, the higher of two pairs) comes first, so the
    # tiebreak tuple reads in poker significance order.
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    group_ranks = tuple(rank for rank, _ in groups)
    shape = tuple(count for _, count in groups)  # e.g. (3, 2) = full house

    straight_high = _straight_high(sorted(counts, reverse=True))

    if straight_high is not None and is_flush:
        return HandRank(HandCategory.STRAIGHT_FLUSH, (straight_high,))
    if shape == (4, 1):
        return HandRank(HandCategory.FOUR_OF_A_KIND, group_ranks)
    if shape == (3, 2):
        return HandRank(HandCategory.FULL_HOUSE, group_ranks)
    if is_flush:
        return HandRank(HandCategory.FLUSH, tuple(ranks_desc))
    if straight_high is not None:
        return HandRank(HandCategory.STRAIGHT, (straight_high,))
    if shape == (3, 1, 1):
        return HandRank(HandCategory.THREE_OF_A_KIND, group_ranks)
    if shape == (2, 2, 1):
        return HandRank(HandCategory.TWO_PAIR, group_ranks)
    if shape == (2, 1, 1, 1):
        return HandRank(HandCategory.ONE_PAIR, group_ranks)
    return HandRank(HandCategory.HIGH_CARD, tuple(ranks_desc))
