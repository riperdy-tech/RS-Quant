import math
from collections import deque


class SMA:
    def __init__(self, period: int) -> None:
        self.period = period
        self.values: deque[float] = deque(maxlen=period)
        self.total = 0.0

    def update(self, value: float) -> float | None:
        if len(self.values) == self.period:
            self.total -= self.values[0]
        self.values.append(value)
        self.total += value
        if len(self.values) == self.period:
            return self.total / self.period
        return None

class EMA:
    def __init__(self, period: int):
        self.period = period
        self.alpha = 2.0 / (period + 1.0)
        self.sma = SMA(period)
        self.value: float | None = None

    def update(self, value: float) -> float | None:
        if self.value is None:
            sma_val = self.sma.update(value)
            if sma_val is not None:
                self.value = sma_val
        else:
            self.value = (value - self.value) * self.alpha + self.value
        return self.value

class WilderMovingAverage:
    def __init__(self, period: int):
        self.period = period
        self.alpha = 1.0 / period
        self.sma = SMA(period)
        self.value: float | None = None

    def update(self, value: float) -> float | None:
        if self.value is None:
            sma_val = self.sma.update(value)
            if sma_val is not None:
                self.value = sma_val
        else:
            self.value = (value - self.value) * self.alpha + self.value
        return self.value

class RSI:
    def __init__(self, period: int = 14):
        self.period = period
        self.prev_close: float | None = None
        self.avg_gain = WilderMovingAverage(period)
        self.avg_loss = WilderMovingAverage(period)
        self.value: float | None = None

    def update(self, close: float) -> float | None:
        if self.prev_close is None:
            self.prev_close = close
            return None
        
        diff = close - self.prev_close
        self.prev_close = close
        
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        
        avg_g = self.avg_gain.update(gain)
        avg_l = self.avg_loss.update(loss)
        
        if avg_g is not None and avg_l is not None:
            if avg_l == 0:
                self.value = 100.0 if avg_g > 0 else 50.0
            else:
                rs = avg_g / avg_l
                self.value = 100.0 - (100.0 / (1.0 + rs))
        return self.value

class ATR:
    def __init__(self, period: int = 14):
        self.period = period
        self.prev_close: float | None = None
        self.avg_tr = WilderMovingAverage(period)
        self.value: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        if self.prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))
        
        self.prev_close = close
        self.value = self.avg_tr.update(tr)
        return self.value

class BollingerBands:
    def __init__(self, period: int = 20) -> None:
        self.period = period
        self.values: deque[float] = deque(maxlen=period)
    
    def update(self, close: float) -> tuple[float | None, float | None, float | None]:
        self.values.append(close)
        if len(self.values) < self.period:
            return None, None, None
        
        mean = sum(self.values) / self.period
        variance = sum((x - mean) ** 2 for x in self.values) / self.period
        std = math.sqrt(variance)
        
        return mean - 2 * std, mean, mean + 2 * std

class VWAP:
    def __init__(self) -> None:
        self.cumulative_pv = 0.0
        self.cumulative_v = 0.0

    def update(self, price: float, volume: float) -> float | None:
        self.cumulative_pv += price * volume
        self.cumulative_v += volume
        if self.cumulative_v == 0:
            return None
        return self.cumulative_pv / self.cumulative_v

class Supertrend:
    def __init__(self, period: int = 10, multiplier: float = 3.0) -> None:
        self.period = period
        self.multiplier = multiplier
        self.atr = ATR(period)
        self.prev_close: float | None = None
        self.upper_band: float | None = None
        self.lower_band: float | None = None
        self.trend = 1  # 1 for up, -1 for down
        self.value: float | None = None

    def update(self, high: float, low: float, close: float) -> tuple[float | None, int]:
        atr_val = self.atr.update(high, low, close)
        if atr_val is None:
            self.prev_close = close
            return None, 1

        hl2 = (high + low) / 2.0
        basic_upper = hl2 + self.multiplier * atr_val
        basic_lower = hl2 - self.multiplier * atr_val

        if self.upper_band is None or self.lower_band is None or self.prev_close is None:
            self.upper_band = basic_upper
            self.lower_band = basic_lower
            self.value = self.lower_band
            self.prev_close = close
            return self.value, self.trend

        # Band carry logic per §12.2
        if basic_lower > self.lower_band or self.prev_close < self.lower_band:
            self.lower_band = basic_lower

        if basic_upper < self.upper_band or self.prev_close > self.upper_band:
            self.upper_band = basic_upper

        # Trend flip logic
        if self.trend == 1 and close < self.lower_band:
            self.trend = -1
        elif self.trend == -1 and close > self.upper_band:
            self.trend = 1

        self.value = self.lower_band if self.trend == 1 else self.upper_band
        self.prev_close = close
        return self.value, self.trend
