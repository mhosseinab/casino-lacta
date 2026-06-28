"""Pure PvP-poker outcome logic — stdlib only (side pots, table reducer, anti-collusion).

The package depends on the shared ``engine.cards.evaluator`` for hand ranking and never
re-implements it. ``pots`` owns the main + side-pot math (the classic all-in bug source):
layering per-player contributions into pots and awarding them by hand rank at showdown,
with chips conserved to the minor unit. Stdlib only; no IO, clock, or randomness.
"""
