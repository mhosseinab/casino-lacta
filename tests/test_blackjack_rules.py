"""S25 — blackjack rules engine: Hypothesis property tests + targeted scenario gates.

The rule invariants that must hold for the server-authoritative blackjack engine
(``engine.table.blackjack``). Two layers:

* **Property tests** (Hypothesis over generated ``(serverSeed, nonce)``) drive a full
  round under basic strategy and assert invariants RECOMPUTED from the disclosed
  terminal hands — never by forcing a specific hand (which a finite count-shoe makes
  painful). Every generated round is legal play, so the properties are non-vacuous.
* **Targeted gates** scan nonces for a specific deal (player natural, dealer natural,
  a pair, …) reading the opaque state WHITE-BOX (exactly as ``test_mines_ev`` reads
  the mine layout) to exercise bust / blackjack / push / payout / split / double /
  dealer-draw / insurance / surrender resolution deterministically.

Pure engine step: no DB, no FastAPI. Money is expressed as a multiplier on the base
stake; minor-units rounding/caps live in ``app/money.py``, never here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from blackjack_sim import GAME_ID, TERMINAL, basic_strategy, play_round
from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.table.blackjack import (
    DEFAULT_PARAMS,
    Blackjack,
    card_value,
    hand_value,
    is_natural,
    legal_actions,
)
from engine.types import GameConfig, InvalidBetInput

CLIENT = "blackjack-rules-client"

# Return multiple (× hand bet) for each per-hand result label.
_RETURN = {
    "BLACKJACK": 2.5,  # 3:2 default
    "WIN": 2.0,
    "PUSH": 1.0,
    "LOSE": 0.0,
    "BUST": 0.0,
    "SURRENDER": 0.5,
}


def _cfg(**override: Any) -> GameConfig:
    base = default_config(GAME_ID)
    if not override:
        return base
    return GameConfig(edge=base.edge, params={**base.params, **override})


def _new_round(
    cfg: GameConfig, nonce: int, seed: bytes = b"bj-seed"
) -> tuple[Blackjack, Any, dict[str, Any]]:
    game = load_game(GAME_ID)
    rng = create_rng(seed, CLIENT, nonce)
    state = game.init({}, rng, cfg)  # type: ignore[union-attr]
    return game, rng, state  # type: ignore[return-value]


def _drive(
    game: Blackjack,
    rng: Any,
    state: dict[str, Any],
    policy: Callable[[dict[str, Any]], dict[str, Any]],
):
    out = None
    for _ in range(64):
        if state["status"] != "ACTIVE":
            break
        state, out = game.step(state, policy(state), rng)
        if out is not None and out.detail["status"] in TERMINAL:
            break
    return state, out


# === Hypothesis property tests ===============================================


@given(seed=st.binary(min_size=4, max_size=24), nonce=st.integers(0, 50_000))
@settings(max_examples=400, deadline=None)
def test_terminal_payout_invariants(seed: bytes, nonce: int) -> None:
    """Over generated basic-strategy rounds: payout == sum of per-hand returns and
    every per-hand result resolves correctly against the dealer."""
    out = play_round(default_config(GAME_ID), seed, CLIENT, nonce)
    d = out.detail
    assert d["status"] in TERMINAL
    assert out.multiplier >= 0.0

    hand_ret = sum(h["return"] for h in d["hands"])
    assert out.multiplier == pytest.approx(hand_ret + d["insurance"]["return"])
    assert out.multiplier == pytest.approx(d["totalReturned"])

    # status matches the net sign.
    net = d["totalReturned"] - d["totalWagered"]
    expect = "WON" if net > 1e-9 else "LOST" if net < -1e-9 else "PUSH"
    assert d["status"] == expect

    dv = d["dealerValue"]
    dealer_bust = dv > 21
    for h in d["hands"]:
        assert h["return"] == pytest.approx(_RETURN[h["result"]] * h["bet"])
        # The win/push/lose label must match value-vs-dealer for played-out hands.
        if h["result"] in {"WIN", "PUSH", "LOSE"}:
            v = h["value"]
            assert v <= 21
            if dealer_bust or v > dv:
                assert h["result"] == "WIN"
            elif v == dv:
                assert h["result"] == "PUSH"
            else:
                assert h["result"] == "LOSE"
        if h["result"] == "BUST":
            assert h["value"] > 21


@given(seed=st.binary(min_size=4, max_size=24), nonce=st.integers(0, 50_000))
@settings(max_examples=400, deadline=None)
def test_dealer_stands_per_rule(seed: bytes, nonce: int) -> None:
    """When the dealer plays out, the final total honours the stand rule:
    S17 (default) ⇒ final ≥ 17; H17 ⇒ final ≥ 17 and never a STANDING soft 17."""
    for hit_soft_17 in (False, True):
        out = play_round(_cfg(hit_soft_17=hit_soft_17), seed, CLIENT, nonce)
        d = out.detail
        if not d["dealerPlayed"]:
            continue
        total, soft = hand_value(d["dealer"])
        assert total >= 17 or total > 21
        if hit_soft_17:
            # The dealer would have hit a soft 17, so a STANDING soft 17 is impossible.
            assert not (total == 17 and soft)


@given(seed=st.binary(min_size=4, max_size=24), nonce=st.integers(0, 50_000))
@settings(max_examples=200, deadline=None)
def test_determinism(seed: bytes, nonce: int) -> None:
    """Same (seed, client, nonce) ⇒ identical settlement (pure function of the stream)."""
    a = play_round(default_config(GAME_ID), seed, CLIENT, nonce)
    b = play_round(default_config(GAME_ID), seed, CLIENT, nonce)
    assert a.multiplier == b.multiplier
    assert a.detail == b.detail


# === hand-value helper unit checks ===========================================


def test_hand_value_soft_and_hard() -> None:
    assert hand_value([1, 6]) == (17, True)  # soft 17
    assert hand_value([1, 6, 10]) == (17, False)  # ace forced to 1 → hard 17
    assert hand_value([1, 1]) == (12, True)  # one ace 11, one 1
    assert hand_value([10, 10, 10]) == (30, False)  # bust, hard
    assert hand_value([13, 12]) == (20, False)  # K, Q


def test_is_natural() -> None:
    assert is_natural([1, 13])  # A + K
    assert is_natural([10, 1])
    assert not is_natural([1, 5, 5])  # 21 in three cards is NOT a natural
    assert not is_natural([10, 10])


# === targeted scenario gates (deterministic nonce scans) =====================


def _find(cfg: GameConfig, predicate: Callable[[dict[str, Any]], bool], limit: int = 80_000):
    """Scan nonces for an opening state matching ``predicate``; skip if none found."""
    for nonce in range(limit):
        game, rng, state = _new_round(cfg, nonce)
        if predicate(state):
            return game, rng, state, nonce
    pytest.skip("no matching deal found within scan budget")


def _is_ten_or_ace(rank: int) -> bool:
    return card_value(rank) in (10, 11)


def test_player_blackjack_pays_3_2() -> None:
    """Player natural vs a non-ten/ace up card (dealer cannot have BJ) ⇒ 2.5×, WON."""
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(
        cfg,
        lambda s: is_natural(s["hands"][0]["cards"]) and not _is_ten_or_ace(s["dealer"][0]),
    )
    state, out = _drive(game, rng, state, lambda s: basic_strategy(s, DEFAULT_PARAMS))
    assert out is not None
    assert out.detail["status"] == "WON"
    assert out.multiplier == pytest.approx(2.5)
    assert out.detail["hands"][0]["result"] == "BLACKJACK"


def test_both_naturals_push() -> None:
    """Player natural AND dealer natural ⇒ push, 1.0× (stake returned)."""
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(
        cfg,
        lambda s: is_natural(s["hands"][0]["cards"]) and is_natural(s["dealer"]),
    )
    # Decline insurance, then play (a natural just stands).
    state, out = _drive(game, rng, state, lambda s: basic_strategy(s, DEFAULT_PARAMS))
    assert out is not None
    assert out.detail["status"] == "PUSH"
    assert out.multiplier == pytest.approx(1.0)


def test_dealer_blackjack_loses_player() -> None:
    """Dealer natural, player not ⇒ player loses base (0×), insurance declined."""
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(
        cfg,
        lambda s: is_natural(s["dealer"]) and not is_natural(s["hands"][0]["cards"]),
    )
    state, out = _drive(game, rng, state, lambda s: basic_strategy(s, DEFAULT_PARAMS))
    assert out is not None
    assert out.detail["status"] == "LOST"
    assert out.multiplier == pytest.approx(0.0)
    assert out.detail["totalWagered"] == pytest.approx(1.0)  # insurance declined


def test_player_bust_pays_zero() -> None:
    """Always-hit a single hand (dealer 2–9, player not natural) ⇒ bust, 0×."""
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(
        cfg,
        lambda s: 2 <= card_value(s["dealer"][0]) <= 9
        and not is_natural(s["hands"][0]["cards"]),
    )
    state, out = _drive(game, rng, state, lambda s: {"op": "hit"})
    assert out is not None
    assert out.detail["status"] == "LOST"
    assert out.multiplier == pytest.approx(0.0)
    assert out.detail["hands"][0]["result"] == "BUST"
    assert out.detail["hands"][0]["value"] > 21


def test_push_returns_stake() -> None:
    """Stand pat on a hard 20; find a deal where the dealer also makes 20 ⇒ push 1.0×."""
    cfg = default_config(GAME_ID)

    def predicate(s: dict[str, Any]) -> bool:
        return (
            hand_value(s["hands"][0]["cards"])[0] == 20
            and not is_natural(s["hands"][0]["cards"])
            and 2 <= card_value(s["dealer"][0]) <= 9
        )

    for nonce in range(80_000):
        game, rng, state = _new_round(cfg, nonce)
        if not predicate(state):
            continue
        state, out = _drive(game, rng, state, lambda s: {"op": "stand"})
        assert out is not None
        if out.detail["status"] == "PUSH":
            assert out.multiplier == pytest.approx(1.0)
            assert out.detail["dealerValue"] == 20
            return
    pytest.skip("no stand-20 push found within scan budget")


def test_double_resolution() -> None:
    """Double a non-pair 2-card hand: total wagered = 2, payout ∈ {0, 2, 4}×base."""
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(
        cfg,
        lambda s: 2 <= card_value(s["dealer"][0]) <= 9
        and not is_natural(s["hands"][0]["cards"])
        and card_value(s["hands"][0]["cards"][0]) != card_value(s["hands"][0]["cards"][1]),
    )
    state, out = _drive(game, rng, state, lambda s: {"op": "double"})
    assert out is not None
    d = out.detail
    assert d["totalWagered"] == pytest.approx(2.0)
    assert d["hands"][0]["bet"] == pytest.approx(2.0)
    assert len(d["hands"][0]["cards"]) == 3  # exactly one extra card
    assert out.multiplier in (pytest.approx(0.0), pytest.approx(2.0), pytest.approx(4.0))


def test_split_resolution() -> None:
    """Split a pair then stand both hands: two hands, total wagered = 2, payout ∈ [0, 4]."""
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(
        cfg,
        lambda s: 2 <= card_value(s["dealer"][0]) <= 9
        and card_value(s["hands"][0]["cards"][0]) == card_value(s["hands"][0]["cards"][1])
        and not is_natural(s["hands"][0]["cards"]),
    )
    def _split_then_stand(s: dict[str, Any]) -> dict[str, Any]:
        return {"op": "split"} if "split" in legal_actions(s) else {"op": "stand"}

    state, out = _drive(game, rng, state, _split_then_stand)
    assert out is not None
    d = out.detail
    assert len(d["hands"]) >= 2
    assert d["totalWagered"] == pytest.approx(2.0 + 0.0)  # one extra base for the split
    for h in d["hands"]:
        assert h["bet"] == pytest.approx(1.0)
    assert 0.0 <= out.multiplier <= 4.0


def test_split_aces_one_card_each() -> None:
    """Split aces: each ace gets exactly one card and the hands auto-resolve."""
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(
        cfg,
        lambda s: card_value(s["hands"][0]["cards"][0]) == 11
        and card_value(s["hands"][0]["cards"][1]) == 11
        and 2 <= card_value(s["dealer"][0]) <= 9,
    )
    state, out = _drive(game, rng, state, lambda s: {"op": "split"})
    assert out is not None
    d = out.detail
    assert len(d["hands"]) == 2
    for h in d["hands"]:
        assert len(h["cards"]) == 2  # one ace + one dealt card, no further hits
    assert d["totalWagered"] == pytest.approx(2.0)


def test_insurance_pays_when_dealer_has_blackjack() -> None:
    """Take insurance vs an ace-up dealer who has BJ ⇒ insurance returns 1.5 (2:1 + stake);
    the round nets to zero (insurance exactly offsets the lost base bet)."""
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(
        cfg,
        lambda s: card_value(s["dealer"][0]) == 11
        and is_natural(s["dealer"])
        and not is_natural(s["hands"][0]["cards"]),
    )
    assert state["phase"] == "INSURANCE"
    state, out = game.step(state, {"op": "insurance", "take": True}, rng)
    assert out is not None
    d = out.detail
    assert d["insurance"]["bet"] == pytest.approx(0.5)
    assert d["insurance"]["return"] == pytest.approx(1.5)  # 0.5 stake + 2:1 win
    assert d["totalWagered"] == pytest.approx(1.5)
    assert d["totalReturned"] == pytest.approx(1.5)
    assert out.multiplier == pytest.approx(1.5)
    assert d["status"] == "PUSH"  # net zero


def test_insurance_lost_when_dealer_has_no_blackjack() -> None:
    """Take insurance vs an ace-up dealer with NO BJ ⇒ insurance lost, play continues."""
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(
        cfg,
        lambda s: card_value(s["dealer"][0]) == 11 and not is_natural(s["dealer"]),
    )
    state, out = game.step(state, {"op": "insurance", "take": True}, rng)
    assert out is not None
    assert out.detail["status"] == "ACTIVE"  # round continues
    assert state["phase"] == "PLAYER"
    assert state["insurance_bet"] == pytest.approx(0.5)


def test_late_surrender_returns_half() -> None:
    """With surrender enabled, surrendering the opening hand returns 0.5× and ends the round."""
    cfg = _cfg(surrender=True)
    game, rng, state, _ = _find(
        cfg,
        lambda s: 2 <= card_value(s["dealer"][0]) <= 9
        and not is_natural(s["hands"][0]["cards"]),
    )
    assert "surrender" in legal_actions(state)
    state, out = game.step(state, {"op": "surrender"}, rng)
    assert out is not None
    assert out.multiplier == pytest.approx(0.5)
    assert out.detail["status"] == "LOST"
    assert out.detail["hands"][0]["result"] == "SURRENDER"


def test_surrender_off_by_default() -> None:
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(
        cfg, lambda s: s["phase"] == "PLAYER" and len(s["hands"][0]["cards"]) == 2
    )
    assert "surrender" not in legal_actions(state)
    with pytest.raises(InvalidBetInput):
        game.step(state, {"op": "surrender"}, rng)


# === redaction (public_view) =================================================


def test_public_view_hides_hole_card_while_active() -> None:
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(cfg, lambda s: s["phase"] == "PLAYER")
    view = game.public_view(state)
    assert view["status"] == "ACTIVE"
    assert view["dealerUp"] == state["dealer"][0]
    # Only the up card is surfaced; the hole card must not appear anywhere in the view.
    assert "dealerHole" not in view
    assert view.get("dealer") in (None, [state["dealer"][0]])
    assert set(view["legalActions"]) == set(legal_actions(state))
    # The undrawn shoe must NOT leak: the shoe is the remainder of a KNOWN D-deck
    # composition, so leaking it deterministically reveals the hole-card rank by
    # deduction (a shoe leak IS a hole-card leak). Assert the ACTIVE view's key set is
    # EXACTLY the intended public allowlist, so any future field must be deliberately
    # whitelisted here (leak-proof) — and call out the shoe explicitly.
    assert "shoe" not in view
    assert set(view) == {
        "status",
        "phase",
        "dealerUp",
        "dealerUpValue",
        "hands",
        "active",
        "insuranceBet",
        "totalWagered",
        "legalActions",
    }


def test_public_view_discloses_dealer_at_terminal() -> None:
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(
        cfg, lambda s: 2 <= card_value(s["dealer"][0]) <= 9
    )
    state, out = _drive(game, rng, state, lambda s: {"op": "stand"})
    assert out is not None
    view = game.public_view(state)
    assert view["status"] in TERMINAL
    assert view["dealer"] == state["dealer"]  # full hand disclosed
    assert "multiplier" in view


# === illegal-action / terminal guards ========================================


def test_step_after_terminal_raises() -> None:
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(cfg, lambda s: 2 <= card_value(s["dealer"][0]) <= 9)
    state, out = _drive(game, rng, state, lambda s: {"op": "stand"})
    assert out is not None and out.detail["status"] in TERMINAL
    with pytest.raises(InvalidBetInput):
        game.step(state, {"op": "hit"}, rng)


def test_double_on_three_cards_raises() -> None:
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(
        cfg,
        lambda s: 2 <= card_value(s["dealer"][0]) <= 9
        and not is_natural(s["hands"][0]["cards"]),
    )
    state, out = game.step(state, {"op": "hit"}, rng)  # now 3 cards (or bust)
    if out is not None and out.detail["status"] in TERMINAL:
        pytest.skip("hand busted on first hit")
    assert len(state["hands"][state["active"]]["cards"]) >= 3
    with pytest.raises(InvalidBetInput):
        game.step(state, {"op": "double"}, rng)


def test_split_non_pair_raises() -> None:
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(
        cfg,
        lambda s: card_value(s["hands"][0]["cards"][0]) != card_value(s["hands"][0]["cards"][1])
        and 2 <= card_value(s["dealer"][0]) <= 9
        and not is_natural(s["hands"][0]["cards"]),
    )
    with pytest.raises(InvalidBetInput):
        game.step(state, {"op": "split"}, rng)


def test_unknown_op_raises() -> None:
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(
        cfg,
        lambda s: s["phase"] == "PLAYER"
        and not is_natural(s["hands"][0]["cards"])
        and not is_natural(s["dealer"]),
    )
    with pytest.raises(InvalidBetInput):
        game.step(state, {"op": "teleport"}, rng)


def test_insurance_required_first_when_ace_up() -> None:
    cfg = default_config(GAME_ID)
    game, rng, state, _ = _find(cfg, lambda s: s["phase"] == "INSURANCE")
    with pytest.raises(InvalidBetInput):
        game.step(state, {"op": "hit"}, rng)


def test_validate_input_is_noop() -> None:
    game = load_game(GAME_ID)
    game.validate_input({}, default_config(GAME_ID))  # type: ignore[union-attr]
