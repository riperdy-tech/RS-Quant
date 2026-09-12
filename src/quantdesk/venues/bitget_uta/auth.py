"""Sign the exact encoded request target and UTF-8 body handed to transport."""

import base64
import hashlib
import hmac
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Credentials:
    api_key: str = field(repr=False)
    secret: str = field(repr=False)
    passphrase: str = field(repr=False)
    demo: bool = False

    def __post_init__(self) -> None:
        if not all((self.api_key, self.secret, self.passphrase)):
            raise ValueError("complete credential reference required")
        if any("\r" in v or "\n" in v for v in (self.api_key, self.passphrase)):
            raise ValueError("invalid credential header")


def sign(credentials: Credentials, message: bytes) -> str:
    return base64.b64encode(
        hmac.new(credentials.secret.encode(), message, hashlib.sha256).digest()
    ).decode("ascii")


def signed_headers(
    credentials: Credentials, timestamp_ms: int, method: str, target: str, body: bytes = b""
) -> dict[str, str]:
    if type(timestamp_ms) is not int or timestamp_ms < 0:
        raise ValueError("timestamp requires integer milliseconds")
    message = f"{timestamp_ms}{method.upper()}{target}".encode("ascii") + body
    return {
        "ACCESS-KEY": credentials.api_key,
        "ACCESS-PASSPHRASE": credentials.passphrase,
        "ACCESS-TIMESTAMP": str(timestamp_ms),
        "ACCESS-SIGN": sign(credentials, message),
    }


def login(credentials: Credentials, timestamp_ms: int) -> dict[str, object]:
    headers = signed_headers(credentials, timestamp_ms, "GET", "/user/verify")
    return {
        "op": "login",
        "args": [
            {
                "apiKey": credentials.api_key,
                "passphrase": credentials.passphrase,
                "timestamp": str(timestamp_ms),
                "sign": headers["ACCESS-SIGN"],
            }
        ],
    }
