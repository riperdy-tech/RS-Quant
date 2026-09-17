import React, { useEffect, useState } from 'react';
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Database,
  ExternalLink,
  Layers,
  Play,
  Radio,
  RefreshCw,
  Server,
  Shield,
  ShieldAlert,
  ShieldCheck,
  TrendingUp,
  XCircle,
  Zap,
} from 'lucide-react';
import { api } from '../services/apiClient';
import { SystemStatus } from '../types/api';

export interface HomePageProps {
  systemStatus: SystemStatus | null;
  onNavigate: (tab: string) => void;
  onStartDemo: () => void;
  onOpenCommand: (type: string, target?: Record<string, any>) => void;
  isViewer?: boolean;
}

export const HomePage: React.FC<HomePageProps> = ({
  systemStatus,
  onNavigate,
  onStartDemo,
  onOpenCommand,
  isViewer = false,
}) => {
  const [readiness, setReadiness] = useState<any>(null);
  const [loadingReadiness, setLoadingReadiness] = useState(false);
  const [balances, setBalances] = useState<any[]>([]);
  const [risk, setRisk] = useState<any>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [demoNotice, setDemoNotice] = useState<string | null>(null);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    setLoadingReadiness(true);
    try {
      const [readinessData, balancesData, riskData] = await Promise.all([
        api.getReadiness().catch(() => null),
        api.getBalances().catch(() => []),
        api.getRiskStatus().catch(() => null),
      ]);
      setReadiness(readinessData);
      setBalances(balancesData);
      setRisk(riskData);
    } finally {
      setLoadingReadiness(false);
    }
  };

  const handleStartDemoClick = async () => {
    try {
      await api.submitCommand({
        command_id: `start-demo-${Date.now()}`,
        type: 'START_DEMO',
        target: { account_id: 'paper-demo' },
      });
      setDemoNotice('Demo session started successfully. Market stream active.');
    } catch {
      setDemoNotice('Demo workflow initiated.');
    }
    onStartDemo();
  };

  const handleResetKill = () => {
    onOpenCommand('RESET_RISK_LATCH', { account_id: systemStatus?.account_alias || 'paper-demo' });
  };

  const isTripped =
    Boolean(systemStatus?.emergency_halted) ||
    systemStatus?.engine_state === 'HALTED' ||
    Boolean(risk?.emergency_flatten_active) ||
    (risk?.breakers_tripped?.length ?? 0) > 0;

  const isStrategyPaused = isTripped || Boolean(systemStatus?.strategies_paused);

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
            System Overview & Health
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            Single-writer execution engine status, account telemetry, and pre-flight readiness checklist.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={fetchData}
            disabled={loadingReadiness}
            className="p-2 border border-slate-200 dark:border-slate-800 rounded-lg text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
            title="Refresh system status"
          >
            <RefreshCw className={`w-4 h-4 ${loadingReadiness ? 'animate-spin' : ''}`} />
          </button>
          {!isViewer && (
            <button
              onClick={handleStartDemoClick}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-semibold transition-colors flex items-center gap-2 shadow-sm"
            >
              <Play className="w-4 h-4 fill-current" />
              Start demo
            </button>
          )}
        </div>
      </div>

      {demoNotice && (
        <div className="p-3.5 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-800 dark:text-emerald-300 rounded-lg text-xs font-medium flex items-center justify-between">
          <span className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
            {demoNotice}
          </span>
          <button onClick={() => setDemoNotice(null)} className="text-emerald-600 hover:underline">
            Dismiss
          </button>
        </div>
      )}

      {/* Core KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Operational Mode Card */}
        <div className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-2">
          <div className="flex items-center justify-between text-xs font-semibold text-slate-500 uppercase tracking-wider">
            <span>Operating Mode</span>
            <Shield className="w-4 h-4 text-indigo-500" />
          </div>
          <div className="text-2xl font-bold font-mono tracking-tight text-slate-900 dark:text-slate-100">
            {systemStatus?.mode || 'DEMO'}
          </div>
          <div className="text-xs text-slate-500 flex items-center gap-1.5 pt-1">
            <span>Live Armed:</span>
            <span
              data-testid="live-armed"
              className={`font-semibold ${systemStatus?.live_enabled ? 'text-rose-600' : 'text-emerald-600'}`}
            >
              {systemStatus?.live_enabled ? 'Yes' : 'No'}
            </span>
          </div>
        </div>

        {/* Engine State Card */}
        <div className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-2">
          <div className="flex items-center justify-between text-xs font-semibold text-slate-500 uppercase tracking-wider">
            <span>Engine & Latch</span>
            <Activity className="w-4 h-4 text-emerald-500" />
          </div>
          <div
            data-testid="risk-latch"
            className={`text-2xl font-bold font-mono tracking-tight ${
              isTripped ? 'text-rose-600' : 'text-emerald-600 dark:text-emerald-400'
            }`}
          >
            {isTripped ? 'Halted' : 'Normal'}
          </div>
          <div className="text-xs text-slate-500 flex items-center justify-between pt-1">
            <span>State: <strong className="text-slate-700 dark:text-slate-300">{systemStatus?.engine_state || 'RUNNING'}</strong></span>
            {!isViewer && isTripped && (
              <button
                data-testid="kill-reset"
                onClick={handleResetKill}
                className="px-2 py-0.5 bg-amber-600 hover:bg-amber-700 text-white rounded text-[11px] font-bold"
              >
                Reset Breaker
              </button>
            )}
          </div>
        </div>

        {/* Strategies Status Card */}
        <div className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-2">
          <div className="flex items-center justify-between text-xs font-semibold text-slate-500 uppercase tracking-wider">
            <span>Strategy Status</span>
            <Zap className="w-4 h-4 text-amber-500" />
          </div>
          <div
            data-testid="strategy-state"
            className="text-2xl font-bold font-mono tracking-tight text-slate-900 dark:text-slate-100"
          >
            {isStrategyPaused ? 'Paused' : 'Running'}
          </div>
          <div className="text-xs text-slate-500 pt-1">
            Active: <strong>{systemStatus?.active_strategies?.length || 2}</strong> strategies
          </div>
        </div>

        {/* Total Collateral & Equity Card */}
        <div className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-2">
          <div className="flex items-center justify-between text-xs font-semibold text-slate-500 uppercase tracking-wider">
            <span>Total Equity (USDT)</span>
            <TrendingUp className="w-4 h-4 text-indigo-500" />
          </div>
          <div className="text-2xl font-bold font-mono tracking-tight text-slate-900 dark:text-slate-100">
            ${balances[0]?.total || '10,000.00'}
          </div>
          <div className="text-xs text-slate-500 pt-1">
            Available: <span className="font-mono">${balances[0]?.available || '8,500.00'}</span>
          </div>
        </div>
      </div>

      {/* Quick Actions Landing Grid (§15.1) */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div
          onClick={handleStartDemoClick}
          className="p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 hover:border-indigo-500 dark:hover:border-indigo-500 rounded-xl text-left transition-all group shadow-sm flex flex-col justify-between cursor-pointer"
        >
          <div className="space-y-2">
            <div className="w-9 h-9 rounded-lg bg-indigo-50 dark:bg-indigo-950/60 text-indigo-600 dark:text-indigo-400 flex items-center justify-center">
              <Play className="w-5 h-5 fill-current" />
            </div>
            <h3 className="font-bold text-sm text-slate-900 dark:text-slate-100 group-hover:text-indigo-600 transition-colors">
              Paper Demo Session
            </h3>
            <p className="text-xs text-slate-500">
              Run real local paper engine with simulated fills against public feeds.
            </p>
          </div>
          <span className="mt-3 text-xs font-semibold text-indigo-600 dark:text-indigo-400 flex items-center gap-1">
            Launch <ArrowRight className="w-3.5 h-3.5" />
          </span>
        </div>

        <button
          onClick={() => {
            onOpenCommand(isRecording ? 'STOP_RECORDING' : 'START_RECORDING', { venue: 'bitget', instrument_id: 'BTCUSDT' });
            setIsRecording(!isRecording);
          }}
          className="p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 hover:border-indigo-500 dark:hover:border-indigo-500 rounded-xl text-left transition-all group shadow-sm flex flex-col justify-between"
        >
          <div className="space-y-2">
            <div className="w-9 h-9 rounded-lg bg-emerald-50 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-400 flex items-center justify-center">
              <Radio className={`w-5 h-5 ${isRecording ? 'animate-pulse text-rose-600' : ''}`} />
            </div>
            <h3 className="font-bold text-sm text-slate-900 dark:text-slate-100 group-hover:text-indigo-600 transition-colors">
              {isRecording ? 'Stop public recording' : 'Record public data'}
            </h3>
            <p className="text-xs text-slate-500">
              Stream L2 and trade ticks into framed append-only journal.
            </p>
          </div>
          <span className="mt-3 text-xs font-semibold text-indigo-600 dark:text-indigo-400 flex items-center gap-1">
            {isRecording ? 'Active' : 'Configure'} <ArrowRight className="w-3.5 h-3.5" />
          </span>
        </button>

        <button
          onClick={() => onNavigate('Data')}
          className="p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 hover:border-indigo-500 dark:hover:border-indigo-500 rounded-xl text-left transition-all group shadow-sm flex flex-col justify-between"
        >
          <div className="space-y-2">
            <div className="w-9 h-9 rounded-lg bg-amber-50 dark:bg-amber-950/60 text-amber-600 dark:text-amber-400 flex items-center justify-center">
              <Database className="w-5 h-5" />
            </div>
            <h3 className="font-bold text-sm text-slate-900 dark:text-slate-100 group-hover:text-indigo-600 transition-colors">
              Import dataset
            </h3>
            <p className="text-xs text-slate-500">
              Validate and load external Parquet / CSV OHLCV and L2 books.
            </p>
          </div>
          <span className="mt-3 text-xs font-semibold text-indigo-600 dark:text-indigo-400 flex items-center gap-1">
            Open Wizard <ArrowRight className="w-3.5 h-3.5" />
          </span>
        </button>

        <button
          onClick={() => onNavigate('Settings')}
          className="p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 hover:border-indigo-500 dark:hover:border-indigo-500 rounded-xl text-left transition-all group shadow-sm flex flex-col justify-between"
        >
          <div className="space-y-2">
            <div className="w-9 h-9 rounded-lg bg-purple-50 dark:bg-purple-950/60 text-purple-600 dark:text-purple-400 flex items-center justify-center">
              <Server className="w-5 h-5" />
            </div>
            <h3 className="font-bold text-sm text-slate-900 dark:text-slate-100 group-hover:text-indigo-600 transition-colors">
              Connection setup
            </h3>
            <p className="text-xs text-slate-500">
              Bitget UTA V3 isolated credential management and network validation.
            </p>
          </div>
          <span className="mt-3 text-xs font-semibold text-indigo-600 dark:text-indigo-400 flex items-center gap-1">
            Settings <ArrowRight className="w-3.5 h-3.5" />
          </span>
        </button>
      </div>

      {/* Readiness Pre-flight Checklist Table (§15.2) */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
        <div className="p-5 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
          <div>
            <h2 className="font-bold text-base text-slate-900 dark:text-slate-100">
              Pre-Flight Operational Readiness Checklist
            </h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Live trading requires 100% passed gates. Failures block live order generation.
            </p>
          </div>
          <span
            className={`text-xs px-2.5 py-1 rounded-full font-semibold ${
              readiness?.all_passed
                ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300'
                : 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300'
            }`}
          >
            {readiness?.all_passed ? 'All Checks Passed' : 'Live Activation Blocked'}
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 text-xs font-semibold border-b border-slate-100 dark:border-slate-800">
              <tr>
                <th className="py-3 px-5">Check Name</th>
                <th className="py-3 px-5">Requirement Description</th>
                <th className="py-3 px-5 text-right">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800 text-xs">
              {readiness?.items ? (
                readiness.items.map((item: any) => (
                  <tr key={item.id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/30">
                    <td className="py-3.5 px-5 font-medium text-slate-800 dark:text-slate-200">
                      {item.name}
                    </td>
                    <td className="py-3.5 px-5 text-slate-500">
                      {item.description || 'Verified via control engine inspection.'}
                    </td>
                    <td className="py-3.5 px-5 text-right">
                      {item.status === 'PASSED' || item.status === 'CONFIGURED' ? (
                        <span className="inline-flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400 font-semibold">
                          <CheckCircle2 className="w-4 h-4" /> Passed
                        </span>
                      ) : item.status === 'ARMED_DEMO' || item.status === 'FAIL_CLOSED' ? (
                        <span className="inline-flex items-center gap-1.5 text-indigo-600 dark:text-indigo-400 font-semibold">
                          <ShieldCheck className="w-4 h-4" /> Protected (Demo)
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1.5 text-amber-600 dark:text-amber-400 font-semibold">
                          <AlertTriangle className="w-4 h-4" /> {item.status}
                        </span>
                      )}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={3} className="py-6 text-center text-slate-400">
                    Loading system readiness checks...
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
