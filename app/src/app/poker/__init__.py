"""Poker app member — lobby/matchmaking, buy-in, leave, and rake (S33).

The realtime table actor + per-seat redaction live in ``app.ws.poker`` (S32/S34);
this package owns the money/seat seam: it is the ONLY place poker touches the ledger.
"""
