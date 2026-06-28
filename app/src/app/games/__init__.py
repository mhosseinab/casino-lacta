"""Game orchestration (app member) — the shared bet loop.

The single place that turns a validated *intent* into a settled, audited bet:
limits + RG gate → single debit → pure engine outcome → single credit on win →
AuditEvent. Every game (instant or stateful) flows through here; a new game adds
only a pure outcome fn + a registry entry (OCP).
"""

from app.games.bet_loop import (
    ActiveRoundExists,
    BetObject,
    BetRejected,
    GameDisabled,
    RgDenied,
    RoundNotFound,
    RoundTerminal,
    StakeOutOfRange,
    assert_no_active_round,
    place_bet,
    stateful_round_view,
    step_action,
)

__all__ = [
    "ActiveRoundExists",
    "BetObject",
    "BetRejected",
    "GameDisabled",
    "RgDenied",
    "RoundNotFound",
    "RoundTerminal",
    "StakeOutOfRange",
    "assert_no_active_round",
    "place_bet",
    "stateful_round_view",
    "step_action",
]
