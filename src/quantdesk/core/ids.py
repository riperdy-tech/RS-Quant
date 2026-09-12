from __future__ import annotations

import re
from hashlib import sha256

from quantdesk.core.events import canonical_bytes

_NAMESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


def derive_id(namespace: str, parent: str, producer: str, ordinal: int) -> str:
    """Derive a stable, domain-separated identifier from logical causal inputs."""

    if _NAMESPACE.fullmatch(namespace) is None:
        raise ValueError("namespace must use 1-32 safe identifier characters")
    if not parent:
        raise ValueError("parent must be non-empty")
    if not producer:
        raise ValueError("producer must be non-empty")
    if ordinal < 0:
        raise ValueError("ordinal must be non-negative")
    digest = sha256(
        canonical_bytes(
            {
                "namespace": namespace,
                "ordinal": ordinal,
                "parent": parent,
                "producer": producer,
                "version": 1,
            }
        )
    ).hexdigest()
    return f"{namespace}_{digest[:32]}"
