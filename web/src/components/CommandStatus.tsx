import React, { useEffect, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  ExternalLink,
  Loader2,
  RefreshCw,
  RotateCcw,
  XCircle,
} from 'lucide-react';
import { api } from '../services/apiClient';

export interface CommandStatusProps {
  commandId: string;
  initialStatus?: string;
  onDismiss?: () => void;
}

export const CommandStatus: React.FC<CommandStatusProps> = ({
  commandId,
  initialStatus = 'QUEUED',
  onDismiss,
}) => {
  const [status, setStatus] = useState<string>(initialStatus);
  const [loading, setLoading] = useState(false);
  const [record, setRecord] = useState<any | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getCommandStatus(commandId);
      setRecord(data);
      if (data.status) setStatus(data.status);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStatus();
    // Poll status while in transient states
    if (status === 'QUEUED' || status === 'VALIDATING' || status === 'RUNNING') {
      const interval = setInterval(fetchStatus, 2000);
      return () => clearInterval(interval);
    }
  }, [commandId, status]);

  const isSuccess = status === 'APPLIED' || status === 'SUCCEEDED';
  const isFailed = status === 'REJECTED' || status === 'FAILED' || status === 'UNKNOWN';

  return (
    <div
      data-testid="command-status"
      className={`p-3.5 rounded-xl border flex items-center justify-between shadow-sm text-xs ${
        isSuccess
          ? 'bg-emerald-50 dark:bg-emerald-950/40 border-emerald-200 dark:border-emerald-800 text-emerald-900 dark:text-emerald-200'
          : isFailed
          ? 'bg-rose-50 dark:bg-rose-950/40 border-rose-200 dark:border-rose-800 text-rose-900 dark:text-rose-200'
          : 'bg-indigo-50 dark:bg-indigo-950/40 border-indigo-200 dark:border-indigo-800 text-indigo-900 dark:text-indigo-200'
      }`}
    >
      <div className="flex items-center gap-2.5">
        {isSuccess ? (
          <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400 flex-shrink-0" />
        ) : isFailed ? (
          <XCircle className="w-4 h-4 text-rose-600 dark:text-rose-400 flex-shrink-0" />
        ) : (
          <Loader2 className="w-4 h-4 animate-spin text-indigo-600 dark:text-indigo-400 flex-shrink-0" />
        )}
        <div>
          <div className="flex items-center gap-2">
            <span className="font-semibold">Command Status:</span>
            <span className="font-mono font-bold uppercase">{status}</span>
          </div>
          <p className="text-[11px] opacity-80 font-mono">ID: {commandId}</p>
          {error && <p className="text-[11px] text-rose-600 dark:text-rose-400 mt-0.5">{error}</p>}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={fetchStatus}
          disabled={loading}
          className="p-1 rounded hover:bg-black/5 dark:hover:bg-white/5 transition-colors"
          title="Refresh receipt from durable inbox"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
        {onDismiss && (
          <button
            onClick={onDismiss}
            className="text-[11px] font-semibold underline opacity-70 hover:opacity-100"
          >
            Dismiss
          </button>
        )}
      </div>
    </div>
  );
};
