"""Slots support (app member) — the machine-config READER.

The slot ``engine/`` framework is pure (stdlib only): it may NOT ``open()`` a file
or import ``pathlib``/``os`` (the load-bearing purity constraint). So a machine's
JSON config — which physically ships under ``engine/src/engine/slots/machines/`` as
INERT DATA (build-plan_v2 §96), never read by engine code — is loaded HERE, in
``app/`` (where IO is allowed). The JSON is the SINGLE SOURCE OF TRUTH for a machine;
the runtime flow is::

    machine01.json → load_machine() → DB GameConfig.params (seeded by a migration)
                  → the bet loop → SlotMachine.play(input, rng, cfg) reads cfg.params

so the engine resolves every machine purely from config, exactly like every other
game. The registry default for ``slots.*`` therefore carries NO params (it cannot
open the file): DB-seeded-from-JSON is the sole runtime authority (no Python-literal
copy to drift from).
"""

from app.slots.loader import MachineConfig, load_machine

__all__ = ["MachineConfig", "load_machine"]
