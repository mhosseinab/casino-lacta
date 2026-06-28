"""Table games — server-authoritative, pure outcome logic (stdlib only).

Each module exposes a module-level ``GAME`` singleton conforming to
``engine.types.StatefulGame`` (blackjack, baccarat, …); the registry maps a game id
to the module path and the bet loop resolves it (the OCP seam).
"""
