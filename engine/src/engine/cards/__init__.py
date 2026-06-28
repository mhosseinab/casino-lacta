"""Pure card primitives — a 52-card model + a game-AGNOSTIC hand-rank evaluator.

``evaluator.rank_five`` is the single, shared 5-card ranking core (high card →
straight flush) returning a totally ordered :class:`~engine.cards.evaluator.HandRank`.
Video poker (S28) layers its paytable on top; PvP poker (S29) ranks the best-5-of-7 via
``evaluator.rank_seven`` — a thin ``max(rank_five(c) for c in combinations(seven, 5))``
wrapper over the SAME core, never duplicated. Stdlib only; no game-specific rules (e.g.
"jacks-or-better") leak into the evaluator.
"""
