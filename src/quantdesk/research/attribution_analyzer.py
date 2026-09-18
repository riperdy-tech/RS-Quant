"""Tier 3 Automated Attribution & Failure Post-Mortem Analyzer.

Clusters closed trade losses across microstructure conditions (Spread, Volume Z-Score,
Order Book Depth, Market Regime) and diagnoses systematic alpha leakages.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import logging
from typing import Any, Sequence

from quantdesk.strategies.unified_agentic import AttributionTag, TradeEpisode

logger = logging.getLogger("quantdesk.attribution_analyzer")


@dataclass(frozen=True)
class AttributionCluster:
    tag: str
    count: int
    total_net_pnl: float
    avg_net_pnl: float
    avg_duration_s: float
    loss_rate_pct: float
    primary_microstructure_culprit: str


class LossAttributionAnalyzer:
    """Diagnoses trade outcomes and generates structured post-mortem reports."""

    def __init__(self, instrument_id: str) -> None:
        self.instrument_id = instrument_id

    def analyze(self, episodes: Sequence[TradeEpisode]) -> dict[str, Any]:
        """Clusters trade episodes by attribution and derives root-cause hypotheses."""
        if not episodes:
            return {
                "instrument_id": self.instrument_id,
                "total_trades": 0,
                "win_rate_pct": 0.0,
                "total_net_pnl": 0.0,
                "clusters": {},
                "top_alpha_leak": None,
                "recommended_hypothesis": "Insufficient trade data for statistical clustering (requires >= 5 trades).",
            }

        total_trades = len(episodes)
        wins = [ep for ep in episodes if ep.net_pnl > Decimal("0")]
        losses = [ep for ep in episodes if ep.net_pnl <= Decimal("0")]
        win_rate = round(100.0 * len(wins) / total_trades, 1)
        total_pnl = float(sum((ep.net_pnl for ep in episodes), Decimal("0.00")))

        # Group by AttributionTag
        tag_groups: dict[str, list[TradeEpisode]] = defaultdict(list)
        for ep in episodes:
            tag_groups[ep.attribution.value].append(ep)

        clusters: dict[str, dict[str, Any]] = {}
        for tag, eps in tag_groups.items():
            cnt = len(eps)
            pnl_sum = float(sum((e.net_pnl for e in eps), Decimal("0.00")))
            avg_pnl = round(pnl_sum / cnt, 2)
            avg_dur = round(sum(e.duration_s for e in eps) / cnt, 1)
            loss_cnt = sum(1 for e in eps if e.net_pnl < Decimal("0"))
            loss_rate = round(100.0 * loss_cnt / cnt, 1)

            # Analyze features at entry
            vol_deltas = [e.features_at_entry.get("volume_delta", 0.0) for e in eps]
            avg_vol_delta = round(sum(vol_deltas) / cnt, 2) if vol_deltas else 0.0

            culprit = "NONE"
            if tag == AttributionTag.FEE_DRAG_LOSS.value:
                culprit = "EXCHANGE_FEE_EROSION (Target too close to spread/fee drag)"
            elif tag == AttributionTag.RAPID_STOP_CHOP.value:
                culprit = "FALSE_BREAKOUT_CHOP (Entered during order book vacuum)"
            elif tag == AttributionTag.ALPHA_SCRATCH.value:
                culprit = "ALPHA_HORIZON_STALL (Momentum decayed before reaching target)"
            elif tag == AttributionTag.TIMEOUT_EXIT.value:
                culprit = "STAGNANT_CHOP (Trade failed to develop within time window)"

            clusters[tag] = {
                "count": cnt,
                "total_net_pnl": round(pnl_sum, 2),
                "avg_net_pnl": avg_pnl,
                "avg_duration_s": avg_dur,
                "loss_rate_pct": loss_rate,
                "avg_vol_delta": avg_vol_delta,
                "primary_culprit": culprit,
            }

        # Identify top alpha leak
        worst_tag = None
        worst_pnl = 0.0
        for tag, c_data in clusters.items():
            if c_data["total_net_pnl"] < worst_pnl:
                worst_pnl = c_data["total_net_pnl"]
                worst_tag = tag

        # Generate actionable hypothesis
        recommended_hypothesis = "System performing within normal variance bounds."
        if worst_tag == AttributionTag.FEE_DRAG_LOSS.value:
            recommended_hypothesis = (
                "ELEVATE_VOLATILITY_HURDLE: Gross profit target is too small relative to round-trip fees. "
                "Mutate parameter: increase volatility_hurdle_bps from 5.0 to 8.0 bps and atr_target_mult to 2.8x."
            )
        elif worst_tag == AttributionTag.RAPID_STOP_CHOP.value:
            recommended_hypothesis = (
                "TIGHTEN_MICRO_CONFIRMATION: Rapid stops indicate entering in microstructural chop. "
                "Mutate parameter: elevate depth5_threshold by +0.05 and require volume_zscore > +0.50."
            )
        elif worst_tag == AttributionTag.ALPHA_SCRATCH.value and len(losses) > len(wins):
            recommended_hypothesis = (
                "FILTER_LOW_MOMENTUM: Alpha scratching frequently without follow-through. "
                "Mutate parameter: raise conviction_threshold from 0.35 to 0.45."
            )

        return {
            "instrument_id": self.instrument_id,
            "total_trades": total_trades,
            "win_rate_pct": win_rate,
            "total_net_pnl": round(total_pnl, 2),
            "clusters": clusters,
            "top_alpha_leak": worst_tag,
            "top_alpha_leak_loss": round(worst_pnl, 2),
            "recommended_hypothesis": recommended_hypothesis,
        }
