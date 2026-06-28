"""The data-driven slot framework — pure, stdlib-only (spec §B.1).

A single config-driven :class:`~engine.slots.framework.SlotMachine` evaluates ANY
machine from its ``GameConfig.params`` (grid, weighted reel strips, paytable,
paylines or ways-to-win, wild/scatter). RTP emerges from the strip weights +
paytable, never a post-hoc clamp. A concrete machine + its game id and RTP CI
gate ship as config in S23 (this package adds no machine and no registry entry).
"""
