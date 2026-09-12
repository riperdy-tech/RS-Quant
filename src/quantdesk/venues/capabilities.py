from dataclasses import dataclass
from enum import StrEnum


class Capability(StrEnum):
    OHLCV = "OHLCV"
    TRADES = "TRADES"
    BBO = "BBO"
    L2 = "L2"
    MARK = "MARK"
    FUNDING = "FUNDING"
    RECEIVE_TIMESTAMPS = "RECEIVE_TIMESTAMPS"


class UnsupportedCapability(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VenueCapabilities:
    order_types: frozenset[str] = frozenset()
    time_in_force: frozenset[str] = frozenset()
    post_only: bool = False
    reduce_only: bool = False
    native_stops: bool = False
    amendments: bool = False
    client_ids: bool = False
    position_modes: frozenset[str] = frozenset()
    margin_modes: frozenset[str] = frozenset()
    sequence_contracts: frozenset[str] = frozenset()
    snapshot_recovery: bool = False
    rate_limits: tuple[tuple[str, int, int], ...] = ()
    dead_man_cancellation: bool = False

    def require_order(self, order_type: str, tif: str, *, reduce_only: bool = False) -> None:
        if (
            order_type not in self.order_types
            or tif not in self.time_in_force
            or (reduce_only and not self.reduce_only)
            or (tif == "POST_ONLY" and not self.post_only)
            or (order_type == "STOP" and not self.native_stops)
        ):
            raise UnsupportedCapability("unsupported venue order capability")
