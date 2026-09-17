from __future__ import annotations

import contextvars
import secrets
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from quantdesk.observability.metrics import metrics_collector

_active_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "active_trace_id", default=None
)
_active_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "active_span_id", default=None
)


@dataclass
class TraceSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    start_ns: int
    end_ns: int = 0
    tags: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        if self.end_ns == 0:
            return round((time.time_ns() - self.start_ns) / 1_000_000.0, 4)
        return round((self.end_ns - self.start_ns) / 1_000_000.0, 4)


class CorrelationTracer:
    """Manages correlation IDs and structured lifecycle spans (§16, §16.2)."""

    def __init__(self, max_retained_spans: int = 1_000) -> None:
        self.max_spans = max_retained_spans
        self.completed_spans: list[TraceSpan] = []

    def new_trace_id(self) -> str:
        """Generates a cryptographically strong correlation trace ID."""
        return secrets.token_hex(16)

    def new_span_id(self) -> str:
        return secrets.token_hex(8)

    def current_trace_id(self) -> str:
        tid = _active_trace_id.get()
        if not tid:
            tid = self.new_trace_id()
            _active_trace_id.set(tid)
        return tid

    @contextmanager
    def span(
        self,
        name: str,
        tags: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> Generator[TraceSpan, None, None]:
        """Context manager creating a correlated execution span."""
        tid = trace_id or _active_trace_id.get() or self.new_trace_id()
        parent_sid = _active_span_id.get()
        sid = self.new_span_id()

        token_trace = _active_trace_id.set(tid)
        token_span = _active_span_id.set(sid)

        span_obj = TraceSpan(
            trace_id=tid,
            span_id=sid,
            parent_span_id=parent_sid,
            name=name,
            start_ns=time.time_ns(),
            tags=dict(tags or {}),
        )

        try:
            yield span_obj
        finally:
            span_obj.end_ns = time.time_ns()
            # Record stage latency to global metrics collector
            metrics_collector.record_stage_latency(name, span_obj.duration_ms)

            self.completed_spans.append(span_obj)
            if len(self.completed_spans) > self.max_spans:
                self.completed_spans.pop(0)

            _active_span_id.reset(token_span)
            _active_trace_id.reset(token_trace)

    def get_spans_for_trace(self, trace_id: str) -> list[TraceSpan]:
        """Returns all recorded spans belonging to a trace ID in chronological order."""
        return [s for s in self.completed_spans if s.trace_id == trace_id]


tracer = CorrelationTracer()
