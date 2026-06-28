"""Realtime WebSocket gateways + authoritative round/table actors (app layer).

Transport + timing live here, NEVER in ``engine`` — the outcome math is pure and
reused verbatim. S18 adds Crash: a pure synchronous round core (``crash_core``)
and a thin async shell (``crash``) that drives the timed loop and fans events out
over Redis pub/sub.
"""
