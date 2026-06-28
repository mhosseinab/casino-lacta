"""Observability for the casino service: structured logs, metrics, RTP drift monitor.

Three operational concerns over already-settled-bet data — all pure in-app (no DB, no
infra), so they run anywhere:

* :mod:`app.observability.logs` — secret-safe structured JSON logging (trace/bet IDs);
* :mod:`app.observability.metrics` — a tiny Prometheus-style counter/histogram registry;
* :mod:`app.observability.rtp` — the per-game realtime RTP drift monitor.

:func:`observe_settled_bet` is the single hook the bet loop calls at a settle point (the
documented seam): one call increments the metrics, threads the outcome into the per-game
RTP monitor, emits a structured (secret-free) log line, and returns any drift alert. It is
intentionally NOT wired into ``games/bet_loop.py`` here — keeping S39 out of the
security-reviewed saga — but is shaped to drop in at the instant-settle / cashout points.
"""

from __future__ import annotations

import logging

from app.observability.logs import (
    LOGGER_NAME,
    get_logger,
    log_event,
    redact_secrets,
    structured_event,
)
from app.observability.metrics import (
    BET_LATENCY_SECONDS,
    BETS_TOTAL,
    PAYOUTS_MINOR_TOTAL,
    REGISTRY,
    Counter,
    Histogram,
    MetricsRegistry,
)
from app.observability.rtp import (
    DEFAULT_MIN_SAMPLES,
    DEFAULT_TOLERANCE,
    MONITORS,
    RtpDrift,
    RtpMonitor,
    monitor_for,
)

__all__ = [
    "BETS_TOTAL",
    "BET_LATENCY_SECONDS",
    "Counter",
    "DEFAULT_MIN_SAMPLES",
    "DEFAULT_TOLERANCE",
    "Histogram",
    "LOGGER_NAME",
    "MONITORS",
    "MetricsRegistry",
    "PAYOUTS_MINOR_TOTAL",
    "REGISTRY",
    "RtpDrift",
    "RtpMonitor",
    "get_logger",
    "log_event",
    "monitor_for",
    "observe_settled_bet",
    "redact_secrets",
    "structured_event",
]


def observe_settled_bet(
    *,
    game_id: str,
    bet_id: str,
    status: str,
    stake_minor: int,
    payout_minor: int,
    latency_s: float,
    trace_id: str | None = None,
    round_id: str | None = None,
    registry: MetricsRegistry | None = None,
    monitors: dict[str, RtpMonitor] | None = None,
    target: float | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> RtpDrift | None:
    """Record one settled bet across all three observability concerns; return drift if any.

    Pure in-memory side effects (counter/histogram increments, monitor accumulation, a log
    line) — cannot fail on internal data, so no defensive guard wraps it. Injectable
    ``registry``/``monitors`` (default to the process-wide singletons) keep it test-isolable.
    """
    reg = registry if registry is not None else REGISTRY
    mons = monitors if monitors is not None else MONITORS

    reg.counter(BETS_TOTAL).inc(game=game_id, status=status)
    reg.counter(PAYOUTS_MINOR_TOTAL).inc(payout_minor, game=game_id)
    reg.histogram(BET_LATENCY_SECONDS).observe(latency_s, game=game_id)

    monitor = monitor_for(
        game_id, mons, target=target, tolerance=tolerance, min_samples=min_samples
    )
    monitor.observe(stake_minor=stake_minor, payout_minor=payout_minor)
    drift = monitor.check()

    log_event(
        "bet_settled",
        trace_id=trace_id or bet_id,
        bet_id=bet_id,
        round_id=round_id,
        game_id=game_id,
        status=status,
        stake_minor=stake_minor,
        payout_minor=payout_minor,
        latency_s=latency_s,
    )
    if drift is not None:
        log_event(
            "rtp_drift",
            level=logging.WARNING,
            trace_id=trace_id or bet_id,
            game_id=game_id,
            target=drift.target,
            measured_rtp=drift.measured_rtp,
            delta=drift.delta,
            samples=drift.samples,
            tolerance=drift.tolerance,
        )
    return drift
