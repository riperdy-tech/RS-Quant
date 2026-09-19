"""Authentication and HMAC-SHA256 request signing for MEXC Contract V1 API."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import json
from typing import Any
from urllib.parse import urlencode


@dataclass(frozen=True, slots=True)
class MEXCCredentials:
    """MEXC API credentials container."""
    api_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.api_key or not self.secret_key:
            raise ValueError("Complete MEXC credentials required (api_key, secret_key)")
        if any("\r" in v or "\n" in v for v in (self.api_key, self.secret_key)):
            raise ValueError("Invalid credential header: newline characters prohibited")


def generate_mexc_signature(
    secret_key: str,
    api_key: str,
    request_time_ms: int,
    param_str: str = "",
) -> str:
    """Generates hexadecimal HMAC-SHA256 signature for MEXC Contract V1.
    
    Sign string: f"{api_key}{request_time_ms}{param_str}"
    """
    sign_str = f"{api_key}{request_time_ms}{param_str}"
    return hmac.new(
        secret_key.encode("utf-8"),
        sign_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def signed_headers(
    credentials: MEXCCredentials,
    timestamp_ms: int,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | str | None = None,
) -> dict[str, str]:
    """Builds standard MEXC Contract V1 authentication headers."""
    if not isinstance(timestamp_ms, int) or timestamp_ms <= 0:
        raise ValueError("timestamp requires positive integer milliseconds")

    param_str = ""
    if params:
        # Sort query params alphabetically
        sorted_pairs = sorted((str(k), str(v)) for k, v in params.items() if v is not None)
        param_str = urlencode(sorted_pairs)
    elif body:
        if isinstance(body, dict):
            param_str = json.dumps(body, separators=(",", ":"))
        else:
            param_str = str(body)

    sig = generate_mexc_signature(
        secret_key=credentials.secret_key,
        api_key=credentials.api_key,
        request_time_ms=timestamp_ms,
        param_str=param_str,
    )

    return {
        "ApiKey": credentials.api_key,
        "Request-Time": str(timestamp_ms),
        "Signature": sig,
        "Content-Type": "application/json",
    }


def ws_login_message(credentials: MEXCCredentials, timestamp_ms: int) -> dict[str, Any]:
    """Builds the WebSocket authentication login frame for wss://contract.mexc.com/edge."""
    sig = generate_mexc_signature(
        secret_key=credentials.secret_key,
        api_key=credentials.api_key,
        request_time_ms=timestamp_ms,
        param_str="",
    )
    return {
        "method": "login",
        "param": {
            "apiKey": credentials.api_key,
            "reqTime": str(timestamp_ms),
            "signature": sig,
        },
    }
