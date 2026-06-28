"""Responsible-gaming gate (app member).

A SEAM: the bet loop asks ``can_bet`` BEFORE any ledger movement; a deny blocks
the bet with a TYPED ``RgReason`` (surfaced to the UI), no money moved. S6 shipped
the allow-everything stub; S38 enforces self-imposed spend/loss/session limits,
cool-off, and self-exclusion behind this same interface, and exposes the persistent
play-money ("not real money / no prizes") disclosure for the client to render.
"""

from app.rg.gate import (
    NOT_REAL_MONEY,
    PLAY_MONEY_NOTICE,
    RgDecision,
    RgReason,
    can_bet,
    play_money_notice,
)

__all__ = [
    "NOT_REAL_MONEY",
    "PLAY_MONEY_NOTICE",
    "RgDecision",
    "RgReason",
    "can_bet",
    "play_money_notice",
]
