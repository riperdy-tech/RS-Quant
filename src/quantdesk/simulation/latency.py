from dataclasses import dataclass


@dataclass(frozen=True)
class LatencyProfile:
    public_feed_delivery_ns: int = 20_000_000
    computation_delay_ns: int = 1_000_000
    submit_outbound_ns: int = 20_000_000
    venue_handling_ns: int = 2_000_000
    private_ack_ns: int = 20_000_000
    fill_report_delivery_ns: int = 20_000_000
    cancel_outbound_ns: int = 20_000_000
    cancel_handling_ns: int = 2_000_000
    throttle_delay_ns: int = 0

    @classmethod
    def synthetic_defaults(cls) -> "LatencyProfile":
        return cls()
        
    @classmethod
    def stress_profile(cls, multiplier: int = 2) -> "LatencyProfile":
        return cls(
            public_feed_delivery_ns=20_000_000 * multiplier,
            computation_delay_ns=1_000_000,
            submit_outbound_ns=20_000_000 * multiplier,
            venue_handling_ns=2_000_000 * multiplier,
            private_ack_ns=20_000_000 * multiplier,
            fill_report_delivery_ns=20_000_000 * multiplier,
            cancel_outbound_ns=20_000_000 * multiplier,
            cancel_handling_ns=2_000_000 * multiplier,
            throttle_delay_ns=0
        )
