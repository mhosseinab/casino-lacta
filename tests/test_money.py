"""Minor-units money math: floor rounding (house margin) + cap after rounding."""

from engine.money import apply_multiplier, cap


def test_apply_multiplier_floors_truncated_fraction_to_house() -> None:
    # 100 minor * 1.005 = 100.5 -> floored to 100; the 0.5 fraction is house margin.
    assert apply_multiplier(100, 1.005) == 100


def test_apply_multiplier_exact_product_is_kept() -> None:
    assert apply_multiplier(250, 2.0) == 500


def test_apply_multiplier_sub_unit_payout_floors_to_zero() -> None:
    # 1 minor * 0.99 = 0.99 -> 0; the loss of the sub-unit favours the house.
    assert apply_multiplier(1, 0.99) == 0


def test_apply_multiplier_returns_int() -> None:
    result = apply_multiplier(100, 1.5)
    assert isinstance(result, int)
    assert not isinstance(result, float)


def test_apply_multiplier_zero_multiplier_is_zero() -> None:
    assert apply_multiplier(10_000, 0.0) == 0


def test_cap_returns_payout_when_below_max() -> None:
    assert cap(500, 1_000) == 500


def test_cap_clamps_payout_to_max_when_exceeded() -> None:
    assert cap(5_000, 1_000) == 1_000


def test_cap_returns_max_when_equal() -> None:
    assert cap(1_000, 1_000) == 1_000


def test_cap_after_rounding_floored_product_then_capped() -> None:
    # apply_multiplier first (floored), THEN cap — payout == maxWin when floored product exceeds it.
    payout = apply_multiplier(1_000, 9.999)  # -> 9999
    assert payout == 9_999
    assert cap(payout, 5_000) == 5_000


def test_cap_returns_int() -> None:
    result = cap(5_000, 1_000)
    assert isinstance(result, int)
    assert not isinstance(result, float)
