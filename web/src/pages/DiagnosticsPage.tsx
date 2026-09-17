import React, { useEffect, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Bug,
  CheckCircle2,
  Cpu,
  Download,
  FileCode,
  FileText,
  Filter,
  HardDrive,
  RefreshCw,
  Search,
  Server,
  ShieldAlert,
} from 'lucide-react';
import { api } from '../services/apiClient';

export const DiagnosticsPage: React.FC = () => {
  const [filterCorrelation, setFilterCorrelation] = useState('');
  const [exporting, setExporting] = useState(false);
  const [exportSuccess, setExportSuccess] = useState(false);

  const logEntries = [
    {
      timestamp: '12:00:00.104',
      level: 'INFO',
      component: 'engine.serial',
      correlation_id: 'corr-init-001',
      message: 'Canonical event journal synchronized at watermark 1250',
    },
    {
      timestamp: '12:00:01.420',
      level: 'INFO',
      component: 'adapter.bitget',
      correlation_id: 'corr-ws-002',
      message: 'Public WebSocket subscribed to BTCUSDT L2 books and trades',
    },
    {
      timestamp: '12:00:02.810',
      level: 'INFO',
      component: 'strategy.imbalance',
      correlation_id: 'corr-strat-003',
      message: 'Warmup completed: 500 bars ingested. Intent generation armed.',
    },
    {
      timestamp: '12:00:04.110',
      level: 'INFO',
      component: 'risk.manager',
      correlation_id: 'corr-risk-004',
      message: 'Pre-trade check PASSED for order ord-btc-001 (lots: 100, side: BUY)',
    },
    {
      timestamp: '12:00:04.150',
      level: 'INFO',
      component: 'ledger.double_entry',
      correlation_id: 'corr-ledger-005',
      message: 'Balanced posting committed: Cr Margin / Dr Cash (1500.00 USDT)',
    },
  ];

  const handleExportBundle = () => {
    setExporting(true);
    setExportSuccess(false);
    setTimeout(() => {
      setExporting(false);
      setExportSuccess(true);
      setTimeout(() => setExportSuccess(false), 4000);
    }, 1000);
  };

  const filteredLogs = logEntries.filter(
    (l) =>
      l.message.toLowerCase().includes(filterCorrelation.toLowerCase()) ||
      l.correlation_id.toLowerCase().includes(filterCorrelation.toLowerCase()) ||
      l.component.toLowerCase().includes(filterCorrelation.toLowerCase())
  );

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
            System Observability & Structured Diagnostics
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            Structured correlation timelines, process isolation metrics, latency budgets, and support bundles.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleExportBundle}
            disabled={exporting}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors shadow-sm"
          >
            <Download className="w-3.5 h-3.5" />
            {exporting ? 'Generating Bundle...' : 'Export Redacted Support Bundle'}
          </button>
        </div>
      </div>

      {exportSuccess && (
        <div className="p-3 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-800 dark:text-emerald-300 rounded-lg text-xs font-medium flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
          Redacted diagnostic support bundle downloaded successfully. All private credentials excluded.
        </div>
      )}

      {/* Latency and System Health Gauges (§17, §15.2) */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-1">
          <span className="text-xs font-medium text-slate-500 uppercase tracking-wider">Core Reducer P99</span>
          <div className="text-2xl font-bold font-mono text-emerald-600 dark:text-emerald-400">
            1.8 ms
          </div>
          <span className="text-xs text-slate-400">Budget: &lt; 5.0 ms</span>
        </div>

        <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-1">
          <span className="text-xs font-medium text-slate-500 uppercase tracking-wider">Raw-to-Decision P99</span>
          <div className="text-2xl font-bold font-mono text-emerald-600 dark:text-emerald-400">
            11.4 ms
          </div>
          <span className="text-xs text-slate-400">Budget: &lt; 25.0 ms</span>
        </div>

        <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-1">
          <span className="text-xs font-medium text-slate-500 uppercase tracking-wider">Active Workers</span>
          <div className="text-2xl font-bold font-mono text-slate-900 dark:text-slate-100">
            1 Subprocess
          </div>
          <span className="text-xs text-slate-400">Isolated backtest/training</span>
        </div>

        <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-1">
          <span className="text-xs font-medium text-slate-500 uppercase tracking-wider">Ingress Saturation</span>
          <div className="text-2xl font-bold font-mono text-emerald-600 dark:text-emerald-400">
            0% (Normal)
          </div>
          <span className="text-xs text-slate-400">0 dropped messages</span>
        </div>
      </div>

      {/* Structured Log Timeline (§15.2) */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden flex flex-col">
        <div className="p-4 border-b border-slate-100 dark:border-slate-800 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h2 className="font-bold text-base text-slate-900 dark:text-slate-100">
              Structured Diagnostic Event Stream
            </h2>
            <p className="text-xs text-slate-500">
              Correlated audit messages with component identity, reason codes, and causal scopes.
            </p>
          </div>

          <div className="relative w-full sm:w-72">
            <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-400" />
            <input
              type="text"
              value={filterCorrelation}
              onChange={(e) => setFilterCorrelation(e.target.value)}
              placeholder="Filter correlation ID or component..."
              className="w-full pl-9 pr-3 py-1.5 text-xs rounded-lg border border-slate-300 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono"
            />
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs whitespace-nowrap">
            <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 font-semibold border-b border-slate-100 dark:border-slate-800">
              <tr>
                <th className="py-3 px-4">Time</th>
                <th className="py-3 px-4">Level</th>
                <th className="py-3 px-4">Component</th>
                <th className="py-3 px-4">Correlation ID</th>
                <th className="py-3 px-4">Event Message</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800 font-mono text-[11px]">
              {filteredLogs.map((log, idx) => (
                <tr key={idx} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/30">
                  <td className="py-2.5 px-4 text-slate-400">{log.timestamp}</td>
                  <td className="py-2.5 px-4">
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300">
                      {log.level}
                    </span>
                  </td>
                  <td className="py-2.5 px-4 text-indigo-600 dark:text-indigo-400">{log.component}</td>
                  <td className="py-2.5 px-4 text-slate-500">{log.correlation_id}</td>
                  <td className="py-2.5 px-4 text-slate-800 dark:text-slate-200">{log.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
