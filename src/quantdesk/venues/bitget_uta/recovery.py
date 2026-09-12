"""Read-only account eligibility; unknown or missing fields retain a block."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from quantdesk.venues.bitget_uta.normalize import decimal


@dataclass(frozen=True, slots=True)
class AccountProfile:
    eligible: bool
    reasons: tuple[str, ...]
    available_collateral: Decimal | None
    account_level: str | None
    hold_mode: str | None


def account_profile(
    info: dict[str, Any],
    settings: dict[str, Any],
    assets: dict[str, Any],
    positions: tuple[dict[str, Any], ...],
    symbols: tuple[str, ...],
) -> AccountProfile:
    reasons: set[str] = set()
    if settings.get("accountMode") != "unified":
        reasons.add("ACCOUNT_MODE")
    if settings.get("accountLevel") != "isolated":
        reasons.add("ACCOUNT_LEVEL")
    if settings.get("holdMode") != "one_way_mode":
        reasons.add("HOLD_MODE")
    if settings.get("deltaSwitch") != "no":
        reasons.add("DELTA_MODE_UNVERIFIED")
    if not info.get("userId") or info.get("userId") != settings.get("uid"):
        reasons.add("ACCOUNT_ID_MISMATCH")
    permissions = info.get("permissions")
    if (
        not isinstance(permissions, list)
        or "uta_trade" not in permissions
        or "uta_mgt" not in permissions
        or info.get("permType") != "read-and-write"
    ):
        reasons.add("PERMISSIONS_UNVERIFIED")
    if isinstance(permissions, list) and any(
        p not in {"uta_trade", "uta_mgt"} for p in permissions
    ):
        reasons.add("UNSAFE_PERMISSIONS")
    configs = settings.get("symbolConfigList", [])
    for symbol in symbols:
        matching = [
            r for r in configs if r.get("symbol") == symbol and r.get("category") == "USDT-FUTURES"
        ]
        if len(matching) != 1 or matching[0].get("marginMode") != "isolated":
            reasons.add(f"SYMBOL_MARGIN_MODE:{symbol}")
    collateral = None
    rows = assets.get("assets")
    if not isinstance(rows, list):
        reasons.add("ASSETS_UNAVAILABLE")
        rows = []
    for row in rows:
        try:
            debt, balance = decimal(row["debt"]), decimal(row["balance"])
            if debt != 0:
                reasons.add("BORROWING")
            if row["coin"] != "USDT" and balance != 0:
                reasons.add("MIXED_COLLATERAL")
            if row["coin"] == "USDT":
                collateral = decimal(row["available"])
        except (KeyError, ValueError, TypeError):
            reasons.add("ASSET_FIELDS_UNAVAILABLE")
    if collateral is None or collateral < 0:
        reasons.add("COLLATERAL_UNAVAILABLE")
    for row in positions:
        if row.get("category", "").upper() != "USDT-FUTURES" or row.get("symbol") not in symbols:
            reasons.add("FOREIGN_EXPOSURE")
        if row.get("marginMode") != "isolated" or row.get("holdMode") != "one_way_mode":
            reasons.add("POSITION_MODE")
    return AccountProfile(
        not reasons,
        tuple(sorted(reasons)),
        collateral,
        settings.get("accountLevel"),
        settings.get("holdMode"),
    )
