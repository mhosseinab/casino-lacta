"""``engine.slots.features`` — composable slot feature modules (spec §B.1).

Three PURE feature mini-games the framework invokes when a config-declared trigger
fires: **free spins** (retrigger-capable), **hold-and-spin** (lock symbols, respin
the rest, reset on new locks), and **pick bonus** (choose-to-reveal until a
terminator). Each is a pure function of the seeded :class:`~engine.types.RngStream`
(and, for hold-and-spin, the base grid) plus its own config dict, returning a
``(multiplier, detail)`` contribution. Entropy enters ONLY through the passed
stream — the SAME stream the framework threads through the spin — so a feature is
deterministic given ``(serverSeed, clientSeed, nonce)`` and the verifier reproduces
it by construction.

**The seam.** :func:`apply_features` is the single dispatcher: the framework calls it
once per spin with the base ``grid`` and ``params``; for each entry in
``params["features"]`` whose :func:`trigger_fires` over the grid, it runs the matching
feature and sums the contributions. A spin with no ``"features"`` key is a no-op
(``(0.0, [])``), so a base-only machine is unaffected.

**Prize / value selection mirrors the reel-stop discipline.** A "strip" is a list;
the draw index is ``floor(f · len(strip))`` (weighting = repeated entries), exactly
as the framework picks a reel stop. RTP therefore emerges from the config (trigger
reach + the weighted strips), never a post-hoc clamp. Features return MULTIPLIER
contributions on the TOTAL bet (added directly, like ways/scatter); money rounding
and the max-win cap live later in ``app/money.py``.

**Free-spins modeling (documented abstraction).** The step's "more scatters during
the feature add spins" is modelled as a drawn prize-strip entry carrying a
``retrigger`` spin count — the retrigger prize IS the "scatter reappears" mechanic.
This keeps the feature self-contained (no base-spin re-entry / extracted helper —
YAGNI) while staying retrigger-capable. ``maxSpins`` bounds only the spin COUNT (never
any per-spin multiplier), guaranteeing termination on a malformed all-retrigger strip
without affecting RTP. hold-and-spin and pick-bonus are genuinely distinct mini-games
(not reel re-spins), so they are config-driven outright.

Pure: stdlib only; no IO/clock/``random``/``secrets``; all entropy via ``rng``.
"""

from __future__ import annotations

from math import floor
from typing import Any, cast

from engine.types import RngStream

# A spin-count safety bound for retrigger loops (count cap only, not an RTP clamp).
_DEFAULT_MAX_SPINS = 1000


def trigger_fires(grid: list[list[str]], trigger: dict[str, Any]) -> bool:
    """True iff the count of ``trigger["symbol"]`` anywhere in ``grid`` meets
    ``trigger["count"]`` (scatter-style anywhere count)."""
    symbol = cast("str", trigger["symbol"])
    count = sum(1 for reel in grid for cell in reel if cell == symbol)
    return count >= cast("int", trigger["count"])


def _draw_index(rng: RngStream, length: int) -> int:
    """Reel-stop-style index into a strip of ``length``: ``floor(f · length)``."""
    return floor(rng.next() * length)


def free_spins(rng: RngStream, cfg: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """Award ``cfg["spins"]`` spins, each drawing one prize from ``cfg["prizeStrip"]``;
    a prize's ``retrigger`` count adds more spins (retrigger-capable). Accumulates the
    per-spin multipliers. ``cfg["maxSpins"]`` bounds the total spin count.

    Consumes one draw per spin played. Returns ``(total_multiplier, {"spinsPlayed": n})``.
    """
    strip = cast("list[dict[str, Any]]", cfg["prizeStrip"])
    max_spins = cast("int", cfg.get("maxSpins", _DEFAULT_MAX_SPINS))
    remaining = cast("int", cfg["spins"])
    total = 0.0
    played = 0
    while remaining > 0 and played < max_spins:
        remaining -= 1
        played += 1
        prize = strip[_draw_index(rng, len(strip))]
        total += float(prize["multiplier"])
        remaining += cast("int", prize.get("retrigger", 0))
    return total, {"spinsPlayed": played}


def hold_and_spin(
    grid: list[list[str]], rng: RngStream, cfg: dict[str, Any]
) -> tuple[float, dict[str, Any]]:
    """Lock the triggering coin symbols, respin the rest, reset on new locks, pay on
    the final locked set.

    Positions are the flattened grid in (reel, row) order. Every cell equal to
    ``cfg["coinSymbol"]`` locks initially and draws a value from
    ``cfg["coinValueStrip"]``. Then, for up to ``cfg["respins"]`` respins, each unlocked
    position draws once: with probability ``cfg["coinChance"]`` (``f < coinChance``) it
    locks and draws a value. A round that produces any new lock RESETS the respin
    counter; a round with none decrements it. Stops when respins are exhausted or the
    grid is full. Returns ``(sum_of_locked_values, {"coins": k, "lockedValues": [...]})``
    with locked values in position order.
    """
    coin = cast("str", cfg["coinSymbol"])
    value_strip = cast("list[float]", cfg["coinValueStrip"])
    max_respins = cast("int", cfg["respins"])
    coin_chance = cast("float", cfg["coinChance"])

    positions = [(r, row) for r, reel in enumerate(grid) for row in range(len(reel))]
    locked: dict[tuple[int, int], float] = {}
    for pos in positions:
        if grid[pos[0]][pos[1]] == coin:
            locked[pos] = float(value_strip[_draw_index(rng, len(value_strip))])

    respins_left = max_respins
    while respins_left > 0 and len(locked) < len(positions):
        new_locks = 0
        for pos in positions:
            if pos in locked:
                continue
            if rng.next() < coin_chance:
                locked[pos] = float(value_strip[_draw_index(rng, len(value_strip))])
                new_locks += 1
        respins_left = max_respins if new_locks else respins_left - 1

    values = [locked[pos] for pos in positions if pos in locked]
    return sum(values), {"coins": len(values), "lockedValues": values}


def pick_bonus(rng: RngStream, cfg: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """Reveal prizes from ``cfg["prizePool"]`` (without replacement) until a
    terminator prize is revealed or the pool empties; accumulate the multipliers.

    Each pick draws one index ``floor(f · len(remaining))`` into the shrinking pool —
    the stream is the server-authoritative "pick"; a client tap is cosmetic. A revealed
    prize with ``terminator`` true ends the bonus (it does not pay). Returns
    ``(total_multiplier, {"reveals": [...], "terminated": bool})`` where ``reveals`` are
    the non-terminator multipliers in reveal order.
    """
    pool = list(cast("list[dict[str, Any]]", cfg["prizePool"]))
    total = 0.0
    reveals: list[float] = []
    terminated = False
    while pool:
        prize = pool.pop(_draw_index(rng, len(pool)))
        if prize.get("terminator", False):
            terminated = True
            break
        total += float(prize["multiplier"])
        reveals.append(float(prize["multiplier"]))
    return total, {"reveals": reveals, "terminated": terminated}


def apply_features(
    grid: list[list[str]], rng: RngStream, params: dict[str, Any]
) -> tuple[float, list[dict[str, Any]]]:
    """Run every configured feature whose trigger fires over ``grid``; sum the
    contributions. The framework's single feature hook.

    For each entry in ``params["features"]`` (a list of feature specs, each with a
    ``"type"``, a ``"trigger"`` and the feature's own params) whose :func:`trigger_fires`
    holds, dispatch to the matching feature and record
    ``{"type": ..., "multiplier": m, **detail}``. No ``"features"`` key → ``(0.0, [])``.
    """
    specs = cast("list[dict[str, Any]]", params.get("features", []))
    total = 0.0
    results: list[dict[str, Any]] = []
    for spec in specs:
        if not trigger_fires(grid, cast("dict[str, Any]", spec["trigger"])):
            continue
        kind = cast("str", spec["type"])
        if kind == "freeSpins":
            mult, detail = free_spins(rng, spec)
        elif kind == "holdAndSpin":
            mult, detail = hold_and_spin(grid, rng, spec)
        elif kind == "pickBonus":
            mult, detail = pick_bonus(rng, spec)
        else:  # pragma: no cover - config fence; S23 only registers known types
            raise ValueError(f"unknown slot feature type: {kind!r}")
        total += mult
        results.append({"type": kind, "multiplier": mult, **detail})
    return total, results
