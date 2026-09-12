import json
import subprocess
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from quantdesk.config.loader import load_config
from quantdesk.core.events import canonical_bytes
from quantdesk.core.ids import derive_id

type CaseDriver = Callable[..., dict[str, object]]
_CASES: dict[str, CaseDriver] = {}


def register_case(name: str, driver: CaseDriver) -> None:
    if not name or name in _CASES:
        raise ValueError(f"case name must be non-empty and unique: {name!r}")
    _CASES[name] = driver


def run_case(name: str, **overrides: object) -> dict[str, object]:
    try:
        driver = _CASES[name]
    except KeyError as exc:
        raise KeyError(f"unknown acceptance case: {name}") from exc
    return driver(**overrides)


def _foundation_case(**overrides: object) -> dict[str, object]:
    qty = Decimal(str(overrides["qty"]))
    timestamp_ns = int(str(overrides["timestamp_ns"]))
    config = load_config(Path("configs/demo.yaml"))
    boundary = json.loads(canonical_bytes({"qty": qty, "timestamp_ns": timestamp_ns}))
    expected_id = derive_id("event", "parent-1", "foundation", 0)
    script = (
        "from quantdesk.core.ids import derive_id; "
        "print(derive_id('event', 'parent-1', 'foundation', 0))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return {
        "mode": config.mode.value,
        "live_enabled": config.account.live_enabled,
        "roundtrip_qty": boundary["qty"],
        "roundtrip_timestamp_ns": boundary["timestamp_ns"],
        "ids_match_across_processes": completed.stdout.strip() == expected_id,
    }


register_case("foundation", _foundation_case)
