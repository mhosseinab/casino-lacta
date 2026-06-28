"""``table.video_poker`` — 5-card draw Video Poker (spec §B.5), a ``StatefulGame``.

9/6 Jacks-or-Better by default, but fully **paytable-driven** (the schedule + the
minimum paying pair are config, never literals in the outcome path — RTP emerges from
the paytable + the player's strategy, never a clamp).

Flow (two phases):

* ``init`` commits TEN distinct cards from the seeded stream in one
  ``sample_without_replacement(rng, range(52), 10)`` draw — ``[0:5]`` is the dealt hand,
  ``[5:10]`` are the replacement cards drawn IN ORDER as discards are filled. Because the
  ten are distinct, redraws come from the 47 unseen cards (discards are NOT returned to
  the deck — the classic video-poker bug, avoided by construction). The cursor advances
  by exactly 10, so the round is a pure function of ``(serverSeed, clientSeed, nonce)``
  and the verifier reproduces it bit-for-bit.
* ``step`` takes the single ``draw`` action carrying ``holds`` (the positions ``0..4`` to
  keep); the discarded positions are filled from the committed pool in order, the final
  5 are ranked by the shared ``engine.cards.evaluator``, and the paytable maps the hand
  to a settlement multiplier. One step resolves the round.

**Status mapping (the generic stateful-game settlement signal).** A paying hand →
``CASHED_OUT`` (the app credits ``Outcome.multiplier``); a non-paying hand → ``LOST``.
The app drives credit/round-status from ``detail["status"]`` + ``Outcome.multiplier``
generically, knowing nothing of poker hands.

**Redaction.** ``public_view`` exposes the dealt 5 (the player holds against them) but
HIDES the committed replacement cards while ACTIVE — leaking the upcoming draws would
break fairness (the seed is secret until reveal). At a terminal status it discloses the
final hand. The randomness is fully committed at ``init`` (like Mines), so ``step``
ignores its injected ``rng`` and the cursor does not advance further.

Pure: stdlib only; entropy enters solely via the injected ``RngStream``; the evaluator
is the single shared ranking core (no JoB rule baked into it — the minimum paying pair
lives here, in the paytable layer).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from engine.cards.evaluator import Card, HandCategory, HandRank, rank_five
from engine.sampling import sample_without_replacement
from engine.types import GameConfig, InvalidBetInput, Outcome, RngStream

_DECK = 52
_HAND = 5
_COMMIT = 10  # dealt 5 + up to 5 committed replacements (covers discarding all five)

# Round status literals — the generic settlement signal in Outcome.detail["status"].
_ACTIVE = "ACTIVE"
_CASHED_OUT = "CASHED_OUT"
_LOST = "LOST"

# Paytable keys (a royal flush is the ace-high straight flush — paid specially here,
# never as an evaluator category, keeping the evaluator game-agnostic).
ROYAL_FLUSH = "ROYAL_FLUSH"
STRAIGHT_FLUSH = "STRAIGHT_FLUSH"
FOUR_OF_A_KIND = "FOUR_OF_A_KIND"
FULL_HOUSE = "FULL_HOUSE"
FLUSH = "FLUSH"
STRAIGHT = "STRAIGHT"
THREE_OF_A_KIND = "THREE_OF_A_KIND"
TWO_PAIR = "TWO_PAIR"
JACKS_OR_BETTER = "JACKS_OR_BETTER"

# The canonical full-pay 9/6 Jacks-or-Better schedule (multiplier per unit bet,
# max-coin-equivalent royal = 800 → ≈99.54% RTP under optimal play). Tune the
# schedule — never clamp outcomes — to change RTP.
DEFAULT_PAYTABLE: dict[str, float] = {
    ROYAL_FLUSH: 800.0,
    STRAIGHT_FLUSH: 50.0,
    FOUR_OF_A_KIND: 25.0,
    FULL_HOUSE: 9.0,
    FLUSH: 6.0,
    STRAIGHT: 4.0,
    THREE_OF_A_KIND: 3.0,
    TWO_PAIR: 2.0,
    JACKS_OR_BETTER: 1.0,
}
DEFAULT_MIN_PAIR_RANK = 11  # Jacks (J=11): the lowest pair that pays in JoB.

# The non-pair categories map straight to a paytable key; the pair/royal cases need the
# tiebreak (min paying pair / ace-high) so they are handled explicitly in `_paytable_key`.
_CATEGORY_KEY: dict[HandCategory, str] = {
    HandCategory.FOUR_OF_A_KIND: FOUR_OF_A_KIND,
    HandCategory.FULL_HOUSE: FULL_HOUSE,
    HandCategory.FLUSH: FLUSH,
    HandCategory.STRAIGHT: STRAIGHT,
    HandCategory.THREE_OF_A_KIND: THREE_OF_A_KIND,
    HandCategory.TWO_PAIR: TWO_PAIR,
}


def default_params() -> dict[str, Any]:
    """The engine default ``GameConfig.params`` (9/6 JoB). DB config is authoritative at runtime."""
    return {"paytable": dict(DEFAULT_PAYTABLE), "minPairRank": DEFAULT_MIN_PAIR_RANK}


def _paytable_key(hand_rank: HandRank, min_pair_rank: int) -> str | None:
    """Map a ranked hand to its paytable key (``None`` = non-paying).

    The only game-specific rules in the whole module: a royal flush is the ace-high
    straight flush, and a one-pair hand pays only at/above ``min_pair_rank`` (Jacks by
    default). Everything else is a direct category lookup. Keeping this here — not in the
    evaluator — is what lets S29 poker reuse the evaluator unchanged.
    """
    category = hand_rank.category
    if category is HandCategory.STRAIGHT_FLUSH:
        return ROYAL_FLUSH if hand_rank.tiebreak[0] == 14 else STRAIGHT_FLUSH
    if category is HandCategory.ONE_PAIR:
        return JACKS_OR_BETTER if hand_rank.tiebreak[0] >= min_pair_rank else None
    return _CATEGORY_KEY.get(category)


def score(
    cards: Sequence[Card], paytable: Mapping[str, float], min_pair_rank: int
) -> tuple[str | None, float]:
    """Rank ``cards`` and return ``(paytable_key, multiplier)`` — the pure paytable layer.

    ``multiplier`` is ``0.0`` for a non-paying hand or a paytable missing the key. This
    is the single place the paytable is consulted (used by both ``step`` and
    ``public_view``); RTP is whatever this schedule + the player's holds produce.
    """
    key = _paytable_key(rank_five(cards), min_pair_rank)
    if key is None:
        return None, 0.0
    return key, float(paytable.get(key, 0.0))


def _config(cfg: GameConfig) -> tuple[dict[str, float], int]:
    """Read the paytable + min paying pair from config, falling back to the 9/6 default.

    Coerces JSON-round-tripped values (``GameConfig.params`` survives the DB) into a
    ``dict[str, float]`` so a DB-sourced paytable behaves identically to the literal.
    """
    raw = cfg.params.get("paytable", DEFAULT_PAYTABLE)
    paytable = {str(k): float(v) for k, v in raw.items()}
    min_pair_rank = int(cfg.params.get("minPairRank", DEFAULT_MIN_PAIR_RANK))
    return paytable, min_pair_rank


@dataclass(frozen=True)
class VideoPoker:
    """Conforms to ``engine.types.StatefulGame`` (spec §B.5)."""

    id: str = "table.video_poker"

    def validate_input(self, input: dict[str, Any], cfg: GameConfig) -> None:
        """No game-specific round-open input to fence — the only client input is the
        per-step ``holds`` (validated in :meth:`step`); stake/currency are fenced by the
        shared bet loop. Explicit no-op so a new game cannot inherit a missing fence."""

    def init(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> dict[str, Any]:
        """Commit 10 distinct cards from the stream (cursor → 10); return opaque state.

        ``[0:5]`` is dealt, ``[5:10]`` are the hidden replacements. The paytable +
        min-pair are snapshotted so :meth:`step` settles from the state alone (the
        round's config is pinned at open)."""
        committed = sample_without_replacement(rng, range(_DECK), _COMMIT)
        paytable, min_pair_rank = _config(cfg)
        return {
            "status": _ACTIVE,
            "dealt": committed[:_HAND],
            "pool": committed[_HAND:],  # committed replacements — HIDDEN until terminal
            "paytable": paytable,
            "min_pair_rank": min_pair_rank,
        }

    def step(
        self, state: dict[str, Any], action: dict[str, Any], rng: RngStream
    ) -> tuple[dict[str, Any], Outcome | None]:
        """Resolve the round with the single ``draw`` action; return ``(next_state, Outcome)``.

        ``rng`` is part of the seam (symmetric to :meth:`init`) but is IGNORED — the whole
        deck is committed at ``init`` (like Mines), so the draw consumes the pre-committed
        pool and the cursor does not advance."""
        del rng  # randomness fully committed at init; the draw uses the committed pool.
        if state["status"] != _ACTIVE:
            raise InvalidBetInput(f"round is terminal ({state['status']}); no further actions")
        op = action.get("op")
        if op != "draw":
            raise InvalidBetInput(f"unknown video_poker op {op!r}")

        holds = _validate_holds(action.get("holds"))
        dealt: list[int] = list(state["dealt"])
        pool: list[int] = list(state["pool"])
        final = list(dealt)
        for fill_index, position in enumerate(p for p in range(_HAND) if p not in holds):
            final[position] = pool[fill_index]

        cards = [Card.from_index(i) for i in final]
        key, multiplier = score(cards, state["paytable"], int(state["min_pair_rank"]))
        status = _CASHED_OUT if multiplier > 0.0 else _LOST
        next_state = {**state, "status": status, "final": final}
        return next_state, Outcome(
            multiplier=multiplier,
            detail={
                "status": status,
                "holds": sorted(holds),
                "dealtCards": dealt,
                "finalCards": final,
                "handCategory": rank_five(cards).category.name,
                "handKey": key,
                "multiplier": multiplier,
            },
        )

    def public_view(self, state: dict[str, Any]) -> dict[str, Any]:
        """The client-safe snapshot (for ``/bet`` open + ``/state`` resume).

        Surfaces the dealt 5 (public — the player holds against them) but NEVER the
        committed replacement pool while ACTIVE (the secret upcoming draws). At a terminal
        status it discloses the final hand + its paytable result (provable fairness)."""
        status = str(state["status"])
        view: dict[str, Any] = {"status": status, "dealtCards": list(state["dealt"])}
        if status == _ACTIVE:
            return view
        final = list(state["final"])
        cards = [Card.from_index(i) for i in final]
        key, multiplier = score(cards, state["paytable"], int(state["min_pair_rank"]))
        view["finalCards"] = final
        view["handCategory"] = rank_five(cards).category.name
        view["handKey"] = key
        view["multiplier"] = multiplier
        return view


def _validate_holds(holds: Any) -> list[int]:
    """Fence the per-step ``holds``: a list of DISTINCT positions in ``0..4``.

    ``bool`` is rejected (it subclasses ``int``, so ``True`` must not pass as ``1``).
    Returns the validated positions; raises :class:`InvalidBetInput` otherwise.
    """
    if not isinstance(holds, list):
        raise InvalidBetInput(f"video_poker 'holds' must be a list, got {holds!r}")
    for position in holds:
        if not isinstance(position, int) or isinstance(position, bool):
            raise InvalidBetInput(f"video_poker hold position must be an int, got {position!r}")
        if not 0 <= position < _HAND:
            raise InvalidBetInput(f"video_poker hold position {position} out of range [0, 4]")
    if len(set(holds)) != len(holds):
        raise InvalidBetInput(f"video_poker 'holds' has duplicate positions: {holds!r}")
    return list(holds)


# The module-level singleton the registry resolves (see engine.table.__init__).
GAME = VideoPoker()
