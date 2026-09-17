import React, { useEffect, useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  Award,
  CheckCircle2,
  Cpu,
  Layers,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  Sliders,
  TrendingUp,
  XCircle,
} from 'lucide-react';
import { api } from '../services/apiClient';

export interface ModelsPageProps {
  onOpenCommand: (type: string, target?: Record<string, any>, payload?: Record<string, any>) => void;
}

export const ModelsPage: React.FC<ModelsPageProps> = ({ onOpenCommand }) => {
  const [models, setModels] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [algorithm, setAlgorithm] = useState('lightgbm');
  const [datasetId, setDatasetId] = useState('ds-btc-2024-q1');
  const [training, setTraining] = useState(false);

  useEffect(() => {
    fetchModels();
  }, []);

  const fetchModels = async () => {
    setLoading(true);
    try {
      const data = await api.getModels().catch(() => []);
      setModels(data.length > 0 ? data : defaultModels);
    } finally {
      setLoading(false);
    }
  };

  const defaultModels = [
    {
      model_id: 'lgbm-champion-v2',
      algorithm: 'LightGBM Classifier',
      lifecycle_state: 'CHAMPION',
      dataset_origin: 'ds-btc-2024-q1 (50k rows)',
      log_loss: '0.621',
      brier_score: '0.218',
      psi_drift: '0.042', // < 0.1 is stable
      features_count: 8,
      created_at: '2026-09-15',
    },
    {
      model_id: 'lgbm-candidate-04',
      algorithm: 'LightGBM Classifier',
      lifecycle_state: 'CANDIDATE',
      dataset_origin: 'ds-btc-2024-q1',
      log_loss: '0.609',
      brier_score: '0.212',
      psi_drift: '0.038',
      features_count: 8,
      created_at: '2026-09-16',
    },
    {
      model_id: 'logistic-baseline-01',
      algorithm: 'Logistic Regression',
      lifecycle_state: 'ARCHIVED',
      dataset_origin: 'ds-btc-2024-q1',
      log_loss: '0.674',
      brier_score: '0.241',
      psi_drift: '0.128',
      features_count: 4,
      created_at: '2026-09-10',
    },
    {
      model_id: 'lgbm-candidate-03',
      algorithm: 'LightGBM Classifier',
      lifecycle_state: 'REJECTED',
      dataset_origin: 'ds-btc-2024-q1',
      log_loss: '0.712',
      brier_score: '0.265',
      psi_drift: '0.285', // severe drift
      features_count: 8,
      created_at: '2026-09-14',
    },
  ];

  const handleLaunchTraining = async (e: React.FormEvent) => {
    e.preventDefault();
    setTraining(true);
    try {
      await api.trainModel({
        algorithm,
        dataset_id: datasetId,
      });
      fetchModels();
    } catch (err: any) {
      alert(`Training job failed: ${err.message}`);
    } finally {
      setTraining(false);
    }
  };

  const handlePromote = (modelId: string) => {
    onOpenCommand('PROMOTE_MODEL', { model_id: modelId, account_id: 'paper-demo' });
  };

  const handleRollback = () => {
    onOpenCommand('ROLLBACK_MODEL', { account_id: 'paper-demo' });
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
            Model Governance & Machine Learning Registry
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            Champion/candidate lifecycle tracking, calibration metrics, PSI drift monitoring, and atomic promotion.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={fetchModels}
            disabled={loading}
            className="p-2 border border-slate-200 dark:border-slate-800 rounded-lg text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
            title="Refresh models"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={handleRollback}
            className="px-3.5 py-2 bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 rounded-lg text-xs font-semibold hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors flex items-center gap-1.5"
          >
            <RotateCcw className="w-4 h-4" /> Rollback Champion
          </button>
        </div>
      </div>

      {/* Launcher & Metrics Overview */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Model Training Launcher */}
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 shadow-sm space-y-4">
          <h2 className="font-bold text-base text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Cpu className="w-4 h-4 text-indigo-600" />
            Train Candidate Model
          </h2>
          <form onSubmit={handleLaunchTraining} className="space-y-4 text-xs">
            <div>
              <label className="block font-medium text-slate-700 dark:text-slate-300 mb-1">
                Algorithm
              </label>
              <select
                value={algorithm}
                onChange={(e) => setAlgorithm(e.target.value)}
                className="w-full px-3 py-2 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100"
              >
                <option value="lightgbm">LightGBM GBDT (Purged Walk-Forward Folds)</option>
                <option value="logistic">Logistic Regression Baseline</option>
              </select>
            </div>

            <div>
              <label className="block font-medium text-slate-700 dark:text-slate-300 mb-1">
                Training Dataset
              </label>
              <select
                value={datasetId}
                onChange={(e) => setDatasetId(e.target.value)}
                className="w-full px-3 py-2 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100"
              >
                <option value="ds-btc-2024-q1">ds-btc-2024-q1 (BTCUSDT Q1 2024)</option>
                <option value="ds-btc-2024-trades">ds-btc-2024-trades (100k Trades)</option>
              </select>
            </div>

            <div className="pt-2">
              <button
                type="submit"
                disabled={training}
                className="w-full py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-semibold transition-colors disabled:opacity-50"
              >
                {training ? 'Training...' : 'Dispatch Training Worker'}
              </button>
            </div>
          </form>
        </div>

        {/* Model Governance Rules Card */}
        <div className="lg:col-span-2 bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 shadow-sm space-y-3">
          <h2 className="font-bold text-base text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <Award className="w-4 h-4 text-amber-500" />
            Model Registry Gates (§12, §15.2)
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
            <div className="p-3 bg-slate-50 dark:bg-slate-800/40 rounded-lg space-y-1">
              <span className="font-bold text-slate-800 dark:text-slate-200 block">Flat Position Required</span>
              <p className="text-slate-500 text-[11px]">
                Promotion to LIVE or CHAMPION is strictly blocked if open inventory is non-zero.
              </p>
            </div>
            <div className="p-3 bg-slate-50 dark:bg-slate-800/40 rounded-lg space-y-1">
              <span className="font-bold text-slate-800 dark:text-slate-200 block">PSI Population Stability</span>
              <p className="text-slate-500 text-[11px]">
                PSI &gt; 0.20 indicates severe feature drift and halts automatic inference.
              </p>
            </div>
            <div className="p-3 bg-slate-50 dark:bg-slate-800/40 rounded-lg space-y-1">
              <span className="font-bold text-slate-800 dark:text-slate-200 block">Calibration & Log Loss</span>
              <p className="text-slate-500 text-[11px]">
                Brier score and log loss evaluated on purged holdout validation folds.
              </p>
            </div>
            <div className="p-3 bg-slate-50 dark:bg-slate-800/40 rounded-lg space-y-1">
              <span className="font-bold text-slate-800 dark:text-slate-200 block">Atomic Rollback</span>
              <p className="text-slate-500 text-[11px]">
                Instant reversion to prior verified champion without restarting engine.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Models Registry Table (§15.2) */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
        <div className="p-4 border-b border-slate-100 dark:border-slate-800">
          <h2 className="font-bold text-base text-slate-900 dark:text-slate-100">
            Model Registry & Lifecycle States
          </h2>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs whitespace-nowrap">
            <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 font-semibold border-b border-slate-100 dark:border-slate-800">
              <tr>
                <th className="py-3 px-4">Model ID</th>
                <th className="py-3 px-4">State</th>
                <th className="py-3 px-4">Algorithm</th>
                <th className="py-3 px-4">Log Loss</th>
                <th className="py-3 px-4">Brier Score</th>
                <th className="py-3 px-4">PSI Drift</th>
                <th className="py-3 px-4">Features</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {models.map((m) => (
                <tr key={m.model_id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/30">
                  <td className="py-3 px-4 font-mono font-bold text-slate-900 dark:text-slate-100">
                    {m.model_id}
                  </td>
                  <td className="py-3 px-4">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                      m.lifecycle_state === 'CHAMPION'
                        ? 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300'
                        : m.lifecycle_state === 'CANDIDATE'
                        ? 'bg-indigo-100 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-300'
                        : m.lifecycle_state === 'REJECTED'
                        ? 'bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300'
                        : 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-400'
                    }`}>
                      {m.lifecycle_state}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-slate-600 dark:text-slate-400">{m.algorithm}</td>
                  <td className="py-3 px-4 font-mono">{m.log_loss}</td>
                  <td className="py-3 px-4 font-mono">{m.brier_score}</td>
                  <td className="py-3 px-4 font-mono">
                    <span className={Number(m.psi_drift) > 0.2 ? 'text-rose-600 font-bold' : 'text-emerald-600'}>
                      {m.psi_drift}
                    </span>
                  </td>
                  <td className="py-3 px-4 font-mono">{m.features_count}</td>
                  <td className="py-3 px-4 text-right">
                    {m.lifecycle_state === 'CANDIDATE' && (
                      <button
                        onClick={() => handlePromote(m.model_id)}
                        className="px-2.5 py-1 bg-indigo-600 hover:bg-indigo-700 text-white rounded text-[11px] font-semibold"
                      >
                        Promote to Champion
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
  );
};
