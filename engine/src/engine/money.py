"""Minor-units money math — the single owner of payout rounding + caps.

Money is integer minor units everywhere. `apply_multiplier` rounds DOWN (floor):
the truncated fractional minor unit is house margin. `cap` is applied AFTER rounding.
No float is ever stored or settled — these helpers return ints. Build-plan_v2 §3, iron rules.
"""

import math


def apply_multiplier(stake_minor: int, multiplier: float) -> int:
    """Floor `stake_minor * multiplier` to whole minor units.

    The truncated fraction is kept by the house (margin). Floats enter only as the
    multiplier; the returned payout is an integer count of minor units, never a float.
    """
    return math.floor(stake_minor * multiplier)


def cap(payout_minor: int, max_win_minor: int) -> int:
    """Clamp a (already floored) payout to the max-win ceiling.

    Applied AFTER `apply_multiplier`: payout == max_win when the floored product exceeds it.
    """
    return min(payout_minor, max_win_minor)
