from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class LabelRow:
    """A point-in-time ML training candidate row with causal labels per §13.1."""

    candidate_id: str = ""
    decision_ns: int = 0
    horizon_ns: int = 0
    features_hash: str = ""
    side: str = "BUY"
    entry_price: Decimal = Decimal("0")
    exit_price: Decimal = Decimal("0")
    net_return: Decimal = Decimal("0")
    is_profitable: bool = False
    features: dict[str, float] = field(default_factory=dict)
    dropped_reason: str | None = None


class LabelBuilder:
    """Builds causal ML training rows and binary profitability labels
    at candidate decision points.
    """

    @staticmethod
    def build(
        candidates: list[dict[str, Any]],
        market_prices: Mapping[int, Decimal | float | tuple[Decimal | float, Decimal | float]],
        horizon_ns: int = 5_000_000_000,
        taker_fee_rate: Decimal = Decimal("0.00055"),
        funding_rate: Decimal = Decimal("0"),
        half_spread_ticks: Decimal = Decimal("1"),
        latency_ns: int = 20_000_000,
        max_staleness_ns: int | None = None,
    ) -> list[LabelRow]:
        sorted_times = sorted(market_prices.keys())
        rows: list[LabelRow] = []
        staleness_limit = (
            max_staleness_ns if max_staleness_ns is not None else max(latency_ns * 2, 2_000_000_000)
        )

        def get_price_at(target_ns: int) -> tuple[Decimal, Decimal] | None:
            """Finds closest valid quote at or immediately preceding target_ns."""
            if not sorted_times:
                return None
            idx = -1
            for i, t in enumerate(sorted_times):
                if t <= target_ns:
                    idx = i
                else:
                    break
            if idx < 0:
                return None
            if (target_ns - sorted_times[idx]) > staleness_limit:
                return None
            val = market_prices[sorted_times[idx]]
            if isinstance(val, tuple):
                return (Decimal(str(val[0])), Decimal(str(val[1])))
            mid = Decimal(str(val))
            return (mid - half_spread_ticks, mid + half_spread_ticks)

        for cand in candidates:
            cand_id = str(cand.get("id", cand.get("intent_id", "")))
            decision_ns = int(cand.get("decision_ns", cand.get("available_ns", 0)))
            side = str(cand.get("side", "BUY")).upper()
            features = dict(cand.get("features", {}))
            f_hash = str(cand.get("features_hash", ""))

            entry_ns = decision_ns + latency_ns
            exit_ns = decision_ns + horizon_ns

            entry_quote = get_price_at(entry_ns)
            exit_quote = get_price_at(exit_ns)

            if entry_quote is None:
                rows.append(
                    LabelRow(
                        candidate_id=cand_id,
                        decision_ns=decision_ns,
                        horizon_ns=horizon_ns,
                        features_hash=f_hash,
                        side=side,
                        entry_price=Decimal("0"),
                        exit_price=Decimal("0"),
                        net_return=Decimal("0"),
                        is_profitable=False,
                        features=features,
                        dropped_reason="DROPPED_UNAVAILABLE_ENTRY_QUOTE",
                    )
                )
                continue

            if exit_quote is None:
                rows.append(
                    LabelRow(
                        candidate_id=cand_id,
                        decision_ns=decision_ns,
                        horizon_ns=horizon_ns,
                        features_hash=f_hash,
                        side=side,
                        entry_price=Decimal("0"),
                        exit_price=Decimal("0"),
                        net_return=Decimal("0"),
                        is_profitable=False,
                        features=features,
                        dropped_reason="DROPPED_UNAVAILABLE_EXIT_QUOTE",
                    )
                )
                continue

            entry_bid, entry_ask = entry_quote
            exit_bid, exit_ask = exit_quote

            # Conservative marketable entry and exit at horizon (§13.1):
            # BUY: entry at ask, exit at bid
            # SELL: entry at bid, exit at ask
            if side == "BUY":
                entry_price = entry_ask
                exit_price = exit_bid
                gross_diff = exit_price - entry_price
            else:
                entry_price = entry_bid
                exit_price = exit_ask
                gross_diff = entry_price - exit_price

            entry_fee = entry_price * taker_fee_rate
            exit_fee = exit_price * taker_fee_rate
            funding_cost = (
                (entry_price * funding_rate) if side == "BUY" else (-entry_price * funding_rate)
            )

            net_return = gross_diff - entry_fee - exit_fee - funding_cost
            is_profitable = net_return > Decimal("0")

            # Filter out non-feature metadata and forbidden target leakages (§13.1)
            sanitized_features = {
                k: float(v)
                for k, v in features.items()
                if not (
                    k.startswith("label_")
                    or k.startswith("target_")
                    or k in ("id", "pnl", "trade_pnl", "exit_price", "decision_ns")
                )
            }

            rows.append(
                LabelRow(
                    candidate_id=cand_id,
                    decision_ns=decision_ns,
                    horizon_ns=horizon_ns,
                    features_hash=f_hash,
                    side=side,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    net_return=net_return,
                    is_profitable=is_profitable,
                    features=sanitized_features,
                    dropped_reason=None,
                )
            )

        return rows
