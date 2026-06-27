"""Responsible-gaming gate (app member).

A SEAM: the bet loop asks ``can_bet`` BEFORE any ledger movement; a deny blocks
the bet with no money moved. S6 ships the allow-everything stub; the real policy
(loss/session/deposit limits, cool-off) lands in S38 behind this same interface.
"""

from app.rg.gate import RgDecision, can_bet

__all__ = ["RgDecision", "can_bet"]
