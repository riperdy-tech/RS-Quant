import heapq
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(order=True)
class ScheduledEvent:
    time_ns: int
    seq: int
    callback: Callable[[], None] = field(compare=False)
    description: str = field(compare=False, default="")


class VirtualTimeline:
    def __init__(self) -> None:
        self.current_time_ns: int = 0
        self.events: list[ScheduledEvent] = []
        self._seq = 0

    def schedule(
        self, delay_ns: int, callback: Callable[[], None], description: str = ""
    ) -> None:
        self._seq += 1
        heapq.heappush(
            self.events,
            ScheduledEvent(
                self.current_time_ns + delay_ns, self._seq, callback, description
            ),
        )

    def schedule_absolute(
        self, time_ns: int, callback: Callable[[], None], description: str = ""
    ) -> None:
        self._seq += 1
        if time_ns < self.current_time_ns:
            time_ns = self.current_time_ns
        heapq.heappush(
            self.events,
            ScheduledEvent(time_ns, self._seq, callback, description),
        )

    def next_event_time(self) -> int | None:
        if not self.events:
            return None
        return self.events[0].time_ns

    def advance_to(self, until_ns: int) -> int:
        executed = 0
        while self.events and self.events[0].time_ns <= until_ns:
            event = heapq.heappop(self.events)
            self.current_time_ns = event.time_ns
            event.callback()
            executed += 1
        self.current_time_ns = max(self.current_time_ns, until_ns)
        return executed
