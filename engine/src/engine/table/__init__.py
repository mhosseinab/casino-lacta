"""Pure table-game outcome logic — stdlib only (Blackjack, Roulette, Baccarat, Video Poker).

Each module exposes a module-level ``GAME`` singleton conforming to
``engine.types.InstantGame`` / ``StatefulGame``; the registry maps a game id to the
module path and the bet loop resolves it (the OCP seam). Card games rank hands via the
shared ``engine.cards.evaluator`` — never a re-implemented ranking.
"""
