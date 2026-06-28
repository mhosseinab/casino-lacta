"""``table.roulette`` — European Roulette, single-zero wheel (spec §B.3).

37 pockets (0–36). One draw → ``pocket = floor(f·37)`` ∈ {0,…,36}. Every standard
inside/outside bet settles via a PAYOUT TABLE carried in ``GameConfig.params``
(config, never literals here): straight 35:1, split 17:1, street 11:1, corner 8:1,
line 5:1, column/dozen 2:1, even-money (red/black, odd/even, high/low) 1:1.

**RTP = 36/37 by construction, not a clamp.** A win returns the *gross* multiplier
``payout + 1`` (stake back + winnings); a loss returns ``0``. Because every payout
is the fair ``X:1`` and the true probability of a cover of ``count`` pockets is
``count/37``, each bet's per-bet RTP is

    (count/37) · (payout + 1) = 36/37 = 0.9730…

so the single pocket 0 IS the whole 1/37 ≈ 2.70% house edge — every even-money /
column / dozen cover excludes 0, and only an inside bet that NAMES 0 wins on it.
``cfg.edge`` (= 1/37) is documentation; the multiplier comes from the payout table.

Bet input shapes (one spin carries multiple bets — the shared bet loop settles a
single stake-weighted ``Outcome``, exactly like ``originals.roulette``):

* inside  — ``{"type": "STRAIGHT"|"SPLIT"|"STREET"|"CORNER"|"LINE",
              "numbers": [...], "stakeMinor": n}``  (``numbers`` lists the covered
              pockets; its length MUST equal the type's selection size).
* outside — ``{"type": "RED"|"BLACK"|"ODD"|"EVEN"|"HIGH"|"LOW", "stakeMinor": n}``.
* indexed — ``{"type": "COLUMN"|"DOZEN", "index": 1|2|3, "stakeMinor": n}``.

The engine validates bet TYPE, selection COUNT, pocket RANGE/uniqueness, and the
column/dozen index. It does NOT enforce board ADJACENCY (that a "split" names two
touching pockets): RTP depends only on the cover's *count*, so a non-adjacent
selection still returns 36/37 — adjacency is a client/UX concern, kept out of the
engine to avoid hard-coding the wheel's geometry. The selection size IS the
load-bearing fence: a 5-number "line" would silently pay 30/37.

Pure: stdlib only; entropy enters solely via the injected ``RngStream``; the payout
table and covers are read from ``cfg`` (never literals). Same
``(server_seed, client_seed, nonce)`` + ``input`` → same pocket — server ==
verifier by construction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, cast

from engine.types import GameConfig, InvalidBetInput, Outcome, RngStream

_POCKET_COUNT = 37  # single zero: pockets 0–36

# Inside bet types → the exact number of pockets their ``numbers`` selection covers.
# This count is what makes RTP = (count/37)·(payout+1) = 36/37 exact.
_SELECTION_SIZE = {"STRAIGHT": 1, "SPLIT": 2, "STREET": 3, "CORNER": 4, "LINE": 6}


def default_params() -> dict[str, Any]:
    """The default European-wheel config (board + payout table) — authoritative copy
    is the DB ``GameConfig`` at runtime; this seeds it (registry default).

    ``payouts`` are the fair ``X:1`` ratios; ``covers`` lists the pockets each named
    outside bet wins on; ``indexed_covers`` the 1-based column/dozen trios. All board
    data lives here (data, not code) so ``play``/``validate_input`` are table lookups.
    """
    return {
        "payouts": {
            "STRAIGHT": 35,
            "SPLIT": 17,
            "STREET": 11,
            "CORNER": 8,
            "LINE": 5,
            "COLUMN": 2,
            "DOZEN": 2,
            "RED": 1,
            "BLACK": 1,
            "ODD": 1,
            "EVEN": 1,
            "HIGH": 1,
            "LOW": 1,
        },
        # Named outside bets → the pockets they cover (standard European wheel).
        "covers": {
            "RED": [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36],
            "BLACK": [2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35],
            "ODD": list(range(1, 37, 2)),
            "EVEN": list(range(2, 37, 2)),
            "LOW": list(range(1, 19)),
            "HIGH": list(range(19, 37)),
        },
        # 1-based index → covered trio (columns down the felt, dozens 1-12/13-24/25-36).
        "indexed_covers": {
            "COLUMN": [list(range(1, 37, 3)), list(range(2, 37, 3)), list(range(3, 37, 3))],
            "DOZEN": [list(range(1, 13)), list(range(13, 25)), list(range(25, 37))],
        },
    }


def _payouts(cfg: GameConfig) -> dict[str, int]:
    return cast("dict[str, int]", cfg.params["payouts"])


def _covers(cfg: GameConfig) -> dict[str, list[int]]:
    return cast("dict[str, list[int]]", cfg.params["covers"])


def _indexed_covers(cfg: GameConfig) -> dict[str, list[list[int]]]:
    return cast("dict[str, list[list[int]]]", cfg.params["indexed_covers"])


def _bets(input: dict[str, Any]) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", input["bets"])


def _is_positive_int(value: Any) -> bool:
    # bool is an int subclass — exclude it explicitly; stakes/indices are counts.
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _covered_pockets(bet: dict[str, Any], cfg: GameConfig) -> set[int]:
    """The set of pockets ``bet`` wins on (assumes ``bet`` already passed the fence)."""
    kind = bet["type"]
    if kind in _SELECTION_SIZE:
        return set(bet["numbers"])
    if kind in _indexed_covers(cfg):
        return set(_indexed_covers(cfg)[kind][bet["index"] - 1])
    return set(_covers(cfg)[kind])


@dataclass(frozen=True)
class RouletteWheel:
    """European single-zero wheel. Conforms to ``engine.types.InstantGame`` (§B.3)."""

    id: str = "table.roulette"

    def validate_input(self, input: dict[str, Any], cfg: GameConfig) -> None:
        """Reject malformed / out-of-band roulette input BEFORE any money moves (pure).

        ``bets`` must be a non-empty list; each bet carries a known ``type`` and a
        positive-integer ``stakeMinor``. Inside types require a ``numbers`` list of
        unique pockets in 0–36 whose length equals the type's selection size;
        column/dozen require an ``index`` in 1–3. Passing this fence guarantees
        :meth:`play` cannot raise on this input.
        """
        bets = input.get("bets")
        if not isinstance(bets, list) or not bets:
            raise InvalidBetInput("roulette requires a non-empty 'bets' list")
        payouts = _payouts(cfg)
        for i, bet in enumerate(bets):
            if not isinstance(bet, dict) or "type" not in bet:
                raise InvalidBetInput(f"roulette bet {i} requires a 'type'")
            kind = bet["type"]
            if kind not in payouts:
                raise InvalidBetInput(
                    f"roulette bet {i} type {kind!r} not in {sorted(payouts)}"
                )
            if "stakeMinor" not in bet:
                raise InvalidBetInput(f"roulette bet {i} requires a 'stakeMinor'")
            if not _is_positive_int(bet["stakeMinor"]):
                raise InvalidBetInput(
                    f"roulette bet {i} 'stakeMinor' must be a positive integer, "
                    f"got {bet['stakeMinor']!r}"
                )
            if kind in _SELECTION_SIZE:
                self._validate_inside(i, kind, bet)
            elif kind in _indexed_covers(cfg):
                self._validate_indexed(i, kind, bet, cfg)

    def _validate_inside(self, i: int, kind: str, bet: dict[str, Any]) -> None:
        numbers = bet.get("numbers")
        size = _SELECTION_SIZE[kind]
        if not isinstance(numbers, list) or len(numbers) != size:
            raise InvalidBetInput(
                f"roulette bet {i} {kind} requires a 'numbers' list of {size} pockets"
            )
        for n in numbers:
            if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n < _POCKET_COUNT:
                raise InvalidBetInput(
                    f"roulette bet {i} {kind} pocket {n!r} not in 0..{_POCKET_COUNT - 1}"
                )
        if len(set(numbers)) != size:
            raise InvalidBetInput(f"roulette bet {i} {kind} 'numbers' must be unique")

    def _validate_indexed(
        self, i: int, kind: str, bet: dict[str, Any], cfg: GameConfig
    ) -> None:
        index = bet.get("index")
        count = len(_indexed_covers(cfg)[kind])
        if not isinstance(index, int) or isinstance(index, bool) or not 1 <= index <= count:
            raise InvalidBetInput(
                f"roulette bet {i} {kind} requires an 'index' in 1..{count}"
            )

    def play(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> Outcome:
        """Resolve one spin from a single draw — pure function of (input, rng, cfg).

        Consumes exactly ONE ``rng.next()`` → ``pocket = floor(f·37)``, settles each
        placed bet at the gross multiplier ``payout + 1`` on a covered pocket else
        ``0``, and returns the stake-weighted aggregate ``multiplier`` plus the
        per-bet ``settlements`` breakdown.
        """
        payouts = _payouts(cfg)
        pocket = math.floor(rng.next() * _POCKET_COUNT)

        settlements: list[dict[str, Any]] = []
        weighted_payout = 0.0
        total_stake = 0
        for i, bet in enumerate(_bets(input)):
            kind = bet["type"]
            stake = bet["stakeMinor"]
            won = pocket in _covered_pockets(bet, cfg)
            multiplier = float(payouts[kind] + 1) if won else 0.0
            weighted_payout += stake * multiplier
            total_stake += stake
            settlements.append(
                {"bet": i, "type": kind, "won": won, "multiplier": multiplier}
            )

        aggregate = weighted_payout / total_stake
        return Outcome(
            multiplier=aggregate,
            detail={"pocket": pocket, "settlements": settlements},
        )


# The module-level singleton the registry resolves (see engine.table package).
GAME = RouletteWheel()
