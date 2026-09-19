"""MEXC Contract V1 (USDT-M Futures) venue package for QuantDesk."""

from quantdesk.venues.mexc.auth import (
    MEXCCredentials,
    generate_mexc_signature,
    signed_headers,
    ws_login_message,
)
from quantdesk.venues.mexc.contract_specs import (
    MEXCContractSpec,
    MEXCContractSpecsRegistry,
    from_mexc_symbol,
    to_mexc_symbol,
)
from quantdesk.venues.mexc.live_feed import (
    MEXCLiveFeedService,
    mexc_live_feed_service,
)
from quantdesk.venues.mexc.rest import MEXCRestClient

__all__ = [
    "MEXCCredentials",
    "generate_mexc_signature",
    "signed_headers",
    "ws_login_message",
    "MEXCContractSpec",
    "MEXCContractSpecsRegistry",
    "to_mexc_symbol",
    "from_mexc_symbol",
    "MEXCRestClient",
    "MEXCLiveFeedService",
    "mexc_live_feed_service",
]
