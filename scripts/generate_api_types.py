from __future__ import annotations

import argparse
import sys
from pathlib import Path

from quantdesk.api.app import app


def generate_typescript_contracts() -> str:
    """Generates TypeScript contract definitions from the FastAPI OpenAPI schema (§15)."""
    openapi = app.openapi()
    schemas = openapi.get("components", {}).get("schemas", {})

    lines: list[str] = [
        "/* Auto-generated TypeScript contracts from QuantDesk OpenAPI schema (§15) */",
        "/* Do not edit directly; regenerate with scripts/generate_api_types.py */",
        "",
    ]

    # Type mapping for OpenAPI primitives
    type_map = {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "array": "any[]",
        "object": "Record<string, any>",
    }

    for schema_name, schema_def in sorted(schemas.items()):
        lines.append(f"export interface {schema_name} {{")
        props = schema_def.get("properties", {})
        required = set(schema_def.get("required", []))

        for prop_name, prop_def in sorted(props.items()):
            opt = "" if prop_name in required else "?"
            t = prop_def.get("type", "any")
            ts_type = type_map.get(t, "any")
            if "$ref" in prop_def:
                ts_type = prop_def["$ref"].split("/")[-1]
            elif t == "array" and "items" in prop_def:
                item_ref = prop_def["items"].get("$ref")
                if item_ref:
                    ts_type = f"{item_ref.split('/')[-1]}[]"
                else:
                    item_type = type_map.get(prop_def["items"].get("type", "any"), "any")
                    ts_type = f"{item_type}[]"
            lines.append(f"  {prop_name}{opt}: {ts_type};")

        lines.append("}")
        lines.append("")

    # Add core domain types for system, trading, commands, and events
    lines.extend([
        "export type CommandStatus = 'QUEUED' | 'VALIDATING' | 'APPLIED' | 'REJECTED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'UNKNOWN';",
        "",
        "export interface SystemStatus {",
        "  mode: 'DEMO' | 'LIVE';",
        "  live_enabled: boolean;",
        "  venue: string;",
        "  account_alias: string;",
        "  engine_state: string;",
        "  active_strategies: string[];",
        "  unresolved_incidents: number;",
        "  service_health: string;",
        "}",
        "",
        "export interface PositionItem {",
        "  instrument_id: string;",
        "  lots: number;",
        "  side: 'BUY' | 'SELL';",
        "  entry_price: string;",
        "  mark_price: string;",
        "  unrealized_pnl: string;",
        "  realized_pnl: string;",
        "  margin_equity: string;",
        "  initial_margin: string;",
        "  maintenance_margin: string;",
        "  currency: string;",
        "  timestamp_ns: number;",
        "}",
        "",
        "export interface SSEEventEnvelope {",
        "  id: string;",
        "  topic: string;",
        "  resource_version: string;",
        "  projection_watermark: number;",
        "  update_time_ns: number;",
        "  payload: Record<string, any>;",
        "}",
        "",
    ])

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate TypeScript API contracts from OpenAPI schema")
    parser.add_argument("--check", action="store_true", help="Verify that generated types match on-disk file with no diff")
    parser.add_argument("--out", type=Path, default=Path("web/src/types/api.ts"), help="Output path for TypeScript file")
    args = parser.parse_args()

    content = generate_typescript_contracts()
    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.check:
        if not out_path.exists():
            print(f"Error: {out_path} does not exist. Run without --check to generate it.", file=sys.stderr)
            return 1
        existing = out_path.read_text(encoding="utf-8")
        if existing.strip() != content.strip():
            print(f"Error: {out_path} is out of date. Run 'python scripts/generate_api_types.py' to update.", file=sys.stderr)
            return 1
        print(f"PASS: {out_path} is up to date (no diff).")
        return 0

    out_path.write_text(content, encoding="utf-8")
    print(f"Generated {out_path} successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
