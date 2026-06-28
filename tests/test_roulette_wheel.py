"""S26 — European Roulette (single-zero wheel, ``table.roulette``) — spec §B.3.

37 pockets (0–36); one draw → ``pocket = floor(f·37)`` ∈ {0,…,36}; every standard
inside/outside bet settles via a payout table carried in ``GameConfig.params``
(config, never literals). Because each payout is the fair ``35:1 … 1:1`` and the
true probability is ``count/37``, EVERY bet's per-bet RTP is

    (count/37) · (payout + 1) = 36/37 = 0.9730…   (NOT a clamp)

so the single pocket 0 is the entire 1/37 ≈ 2.70% house edge: every even-money /
column / dozen / inside cover EXCLUDES 0, and only a straight/split/… that names 0
wins on it.

Distinct game id from the Originals ``originals.roulette`` (the 0–99 colour-pick
game) by design.

Tests:
* determinism — same seed → same pocket; ``pocket = floor(f·37)``; one draw/spin.
* uniformity — pocket uniform over 0–36 within a 5σ binomial band (derived, not flat).
* settlement — hand-checked single- and multi-bet payouts via a controlled stub rng,
  including the 0 edge case.
* config structure — red⊎black partition {1..36}; each even-money cover has 18; each
  column/dozen has 12 and the trios partition {1..36}; 0 in no outside cover. (Guards
  the sibling covers the tier-representative RTP tests never exercise.)
* RTP — each bet type's measured RTP = 1-edge = 36/37 within the harness 5σ CLT
  half-width (fast representative-per-tier at 1e6; ``rtp_heavy`` 1e7 for the
  high-variance inside bets). The harness measures UNCAPPED engine math.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from engine.registry import default_config, load_game
from engine.rng import create_rng
from engine.types import GameConfig, InstantGame, InvalidBetInput, Outcome

GAME_ID = "table.roulette"
POCKET_COUNT = 37
SERVER_SEED = b"roulette-wheel-determinism-server-seed"
CLIENT_SEED = "player-client-seed"

_Z = 5.0  # 5σ → ~6e-7 two-sided false-reject rate; deterministic under a fixed seed.
_UNIFORM_SERVER_SEED = b"roulette-wheel-uniform-server-seed"
_UNIFORM_CLIENT_SEED = "roulette-wheel-uniform"


def _game() -> InstantGame:
    return load_game(GAME_ID)  # type: ignore[return-value]


def _cfg() -> GameConfig:
    return default_config(GAME_ID)


class _StubRng:
    """A controlled ``RngStream`` returning a fixed float — for exact-pocket tests."""

    def __init__(self, value: float) -> None:
        self._value = value
        self.cursor = 0

    def next(self) -> float:
        self.cursor += 1
        return self._value


def _rng_for_pocket(pocket: int) -> _StubRng:
    """A stub whose single draw maps to exactly ``pocket`` (``floor(f·37)``)."""
    return _StubRng((pocket + 0.5) / POCKET_COUNT)


def _play_pocket(input: dict[str, Any], pocket: int) -> Outcome:
    return _game().play(dict(input), _rng_for_pocket(pocket), _cfg())


def _real_play(input: dict[str, Any], nonce: int) -> Outcome:
    """The exact call the bet loop makes: create_rng → game.play."""
    rng = create_rng(SERVER_SEED, CLIENT_SEED, nonce)
    return _game().play(dict(input), rng, _cfg())


def _expected_pocket(nonce: int) -> int:
    f = create_rng(SERVER_SEED, CLIENT_SEED, nonce).next()
    return math.floor(f * POCKET_COUNT)


# --------------------------------------------------------------------------- determinism


def test_same_inputs_same_outcome() -> None:
    a = _real_play({"bets": [{"type": "RED", "stakeMinor": 100}]}, 7)
    b = _real_play({"bets": [{"type": "RED", "stakeMinor": 100}]}, 7)
    assert a == b
    assert a.detail["pocket"] == b.detail["pocket"]


def test_pocket_matches_floor_of_first_draw() -> None:
    """pocket == floor(f·37) ∈ {0,…,36} for the stream's first draw (spec §B.3)."""
    for nonce in range(300):
        outcome = _real_play({"bets": [{"type": "RED", "stakeMinor": 100}]}, nonce)
        pocket = outcome.detail["pocket"]
        assert pocket == _expected_pocket(nonce)
        assert 0 <= pocket <= 36


def test_consumes_exactly_one_draw() -> None:
    rng = create_rng(SERVER_SEED, CLIENT_SEED, 3)
    _game().play({"bets": [{"type": "RED", "stakeMinor": 100}]}, rng, _cfg())
    assert rng.cursor == 1


def test_distinct_nonces_diverge() -> None:
    pockets = {
        _real_play({"bets": [{"type": "RED", "stakeMinor": 100}]}, n).detail["pocket"]
        for n in range(60)
    }
    assert len(pockets) > 1


# --------------------------------------------------------------------------- uniformity


def _measure_uniformity(n: int) -> tuple[list[int], float, float]:
    """Count pocket occurrences over ``n`` seeded spins.

    Returns ``(counts, expected, half_width)`` with ``expected = n/37`` and the 5σ
    binomial band ``_Z·sqrt(n·p·(1-p))``, ``p = 1/37`` (derived, never hand-set)."""
    counts = [0] * POCKET_COUNT
    for nonce in range(n):
        f = create_rng(_UNIFORM_SERVER_SEED, _UNIFORM_CLIENT_SEED, nonce).next()
        counts[math.floor(f * POCKET_COUNT)] += 1
    p = 1.0 / POCKET_COUNT
    expected = n * p
    half_width = _Z * math.sqrt(n * p * (1.0 - p))
    return counts, expected, half_width


def test_pocket_uniform_over_0_36_fast() -> None:
    """~3e5 spins on PR: every pocket 0–36 within the 5σ binomial band of n/37."""
    n = 300_000
    counts, expected, half_width = _measure_uniformity(n)
    assert sum(counts) == n
    for pocket, count in enumerate(counts):
        assert abs(count - expected) <= half_width, (
            f"pocket {pocket}: count {count} deviates from {expected:.1f} "
            f"by {abs(count - expected):.1f} > 5σ band {half_width:.1f}"
        )


@pytest.mark.rtp_heavy
def test_pocket_uniform_over_0_36_heavy() -> None:
    """1e7 spins (CI rtp-gate / nightly): pocket distribution converges to uniform."""
    n = 10_000_000
    counts, expected, half_width = _measure_uniformity(n)
    assert sum(counts) == n
    worst = max(abs(c - expected) for c in counts)
    for count in counts:
        assert abs(count - expected) <= half_width
    print(
        f"\n[roulette-wheel uniformity] n={n}: expected={expected:.1f}/bucket "
        f"worst_dev={worst:.1f} tol(5σ binomial)={half_width:.1f}"
    )


# ------------------------------------------------------------------- config structure


def test_red_black_partition_1_to_36() -> None:
    params = _cfg().params
    red = set(params["covers"]["RED"])
    black = set(params["covers"]["BLACK"])
    assert len(red) == 18
    assert len(black) == 18
    assert red.isdisjoint(black)
    assert red | black == set(range(1, 37))
    assert 0 not in red and 0 not in black


@pytest.mark.parametrize("name", ["RED", "BLACK", "ODD", "EVEN", "HIGH", "LOW"])
def test_even_money_cover_has_18_and_excludes_zero(name: str) -> None:
    cover = set(_cfg().params["covers"][name])
    assert len(cover) == 18
    assert 0 not in cover
    assert cover <= set(range(1, 37))


@pytest.mark.parametrize("kind", ["COLUMN", "DOZEN"])
def test_columns_and_dozens_each_12_and_partition(kind: str) -> None:
    trios = _cfg().params["indexed_covers"][kind]
    assert len(trios) == 3
    union: set[int] = set()
    for trio in trios:
        s = set(trio)
        assert len(s) == 12
        assert 0 not in s
        assert s.isdisjoint(union)
        union |= s
    assert union == set(range(1, 37))


def test_payout_table_is_fair_per_count() -> None:
    """For every bet type (count·(payout+1))/37 == 36/37 — the RTP-by-construction."""
    params = _cfg().params
    payouts = params["payouts"]
    sizes = {
        "STRAIGHT": 1, "SPLIT": 2, "STREET": 3, "CORNER": 4, "LINE": 6,
        "RED": 18, "BLACK": 18, "ODD": 18, "EVEN": 18, "HIGH": 18, "LOW": 18,
        "COLUMN": 12, "DOZEN": 12,
    }
    assert set(payouts) == set(sizes)
    for kind, count in sizes.items():
        assert count * (payouts[kind] + 1) == 36, kind


# --------------------------------------------------------------------------- settlement


def test_straight_win_pays_36x_and_miss_pays_zero() -> None:
    bet = {"type": "STRAIGHT", "numbers": [17], "stakeMinor": 100}
    win = _play_pocket({"bets": [bet]}, 17)
    assert win.detail["settlements"][0]["won"] is True
    assert win.multiplier == pytest.approx(36.0)
    miss = _play_pocket({"bets": [bet]}, 18)
    assert miss.detail["settlements"][0]["won"] is False
    assert miss.multiplier == 0.0


def test_split_corner_street_line_pay_correct_multiplier() -> None:
    cases = [
        ({"type": "SPLIT", "numbers": [1, 2]}, 2, 18.0),
        ({"type": "STREET", "numbers": [4, 5, 6]}, 5, 12.0),
        ({"type": "CORNER", "numbers": [1, 2, 4, 5]}, 5, 9.0),
        ({"type": "LINE", "numbers": [1, 2, 3, 4, 5, 6]}, 6, 6.0),
    ]
    for sel, pocket, mult in cases:
        outcome = _play_pocket({"bets": [{**sel, "stakeMinor": 100}]}, pocket)
        assert outcome.multiplier == pytest.approx(mult), sel["type"]


def test_red_black_settle_by_colour() -> None:
    red_bet = {"type": "RED", "stakeMinor": 100}
    assert _play_pocket({"bets": [red_bet]}, 1).multiplier == pytest.approx(2.0)  # 1 red
    assert _play_pocket({"bets": [red_bet]}, 2).multiplier == 0.0  # 2 black


def test_column_and_dozen_settle_by_index() -> None:
    col1 = {"type": "COLUMN", "index": 1, "stakeMinor": 100}  # 1,4,...,34
    assert _play_pocket({"bets": [col1]}, 4).multiplier == pytest.approx(3.0)
    assert _play_pocket({"bets": [col1]}, 5).multiplier == 0.0
    doz2 = {"type": "DOZEN", "index": 2, "stakeMinor": 100}  # 13..24
    assert _play_pocket({"bets": [doz2]}, 20).multiplier == pytest.approx(3.0)
    assert _play_pocket({"bets": [doz2]}, 12).multiplier == 0.0


def test_zero_is_the_house_edge() -> None:
    """On pocket 0 every outside bet loses; only a straight naming 0 wins."""
    for outside in [
        {"type": "RED", "stakeMinor": 100},
        {"type": "BLACK", "stakeMinor": 100},
        {"type": "ODD", "stakeMinor": 100},
        {"type": "EVEN", "stakeMinor": 100},
        {"type": "HIGH", "stakeMinor": 100},
        {"type": "LOW", "stakeMinor": 100},
        {"type": "COLUMN", "index": 1, "stakeMinor": 100},
        {"type": "DOZEN", "index": 1, "stakeMinor": 100},
    ]:
        assert _play_pocket({"bets": [outside]}, 0).multiplier == 0.0, outside["type"]
    straight0 = {"type": "STRAIGHT", "numbers": [0], "stakeMinor": 100}
    assert _play_pocket({"bets": [straight0]}, 0).multiplier == pytest.approx(36.0)


def test_multi_bet_stake_weighted_aggregate() -> None:
    """Hand-checked: pocket 1 (red, low, odd, dozen1, column1).

    straight[1]·100 wins 36×; RED·100 wins 2×; BLACK·100 loses; DOZEN#2·100 loses.
    total_stake=400; weighted payout = 100·36 + 100·2 = 3800;
    aggregate = 3800/400 = 9.5; floor(400·9.5) = 3800 (the loop's credited total).
    """
    bets = [
        {"type": "STRAIGHT", "numbers": [1], "stakeMinor": 100},
        {"type": "RED", "stakeMinor": 100},
        {"type": "BLACK", "stakeMinor": 100},
        {"type": "DOZEN", "index": 2, "stakeMinor": 100},
    ]
    outcome = _play_pocket({"bets": bets}, 1)
    assert [s["won"] for s in outcome.detail["settlements"]] == [True, True, False, False]
    total_stake = 400
    expected_total = 100 * 36 + 100 * 2
    assert math.floor(total_stake * outcome.multiplier) == expected_total
    assert outcome.multiplier == pytest.approx(expected_total / total_stake)


def test_same_bet_placed_twice_both_settle() -> None:
    bets = [
        {"type": "STRAIGHT", "numbers": [7], "stakeMinor": 100},
        {"type": "STRAIGHT", "numbers": [7], "stakeMinor": 100},
    ]
    outcome = _play_pocket({"bets": bets}, 7)
    assert all(s["won"] for s in outcome.detail["settlements"])
    assert outcome.multiplier == pytest.approx(36.0)


# --------------------------------------------------------------------------- input fence


@pytest.mark.parametrize("bad", [{}, {"bets": []}, {"bets": "RED"}])
def test_validate_rejects_missing_or_empty_bets(bad: dict[str, Any]) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input(bad, _cfg())


@pytest.mark.parametrize(
    "bet",
    [
        {"stakeMinor": 100},  # no type
        {"type": "PURPLE", "stakeMinor": 100},  # unknown type
        {"type": "RED"},  # no stake
        {"type": "RED", "stakeMinor": 0},  # non-positive
        {"type": "RED", "stakeMinor": -5},
        {"type": "RED", "stakeMinor": 1.5},  # non-integer
        {"type": "RED", "stakeMinor": "100"},  # non-integer
        {"type": "RED", "stakeMinor": True},  # bool is not a stake
    ],
)
def test_validate_rejects_malformed_bet(bet: dict[str, Any]) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"bets": [bet]}, _cfg())


@pytest.mark.parametrize(
    "bet",
    [
        {"type": "STRAIGHT", "numbers": [1, 2], "stakeMinor": 100},  # wrong count
        {"type": "SPLIT", "numbers": [1], "stakeMinor": 100},  # wrong count
        {"type": "STRAIGHT", "numbers": [37], "stakeMinor": 100},  # out of range
        {"type": "STRAIGHT", "numbers": [-1], "stakeMinor": 100},  # out of range
        {"type": "SPLIT", "numbers": [1, 1], "stakeMinor": 100},  # duplicate
        {"type": "STRAIGHT", "stakeMinor": 100},  # missing numbers
        {"type": "STRAIGHT", "numbers": "1", "stakeMinor": 100},  # not a list
    ],
)
def test_validate_rejects_bad_inside_selection(bet: dict[str, Any]) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"bets": [bet]}, _cfg())


@pytest.mark.parametrize(
    "bet",
    [
        {"type": "COLUMN", "stakeMinor": 100},  # missing index
        {"type": "COLUMN", "index": 0, "stakeMinor": 100},  # out of range
        {"type": "COLUMN", "index": 4, "stakeMinor": 100},  # out of range
        {"type": "DOZEN", "index": "1", "stakeMinor": 100},  # non-int
    ],
)
def test_validate_rejects_bad_indexed_selection(bet: dict[str, Any]) -> None:
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"bets": [bet]}, _cfg())


def test_validate_rejects_when_any_bet_malformed() -> None:
    bets = [
        {"type": "RED", "stakeMinor": 100},
        {"type": "PURPLE", "stakeMinor": 100},
    ]
    with pytest.raises(InvalidBetInput):
        _game().validate_input({"bets": bets}, _cfg())


def test_validate_accepts_valid_bets() -> None:
    bets = [
        {"type": "STRAIGHT", "numbers": [0], "stakeMinor": 100},
        {"type": "SPLIT", "numbers": [1, 2], "stakeMinor": 50},
        {"type": "STREET", "numbers": [4, 5, 6], "stakeMinor": 10},
        {"type": "CORNER", "numbers": [1, 2, 4, 5], "stakeMinor": 10},
        {"type": "LINE", "numbers": [1, 2, 3, 4, 5, 6], "stakeMinor": 10},
        {"type": "RED", "stakeMinor": 100},
        {"type": "COLUMN", "index": 2, "stakeMinor": 100},
        {"type": "DOZEN", "index": 3, "stakeMinor": 100},
    ]
    assert _game().validate_input({"bets": bets}, _cfg()) is None


# --------------------------------------------------------------------------- RTP


TARGET_RTP = 1.0 - 1.0 / 37.0  # 1 - edge = 36/37 (the config carries edge = 1/37)

# One representative bet per payout tier (CLT band auto-widens with variance).
_FAST_BETS: dict[str, dict[str, Any]] = {
    "STRAIGHT": {"type": "STRAIGHT", "numbers": [17], "stakeMinor": 100},
    "SPLIT": {"type": "SPLIT", "numbers": [1, 2], "stakeMinor": 100},
    "STREET": {"type": "STREET", "numbers": [4, 5, 6], "stakeMinor": 100},
    "CORNER": {"type": "CORNER", "numbers": [1, 2, 4, 5], "stakeMinor": 100},
    "LINE": {"type": "LINE", "numbers": [1, 2, 3, 4, 5, 6], "stakeMinor": 100},
    "COLUMN": {"type": "COLUMN", "index": 1, "stakeMinor": 100},
    "DOZEN": {"type": "DOZEN", "index": 2, "stakeMinor": 100},
    "EVEN_MONEY": {"type": "RED", "stakeMinor": 100},
}


def _import_run_rtp() -> Any:
    from rtp_harness import run_rtp

    return run_rtp


@pytest.mark.parametrize("tier", sorted(_FAST_BETS))
def test_per_bet_rtp_is_36_over_37_fast(tier: str) -> None:
    """~5e5 trials on PR: each bet tier's RTP within the 5σ CLT half-width of 36/37."""
    run_rtp = _import_run_rtp()
    result = run_rtp(GAME_ID, {"bets": [_FAST_BETS[tier]]}, 500_000, TARGET_RTP)
    assert abs(result.rtp - TARGET_RTP) <= result.tol


@pytest.mark.rtp_heavy
@pytest.mark.parametrize("tier", ["STRAIGHT", "SPLIT", "STREET", "CORNER"])
def test_per_bet_rtp_is_36_over_37_heavy(tier: str) -> None:
    """1e7 trials (CI rtp-gate): high-variance inside bets converge to 36/37."""
    run_rtp = _import_run_rtp()
    result = run_rtp(GAME_ID, {"bets": [_FAST_BETS[tier]]}, 10_000_000, TARGET_RTP)
    assert abs(result.rtp - TARGET_RTP) <= result.tol
    print(
        f"\n[roulette-wheel RTP] tier={tier}: rtp={result.rtp:.6f} "
        f"target={TARGET_RTP:.6f} dev={abs(result.rtp - TARGET_RTP):.6f} "
        f"tol(5σ CLT)={result.tol:.6f} var={result.variance:.4f} n=10000000"
    )
