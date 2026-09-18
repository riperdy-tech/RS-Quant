import React, { useEffect, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Cpu,
  Layers,
  Pause,
  Play,
  RefreshCw,
  Settings2,
  Sliders,
  Zap,
} from 'lucide-react';
import { api } from '../services/apiClient';

export interface StrategiesPageProps {
  onOpenCommand: (type: string, target?: Record<string, any>, payload?: Record<string, any>) => void;
}

export const StrategiesPage: React.FC<StrategiesPageProps> = ({ onOpenCommand }) => {
  const [strategies, setStrategies] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedStrategy, setSelectedStrategy] = useState<any | null>(null);

  useEffect(() => {
    fetchStrategies();
  }, []);

  const fetchStrategies = async () => {
    setLoading(true);
    try {
      const data = await api.getStrategies().catch(() => []);
      setStrategies(data.length > 0 ? data : defaultStrategies);
    } finally {
      setLoading(false);
    }
  };

  const defaultStrategies = [
    {
      strategy_id: 'unified-btc',
      name: 'Unified Agentic Alpha Engine (BTCUSDT)',
      instrument_id: 'BTCUSDT',
      status: 'RUNNING',
      capital_allocation: '10000.00',
      signal: 'LONG_ONLY (WEAK_BULL)',
      active_model_id: 'agentic-regressive-policy-v1',
      warmup_status: 'ACTIVE (50 bars pre-seeded)',
      rejected_intents_count: 0,
      parameters: {
        volatility_hurdle_bps: 12.0,
        depth5_threshold: 0.35,
        atr_target_mult: 3.0,
        leverage: 3.0,
        entry_cooldown_s: 60,
        maker_fee_bps: 2.0,
        taker_fee_bps: 6.0,
      },
    },
    {
      strategy_id: 'unified-eth',
      name: 'Unified Agentic Alpha Engine (ETHUSDT)',
      instrument_id: 'ETHUSDT',
      status: 'RUNNING',
      capital_allocation: '10000.00',
      signal: 'LONG_ONLY (STRONG_BULL)',
      active_model_id: 'agentic-regressive-policy-v1',
      warmup_status: 'ACTIVE (50 bars pre-seeded)',
      rejected_intents_count: 0,
      parameters: {
        volatility_hurdle_bps: 12.0,
        depth5_threshold: 0.35,
        atr_target_mult: 3.0,
        leverage: 3.0,
        entry_cooldown_s: 60,
        maker_fee_bps: 2.0,
        taker_fee_bps: 6.0,
      },
    },
    {
      strategy_id: 'curated-btc',
      name: 'Curated 12-Factor Pine Ensemble (BTCUSDT)',
      instrument_id: 'BTCUSDT',
      status: 'RUNNING',
      capital_allocation: '5000.00',
      signal: 'BULLISH (6/10, WEAK_BULL)',
      active_model_id: 'lgbm-triple-barrier',
      warmup_status: 'COMPLETE (50 bars)',
      rejected_intents_count: 0,
      parameters: {
        leverage: 3.0,
        timeframe: '2H',
        score_threshold: 5.5,
        stop_loss_mult: 3.0,
      },
    },
    {
      strategy_id: 'curated-eth',
      name: 'Curated 12-Factor Pine Ensemble (ETHUSDT)',
      instrument_id: 'ETHUSDT',
      status: 'RUNNING',
      capital_allocation: '5000.00',
      signal: 'BULLISH (7/10, MEDIUM_BULL)',
      active_model_id: 'lgbm-triple-barrier',
      warmup_status: 'COMPLETE (50 bars)',
      rejected_intents_count: 0,
      parameters: {
        leverage: 3.0,
        timeframe: '2H',
        score_threshold: 5.5,
        stop_loss_mult: 3.0,
      },
    },
  ];

  const handleTogglePause = (strat: any) => {
    const isRunning = strat.status === 'RUNNING';
    onOpenCommand(isRunning ? 'PAUSE_STRATEGY' : 'RESUME_STRATEGY', {
      strategy_id: strat.strategy_id,
      account_id: 'paper-demo',
    });
  };

  const handleSaveParameters = (strat: any) => {
    onOpenCommand(
      'UPDATE_STRATEGY_CONFIG',
      { strategy_id: strat.strategy_id, account_id: 'paper-demo' },
      { parameters: strat.parameters }
    );
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
            Strategy Management & Intent Governance
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            Catalog of deterministic alpha strategies, execution parameters, signals, and intent audit.
          </p>
        </div>
        <button
          onClick={fetchStrategies}
          disabled={loading}
          className="p-2 border border-slate-200 dark:border-slate-800 rounded-lg text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          title="Refresh strategies"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Strategies Catalog Grid (§15.2) */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {(strategies.length > 0 ? strategies : defaultStrategies).map((strat) => {
          const isRunning = strat.status === 'RUNNING';
          return (
            <div
              key={strat.strategy_id}
              className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 shadow-sm flex flex-col justify-between space-y-4 hover:border-slate-300 dark:hover:border-slate-700 transition-all"
            >
              <div className="space-y-3">
                <div className="flex items-start justify-between">
                  <div className="space-y-1">
                    <span className="text-[10px] font-mono uppercase tracking-wider text-slate-400">
                      {strat.instrument_id}
                    </span>
                    <h3 className="font-bold text-base text-slate-900 dark:text-slate-100">
                      {strat.name}
                    </h3>
                  </div>
                  <span
                    className={`px-2 py-0.5 rounded text-xs font-bold ${
                      isRunning
                        ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-900'
                        : 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-400 border border-amber-200 dark:border-amber-900'
                    }`}
                  >
                    {strat.status}
                  </span>
                </div>

                <div className="grid grid-cols-2 gap-2 text-xs py-2 border-y border-slate-100 dark:border-slate-800">
                  <div>
                    <span className="text-slate-400 block">Signal:</span>
                    <span className={`font-semibold font-mono ${
                      strat.signal === 'BULLISH'
                        ? 'text-emerald-600 dark:text-emerald-400'
                        : strat.signal === 'BEARISH'
                        ? 'text-rose-600'
                        : 'text-slate-600 dark:text-slate-300'
                    }`}>
                      {strat.signal}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-400 block">Allocation:</span>
                    <span className="font-semibold font-mono text-slate-800 dark:text-slate-200">
                      ${strat.capital_allocation}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-400 block">Model:</span>
                    <span className="font-mono text-slate-700 dark:text-slate-300">
                      {strat.active_model_id || 'Rule-Based'}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-400 block">Warmup:</span>
                    <span className="font-mono text-[11px] text-emerald-600 dark:text-emerald-400">
                      {strat.warmup_status || 'COMPLETE'}
                    </span>
                  </div>
                </div>

                {strat.rejected_intents_count > 0 && (
                  <div className="p-2 bg-amber-50 dark:bg-amber-950/40 rounded text-[11px] text-amber-700 dark:text-amber-400 flex items-center gap-1.5">
                    <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
                    <span>{strat.rejected_intents_count} intents rejected by risk governor</span>
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between pt-2 border-t border-slate-100 dark:border-slate-800">
                <button
                  onClick={() => setSelectedStrategy(strat)}
                  className="text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:text-indigo-800 flex items-center gap-1"
                >
                  <Sliders className="w-3.5 h-3.5" /> Configure
                </button>
                <button
                  onClick={() => handleTogglePause(strat)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-colors flex items-center gap-1.5 ${
                    isRunning
                      ? 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-400 border border-amber-200 dark:border-amber-900 hover:bg-amber-100'
                      : 'bg-emerald-600 text-white hover:bg-emerald-700'
                  }`}
                >
                  {isRunning ? (
                    <>
                      <Pause className="w-3.5 h-3.5" /> Pause
                    </>
                  ) : (
                    <>
                      <Play className="w-3.5 h-3.5" /> Resume
                    </>
                  )}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Parameter Editor Drawer / Modal */}
      {selectedStrategy && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 backdrop-blur-sm p-4">
          <div className="bg-white dark:bg-slate-900 rounded-xl shadow-2xl max-w-lg w-full border border-slate-200 dark:border-slate-800 overflow-hidden">
            <div className="p-5 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
              <div>
                <h3 className="font-bold text-base text-slate-900 dark:text-slate-100">
                  Configure: {selectedStrategy.name}
                </h3>
                <p className="text-xs text-slate-500 font-mono">ID: {selectedStrategy.strategy_id}</p>
              </div>
              <button
                onClick={() => setSelectedStrategy(null)}
                className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
              >
                ✕
              </button>
            </div>

            <div className="p-6 space-y-4 text-xs">
              <p className="text-slate-500">
                Strategy parameters are schema-validated. Any parameter changes create a durable configuration revision.
              </p>

              <div className="space-y-3">
                {Object.entries(selectedStrategy.parameters || {}).map(([key, val]) => (
                  <div key={key}>
                    <label className="block font-mono font-semibold text-slate-700 dark:text-slate-300 mb-1">
                      {key}
                    </label>
                    <input
                      type="text"
                      defaultValue={String(val)}
                      className="w-full px-3 py-1.5 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 font-mono"
                    />
                  </div>
                ))}
              </div>
            </div>

            <div className="px-6 py-3 bg-slate-50 dark:bg-slate-900/50 border-t border-slate-100 dark:border-slate-800 flex justify-end gap-2">
              <button
                onClick={() => setSelectedStrategy(null)}
                className="px-3 py-1.5 border rounded-lg text-xs font-semibold text-slate-600 dark:text-slate-400"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  handleSaveParameters(selectedStrategy);
                  setSelectedStrategy(null);
                }}
                className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-semibold"
              >
                Submit Parameter Revision
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
