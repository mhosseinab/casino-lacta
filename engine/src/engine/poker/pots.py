"""Main + side-pot engine — the classic all-in bug source, made conservative by design.

Two cohesive, separately testable responsibilities (SRP):

* :func:`build_pots` — layer per-player *contributions* into the main pot + side pots.
  One pot per distinct contribution/all-in level: level ``L`` collects ``(L - prev) *
  (number of players in for ≥ L)``; a pot's **eligible** set is the players in for ≥ L who
  have NOT folded. Folded players' chips stay IN the pots (they were wagered) but a folder
  is eligible nowhere.
* :func:`award_pots` — award each pot to the best hand(s) among its eligible set, splitting
  ties evenly. It consumes a pre-computed ``Mapping[player, comparable rank]`` — the ISP
  seam: this module never ranks cards, so hand ranking keeps its single owner
  (``engine.cards.evaluator``). Any total order works; production passes
  ``evaluator.rank_seven`` results.

**The load-bearing invariant — chip conservation.** Total chips in == total chips out, to
the minor unit, under ANY multi-all-in / fold configuration: ``sum(award_pots(...).values())
== sum(contributions.values())``. Two design choices make it ironclad:

* **Cascade-down for orphaned pots.** A pot whose eligible set is empty (every contributor
  at that level folded) has no winner. Because eligibility is *monotone* in level (a higher
  level's contributors are a subset of a lower level's), empty pots are always a contiguous
  *top* block; their chips cascade down into the highest awardable pot below — never to a
  folded player. This also yields "everyone folds to one survivor": the lone survivor scoops
  every layer. If even the main pot is empty (no non-folded contributor anywhere) there is
  nothing to award — a precondition violation, raised, not silently dropped.
* **Deterministic odd chip.** When a pot splits ``k`` ways, the ``amount % k`` leftover
  chips go one each to the **lowest seat ids** among the tied winners. This is a documented,
  reproducible rule (same inputs → same award). S31 (table reducer) holds the button, so if a
  button-relative odd-chip rule is ever wanted it overrides this from there; S33 rakes each
  :class:`Pot` *before* awarding, so the per-pot structure is the seam it consumes.

Contributions are the **called** amounts; netting genuinely uncalled bets back to the bettor
is the table reducer's (S31) job — here, an uncalled top layer simply forms a lone-eligible
pot returned to its sole contributor.

Pure: stdlib only; integer minor units throughout (floats are rejected); no IO, clock, or
randomness.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Any, Protocol


class _Comparable(Protocol):
    """A totally ordered rank — anything with ``<`` (ints in tests, ``HandRank`` in prod)."""

    def __lt__(self, other: Any) -> bool: ...


@dataclass(frozen=True)
class Pot:
    """One pot layer: ``amount`` minor units contested by the ``eligible`` seats.

    ``eligible`` is the non-folded contributors at this layer, as a seat-ascending tuple
    (the order the odd-chip rule depends on). The main pot is the first element returned by
    :func:`build_pots`; side pots follow in ascending all-in level.
    """

    amount: int
    eligible: tuple[int, ...]


def _check_amount(player: int, amount: int) -> None:
    # bool is an int subclass — reject it explicitly so True/False can't pose as chips.
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise TypeError(f"contribution for player {player} must be int, got {amount!r}")
    if amount < 0:
        raise ValueError(f"contribution for player {player} must be >= 0, got {amount}")


def build_pots(
    contributions: Mapping[int, int],
    folded: AbstractSet[int] = frozenset(),
) -> list[Pot]:
    """Layer ``contributions`` (seat → total wagered, minor units) into main + side pots.

    ``folded`` are seats that folded: their chips remain in the pots but they are eligible
    for none. Returns the pots main-first, in ascending all-in level. Pure and
    deterministic — identical inputs yield an identical list. Empty input → ``[]``.
    """
    for player, amount in contributions.items():
        _check_amount(player, amount)

    positive = {p: a for p, a in contributions.items() if a > 0}
    if not positive:
        return []

    pots: list[Pot] = []
    prev = 0
    for level in sorted(set(positive.values())):
        contributors = [p for p, a in positive.items() if a >= level]
        amount = (level - prev) * len(contributors)
        eligible = tuple(sorted(p for p in contributors if p not in folded))
        pots.append(Pot(amount=amount, eligible=eligible))
        prev = level
    return pots


def award_pots[RankT: _Comparable](
    pots: Sequence[Pot],
    ranks: Mapping[int, RankT],
) -> dict[int, int]:
    """Award each pot to the best eligible hand(s); return seat → chips won (positive only).

    ``ranks`` maps each eligible seat to a totally ordered hand rank (the ISP seam — any
    comparable; production passes ``engine.cards.evaluator.rank_seven`` results). Ties split
    evenly with the odd chip(s) going to the lowest seat ids. Orphaned pots (empty eligible)
    cascade down into the next awardable pot; if none exists the chips cannot be awarded and
    ``ValueError`` is raised. Conserves chips exactly: ``sum(result.values()) ==
    sum(p.amount for p in pots)``.
    """
    awards: dict[int, int] = {}
    carry = 0  # chips from orphaned (empty-eligible) pots, cascading down to a lower pot
    # Top-down: empty top pots accumulate into `carry` and land in the first awardable
    # pot below them (eligibility is monotone, so empties are always a top block).
    for pot in reversed(pots):
        amount = pot.amount + carry
        carry = 0
        if not pot.eligible:
            carry = amount
            continue
        for seat in pot.eligible:
            if seat not in ranks:
                raise ValueError(f"no hand rank for eligible seat {seat}")
        best = max(ranks[seat] for seat in pot.eligible)
        winners = sorted(seat for seat in pot.eligible if ranks[seat] == best)
        base, remainder = divmod(amount, len(winners))
        for i, seat in enumerate(winners):
            share = base + (1 if i < remainder else 0)
            if share:
                awards[seat] = awards.get(seat, 0) + share
    if carry:
        raise ValueError("no eligible players for the remaining chips")
    return awards
