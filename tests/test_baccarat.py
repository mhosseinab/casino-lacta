"""S27 — Baccarat (punto banco): drawing-rules engine + edges by enumeration.

Baccarat has NO player decisions, so it conforms to ``engine.types.InstantGame``:
one ``play`` deals Player/Banker from a finite shoe, applies the FIXED third-card
drawing rules (the canonical punto-banco tableau), and settles Player/Banker/Tie
bets atomically. Like roulette it supports MULTIPLE simultaneous bets, aggregated
into one stake-weighted ``Outcome.multiplier`` (the only aggregation that makes
``floor(total_stake · multiplier)`` the total payout the bet loop credits).

Correctness is established by a chain of deterministic checks, each link external:

* **The drawing rules** (``_is_natural`` / ``_player_draws`` / ``_banker_draws``)
  are verified EXHAUSTIVELY against the published punto-banco tableau hardcoded
  here as the independent specification — this single banker-tableau test carries
  the whole correctness argument.
* **The shoe draw** is verified to be a true no-replacement permutation: dealing
  the ENTIRE shoe yields exactly the canonical card multiset (no off-by-one, no
  replacement).
* **The edges** follow by EXACT enumeration (8-deck, with depletion) driven by the
  engine's own rule predicates + settlement — matching the published 8-deck values
  (Banker ≈ 1.06 %, Player ≈ 1.24 %, Tie ≈ 14.36 %) and the canonical probabilities
  to 6 dp. play() is then correct by composition (verified rules ∘ verified shoe).

A Monte-Carlo RTP gate (fast unmarked + heavy ``rtp_heavy``) cross-checks the full
play()→config path end to end, with the harness's variance-aware CLT tolerance.
"""

from __future__ import annotations

from collections import Counter
from itertools import product
from typing import Any, cast

import pytest

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.table import baccarat
from engine.types import GameConfig, InstantGame, InvalidBetInput, Outcome
from rtp_harness import run_rtp

GAME_ID = "table.baccarat"
SERVER_SEED = b"baccarat-determinism-server-seed"
CLIENT_SEED = "player-client-seed"


def _game() -> InstantGame:
    return cast("InstantGame", load_game(GAME_ID))


def _cfg() -> GameConfig:
    return default_config(GAME_ID)


def _winner(player_total: int, banker_total: int) -> str:
    """The trivial comparison of final totals (the rule under test is the DRAW
    tableau, not this comparison)."""
    if player_total > banker_total:
        return "PLAYER"
    if banker_total > player_total:
        return "BANKER"
    return "TIE"


# --------------------------------------------------------------------------- card values


def test_card_value_mapping_exhaustive() -> None:
    """A=1, 2–9 pip, 10/J/Q/K=0 (ranks 0..12 = A,2,…,9,10,J,Q,K)."""
    expected = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 0, 0, 0]
    for rank, value in enumerate(expected):
        assert baccarat._card_value(rank) == value


# --------------------------------------------------------------------------- drawing rules
#
# The PUBLISHED punto-banco tableau, hardcoded as the independent specification.
# Perturb any cell and the matching exhaustive test must fail — this is the
# load-bearing correctness check the edges chain off.


def test_natural_rule_exhaustive() -> None:
    """A natural (either two-card total 8 or 9) stops all drawing."""
    for pt, bt in product(range(10), repeat=2):
        assert baccarat._is_natural(pt, bt) == (pt >= 8 or bt >= 8)


def test_player_third_card_rule_exhaustive() -> None:
    """Player draws on a two-card total 0–5, stands on 6–7; never on a natural."""
    for pt, bt in product(range(10), repeat=2):
        expected = (pt < 8 and bt < 8) and pt <= 5
        assert baccarat._player_draws(pt, bt) == expected


# Banker draws (when the PLAYER drew a third card of value p3) iff p3 ∈ this set.
_BANKER_DRAWS_ON_PLAYER_THIRD: dict[int, set[int]] = {
    0: set(range(10)),
    1: set(range(10)),
    2: set(range(10)),
    3: set(range(10)) - {8},
    4: {2, 3, 4, 5, 6, 7},
    5: {4, 5, 6, 7},
    6: {6, 7},
    7: set(),
}


def test_banker_third_card_rule_when_player_drew_exhaustive() -> None:
    """Banker tableau for banker two-card totals 0–7 × every player third card."""
    for bt in range(8):
        for p3 in range(10):
            expected = p3 in _BANKER_DRAWS_ON_PLAYER_THIRD[bt]
            assert baccarat._banker_draws(bt, p3) == expected, (bt, p3)


def test_banker_third_card_rule_when_player_stood_exhaustive() -> None:
    """When the player stands (no third card), banker draws on 0–5, stands 6–7."""
    for bt in range(8):
        assert baccarat._banker_draws(bt, None) == (bt <= 5)


# --------------------------------------------------------------------------- resolution


def _resolve_minimal(pt: int, bt: int, p3: int, b3: int) -> tuple[str, int]:
    """Build the minimal deal (representative cards realising two-card totals
    ``pt``/``bt``) in shoe-consumption order and resolve it via the ENGINE.

    Card 0 of each hand carries the total, card 1 is a ``0`` filler (value
    10/J/Q/K). Thirds are appended in the order the shoe yields them: when the
    player draws, ``p3`` then (if the banker draws) ``b3``; when the player stands
    but the banker draws, the next card off the shoe is the banker's third.
    """
    cards = [pt, bt, 0, 0]
    if baccarat._is_natural(pt, bt):
        pass
    elif baccarat._player_draws(pt, bt):
        cards.append(p3)
        if baccarat._banker_draws(bt, p3):
            cards.append(b3)
    elif baccarat._banker_draws(bt, None):
        cards.append(b3)
    rnd = baccarat.resolve_from_values(cards)
    return rnd.winner, rnd.cards_used


def test_resolve_consistent_with_rules_exhaustive() -> None:
    """``resolve_from_values`` (the path play() uses) yields the winner and card
    count the rule predicates predict, over every two-card-total × third-card
    combination — proving the cursor / deal-order plumbing, not just the rules."""
    for pt, bt, p3, b3 in product(range(10), repeat=4):
        ppt, bbt = pt, bt
        used = 4
        if baccarat._is_natural(pt, bt):
            pass
        else:
            if baccarat._player_draws(pt, bt):
                ppt = (pt + p3) % 10
                used += 1
                drew = p3
            else:
                drew = None
            if baccarat._banker_draws(bt, drew):
                bbt = (bt + b3) % 10
                used += 1
        winner, cards_used = _resolve_minimal(pt, bt, p3, b3)
        assert winner == _winner(ppt, bbt), (pt, bt, p3, b3)
        assert cards_used == used, (pt, bt, p3, b3)


def test_resolve_naturals_take_no_third_cards() -> None:
    for pt, bt in product(range(10), repeat=2):
        if not (pt >= 8 or bt >= 8):
            continue
        rnd = baccarat.resolve_from_values([pt, bt, 0, 0])
        assert rnd.cards_used == 4
        assert len(rnd.player) == 2 and len(rnd.banker) == 2


# --------------------------------------------------------------------------- shoe sampling


def test_build_counts_is_canonical_eight_deck() -> None:
    cfg = _cfg()
    decks = cfg.params["decks"]
    counts = baccarat._build_counts(cfg)
    assert counts[0] == 16 * decks  # 10/J/Q/K → value 0 (4 ranks × 4 suits)
    for value in range(1, 10):
        assert counts[value] == 4 * decks
    assert sum(counts) == decks * 52


def test_full_shoe_deal_is_exact_permutation() -> None:
    """Dealing the ENTIRE shoe returns exactly the canonical multiset — proving
    the rng→shoe draw is no-replacement with no off-by-one (one float per card)."""
    cfg = _cfg()
    decks = cfg.params["decks"]
    total = decks * 52
    shoe = baccarat._Shoe(counts=baccarat._build_counts(cfg), total=total)
    rng = create_rng(b"shoe-permutation-seed", "shoe", 0)
    dealt = Counter(shoe.draw(rng) for _ in range(total))
    assert dealt[0] == 16 * decks
    for value in range(1, 10):
        assert dealt[value] == 4 * decks
    assert sum(dealt.values()) == total
    assert shoe.total == 0
    assert rng.cursor == total  # exactly one float consumed per card


# --------------------------------------------------------------------------- edges (exact)


def _exact_probs() -> dict[str, float]:
    """EXACT 8-deck P/B/T probabilities by enumeration with depletion, driven by
    the engine's own rule predicates (``_is_natural`` / ``_player_draws`` /
    ``_banker_draws``). A uniformly random card from the remaining shoe equals
    picking value ``v`` with probability ``count[v]/total``; we recurse over value
    classes, decrementing each as it is dealt and restoring it on backtrack."""
    counts = baccarat._build_counts(_cfg())
    total = sum(counts)
    prob = {"PLAYER": 0.0, "BANKER": 0.0, "TIE": 0.0}

    def win(pt: int, bt: int, w: float) -> None:
        prob[_winner(pt, bt)] += w

    for p1 in range(10):
        if counts[p1] == 0:
            continue
        w1 = counts[p1] / total
        counts[p1] -= 1
        t1 = total - 1
        for b1 in range(10):
            if counts[b1] == 0:
                continue
            w2 = w1 * counts[b1] / t1
            counts[b1] -= 1
            t2 = t1 - 1
            for p2 in range(10):
                if counts[p2] == 0:
                    continue
                w3 = w2 * counts[p2] / t2
                counts[p2] -= 1
                t3 = t2 - 1
                for b2 in range(10):
                    if counts[b2] == 0:
                        continue
                    w4 = w3 * counts[b2] / t3
                    counts[b2] -= 1
                    t4 = t3 - 1
                    pt = (p1 + p2) % 10
                    bt = (b1 + b2) % 10
                    if baccarat._is_natural(pt, bt):
                        win(pt, bt, w4)
                    elif baccarat._player_draws(pt, bt):
                        for p3 in range(10):
                            if counts[p3] == 0:
                                continue
                            w5 = w4 * counts[p3] / t4
                            counts[p3] -= 1
                            t5 = t4 - 1
                            ppt = (pt + p3) % 10
                            if baccarat._banker_draws(bt, p3):
                                for b3 in range(10):
                                    if counts[b3] == 0:
                                        continue
                                    win(ppt, (bt + b3) % 10, w5 * counts[b3] / t5)
                            else:
                                win(ppt, bt, w5)
                            counts[p3] += 1
                    elif baccarat._banker_draws(bt, None):
                        for b3 in range(10):
                            if counts[b3] == 0:
                                continue
                            win(pt, (bt + b3) % 10, w4 * counts[b3] / t4)
                    else:
                        win(pt, bt, w4)
                    counts[b2] += 1
                counts[p2] += 1
            counts[b1] += 1
        counts[p1] += 1
    return prob


def test_outcome_probabilities_match_canonical_eight_deck() -> None:
    """Exact 8-deck probabilities equal the published canonical values (6 dp)."""
    prob = _exact_probs()
    assert sum(prob.values()) == pytest.approx(1.0, abs=1e-9)
    assert prob["PLAYER"] == pytest.approx(0.446247, abs=1e-5)
    assert prob["BANKER"] == pytest.approx(0.458597, abs=1e-5)
    assert prob["TIE"] == pytest.approx(0.095156, abs=1e-5)


def test_edges_match_published_values() -> None:
    """House edge per bet, computed from the ENGINE's settlement multipliers over
    the exact probabilities, matches the published 8-deck values within the
    2-dp rounding of those published figures (~5e-5)."""
    cfg = _cfg()
    prob = _exact_probs()

    def rtp(value: str) -> float:
        return sum(prob[w] * baccarat.bet_multiplier(w, value, cfg) for w in prob)

    banker_edge = 1.0 - rtp("BANKER")
    player_edge = 1.0 - rtp("PLAYER")
    tie_edge = 1.0 - rtp("TIE")

    # Published 8-deck values (commission 5 %, tie 8:1), to their stated 2 dp.
    assert banker_edge == pytest.approx(0.0106, abs=5e-5)
    assert player_edge == pytest.approx(0.0124, abs=5e-5)
    assert tie_edge == pytest.approx(0.1436, abs=5e-5)


# --------------------------------------------------------------------------- settlement


def _bets(*pairs: tuple[str, int]) -> dict[str, Any]:
    return {"bets": [{"value": v, "stakeMinor": s} for v, s in pairs]}


def _play(input: dict[str, Any], nonce: int) -> Outcome:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return _game().play(dict(input), rng, _cfg())


def _find_nonce_with_winner(winner: str, span: int = 5000) -> int:
    for nonce in range(span):
        if _play(_bets(("PLAYER", 100)), nonce).detail["winner"] == winner:
            return nonce
    raise AssertionError(f"no nonce in 0..{span} produced winner {winner}")


def test_bet_multiplier_pays_per_configured_rules() -> None:
    cfg = _cfg()
    # Player even-money (push on tie); Banker 5 % commission; Tie 8:1.
    assert baccarat.bet_multiplier("PLAYER", "PLAYER", cfg) == 2.0
    assert baccarat.bet_multiplier("TIE", "PLAYER", cfg) == 1.0
    assert baccarat.bet_multiplier("BANKER", "PLAYER", cfg) == 0.0
    assert baccarat.bet_multiplier("BANKER", "BANKER", cfg) == 1.95
    assert baccarat.bet_multiplier("TIE", "BANKER", cfg) == 1.0
    assert baccarat.bet_multiplier("PLAYER", "BANKER", cfg) == 0.0
    assert baccarat.bet_multiplier("TIE", "TIE", cfg) == 9.0
    assert baccarat.bet_multiplier("PLAYER", "TIE", cfg) == 0.0
    assert baccarat.bet_multiplier("BANKER", "TIE", cfg) == 0.0


def test_player_bet_settlement() -> None:
    nonce = _find_nonce_with_winner("PLAYER")
    outcome = _play(_bets(("PLAYER", 100)), nonce)
    assert outcome.detail["winner"] == "PLAYER"
    assert outcome.multiplier == pytest.approx(2.0)


def test_banker_bet_settlement_applies_commission() -> None:
    nonce = _find_nonce_with_winner("BANKER")
    outcome = _play(_bets(("BANKER", 100)), nonce)
    assert outcome.detail["winner"] == "BANKER"
    assert outcome.multiplier == pytest.approx(1.95)


def test_tie_bet_settlement_pays_eight_to_one() -> None:
    nonce = _find_nonce_with_winner("TIE")
    outcome = _play(_bets(("TIE", 100)), nonce)
    assert outcome.detail["winner"] == "TIE"
    assert outcome.multiplier == pytest.approx(9.0)


def test_tie_pushes_player_and_banker_bets() -> None:
    nonce = _find_nonce_with_winner("TIE")
    outcome = _play(_bets(("PLAYER", 100), ("BANKER", 100)), nonce)
    settlements = {s["value"]: s["multiplier"] for s in outcome.detail["settlements"]}
    assert settlements["PLAYER"] == pytest.approx(1.0)
    assert settlements["BANKER"] == pytest.approx(1.0)


def test_multi_bet_aggregate_is_stake_weighted() -> None:
    """Aggregate multiplier = Σ(stakeᵢ·mᵢ)/Σstakeᵢ so floor(total·m) == total payout."""
    nonce = _find_nonce_with_winner("PLAYER")
    bets = _bets(("PLAYER", 300), ("BANKER", 100), ("TIE", 50))
    outcome = _play(bets, nonce)
    total_stake = 450
    expected_payout = 300 * 2.0 + 100 * 0.0 + 50 * 0.0  # only PLAYER won
    assert outcome.multiplier == pytest.approx(expected_payout / total_stake)
    settlements = outcome.detail["settlements"]
    assert [s["value"] for s in settlements] == ["PLAYER", "BANKER", "TIE"]


# --------------------------------------------------------------------------- determinism


def test_same_inputs_same_outcome() -> None:
    a = _play(_bets(("PLAYER", 100)), 11)
    b = _play(_bets(("PLAYER", 100)), 11)
    assert a == b


def test_play_consumes_one_float_per_card() -> None:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 5)
    outcome = _game().play(_bets(("PLAYER", 100)), rng, _cfg())
    cards_used = outcome.detail["cardsUsed"]
    assert 4 <= cards_used <= 6
    assert rng.cursor == cards_used


def test_distinct_nonces_diverge() -> None:
    seen = {
        (
            _play(_bets(("PLAYER", 100)), n).detail["playerTotal"],
            _play(_bets(("PLAYER", 100)), n).detail["bankerTotal"],
        )
        for n in range(60)
    }
    assert len(seen) > 1


def test_play_resolution_matches_resolve_from_values() -> None:
    """play()'s drawn cards resolve to the same winner resolve_from_values gives —
    the rng path and the enumeration path share one resolution."""
    for nonce in range(50):
        outcome = _play(_bets(("PLAYER", 100)), nonce)
        rnd = baccarat.resolve_from_values(_dealt_sequence(outcome.detail))
        assert rnd.winner == outcome.detail["winner"]


def _dealt_sequence(detail: dict[str, Any]) -> list[int]:
    """Rebuild the shoe-consumption order from a play() detail: P1,B1,P2,B2,[P3],[B3]."""
    player = list(detail["player"])
    banker = list(detail["banker"])
    seq = [player[0], banker[0], player[1], banker[1]]
    # Player third (if any) is consumed before banker third.
    if len(player) == 3:
        seq.append(player[2])
    if len(banker) == 3:
        seq.append(banker[2])
    return seq


# --------------------------------------------------------------------------- input fence


@pytest.mark.parametrize("bad", [{}, {"bets": []}, {"bets": "PLAYER"}])
def test_validate_input_rejects_missing_or_empty_bets(bad: dict[str, Any]) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input(bad, _cfg())


@pytest.mark.parametrize(
    "bet",
    [
        {"stakeMinor": 100},  # no value
        {"value": "DRAGON", "stakeMinor": 100},  # unknown bet
        {"value": "PLAYER"},  # no stake
        {"value": "PLAYER", "stakeMinor": 0},  # non-positive
        {"value": "PLAYER", "stakeMinor": -5},
        {"value": "PLAYER", "stakeMinor": 1.5},  # non-integer
        {"value": "PLAYER", "stakeMinor": True},  # bool excluded
    ],
)
def test_validate_input_rejects_malformed_bet(bet: dict[str, Any]) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"bets": [bet]}, _cfg())


def test_validate_input_accepts_valid_bets() -> None:
    bets = _bets(("PLAYER", 100), ("BANKER", 50), ("TIE", 1))
    assert _game().validate_input(bets, _cfg()) is None


# --------------------------------------------------------------------------- RTP gate


def _rtp_targets() -> dict[str, float]:
    cfg = _cfg()
    prob = _exact_probs()
    return {
        v: sum(prob[w] * baccarat.bet_multiplier(w, v, cfg) for w in prob)
        for v in ("PLAYER", "BANKER", "TIE")
    }


@pytest.mark.parametrize("value", ["PLAYER", "BANKER"])
def test_baccarat_rtp_fast(value: str) -> None:
    """~5e4 trials on PR: low-variance Player/Banker RTP within the CLT band of the
    exact enumerated target (the shoe-sampling end-to-end check)."""
    target = _rtp_targets()[value]
    result = run_rtp(GAME_ID, _bets((value, 100)), 50_000, target)
    assert abs(result.rtp - target) <= result.tol


@pytest.mark.rtp_heavy
@pytest.mark.parametrize("value", ["PLAYER", "BANKER", "TIE"])
def test_baccarat_rtp_heavy(value: str) -> None:
    """1e6 trials (rtp-gate / nightly): every bet's RTP converges to the exact
    enumerated target, including the high-variance Tie (9×)."""
    target = _rtp_targets()[value]
    result = run_rtp(GAME_ID, _bets((value, 100)), 1_000_000, target)
    assert abs(result.rtp - target) <= result.tol
    print(
        f"\n[baccarat RTP] value={value}: rtp={result.rtp:.6f} "
        f"target={target:.6f} dev={abs(result.rtp - target):.6f} "
        f"tol(5σ CLT)={result.tol:.6f} var={result.variance:.4f} n=1000000"
    )
