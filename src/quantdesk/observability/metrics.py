from __future__ import annotations

import gc
import math
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass
class LatencyStats:
    count: int = 0
    min_ms: float = 0.0
    max_ms: float = 0.0
    mean_ms: float = 0.0
    p50_ms: float = 0.0
    p90_ms: float = 0.0
    p99_ms: float = 0.0


class StageMetrics:
    """Tracks latency percentiles and throughput across execution stages (§16, §16.2)."""

    def __init__(self, max_samples_per_stage: int = 10_000) -> None:
        self.max_samples = max_samples_per_stage
        self.samples: dict[str, deque[float]] = {}
        self.event_timestamps: deque[float] = deque(maxlen=20_000)
        self.queue_depth: int = 0
        self.stage_counters: dict[str, int] = {}

    def record_stage_latency(self, stage: str, duration_ms: float) -> None:
        """Records the latency duration in milliseconds for a processing stage."""
        if stage not in self.samples:
            self.samples[stage] = deque(maxlen=self.max_samples)
            self.stage_counters[stage] = 0
        self.samples[stage].append(duration_ms)
        self.stage_counters[stage] += 1

    def record_event(self) -> None:
        """Records an event processing occurrence for throughput calculation."""
        self.event_timestamps.append(time.monotonic())

    def set_queue_depth(self, depth: int) -> None:
        """Updates the current backlog queue depth."""
        self.queue_depth = max(0, depth)

    def throughput_events_per_second(self, window_seconds: float = 5.0) -> float:
        """Calculates moving event processing throughput per second."""
        now = time.monotonic()
        cutoff = now - window_seconds
        recent = sum(1 for ts in self.event_timestamps if ts >= cutoff)
        return round(recent / window_seconds, 2)

    def calculate_percentiles(self, stage: str) -> LatencyStats:
        """Computes statistical metrics and P50, P90, P99 percentiles for a stage."""
        data = self.samples.get(stage)
        if not data:
            return LatencyStats()

        sorted_data = sorted(data)
        n = len(sorted_data)
        min_v = sorted_data[0]
        max_v = sorted_data[-1]
        mean_v = sum(sorted_data) / n

        def get_p(pct: float) -> float:
            k = (n - 1) * pct
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return sorted_data[int(k)]
            return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)

        return LatencyStats(
            count=n,
            min_ms=round(min_v, 4),
            max_ms=round(max_v, 4),
            mean_ms=round(mean_v, 4),
            p50_ms=round(get_p(0.50), 4),
            p90_ms=round(get_p(0.90), 4),
            p99_ms=round(get_p(0.99), 4),
        )

    def get_memory_stats(self) -> dict[str, Any]:
        """Collects memory and garbage collection telemetry without external dependencies."""
        gc_counts = gc.get_count()
        stats: dict[str, Any] = {
            "gc_gen0_collections": gc_counts[0],
            "gc_gen1_collections": gc_counts[1],
            "gc_gen2_collections": gc_counts[2],
            "pid": os.getpid(),
        }
        # On Windows or Linux, attempt to read process resident memory via ctypes or os
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            handle = ctypes.windll.kernel32.OpenProcess(0x0410, False, os.getpid())
            if handle:
                try:
                    counters = PROCESS_MEMORY_COUNTERS()
                    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                    if ctypes.windll.psapi.GetProcessMemoryInfo(
                        handle, ctypes.byref(counters), counters.cb
                    ):
                        rss_mb = counters.WorkingSetSize / (1024 * 1024)
                        peak_mb = counters.PeakWorkingSetSize / (1024 * 1024)
                        stats["rss_mb"] = round(rss_mb, 2)
                        stats["peak_rss_mb"] = round(peak_mb, 2)
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            stats["rss_mb"] = 0.0

        return stats

    def summary(self) -> dict[str, Any]:
        """Returns full telemetry summary across all tracked stages and resources."""
        stages_summary = {
            stage: {
                "count": stats.count,
                "p50_ms": stats.p50_ms,
                "p90_ms": stats.p90_ms,
                "p99_ms": stats.p99_ms,
                "mean_ms": stats.mean_ms,
                "max_ms": stats.max_ms,
            }
            for stage in self.samples
            for stats in [self.calculate_percentiles(stage)]
        }
        mem = self.get_memory_stats()
        throughput = self.throughput_events_per_second()
        return {
            "stages": stages_summary,
            "raw_to_decision": stages_summary.get("raw_to_decision", {}),
            "reducer": stages_summary.get("reducer", {}),
            "queue_depth": self.queue_depth,
            "throughput_eps": throughput,
            "throughput_events_per_sec": throughput,
            "memory": mem,
            "memory_rss_mb": mem.get("rss_mb", 0.0),
        }

    def get_summary(self) -> dict[str, Any]:
        return self.summary()


metrics_collector = StageMetrics()
