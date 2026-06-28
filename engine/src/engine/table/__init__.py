"""Pure table-game outcome functions — one module per game, stdlib only.

Each module exposes a module-level ``GAME`` singleton conforming to
``engine.types.InstantGame`` or ``engine.types.StatefulGame``; the registry maps a
game id to that module path, and the bet loop resolves it (OCP seam — a new game is
a new module + a registry entry, never a bet-loop edit).
"""
