"""Crash resume endpoint (S20): ``GET /games/originals.crash/state``.

Crash is a shared, actor-owned MULTIPLAYER round — UNLIKE the per-user instant /
stateful games the generic ``/games/{game_id}/state`` serves. So it has its OWN
state handler with the Crash-shaped projection (the current shared round + the
CALLER's active bets). This router is included BEFORE the generic games router so
the literal path wins the route match (precedence is registration order).

Identity is server-authoritative — the bearer token's user (the S8
``get_current_user`` dependency), never the request. Redaction (withhold ``C`` +
the raw seed while the round is live) lives in ``crash_state``, keyed off the
persisted round status (the recovery authority). The live cosmetic multiplier is
enriched from an in-process actor when one is registered (``app.state.crash_actor``)
— ``None`` otherwise (the unwired-actor fence); it is public regardless.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.deps import CurrentUser, get_current_user
from app.ws.crash import CrashActor
from app.ws.crash_bets import CRASH_GAME_ID, crash_state

router = APIRouter(tags=["crash"])


@router.get(f"/games/{CRASH_GAME_ID}/state")
async def get_crash_state(
    request: Request,
    current: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """The current shared Crash round + the caller's active bets (resume on reconnect).

    Identity is the token's user — a caller sees ONLY its own bets. ``404`` if no
    Crash round has ever opened in this deployment."""
    from app.api.games import _runtime

    rt = _runtime(request)
    actor: CrashActor | None = getattr(request.app.state, "crash_actor", None)
    live_multiplier = actor.current_cosmetic_multiplier if actor is not None else None
    state = await crash_state(
        rt.session_factory, user_id=current.user_id, live_multiplier=live_multiplier
    )
    if state["round"] is None:
        raise HTTPException(status_code=404, detail="no crash round")
    return state
