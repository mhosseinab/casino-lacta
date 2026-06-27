"""``originals.roulette`` — the provably-fair Roulette Original, 0–99 (spec §A.9).

A 100-outcome colour-pick game (NOT a European wheel — that is Part B B.3). One
draw from the seeded stream maps to ``result = floor(f * 100)`` ∈ {0,…,99}; the
result's colour is read from a CONFIGURABLE pocket map (default 1 green / 49 red /
50 black). A player may place MULTIPLE simultaneous colour bets on one spin; each
matching bet pays ``(1 - edge) / p`` where ``p = pockets/100``, so per-bet
``RTP = p · (1-edge)/p = 1 - edge`` for every colour.

The shared bet loop settles a single ``stake`` against a single ``Outcome``
``multiplier``, so the per-bet results are aggregated into the ONE multiplier that
makes ``floor(total_stake · multiplier)`` equal the total payout: the
stake-weighted mean ``Σ(stakeᵢ·mᵢ) / Σ(stakeᵢ)``. The per-bet breakdown is carried
in ``detail.settlements`` for display/audit; minor-unit rounding stays with
``engine.money`` at the loop (no money math here).

Pure: stdlib only; entropy enters solely via the injected ``RngStream``; ``edge``
and the colour map are read from ``cfg`` (never literals). The same
``(server_seed, client_seed, nonce)`` + ``input`` always yields the same result —
server == verifier by construction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, cast

from engine.types import GameConfig, InvalidBetInput, Outcome, RngStream

# One draw → a result in {0,…,99}: ``floor(f * 100)`` (spec §A.9 RNG mapping).
_RESULT_RANGE = 100


def _pockets(cfg: GameConfig) -> dict[str, list[int]]:
    """The configured colour → pocket-list map (authoritative copy is config)."""
    return cast("dict[str, list[int]]", cfg.params["pockets"])


def _colour_of(result: int, pockets: dict[str, list[int]]) -> str:
    """The colour whose configured pocket list contains ``result``.

    Total over the trusted default map (pockets partition {0,…,99}); a malformed
    map is a config error, not a player input the fence guards.
    """
    for colour, results in pockets.items():
        if result in results:
            return colour
    raise KeyError(f"no colour covers result {result}")  # pragma: no cover


def _bets(input: dict[str, Any]) -> list[dict[str, Any]]:
    """The placed bets (spec §A.9 request body ``bets``)."""
    return cast("list[dict[str, Any]]", input["bets"])


@dataclass(frozen=True)
class Roulette99:
    """Conforms to ``engine.types.InstantGame`` (spec §A.9)."""

    id: str = "originals.roulette"

    def validate_input(self, input: dict[str, Any], cfg: GameConfig) -> None:
        """Reject malformed / out-of-band roulette input BEFORE any money moves (pure).

        ``bets`` must be a non-empty list; each bet must carry a ``value`` that is a
        configured colour and a positive integer ``stakeMinor``. This is the
        COMPLETE fence — passing it guarantees :meth:`play` cannot raise on this
        input.
        """
        bets = input.get("bets")
        if not isinstance(bets, list) or not bets:
            raise InvalidBetInput("roulette requires a non-empty 'bets' list")
        pockets = _pockets(cfg)
        for i, bet in enumerate(bets):
            if not isinstance(bet, dict) or "value" not in bet:
                raise InvalidBetInput(f"roulette bet {i} requires a 'value' colour")
            value = bet["value"]
            if value not in pockets:
                raise InvalidBetInput(
                    f"roulette bet {i} colour {value!r} not in {sorted(pockets)}"
                )
            if "stakeMinor" not in bet:
                raise InvalidBetInput(f"roulette bet {i} requires a 'stakeMinor'")
            stake = bet["stakeMinor"]
            # bool is an int subclass — exclude it explicitly; stakes are counts.
            if isinstance(stake, bool) or not isinstance(stake, int) or stake <= 0:
                raise InvalidBetInput(
                    f"roulette bet {i} 'stakeMinor' must be a positive integer, "
                    f"got {stake!r}"
                )

    def play(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> Outcome:
        """Resolve one spin from a single draw — pure function of (input, rng, cfg).

        Consumes exactly ONE ``rng.next()`` → ``result = floor(f·100)``, maps it to
        a colour, settles each placed bet at ``(1-edge)/p`` on a match else ``0``,
        and returns the stake-weighted aggregate ``multiplier`` plus the per-bet
        ``settlements`` breakdown.
        """
        pockets = _pockets(cfg)
        result = math.floor(rng.next() * _RESULT_RANGE)
        colour = _colour_of(result, pockets)

        settlements: list[dict[str, Any]] = []
        weighted_payout = 0.0
        total_stake = 0
        for i, bet in enumerate(_bets(input)):
            value = bet["value"]
            stake = bet["stakeMinor"]
            won = value == colour
            p = len(pockets[value]) / _RESULT_RANGE
            multiplier = (1.0 - cfg.edge) / p if won else 0.0
            weighted_payout += stake * multiplier
            total_stake += stake
            settlements.append(
                {"bet": i, "value": value, "won": won, "multiplier": multiplier}
            )

        aggregate = weighted_payout / total_stake
        return Outcome(
            multiplier=aggregate,
            detail={
                "result": result,
                "colour": colour,
                "settlements": settlements,
            },
        )


# The module-level singleton the registry resolves (see engine.games.__init__).
GAME = Roulette99()
