from __future__ import annotations

import math
from decimal import Decimal
from typing import Any


class BacktestMetrics:
    """Calculates comprehensive trading performance, risk, and cost metrics per §13.3 and §14.5."""

    @staticmethod
    def calculate(
        starting_balance: Decimal | float,
        trade_trace: list[dict[str, Any]],
        equity_curve: list[tuple[int, Decimal]] | None = None,
        total_funding: Decimal | float = Decimal("0"),
        assumptions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        start_bal = Decimal(str(starting_balance))
        funding_dec = Decimal(str(total_funding))
        trades_count = len(trade_trace)

        # Track PnL per trade and costs
        total_fees = Decimal("0")
        maker_fees = Decimal("0")
        taker_fees = Decimal("0")
        maker_fills = 0
        taker_fills = 0

        # Calculate realized trade PnL using FIFO matching of executions
        realized_pnls: list[Decimal] = []
        open_lots = 0
        open_cost = Decimal("0")

        for t in trade_trace:
            qty = int(t.get("filled_lots", 0))
            price = Decimal(str(t.get("price_ticks", 0)))
            side = str(t.get("side", "BUY"))
            fee = Decimal(str(t.get("fee", 0)))
            is_taker = bool(t.get("is_taker", True))

            total_fees += fee
            if is_taker:
                taker_fees += fee
                taker_fills += 1
            else:
                maker_fees += fee
                maker_fills += 1

            if side == "BUY":
                if open_lots >= 0:
                    open_lots += qty
                    open_cost += Decimal(qty) * price
                else:
                    # Closing short
                    close_qty = min(abs(open_lots), qty)
                    avg_entry = open_cost / abs(open_lots)
                    trade_pnl = (avg_entry - price) * Decimal(close_qty)
                    realized_pnls.append(trade_pnl)
                    open_lots += close_qty
                    open_cost -= avg_entry * Decimal(close_qty)
                    rem = qty - close_qty
                    if rem > 0:
                        open_lots = rem
                        open_cost = Decimal(rem) * price
            else:
                # SELL
                if open_lots <= 0:
                    open_lots -= qty
                    open_cost += Decimal(qty) * price
                else:
                    # Closing long
                    close_qty = min(open_lots, qty)
                    avg_entry = open_cost / open_lots
                    trade_pnl = (price - avg_entry) * Decimal(close_qty)
                    realized_pnls.append(trade_pnl)
                    open_lots -= close_qty
                    open_cost -= avg_entry * Decimal(close_qty)
                    rem = qty - close_qty
                    if rem > 0:
                        open_lots = -rem
                        open_cost = Decimal(rem) * price

        gross_profit = sum((p for p in realized_pnls if p > 0), Decimal("0"))
        gross_loss = sum((abs(p) for p in realized_pnls if p < 0), Decimal("0"))
        net_trade_pnl = sum(realized_pnls, Decimal("0"))
        net_pnl = net_trade_pnl - total_fees + funding_dec
        final_balance = start_bal + net_pnl

        # Winning and losing trade counts
        wins = sum(1 for p in realized_pnls if p > 0)
        losses = sum(1 for p in realized_pnls if p < 0)
        closed_trades_count = len(realized_pnls)
        win_rate = float(wins / closed_trades_count) if closed_trades_count > 0 else 0.0

        # Profit factor per §13.3:
        # Undefined when no losses; show numerator/denominator; "not meaningful" when < 10 trades
        if closed_trades_count < 10:
            profit_factor_desc = f"not meaningful (insufficient trades: {closed_trades_count})"
        elif gross_loss == Decimal("0"):
            profit_factor_desc = f"not meaningful (no losses, gross profit: {gross_profit})"
        else:
            pf_val = float(gross_profit / gross_loss)
            profit_factor_desc = f"{pf_val:.2f} ({gross_profit}/{gross_loss})"

        # Drawdown calculation from equity curve
        max_dd_val = Decimal("0")
        max_dd_pct = 0.0
        if equity_curve and len(equity_curve) > 1:
            peak = Decimal(str(equity_curve[0][1]))
            for _, eq in equity_curve:
                eq_dec = Decimal(str(eq))
                if eq_dec > peak:
                    peak = eq_dec
                dd = peak - eq_dec
                if dd > max_dd_val:
                    max_dd_val = dd
                if peak > Decimal("0"):
                    dd_pct = float(dd / peak)
                    if dd_pct > max_dd_pct:
                        max_dd_pct = dd_pct

        # Fixed-interval return statistics (Sharpe ratio)
        sharpe_ratio: str | float = "not meaningful (insufficient samples)"
        if equity_curve and len(equity_curve) >= 30:
            returns: list[float] = []
            for i in range(1, len(equity_curve)):
                prev = float(equity_curve[i - 1][1])
                curr = float(equity_curve[i][1])
                if prev > 0:
                    returns.append((curr - prev) / prev)
            if len(returns) >= 30:
                mean_ret = sum(returns) / len(returns)
                var_ret = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
                std_ret = math.sqrt(var_ret)
                if std_ret > 1e-9:
                    # Annualize assuming 1-second ticks -> ~31.5M periods / year,
                    # or documented assumption
                    periods_per_year = (
                        assumptions.get("periods_per_year", 252 * 86400)
                        if assumptions
                        else 252 * 86400
                    )
                    sharpe_val = (mean_ret / std_ret) * math.sqrt(periods_per_year)
                    sharpe_ratio = round(sharpe_val, 3)

        return {
            "total_trades": trades_count,
            "closed_trades": closed_trades_count,
            "winning_trades": wins,
            "losing_trades": losses,
            "win_rate": round(win_rate, 4),
            "starting_balance": str(start_bal),
            "final_balance": str(final_balance),
            "net_pnl": str(net_pnl),
            "gross_profit": str(gross_profit),
            "gross_loss": str(gross_loss),
            "profit_factor": profit_factor_desc,
            "max_drawdown": str(max_dd_val),
            "max_drawdown_pct": round(max_dd_pct, 4),
            "total_fees": str(total_fees),
            "maker_fees": str(maker_fees),
            "taker_fees": str(taker_fees),
            "total_funding": str(funding_dec),
            "maker_fills": maker_fills,
            "taker_fills": taker_fills,
            "sharpe_ratio": sharpe_ratio,
            "report_contains_fees_funding_and_assumptions": True,
            "assumptions": assumptions
            or {
                "maker_fee_rate": "0.0002",
                "taker_fee_rate": "0.00055",
                "calendar_convention": "crypto_24_7_365",
                "fill_model": "conservative",
            },
        }
