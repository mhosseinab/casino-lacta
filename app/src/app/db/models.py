"""SQLAlchemy 2.0 (async-capable) ORM models for the games-layer data model.

Source of truth: ``docs/2026-06-27_casino-games_spec_v2.md`` §2.9.

Iron rules honoured here:
- **Money is integer minor units.** Every credit/stake/payout/balance column is
  ``BigInteger`` (Postgres ``BIGINT``). No float is ever stored or settled.
  ``Bet.multiplier`` is a payout *ratio* (not money) and uses exact ``Numeric``
  decimal — never binary float.
- **Double-entry ledger.** ``LedgerEntry`` is append-only; rows sharing one
  ``txn_id`` sum to zero, with the reserved SYSTEM (house) account as the
  counterparty of every player movement.
- **Config authority.** ``GameConfig``/``GameLimit`` rows are authoritative at
  runtime; the engine registry supplies only defaults/seed values (seeded by a
  data migration). ``GameConfig.version`` is the ``config_version`` stamped into
  ``AuditEvent`` so an outcome is reconstructable against its exact config.

SQLAlchemy is imported ONLY in ``app`` — never in ``engine``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Reserved house account id (§2.2): counterparty of every player movement.
SYSTEM_USER_ID = "SYSTEM"

# String enum values — the spec's exact literals (§2.9). Kept as plain strings
# (not DB ENUM types) so migrations stay simple and reversible.
LEDGER_TYPES = ("WAGER", "WIN", "GRANT", "ROLLBACK", "PURCHASE")
ROUND_TYPES = ("SINGLE", "MULTIPLAYER")


class Base(DeclarativeBase):
    """Declarative base — its ``metadata`` is what Alembic targets."""


class User(Base):
    """A player. Includes the reserved ``User(id="SYSTEM")`` house account."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Wallet(Base):
    """A per-(user, currency, mode) balance. ``balance_minor`` is a reconciled
    projection of the ledger, never mutated directly (§2.2)."""

    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint("user_id", "currency", "mode", name="uq_wallet_user_currency_mode"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    currency: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(8), default="PLAY")
    balance_minor: Mapped[int] = mapped_column(BigInteger, default=0)


class LedgerEntry(Base):
    """Append-only, double-entry ledger row. Credits move ONLY by writing a
    balanced two-row set sharing one ``txn_id`` that sums to zero (§2.2)."""

    __tablename__ = "ledger_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    wallet_id: Mapped[str] = mapped_column(ForeignKey("wallets.id"), index=True)
    txn_id: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(16))  # WAGER|WIN|GRANT|ROLLBACK|PURCHASE
    delta_minor: Mapped[int] = mapped_column(BigInteger)
    balance_after: Mapped[int] = mapped_column(BigInteger)
    ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # DB-level idempotency fence (§2.2): the player-side row of an op carries the
    # op's idempotency key; the house-side row leaves it NULL. The UNIQUE
    # constraint (multiple NULLs permitted in Postgres) is the authority — a
    # replayed key cannot insert a second balanced set, so an op applies once.
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ServerSeed(Base):
    """Per-(user, rotation) server seed. ``seed_encrypted`` is revealed only
    after rotation (``rotated_at`` set); ``seed_hash`` is the prior commitment."""

    __tablename__ = "server_seeds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    seed_hash: Mapped[str] = mapped_column(String(64))  # SHA256 hex
    seed_encrypted: Mapped[str] = mapped_column(String(512))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClientSeed(Base):
    """Player-chosen, editable client seed (one active per user)."""

    __tablename__ = "client_seeds"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    value: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NonceCounter(Base):
    """Monotonic per-(user, server_seed) nonce; +1 each bet (§2.3)."""

    __tablename__ = "nonce_counters"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    server_seed_id: Mapped[str] = mapped_column(ForeignKey("server_seeds.id"), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, default=0)


class GameRound(Base):
    """A single round of play. ``config_version`` pins the exact ``GameConfig``
    version that produced the outcome (§2.5)."""

    __tablename__ = "game_rounds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    game_id: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(16), default="SINGLE")  # SINGLE|MULTIPLAYER
    server_seed_id: Mapped[str | None] = mapped_column(ForeignKey("server_seeds.id"), nullable=True)
    nonce: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # SERVER-ONLY opaque StatefulGame state (e.g. the Mines mine layout). Held back
    # from every client-facing projection (/state + action responses never serialize
    # it) so a hidden layout cannot leak mid-round. NULL for instant (SINGLE) rounds.
    server_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Bet(Base):
    """A wager within a round. ``stake_minor``/``payout_minor`` are minor units;
    ``multiplier`` is an exact decimal payout ratio (not money). Idempotency
    keys enforce single-debit / single-credit per bet (§2.2)."""

    __tablename__ = "bets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    round_id: Mapped[str] = mapped_column(ForeignKey("game_rounds.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    wallet_id: Mapped[str] = mapped_column(ForeignKey("wallets.id"))
    currency: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(8), default="PLAY")
    stake_minor: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16))
    multiplier: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    payout_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    idempotency_key_debit: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True
    )
    idempotency_key_credit: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GameConfig(Base):
    """Versioned, hot-updatable game configuration — AUTHORITATIVE at runtime.
    Seeded from engine defaults; each game step adds/updates its own row."""

    __tablename__ = "game_configs"
    __table_args__ = (UniqueConstraint("game_id", "version", name="uq_game_config_game_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    edge: Mapped[Decimal] = mapped_column(Numeric(8, 6), default=Decimal("0.01"))
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    paytable: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    rtp: Mapped[Decimal | None] = mapped_column(Numeric(8, 6), nullable=True)
    volatility: Mapped[str | None] = mapped_column(String(16), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)


class GameLimit(Base):
    """Per-(game, currency) bet bounds. Money columns are minor units (§2.5)."""

    __tablename__ = "game_limits"

    game_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    currency: Mapped[str] = mapped_column(String(16), primary_key=True)
    min_bet: Mapped[int] = mapped_column(BigInteger)
    max_bet: Mapped[int] = mapped_column(BigInteger)
    max_win: Mapped[int] = mapped_column(BigInteger)
    max_multiplier: Mapped[Decimal] = mapped_column(Numeric(20, 8))


class AuditEvent(Base):
    """Immutable audit record per transition (§2.10). Links a bet/round to the
    seed + nonce + input + config-version that produced it."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    bet_id: Mapped[str | None] = mapped_column(ForeignKey("bets.id"), nullable=True, index=True)
    round_id: Mapped[str | None] = mapped_column(
        ForeignKey("game_rounds.id"), nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class PokerTable(Base):
    """Poker table (stub for now — Redis during play, checkpointed; A.x)."""

    __tablename__ = "poker_tables"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stakes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    seats: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hand_no: Mapped[int] = mapped_column(Integer, default=0)


class Jackpot(Base):
    """Optional jackpot pool (stub — A.10). ``pool_minor`` is minor units."""

    __tablename__ = "jackpots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    game_id: Mapped[str] = mapped_column(String(64), index=True)
    pool_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    pop_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
