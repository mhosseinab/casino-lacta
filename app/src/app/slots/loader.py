"""Read a slot machine's JSON config (app-side IO; engine stays pure).

``load_machine(machine_id)`` resolves ``<machineId-suffix>.json`` shipped as inert
package data under ``engine.slots.machines`` via :mod:`importlib.resources` — so it
works regardless of install layout and never reaches into the engine source tree by
path. The returned :class:`MachineConfig` exposes the engine ``params`` (fed to the
DB ``GameConfig`` and to ``SlotMachine.play``) plus the machine's house policy
(``edge``/``rtp``/``volatility``) used to seed the authoritative config row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

_SLOTS_PACKAGE = "engine.slots"
_MACHINES_DIR = "machines"


@dataclass(frozen=True)
class MachineConfig:
    """A loaded machine definition. ``params`` is the engine ``GameConfig.params``
    (strips/paytable/paylines/features); the rest is house policy + presentation."""

    machine_id: str
    edge: float
    rtp: float
    volatility: str
    params: dict[str, Any]
    raw: dict[str, Any]


def _filename(machine_id: str) -> str:
    """``slots.machine01`` → ``machine01.json`` (the registry-id suffix is the file)."""
    return f"{machine_id.split('.', 1)[-1]}.json"


def load_machine(machine_id: str) -> MachineConfig:
    """Load and parse the machine's JSON config from the engine package data."""
    resource = files(_SLOTS_PACKAGE) / _MACHINES_DIR / _filename(machine_id)
    text = resource.read_text(encoding="utf-8")
    doc: dict[str, Any] = json.loads(text)
    return MachineConfig(
        machine_id=str(doc["machineId"]),
        edge=float(doc["edge"]),
        rtp=float(doc["rtp"]),
        volatility=str(doc["volatility"]),
        params=dict(doc["params"]),
        raw=doc,
    )
