"""S39 — observability: structured logging, Prometheus-style metrics, RTP drift monitor.

Three concerns, all pure in-app (no DB, no infra — these are operational signals over
already-settled-bet data, so the suite runs anywhere):

* **metrics increment on a settled bet** — the bet counter, the payout total, and the
  latency histogram all move when a settled bet is observed;
* **the RTP monitor flags a synthetic drift** — fed outcomes whose realized RTP diverges
  from the configured target beyond tolerance, a drift alert fires; within tolerance (and
  below the minimum-sample floor) it stays quiet;
* **logs are structured and leak no secret** — an emitted record parses as JSON carrying
  the trace/bet correlation IDs, and a raw server seed handed to the logger never reaches
  the output (key dropped AND value absent from the serialized line).
"""

from __future__ import annotations

import json
import logging

import pytest

from app.observability import (
    BET_LATENCY_SECONDS,
    BETS_TOTAL,
    PAYOUTS_MINOR_TOTAL,
    MetricsRegistry,
    RtpDrift,
    RtpMonitor,
    log_event,
    observe_settled_bet,
    redact_secrets,
    structured_event,
)

# --------------------------------------------------------------------------- metrics


def test_metrics_increment_on_a_settled_bet() -> None:
    """A settled bet bumps the bet counter, adds its payout, and records its latency."""
    registry = MetricsRegistry()  # isolated — never assert against module-global state
    monitors: dict[str, RtpMonitor] = {}

    before_bets = registry.counter(BETS_TOTAL).value(game="originals.dice", status="WON")
    before_payout = registry.counter(PAYOUTS_MINOR_TOTAL).value(game="originals.dice")
    before_lat = registry.histogram(BET_LATENCY_SECONDS).count(game="originals.dice")

    observe_settled_bet(
        game_id="originals.dice",
        bet_id="bet-1",
        status="WON",
        stake_minor=100,
        payout_minor=198,
        latency_s=0.012,
        registry=registry,
        monitors=monitors,
    )

    bets = registry.counter(BETS_TOTAL).value(game="originals.dice", status="WON")
    assert bets == before_bets + 1
    assert (
        registry.counter(PAYOUTS_MINOR_TOTAL).value(game="originals.dice")
        == before_payout + 198
    )
    hist = registry.histogram(BET_LATENCY_SECONDS)
    assert hist.count(game="originals.dice") == before_lat + 1
    assert hist.sum(game="originals.dice") == 0.012


def test_metrics_registry_renders_prometheus_exposition() -> None:
    """The registry renders Prometheus-style text (HELP/TYPE + samples)."""
    registry = MetricsRegistry()
    observe_settled_bet(
        game_id="originals.dice",
        bet_id="bet-2",
        status="LOST",
        stake_minor=100,
        payout_minor=0,
        latency_s=0.02,
        registry=registry,
        monitors={},
    )
    text = registry.render()
    assert "# TYPE bets_total counter" in text
    assert 'bets_total{game="originals.dice",status="LOST"} 1' in text
    assert "bet_latency_seconds" in text


# --------------------------------------------------------------------------- RTP monitor


def test_rtp_monitor_flags_synthetic_drift() -> None:
    """Realized RTP far below target (beyond tolerance, past the sample floor) → drift fires."""
    mon = RtpMonitor(game_id="originals.dice", target=0.99, tolerance=0.05, min_samples=50)
    for _ in range(100):
        mon.observe(stake_minor=100, payout_minor=50)  # realized RTP 0.50 — gross drift

    drift = mon.check()
    assert isinstance(drift, RtpDrift)
    assert drift.game_id == "originals.dice"
    assert drift.measured_rtp == 0.5
    assert drift.target == 0.99
    assert abs(drift.delta) > 0.05


def test_rtp_monitor_quiet_within_tolerance() -> None:
    """Realized RTP at target raises no flag."""
    mon = RtpMonitor(game_id="originals.dice", target=0.99, tolerance=0.05, min_samples=50)
    for _ in range(100):
        mon.observe(stake_minor=100, payout_minor=99)  # realized RTP 0.99

    assert mon.check() is None
    assert mon.measured_rtp == 0.99


def test_rtp_monitor_silent_below_sample_floor() -> None:
    """Below min_samples a divergent run does NOT flag — too little data to alert on."""
    mon = RtpMonitor(game_id="originals.dice", target=0.99, tolerance=0.05, min_samples=50)
    for _ in range(5):
        mon.observe(stake_minor=100, payout_minor=0)  # RTP 0.0 but only 5 samples

    assert mon.check() is None


def test_observe_settled_bet_returns_drift_when_monitor_trips() -> None:
    """The combined hook threads outcomes into the per-game monitor and surfaces drift."""
    registry = MetricsRegistry()
    monitors: dict[str, RtpMonitor] = {}
    drift: RtpDrift | None = None
    for i in range(100):
        drift = observe_settled_bet(
            game_id="originals.dice",
            bet_id=f"bet-{i}",
            status="LOST",
            stake_minor=100,
            payout_minor=40,  # realized RTP 0.40, target 0.99
            latency_s=0.01,
            registry=registry,
            monitors=monitors,
            target=0.99,
            tolerance=0.05,
            min_samples=50,
        )
    assert isinstance(drift, RtpDrift)
    assert drift.measured_rtp == 0.4


# --------------------------------------------------------------------------- structured logs


def test_structured_event_carries_trace_and_bet_ids() -> None:
    rec = structured_event("bet_settled", trace_id="trace-1", bet_id="bet-1", stake_minor=100)
    assert rec["event"] == "bet_settled"
    assert rec["trace_id"] == "trace-1"
    assert rec["bet_id"] == "bet-1"
    assert rec["stake_minor"] == 100


def test_redaction_drops_secrets_but_keeps_public_fairness_fields() -> None:
    """Raw seeds/tokens are dropped; the PUBLIC commitment fields are preserved."""
    fields = redact_secrets(
        {
            "server_seed": "DEADBEEFCAFE",  # secret — the unrevealed raw seed
            "seed_encrypted": "00ff00ff",  # secret
            "access_token": "jwt.xxx.yyy",  # secret
            "server_seed_hash": "abc123",  # PUBLIC commitment — keep
            "client_seed": "user-seed",  # PUBLIC — keep
            "nonce": 7,  # PUBLIC — keep
        }
    )
    assert "server_seed" not in fields
    assert "seed_encrypted" not in fields
    assert "access_token" not in fields
    assert fields["server_seed_hash"] == "abc123"
    assert fields["client_seed"] == "user-seed"
    assert fields["nonce"] == 7


def test_emitted_log_line_is_structured_json_without_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The ACTUAL emitted record parses as JSON, carries the IDs, and the secret VALUE
    never reaches the output."""
    with caplog.at_level(logging.INFO, logger="app.observability"):
        log_event(
            "bet_settled",
            trace_id="trace-9",
            bet_id="bet-9",
            game_id="originals.dice",
            server_seed="DEADBEEFCAFE",  # must never appear in the output
            stake_minor=100,
        )

    assert len(caplog.records) == 1
    line = caplog.records[0].getMessage()
    parsed = json.loads(line)
    assert parsed["event"] == "bet_settled"
    assert parsed["trace_id"] == "trace-9"
    assert parsed["bet_id"] == "bet-9"
    assert "server_seed" not in parsed
    assert "DEADBEEFCAFE" not in line
