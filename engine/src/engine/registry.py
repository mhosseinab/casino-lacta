"""Game registry — id -> (module path, default GameConfig).

The OCP seam: a new game is a registry entry + a conforming outcome function; the
bet loop, ledger, and API never change. DB GameConfig/GameLimit are authoritative at
runtime; these are the defaults/seed values. Empty until the first game lands. §3.
"""

from engine.types import GameConfig

# id -> (dotted module path of the game's outcome impl, default GameConfig)
REGISTRY: dict[str, tuple[str, GameConfig]] = {}
