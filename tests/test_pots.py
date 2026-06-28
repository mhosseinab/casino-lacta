"""S30 — main + side-pot engine: conservation, eligibility, splits, odd chips.

The headline invariant is **chip conservation**: every minor unit contributed is awarded
to exactly one player — nothing created, nothing lost — under ANY multi-all-in / fold
configuration. Conservation is necessary but the *weakest* check (a wrong-winner or
dropped-odd-chip bug still conserves), so the property test asserts more than sum-equality
(recipients are non-folded, ⊆ the eligible union, and each award is positive), and the
generator deliberately hits the hard paths: many distinct contribution levels (real side
pots), fold sets that include the top contributor (cascade + eligibility), and a *small*
rank space so ties — hence splits and the odd-chip path — are frequent.

``award_pots`` consumes a pre-computed ``Mapping[player, comparable rank]`` (the ISP seam
the step blesses), so ``pots.py`` never ranks anything — ranking has exactly one owner, the
evaluator. One test threads real ``rank_seven`` output through ``award_pots`` to prove the
seam against the actual evaluator; the rest use plain ints to force ties cheaply.

The seeded ``random.Random`` / Hypothesis live HERE (tests), never in the engine — engine
purity forbids ``random``.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.cards.evaluator import Card, rank_seven
from engine.poker.pots import Pot, award_pots, build_pots

# --------------------------------------------------------------------------- #
# Hand-built worked scenarios — independent oracles (exact amounts + winners)  #
# --------------------------------------------------------------------------- #


def test_no_contributions_build_is_empty() -> None:
    assert build_pots({}, set()) == []


def test_award_no_pots_is_empty() -> None:
    assert award_pots([], {}) == {}


def test_award_raises_when_no_eligible_player_anywhere() -> None:
    # Conservation last-line-of-defense: EVERY contributor folded, so no pot has an
    # eligible winner and the chips have nowhere to go. Awarding must RAISE, not silently
    # drop the chips (regressing the guard to `return awards` would vanish 200 chips).
    pots = build_pots({0: 100, 1: 100}, folded={0, 1})
    assert pots == [Pot(amount=200, eligible=())]  # chips in, eligible nowhere
    with pytest.raises(ValueError, match="no eligible players"):
        award_pots(pots, {0: 5, 1: 5})


def test_build_pots_rejects_invalid_contributions() -> None:
    # The minor-units fence: floats, bool-as-int, and negatives are not valid chip counts.
    with pytest.raises(TypeError):
        build_pots({0: 1.5, 1: 50})  # type: ignore[dict-item]  # float rejected
    with pytest.raises(TypeError):
        build_pots({0: True, 1: 50})  # bool is an int subclass — still rejected
    with pytest.raises(ValueError, match=">= 0"):
        build_pots({0: -10, 1: 50})  # negative rejected


def test_award_raises_when_eligible_seat_missing_a_rank() -> None:
    # award_pots must refuse to guess: an eligible seat with no rank is a caller bug.
    pots = build_pots({0: 100, 1: 100}, set())
    assert pots == [Pot(amount=200, eligible=(0, 1))]
    with pytest.raises(ValueError, match="no hand rank"):
        award_pots(pots, {0: 5})  # seat 1 is eligible but absent from ranks


def test_single_layer_winner_takes_all() -> None:
    # Three players each in for 100, nobody folded, player 1 has the best hand.
    pots = build_pots({0: 100, 1: 100, 2: 100}, set())
    assert pots == [Pot(amount=300, eligible=(0, 1, 2))]
    assert award_pots(pots, {0: 5, 1: 9, 2: 1}) == {1: 300}


def test_classic_three_way_all_in_at_different_levels() -> None:
    # The textbook case. Contributions: P0=50, P1=100, P2=200, none folded.
    #   main pot  : level 50  -> (50-0)  * 3 = 150, eligible {0,1,2}
    #   side pot 1: level 100 -> (100-50)* 2 = 100, eligible {1,2}
    #   side pot 2: level 200 -> (200-100)*1 = 100, eligible {2}   (uncalled excess)
    pots = build_pots({0: 50, 1: 100, 2: 200}, set())
    assert pots == [
        Pot(amount=150, eligible=(0, 1, 2)),
        Pot(amount=100, eligible=(1, 2)),
        Pot(amount=100, eligible=(2,)),
    ]
    # P0 has the best hand: wins ONLY the main pot (eligible everywhere it qualifies).
    # P1 next: wins side pot 1. P2 worst: keeps its lone side pot 2 (returned).
    awards = award_pots(pots, {0: 9, 1: 5, 2: 1})
    assert awards == {0: 150, 1: 100, 2: 100}
    assert sum(awards.values()) == 50 + 100 + 200


def test_main_pot_only_player_cannot_win_side_pot() -> None:
    # P0 is all-in short (in for 50); the side pot above 50 is contested by P1,P2.
    # Even with the BEST hand, P0 can only win the main pot.
    pots = build_pots({0: 50, 1: 150, 2: 150}, set())
    assert pots == [
        Pot(amount=150, eligible=(0, 1, 2)),
        Pot(amount=200, eligible=(1, 2)),
    ]
    awards = award_pots(pots, {0: 9, 1: 8, 2: 1})
    assert awards == {0: 150, 1: 200}  # P0 takes main; side pot to P1 (P0 ineligible)
    assert sum(awards.values()) == 350


def test_folded_player_chips_stay_in_pot_but_player_wins_nothing() -> None:
    # P2 folded after contributing 100; chips stay in, P2 eligible nowhere.
    pots = build_pots({0: 100, 1: 100, 2: 100}, folded={2})
    assert pots == [Pot(amount=300, eligible=(0, 1))]
    awards = award_pots(pots, {0: 9, 1: 5, 2: 14})  # P2 "best" but folded
    assert awards == {0: 300}
    assert 2 not in awards
    assert sum(awards.values()) == 300


def test_uncontested_single_survivor_scoops_everything() -> None:
    # Everyone folds to P1, including a folder who over-contributed above P1's level:
    # the orphaned top layer cascades down to the lone survivor. Chips conserved.
    pots = build_pots({0: 200, 1: 50, 2: 50}, folded={0, 2})
    awards = award_pots(pots, {0: 9, 1: 1, 2: 9})
    assert awards == {1: 300}
    assert sum(awards.values()) == 300


def test_single_eligible_side_pot_returns_to_that_player() -> None:
    pots = build_pots({0: 30, 1: 100}, set())
    assert pots == [
        Pot(amount=60, eligible=(0, 1)),
        Pot(amount=70, eligible=(1,)),
    ]
    awards = award_pots(pots, {0: 9, 1: 1})
    assert awards == {0: 60, 1: 70}  # P0 wins main; lone side pot returns to P1
    assert sum(awards.values()) == 130


def test_tie_splits_pot_evenly() -> None:
    pots = build_pots({0: 100, 1: 100}, set())
    awards = award_pots(pots, {0: 7, 1: 7})
    assert awards == {0: 100, 1: 100}


def test_odd_chip_goes_to_lowest_seat_id() -> None:
    # 101 split between tied seats 0 and 1 -> 51/50; the odd chip to the lower seat.
    pots = build_pots({0: 50, 1: 51}, set())
    # main: 50*2 = 100 eligible {0,1}; side: 1*1 = 1 eligible {1}
    awards = award_pots(pots, {0: 7, 1: 7})
    # main pot 100 splits 50/50; side pot 1 returns to P1 -> {0:50, 1:51}
    assert awards == {0: 50, 1: 51}
    # Now an explicitly odd single pot: three-way tie on 100 -> 34/33/33.
    pot = [Pot(amount=100, eligible=(0, 1, 2))]
    assert award_pots(pot, {0: 5, 1: 5, 2: 5}) == {0: 34, 1: 33, 2: 33}


def test_odd_chip_recipient_is_by_seat_order_not_insertion_order() -> None:
    # Eligible given high-id-first; remainder must still go to the LOWEST seat ids.
    pot = [Pot(amount=10, eligible=(2, 0, 1))]
    # 10 / 3 = 3 each, remainder 1 -> seat 0 gets the extra.
    assert award_pots(pot, {0: 9, 1: 9, 2: 9}) == {0: 4, 1: 3, 2: 3}


def test_isp_seam_real_hand_ranks_thread_through_award() -> None:
    # Prove award_pots's `max`/`==`/`>` work on real evaluator output, not just ints,
    # exercising BOTH branches: strict ordering (winner-takes-all) and a genuine tie.
    def seven(*cards: tuple[int, int]) -> list[Card]:  # (rank, suit) pairs
        return [Card(rank=r, suit=s) for r, s in cards]

    # Strict order: P0 holds an ace-high flush (all suit 0); P1 is ace-high garbage.
    p0 = rank_seven(seven((14, 0), (12, 0), (10, 0), (8, 0), (6, 0), (3, 1), (2, 2)))
    p1 = rank_seven(seven((14, 1), (11, 2), (9, 3), (7, 0), (5, 1), (3, 2), (2, 3)))
    assert p0 > p1  # precondition: the seam must actually pick a winner here
    pots = build_pots({0: 100, 1: 100}, set())
    assert award_pots(pots, {0: p0, 1: p1}) == {0: 200}  # winner-takes-all via HandRank

    # Genuine tie on real ranks (same 5 winning cards, different suits) -> split.
    t0 = rank_seven(seven((14, 0), (13, 0), (12, 0), (11, 1), (9, 2), (4, 3), (2, 0)))
    t1 = rank_seven(seven((14, 1), (13, 1), (12, 1), (11, 2), (9, 3), (4, 0), (2, 1)))
    assert t0 == t1  # precondition: identical best-5 (A-K-Q-J-9 high card)
    assert award_pots(pots, {0: t0, 1: t1}) == {0: 100, 1: 100}


# --------------------------------------------------------------------------- #
# Hypothesis property: conservation + recipient correctness over hard paths     #
# --------------------------------------------------------------------------- #


@st.composite
def _scenario(draw: st.DrawFn) -> tuple[dict[int, int], set[int], dict[int, int]]:
    """Random multi-all-in scenarios that HIT the hard paths.

    * 2..6 players, contributions drawn from a small set of *distinct levels* so real
      side pots form (and equal-level all-ins collide);
    * a fold set that may include the top contributor (cascade + eligibility);
    * a *small* rank space (0..2) so ties — splits and the odd-chip path — are frequent.

    Constrained so at least one non-folded positive contributor exists (the main pot is
    then awardable), which is the documented precondition of ``award_pots``.
    """
    n = draw(st.integers(min_value=2, max_value=6))
    players = list(range(n))
    contributions = {
        p: draw(st.integers(min_value=0, max_value=7)) for p in players
    }
    folded = set(draw(st.sets(st.sampled_from(players), max_size=n)))
    # Precondition: someone with chips in the pot is still contesting.
    positive_contesting = [p for p in players if contributions[p] > 0 and p not in folded]
    if not positive_contesting:
        # force the lowest positive contributor (if any) to be contesting
        positive = [p for p in players if contributions[p] > 0]
        if not positive:
            contributions[0] = 1  # ensure the pot is non-empty
            positive = [0]
        keep = min(positive)
        folded.discard(keep)
    ranks = {p: draw(st.integers(min_value=0, max_value=2)) for p in players}
    return contributions, folded, ranks


@given(_scenario())
@settings(max_examples=1000, deadline=None)
def test_conservation_and_eligibility(
    scenario: tuple[dict[int, int], set[int], dict[int, int]],
) -> None:
    contributions, folded, ranks = scenario
    pots = build_pots(contributions, folded)
    awards = award_pots(pots, ranks)

    # Headline: chips in == chips out, to the minor unit.
    assert sum(awards.values()) == sum(contributions.values())

    # Pot-level conservation: the layers sum to the contributions too.
    assert sum(pot.amount for pot in pots) == sum(contributions.values())

    eligible_union = {p for pot in pots for p in pot.eligible}
    for recipient, amount in awards.items():
        assert amount > 0  # no zero-value noise entries
        assert recipient not in folded  # folded players win nothing
        assert recipient in eligible_union  # only eligible players are paid
        assert contributions[recipient] > 0  # a non-contributor wins nothing

    # Every pot's amount is positive and its eligible set is sorted & non-folded.
    for pot in pots:
        assert pot.amount > 0
        assert list(pot.eligible) == sorted(pot.eligible)
        assert all(p not in folded for p in pot.eligible)


@given(_scenario())
@settings(max_examples=500, deadline=None)
def test_per_pot_award_winner_is_best_eligible_rank(
    scenario: tuple[dict[int, int], set[int], dict[int, int]],
) -> None:
    """A pot is won only by the best hand(s) among its eligible set (split on ties)."""
    contributions, folded, ranks = scenario
    pots = build_pots(contributions, folded)
    # award each pot in isolation; the winner(s) must hold the max eligible rank.
    for pot in pots:
        if not pot.eligible:
            continue
        single = award_pots([pot], ranks)
        best = max(ranks[p] for p in pot.eligible)
        winners = {p for p in pot.eligible if ranks[p] == best}
        assert set(single) == winners
        assert sum(single.values()) == pot.amount
