"""Shared blackjack basic-strategy policy + round driver (test infra, like ``rtp_harness``).

NOT a test module — a helper imported by ``test_blackjack_rules`` and
``test_blackjack_ev``. It supplies:

* :func:`basic_strategy` — a multi-deck S17 total-dependent basic-strategy policy.
  This is the PLAYER's decision tool, deliberately kept OUT of ``engine/`` (the engine
  decides server-authoritative OUTCOMES, never a player's strategy). It reads the
  opaque round state white-box for speed (its own cards + the dealer UP card only —
  never the hole card), exactly the information a real player sees.
* :func:`play_round` — drives ``init``/``step`` to a terminal ``Outcome`` under a
  policy, reusing ``engine`` verbatim (same outcome math the server settles).
* :func:`net_of` — the per-round NET result (``returned − wagered``) in units of the
  base stake. Blackjack edge is per INITIAL bet = ``−E[net]`` (doubles/splits/insurance
  inflate total wagered, so gross-return/base would NOT be ``1 − edge``).

The strategy lands within a few hundredths of a percent of optimal for the default
rule set — well inside the EV gate's CLT window — so a measured EV that drifts is a
RULES bug, not a strategy bug.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from engine.registry import load_game
from engine.rng import create_rng
from engine.table.blackjack import (
    DEFAULT_PARAMS,
    card_value,
    hand_value,
    legal_actions,
)
from engine.types import GameConfig, Outcome

GAME_ID = "table.blackjack"
TERMINAL = frozenset({"WON", "LOST", "PUSH"})

Policy = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


# --- basic strategy (multi-deck, S17, DAS) -----------------------------------
# Dealer up value is 2..11 (ace = 11). Each helper returns an ordered preference
# list; the caller plays the first preference that is currently legal (so a
# "double else hit" gracefully degrades to "hit" on a 3-card hand, etc.).


def _hard_prefs(total: int, up: int) -> list[str]:
    if total >= 17:
        return ["stand"]
    if 13 <= total <= 16:
        return ["stand"] if 2 <= up <= 6 else ["hit"]
    if total == 12:
        return ["stand"] if 4 <= up <= 6 else ["hit"]
    if total == 11:
        return ["double", "hit"] if up <= 10 else ["hit"]
    if total == 10:
        return ["double", "hit"] if 2 <= up <= 9 else ["hit"]
    if total == 9:
        return ["double", "hit"] if 3 <= up <= 6 else ["hit"]
    return ["hit"]  # 8 or less


def _soft_prefs(total: int, up: int) -> list[str]:
    if total >= 19:  # A,8 / A,9 — stand (S17)
        return ["stand"]
    if total == 18:  # A,7
        if 3 <= up <= 6:
            return ["double", "stand"]
        if up in (2, 7, 8):
            return ["stand"]
        return ["hit"]  # vs 9, 10, A
    if total == 17:  # A,6
        return ["double", "hit"] if 3 <= up <= 6 else ["hit"]
    if total in (15, 16):  # A,4 / A,5
        return ["double", "hit"] if 4 <= up <= 6 else ["hit"]
    if total in (13, 14):  # A,2 / A,3
        return ["double", "hit"] if 5 <= up <= 6 else ["hit"]
    return ["hit"]


def _total_prefs(cards: list[int], up: int) -> list[str]:
    total, soft = hand_value(cards)
    return _soft_prefs(total, up) if soft else _hard_prefs(total, up)


def _should_split(pair_val: int, up: int) -> bool:
    """Split decision for a pair of value ``pair_val`` (DAS rule set). 10s/5s never split."""
    if pair_val == 11:  # aces
        return True
    if pair_val == 10:
        return False
    if pair_val == 9:
        return up in (2, 3, 4, 5, 6, 8, 9)
    if pair_val == 8:
        return True
    if pair_val == 7:
        return 2 <= up <= 7
    if pair_val == 6:
        return 2 <= up <= 6
    if pair_val == 5:
        return False  # play as hard 10
    if pair_val == 4:
        return up in (5, 6)  # DAS
    if pair_val in (2, 3):
        return 2 <= up <= 7
    return False


def desired_prefs(cards: list[int], up: int, rules: dict[str, Any]) -> list[str]:
    if len(cards) == 2 and card_value(cards[0]) == card_value(cards[1]):
        if _should_split(card_value(cards[0]), up):
            return ["split", *_total_prefs(cards, up)]
    return _total_prefs(cards, up)


def basic_strategy(state: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """The basic-strategy action for the round's current decision point.

    Always DECLINES insurance and NEVER surrenders (basic strategy), so neither
    perturbs the EV target — their correctness is carried by the property tests.
    """
    if state["phase"] == "INSURANCE":
        return {"op": "insurance", "take": False}
    hand = state["hands"][state["active"]]
    up = card_value(state["dealer"][0])
    legal = legal_actions(state)
    for pref in desired_prefs(hand["cards"], up, rules):
        if pref in legal:
            return {"op": pref}
    return {"op": "stand"}


# --- round driver ------------------------------------------------------------


def play_round(
    cfg: GameConfig,
    server_seed: bytes,
    client_seed: str,
    nonce: int,
    policy: Policy = basic_strategy,
) -> Outcome:
    """Drive one round under ``policy`` to its terminal ``Outcome`` (reuses the engine)."""
    game = load_game(GAME_ID)
    rng = create_rng(server_seed, client_seed, nonce)
    state = game.init({}, rng, cfg)  # type: ignore[union-attr]
    rules = {**DEFAULT_PARAMS, **cfg.params}
    outcome: Outcome | None = None
    for _ in range(64):  # a round can never need this many actions
        if state["status"] != "ACTIVE":
            break
        action = policy(state, rules)
        state, outcome = game.step(state, action, rng)  # type: ignore[union-attr]
        if outcome is not None and outcome.detail["status"] in TERMINAL:
            break
    assert outcome is not None and outcome.detail["status"] in TERMINAL, (
        f"round did not settle: {outcome}"
    )
    return outcome


def net_of(outcome: Outcome) -> float:
    """Net result in base-stake units: total returned − total wagered (= −edge in EV)."""
    return outcome.multiplier - float(outcome.detail["totalWagered"])
