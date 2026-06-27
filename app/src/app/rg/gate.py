"""Responsible-gaming decision — the pre-debit gate.

S6 stub: ``can_bet`` always allows. The interface is the seam the real S38 policy
swaps behind without touching the bet loop (DIP): the loop depends on
``RgDecision``, never on a concrete policy.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RgDecision:
    """The verdict of the RG gate. ``reason`` is populated only on a deny."""

    allowed: bool
    reason: str | None = None


def can_bet(*, user_id: str, game_id: str, stake_minor: int, currency: str) -> RgDecision:
    """Decide whether ``user_id`` may place this bet. S6: allow everything."""
    return RgDecision(allowed=True)
