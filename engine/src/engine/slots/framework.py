"""``engine.slots.framework`` — the data-driven slot engine (spec §B.1).

ONE :class:`SlotMachine` resolves any machine from ``GameConfig.params``: it picks
a **stop index per reel** from that reel's weighted strip via the seeded stream,
builds the ``reels × rows`` grid (a ``rows``-tall window over the cyclic strip),
evaluates **paylines** or **ways-to-win** against the paytable (wild substitution,
scatter-anywhere), sums the base wins, and returns a single ``Outcome.multiplier``.
No feature modules (free spins, hold-and-spin) — those land in S22.

**RTP emerges from the strip weights + paytable — never a clamp.** A symbol's
frequency on a reel's strip IS its probability; tuning RTP means editing the strips
or paytable (config), never the outcome. The same ``(server, client, nonce)`` stream
always yields the same stops/grid/wins, so server == verifier by construction.

Config schema (``GameConfig.params`` — JSON-round-trippable, string-keyed counts)::

    {
      "reels": 5, "rows": 3,
      "mode": "lines" | "ways",
      "strips": [[sym, sym, …], …],   # one weighted strip per reel
      "symbols": [sym, …],            # the symbol set
      "paytable": {sym: {"<count>": multiplier, …}, …},
      "paylines": [[row_per_reel, …], …],   # required for "lines"
      "wild": sym | null,             # substitutes for any line/ways symbol
      "scatter": sym | null,          # pays by total count anywhere
      "scatterPaytable": {"<count>": multiplier, …}   # optional, scatter pays
    }

**Multiplier convention** (``Outcome.multiplier`` is payout / TOTAL stake):

* a **paytable entry is a PER-LINE multiplier** (it rides the per-line bet); the
  total stake is split evenly across ``paylines``, so the line contribution is
  ``Σ line_mult / num_lines``. S23's config author and the per-machine RTP gate
  both rely on this convention.
* **ways** and **scatter** entries are multipliers on the **TOTAL bet**, so they
  add directly (no ``/num_lines``).

Because the multiplier is payout/stake, the stake amount itself CANCELS — money
(rounding, max-win cap) is applied later in ``app/money.py``. The step's
``play(stake, …)`` is therefore realised as the seam's ``play(input, rng, cfg)``:
stake is not an engine input here.

**Wild handling (documented simplifications):** a reel/line cell matches a symbol
if it equals that symbol OR the wild; on a line the paying symbol is the first
non-wild (an all-wild line pays the wild's own paytable row, if any). Ways counts
wilds toward every symbol's per-reel cell count. There is no "wild pays the best
symbol on an all-wild reel" maximisation — test configs avoid that ambiguity.

Pure: stdlib only; entropy enters solely via the injected ``RngStream``; strips,
paytable, and lines are read from ``cfg``.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any, cast

from engine.types import GameConfig, Outcome, RngStream


@dataclass(frozen=True)
class SlotMachine:
    """Designed to conform to ``engine.types.InstantGame`` (spec §B.1) — S23 supplies
    the ``id`` when a concrete machine registers; the framework itself carries none.

    Stateless and config-driven: every machine is a distinct ``GameConfig`` fed to
    the same evaluator (``validate_input`` + ``play`` match the seam shape already).
    """

    def validate_input(self, input: dict[str, Any], cfg: GameConfig) -> None:
        """No per-bet game input: a spin's only variable is stake (fenced by the
        app's money/limit checks). Explicit empty body conforms to the
        ``InstantGame`` fence seam (OCP) — line/lines selection arrives later."""

    def play(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> Outcome:
        """Resolve one spin — pure function of (rng stream, cfg).

        Consumes EXACTLY ``reels`` draws (one stop per reel): for reel ``r`` the stop
        is ``floor(rng.next() · len(strip[r]))`` and the visible column is the
        ``rows``-tall window starting at that stop, wrapping the cyclic strip. The
        grid is then evaluated by paylines or ways; scatter is added on top.
        """
        params = cfg.params
        reels = cast("int", params["reels"])
        rows = cast("int", params["rows"])
        strips = cast("list[list[str]]", params["strips"])
        wild = cast("str | None", params.get("wild"))
        scatter = cast("str | None", params.get("scatter"))
        paytable = cast("dict[str, dict[str, float]]", params["paytable"])

        stops = [floor(rng.next() * len(strips[r])) for r in range(reels)]
        grid = [_column(strips[r], stops[r], rows) for r in range(reels)]

        detail: dict[str, Any] = {"stops": stops, "grid": grid}

        if cast("str", params.get("mode", "lines")) == "ways":
            symbols = cast("list[str]", params["symbols"])
            total, way_wins = _eval_ways(grid, symbols, paytable, wild, scatter)
            detail["wayWins"] = way_wins
        else:
            paylines = cast("list[list[int]]", params["paylines"])
            line_sum, line_wins = _eval_lines(grid, paylines, paytable, wild)
            detail["lineWins"] = line_wins
            total = line_sum / len(paylines) if paylines else 0.0

        scatter_total, scatter_win = _eval_scatter(grid, scatter, params)
        if scatter_win is not None:
            detail["scatterWin"] = scatter_win
        total += scatter_total

        return Outcome(multiplier=total, detail=detail)


def _column(strip: list[str], stop: int, rows: int) -> list[str]:
    """The ``rows``-tall visible window over the cyclic ``strip`` starting at ``stop``."""
    n = len(strip)
    return [strip[(stop + row) % n] for row in range(rows)]


def _eval_lines(
    grid: list[list[str]],
    paylines: list[list[int]],
    paytable: dict[str, dict[str, float]],
    wild: str | None,
) -> tuple[float, list[dict[str, Any]]]:
    """Sum the per-line multipliers (left-to-right runs) and record each win.

    For each payline the symbols are read off the grid; the paying symbol is the
    first non-wild (all-wild → the wild itself); the run is the leading streak of
    cells equal to that symbol or the wild; ``paytable[symbol][str(count)]`` (when
    present) is the per-line multiplier. Returns the UN-normalised sum — the caller
    divides by the line count.
    """
    line_sum = 0.0
    wins: list[dict[str, Any]] = []
    for index, line in enumerate(paylines):
        symbols = [grid[reel][row] for reel, row in enumerate(line)]
        paying = next((s for s in symbols if s != wild), wild)
        if paying is None:
            continue
        count = 0
        for s in symbols:
            if s == paying or s == wild:
                count += 1
            else:
                break
        mult = paytable.get(paying, {}).get(str(count))
        if mult:
            line_sum += float(mult)
            wins.append(
                {"line": index, "symbol": paying, "count": count, "multiplier": float(mult)}
            )
    return line_sum, wins


def _eval_ways(
    grid: list[list[str]],
    symbols: list[str],
    paytable: dict[str, dict[str, float]],
    wild: str | None,
    scatter: str | None,
) -> tuple[float, list[dict[str, Any]]]:
    """Sum the ways-to-win multipliers (any-adjacent-reel from reel 0).

    For each payable symbol (excluding wild/scatter): count, per reel, the cells
    equal to that symbol or the wild; take the leading run of reels with a non-zero
    count; ``ways`` is the product of those per-reel counts; the multiplier is
    ``paytable[symbol][str(run_len)] · ways`` (a multiplier on TOTAL bet). Returns
    the sum directly (no per-line normalisation).
    """
    total = 0.0
    wins: list[dict[str, Any]] = []
    for symbol in symbols:
        if symbol == wild or symbol == scatter:
            continue
        run = 0
        ways = 1
        for reel in grid:
            matches = sum(1 for cell in reel if cell == symbol or cell == wild)
            if matches == 0:
                break
            run += 1
            ways *= matches
        base = paytable.get(symbol, {}).get(str(run))
        if base:
            mult = float(base) * ways
            total += mult
            wins.append({"symbol": symbol, "reels": run, "ways": ways, "multiplier": mult})
    return total, wins


def _eval_scatter(
    grid: list[list[str]],
    scatter: str | None,
    params: dict[str, Any],
) -> tuple[float, dict[str, Any] | None]:
    """Scatter pays by TOTAL count anywhere in the grid (a multiplier on total bet).

    Returns ``(0.0, None)`` when there is no scatter symbol/table or the count is
    not in the table; otherwise the multiplier and a win record.
    """
    if scatter is None:
        return 0.0, None
    table = cast("dict[str, float] | None", params.get("scatterPaytable"))
    if not table:
        return 0.0, None
    count = sum(1 for reel in grid for cell in reel if cell == scatter)
    mult = table.get(str(count))
    if not mult:
        return 0.0, None
    return float(mult), {"symbol": scatter, "count": count, "multiplier": float(mult)}
