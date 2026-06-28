"""A minimal, dependency-free Prometheus-style metrics registry.

The stack ships no ``prometheus_client`` dependency (checked: ``app/pyproject.toml``),
and pulling one in for three metrics would be unwarranted weight. This is the
smallest in-house registry that gives the two behaviours S39 needs — labelled
counters/histograms that INCREMENT on a settled bet, and a Prometheus text
``render()`` for an eventual ``/metrics`` scrape (a documented seam, not wired here).

Not a general Prometheus reimplementation: no exemplars, no push, no concurrency
primitives (the bet loop is single-process-per-partition; observation is in-band).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A label set, order-independent, hashable — the child-series key.
LabelKey = tuple[tuple[str, str], ...]


def _label_key(labels: dict[str, str]) -> LabelKey:
    return tuple(sorted(labels.items()))


def _format_labels(key: LabelKey) -> str:
    if not key:
        return ""
    inner = ",".join(f'{name}="{value}"' for name, value in key)
    return "{" + inner + "}"


@dataclass
class Counter:
    """A monotonically increasing labelled counter."""

    name: str
    help: str
    labelnames: tuple[str, ...] = ()
    _series: dict[LabelKey, float] = field(default_factory=dict)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            raise ValueError(f"counter {self.name} cannot decrease (got {amount})")
        key = _label_key(labels)
        self._series[key] = self._series.get(key, 0.0) + amount

    def value(self, **labels: str) -> float:
        return self._series.get(_label_key(labels), 0.0)

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        for key, total in sorted(self._series.items()):
            lines.append(f"{self.name}{_format_labels(key)} {_render_number(total)}")
        return lines


@dataclass
class _HistSeries:
    sum: float = 0.0
    count: int = 0
    buckets: dict[float, int] = field(default_factory=dict)


@dataclass
class Histogram:
    """A labelled histogram — cumulative buckets + ``_sum``/``_count`` (latency in seconds)."""

    name: str
    help: str
    labelnames: tuple[str, ...] = ()
    buckets: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
    _series: dict[LabelKey, _HistSeries] = field(default_factory=dict)

    def observe(self, value: float, **labels: str) -> None:
        key = _label_key(labels)
        series = self._series.get(key)
        if series is None:
            series = _HistSeries(buckets={b: 0 for b in self.buckets})
            self._series[key] = series
        series.sum += value
        series.count += 1
        for bound in self.buckets:
            if value <= bound:
                series.buckets[bound] += 1

    def count(self, **labels: str) -> int:
        series = self._series.get(_label_key(labels))
        return series.count if series is not None else 0

    def sum(self, **labels: str) -> float:
        series = self._series.get(_label_key(labels))
        return series.sum if series is not None else 0.0

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        for key, series in sorted(self._series.items()):
            label_pairs = dict(key)
            for bound in self.buckets:
                le = {**label_pairs, "le": _render_number(bound)}
                lines.append(
                    f"{self.name}_bucket{_format_labels(_label_key(le))} {series.buckets[bound]}"
                )
            inf = {**label_pairs, "le": "+Inf"}
            lines.append(f"{self.name}_bucket{_format_labels(_label_key(inf))} {series.count}")
            lines.append(f"{self.name}_sum{_format_labels(key)} {_render_number(series.sum)}")
            lines.append(f"{self.name}_count{_format_labels(key)} {series.count}")
        return lines


def _render_number(value: float) -> str:
    """Integers render without a trailing ``.0`` (Prometheus accepts both; keeps text tidy)."""
    if value == int(value):
        return str(int(value))
    return repr(value)


# Metric names (the registry keys + exported series names).
BETS_TOTAL = "bets_total"
PAYOUTS_MINOR_TOTAL = "payouts_minor_total"
BET_LATENCY_SECONDS = "bet_latency_seconds"


class MetricsRegistry:
    """Holds the casino's metric instances and renders the exposition text.

    A fresh ``MetricsRegistry()`` is fully isolated — tests construct their own so
    assertions never depend on module-global accumulation across the suite.
    """

    def __init__(self) -> None:
        self._counters: dict[str, Counter] = {
            BETS_TOTAL: Counter(
                BETS_TOTAL, "Settled bets, by game and status.", ("game", "status")
            ),
            PAYOUTS_MINOR_TOTAL: Counter(
                PAYOUTS_MINOR_TOTAL, "Total payout in minor units, by game.", ("game",)
            ),
        }
        self._histograms: dict[str, Histogram] = {
            BET_LATENCY_SECONDS: Histogram(
                BET_LATENCY_SECONDS, "Bet settlement latency in seconds, by game.", ("game",)
            ),
        }

    def counter(self, name: str) -> Counter:
        return self._counters[name]

    def histogram(self, name: str) -> Histogram:
        return self._histograms[name]

    def render(self) -> str:
        lines: list[str] = []
        for counter in self._counters.values():
            lines.extend(counter.render())
        for histogram in self._histograms.values():
            lines.extend(histogram.render())
        return "\n".join(lines) + "\n"


# The process-wide default registry. ``observe_settled_bet`` writes here unless a
# caller injects its own (tests do, for isolation).
REGISTRY = MetricsRegistry()
