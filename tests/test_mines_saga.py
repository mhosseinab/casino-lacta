"""S12 — Mines server-held-state saga, end to end against a real migrated Postgres.

The first stateful game's money + state contract:
- SINGLE-DEBIT at round open; SINGLE-CREDIT at cashout (never on reveal).
- The shared one-active-(user,game)-round guard blocks a second concurrent round.
- Per-action FOR UPDATE row lock serializes reveals (no double-advance of k).
- REDACTION: the opaque server_state (mine layout) is NEVER serialized to the
  client; an ACTIVE round's projection exposes no unrevealed-cell information.
- Revealing a mine LOSES the round (no credit); cashout requires ≥1 reveal; any
  action on a terminal round is rejected.

Requires the migrated DB (see tests/conftest.py):

    docker compose up -d postgres
    DATABASE_URL=postgresql+asyncpg://lacta:lacta@localhost:5432/lacta \
        uv run alembic upgrade head
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    ClientSeed,
    GameRound,
    LedgerEntry,
    NonceCounter,
    ServerSeed,
    User,
    Wallet,
)
from app.games import ActiveRoundExists, place_bet
from app.games.bet_loop import RoundNotFound, RoundTerminal, step_action
from app.wallet import Ledger
from engine.types import InvalidBetInput

GAME_ID = "originals.mines"
CLIENT_SEED = "mines-saga-client-seed"


async def _seed_player(
    session_factory: async_sessionmaker[AsyncSession],
    ledger: Ledger,
    *,
    balance_minor: int = 100_000,
) -> tuple[str, str]:
    """A funded GOLD/PLAY player with a known server/client seed + nonce counter."""
    uid = f"u-{uuid4().hex}"
    wid = f"w-{uuid4().hex}"
    sid = f"s-{uuid4().hex}"
    server_seed = secrets.token_bytes(32)
    async with session_factory() as session, session.begin():
        session.add(User(id=uid))
        await session.flush()
        session.add(Wallet(id=wid, user_id=uid, currency="GOLD", mode="PLAY", balance_minor=0))
        session.add(
            ServerSeed(
                id=sid,
                user_id=uid,
                seed_hash=hashlib.sha256(server_seed).hexdigest(),
                seed_encrypted=server_seed.hex(),
            )
        )
        session.add(ClientSeed(user_id=uid, value=CLIENT_SEED))
        await session.flush()
        session.add(NonceCounter(user_id=uid, server_seed_id=sid, value=0))
    await ledger.grant(
        wallet_id=wid, amount_minor=balance_minor, idempotency_key=f"{wid}:seed-grant"
    )
    return uid, wid


async def _server_state(
    session_factory: async_sessionmaker[AsyncSession], round_id: str
) -> dict:
    async with session_factory() as session:
        row = await session.get(GameRound, round_id)
    assert row is not None
    return dict(row.server_state or {})


async def _safe_cells(
    session_factory: async_sessionmaker[AsyncSession], round_id: str
) -> list[int]:
    state = await _server_state(session_factory, round_id)
    mines = set(state["mine_positions"])
    return [c for c in range(25) if c not in mines]


async def _mine_cell(
    session_factory: async_sessionmaker[AsyncSession], round_id: str
) -> int:
    state = await _server_state(session_factory, round_id)
    return int(state["mine_positions"][0])


async def _ledger_type_count(
    session_factory: async_sessionmaker[AsyncSession], wallet_id: str, type_: str
) -> int:
    async with session_factory() as session:
        n = await session.scalar(
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.wallet_id == wallet_id, LedgerEntry.type == type_)
        )
    return int(n or 0)


# --- happy path: open → reveals → cashout ------------------------------------


async def test_open_reveal_cashout_single_debit_single_credit(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    uid, wid = await _seed_player(session_factory, ledger)
    rid = f"r-{uuid4().hex}"

    opened = await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid,
        stake_minor=1_000, input={"mines": 3},
    )
    assert opened.status == "ACTIVE"
    # ONE debit at open; NO credit yet.
    assert await _ledger_type_count(session_factory, wid, "WAGER") == 1
    assert await _ledger_type_count(session_factory, wid, "WIN") == 0

    safe = await _safe_cells(session_factory, rid)
    for cell in safe[:2]:
        resp = await step_action(
            session_factory, ledger, user_id=uid, game_id=GAME_ID,
            round_id=rid, action={"op": "reveal", "cell": cell},
        )
        assert resp["safe"] is True
        assert resp["status"] == "ACTIVE"
        # No credit lands on a reveal.
        assert await _ledger_type_count(session_factory, wid, "WIN") == 0

    cash = await step_action(
        session_factory, ledger, user_id=uid, game_id=GAME_ID,
        round_id=rid, action={"op": "cashout"},
    )
    assert cash["status"] == "CASHED_OUT"
    assert cash["k"] == 2
    assert cash["payoutMinor"] > 1_000  # k=2, M=3 → 1.286x

    # EXACTLY one WAGER and one WIN; balance reconciles.
    assert await _ledger_type_count(session_factory, wid, "WAGER") == 1
    assert await _ledger_type_count(session_factory, wid, "WIN") == 1
    assert await ledger.reconcile(wid) == 100_000 - 1_000 + cash["payoutMinor"]


# --- losing on a mine --------------------------------------------------------


async def test_revealing_a_mine_loses_no_credit(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    uid, wid = await _seed_player(session_factory, ledger)
    rid = f"r-{uuid4().hex}"
    await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid,
        stake_minor=1_000, input={"mines": 5},
    )
    mine = await _mine_cell(session_factory, rid)
    resp = await step_action(
        session_factory, ledger, user_id=uid, game_id=GAME_ID,
        round_id=rid, action={"op": "reveal", "cell": mine},
    )
    assert resp["safe"] is False
    assert resp["status"] == "LOST"
    assert await _ledger_type_count(session_factory, wid, "WIN") == 0
    assert await ledger.reconcile(wid) == 100_000 - 1_000


# --- terminal & cashout rules ------------------------------------------------


async def test_cashout_requires_a_reveal(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    uid, _ = await _seed_player(session_factory, ledger)
    rid = f"r-{uuid4().hex}"
    await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid,
        stake_minor=1_000, input={"mines": 3},
    )
    with pytest.raises(InvalidBetInput):
        await step_action(
            session_factory, ledger, user_id=uid, game_id=GAME_ID,
            round_id=rid, action={"op": "cashout"},
        )


async def test_no_action_after_terminal(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    uid, _ = await _seed_player(session_factory, ledger)
    rid = f"r-{uuid4().hex}"
    await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid,
        stake_minor=1_000, input={"mines": 3},
    )
    safe = await _safe_cells(session_factory, rid)
    await step_action(
        session_factory, ledger, user_id=uid, game_id=GAME_ID,
        round_id=rid, action={"op": "reveal", "cell": safe[0]},
    )
    await step_action(
        session_factory, ledger, user_id=uid, game_id=GAME_ID,
        round_id=rid, action={"op": "cashout"},
    )
    with pytest.raises(RoundTerminal):
        await step_action(
            session_factory, ledger, user_id=uid, game_id=GAME_ID,
            round_id=rid, action={"op": "reveal", "cell": safe[1]},
        )


async def test_cashout_replay_is_idempotent(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A re-sent cashout on a CASHED_OUT round re-issues no second credit."""
    uid, wid = await _seed_player(session_factory, ledger)
    rid = f"r-{uuid4().hex}"
    await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid,
        stake_minor=1_000, input={"mines": 3},
    )
    safe = await _safe_cells(session_factory, rid)
    await step_action(
        session_factory, ledger, user_id=uid, game_id=GAME_ID,
        round_id=rid, action={"op": "reveal", "cell": safe[0]},
    )
    first = await step_action(
        session_factory, ledger, user_id=uid, game_id=GAME_ID,
        round_id=rid, action={"op": "cashout"},
    )
    second = await step_action(
        session_factory, ledger, user_id=uid, game_id=GAME_ID,
        round_id=rid, action={"op": "cashout"},
    )
    assert second["payoutMinor"] == first["payoutMinor"]
    assert await _ledger_type_count(session_factory, wid, "WIN") == 1


# --- the one-active-round guard ----------------------------------------------


async def test_guard_blocks_second_concurrent_round(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    uid, wid = await _seed_player(session_factory, ledger)
    await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=f"r-{uuid4().hex}",
        stake_minor=1_000, input={"mines": 3},
    )
    with pytest.raises(ActiveRoundExists):
        await place_bet(
            session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=f"r-{uuid4().hex}",
            stake_minor=1_000, input={"mines": 3},
        )
    # The blocked open moved no second WAGER.
    assert await _ledger_type_count(session_factory, wid, "WAGER") == 1


async def test_open_resend_returns_active_round_no_second_debit(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    uid, wid = await _seed_player(session_factory, ledger)
    rid = f"r-{uuid4().hex}"
    first = await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid,
        stake_minor=1_000, input={"mines": 3},
    )
    second = await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid,
        stake_minor=1_000, input={"mines": 3},
    )
    assert second.bet_id == first.bet_id
    assert second.status == "ACTIVE"
    assert await _ledger_type_count(session_factory, wid, "WAGER") == 1


# --- action serialization (FOR UPDATE) ---------------------------------------


async def test_concurrent_distinct_reveals_both_land(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Positive smoke: two concurrent reveals of DIFFERENT cells both land (k=2).

    A correctness regression guard for the concurrent path; the deterministic PROOF
    that the FOR UPDATE lock prevents a lost update is
    ``test_action_reread_under_lock_prevents_lost_update`` below."""
    uid, _ = await _seed_player(session_factory, ledger)
    rid = f"r-{uuid4().hex}"
    await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid,
        stake_minor=1_000, input={"mines": 3},
    )
    safe = await _safe_cells(session_factory, rid)
    a_cell, b_cell = safe[0], safe[1]

    async def reveal(cell: int) -> None:
        await step_action(
            session_factory, ledger, user_id=uid, game_id=GAME_ID,
            round_id=rid, action={"op": "reveal", "cell": cell},
        )

    await asyncio.gather(reveal(a_cell), reveal(b_cell))
    state = await _server_state(session_factory, rid)
    assert set(state["revealed"]) == {a_cell, b_cell}  # both landed — no lost update
    assert len(state["revealed"]) == 2


async def test_action_reread_under_lock_prevents_lost_update(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The deterministic proof of read-modify-write serialization via FOR UPDATE.

    We hold the round row's FOR UPDATE lock in an outside transaction, start a reveal
    of cell B (which must wait for the lock), then — while it waits — commit a reveal
    of a DIFFERENT cell A and release. The reveal of B must then build on the FRESH
    post-A state and produce {A, B}.

    Discriminating: with ``SELECT ... FOR UPDATE`` the action blocks AT THE READ, so
    it reads A's committed change and appends B → {A, B}, k=2. Without it, the action
    reads the STALE pre-A state (a plain read does not wait on a row-lock holder in
    PG's MVCC), then its later UPDATE overwrites A's change → {B} only, k=1 — A is a
    LOST UPDATE. (Verified: this test fails when ``with_for_update`` is removed.)"""
    uid, _ = await _seed_player(session_factory, ledger)
    rid = f"r-{uuid4().hex}"
    await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid,
        stake_minor=1_000, input={"mines": 3},
    )
    safe = await _safe_cells(session_factory, rid)
    a_cell, b_cell = safe[0], safe[1]

    task: asyncio.Task[dict]
    async with session_factory() as holder, holder.begin():
        locked = await holder.get(GameRound, rid, with_for_update=True)  # hold the lock
        assert locked is not None
        # Reveal of B starts and must wait on the held lock before it can read.
        task = asyncio.create_task(
            step_action(
                session_factory, ledger, user_id=uid, game_id=GAME_ID,
                round_id=rid, action={"op": "reveal", "cell": b_cell},
            )
        )
        await asyncio.sleep(0.5)
        assert not task.done(), "reveal proceeded without waiting on the row lock"
        # Commit a concurrent reveal of A under the held lock, then release.
        state = dict(locked.server_state or {})
        state["revealed"] = [a_cell]
        locked.server_state = state  # new dict → flushed on commit (with_begin exit)

    resp = await asyncio.wait_for(task, timeout=5)  # lock released → reveal of B proceeds
    final = await _server_state(session_factory, rid)
    assert set(final["revealed"]) == {a_cell, b_cell}  # A not lost; B built on fresh state
    assert resp["k"] == 2


async def test_concurrent_same_cell_reveal_is_idempotent(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Two racing reveals of the SAME cell: k advances once, no duplicate (re-reveal
    is an intrinsic no-op). Documents idempotency — the no-lost-update proof is the
    distinct-cell test above."""
    uid, _ = await _seed_player(session_factory, ledger)
    rid = f"r-{uuid4().hex}"
    await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid,
        stake_minor=1_000, input={"mines": 3},
    )
    safe = await _safe_cells(session_factory, rid)
    cell = safe[0]

    async def reveal() -> int:
        resp = await step_action(
            session_factory, ledger, user_id=uid, game_id=GAME_ID,
            round_id=rid, action={"op": "reveal", "cell": cell},
        )
        return int(resp["k"])

    a, b = await asyncio.gather(reveal(), reveal())
    assert a == b == 1
    state = await _server_state(session_factory, rid)
    assert state["revealed"] == [cell]  # no duplicate


# --- redaction ---------------------------------------------------------------


async def test_active_round_projection_leaks_no_mine_positions(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    uid, _ = await _seed_player(session_factory, ledger)
    rid = f"r-{uuid4().hex}"
    opened = await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid,
        stake_minor=1_000, input={"mines": 5},
    )
    # The /bet projection carries no layout.
    assert "minePositions" not in opened.outcome
    assert "mine_positions" not in opened.outcome

    safe = await _safe_cells(session_factory, rid)
    resp = await step_action(
        session_factory, ledger, user_id=uid, game_id=GAME_ID,
        round_id=rid, action={"op": "reveal", "cell": safe[0]},
    )
    # A safe-reveal projection never exposes unrevealed cells.
    assert "minePositions" not in resp
    assert "mine_positions" not in resp

    # The client-facing round.outcome column (what /state returns) is layout-free
    # while ACTIVE; the layout lives ONLY in the server-only server_state column.
    async with session_factory() as session:
        row = await session.get(GameRound, rid)
    assert row is not None
    assert "minePositions" not in (row.outcome or {})
    assert row.server_state and "mine_positions" in row.server_state  # server-only


async def test_action_rejected_for_other_users_round(
    ledger: Ledger, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    uid, _ = await _seed_player(session_factory, ledger)
    other, _ = await _seed_player(session_factory, ledger)
    rid = f"r-{uuid4().hex}"
    await place_bet(
        session_factory, ledger, user_id=uid, game_id=GAME_ID, bet_id=rid,
        stake_minor=1_000, input={"mines": 3},
    )
    safe = await _safe_cells(session_factory, rid)
    with pytest.raises(RoundNotFound):
        await step_action(
            session_factory, ledger, user_id=other, game_id=GAME_ID,
            round_id=rid, action={"op": "reveal", "cell": safe[0]},
        )
