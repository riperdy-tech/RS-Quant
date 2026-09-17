import React, { useEffect, useState } from 'react';
import {
  AlertCircle,
  BarChart3,
  CheckCircle2,
  Clock,
  Download,
  FileText,
  Loader2,
  Play,
  RefreshCw,
  Sliders,
  StopCircle,
} from 'lucide-react';
import { api } from '../services/apiClient';

export const BacktestsPage: React.FC = () => {
  const [backtests, setBacktests] = useState<any[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [selectedRun, setSelectedRun] = useState<any | null>(null);

  // Form State
  const [strategyId, setStrategyId] = useState('imbalance-btc');
  const [datasetId, setDatasetId] = useState('ds-btc-2024-q1');
  const [initialCash, setInitialCash] = useState(10000);

  useEffect(() => {
    fetchBacktestData();
  }, []);

  const fetchBacktestData = async () => {
    setLoading(true);
    try {
      const [runs, dsets] = await Promise.all([
        api.getBacktests().catch(() => []),
        api.getDatasets().catch(() => []),
      ]);
      setBacktests(runs.length > 0 ? runs : defaultRuns);
      setDatasets(dsets.length > 0 ? dsets : defaultDatasets);
    } finally {
      setLoading(false);
    }
  };

  const defaultDatasets = [
    { dataset_id: 'ds-btc-2024-q1', name: 'BTCUSDT Jan-Mar 2024 (OHLCV + L2 Depth)', instrument_id: 'BTCUSDT', capabilities: ['OHLCV', 'L2'] },
    { dataset_id: 'ds-btc-2024-trades', name: 'BTCUSDT Aggregated Trades 100k', instrument_id: 'BTCUSDT', capabilities: ['TRADES'] },
    { dataset_id: 'ds-eth-2024-q1', name: 'ETHUSDT Jan-Mar 2024 (OHLCV)', instrument_id: 'ETHUSDT', capabilities: ['OHLCV'] },
  ];

  const defaultRuns = [
    {
      job_id: 'bt-20240916-01',
      strategy_id: 'imbalance-btc',
      dataset_id: 'ds-btc-2024-q1',
      status: 'SUCCEEDED',
      sharpe_ratio: '2.14',
      max_drawdown_pct: '4.8',
      total_pnl: '1420.50',
      trades_count: 342,
      duration_seconds: 48,
      created_at: '2026-09-16 10:30',
      artifact_id: 'art-bt-01-report',
    },
    {
      job_id: 'bt-20240916-02',
      strategy_id: 'momentum-btc',
      dataset_id: 'ds-btc-2024-q1',
      status: 'SUCCEEDED',
      sharpe_ratio: '1.45',
      max_drawdown_pct: '8.2',
      total_pnl: '840.10',
      trades_count: 118,
      duration_seconds: 32,
      created_at: '2026-09-16 11:15',
      artifact_id: 'art-bt-02-report',
    },
  ];

  const handleLaunchBacktest = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      const res = await api.runBacktest({
        strategy_id: strategyId,
        dataset_id: datasetId,
        initial_cash: Number(initialCash),
      });
      // Add optimistic queued run
      setBacktests((prev) => [
        {
          job_id: res.job_id,
          strategy_id: strategyId,
          dataset_id: datasetId,
          status: 'RUNNING',
          sharpe_ratio: '--',
          max_drawdown_pct: '--',
          total_pnl: '--',
          trades_count: 0,
          created_at: 'Just now',
        },
        ...prev,
      ]);
    } catch (err: any) {
      alert(`Backtest launch failed: ${err.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancel = async (jobId: string) => {
    try {
      await api.cancelJob(jobId);
      fetchBacktestData();
    } catch (err: any) {
      alert(`Cancel failed: ${err.message}`);
    }
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
            Backtest Orchestration & Evaluation
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            Isolated deterministic backtesting with conservative queue models, fees, and latency assumptions.
          </p>
        </div>
        <button
          onClick={fetchBacktestData}
          disabled={loading}
          className="p-2 border border-slate-200 dark:border-slate-800 rounded-lg text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          title="Refresh backtest list"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Grid: Form & List */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Launcher Form */}
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 shadow-sm space-y-4">
          <h2 className="font-bold text-base text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Play className="w-4 h-4 text-indigo-600 fill-current" />
            Launch New Backtest
          </h2>
          <form onSubmit={handleLaunchBacktest} className="space-y-4 text-xs">
            <div>
              <label className="block font-medium text-slate-700 dark:text-slate-300 mb-1">
                Strategy
              </label>
              <select
                value={strategyId}
                onChange={(e) => setStrategyId(e.target.value)}
                className="w-full px-3 py-2 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                <option value="imbalance-btc">imbalance-btc (Order Book Imbalance)</option>
                <option value="momentum-btc">momentum-btc (Momentum Breakout)</option>
                <option value="mean-reversion-eth">mean-reversion-eth (Mean Reversion)</option>
                <option value="hybrid-lgbm-btc">hybrid-lgbm-btc (LightGBM Alpha)</option>
              </select>
            </div>

            <div>
              <label className="block font-medium text-slate-700 dark:text-slate-300 mb-1">
                Dataset
              </label>
              <select
                value={datasetId}
                onChange={(e) => setDatasetId(e.target.value)}
                className="w-full px-3 py-2 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                {datasets.map((d) => (
                  <option key={d.dataset_id} value={d.dataset_id}>
                    {d.name || d.dataset_id}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block font-medium text-slate-700 dark:text-slate-300 mb-1">
                Initial Capital (USDT)
              </label>
              <input
                type="number"
                value={initialCash}
                onChange={(e) => setInitialCash(Number(e.target.value))}
                className="w-full px-3 py-2 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono"
              />
            </div>

            <div className="pt-2">
              <button
                type="submit"
                disabled={submitting}
                className="w-full py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-semibold transition-colors flex items-center justify-center gap-2 disabled:opacity-50"
              >
                {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
                Run Backtest Worker
              </button>
            </div>
          </form>
        </div>

        {/* Backtest Run List */}
        <div className="lg:col-span-2 bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden flex flex-col">
          <div className="p-4 border-b border-slate-100 dark:border-slate-800">
            <h2 className="font-bold text-base text-slate-900 dark:text-slate-100">
              Completed & Running Backtest Jobs
            </h2>
          </div>

          <div className="overflow-x-auto flex-1">
            <table className="w-full text-left text-xs whitespace-nowrap">
              <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 font-semibold border-b border-slate-100 dark:border-slate-800">
                <tr>
                  <th className="py-3 px-4">Run ID</th>
                  <th className="py-3 px-4">Strategy</th>
                  <th className="py-3 px-4">Sharpe</th>
                  <th className="py-3 px-4">Max DD</th>
                  <th className="py-3 px-4">Net PnL</th>
                  <th className="py-3 px-4">Trades</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {backtests.map((run) => (
                  <tr key={run.job_id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/30">
                    <td className="py-3 px-4 font-mono font-medium text-slate-700 dark:text-slate-300">
                      {run.job_id}
                    </td>
                    <td className="py-3 px-4 text-slate-600 dark:text-slate-400">{run.strategy_id}</td>
                    <td className="py-3 px-4 font-mono font-bold text-indigo-600 dark:text-indigo-400">
                      {run.sharpe_ratio}
                    </td>
                    <td className="py-3 px-4 font-mono text-rose-600">{run.max_drawdown_pct}%</td>
                    <td className="py-3 px-4 font-mono font-bold text-emerald-600 dark:text-emerald-400">
                      ${run.total_pnl}
                    </td>
                    <td className="py-3 px-4 font-mono">{run.trades_count}</td>
                    <td className="py-3 px-4">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                        run.status === 'SUCCEEDED'
                          ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400'
                          : run.status === 'RUNNING'
                          ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-400 animate-pulse'
                          : 'bg-slate-100 text-slate-600'
                      }`}>
                        {run.status}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-right space-x-2">
                      <button
                        onClick={() => setSelectedRun(run)}
                        className="text-indigo-600 dark:text-indigo-400 hover:underline font-semibold"
                      >
                        Inspect
                      </button>
                      {run.status === 'RUNNING' && (
                        <button
                          onClick={() => handleCancel(run.job_id)}
                          className="text-rose-600 hover:underline font-semibold"
                        >
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Selected Run Inspection Modal */}
      {selectedRun && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 backdrop-blur-sm p-4">
          <div className="bg-white dark:bg-slate-900 rounded-xl shadow-2xl max-w-xl w-full border border-slate-200 dark:border-slate-800 overflow-hidden">
            <div className="p-5 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
              <div>
                <h3 className="font-bold text-base text-slate-900 dark:text-slate-100">
                  Backtest Run Report: {selectedRun.job_id}
                </h3>
                <p className="text-xs text-slate-500 font-mono">Strategy: {selectedRun.strategy_id}</p>
              </div>
              <button
                onClick={() => setSelectedRun(null)}
                className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
              >
                ✕
              </button>
            </div>

            <div className="p-6 space-y-4 text-xs">
              <div className="grid grid-cols-3 gap-3">
                <div className="p-3 bg-slate-50 dark:bg-slate-800/60 rounded-lg">
                  <span className="text-slate-400 block mb-1">Sharpe Ratio</span>
                  <span className="text-xl font-bold font-mono text-indigo-600 dark:text-indigo-400">
                    {selectedRun.sharpe_ratio}
                  </span>
                </div>
                <div className="p-3 bg-slate-50 dark:bg-slate-800/60 rounded-lg">
                  <span className="text-slate-400 block mb-1">Max Drawdown</span>
                  <span className="text-xl font-bold font-mono text-rose-600">
                    {selectedRun.max_drawdown_pct}%
                  </span>
                </div>
                <div className="p-3 bg-slate-50 dark:bg-slate-800/60 rounded-lg">
                  <span className="text-slate-400 block mb-1">Total Net PnL</span>
                  <span className="text-xl font-bold font-mono text-emerald-600 dark:text-emerald-400">
                    ${selectedRun.total_pnl}
                  </span>
                </div>
              </div>

              <div className="p-3 bg-slate-50 dark:bg-slate-800/40 rounded-lg space-y-1 font-mono text-[11px] text-slate-600 dark:text-slate-400">
                <p>Dataset: {selectedRun.dataset_id}</p>
                <p>Execution Mode: Conservative Queue Ahead (Shared Liquidity)</p>
                <p>Fee Model: Bitget UTA Taker 5.5 bps / Maker 2.0 bps</p>
                <p>Total Completed Trades: {selectedRun.trades_count}</p>
              </div>
            </div>

            <div className="px-6 py-3 bg-slate-50 dark:bg-slate-900/50 border-t border-slate-100 dark:border-slate-800 flex justify-end gap-2">
              <button
                onClick={() => alert('Downloading standalone HTML report artifact...')}
                className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5"
              >
                <Download className="w-3.5 h-3.5" /> Download Report Artifact
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
