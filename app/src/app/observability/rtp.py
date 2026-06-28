"""Per-game realtime RTP drift monitor.

A RUNTIME ALERT, deliberately DISTINCT from the offline ``tests/rtp_harness.py`` CI gate.
The harness measures the UNCAPPED engine multiplier EV (pure paytable correctness, no cap,
no rounding) and gates a build. This monitor watches the *realized* settled stream in
production and flags GROSS drift from the configured target for a human to investigate.

Two facts shape the tolerance:

* Realized RTP is structurally a hair BELOW the theoretical ``1 - edge``: settled payouts
  are floor-rounded and ``maxWin``-capped (``cap(apply_multiplier(...))`` in the bet loop —
  the truncated fraction is house margin). So ``Σpayout / Σwager`` sits just under target by
  construction; the tolerance must absorb that. This is an alert for a BROKEN paytable /
  mis-seeded config, not a precise EV assertion (that is the harness's job).
* Early samples are noisy. ``min_samples`` is a floor below which the monitor stays quiet —
  a handful of unlucky bets must not page anyone.

Target defaults to ``1 - default_config(game_id).edge`` but is injectable (DB GameConfig is
authoritative at runtime). Pure in-app accounting over engine outputs — the engine itself
never imports this (purity preserved).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.registry import default_config

# Default gross-drift band and sample floor. CONFIG, not magic literals buried in logic.
DEFAULT_TOLERANCE = 0.05  # 5 percentage points of RTP
DEFAULT_MIN_SAMPLES = 100


@dataclass(frozen=True)
class RtpDrift:
    """A fired drift alert — the snapshot a human reviews / an alert pipeline consumes."""

    game_id: str
    target: float
    measured_rtp: float
    samples: int
    tolerance: float

    @property
    def delta(self) -> float:
        """Signed drift: measured − target (negative = paying below target)."""
        return self.measured_rtp - self.target


@dataclass
class RtpMonitor:
    """Accumulates realized (wager, payout) for one game and flags drift past tolerance."""

    game_id: str
    target: float
    tolerance: float = DEFAULT_TOLERANCE
    min_samples: int = DEFAULT_MIN_SAMPLES
    _wager_minor: int = field(default=0, init=False)
    _payout_minor: int = field(default=0, init=False)
    _samples: int = field(default=0, init=False)

    def observe(self, *, stake_minor: int, payout_minor: int) -> None:
        self._wager_minor += stake_minor
        self._payout_minor += payout_minor
        self._samples += 1

    @property
    def samples(self) -> int:
        return self._samples

    @property
    def measured_rtp(self) -> float:
        if self._wager_minor == 0:
            return 0.0
        return self._payout_minor / self._wager_minor

    @property
    def is_drifting(self) -> bool:
        if self._samples < self.min_samples:
            return False
        return abs(self.measured_rtp - self.target) > self.tolerance

    def check(self) -> RtpDrift | None:
        """Return an :class:`RtpDrift` iff past the sample floor AND outside tolerance."""
        if not self.is_drifting:
            return None
        return RtpDrift(
            game_id=self.game_id,
            target=self.target,
            measured_rtp=self.measured_rtp,
            samples=self._samples,
            tolerance=self.tolerance,
        )


def monitor_for(
    game_id: str,
    monitors: dict[str, RtpMonitor],
    *,
    target: float | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> RtpMonitor:
    """Get-or-create the per-game monitor in ``monitors``; default target is ``1 - edge``."""
    mon = monitors.get(game_id)
    if mon is None:
        resolved = target if target is not None else 1.0 - default_config(game_id).edge
        mon = RtpMonitor(
            game_id=game_id,
            target=resolved,
            tolerance=tolerance,
            min_samples=min_samples,
        )
        monitors[game_id] = mon
    return mon


# Process-wide per-game monitor set; ``observe_settled_bet`` writes here unless a caller
# injects its own (tests do, for isolation).
MONITORS: dict[str, RtpMonitor] = {}
