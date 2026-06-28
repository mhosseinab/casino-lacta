"""``table.blackjack`` — server-authoritative blackjack with configurable rules.

A ``StatefulGame`` (build-plan §5, spec table games): the player opens a round, plays
hit / stand / double / split / insurance / (optional) surrender, the dealer plays out,
and the round settles. Every card is dealt by the SERVER from a D-deck shoe — the
client only sends intent.

**Shoe + RNG mapping (without replacement, committed at round start).** The shoe
COMPOSITION is fixed at :meth:`init` — ``decks`` decks, so ``4·decks`` of each rank
1..13 (rank 1 = ace; 10/J/Q/K all value 10 → ``16·decks`` ten-value cards). Cards are
realised PROGRESSIVELY from the injected stream: each draw consumes ONE float,
``x = floor(f · cardsLeft)``, indexes the remaining cards by walking the rank counts,
and DECREMENTS that rank (true draw without replacement — card-removal matters, unlike
an infinite-deck approximation). The opening deal (player, dealer-up, player,
dealer-hole) draws 4 at ``init`` (cursor → 4); every hit / double / split-card /
dealer-draw consumes the next draws at :meth:`step` from the SAME counter-indexed
stream (HiLo's progressive-draw pattern). Stand / insurance / surrender / peek draw
NOTHING. The whole deal is therefore a pure function of
``(serverSeed, clientSeed, nonce, cursor)`` and the verifier reproduces it by replaying
the stream through the same actions. Suit is irrelevant to blackjack value/payout, so
the shoe is modelled by rank counts (compact, exact).

> The ``penetration`` / reshuffle threshold is recorded in the rules but a fresh shoe
> is committed PER round (each round has its own seed/nonce), so within a round it
> never binds; cross-round shoe persistence + reshuffle is a documented app/seam, not
> built here (KISS / the per-round StatefulGame model).

**Money is a multiplier on the BASE stake.** The engine returns floats; minor-units
rounding/caps live in ``app/money.py``, never here. ``Outcome.multiplier`` is the TOTAL
amount returned to the player per unit of the base stake (loss 0, push 1.0, even-money
win 2.0, blackjack 3:2 → 2.5, a winning double → 4.0, …). Extra wagers from
double/split/insurance are reflected in the multiplier relative to the base stake and
in ``detail["totalWagered"]``; the app debits those extras and credits the multiplier
(a documented seam — S25 is engine-pure). Because the house edge is conventionally
quoted per INITIAL bet, the EV gate measures ``net = multiplier − totalWagered`` and
targets ``−edge`` (see ``tests/test_blackjack_ev``).

**Redaction.** :meth:`public_view` surfaces the dealer UP-card while the round is live
and withholds the HOLE card (and the undrawn shoe); at a terminal status it discloses
the full dealer hand (provable fairness). The player's own cards are never secret.

**Configurable rules** (``GameConfig.params``, merged over :data:`DEFAULT_PARAMS`):
deck count, dealer hit/stand soft-17, blackjack payout, DAS, double restriction, max
split hands, resplit-aces, surrender, dealer peek, insurance, penetration. ``edge`` is
EMERGENT (rule-fixed payouts) — it is documentation of the published target and is
NEVER used to price a payout.

Pure: stdlib only (``math.floor``); entropy enters solely via the injected
``RngStream``. Same ``(server, client, nonce)`` → same deal, always.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any

from engine.types import GameConfig, InvalidBetInput, Outcome, RngStream

_RANKS = 13
_ACE = 1
_BLACKJACK = 21
_DEALER_STAND = 17

# Round status literals — the generic stateful-game settlement signal carried in
# Outcome.detail["status"] (the app reads this, never the opaque state internals).
_ACTIVE = "ACTIVE"
_WON = "WON"
_LOST = "LOST"
_PUSH = "PUSH"
_TERMINAL = frozenset({_WON, _LOST, _PUSH})

# Player decision phases (meaningful only while status == ACTIVE).
_INSURANCE = "INSURANCE"
_PLAYER = "PLAYER"

# Default rule set — a standard, published 6-deck game (see tests/test_blackjack_ev).
DEFAULT_PARAMS: dict[str, Any] = {
    "decks": 6,
    "hit_soft_17": False,  # dealer STANDS on soft 17 (S17)
    "blackjack_payout": 1.5,  # 3:2
    "das": True,  # double after split allowed
    "double_any_two": True,  # double on any two cards (else only hard 9–11)
    "max_split_hands": 4,
    "resplit_aces": False,  # split aces get one card each, no resplit
    "surrender": False,  # late surrender off by default
    "peek": True,  # dealer peeks for blackjack (US/peek rules)
    "insurance": True,
    "penetration": 0.75,  # documented reshuffle threshold (cross-round seam; see module doc)
}


# --- pure card helpers -------------------------------------------------------


def card_value(rank: int) -> int:
    """Blackjack value of a rank: ace → 11, 10/J/Q/K → 10, else the pip value."""
    return 11 if rank == _ACE else min(rank, 10)


def hand_value(ranks: list[int]) -> tuple[int, bool]:
    """Return ``(total, is_soft)``: aces count 11 then drop to 1 while busting.

    ``is_soft`` is True when an ace is still counted as 11 in the final total.
    """
    total = 0
    aces = 0
    for r in ranks:
        if r == _ACE:
            aces += 1
            total += 11
        else:
            total += min(r, 10)
    while total > _BLACKJACK and aces > 0:
        total -= 10
        aces -= 1
    return total, aces > 0


def is_natural(ranks: list[int]) -> bool:
    """A natural / blackjack: exactly two cards totalling 21 (21 in 3+ cards is NOT)."""
    return len(ranks) == 2 and hand_value(ranks)[0] == _BLACKJACK


def draw_card(shoe: list[int], rng: RngStream) -> tuple[list[int], int]:
    """Draw one card without replacement from ``shoe`` (rank counts), consuming ONE draw.

    Returns ``(new_shoe, rank)`` where ``rank`` ∈ 1..13. Uniform over every remaining
    physical card: ``x = floor(f · cardsLeft)`` indexes by walking the cumulative counts.
    """
    cards_left = sum(shoe)
    x = floor(rng.next() * cards_left)
    cumulative = 0
    for rank_idx in range(_RANKS):
        cumulative += shoe[rank_idx]
        if x < cumulative:
            new = list(shoe)
            new[rank_idx] -= 1
            return new, rank_idx + 1
    raise ValueError("draw from an empty shoe")  # unreachable: x < cards_left


def legal_actions(state: dict[str, Any]) -> list[str]:
    """The actions legal at the round's CURRENT decision point (one owner, used by
    :meth:`Blackjack.public_view`, the engine's own guards, and the test strategy)."""
    if state["status"] != _ACTIVE:
        return []
    if state["phase"] == _INSURANCE:
        return ["insurance"]
    rules = state["rules"]
    hands = state["hands"]
    hand = hands[state["active"]]
    cards = hand["cards"]
    acts = ["hit", "stand"]
    if len(cards) == 2 and not hand["is_split_ace"]:
        if (not hand["is_split"]) or rules["das"]:
            if rules["double_any_two"] or hand_value(cards)[0] in (9, 10, 11):
                acts.append("double")
        if card_value(cards[0]) == card_value(cards[1]) and len(hands) < rules["max_split_hands"]:
            is_ace_pair = card_value(cards[0]) == 11
            if not (is_ace_pair and hand["is_split_ace"] and not rules["resplit_aces"]):
                acts.append("split")
        if rules["surrender"] and not hand["is_split"]:
            acts.append("surrender")
    return acts


def _new_hand(
    bet: float, cards: list[int], *, is_split: bool = False, is_split_ace: bool = False
) -> dict[str, Any]:
    return {
        "cards": list(cards),
        "bet": bet,
        "done": False,
        "doubled": False,
        "is_split": is_split,
        "is_split_ace": is_split_ace,
        "surrendered": False,
    }


@dataclass(frozen=True)
class Blackjack:
    """Conforms to ``engine.types.StatefulGame``."""

    id: str = "table.blackjack"

    # -- protocol: validate / init / public_view / step -----------------------

    def validate_input(self, input: dict[str, Any], cfg: GameConfig) -> None:
        """Blackjack has no game-specific round-open input (stake/currency are fenced by
        the shared bet loop; rules come from ``cfg``). Explicit no-op so a new game cannot
        silently inherit a missing fence (mirrors HiLo)."""

    def init(self, input: dict[str, Any], rng: RngStream, cfg: GameConfig) -> dict[str, Any]:
        """Commit a fresh D-deck shoe and deal the opening hand (4 draws); return opaque state."""
        rules = {**DEFAULT_PARAMS, **cfg.params}
        shoe = [4 * int(rules["decks"])] * _RANKS
        shoe, p1 = draw_card(shoe, rng)
        shoe, dealer_up = draw_card(shoe, rng)
        shoe, p2 = draw_card(shoe, rng)
        shoe, dealer_hole = draw_card(shoe, rng)
        dealer_up_is_ace = card_value(dealer_up) == 11
        phase = _INSURANCE if (rules["insurance"] and dealer_up_is_ace) else _PLAYER
        return {
            "status": _ACTIVE,
            "phase": phase,
            "peeked": False,
            "shoe": shoe,
            "dealer": [dealer_up, dealer_hole],
            "hands": [_new_hand(1.0, [p1, p2])],
            "active": 0,
            "insurance_bet": 0.0,
            "total_wagered": 1.0,
            "rules": rules,
        }

    def public_view(self, state: dict[str, Any]) -> dict[str, Any]:
        """Client-safe snapshot. While ACTIVE: dealer UP-card only (hole + shoe withheld);
        at a terminal status: the full dealer hand + per-hand results disclosed."""
        status = state["status"]
        if status == _ACTIVE:
            return {
                "status": _ACTIVE,
                "phase": state["phase"],
                "dealerUp": state["dealer"][0],
                "dealerUpValue": card_value(state["dealer"][0]),
                "hands": [self._hand_view(h) for h in state["hands"]],
                "active": state["active"],
                "insuranceBet": state["insurance_bet"],
                "totalWagered": state["total_wagered"],
                "legalActions": legal_actions(state),
            }
        dealer_total, _ = hand_value(state["dealer"])
        return {
            "status": status,
            "dealer": list(state["dealer"]),
            "dealerValue": dealer_total,
            "hands": state.get("hands_detail", [self._hand_view(h) for h in state["hands"]]),
            "insurance": state.get(
                "insurance_detail", {"bet": state["insurance_bet"], "return": 0.0}
            ),
            "totalWagered": state["total_wagered"],
            "totalReturned": state.get("total_returned", 0.0),
            "multiplier": state.get("total_returned", 0.0),
        }

    def step(
        self, state: dict[str, Any], action: dict[str, Any], rng: RngStream
    ) -> tuple[dict[str, Any], Outcome | None]:
        """Advance the round by one action; ``rng`` supplies fresh cards (symmetric to init)."""
        if state["status"] != _ACTIVE:
            raise InvalidBetInput(f"round is terminal ({state['status']}); no further actions")
        op = action.get("op")
        if state["phase"] == _INSURANCE:
            if op != "insurance":
                raise InvalidBetInput("insurance must be resolved before any other action")
            return self._resolve_insurance(state, action)
        if not state["peeked"]:
            settled = self._peek(state)
            if settled is not None:
                return settled
            state = {**state, "peeked": True}
        if op == "hit":
            return self._hit(state, rng)
        if op == "stand":
            return self._advance(self._mark_done(state, state["active"]), rng)
        if op == "double":
            return self._double(state, rng)
        if op == "split":
            return self._split(state, rng)
        if op == "surrender":
            return self._surrender(state, rng)
        if op == "insurance":
            raise InvalidBetInput("insurance is not available now")
        raise InvalidBetInput(f"unknown blackjack op {op!r}")

    # -- opening resolution (insurance / peek / naturals) ---------------------

    def _resolve_insurance(
        self, state: dict[str, Any], action: dict[str, Any]
    ) -> tuple[dict[str, Any], Outcome]:
        take = bool(action.get("take", False))
        ins_bet = 0.5 * state["hands"][0]["bet"] if take else 0.0
        state = {
            **state,
            "insurance_bet": ins_bet,
            "total_wagered": state["total_wagered"] + ins_bet,
            "peeked": True,
        }
        if is_natural(state["dealer"]) or is_natural(state["hands"][0]["cards"]):
            return self._settle_naturals(state)
        return {**state, "phase": _PLAYER}, self._active_outcome({**state, "phase": _PLAYER})

    def _peek(self, state: dict[str, Any]) -> tuple[dict[str, Any], Outcome] | None:
        """Resolve naturals before player play. With peek (default) a dealer blackjack ends
        the round immediately; a player natural is paid immediately. Returns the terminal
        ``(state, Outcome)`` if settled, else ``None`` (play continues)."""
        dealer_bj = is_natural(state["dealer"])
        player_bj = is_natural(state["hands"][0]["cards"]) and len(state["hands"]) == 1
        if state["rules"]["peek"]:
            if dealer_bj or player_bj:
                return self._settle_naturals(state)
            return None
        # No-peek (ENHC seam): a player natural still pays now; dealer BJ settles at the end.
        if player_bj and not dealer_bj:
            return self._settle_naturals(state)
        return None

    def _settle_naturals(self, state: dict[str, Any]) -> tuple[dict[str, Any], Outcome]:
        dealer_bj = is_natural(state["dealer"])
        hand = state["hands"][0]
        player_bj = is_natural(hand["cards"])
        ins_paid = state["insurance_bet"] > 0 and dealer_bj
        ins_ret = state["insurance_bet"] * 3.0 if ins_paid else 0.0
        if player_bj and dealer_bj:
            result, ret = _PUSH, hand["bet"] * 1.0
        elif player_bj:
            result, ret = "BLACKJACK", hand["bet"] * (1.0 + state["rules"]["blackjack_payout"])
        else:  # dealer blackjack only
            result, ret = "LOSE", 0.0
        return self._terminal(
            state, [self._hand_detail(hand, result, ret)], ins_ret, dealer_played=False
        )

    # -- player actions -------------------------------------------------------

    def _hit(self, state: dict[str, Any], rng: RngStream) -> tuple[dict[str, Any], Outcome]:
        idx = state["active"]
        state, _ = self._deal_to(state, idx, rng)
        if hand_value(state["hands"][idx]["cards"])[0] > _BLACKJACK:  # bust
            return self._advance(self._mark_done(state, idx), rng)
        return state, self._active_outcome(state)

    def _double(self, state: dict[str, Any], rng: RngStream) -> tuple[dict[str, Any], Outcome]:
        if "double" not in legal_actions(state):
            raise InvalidBetInput("double is not allowed for this hand")
        idx = state["active"]
        hand = state["hands"][idx]
        hands = [dict(h) for h in state["hands"]]
        hands[idx] = {**hand, "bet": hand["bet"] * 2.0, "doubled": True}
        state = {**state, "hands": hands, "total_wagered": state["total_wagered"] + hand["bet"]}
        state, _ = self._deal_to(state, idx, rng)
        return self._advance(self._mark_done(state, idx), rng)

    def _split(self, state: dict[str, Any], rng: RngStream) -> tuple[dict[str, Any], Outcome]:
        if "split" not in legal_actions(state):
            raise InvalidBetInput("split is not allowed for this hand")
        idx = state["active"]
        hand = state["hands"][idx]
        c0, c1 = hand["cards"]
        is_aces = card_value(c0) == 11
        new_a = _new_hand(hand["bet"], [c0], is_split=True, is_split_ace=is_aces)
        new_b = _new_hand(hand["bet"], [c1], is_split=True, is_split_ace=is_aces)
        hands = [dict(h) for h in state["hands"]]
        hands[idx : idx + 1] = [new_a, new_b]
        state = {**state, "hands": hands, "total_wagered": state["total_wagered"] + hand["bet"]}
        return self._advance(state, rng)  # deals the split hands their second card

    def _surrender(self, state: dict[str, Any], rng: RngStream) -> tuple[dict[str, Any], Outcome]:
        if "surrender" not in legal_actions(state):
            raise InvalidBetInput("surrender is not allowed for this hand")
        idx = state["active"]
        hands = [dict(h) for h in state["hands"]]
        hands[idx] = {**hands[idx], "surrendered": True, "done": True}
        return self._advance({**state, "hands": hands}, rng)

    # -- sequencing & dealer ------------------------------------------------

    def _advance(self, state: dict[str, Any], rng: RngStream) -> tuple[dict[str, Any], Outcome]:
        """Move to the next hand needing a decision (dealing freshly-split hands their second
        card and auto-resolving split-aces / 21), or play the dealer out and settle."""
        while True:
            hands = state["hands"]
            idx = next((i for i, h in enumerate(hands) if not h["done"]), None)
            if idx is None:
                return self._dealer_and_settle(state, rng)
            if len(hands[idx]["cards"]) == 1:  # freshly split — deal its second card
                state, _ = self._deal_to(state, idx, rng)
                hand = state["hands"][idx]
                if hand["is_split_ace"] or hand_value(hand["cards"])[0] >= _BLACKJACK:
                    state = self._mark_done(state, idx)  # one card on a split ace, or auto-stand 21
                    continue
            return {**state, "active": idx}, self._active_outcome({**state, "active": idx})

    def _dealer_and_settle(
        self, state: dict[str, Any], rng: RngStream
    ) -> tuple[dict[str, Any], Outcome]:
        rules = state["rules"]
        hands = state["hands"]
        any_live = any(
            not h["surrendered"] and hand_value(h["cards"])[0] <= _BLACKJACK for h in hands
        )
        dealer = list(state["dealer"])
        shoe = list(state["shoe"])
        dealer_played = False
        if any_live:
            dealer_played = True
            while True:
                total, soft = hand_value(dealer)
                hits_soft_17 = total == _DEALER_STAND and soft and rules["hit_soft_17"]
                if total < _DEALER_STAND or hits_soft_17:
                    shoe, rank = draw_card(shoe, rng)
                    dealer.append(rank)
                else:
                    break
        state = {**state, "dealer": dealer, "shoe": shoe}
        dealer_total, _ = hand_value(dealer)
        dealer_bust = dealer_total > _BLACKJACK
        details = []
        for hand in hands:
            value = hand_value(hand["cards"])[0]
            if hand["surrendered"]:
                result, ret = "SURRENDER", 0.5 * hand["bet"]
            elif value > _BLACKJACK:
                result, ret = "BUST", 0.0
            elif len(hand["cards"]) == 2 and value == _BLACKJACK and not hand["is_split"]:
                result, ret = "BLACKJACK", hand["bet"] * (1.0 + rules["blackjack_payout"])
            elif dealer_bust or value > dealer_total:
                result, ret = "WIN", 2.0 * hand["bet"]
            elif value == dealer_total:
                result, ret = "PUSH", 1.0 * hand["bet"]
            else:
                result, ret = "LOSE", 0.0
            details.append(self._hand_detail(hand, result, ret))
        return self._terminal(state, details, 0.0, dealer_played=dealer_played)

    # -- outcome construction -------------------------------------------------

    def _deal_to(
        self, state: dict[str, Any], idx: int, rng: RngStream
    ) -> tuple[dict[str, Any], int]:
        shoe, rank = draw_card(state["shoe"], rng)
        hands = [dict(h) for h in state["hands"]]
        hands[idx] = {**hands[idx], "cards": [*hands[idx]["cards"], rank]}
        return {**state, "shoe": shoe, "hands": hands}, rank

    def _mark_done(self, state: dict[str, Any], idx: int) -> dict[str, Any]:
        hands = [dict(h) for h in state["hands"]]
        hands[idx] = {**hands[idx], "done": True}
        return {**state, "hands": hands}

    def _active_outcome(self, state: dict[str, Any]) -> Outcome:
        """Non-terminal step result — no credit (the app settles only at a terminal status)."""
        return Outcome(
            multiplier=0.0,
            detail={"status": _ACTIVE, "phase": state["phase"], "active": state["active"]},
        )

    @staticmethod
    def _hand_view(hand: dict[str, Any]) -> dict[str, Any]:
        total, soft = hand_value(hand["cards"])
        return {
            "cards": list(hand["cards"]),
            "value": total,
            "soft": soft,
            "bet": hand["bet"],
            "done": hand["done"],
        }

    @staticmethod
    def _hand_detail(hand: dict[str, Any], result: str, ret: float) -> dict[str, Any]:
        total, soft = hand_value(hand["cards"])
        return {
            "cards": list(hand["cards"]),
            "value": total,
            "soft": soft,
            "bet": hand["bet"],
            "doubled": hand["doubled"],
            "split": hand["is_split"],
            "result": result,
            "return": ret,
        }

    def _terminal(
        self,
        state: dict[str, Any],
        hands_detail: list[dict[str, Any]],
        insurance_return: float,
        *,
        dealer_played: bool,
    ) -> tuple[dict[str, Any], Outcome]:
        total_returned = sum(h["return"] for h in hands_detail) + insurance_return
        wagered = state["total_wagered"]
        if total_returned > wagered + 1e-9:
            status = _WON
        elif total_returned < wagered - 1e-9:
            status = _LOST
        else:
            status = _PUSH
        dealer_total, _ = hand_value(state["dealer"])
        insurance_detail = {"bet": state["insurance_bet"], "return": insurance_return}
        detail = {
            "status": status,
            "dealer": list(state["dealer"]),
            "dealerValue": dealer_total,
            "dealerPlayed": dealer_played,
            "hands": hands_detail,
            "insurance": insurance_detail,
            "totalWagered": wagered,
            "totalReturned": total_returned,
        }
        next_state = {
            **state,
            "status": status,
            "phase": status,
            "total_returned": total_returned,
            "hands_detail": hands_detail,
            "insurance_detail": insurance_detail,
        }
        return next_state, Outcome(multiplier=total_returned, detail=detail)


# The module-level singleton the registry resolves (see engine.table.__init__).
GAME = Blackjack()
