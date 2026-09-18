import React, { useEffect, useState } from 'react';
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  Bot,
  Brain,
  CheckCircle2,
  ChevronRight,
  Cpu,
  DollarSign,
  Gauge,
  HelpCircle,
  Layers,
  LineChart,
  Pause,
  Play,
  Radio,
  RefreshCw,
  RotateCcw,
  Search,
  Shield,
  Sliders,
  Sparkles,
  Terminal,
  TrendingDown,
  TrendingUp,
  Zap,
} from 'lucide-react';
import { api } from '../services/apiClient';
import { AgenticStatus, MacroRadarData, ReflexStatus, PositionItem } from '../types/api';
import { OrderTraceModal } from '../components/OrderTraceModal';

export interface TradingPageProps {
  onOpenCommand: (type: string, target?: Record<string, any>) => void;
  isViewer?: boolean;
}

/**
 * Universal Currency Formatter:
 * Correctly handles negative signs (-$1,234.56), positive signs (+$1,234.56), and neutral ($0.00).
 */
export function formatUsd(val: string | number | undefined | null, showPlus: boolean = false): string {
  if (val === undefined || val === null || val === '') return '$0.00';
  const clean = String(val).replace(/[+$,]/g, '').trim();
  const num = parseFloat(clean);
  if (isNaN(num)) return '$0.00';

  const isNegative = num < 0;
  const absFormatted = Math.abs(num).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  if (isNegative) {
    return `-$${absFormatted}`;
  }
  if (showPlus && num > 0) {
    return `+$${absFormatted}`;
  }
  return `$${absFormatted}`;
}

export const TradingPage: React.FC<TradingPageProps> = ({ onOpenCommand, isViewer = false }) => {
  const [positions, setPositions] = useState<PositionItem[]>([]);
  const [orders, setOrders] = useState<any[]>([]);
  const [fills, setFills] = useState<any[]>([]);
  const [balances, setBalances] = useState<any[]>([]);
  const [performance, setPerformance] = useState<any>(null);
  const [strategies, setStrategies] = useState<any[]>([]);
  const [telemetry, setTelemetry] = useState<any>(null);
  const [decisions, setDecisions] = useState<any[]>([]);
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null);

  // Live Market State from Bitget Feed
  const [selectedSymbol, setSelectedSymbol] = useState<'BTCUSDT' | 'ETHUSDT'>('BTCUSDT');
  const [ticker, setTicker] = useState<any>(null);
  const [depth, setDepth] = useState<{ bids: Array<[string, string]>; asks: Array<[string, string]> }>({
    bids: [],
    asks: [],
  });
  const [liveTrades, setLiveTrades] = useState<any[]>([]);

  // Agentic AI Brain & Telemetry
  const [agenticStatus, setAgenticStatus] = useState<AgenticStatus | null>(null);
  const [macroRadar, setMacroRadar] = useState<MacroRadarData | null>(null);
  const [reflexStatus, setReflexStatus] = useState<ReflexStatus | null>(null);

  // Feedback notifications
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const [diagnosticRunning, setDiagnosticRunning] = useState(false);
  const [baselineLoading, setBaselineLoading] = useState(false);

  const fetchAllData = async () => {
    try {
      const [pos, ords, fls, bals, perf, strats, decs, agentic, radar, rflx, tk, dp, tr, tel] =
        await Promise.all([
          api.getPositions().catch(() => []),
          api.getOrders().catch(() => []),
          api.getFills().catch(() => []),
          api.getBalances().catch(() => []),
          api.getPerformance().catch(() => null),
          api.getStrategies().catch(() => []),
          api.getStrategyDecisions().catch(() => []),
          api.getAgenticStatus(selectedSymbol).catch(() => null),
          api.getMacroRadar().catch(() => null),
          api.getReflexStatus(selectedSymbol).catch(() => null),
          api.getMarketTicker(selectedSymbol).catch(() => null),
          api.getMarketDepth(selectedSymbol).catch(() => null),
          api.getMarketTrades(selectedSymbol).catch(() => []),
          api.getStrategyTelemetry(selectedSymbol).catch(() => null),
        ]);

      setPositions(pos);
      setOrders(ords);
      setFills(fls);
      setBalances(bals);
      setPerformance(perf);
      setStrategies(strats);
      setDecisions(decs);
      if (agentic) setAgenticStatus(agentic);
      if (radar) setMacroRadar(radar);
      if (rflx) setReflexStatus(rflx);
      if (tk) setTicker(tk);
      if (dp) setDepth({ bids: dp.bids || [], asks: dp.asks || [] });
      if (tr && tr.length > 0) setLiveTrades(tr.slice(0, 10));
      if (tel) setTelemetry(tel);
    } catch {
      // transient read error
    }
  };

  useEffect(() => {
    fetchAllData();
    const interval = setInterval(fetchAllData, 1000);
    return () => clearInterval(interval);
  }, [selectedSymbol]);

  const isAllPaused = strategies.length > 0 && strategies.every((s) => s.status === 'PAUSED');

  const handlePauseAll = async () => {
    try {
      await api.pauseAllTrading();
      setActionNotice('All autonomous quant algorithms PAUSED. Market stream remains active.');
      fetchAllData();
      setTimeout(() => setActionNotice(null), 5000);
    } catch (e: any) {
      alert(`Failed to pause trading: ${e.message}`);
    }
  };

  const handleResumeAll = async () => {
    try {
      await api.resumeAllTrading();
      setActionNotice('All autonomous quant algorithms RESUMED in 3x leverage regime.');
      fetchAllData();
      setTimeout(() => setActionNotice(null), 5000);
    } catch (e: any) {
      alert(`Failed to resume trading: ${e.message}`);
    }
  };

  const handleEmergencyStopAll = async () => {
    if (!window.confirm('Trigger Emergency Stop? This flattens all open positions immediately against live Bitget depth.')) {
      return;
    }
    try {
      await api.emergencyStopTrading();
      setActionNotice('EMERGENCY STOP EXECUTED: All trading halted and positions flattened.');
      fetchAllData();
      setTimeout(() => setActionNotice(null), 6000);
    } catch (e: any) {
      alert(`Emergency stop failed: ${e.message}`);
    }
  };

  const handleFlattenInstrument = async (symbol: string) => {
    try {
      await api.flattenPosition(symbol);
      setActionNotice(`Liquidated ${symbol} position against live Bitget depth.`);
      fetchAllData();
      setTimeout(() => setActionNotice(null), 5000);
    } catch (err: any) {
      setActionNotice(`Flatten error: ${err.message}`);
    }
  };

  const handleTriggerSignal = async (side: string) => {
    setDiagnosticRunning(true);
    try {
      const stratId = `unified-${selectedSymbol.slice(0, 3).toLowerCase()}`;
      await api.triggerDiagnosticSignal(stratId, selectedSymbol, side);
      setActionNotice(`Diagnostic ${side} intent emitted for ${stratId} and matched against live Bitget depth.`);
      await fetchAllData();
      setTimeout(() => setActionNotice(null), 5000);
    } catch (err: any) {
      setActionNotice(`Signal error: ${err.message}`);
    } finally {
      setDiagnosticRunning(false);
    }
  };

  const handleResetCleanBaseline = async () => {
    setBaselineLoading(true);
    try {
      await api.applyAIStrategy({
        symbol: selectedSymbol,
        maker_only_mode: true,
        entry_cooldown_s: selectedSymbol === 'ETHUSDT' ? 90 : 60,
        max_session_drawdown_pct: 3.0,
        atr_target_multiplier: selectedSymbol === 'ETHUSDT' ? 4.0 : 3.5,
        depth5_imbalance_threshold: selectedSymbol === 'ETHUSDT' ? 0.40 : 0.35,
        ml_gate_enabled: true,
        reset_capital: true,
      });
      await api.triggerReflexAudit('RESET_BASELINE', selectedSymbol);
      setActionNotice(`Clean 3x institutional baseline restored for ${selectedSymbol}. Capital reset to $10,000.`);
      await fetchAllData();
      setTimeout(() => setActionNotice(null), 6000);
    } catch (e: any) {
      alert(`Baseline reset failed: ${e.message}`);
    } finally {
      setBaselineLoading(false);
    }
  };

  // Helper metrics
  const bal = balances[0] || {};
  const totalEquity = bal.total || '10000.00';
  const availableCash = bal.available || '10000.00';
  const lockedMargin = bal.locked_margin || '0.00';
  const unrealizedPnl = bal.unrealized_pnl || '0.00';
  const realizedPnl = bal.realized_pnl || '0.00';
  const totalProfit = bal.total_profit || '0.00';
  const btcProfit = bal.btc_profit || '0.00';
  const ethProfit = bal.eth_profit || '0.00';

  const numTotalProfit = parseFloat(totalProfit);
  const numUnrealized = parseFloat(unrealizedPnl);
  const numRealized = parseFloat(realizedPnl);

  const obi = telemetry?.depth5_imbalance !== null && telemetry?.depth5_imbalance !== undefined
    ? Number(telemetry.depth5_imbalance)
    : 0;
  const obiPercent = Math.round(obi * 100);
  const midPrice = telemetry?.mid || (ticker?.last_price ? Number(ticker.last_price) : 76119.5);
  const microPrice = telemetry?.microprice || midPrice;
  const microSkew = microPrice - midPrice;

  // Agentic indicators
  const currentBias = agenticStatus?.current_bias || 'STAND_ASIDE';
  const currentRegime = agenticStatus?.current_regime || 'NEUTRAL_CHOP';
  const tacticalState = agenticStatus?.tactical_state || 'SCANNING';
  const dynParams = agenticStatus?.dynamic_parameters;
  const weights = dynParams?.indicator_weights || {};
  const memSummary = agenticStatus?.memory_summary;

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* 1. Page Header & Live Status Bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-200 dark:border-slate-800 pb-4">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <div className="p-1.5 bg-indigo-50 dark:bg-indigo-950/70 rounded-lg text-indigo-600 dark:text-indigo-400">
              <Bot className="w-6 h-6" />
            </div>
            <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
              Autonomous Quant Cockpit
            </h1>
            <span className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 dark:bg-emerald-950 text-emerald-700 dark:text-emerald-400">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
              Bitget Live WebSocket
            </span>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-bold bg-indigo-100 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300">
              3x Leverage Isolated
            </span>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-bold bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300">
              Maker 0.02% / Taker 0.06%
            </span>
          </div>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
            Single Unified Self-Learning Agentic Alpha Engine per leg. Hierarchical 4-Tier filtration funnel with multi-speed quant heartbeat.
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {/* Symbol Switcher */}
          <div className="flex bg-slate-100 dark:bg-slate-800 p-1 rounded-lg">
            {(['BTCUSDT', 'ETHUSDT'] as const).map((sym) => (
              <button
                key={sym}
                onClick={() => setSelectedSymbol(sym)}
                className={`px-3 py-1 rounded-md text-xs font-bold transition-all ${
                  selectedSymbol === sym
                    ? 'bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-400 shadow-sm'
                    : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100'
                }`}
              >
                {sym}
              </button>
            ))}
          </div>

          <button
            onClick={fetchAllData}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold border rounded-lg bg-white dark:bg-slate-900 border-slate-300 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Refresh
          </button>

          {!isViewer && (
            <>
              {isAllPaused ? (
                <button
                  data-testid="resume-trading-btn"
                  onClick={handleResumeAll}
                  className="flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-bold rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white shadow-sm transition-colors"
                >
                  <Play className="w-3.5 h-3.5 fill-current" />
                  Resume Trading
                </button>
              ) : (
                <button
                  data-testid="pause-trading-btn"
                  onClick={handlePauseAll}
                  className="flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-bold rounded-lg bg-amber-500 hover:bg-amber-600 text-slate-950 shadow-sm transition-colors"
                >
                  <Pause className="w-3.5 h-3.5 fill-current" />
                  Pause Trading
                </button>
              )}

              <button
                data-testid="stop-flatten-btn"
                onClick={handleEmergencyStopAll}
                className="flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-bold rounded-lg bg-rose-600 hover:bg-rose-700 text-white shadow-sm transition-colors"
              >
                <AlertOctagon className="w-3.5 h-3.5" />
                Stop & Flatten All
              </button>
            </>
          )}
        </div>
      </div>

      {actionNotice && (
        <div className="p-3 bg-indigo-50 dark:bg-indigo-950/40 border border-indigo-200 dark:border-indigo-800 text-indigo-800 dark:text-indigo-300 rounded-xl text-xs font-semibold flex items-center justify-between shadow-sm">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
            <span>{actionNotice}</span>
          </div>
          <button onClick={() => setActionNotice(null)} className="text-slate-400 hover:text-slate-600">✕</button>
        </div>
      )}

      {/* 2. Master Portfolio & 3x Margin Summary Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3.5">
        {/* Total Equity */}
        <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-1">
          <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider block">Total Equity</span>
          <div className="text-2xl font-bold font-mono text-slate-900 dark:text-slate-100">
            {formatUsd(totalEquity)}
          </div>
          <span className="text-[10px] text-slate-400 block font-mono">
            Initial $10,000.00 + Net P&amp;L
          </span>
        </div>

        {/* Total Net Profit */}
        <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-1">
          <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider block">Total Net Profit</span>
          <div className={`text-2xl font-bold font-mono flex items-baseline gap-2 ${numTotalProfit >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}`}>
            {formatUsd(totalProfit, true)}
            <span className={`text-xs px-1.5 py-0.5 rounded font-bold font-mono ${numTotalProfit >= 0 ? 'bg-emerald-100 dark:bg-emerald-950 text-emerald-700 dark:text-emerald-400' : 'bg-rose-100 dark:bg-rose-950 text-rose-700 dark:text-rose-400'}`}>
              {performance?.total_return_pct || '0.00%'}
            </span>
          </div>
          <span className="text-[10px] text-slate-400 block font-mono">
            Realized {formatUsd(realizedPnl, true)} | Floating {formatUsd(unrealizedPnl, true)}
          </span>
        </div>

        {/* Available Purchasing Power */}
        <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-1">
          <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider block">Available Cash</span>
          <div className="text-2xl font-bold font-mono text-emerald-600 dark:text-emerald-400">
            {formatUsd(availableCash)}
          </div>
          <span className="text-[10px] text-slate-400 block font-mono">
            Free Equity for New Allocations
          </span>
        </div>

        {/* Locked Margin (3x Leverage) */}
        <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-1">
          <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider block">Locked Margin (3x)</span>
          <div className="text-2xl font-bold font-mono text-indigo-600 dark:text-indigo-400">
            {formatUsd(lockedMargin)}
          </div>
          <span className="text-[10px] text-slate-400 block font-mono">
            Exactly 33.33% Notional Collateral
          </span>
        </div>

        {/* Leg Profit Breakdown */}
        <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-1">
          <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider block">Leg P&amp;L Breakdown</span>
          <div className="text-xs font-mono space-y-0.5 pt-0.5">
            <div className="flex justify-between">
              <span className="text-amber-500 font-bold">BTC:</span>
              <span className={`font-bold ${parseFloat(btcProfit) >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}`}>
                {formatUsd(btcProfit, true)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-indigo-500 font-bold">ETH:</span>
              <span className={`font-bold ${parseFloat(ethProfit) >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}`}>
                {formatUsd(ethProfit, true)}
              </span>
            </div>
          </div>
          <span className="text-[10px] text-slate-400 block font-mono">
            Win Rate: {performance?.win_rate_pct || '0.0%'} ({performance?.total_trades || 0} trades)
          </span>
        </div>
      </div>

      {/* 3. UNIFIED SELF-LEARNING AGENTIC ALPHA ENGINE (HERO COMPONENT) */}
      <div className="bg-white dark:bg-slate-900 rounded-2xl border border-indigo-200 dark:border-indigo-900/60 shadow-sm p-5 space-y-5">
        <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4 border-b border-slate-100 dark:border-slate-800 pb-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2.5 flex-wrap">
              <div className="p-1.5 bg-indigo-50 dark:bg-indigo-950/80 rounded-lg text-indigo-600 dark:text-indigo-400">
                <Brain className="w-5 h-5" />
              </div>
              <h2 className="text-lg font-bold text-slate-900 dark:text-slate-100">
                Unified Agentic Alpha Engine ({selectedSymbol})
              </h2>
              <span className="px-2.5 py-0.5 rounded font-mono text-xs font-bold bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300">
                unified-{selectedSymbol.slice(0, 3).toLowerCase()}
              </span>

              {/* Directional Bias Badge */}
              <span className={`px-3 py-0.5 rounded-full text-xs font-extrabold font-mono tracking-wide ${
                currentBias === 'LONG_ONLY'
                  ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300 border border-emerald-300/40'
                  : currentBias === 'SHORT_ONLY'
                  ? 'bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300 border border-rose-300/40'
                  : 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300 border border-amber-300/40'
              }`}>
                BIAS: {currentBias} ({currentRegime})
              </span>
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Fuses Macro Compass, Squeeze Tactical Setup, and L2 Micro Sniper into a single holistic decision engine with autoregressive self-learning.
            </p>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={() => handleTriggerSignal('BUY')}
              disabled={diagnosticRunning || isViewer}
              className="px-3 py-1.5 text-xs font-bold rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white flex items-center gap-1.5 shadow-sm disabled:opacity-50 transition-colors"
              title="Test simulated BUY against live Bitget depth"
            >
              <Zap className="w-3.5 h-3.5" />
              Test Buy Sniper
            </button>
            <button
              onClick={() => handleTriggerSignal('SELL')}
              disabled={diagnosticRunning || isViewer}
              className="px-3 py-1.5 text-xs font-bold rounded-lg bg-rose-600 hover:bg-rose-700 text-white flex items-center gap-1.5 shadow-sm disabled:opacity-50 transition-colors"
              title="Test simulated SELL against live Bitget depth"
            >
              <Zap className="w-3.5 h-3.5" />
              Test Sell Sniper
            </button>
            <button
              onClick={handleResetCleanBaseline}
              disabled={baselineLoading || isViewer}
              className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 transition-colors disabled:opacity-50"
            >
              <RotateCcw className={`w-3.5 h-3.5 ${baselineLoading ? 'animate-spin' : ''}`} />
              Reset Baseline
            </button>
          </div>
        </div>

        {/* 4-Tier Hierarchical Funnel Visualizer */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          {/* Tier 1: Macro & Regime Compass */}
          <div className="p-3 bg-slate-50 dark:bg-slate-800/40 rounded-xl border border-slate-200 dark:border-slate-800 space-y-1.5">
            <div className="flex items-center justify-between text-[11px] font-bold text-slate-500 uppercase">
              <span>Tier 1: Macro Compass</span>
              <span className="text-indigo-600 dark:text-indigo-400">15m–2H</span>
            </div>
            <div className="text-sm font-bold font-mono text-slate-900 dark:text-slate-100 flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-indigo-500"></span>
              Score: {macroRadar?.macro_report?.warning_strength !== undefined ? `${(10 - macroRadar.macro_report.warning_strength / 10).toFixed(1)}/10` : '6.5/10'}
            </div>
            <div className="text-[10px] text-slate-400 space-y-0.5">
              <div className="flex justify-between">
                <span>Fed Liq Z:</span>
                <span className="font-mono text-slate-700 dark:text-slate-300">
                  {macroRadar?.macro_report?.fed_snapshot?.z_score?.toFixed(2) ?? '+0.45'} &sigma;
                </span>
              </div>
              <div className="flex justify-between">
                <span>Whale Net Flow:</span>
                <span className={`font-mono font-semibold ${
                  (macroRadar?.whale_positioning?.[selectedSymbol]?.net_flow_zscore ?? 0) >= 0 ? 'text-emerald-500' : 'text-rose-500'
                }`}>
                  {macroRadar?.whale_positioning?.[selectedSymbol]?.net_flow_zscore?.toFixed(2) ?? '+0.25'} &sigma;
                </span>
              </div>
            </div>
          </div>

          {/* Tier 2: Tactical Setup Engine */}
          <div className="p-3 bg-slate-50 dark:bg-slate-800/40 rounded-xl border border-slate-200 dark:border-slate-800 space-y-1.5">
            <div className="flex items-center justify-between text-[11px] font-bold text-slate-500 uppercase">
              <span>Tier 2: Tactical Setup</span>
              <span className="text-indigo-600 dark:text-indigo-400">1m–5m</span>
            </div>
            <div className="text-sm font-bold font-mono text-slate-900 dark:text-slate-100 truncate" title={tacticalState}>
              {tacticalState}
            </div>
            <div className="text-[10px] text-slate-400 space-y-0.5">
              <div className="flex justify-between">
                <span>Squeeze Momentum:</span>
                <span className="font-bold text-indigo-500 font-mono">Linreg State</span>
              </div>
              <div className="flex justify-between">
                <span>Volatility Hurdle:</span>
                <span className="font-mono text-emerald-600 dark:text-emerald-400 font-bold">
                  &ge; {dynParams?.volatility_hurdle_bps ?? 12.0} bps
                </span>
              </div>
            </div>
          </div>

          {/* Tier 3: Microstructural Sniper */}
          <div className="p-3 bg-slate-50 dark:bg-slate-800/40 rounded-xl border border-slate-200 dark:border-slate-800 space-y-1.5">
            <div className="flex items-center justify-between text-[11px] font-bold text-slate-500 uppercase">
              <span>Tier 3: Micro Sniper</span>
              <span className="text-indigo-600 dark:text-indigo-400">L2 Sub-sec</span>
            </div>
            <div className="text-sm font-bold font-mono flex items-center justify-between">
              <span className={obi >= 0 ? 'text-emerald-600' : 'text-rose-600'}>
                OBI: {obi >= 0 ? '+' : ''}{obi.toFixed(2)}
              </span>
              <span className="text-[11px] text-slate-400 font-normal">
                Req: &plusmn;{dynParams?.depth5_threshold ?? 0.35}
              </span>
            </div>
            <div className="text-[10px] text-slate-400 space-y-0.5">
              <div className="flex justify-between">
                <span>Microprice Skew:</span>
                <span className={`font-mono font-bold ${microSkew >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
                  {formatUsd(microSkew, true)}
                </span>
              </div>
              <div className="flex justify-between">
                <span>Execution Type:</span>
                <span className="font-mono text-emerald-600 dark:text-emerald-400 font-bold">Passive Maker (0%)</span>
              </div>
            </div>
          </div>

          {/* Tier 4: 3x Leverage Risk & Ratchet */}
          <div className="p-3 bg-slate-50 dark:bg-slate-800/40 rounded-xl border border-slate-200 dark:border-slate-800 space-y-1.5">
            <div className="flex items-center justify-between text-[11px] font-bold text-slate-500 uppercase">
              <span>Tier 4: 3x Risk &amp; SL</span>
              <span className="text-indigo-600 dark:text-indigo-400">Ratchet</span>
            </div>
            <div className="text-sm font-bold font-mono text-indigo-600 dark:text-indigo-400">
              {dynParams?.atr_target_mult ?? '3.0'}x ATR Target
            </div>
            <div className="text-[10px] text-slate-400 space-y-0.5">
              <div className="flex justify-between">
                <span>Stop Ratchet:</span>
                <span className="font-mono text-emerald-600 dark:text-emerald-400 font-semibold">Post-Entry Profit Only</span>
              </div>
              <div className="flex justify-between">
                <span>Margin Locked:</span>
                <span className="font-mono text-indigo-600 dark:text-indigo-400 font-bold">33.33% Notional</span>
              </div>
            </div>
          </div>
        </div>

        {/* Multi-Speed Self-Learning & Dynamic Indicator Weights */}
        <div className="bg-slate-50 dark:bg-slate-950/60 rounded-xl p-4 border border-slate-200 dark:border-slate-800 space-y-3">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-200/60 dark:border-slate-800 pb-2">
            <div className="flex items-center gap-2">
              <Activity className="w-4 h-4 text-indigo-500" />
              <span className="text-xs font-bold text-slate-800 dark:text-slate-200">
                Self-Learning Autoregressive Indicator Weights ($w_i$) &amp; Rolling Information Coefficients
              </span>
            </div>
            <div className="flex items-center gap-3 text-[10px] font-mono text-slate-400">
              <span>Fast Rhythm: Cooldown {dynParams?.entry_cooldown_s ?? 60}s</span>
              <span>Medium: Rolling 10-Trade IC</span>
              <span>Episodes: {memSummary?.total_episodes ?? 0}</span>
            </div>
          </div>

          {/* Indicator Weight Badges */}
          <div className="grid grid-cols-2 sm:grid-cols-5 lg:grid-cols-10 gap-2 text-center text-xs font-mono">
            {Object.entries(weights).length > 0 ? (
              Object.entries(weights).map(([name, weight]) => (
                <div key={name} className="p-2 bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800">
                  <span className="text-[10px] text-slate-400 uppercase font-bold block truncate">{name}</span>
                  <span className={`text-sm font-bold block mt-0.5 ${
                    weight > 1.0 ? 'text-emerald-600 dark:text-emerald-400' : weight < 1.0 ? 'text-amber-600 dark:text-amber-400' : 'text-slate-700 dark:text-slate-300'
                  }`}>
                    {weight.toFixed(2)}x
                  </span>
                  <span className="text-[9px] text-slate-400 block font-sans">
                    {weight > 1.0 ? 'Overweighted' : weight < 1.0 ? 'Downweighted' : 'Baseline'}
                  </span>
                </div>
              ))
            ) : (
              ['rqk', 'mcginley', 'squeeze', 'cmf', 'stc', 'qqe', 'adx', 'chandelier', 'volume_delta', 'donchian'].map((name) => (
                <div key={name} className="p-2 bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800">
                  <span className="text-[10px] text-slate-400 uppercase font-bold block truncate">{name}</span>
                  <span className="text-sm font-bold text-slate-700 dark:text-slate-300 block mt-0.5">1.00x</span>
                  <span className="text-[9px] text-slate-400 block font-sans">Baseline</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* 4. Live Market Depth, Tape & Autonomous Decision Stream */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Col 1: Live L2 Order Book Depth */}
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm p-4 space-y-3">
          <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800 pb-2">
            <h3 className="font-bold text-sm text-slate-900 dark:text-slate-100 flex items-center gap-1.5">
              <Layers className="w-4 h-4 text-indigo-600" />
              Live Bitget L2 Depth ({selectedSymbol})
            </h3>
            <span className="text-[10px] font-mono text-emerald-600 font-bold">Streaming</span>
          </div>

          <div className="space-y-1.5 font-mono text-xs">
            {/* Asks (Red) */}
            <div className="space-y-1">
              <span className="text-[10px] text-rose-500 font-bold uppercase block">Asks (Sell Depth)</span>
              {depth.asks.slice(0, 5).reverse().map(([price, size], idx) => (
                <div key={idx} className="flex justify-between items-center text-[11px] text-rose-600 dark:text-rose-400 bg-rose-50/40 dark:bg-rose-950/20 px-2 py-0.5 rounded">
                  <span>${formatUsd(price)}</span>
                  <span>{parseFloat(size).toFixed(3)}</span>
                </div>
              ))}
            </div>

            {/* Mid Price Separator */}
            <div className="py-1 my-1 border-y border-dashed border-slate-200 dark:border-slate-700 flex justify-between items-center text-xs font-bold text-slate-800 dark:text-slate-200">
              <span>Spread: {telemetry?.spread_bps ? Number(telemetry.spread_bps).toFixed(2) : '0.15'} bps</span>
              <span className="text-indigo-600 dark:text-indigo-400">Mid: ${formatUsd(midPrice)}</span>
            </div>

            {/* Bids (Green) */}
            <div className="space-y-1">
              <span className="text-[10px] text-emerald-500 font-bold uppercase block">Bids (Buy Depth)</span>
              {depth.bids.slice(0, 5).map(([price, size], idx) => (
                <div key={idx} className="flex justify-between items-center text-[11px] text-emerald-600 dark:text-emerald-400 bg-emerald-50/40 dark:bg-emerald-950/20 px-2 py-0.5 rounded">
                  <span>${formatUsd(price)}</span>
                  <span>{parseFloat(size).toFixed(3)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Col 2: Level-5 OBI Gauge & Microstructure Telemetry */}
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm p-4 space-y-4 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between border-b border-slate-100 dark:border-slate-800 pb-2">
              <h3 className="font-bold text-sm text-slate-900 dark:text-slate-100 flex items-center gap-1.5">
                <Gauge className="w-4 h-4 text-indigo-600" />
                Microstructure Imbalance &amp; Flow
              </h3>
              <span className="text-[10px] font-mono text-slate-400">Real-Time</span>
            </div>

            {/* OBI Visual Gauge */}
            <div className="mt-3 space-y-2">
              <div className="flex justify-between items-center text-xs">
                <span className="font-semibold text-slate-700 dark:text-slate-300">Level-5 Order Book Imbalance (OBI)</span>
                <span className={`font-mono font-bold ${obi >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                  {obi >= 0 ? '+' : ''}{obi.toFixed(3)} ({obiPercent}%)
                </span>
              </div>

              {/* Progress Bar with neutral center (0) */}
              <div className="h-4 bg-slate-100 dark:bg-slate-800 rounded-full relative overflow-hidden flex">
                <div
                  className="h-full bg-rose-500 transition-all duration-300 ml-auto"
                  style={{ width: obi < 0 ? `${Math.min(Math.abs(obi) * 50, 50)}%` : '0%' }}
                ></div>
                <div className="w-0.5 h-full bg-slate-400 z-10"></div>
                <div
                  className="h-full bg-emerald-500 transition-all duration-300"
                  style={{ width: obi > 0 ? `${Math.min(obi * 50, 50)}%` : '0%' }}
                ></div>
              </div>

              <div className="flex justify-between text-[10px] text-slate-400 font-mono">
                <span>Ask Dominance (-1.0)</span>
                <span>Threshold &plusmn;{dynParams?.depth5_threshold ?? 0.35}</span>
                <span>Bid Dominance (+1.0)</span>
              </div>
            </div>

            {/* Key Micro Metrics Grid */}
            <div className="grid grid-cols-2 gap-3 mt-4">
              <div className="p-3 bg-slate-50 dark:bg-slate-800/50 rounded-lg space-y-1">
                <span className="text-[10px] text-slate-400 uppercase font-semibold block">Microprice Skew</span>
                <span className={`font-mono font-bold text-sm ${microSkew >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                  {formatUsd(microSkew, true)}
                </span>
                <span className="text-[10px] text-slate-400 block font-mono">Micro: {formatUsd(microPrice)}</span>
              </div>

              <div className="p-3 bg-slate-50 dark:bg-slate-800/50 rounded-lg space-y-1">
                <span className="text-[10px] text-slate-400 uppercase font-semibold block">Signed Flow (1s)</span>
                <span className={`font-mono font-bold text-sm ${Number(telemetry?.volume_1s_signed || 0) >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                  {Number(telemetry?.volume_1s_signed || 0) >= 0 ? '+' : ''}
                  {Number(telemetry?.volume_1s_signed || 0).toFixed(4)}
                </span>
                <span className="text-[10px] text-slate-400 block font-mono">Taker Net Flow</span>
              </div>
            </div>
          </div>

          {/* Recent Public Trades from Bitget */}
          <div className="pt-2 border-t border-slate-100 dark:border-slate-800 space-y-1">
            <span className="text-[10px] text-slate-400 font-semibold uppercase block">Live Trade Prints</span>
            <div className="space-y-1 font-mono text-[11px] max-h-24 overflow-y-auto pr-1">
              {liveTrades.slice(0, 4).map((tr, idx) => (
                <div key={idx} className="flex justify-between items-center text-slate-600 dark:text-slate-400">
                  <span className={tr.side === 'BUY' ? 'text-emerald-600 font-bold' : 'text-rose-600 font-bold'}>
                    {tr.side} ${formatUsd(tr.price)}
                  </span>
                  <span>{parseFloat(tr.size).toFixed(3)}</span>
                  <span className="text-[10px] text-slate-400">{new Date(tr.ts).toLocaleTimeString()}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Col 3: AI Decisions Stream Terminal */}
        <div className="bg-slate-950 text-slate-100 rounded-xl border border-slate-800 shadow-sm p-4 flex flex-col justify-between text-xs font-mono">
          <div className="flex items-center justify-between border-b border-slate-800 pb-2">
            <div className="flex items-center gap-2">
              <Terminal className="w-4 h-4 text-emerald-400" />
              <span className="font-bold text-xs text-slate-200">AI Decision Stream</span>
            </div>
            <span className="text-[10px] text-emerald-400 font-semibold flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
              Live Autonomous Log
            </span>
          </div>

          <div className="space-y-2 my-2 overflow-y-auto max-h-72 pr-1">
            {decisions.length === 0 ? (
              <div className="text-slate-500 py-12 text-center text-xs">
                Scanning Bitget feed... Evaluating unified alpha logic...
              </div>
            ) : (
              decisions.map((dec, idx) => (
                <div key={idx} className="border-b border-slate-900 pb-1.5 last:border-0">
                  <div className="flex justify-between text-[10px] text-slate-500">
                    <span>{new Date(Number(dec.timestamp_ns / 1_000_000)).toLocaleTimeString()}</span>
                    <span className="text-indigo-400 font-bold">{dec.strategy_id}</span>
                  </div>
                  <div className="text-[11px] mt-0.5">
                    <span className={`font-bold mr-1.5 ${
                      dec.action.includes('BUY') || dec.action.includes('ENTER')
                        ? 'text-emerald-400'
                        : dec.action.includes('SELL') || dec.action.includes('EXIT')
                        ? 'text-rose-400'
                        : 'text-slate-400'
                    }`}>
                      [{dec.action}]
                    </span>
                    <span className="text-slate-300">{dec.reason}</span>
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="pt-2 border-t border-slate-900 text-[10px] text-slate-500 flex justify-between">
            <span>Execution: Maker Post-Only Limit</span>
            <span className="text-emerald-400 font-semibold">Bitget 3x Isolated</span>
          </div>
        </div>
      </div>

      {/* 5. Active Strategy Positions Table */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
        <div className="p-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
          <div>
            <h2 className="font-bold text-base text-slate-900 dark:text-slate-100">
              Active Strategy Positions
            </h2>
            <p className="text-xs text-slate-500">
              Marked to market continuously against live Bitget WebSocket feed with exact 33.33% margin locked.
            </p>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table data-testid="positions-table" className="w-full text-left text-xs whitespace-nowrap">
            <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 font-semibold border-b border-slate-100 dark:border-slate-800">
              <tr>
                <th className="py-3 px-4">Strategy</th>
                <th className="py-3 px-4">Instrument</th>
                <th className="py-3 px-4">Side</th>
                <th className="py-3 px-4">Size</th>
                <th className="py-3 px-4">Entry Price</th>
                <th className="py-3 px-4">Live Mark Price</th>
                <th className="py-3 px-4">Unrealized PnL</th>
                <th className="py-3 px-4">3x Margin Equity</th>
                <th className="py-3 px-4 text-right">Emergency Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {positions.length === 0 ? (
                <tr>
                  <td colSpan={9} className="py-6 text-center text-slate-400 font-mono">
                    No open positions held. Algorithms in cash / scanning mode.
                  </td>
                </tr>
              ) : (
                positions.map((pos, idx) => (
                  <tr key={idx} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/30">
                    <td className="py-3 px-4 font-semibold text-indigo-600 dark:text-indigo-400">
                      {pos.strategy_id}
                    </td>
                    <td className="py-3 px-4 font-bold font-mono text-slate-900 dark:text-slate-100">
                      {pos.instrument_id}
                    </td>
                    <td className="py-3 px-4">
                      <span className={`px-2 py-0.5 rounded font-bold uppercase ${
                        pos.side === 'BUY'
                          ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400'
                          : 'bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-400'
                      }`}>
                        {pos.side}
                      </span>
                    </td>
                    <td className="py-3 px-4 font-mono font-medium">{pos.units || pos.lots}</td>
                    <td className="py-3 px-4 font-mono">{formatUsd(pos.entry_price)}</td>
                    <td className="py-3 px-4 font-mono font-bold text-indigo-600 dark:text-indigo-400">
                      {formatUsd(ticker?.mark_price || pos.mark_price)}
                    </td>
                    <td className={`py-3 px-4 font-mono font-bold ${
                      parseFloat(pos.unrealized_pnl || '0') >= 0 ? 'text-emerald-600' : 'text-rose-600'
                    }`}>
                      {formatUsd(pos.unrealized_pnl, true)}
                    </td>

                    <td className="py-3 px-4 font-mono text-slate-600 dark:text-slate-400">
                      {formatUsd(pos.margin_equity)} (33%)
                    </td>
                    <td className="py-3 px-4 text-right">
                      {!isViewer && (
                        <button
                          onClick={() => handleFlattenInstrument(pos.instrument_id)}
                          className="px-2.5 py-1 text-[11px] font-semibold bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-400 border border-rose-200 dark:border-rose-900 rounded hover:bg-rose-100 transition-colors"
                        >
                          Flatten
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 6. Executed Fills & Orders Tables */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Executed Fills Table */}
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
          <div className="p-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
            <div>
              <h2 className="font-bold text-base text-slate-900 dark:text-slate-100">
                Executed Fills (Bitget Depth Match)
              </h2>
              <p className="text-xs text-slate-500">
                Executions confirmed against live exchange liquidity with real 0.02% Maker fee accounting.
              </p>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table data-testid="fills-table" className="w-full text-left text-xs whitespace-nowrap">
              <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 font-semibold border-b border-slate-100 dark:border-slate-800">
                <tr>
                  <th className="py-3 px-4">Fill ID</th>
                  <th className="py-3 px-4">Side</th>
                  <th className="py-3 px-4">Size</th>
                  <th className="py-3 px-4">Price</th>
                  <th className="py-3 px-4">Fee (USDT)</th>
                  <th className="py-3 px-4">Liquidity</th>
                  <th className="py-3 px-4 text-right">Audit</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {fills.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-6 text-center text-slate-400 font-mono">
                      No fills recorded yet.
                    </td>
                  </tr>
                ) : (
                  fills.slice(0, 10).map((fl) => (
                    <tr key={fl.fill_id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/30">
                      <td className="py-3 px-4 font-mono text-slate-700 dark:text-slate-300">{fl.fill_id}</td>
                      <td className="py-3 px-4">
                        <span className={`px-2 py-0.5 rounded font-bold uppercase ${
                          fl.side === 'BUY'
                            ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400'
                            : 'bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-400'
                        }`}>
                          {fl.side}
                        </span>
                      </td>
                      <td className="py-3 px-4 font-mono">{fl.qty}</td>
                      <td className="py-3 px-4 font-mono font-bold">{formatUsd(fl.price)}</td>
                      <td className="py-3 px-4 font-mono text-slate-500">-{fl.fee} USDT</td>
                      <td className="py-3 px-4">
                        <span className="px-1.5 py-0.5 rounded text-[10px] bg-slate-100 dark:bg-slate-800 font-mono">
                          {fl.liquidity}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <button
                          onClick={() => setSelectedOrderId(fl.order_id)}
                          className="text-indigo-600 hover:text-indigo-800 dark:text-indigo-400 font-semibold"
                        >
                          Trace
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Autonomous Bot Orders Table */}
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
          <div className="p-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
            <div>
              <h2 className="font-bold text-base text-slate-900 dark:text-slate-100">
                Autonomous Bot Orders
              </h2>
              <p className="text-xs text-slate-500">Orders placed automatically by AI quant strategies.</p>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs whitespace-nowrap">
              <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 font-semibold border-b border-slate-100 dark:border-slate-800">
                <tr>
                  <th className="py-3 px-4">Order ID</th>
                  <th className="py-3 px-4">Strategy</th>
                  <th className="py-3 px-4">Side</th>
                  <th className="py-3 px-4">Qty</th>
                  <th className="py-3 px-4">Price</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4 text-right">Audit</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {orders.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-6 text-center text-slate-400 font-mono">
                      No orders placed yet.
                    </td>
                  </tr>
                ) : (
                  orders.slice(0, 10).map((ord) => (
                    <tr key={ord.order_id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/30">
                      <td className="py-3 px-4 font-mono text-slate-700 dark:text-slate-300">{ord.order_id}</td>
                      <td className="py-3 px-4 font-semibold text-indigo-600 dark:text-indigo-400">{ord.strategy_id}</td>
                      <td className="py-3 px-4">
                        <span className={`px-2 py-0.5 rounded font-bold uppercase ${
                          ord.side === 'BUY'
                            ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400'
                            : 'bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-400'
                        }`}>
                          {ord.side}
                        </span>
                      </td>
                      <td className="py-3 px-4 font-mono">{ord.qty}</td>
                      <td className="py-3 px-4 font-mono">{formatUsd(ord.limit_price)}</td>
                      <td className="py-3 px-4">
                        <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400">
                          {ord.status}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <button
                          onClick={() => setSelectedOrderId(ord.order_id)}
                          className="text-indigo-600 hover:text-indigo-800 dark:text-indigo-400 font-semibold"
                        >
                          Trace
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Order Trace Modal Hook (§15.2) */}
      <OrderTraceModal
        orderId={selectedOrderId}
        onClose={() => setSelectedOrderId(null)}
      />
    </div>
  );
};
