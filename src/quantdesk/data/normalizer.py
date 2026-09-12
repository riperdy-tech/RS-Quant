import json
from dataclasses import dataclass

from quantdesk.core.events import IncomingEvent, Trade, canonical_bytes
from quantdesk.core.types import Side
from quantdesk.persistence.manifests import RawRef
from quantdesk.persistence.raw_journal import RawFrame
from quantdesk.venues.instruments import InstrumentSpec


@dataclass(frozen=True, slots=True)
class Normalizer:
    """Generic declared trade schema. Venue adapters retain their own field/side mappings."""

    spec: InstrumentSpec
    revision: str = "generic-trade-v1"

    def trade(
        self, frame: RawFrame, ref: RawRef, *, run_id: str, available_ns: int
    ) -> IncomingEvent:
        if frame.private:
            raise ValueError("public trade normalizer rejects private frames")
        value = json.loads(frame.payload)
        side = value.get("aggressor_side")
        payload = Trade(
            str(value["id"]),
            self.spec.price_to_ticks(value["price"]),
            self.spec.quantity_to_lots(value["size"]),
            Side(side) if side is not None else None,
            (),
        )
        timestamp = value["event_ns"]
        if type(timestamp) is not int and not (
            isinstance(timestamp, str) and timestamp.isdecimal()
        ):
            raise ValueError("integer event_ns required")
        return IncomingEvent(
            "Trade",
            1,
            run_id,
            None,
            frame.venue,
            frame.environment,
            self.spec.instrument_id,
            "trades",
            frame.connection_epoch,
            str(value["id"]),
            None,
            int(timestamp),
            None,
            frame.receive_wall_ns,
            frame.receive_monotonic_ns,
            available_ns,
            None,
            f"{ref}",
            str(ref),
            self.revision,
            canonical_bytes(payload),
        )
