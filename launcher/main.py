"""QuantDesk Desktop Entrypoint (§16.1)."""

import argparse
import sys
from pathlib import Path

# Add src directory to path if running from source checkout
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if src_dir.exists() and str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from quantdesk.launcher.app import LauncherApp


def main() -> int:
    parser = argparse.ArgumentParser(description="QuantDesk Desktop Application")
    parser.add_argument(
        "--account-dir",
        type=str,
        default="data/accounts/demo",
        help="Path to account storage directory",
    )
    parser.add_argument(
        "--account",
        type=str,
        default="demo",
        help="Account name",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="DEMO",
        choices=["DEMO", "PAPER", "SANDBOX", "LIVE"],
        help="Execution mode (defaults to DEMO)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run in headless mode without GUI window",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open web browser on startup",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Preferred loopback port",
    )

    args = parser.parse_args()

    app = LauncherApp(
        account_dir=args.account_dir,
        account_name=args.account,
        mode=args.mode,
        headless=args.headless,
        auto_open_browser=not args.no_browser,
        preferred_port=args.port,
    )

    result = app.start()
    action = result.get("action")
    if action == "OPEN_EXISTING_DASHBOARD":
        print(f"Existing QuantDesk instance detected at {result.get('base_url')}. Dashboard opened.")
        return 0
    elif action == "ABORTED_DIAGNOSTIC_FAILURE":
        print(f"Startup aborted due to environment diagnostic failures: {result.get('issues')}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
