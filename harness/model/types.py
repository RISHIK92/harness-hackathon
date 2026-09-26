"""Normalized model types shared by every adapter."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolCall:
    name: str
    args: dict
    id: str = ""


@dataclass
class ModelReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    stop_reason: str = ""
    model: str = ""
    latency_ms: int = 0
    raw: dict = field(default_factory=dict)


class ProviderError(Exception):
    """Transport or API failure. `status` is None for transport errors."""

    def __init__(self, msg: str, status: int | None = None,
                 retry_after: float | None = None) -> None:
        super().__init__(msg)
        self.status = status
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        if self.status is None:
            return True                      # transport: worth one more try
        return self.status == 429 or 500 <= self.status < 600
