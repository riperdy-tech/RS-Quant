from __future__ import annotations

import argparse
import json
import platform
from collections.abc import Sequence
from pathlib import Path

from quantdesk import __version__
from quantdesk.config.loader import ConfigError, load_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quantdesk")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version", help="report the installed QuantDesk version")
    diagnostics = subparsers.add_parser("diagnostics", help="report safe local diagnostics")
    diagnostics.add_argument("--config", type=Path, default=Path("configs/demo.yaml"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(json.dumps({"config_status": "ERROR", "message": str(exc)}, sort_keys=True))
        return 2
    report = {
        "config_status": "OK",
        "live_enabled": config.account.live_enabled,
        "mode": config.mode.value,
        "python": platform.python_version(),
        "schema_version": config.schema_version,
        "version": __version__,
    }
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
