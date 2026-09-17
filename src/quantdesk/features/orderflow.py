from collections import deque
from typing import Any


def l1_imbalance(best_bid_size: float, best_ask_size: float) -> float | None:
    denominator = best_bid_size + best_ask_size
    if denominator == 0:
        return None
    return (best_bid_size - best_ask_size) / denominator


def microprice(
    best_bid: float, best_ask: float, best_bid_size: float, best_ask_size: float
) -> float | None:
    denominator = best_bid_size + best_ask_size
    if denominator == 0:
        return None
    return (best_ask * best_bid_size + best_bid * best_ask_size) / denominator


def depth_imbalance(
    bids: list[tuple[float, float]], asks: list[tuple[float, float]], k: int
) -> float | None:
    if len(bids) < k or len(asks) < k:
        return None
    bid_sum = sum(size for _, size in bids[:k])
    ask_sum = sum(size for _, size in asks[:k])
    denominator = bid_sum + ask_sum
    if denominator == 0:
        return None
    return (bid_sum - ask_sum) / denominator


class CVD:
    def __init__(self) -> None:
        self.value = 0.0

    def update(self, volume: float, aggressor_side: str | None) -> float:
        if aggressor_side == "BUY":
            self.value += volume
        elif aggressor_side == "SELL":
            self.value -= volume
        return self.value

    def reset(self) -> None:
        self.value = 0.0


class L1OFI:
    def __init__(self) -> None:
        self.prev_bid: float | None = None
        self.prev_ask: float | None = None
        self.prev_bid_size: float | None = None
        self.prev_ask_size: float | None = None

    def update(
        self, bid: float, ask: float, bid_size: float, ask_size: float
    ) -> float | None:
        if self.prev_bid is None or self.prev_ask is None:
            self.prev_bid = bid
            self.prev_ask = ask
            self.prev_bid_size = bid_size
            self.prev_ask_size = ask_size
            return None

        bid_change = 0.0
        if bid > self.prev_bid:
            bid_change = bid_size
        elif bid == self.prev_bid and self.prev_bid_size is not None:
            bid_change = bid_size - self.prev_bid_size
        elif self.prev_bid_size is not None:
            bid_change = -self.prev_bid_size

        ask_change = 0.0
        if ask < self.prev_ask:
            ask_change = ask_size
        elif ask == self.prev_ask and self.prev_ask_size is not None:
            ask_change = ask_size - self.prev_ask_size
        elif self.prev_ask_size is not None:
            ask_change = -self.prev_ask_size

        self.prev_bid = bid
        self.prev_ask = ask
        self.prev_bid_size = bid_size
        self.prev_ask_size = ask_size

        return bid_change - ask_change


class RankedMLOFI:
    def __init__(self, ranks: int = 5) -> None:
        self.ranks = ranks
        self.prev_bids: list[tuple[float, float]] = []
        self.prev_asks: list[tuple[float, float]] = []
        self.window_ofi: deque[tuple[int, float]] = deque()
        self.window_depth: deque[tuple[int, float]] = deque()
        self.trailing_mean_depth: float | None = None

    def _level_change(
        self, price: float, size: float, prev_price: float, prev_size: float, is_bid: bool
    ) -> float:
        if is_bid:
            if price > prev_price:
                return size
            if price == prev_price:
                return size - prev_size
            return -prev_size
        else:
            if price < prev_price:
                return size
            if price == prev_price:
                return size - prev_size
            return -prev_size

    def update(
        self,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
        timestamp: int,
    ) -> float | None:
        if len(bids) < self.ranks or len(asks) < self.ranks:
            return None
        if not self.prev_bids:
            self.prev_bids = bids[: self.ranks]
            self.prev_asks = asks[: self.ranks]
            return None

        mlofi = 0.0
        current_depth = 0.0
        for k in range(self.ranks):
            current_depth += bids[k][1] + asks[k][1]
            b_change = self._level_change(
                bids[k][0], bids[k][1], self.prev_bids[k][0], self.prev_bids[k][1], True
            )
            a_change = self._level_change(
                asks[k][0], asks[k][1], self.prev_asks[k][0], self.prev_asks[k][1], False
            )
            mlofi += (1.0 / (k + 1)) * (b_change - a_change)

        self.prev_bids = bids[: self.ranks]
        self.prev_asks = asks[: self.ranks]

        # 1-second rolling window logic
        self.window_ofi.append((timestamp, mlofi))
        self.window_depth.append((timestamp, current_depth))

        # Prune older than 1 second (10^9 ns)
        cutoff = timestamp - 1_000_000_000
        while self.window_ofi and self.window_ofi[0][0] < cutoff:
            self.window_ofi.popleft()
        while self.window_depth and self.window_depth[0][0] < cutoff:
            self.window_depth.popleft()

        if not self.window_depth:
            return None

        mean_depth = sum(d for _, d in self.window_depth) / len(self.window_depth)
        self.trailing_mean_depth = mean_depth

        sum_ofi = sum(o for _, o in self.window_ofi)
        if mean_depth == 0:
            return None
        return sum_ofi / mean_depth


class TradeCluster:
    def __init__(self, time_limit_ns: int = 50_000_000) -> None:
        self.time_limit_ns = time_limit_ns
        self.aggressor: str | None = None
        self.total_size = 0.0
        self.start_time: int | None = None
        self.end_time: int | None = None
        self.prices: set[float] = set()
        self.member_ids: list[str] = []
        # Trailing past-only 5-minute clusters: (completed_ns, total_size)
        self.cluster_history_5m: deque[tuple[int, float]] = deque()

    def update(
        self,
        price: float,
        size: float,
        aggressor: str,
        timestamp_ns: int,
        trade_id: str | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        if self.aggressor is None or self.end_time is None:
            self._start(price, size, aggressor, timestamp_ns, trade_id)
            return False, None

        if (
            aggressor == self.aggressor
            and (timestamp_ns - self.end_time) <= self.time_limit_ns
        ):
            self.total_size += size
            self.end_time = timestamp_ns
            self.prices.add(price)
            if trade_id:
                self.member_ids.append(trade_id)
            return False, None
        else:
            cluster = self._flush()
            self._start(price, size, aggressor, timestamp_ns, trade_id)
            return True, cluster

    def _start(
        self,
        price: float,
        size: float,
        aggressor: str,
        timestamp_ns: int,
        trade_id: str | None = None,
    ) -> None:
        self.aggressor = aggressor
        self.total_size = size
        self.start_time = timestamp_ns
        self.end_time = timestamp_ns
        self.prices = {price}
        self.member_ids = [trade_id] if trade_id else []

    def _flush(self) -> dict[str, Any] | None:
        if self.aggressor is None or self.end_time is None or self.start_time is None:
            return None

        # Check 95th percentile against past-only history before recording this cluster
        threshold_95 = self.get_95th_percentile_size(self.end_time)
        distinct_count = len(self.prices)
        is_sweep = distinct_count >= 3 and self.total_size >= threshold_95

        cluster = {
            "aggressor": self.aggressor,
            "total_size": self.total_size,
            "span_ns": self.end_time - self.start_time,
            "prices": distinct_count,
            "distinct_prices": sorted(self.prices),
            "min_price": min(self.prices),
            "max_price": max(self.prices),
            "member_ids": tuple(self.member_ids),
            "completed_ns": self.end_time,
            "is_sweep": is_sweep,
            "threshold_95": threshold_95,
        }

        # Record in 5-minute past history
        self.cluster_history_5m.append((self.end_time, self.total_size))
        cutoff = self.end_time - 300_000_000_000  # 5 minutes
        while self.cluster_history_5m and self.cluster_history_5m[0][0] < cutoff:
            self.cluster_history_5m.popleft()

        return cluster

    def get_95th_percentile_size(self, current_time_ns: int) -> float:
        """Compute past-only 95th percentile cluster size within trailing 5 minutes."""
        cutoff = current_time_ns - 300_000_000_000
        while self.cluster_history_5m and self.cluster_history_5m[0][0] < cutoff:
            self.cluster_history_5m.popleft()

        if not self.cluster_history_5m:
            return 0.0

        sizes = sorted(s for _, s in self.cluster_history_5m)
        idx = int(0.95 * (len(sizes) - 1))
        return sizes[idx]
