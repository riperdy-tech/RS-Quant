import React from 'react';
import { AlertTriangle, Radio, Wifi, WifiOff } from 'lucide-react';

export interface StaleBadgeProps {
  isStale: boolean;
  isConnected: boolean;
  latencyMs?: number;
}

export const StaleBadge: React.FC<StaleBadgeProps> = ({
  isStale,
  isConnected,
  latencyMs,
}) => {
  if (!isConnected) {
    return (
      <div
        data-testid="stale-badge"
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300 border border-rose-200 dark:border-rose-900"
      >
        <WifiOff className="w-3.5 h-3.5 text-rose-600 dark:text-rose-400" />
        <span>Disconnected</span>
      </div>
    );
  }

  if (isStale) {
    return (
      <div
        data-testid="stale-badge"
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300 border border-amber-300 dark:border-amber-800 animate-pulse"
      >
        <AlertTriangle className="w-3.5 h-3.5 text-amber-600 dark:text-amber-400" />
        <span>STALE (&gt;3s)</span>
      </div>
    );
  }

  return (
    <div
      data-testid="stale-badge"
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-900"
    >
      <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
      <span>Live {latencyMs ? `(${latencyMs}ms)` : ''}</span>
    </div>
  );
};
