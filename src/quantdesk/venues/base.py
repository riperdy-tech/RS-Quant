from typing import Protocol

from quantdesk.core.events import IncomingEvent
from quantdesk.venues.capabilities import VenueCapabilities


class PublicVenueAdapter(Protocol):
    capabilities: VenueCapabilities

    async def connect_public(self) -> None: ...

    async def receive(self) -> IncomingEvent: ...

    async def close(self) -> None: ...
