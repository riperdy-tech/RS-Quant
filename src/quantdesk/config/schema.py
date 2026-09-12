from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PositiveDecimal = Annotated[Decimal, Field(gt=0)]
Fraction = Annotated[Decimal, Field(gt=0, le=1)]
PositiveInt = Annotated[int, Field(gt=0)]


class Mode(StrEnum):
    DEMO = "DEMO"
    REPLAY = "REPLAY"
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    SANDBOX = "SANDBOX"
    LIVE = "LIVE"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class SecretReference(StrictModel):
    """An identifier for a secret stored outside configuration and source control."""

    provider: Literal["keyring", "environment", "service"]
    name: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:/-]+$")]


class AccountConfig(StrictModel):
    virtual_equity_usdt: PositiveDecimal = Decimal("10000")
    live_enabled: bool = False
    margin_mode: Literal["isolated"] = "isolated"
    position_mode: Literal["one_way"] = "one_way"
    leverage: Annotated[Decimal, Field(gt=0, le=125)] = Decimal("1")
    credential_ref: SecretReference | None = None


class RiskConfig(StrictModel):
    per_trade_risk_fraction: Fraction = Decimal("0.001")
    daily_loss_fraction: Fraction = Decimal("0.01")
    peak_drawdown_fraction: Fraction = Decimal("0.03")
    max_gross_notional_fraction: Fraction = Decimal("0.25")
    max_symbol_notional_fraction: Fraction = Decimal("0.15")
    max_open_entry_orders_per_symbol: PositiveInt = 1
    max_open_orders_account: PositiveInt = 10
    max_order_visible_depth_fraction: Fraction = Decimal("0.01")
    max_spread_bps: PositiveDecimal = Decimal("5")
    market_data_max_age_ms: Annotated[int, Field(gt=0, le=86_400_000)] = 500
    mark_max_age_ms: Annotated[int, Field(gt=0, le=86_400_000)] = 2000
    private_heartbeat_max_age_ms: Annotated[int, Field(gt=0, le=86_400_000)] = 30000
    max_clock_offset_ms: Annotated[int, Field(gt=0, le=60_000)] = 250
    max_ingress_age_ms: Annotated[int, Field(gt=0, le=86_400_000)] = 250
    disk_reserve_gib: Annotated[int, Field(gt=0, le=1024)] = 2
    protection_confirmation_ms: Annotated[int, Field(gt=0, le=60_000)] = 2000
    session_boundary_timezone: Literal["UTC"] = "UTC"
    max_order_notional_usdt: PositiveDecimal | None = None
    max_total_notional_usdt: PositiveDecimal | None = None
    max_daily_loss_usdt: PositiveDecimal | None = None


class AppConfig(StrictModel):
    schema_version: Literal[1] = 1
    mode: Mode = Mode.DEMO
    account: AccountConfig = Field(default_factory=AccountConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)

    @model_validator(mode="after")
    def validate_mode_compatibility(self) -> AppConfig:
        if self.account.live_enabled and self.mode is not Mode.LIVE:
            raise ValueError("live_enabled may only be true in LIVE mode")
        if self.account.live_enabled and self.account.credential_ref is None:
            raise ValueError("enabled LIVE mode requires an external credential_ref")
        live_caps = (
            self.risk.max_order_notional_usdt,
            self.risk.max_total_notional_usdt,
            self.risk.max_daily_loss_usdt,
        )
        if self.account.live_enabled and any(cap is None for cap in live_caps):
            raise ValueError("enabled LIVE mode requires all absolute risk caps")
        return self
