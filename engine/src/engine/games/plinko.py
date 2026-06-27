"""``originals.plinko`` — the provably-fair Plinko Original (spec §A.5).

Drop a ball through ``R`` rows of pegs into one of ``R+1`` bins. Each row is one
draw from the seeded stream: the ball bounces RIGHT when ``f < 0.5``, so
``rightBounces = Σ[f < 0.5]`` over the ``R`` draws and the landing ``bin =
rightBounces`` ~ ``Binomial(R, ½)`` (spec §A.5 RNG mapping). The bin indexes a
per-``(rows, risk)`` multiplier table; the rendered ``path`` is advisory but always
ends in the authoritative server bin (``path.count("R") == bin``).

**Tuned table (the crux).** Bin ``i`` has probability ``P(i) = C(R,i)/2^R``; the
published 2-dp multipliers ``{m_i}`` are TUNED per ``(rows, risk)`` so the analytic
RTP ``Σ P(i)·m_i = 1 - edge`` AFTER 2-dp rounding (verified to within ±0.2% for
every combo by ``tests/test_plinko_rtp.py``). Outcomes are NEVER clamped to hit
target — only the published table is optimised. The tables live in
``cfg.params["tables"]`` (config, not literals) and the accepted ``(rows, risk)``
set is derived from there, so adding a board is a config change.

Pure: stdlib only; entropy enters solely via the injected ``RngStream``; the
multiplier table + ``edge`` are read from ``cfg``. The same ``(server_seed,
client_seed, nonce)`` + ``input`` always yields the same bin/path/multiplier —
server == verifier by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from engine.types import GameConfig, InvalidBetInput, Outcome, RngStream

# The ball bounces right when the draw is below this threshold; with uniform
# ``f ∈ [0, 1)`` this is an exact p=½ per row (spec §A.5).
_RIGHT_THRESHOLD = 0.5


def _tables(cfg: GameConfig) -> dict[str, dict[str, list[float]]]:
    """The per-(rows, risk) multiplier tables — the single source of truth for both
    the input fence and :meth:`Plinko.play` (keys are JSON-friendly string rows)."""
    return cast("dict[str, dict[str, list[float]]]", cfg.params["tables"])


@dataclass(frozen=True)
class Plinko:
    """Conforms to ``engine.types.InstantGame`` (spec §A.5)."""

    id: str = "originals.plinko"

    def validate_input(self, input: dict[str, Any], cfg: GameConfig) -> None:
        """Reject malformed / unsupported plinko input BEFORE any money moves (pure).

        ``rows`` must be an ``int`` naming a published board and ``risk`` a string
        naming a tier configured for that board — both derived from
        ``cfg.params["tables"]`` (no hardcoded set). This is the COMPLETE fence:
        passing it guarantees :meth:`play` cannot raise on this input.
        """
        tables = _tables(cfg)
        if "rows" not in input:
            raise InvalidBetInput("plinko requires 'rows'")
        if "risk" not in input:
            raise InvalidBetInput("plinko requires 'risk'")
        rows = input["rows"]
        # bool is a subclass of int — exclude it explicitly so True/False can't pose
        # as a row count.
        if not isinstance(rows, int) or isinstance(rows, bool):
            raise InvalidBetInput(f"plinko 'rows' must be an int, got {rows!r}")
        by_risk = tables.get(str(rows))
        if by_risk is None:
            raise InvalidBetInput(
                f"plinko 'rows' {rows} is not a supported board "
                f"(have {sorted(int(r) for r in tables)})"
            )
        risk = input["risk"]
        if not isinstance(risk, str) or risk not in by_risk:
            raise InvalidBetInput(
                f"plinko 'risk' {risk!r} is not supported for rows={rows} "
                f"(have {sorted(by_risk)})"
            )

    def play(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> Outcome:
        """Resolve one plinko ball — pure function of (input, rng stream, cfg).

        Consumes exactly ``rows`` draws (one bounce per row); ``rightBounces`` is
        the count of draws below ``_RIGHT_THRESHOLD`` and ``bin = rightBounces``.
        The multiplier is the published table entry for the landing bin; the ``path``
        is recorded from the same draws so it always ends in that bin.
        """
        rows: int = input["rows"]
        risk: str = input["risk"]
        table = _tables(cfg)[str(rows)][risk]

        path: list[str] = []
        right_bounces = 0
        for _ in range(rows):
            if rng.next() < _RIGHT_THRESHOLD:
                right_bounces += 1
                path.append("R")
            else:
                path.append("L")

        bin_index = right_bounces
        return Outcome(
            multiplier=float(table[bin_index]),
            detail={
                "rows": rows,
                "risk": risk,
                "bin": bin_index,
                "rightBounces": right_bounces,
                "path": path,
            },
        )


# The module-level singleton the registry resolves (see engine.games.__init__).
GAME = Plinko()
