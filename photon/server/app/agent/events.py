"""Real-time trace events for one agent turn.

`answer_question()` only ever returned its finished `tool_trace` — which
is enough to show what happened after the fact, but nothing at all while
it's happening. A live call needs the opposite: which tool is running
*right now* and how long the turn has been going. This module is that
seam.

Deliberately a plain callable sink, not a queue/stream/websocket: the
agent package must stay transport-free (CLAUDE.md Section 5), so the loop
knows nothing about who is listening. The router wraps a queue around it
for SSE; a test can pass `list.append`; passing nothing disables tracing
entirely at near-zero cost.

Every event carries:
  type  — dotted stage name, e.g. "tool.start"
  t     — ms since the turn began, so a client can lay events on a timeline
          without trusting its own clock or the network's jitter
  seq   — monotonic counter, so out-of-order/duplicated delivery (LiveKit
          data packets are reliable but the browser may still re-render)
          can be ordered and de-duplicated
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

import structlog

log = structlog.get_logger()

EventSink = Callable[[dict[str, Any]], None]


class TurnTracer:
    """Stamps and forwards events for a single turn. A tracer with no sink
    is a no-op, so the loop can emit unconditionally."""

    def __init__(self, sink: Optional[EventSink] = None):
        self._sink = sink
        self._start = time.monotonic()
        self._seq = 0

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

    def emit(self, type: str, **fields: Any) -> None:
        if self._sink is None:
            return
        self._seq += 1
        event = {"type": type, "t": self.elapsed_ms, "seq": self._seq, **fields}
        try:
            self._sink(event)
        except Exception as exc:  # noqa: BLE001 - tracing must never break the turn it traces
            log.warning("agent.trace_sink_error", type=type, error=str(exc))
