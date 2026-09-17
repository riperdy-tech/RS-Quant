"""§11.2 breaker semantics: soft breakers and kill switch.

Kill: immediately persist a latched halt, block risk-increasing dispatch,
request cancellation of bot-owned entry orders, retain protection.
Soft breaker: disable new risk for a scope; continue protective work.
"""

from __future__ import annotations

from quantdesk.risk.emergency import Emergency

__all__ = ["Emergency"]
