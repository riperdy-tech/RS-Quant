from typing import Any, Protocol, runtime_checkable

from quantdesk.core.events import Envelope, StrategyIntent


@runtime_checkable
class Strategy(Protocol):
    def on_event(self, event: Envelope, context: Any) -> tuple[StrategyIntent, ...]: ...
