"""Topic-specific V3 stream lifecycle and sequence contract."""

from dataclasses import dataclass

from quantdesk.venues.bitget_uta.normalize import integer


@dataclass
class BitgetBookSequence:
    name: str = "bitget-uta-v3-books-2026-09-13"
    first_update: bool = True

    def validate(
        self, previous: str | None, current: str | None, prior: str | None, snapshot: bool
    ) -> str | None:
        try:
            current_seq = integer(current)
            if current_seq == 0:
                return "INVALID_SEQUENCE"
            if snapshot:
                self.first_update = True
                return None
            before, prior_seq = integer(previous), integer(prior)
            if prior_seq == 0:
                return "VENUE_SEQUENCE_RESET"
            if self.first_update:
                if not prior_seq <= before <= current_seq:
                    return "SNAPSHOT_ALIGNMENT_FAILED"
            elif prior_seq != before or current_seq <= before:
                return "SEQUENCE_CHAIN_BROKEN"
            if current_seq <= prior_seq:
                return "OUT_OF_ORDER_SEQUENCE"
            self.first_update = False
            return None
        except (TypeError, ValueError):
            return "MALFORMED_SEQUENCE"
