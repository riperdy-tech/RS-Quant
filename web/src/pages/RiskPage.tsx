import React, { useEffect, useState } from 'react';
import {
  AlertOctagon,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  FileText,
  Lock,
  RefreshCw,
  RotateCcw,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sliders,
  XCircle,
} from 'lucide-react';
import { api } from '../services/apiClient';

export interface RiskPageProps {
  onOpenCommand: (type: string, target?: Record<string, any>, payload?: Record<string, any>) => void;
  isViewer?: boolean;
}

export const RiskPage: React.FC<RiskPageProps> = ({ onOpenCommand, isViewer = false }) => {
  const [riskData, setRiskData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [draftMaxLots, setDraftMaxLots] = useState(500);
  const [draftDailyLoss, setDraftDailyLoss] = useState(500);

  useEffect(() => {
    fetchRiskStatus();
  }, []);

  const fetchRiskStatus = async () => {
    setLoading(true);
    try {
      const data = await api.getRiskStatus().catch(() => null);
      if (data) {
        setRiskData(data);
        setDraftMaxLots(data.max_position_lots);
        setDraftDailyLoss(Number(data.daily_loss_limit));
      }
    } finally {
      setLoading(false);
    }
  };

  const isTripped =
    Boolean(riskData?.emergency_flatten_active) ||
    (riskData?.breakers_tripped?.length ?? 0) > 0;

  const handleEmergencyKill = () => {
    onOpenCommand('EMERGENCY_KILL', { account_id: 'paper-demo' });
  };

  const handleResetBreaker = () => {
    onOpenCommand('RESET_RISK_LATCH', { account_id: 'paper-demo' });
  };

  const handleSaveLimits = (e: React.FormEvent) => {
    e.preventDefault();
    onOpenCommand(
      'UPDATE_RISK_LIMITS',
      { account_id: 'paper-demo' },
      {
        max_position_lots: draftMaxLots,
        daily_loss_limit: String(draftDailyLoss),
      }
    );
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
            Pre-Trade Risk Governance & Circuit Breakers
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            Durable pre-trade validation, daily loss bounds, peak drawdown latches, and immediate kill controls.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={fetchRiskStatus}
            disabled={loading}
            className="p-2 border border-slate-200 dark:border-slate-800 rounded-lg text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
            title="Refresh risk status"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          {!isViewer && (
            <button
              onClick={handleEmergencyKill}
              className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-xs font-bold transition-colors flex items-center gap-1.5 shadow-sm"
            >
              <ShieldAlert className="w-4 h-4" /> Emergency Kill (Immediate)
            </button>
          )}
        </div>
      </div>

      {/* Primary Risk Status Banner (§15.2) */}
      <div className={`p-4 rounded-xl border flex items-center justify-between shadow-sm ${
        isTripped
          ? 'bg-rose-50 dark:bg-rose-950/40 border-rose-200 dark:border-rose-900 text-rose-900 dark:text-rose-200'
          : 'bg-emerald-50 dark:bg-emerald-950/40 border-emerald-200 dark:border-emerald-900 text-emerald-900 dark:text-emerald-200'
      }`}>
        <div className="flex items-center gap-3">
          {isTripped ? (
            <AlertOctagon className="w-6 h-6 text-rose-600 dark:text-rose-400 flex-shrink-0" />
          ) : (
            <ShieldCheck className="w-6 h-6 text-emerald-600 dark:text-emerald-400 flex-shrink-0" />
          )}
          <div>
            <div className="flex items-center gap-2">
              <span className="font-bold text-sm">Circuit Breaker & Latch:</span>
              <span
                data-testid="risk-latch"
                className={`font-mono font-bold text-sm px-2 py-0.5 rounded ${
                  isTripped ? 'bg-rose-200 dark:bg-rose-900 text-rose-800 dark:text-rose-200' : 'bg-emerald-200 dark:bg-emerald-900 text-emerald-800 dark:text-emerald-200'
                }`}
              >
                {isTripped ? 'Halted' : 'Normal'}
              </span>
            </div>
            <p className="text-xs text-slate-600 dark:text-slate-400 mt-0.5">
              {isTripped
                ? 'Trading halted by circuit breaker or emergency kill. All new entries blocked.'
                : 'All safety gates operating nominally. Pre-trade limits enforced on every event.'}
            </p>
          </div>
        </div>

        <div>
          {!isViewer && isTripped ? (
            <button
              data-testid="kill-reset"
              onClick={handleResetBreaker}
              className="px-3.5 py-2 bg-amber-600 hover:bg-amber-700 text-white rounded-lg text-xs font-bold transition-colors flex items-center gap-1.5 shadow-sm"
            >
              <RotateCcw className="w-3.5 h-3.5" /> Reset Breaker (Confirmed)
            </button>
          ) : isTripped ? (
            <span className="text-xs font-semibold text-rose-700 dark:text-rose-400 flex items-center gap-1">
              <ShieldAlert className="w-4 h-4" /> Breaker Tripped (Reset requires Operator)
            </span>
          ) : (
            <span className="text-xs font-semibold text-emerald-700 dark:text-emerald-400 flex items-center gap-1">
              <CheckCircle2 className="w-4 h-4" /> Operational
            </span>
          )}
        </div>
      </div>

      {/* Risk Limits vs Usage Gauges */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Position Lots Gauge */}
        <div className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-3">
          <div className="flex justify-between items-center text-xs font-semibold text-slate-500 uppercase tracking-wider">
            <span>Position Lots Usage</span>
            <Shield className="w-4 h-4 text-indigo-500" />
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold font-mono text-slate-900 dark:text-slate-100">
              {riskData?.current_position_lots ?? 100}
            </span>
            <span className="text-xs text-slate-400 font-mono">
              / {riskData?.max_position_lots ?? 500} lots
            </span>
          </div>
          <div className="w-full bg-slate-100 dark:bg-slate-800 h-2 rounded-full overflow-hidden">
            <div
              className="bg-indigo-600 h-full rounded-full"
              style={{
                width: `${Math.min(
                  100,
                  ((riskData?.current_position_lots ?? 100) / (riskData?.max_position_lots ?? 500)) * 100
                )}%`,
              }}
            />
          </div>
          <span className="text-[11px] text-slate-400 block">
            {(((riskData?.current_position_lots ?? 100) / (riskData?.max_position_lots ?? 500)) * 100).toFixed(1)}% of maximum portfolio commitment
          </span>
        </div>

        {/* Daily Loss Limit Gauge */}
        <div className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-3">
          <div className="flex justify-between items-center text-xs font-semibold text-slate-500 uppercase tracking-wider">
            <span>Daily Loss Limit</span>
            <AlertTriangle className="w-4 h-4 text-amber-500" />
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold font-mono text-emerald-600 dark:text-emerald-400">
              ${riskData?.current_daily_loss ?? '0.00'}
            </span>
            <span className="text-xs text-slate-400 font-mono">
              / ${riskData?.daily_loss_limit ?? '500.00'}
            </span>
          </div>
          <div className="w-full bg-slate-100 dark:bg-slate-800 h-2 rounded-full overflow-hidden">
            <div
              className="bg-amber-500 h-full rounded-full"
              style={{
                width: `${Math.min(
                  100,
                  (Number(riskData?.current_daily_loss ?? 0) / Number(riskData?.daily_loss_limit ?? 500)) * 100
                )}%`,
              }}
            />
          </div>
          <span className="text-[11px] text-slate-400 block">
            Reset every day at 00:00:00 UTC
          </span>
        </div>

        {/* Peak Drawdown Gauge */}
        <div className="bg-white dark:bg-slate-900 p-5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-3">
          <div className="flex justify-between items-center text-xs font-semibold text-slate-500 uppercase tracking-wider">
            <span>Peak Drawdown Latch</span>
            <AlertOctagon className="w-4 h-4 text-rose-500" />
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold font-mono text-indigo-600 dark:text-indigo-400">
              {riskData?.peak_drawdown_pct ?? '1.2'}%
            </span>
            <span className="text-xs text-slate-400 font-mono">
              / max {riskData?.max_drawdown_limit_pct ?? '5.0'}%
            </span>
          </div>
          <div className="w-full bg-slate-100 dark:bg-slate-800 h-2 rounded-full overflow-hidden">
            <div
              className="bg-rose-500 h-full rounded-full"
              style={{
                width: `${Math.min(
                  100,
                  (Number(riskData?.peak_drawdown_pct ?? 1.2) / Number(riskData?.max_drawdown_limit_pct ?? 5.0)) * 100
                )}%`,
              }}
            />
          </div>
          <span className="text-[11px] text-slate-400 block">
            Automatic trip when high-water mark drawdown exceeds 5.0%
          </span>
        </div>
      </div>

      {/* Form: Update Draft Risk Limits (§15.2) */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 shadow-sm space-y-4">
        <h2 className="font-bold text-base text-slate-900 dark:text-slate-100 flex items-center gap-2">
          <Sliders className="w-4 h-4 text-indigo-600" />
          Update Draft Risk Limits
        </h2>
        <form onSubmit={handleSaveLimits} className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
          <div>
            <label className="block font-medium text-slate-700 dark:text-slate-300 mb-1">
              Max Position Lots
            </label>
            <input
              type="number"
              value={draftMaxLots}
              onChange={(e) => setDraftMaxLots(Number(e.target.value))}
              className="w-full px-3 py-2 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100 font-mono"
            />
          </div>

          <div>
            <label className="block font-medium text-slate-700 dark:text-slate-300 mb-1">
              Daily Loss Limit (USDT)
            </label>
            <input
              type="number"
              value={draftDailyLoss}
              onChange={(e) => setDraftDailyLoss(Number(e.target.value))}
              className="w-full px-3 py-2 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100 font-mono"
            />
          </div>

          <div className="flex items-end">
            {!isViewer ? (
              <button
                type="submit"
                className="w-full py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-semibold transition-colors shadow-sm"
              >
                Stage Limit Revision
              </button>
            ) : (
              <div className="w-full py-2.5 bg-slate-100 dark:bg-slate-800 text-slate-400 text-center rounded-lg font-semibold">
                Read-Only (Viewer)
              </div>
            )}
          </div>
        </form>
      </div>
    </div>
  );
};
