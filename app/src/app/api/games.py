"""Generic game endpoints (spec §2.10 envelope):

    POST /games/{gameId}/bet      → single instant bet (debit → outcome → credit)
    POST /games/{gameId}/action   → reveal/step/cashout (stateful — wired in S12+)
    GET  /games/{gameId}/state    → current/last round (resume after reconnect)

The router is transport-only: it shapes the request, delegates to the shared bet
loop, and maps domain errors to HTTP status. There is NO per-game code here (OCP)
and NO outcome/balance logic (server-authoritative).

Identity is server-authoritative: it comes ONLY from the bearer token (the S8
``get_current_user`` dependency), NEVER from the request body or query — a client
cannot bet as, or read the state of, another user.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import CurrentUser, get_current_user
from app.db.models import Bet, GameRound
from app.db.session import make_engine, make_session_factory
from app.games import (
    ActiveRoundExists,
    BetObject,
    GameDisabled,
    RgDenied,
    RoundNotFound,
    RoundTerminal,
    StakeOutOfRange,
    place_bet,
    step_action,
)
from app.wallet import InsufficientFunds, Ledger, WalletNotFound
from engine.registry import UnknownGame
from engine.types import InvalidBetInput

router = APIRouter(prefix="/games", tags=["games"])

_DEFAULT_DB_URL = "postgresql+asyncpg://lacta:lacta@localhost:5432/lacta"


class _Runtime:
    """Per-process DB seam (session factory + ledger), built lazily so importing
    the app needs no live DB (the health probe / unit imports stay DB-free)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory
        self.ledger = Ledger(session_factory)


def _runtime(request: Request) -> _Runtime:
    rt: _Runtime | None = getattr(request.app.state, "games_runtime", None)
    if rt is None:
        url = os.environ.get("DATABASE_URL", _DEFAULT_DB_URL)
        rt = _Runtime(make_session_factory(make_engine(url)))
        request.app.state.games_runtime = rt
    return rt


class BetRequest(BaseModel):
    """The /bet intent. The client sends ONLY intent + stake; never an outcome and
    never an identity — ``userId`` is intentionally absent (it comes from the token,
    so a client cannot even express acting as another user)."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    bet_id: str
    stake_minor: int
    currency: str = "GOLD"
    mode: str = "PLAY"
    input: dict[str, Any] = Field(default_factory=dict)


@router.post("/{game_id}/bet", response_model=BetObject)
async def post_bet(
    game_id: str,
    body: BetRequest,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
) -> BetObject:
    rt = _runtime(request)
    try:
        return await place_bet(
            rt.session_factory,
            rt.ledger,
            user_id=current.user_id,  # server-authoritative identity (from the token)
            game_id=game_id,
            bet_id=body.bet_id,
            stake_minor=body.stake_minor,
            currency=body.currency,
            mode=body.mode,
            input=body.input,
        )
    except UnknownGame as exc:
        raise HTTPException(status_code=404, detail=f"unknown game {game_id}") from exc
    except InvalidBetInput as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StakeOutOfRange as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (GameDisabled, ActiveRoundExists) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RgDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InsufficientFunds as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    except WalletNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ActionRequest(BaseModel):
    """The /action intent for a stateful round (Mines reveal/cashout, HiLo guess/cashout).
    Identity comes from the token, NEVER the body; the server decides the outcome from its
    held state. Per-game fields (``cell`` for Mines, ``side`` for HiLo) are optional —
    each game validates the ones it needs in its pure ``step`` (the router stays generic)."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    round_id: str
    op: str
    cell: int | None = None
    side: str | None = None


@router.post("/{game_id}/action")
async def post_action(
    game_id: str,
    body: ActionRequest,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Advance a stateful round (Mines reveal/cashout, HiLo guess/cashout). Returns the
    safe client-facing projection only — never hidden state. Identity is
    server-authoritative."""
    rt = _runtime(request)
    action: dict[str, Any] = {"op": body.op}
    if body.cell is not None:
        action["cell"] = body.cell
    if body.side is not None:
        action["side"] = body.side
    try:
        return await step_action(
            rt.session_factory,
            rt.ledger,
            user_id=current.user_id,  # server-authoritative identity (from the token)
            game_id=game_id,
            round_id=body.round_id,
            action=action,
        )
    except UnknownGame as exc:
        raise HTTPException(status_code=404, detail=f"unknown game {game_id}") from exc
    except RoundNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RoundTerminal as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidBetInput as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InsufficientFunds as exc:  # pragma: no cover - credit cannot underflow
        raise HTTPException(status_code=402, detail=str(exc)) from exc


@router.get("/{game_id}/state")
async def get_state(
    game_id: str,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """The caller's most recent round for this game (resume after reconnect).

    Identity is the token's user — a caller can only read its OWN round."""
    rt = _runtime(request)
    async with rt.session_factory() as session:
        round_row = await session.scalar(
            select(GameRound)
            .join(Bet, Bet.round_id == GameRound.id)
            .where(Bet.user_id == current.user_id, GameRound.game_id == game_id)
            .order_by(GameRound.created_at.desc())
            .limit(1)
        )
    if round_row is None:
        raise HTTPException(status_code=404, detail="no round for this user/game")
    # REDACTION: only the client-facing safe projection is serialized. The server-only
    # `server_state` column (the Mines mine layout) is deliberately NEVER returned, so
    # a mid-round caller cannot infer an unrevealed mine.
    return {
        "roundId": round_row.id,
        "gameId": round_row.game_id,
        "status": round_row.status,
        "nonce": round_row.nonce,
        "configVersion": round_row.config_version,
        "input": round_row.input,
        "outcome": round_row.outcome,
    }
