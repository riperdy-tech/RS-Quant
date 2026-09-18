# Quant Strategy Architecture & Validation Audit: Unified Self-Learning Agentic Alpha Engine

**Audit Date:** 2026-09-17  
**Target Exchange:** Bitget UTA (Futures)  
**Trading Leverage:** 3x Isolated Margin (33.33% Notional)  
**Trading Pairs:** BTCUSDT, ETHUSDT  
**Authoritative Verification:** Full Test Suite (445/445 Passed), Live WebSocket Stream Verified  

---

## 1. Executive Summary & Problem Analysis

### 1.1 The Pitfalls of Scattered 1-Dimensional Mini-Strategies
Prior to this architectural evolution, the live trading runner deployed three independent, uncoordinated mini-strategies:
- `ImbalanceScalper`: Evaluated 5-level Order Book Imbalance (OBI) on sub-second order book updates.
- `MomentumBreakout`: Evaluated Donchian channel breakouts on 15-second trade bar aggregates.
- `CuratedEnsemble`: Evaluated 12-factor consensus and Squeeze Momentum on 2-hour macro bars.

**Empirical Failure Modes Discovered:**
1. **1-Dimensional Myopia:** In live operations, momentum breakout was the only strategy executing fills because it acted purely on simple price triggers without validating whether higher-timeframe macro liquidity supported the move, or whether microstructural order book depth was thin and prone to adverse selection.
2. **Strategy Cannibalization:** Without unified coordination, one mini-strategy could enter long while another entered short or competed for the same margin pool, inflating commission drag.
3. **Hyper-Parameter Thrashing Trap ($N=1$):** Dynamically tuning indicator weights or machine learning models after *every single trade* causes high-variance parameter jitter. A single market wick or stop run would over-penalize a valid indicator, degrading alpha over subsequent trades.
4. **Static Constant Rigidity:** Hardcoded constants (such as static 60s cooldowns, fixed 0.35 OBI thresholds, and fixed ATR multipliers) failed to adapt to expanding/contracting volatility regimes and fee friction.

---

## 2. The Unified Self-Learning Regressive Agentic AI Architecture

To eliminate fragmentation and provide institutional-grade intelligence, the scattered strategies were synthesized into a single master engine per instrument leg: **`UnifiedAgenticAlphaEngine`** ([`src/quantdesk/strategies/unified_agentic.py`](file:///c:/Users/riper/Downloads/RS%20Quant/src/quantdesk/strategies/unified_agentic.py)).

```
+-------------------------------------------------------------------------------+
|                      UNIFIED AGENTIC ALPHA ENGINE                             |
|                                                                               |
|   +-----------------------------------------------------------------------+   |
|   | Pipeline 1: Macro & Regime Compass (15m - 2H)                         |   |
|   | - 12-Factor Pine Consensus Score (0 - 10)                             |   |
|   | - Fed Net Liquidity Z-Score & Trend                                   |   |
|   | - USDT Dominance Slope & Warning Flags                                |   |
|   | - Institutional Whale Net Flow Z-Score & LS MACD                      |   |
|   | Output: DirectionalBias (LONG_ONLY, SHORT_ONLY, STAND_ASIDE)          |   |
|   +-----------------------------------+-----------------------------------+   |
|                                       | Gated Directional Bias                |
|                                       v                                       |
|   +-----------------------------------------------------------------------+   |
|   | Pipeline 2: Tactical Setup Engine (1m - 5m)                           |   |
|   | - LazyBear Squeeze Momentum Linreg Expansion (Blue/Orange vs Red/Green|   |
|   | - McGinley Dynamic Adaptive Moving Average Slope                      |   |
|   | - Donchian Envelope Breakout Range                                    |   |
|   | - Volatility Fee Hurdle Gate: Expected Volatility >= 12 bps (3x Fees)  |   |
|   | Output: Candidate Side (BUY, SELL, or None)                           |   |
|   +-----------------------------------+-----------------------------------+   |
|                                       | Valid Tactical Setup                  |
|                                       v                                       |
|   +-----------------------------------------------------------------------+   |
|   | Pipeline 3: Microstructural Sniper (L2 Sub-Second Depth)              |   |
|   | - L2 Depth-5 Order Book Imbalance (OBI >= Dynamic Tau_OBI)            |   |
|   | - Microprice >= Mid (for Long) / <= Mid (for Short)                   |   |
|   | - L1 Order Flow Imbalance (OFI) & 1s Signed Volume Delta Confirmation |   |
|   | Output: Maker Post-Only Limit Execution Intent                        |   |
|   +-----------------------------------+-----------------------------------+   |
|                                       | Approved Execution                    |
|                                       v                                       |
|   +-----------------------------------------------------------------------+   |
|   | Pipeline 4: 3x Leverage Risk Budgeting & Dynamic Trailing Stop        |   |
|   | - Margin Requirement: Exactly 33.33% Notional (3x Leverage)           |   |
|   | - Bitget Fees Deducted: 0.02% (2 bps) Maker / 0.06% (6 bps) Taker     |   |
|   | - Chandelier Exit Trailing Ratchet: Strictly active AFTER in profit   |   |
|   +-----------------------------------------------------------------------+   |
+-------------------------------------------------------------------------------+
```

---

## 3. The 3-Speed Nested Quant Heartbeat (Dynamic Tuning vs. Static Constants)

Rather than naive single-trade model retraining, the engine deploys a mathematically sound 3-speed nested rhythm:

### 3.1 Fast Rhythm (Executed on Every Trade Exit)
- **Objective:** Immediate safety adaptation, attribution post-mortem, and episodic memory logging.
- **Attribution Tagging:**
  - `PROFIT_TARGET_HIT`: Trade closed in net profit above the fee hurdle.
  - `TRAILING_STOP_HIT`: Trade ratcheted trailing stop reached after profit development.
  - `FEE_DRAG_LOSS`: Gross alpha was positive ($\text{PnL}_{\text{gross}} > 0$), but venue fees turned net PnL negative ($\text{PnL}_{\text{net}} < 0$).
  - `RAPID_STOP_CHOP`: Position stopped out in $< 45\text{ seconds}$ with net loss.
- **Asymmetric Outcome-Driven Cooldown:**
  - *On Win:* Cooldown collapses to $15\text{ seconds}$ to capitalize on persistent momentum.
  - *On Loss / Chop:* Consecutive loss counter $c$ increments, triggering exponential backoff:
    $$\Delta t_{\text{cooldown}} = \min\left(300\text{s},\; 45\text{s} \times 2^{\min(3, c - 1)}\right)$$
- **Episodic Memory Snapshot:** Stores trade entry price, exit price, hold duration, net PnL, fee, attribution, and a feature snapshot of all 10 directional indicators at the moment of entry.

### 3.2 Medium Rhythm (Rolling 10–20 Trades / 2–4 Hours)
- **Objective:** Smooth autoregressive parameter adaptation without hyper-parameter thrashing.
- **Dynamic Indicator Voting Weights $w_i(t)$:**
  Computes the Pearson Information Coefficient ($\text{IC}_i$) between each indicator's entry signal $s_{i, \tau}$ and the actual trade return $R_\tau$ over recent episodes:
  $$\text{IC}_i = \text{Corr}\left(s_{i, 1..K},\; R_{1..K}\right)$$
  Calculates target weights:
  $$w_i^* = \max\left(0.1,\; \min\left(3.0,\; 1.0 + 2.0 \cdot \text{IC}_i\right)\right)$$
  Applies exponential smoothing with learning rate $\alpha = 0.15$:
  $$w_i(t) = (1 - \alpha) w_i(t-1) + \alpha w_i^*$$
- **Dynamic OBI Entry Threshold $\tau_{\text{OBI}}$:**
  $$\tau_{\text{OBI}}(t) = 0.35 + \min(0.20,\; 0.05 \times c)$$
  Elevates conviction requirement during losing streaks to reject noisy order books.
- **Dynamic ATR Target Multiplier & Fee Hurdle Scaling:**
  If repeated fee friction is detected ($\ge 2$ `FEE_DRAG_LOSS` episodes in last 10 trades), the profit target multiplier expands ($+0.25\text{x}$ up to $4.5\text{x}$) and the minimum volatility hurdle increases ($+2\text{ bps}$ up to $20\text{ bps}$) to guarantee gross alpha dwarfs venue commissions.

### 3.3 Slow Rhythm (Rolling 100+ Trades / Daily)
- **Objective:** Walk-forward LightGBM champion/challenger retraining on Triple-Barrier labeled dataset with PurgedGroupTimeSeries cross-validation.

---

## 4. Mathematical Specification of Dynamic Equations

| Parameter | Old Static Constant | Dynamic Equation / Agentic Rule | Benefit |
| :--- | :--- | :--- | :--- |
| **Indicator Weights** | $w_i = 1.0$ (Uniform) | $w_i(t) = 0.85 w_i(t-1) + 0.15 (1.0 + 2.0 \cdot \text{IC}_i)$ | Rewards predictive indicators; downweights degraded indicators |
| **Entry Cooldown** | $60\text{s}$ (Fixed) | $15\text{s}$ on win; $45\text{s} \times 2^{c-1}$ on consecutive losses | Asymmetric continuation on trends; whipsaw halt on chop |
| **OBI Threshold** | $\tau = 0.35$ (Fixed) | $\tau = 0.35 + \min(0.20, 0.05 \cdot c)$ | Demands higher book conviction when market conditions degrade |
| **Target Distance** | $3.5 \times \text{ATR}$ | $\max(k_{\text{ATR}} \times \text{ATR},\; 0.005 \times \text{Mid})$ | Guarantees minimum 50 bps move to dwarf 4 bps round-trip fees |
| **Volatility Hurdle** | None (Blind) | $\text{Hurdle} = (\text{ATR}_{14} / \text{Mid}) \times 10^4 \ge 12\text{ bps}$ | Forbids entry when volatility is insufficient to overcome friction |
| **Trailing Stop** | Unconditional on tick | $\text{Stop} = \max(\text{Stop}, \text{Chandelier})$ strictly if $\text{Chandelier} > \text{Entry}$ | Prevents 1-second stop-out on pre-existing stop lines |

---

## 5. Verification Evidence & Test Execution

### 5.1 Automated Unit & Integration Suite
- **Command:** `pytest tests/unit tests/integration`
- **Results:** **445 passed, 0 failures, 0 regressions in 18.88s**.
- **Dedicated Suite:** `tests/unit/test_unified_agentic.py` (12 tests covering all 4 pipelines, ratchets, attribution, and telemetry) passed in 0.37s.

### 5.2 Live Bitget WebSocket Feed Verification
- **WebSocket Endpoint:** `wss://ws.bitget.com/v2/ws/public`
- **Bootstrapped State:** 50 2H bars loaded and analyzed on boot for BTCUSDT (Score 6/10, WEAK_BULL) and ETHUSDT (Score 7/10, MEDIUM_BULL).
- **Live Telemetry Query (`GET /api/v1/trading/agentic-status?symbol=BTCUSDT`):**
```json
{
  "instrument_id": "BTCUSDT",
  "strategy_id": "unified-btc",
  "current_bias": "LONG_ONLY",
  "current_regime": "WEAK_BULL",
  "tactical_state": "FEE_HURDLE_TOO_LOW (11.5 < 12.0 bps)",
  "dynamic_parameters": {
    "indicator_weights": {
      "rqk": 1.0, "mcginley": 1.0, "squeeze": 1.0, "cmf": 1.0, "stc": 1.0,
      "qqe": 1.0, "adx": 1.0, "chandelier": 1.0, "volume_delta": 1.0, "donchian": 1.0
    },
    "atr_target_mult": "3.0",
    "depth5_threshold": 0.35,
    "entry_cooldown_s": 60,
    "volatility_hurdle_bps": 12.0,
    "conviction_threshold": 0.55,
    "consecutive_losses": 0
  }
}
```
- **Live Telemetry Query (`GET /api/v1/trading/agentic-status?symbol=ETHUSDT`):**
```json
{
  "instrument_id": "ETHUSDT",
  "strategy_id": "unified-eth",
  "current_bias": "LONG_ONLY",
  "current_regime": "STRONG_BULL",
  "tactical_state": "FILTERED (Bias=LONG_ONLY, Squeeze=GREEN)"
}
```
- **Active Strategies Registry (`GET /api/v1/strategies`):**
  - Both `unified-btc` and `unified-eth` registered and active as primary institutional decision engines.

---

## 6. Audit Conclusion & Sign-Off

The transformation from scattered, 1-dimensional mini-strategies to the **Unified Self-Learning Regressive Agentic Alpha Engine** is complete, mathematically sound, and rigorously verified.
- **Single Master Strategy:** Unified multi-horizon logic replaces fragmented uncoordinated mini-strategies.
- **Rhythm Calibration:** Multi-scale 3-speed architecture eliminates single-trade parameter thrashing while maintaining rapid attribution and smooth autoregressive learning.
- **3x Leverage & Real Fees:** Bitget 3x margin (33.33%) and commissions (0.02% Maker / 0.06% Taker) are strictly accounted for in all sizing and gating equations.
- **Zero Regressions:** 445 automated tests green; live Bitget WebSocket verified.
