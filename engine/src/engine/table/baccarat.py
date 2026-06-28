"""``table.baccarat`` — Baccarat (punto banco), spec §B.4.

Baccarat has NO player decisions: every hand is resolved by the FIXED third-card
drawing rules (the canonical punto-banco tableau), so it conforms to
``engine.types.InstantGame`` — one pure ``play`` deals Player and Banker from a
finite shoe, applies the drawing rules, and settles the placed Player/Banker/Tie
bets atomically. A player may place MULTIPLE simultaneous bets on one hand; as in
roulette the per-bet results are aggregated into the ONE stake-weighted
``multiplier`` that makes ``floor(total_stake · multiplier)`` the total payout the
bet loop credits (per-bet breakdown carried in ``detail.settlements``; minor-unit
rounding stays with ``engine.money`` at the loop — no money math here).

Card values: A=1, 2–9 pip, 10/J/Q/K=0; a hand total is the sum mod 10. The shoe is
a finite multi-deck shoe (default 8 decks) — a uniformly random card from the
remaining shoe equals drawing value ``v`` with probability ``count[v]/total``, so
one ``rng.next()`` float draws one card WITHOUT replacement. The deck count,
banker commission (default 5 % → a banker win pays 0.95 net) and tie payout
(default 8:1) are read from ``cfg.params`` (config, never literals); the resulting
house edges (Banker ≈ 1.06 %, Player ≈ 1.24 %, Tie ≈ 14.36 % for the defaults)
are an emergent consequence of those rules, certified by exact enumeration in
``tests/test_baccarat.py``.

Pure: stdlib only; entropy enters solely via the injected ``RngStream``. The same
``(server_seed, client_seed, nonce)`` + ``input`` always yields the same hand —
server == verifier by construction.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from engine.types import GameConfig, InvalidBetInput, Outcome, RngStream

# A standard French deck has 13 ranks; only the baccarat value matters for the
# outcome (suits are cosmetic). Rank indices 0..12 = A,2,3,4,5,6,7,8,9,10,J,Q,K.
_RANKS = 13
_DECK_SIZE = 52
_VALID_BETS = ("PLAYER", "BANKER", "TIE")


def _card_value(rank: int) -> int:
    """Baccarat value of a rank index 0..12: A=1, 2–9 pip, 10/J/Q/K=0."""
    return rank + 1 if rank <= 8 else 0


# --------------------------------------------------------------------------- drawing rules
#
# The fixed punto-banco tableau — a pure function of the two-card totals (and, for
# the banker, the player's third card). No player decisions enter here.


def _is_natural(player_total: int, banker_total: int) -> bool:
    """A natural — either two-card total is 8 or 9 — stops all drawing."""
    return player_total >= 8 or banker_total >= 8


def _player_draws(player_total: int, banker_total: int) -> bool:
    """Player draws a third card on a two-card total 0–5, stands on 6–7 (never on
    a natural)."""
    return not _is_natural(player_total, banker_total) and player_total <= 5


def _banker_draws(banker_total: int, player_third: int | None) -> bool:
    """Whether the banker draws a third card.

    ``player_third`` is the value of the player's third card, or ``None`` when the
    player stood (two-card total 6–7). When the player stood the banker acts like
    the player (draws 0–5, stands 6–7); otherwise the canonical tableau keyed on
    the banker's two-card total and the player's third-card value applies.
    """
    if player_third is None:
        return banker_total <= 5
    if banker_total <= 2:
        return True
    if banker_total == 3:
        return player_third != 8
    if banker_total == 4:
        return 2 <= player_third <= 7
    if banker_total == 5:
        return 4 <= player_third <= 7
    if banker_total == 6:
        return 6 <= player_third <= 7
    return False  # banker_total == 7 stands (8/9 are naturals, handled earlier)


def _winner(player_total: int, banker_total: int) -> str:
    if player_total > banker_total:
        return "PLAYER"
    if banker_total > player_total:
        return "BANKER"
    return "TIE"


# --------------------------------------------------------------------------- resolution


@dataclass(frozen=True)
class Round:
    """The resolved hand — a pure function of the cards dealt in shoe order."""

    winner: str
    player: tuple[int, ...]
    banker: tuple[int, ...]
    player_total: int
    banker_total: int
    cards_used: int


def _resolve(draw: Callable[[], int]) -> Round:
    """Apply the fixed drawing rules, pulling card VALUES from ``draw`` in shoe
    order (P1, B1, P2, B2, then the player's third if any, then the banker's)."""
    p1 = draw()
    b1 = draw()
    p2 = draw()
    b2 = draw()
    player = [p1, p2]
    banker = [b1, b2]
    player_total = (p1 + p2) % 10
    banker_total = (b1 + b2) % 10

    if not _is_natural(player_total, banker_total):
        player_third: int | None = None
        if _player_draws(player_total, banker_total):
            player_third = draw()
            player.append(player_third)
            player_total = (player_total + player_third) % 10
        if _banker_draws(banker_total, player_third):
            banker_third = draw()
            banker.append(banker_third)
            banker_total = (banker_total + banker_third) % 10

    return Round(
        winner=_winner(player_total, banker_total),
        player=tuple(player),
        banker=tuple(banker),
        player_total=player_total,
        banker_total=banker_total,
        cards_used=len(player) + len(banker),
    )


def resolve_from_values(values: Sequence[int]) -> Round:
    """Resolve a hand from an explicit sequence of card VALUES in shoe order — the
    enumeration/parity entry point that shares the EXACT drawing rules ``play``
    uses (no re-implementation)."""
    cursor = 0

    def draw() -> int:
        nonlocal cursor
        value = values[cursor]
        cursor += 1
        return value

    return _resolve(draw)


# --------------------------------------------------------------------------- shoe sampling


def _build_counts(cfg: GameConfig) -> list[int]:
    """Card-value counts of a full shoe: value 0 (10/J/Q/K → 4 ranks) ×4 suits,
    each value 1–9 (one rank) ×4 suits, per deck."""
    decks = int(cfg.params["decks"])
    counts = [0] * 10
    for rank in range(_RANKS):
        counts[_card_value(rank)] += 4 * decks  # 4 suits per rank per deck
    return counts


@dataclass
class _Shoe:
    """A finite shoe sampled WITHOUT replacement: one ``rng.next()`` per card."""

    counts: list[int]
    total: int

    def draw(self, rng: RngStream) -> int:
        """Draw a uniformly random card from the remaining shoe and remove it.

        ``r = floor(f · total)`` selects an index into the flattened remaining
        cards; walking the per-value counts maps it to a value (cards of equal
        value are interchangeable), giving exact no-replacement sampling.
        """
        r = math.floor(rng.next() * self.total)
        for value, count in enumerate(self.counts):
            if r < count:
                self.counts[value] -= 1
                self.total -= 1
                return value
            r -= count
        raise AssertionError("shoe draw index out of range")  # pragma: no cover


# --------------------------------------------------------------------------- settlement


def bet_multiplier(winner: str, value: str, cfg: GameConfig) -> float:
    """The TOTAL-return multiplier of a single ``value`` bet given the ``winner``.

    Player pays 1:1 (→ 2.0), Banker pays 1:1 minus commission (→ 1 + (1 − c)),
    Tie pays ``tiePayout``:1 (→ 1 + payout). A Player/Banker bet PUSHES on a tie
    (→ 1.0); a losing bet returns 0. Commission and tie payout are config.
    """
    commission = float(cfg.params["bankerCommission"])
    tie_payout = float(cfg.params["tiePayout"])
    if value == "PLAYER":
        if winner == "PLAYER":
            return 2.0
        return 1.0 if winner == "TIE" else 0.0
    if value == "BANKER":
        if winner == "BANKER":
            return 1.0 + (1.0 - commission)
        return 1.0 if winner == "TIE" else 0.0
    # value == "TIE" — the only remaining configured bet (validate_input fences it).
    return 1.0 + tie_payout if winner == "TIE" else 0.0


def _placed_bets(input: dict[str, Any]) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", input["bets"])


@dataclass(frozen=True)
class Baccarat:
    """Conforms to ``engine.types.InstantGame`` (spec §B.4)."""

    id: str = field(default="table.baccarat")

    def validate_input(self, input: dict[str, Any], cfg: GameConfig) -> None:
        """Reject malformed / out-of-band baccarat input BEFORE any money moves.

        ``bets`` must be a non-empty list; each bet must carry a ``value`` in
        ``{PLAYER, BANKER, TIE}`` and a positive integer ``stakeMinor``. Passing
        this fence guarantees :meth:`play` cannot raise on this input.
        """
        del cfg  # validation needs no per-game config beyond the fixed bet set
        bets = input.get("bets")
        if not isinstance(bets, list) or not bets:
            raise InvalidBetInput("baccarat requires a non-empty 'bets' list")
        for i, bet in enumerate(bets):
            if not isinstance(bet, dict) or "value" not in bet:
                raise InvalidBetInput(f"baccarat bet {i} requires a 'value'")
            if bet["value"] not in _VALID_BETS:
                raise InvalidBetInput(
                    f"baccarat bet {i} value {bet['value']!r} not in {list(_VALID_BETS)}"
                )
            if "stakeMinor" not in bet:
                raise InvalidBetInput(f"baccarat bet {i} requires a 'stakeMinor'")
            stake = bet["stakeMinor"]
            # bool is an int subclass — exclude it explicitly; stakes are counts.
            if isinstance(stake, bool) or not isinstance(stake, int) or stake <= 0:
                raise InvalidBetInput(
                    f"baccarat bet {i} 'stakeMinor' must be a positive integer, "
                    f"got {stake!r}"
                )

    def play(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> Outcome:
        """Deal one hand from a fresh shoe, apply the drawing rules, settle each
        placed bet, and return the stake-weighted aggregate ``multiplier`` plus the
        per-bet ``settlements`` breakdown — pure function of (input, rng, cfg)."""
        counts = _build_counts(cfg)
        shoe = _Shoe(counts=counts, total=sum(counts))
        rnd = _resolve(lambda: shoe.draw(rng))

        settlements: list[dict[str, Any]] = []
        weighted_payout = 0.0
        total_stake = 0
        for i, bet in enumerate(_placed_bets(input)):
            value = bet["value"]
            stake = bet["stakeMinor"]
            multiplier = bet_multiplier(rnd.winner, value, cfg)
            weighted_payout += stake * multiplier
            total_stake += stake
            settlements.append(
                {"bet": i, "value": value, "multiplier": multiplier}
            )

        aggregate = weighted_payout / total_stake
        return Outcome(
            multiplier=aggregate,
            detail={
                "winner": rnd.winner,
                "player": list(rnd.player),
                "banker": list(rnd.banker),
                "playerTotal": rnd.player_total,
                "bankerTotal": rnd.banker_total,
                "cardsUsed": rnd.cards_used,
                "settlements": settlements,
            },
        )


# The module-level singleton the registry resolves (see engine.table.__init__).
GAME = Baccarat()
