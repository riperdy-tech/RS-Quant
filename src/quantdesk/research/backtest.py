from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from quantdesk.core.events import Envelope
from quantdesk.features.base import IncrementalFeatureEngine
from quantdesk.research.metrics import BacktestMetrics
from quantdesk.simulation.fees import FeeProfile
from quantdesk.simulation.latency import LatencyProfile
from quantdesk.simulation.scheduler import VirtualTimeline
from quantdesk.simulation.venue import SimVenue
from quantdesk.venues.capabilities import UnsupportedCapability


@dataclass(frozen=True)
class BacktestSpec:
    """Specification for a backtest run per §14.5."""

    run_id: str
    strategy: Any
    dataset_events: list[Envelope] = field(default_factory=list)
    fee_profile: FeeProfile = field(default_factory=FeeProfile)
    latency_profile: LatencyProfile = field(default_factory=LatencyProfile.synthetic_defaults)
    starting_balance: Decimal = Decimal("10000")
    fill_model: str = "conservative"
    seed: int = 42
    forced_close_at_end: bool = False
    assumptions: dict[str, Any] | None = None
    dataset_capabilities: frozenset[str] | None = None


@dataclass
class RunManifest:
    """Immutable manifest and artifacts produced by a completed backtest run (§14.5)."""

    run_id: str
    code_commit: str
    config_hash: str
    seed: int
    fill_model: str
    starting_balance: Decimal
    final_balance: Decimal
    state_hash: str
    metrics: dict[str, Any]
    equity_curve: list[tuple[int, Decimal]]
    exposure_curve: list[tuple[int, int]]
    trade_trace: list[dict[str, Any]]
    html_report: str


class Backtest:
    """Deterministic, credential-free execution backtester per §13, §14.5, and Task 11."""

    def __init__(
        self,
        run_id: str,
        venue: SimVenue | None = None,
        engine: Any | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.run_id = run_id
        self.venue = venue
        self.engine = engine
        self.config = config or {}
        self.starting_balance = Decimal(str(self.config.get("starting_balance", "10000")))

    def run(
        self,
        spec_or_events: BacktestSpec | list[Envelope] | None = None,
    ) -> RunManifest:
        # Support both BacktestSpec and raw historical_events list
        if isinstance(spec_or_events, BacktestSpec):
            spec = spec_or_events
            events = spec.dataset_events
        else:
            events = spec_or_events or []
            spec = BacktestSpec(
                run_id=self.run_id,
                strategy=getattr(self.engine, "strategy", None) or self.engine,
                dataset_events=events,
                fee_profile=getattr(self.venue, "fee_profile", FeeProfile()),
                latency_profile=getattr(self.venue, "latency", LatencyProfile.synthetic_defaults()),
                starting_balance=self.starting_balance,
                seed=int(self.config.get("seed", 42)),
                fill_model=str(self.config.get("fill_model", "conservative")),
                forced_close_at_end=bool(self.config.get("forced_close_at_end", False)),
                assumptions=self.config.get("assumptions"),
                dataset_capabilities=self.config.get("dataset_capabilities"),
            )

        # 1. Capability Validation (§14.5)
        if spec.strategy is not None and spec.dataset_capabilities is not None:
            strat_name = (
                spec.strategy.__class__.__name__
                if hasattr(spec.strategy, "__class__")
                else str(spec.strategy)
            )
            if strat_name in ("ImbalanceScalper", "LiquiditySweep") and not (
                {"L2", "BBO"}.intersection(spec.dataset_capabilities)
            ):
                caps_list = sorted(spec.dataset_capabilities)
                raise UnsupportedCapability(
                    f"Strategy {strat_name} requires L2/BBO capabilities, "
                    f"but dataset provides {caps_list}"
                )

        # Verify event sequence is causal and ordered
        prev_avail = -1
        for ev in events:
            if ev.available_ns < prev_avail:
                raise ValueError("dataset events contain retrograde availability timestamps")
            prev_avail = ev.available_ns

        # 2. Initialize Real Simulator and Feature Engine
        timeline = VirtualTimeline()
        venue = SimVenue(
            timeline=timeline,
            latency=spec.latency_profile,
            fee_profile=spec.fee_profile,
            isolated_collateral_ticks=spec.starting_balance,
        )
        feature_engine = IncrementalFeatureEngine()

        trade_trace: list[dict[str, Any]] = []
        equity_curve: list[tuple[int, Decimal]] = [(0, spec.starting_balance)]
        exposure_curve: list[tuple[int, int]] = [(0, 0)]
        current_cash = spec.starting_balance
        total_funding = Decimal("0")

        # Map active orders to their intended side for fill reporting
        active_order_sides: dict[str, str] = {}

        # 3. Step Through Historical Events Causally
        for event in events:
            # A. Feed market event to venue (market truth)
            venue.on_market(event)

            # B. Advance venue to event availability
            venue_outputs = venue.advance(event.available_ns)

            # C. Process venue outbox outputs (Fills, Funding, Acks, Rejects)
            for v_event in venue_outputs:
                payload_dict = (
                    json.loads(v_event.payload.decode("utf-8"))
                    if isinstance(v_event.payload, bytes)
                    else v_event.payload
                )

                if v_event.event_type == "OrderFill":
                    oid = str(payload_dict.get("order_id", ""))
                    filled_lots = int(payload_dict.get("filled_lots", 0))
                    price_ticks = int(payload_dict.get("price_ticks", 0))
                    fee_dec = Decimal(str(payload_dict.get("fee", 0)))
                    is_taker = bool(payload_dict.get("is_taker", True))
                    side = active_order_sides.get(oid, "BUY")

                    trade_record = {
                        "order_id": oid,
                        "side": side,
                        "filled_lots": filled_lots,
                        "price_ticks": price_ticks,
                        "fee": str(fee_dec),
                        "is_taker": is_taker,
                        "timestamp_ns": v_event.exchange_event_ns,
                    }
                    trade_trace.append(trade_record)

                    # Update cash balance
                    notional = Decimal(price_ticks * filled_lots)
                    if side == "BUY":
                        current_cash -= notional + fee_dec
                    else:
                        current_cash += notional - fee_dec

                elif v_event.event_type == "FundingPayment":
                    payment = Decimal(str(payload_dict.get("funding_payment", 0)))
                    total_funding += payment
                    current_cash += payment

            # D. Update feature engine
            feature_engine.update(event)
            features = feature_engine.as_dict()

            # E. Evaluate Strategy if attached
            if spec.strategy is not None and hasattr(spec.strategy, "on_event"):
                intents = spec.strategy.on_event(event, {"features": features})
                for intent in intents:
                    side_str = getattr(intent.side, "value", str(intent.side))
                    active_order_sides[intent.intent_id] = side_str
                    instruction = {
                        "action": getattr(intent.action, "value", str(intent.action)),
                        "intent_id": intent.intent_id,
                        "side": side_str,
                        "desired_quantity": intent.desired_quantity,
                        "price_policy": getattr(intent, "price_policy", "MARKET"),
                        "price_ticks": getattr(intent, "limit_ticks", None),
                        "time_in_force": getattr(intent, "time_in_force", "GTC") or "GTC",
                    }
                    venue.submit(instruction)

            # F. Track Equity & Exposure
            pos_lots = venue.position_lots
            mark = venue.mark_price_ticks
            unrealized_pnl = Decimal("0")
            if pos_lots != 0 and mark > 0 and venue.entry_price_ticks > 0:
                pnl_ticks = (
                    (mark - venue.entry_price_ticks) * pos_lots
                    if pos_lots > 0
                    else (venue.entry_price_ticks - mark) * abs(pos_lots)
                )
                unrealized_pnl = Decimal(pnl_ticks)

            equity = current_cash + unrealized_pnl
            equity_curve.append((event.available_ns, equity))
            exposure_curve.append((event.available_ns, pos_lots))

        # 4. End-of-Dataset Bounded Handling (§14.5)
        end_events = venue.on_end_of_data(forced_close=spec.forced_close_at_end)
        for e_ev in end_events:
            if e_ev.event_type == "OrderFill":
                p_dict = json.loads(e_ev.payload.decode("utf-8"))
                f_lots = int(p_dict.get("filled_lots", 0))
                p_ticks = int(p_dict.get("price_ticks", 0))
                fee_val = Decimal(str(p_dict.get("fee", 0)))
                trade_trace.append(
                    {
                        "order_id": "FORCED_CLOSE_END_OF_DATA",
                        "side": "SELL" if venue.position_lots > 0 else "BUY",
                        "filled_lots": f_lots,
                        "price_ticks": p_ticks,
                        "fee": str(fee_val),
                        "is_taker": True,
                        "timestamp_ns": e_ev.exchange_event_ns,
                    }
                )

        # 5. Metrics Calculation
        metrics = BacktestMetrics.calculate(
            starting_balance=spec.starting_balance,
            trade_trace=trade_trace,
            equity_curve=equity_curve,
            total_funding=total_funding,
            assumptions=spec.assumptions,
        )
        final_bal = Decimal(str(metrics["final_balance"]))

        # 6. Deterministic Hashes
        config_data = {
            "run_id": spec.run_id,
            "seed": spec.seed,
            "starting_balance": str(spec.starting_balance),
            "fill_model": spec.fill_model,
            "maker_rate": str(spec.fee_profile.maker_rate),
            "taker_rate": str(spec.fee_profile.taker_rate),
        }
        config_hash = hashlib.sha256(json.dumps(config_data, sort_keys=True).encode()).hexdigest()

        state_repr = json.dumps(
            {
                "config_hash": config_hash,
                "final_balance": str(final_bal),
                "total_trades": len(trade_trace),
                "trade_trace": [
                    (t["order_id"], t["side"], t["filled_lots"], t["price_ticks"], t["fee"])
                    for t in trade_trace
                ],
            },
            sort_keys=True,
        )
        state_hash = hashlib.sha256(state_repr.encode()).hexdigest()

        # 7. Standalone HTML Report Generation
        html_report = self._render_html_report(
            spec=spec,
            metrics=metrics,
            config_hash=config_hash,
            state_hash=state_hash,
            trade_trace=trade_trace,
        )

        return RunManifest(
            run_id=spec.run_id,
            code_commit="HEAD",
            config_hash=config_hash,
            seed=spec.seed,
            fill_model=spec.fill_model,
            starting_balance=spec.starting_balance,
            final_balance=final_bal,
            state_hash=state_hash,
            metrics=metrics,
            equity_curve=equity_curve,
            exposure_curve=exposure_curve,
            trade_trace=trade_trace,
            html_report=html_report,
        )

    def _render_html_report(
        self,
        spec: BacktestSpec,
        metrics: dict[str, Any],
        config_hash: str,
        state_hash: str,
        trade_trace: list[dict[str, Any]],
    ) -> str:
        rows_html = "".join(
            f"<tr><td>{t['timestamp_ns']}</td><td>{t['order_id']}</td><td>{t['side']}</td>"
            f"<td>{t['filled_lots']}</td><td>{t['price_ticks']}</td><td>{t['fee']}</td></tr>"
            for t in trade_trace[:50]
        )
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>QuantDesk Backtest Report: {spec.run_id}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      margin: 2rem; background: #fafafa; color: #222;
    }}
    h1 {{ color: #0d47a1; }}
    .card {{
      background: white; padding: 1.5rem; border-radius: 8px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 1.5rem;
    }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
    th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #ddd; }}
    th {{ background: #f0f4f8; }}
    .badge {{
      display: inline-block; padding: 4px 8px; border-radius: 4px;
      background: #e3f2fd; color: #0d47a1; font-weight: bold; font-size: 0.85rem;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>QuantDesk Backtest Report: <span class="badge">{spec.run_id}</span></h1>
    <p><strong>Config Hash:</strong> <code>{config_hash}</code></p>
    <p><strong>State Hash:</strong> <code>{state_hash}</code></p>
    <p><strong>Seed:</strong> {spec.seed} | <strong>Fill Model:</strong> {spec.fill_model}</p>
    <p><strong>Starting Balance:</strong> {spec.starting_balance}</p>
  </div>

  <div class="card">
    <h2>Performance & Cost Summary</h2>
    <table>
      <tr><th>Metric</th><th>Value</th><th>Metric</th><th>Value</th></tr>
      <tr><td>Final Balance</td><td><strong>{metrics["final_balance"]}</strong></td>
          <td>Net PnL</td><td>{metrics["net_pnl"]}</td></tr>
      <tr><td>Total Trades</td><td>{metrics["total_trades"]}</td>
          <td>Win Rate</td><td>{metrics["win_rate"] * 100:.1f}%</td></tr>
      <tr><td>Profit Factor</td><td>{metrics["profit_factor"]}</td>
          <td>Max Drawdown</td>
          <td>{metrics["max_drawdown"]} ({metrics["max_drawdown_pct"] * 100:.2f}%)</td></tr>
      <tr><td>Total Fees</td><td>{metrics["total_fees"]}</td>
          <td>Total Funding</td><td>{metrics["total_funding"]}</td></tr>
      <tr><td>Maker Fills</td><td>{metrics["maker_fills"]}</td>
          <td>Taker Fills</td><td>{metrics["taker_fills"]}</td></tr>
      <tr><td>Sharpe Ratio</td><td>{metrics["sharpe_ratio"]}</td>
          <td>Assumptions Included</td>
          <td>{metrics["report_contains_fees_funding_and_assumptions"]}</td></tr>
    </table>
  </div>

  <div class="card">
    <h2>Trade Trace (First 50 Executions)</h2>
    <table>
      <tr><th>Time (ns)</th><th>Order ID</th><th>Side</th><th>Lots</th>
          <th>Price (ticks)</th><th>Fee (USDT)</th></tr>
      {rows_html if rows_html else "<tr><td colspan='6'>No trades executed.</td></tr>"}
    </table>
  </div>
</body>
</html>"""
