import React from 'react';
import {
  Activity,
  BarChart2,
  Database,
  FileText,
  Home,
  Layers,
  LineChart,
  Moon,
  Play,
  RotateCcw,
  Settings,
  ShieldAlert,
  Calendar,
  Sun,
  TrendingUp,
  Zap,
} from 'lucide-react';
import { SystemStatus } from '../types/api';
import { StaleBadge } from './StaleBadge';

export interface AppShellProps {
  activeTab: string;
  setActiveTab: (tab: string) => void;
  systemStatus: SystemStatus | null;
  isStale: boolean;
  onEmergencyStop: () => void;
  onStartDemo: () => void;
  isViewer?: boolean;
  children: React.ReactNode;
}

export const navItems = [
  { name: 'Home / System', id: 'Home', icon: Home },
  { name: 'Trading', id: 'Trading', icon: TrendingUp },
  { name: 'Strategies', id: 'Strategies', icon: Zap },
  { name: 'Macro Calendar & Radar', id: 'Calendar', icon: Calendar },
  { name: 'Backtests', id: 'Backtests', icon: BarChart2 },
  { name: 'Risk', id: 'Risk', icon: ShieldAlert },
  { name: 'Logs & Diagnostics', id: 'Diagnostics', icon: FileText },
  { name: 'Settings', id: 'Settings', icon: Settings },
];

export const AppShell: React.FC<AppShellProps> = ({
  activeTab,
  setActiveTab,
  systemStatus,
  isStale,
  onEmergencyStop,
  onStartDemo,
  isViewer = false,
  children,
}) => {
  const [darkMode, setDarkMode] = React.useState<boolean>(() => {
    return localStorage.getItem('quantdesk-theme') === 'dark';
  });

  const toggleTheme = () => {
    const next = !darkMode;
    setDarkMode(next);
    localStorage.setItem('quantdesk-theme', next ? 'dark' : 'light');
  };

  const isLive = systemStatus?.mode === 'LIVE';

  return (
    <div className={`min-h-screen flex flex-col font-sans ${darkMode ? 'bg-slate-950 text-slate-100' : 'bg-slate-50 text-slate-900'}`}>
      {/* Top Environment & Control Bar */}
      <header className={`flex items-center justify-between px-4 py-2.5 border-b ${darkMode ? 'bg-slate-900 border-slate-800' : 'bg-white border-slate-200'}`}>
        <div className="flex items-center space-x-3">
          <span className="font-bold text-lg tracking-tight flex items-center gap-2">
            <Activity className="w-5 h-5 text-indigo-600" />
            QuantDesk
          </span>

          {/* Persistent Mode Banner (§15.1) */}
          <span
            data-testid="mode-banner"
            className={`px-2.5 py-0.5 text-xs font-bold rounded tracking-wider uppercase border ${
              isLive
                ? 'bg-rose-500 text-white border-rose-600 animate-pulse'
                : 'bg-amber-100 text-amber-800 border-amber-300'
            }`}
          >
            {systemStatus?.mode || 'DEMO'}
          </span>

          <span className={`text-xs px-2 py-0.5 rounded ${darkMode ? 'bg-slate-800 text-slate-300' : 'bg-slate-100 text-slate-600'}`}>
            Venue: <strong>{systemStatus?.venue || 'bitget'}</strong>
          </span>

          <span className={`text-xs px-2 py-0.5 rounded ${darkMode ? 'bg-slate-800 text-slate-300' : 'bg-slate-100 text-slate-600'}`}>
            Account: <strong>{systemStatus?.account_alias || 'paper-demo'}</strong>
          </span>

          <span className={`text-xs px-2 py-0.5 rounded font-mono ${
            systemStatus?.engine_state === 'RUNNING' ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'
          }`}>
            Engine: {systemStatus?.engine_state || 'RUNNING'}
          </span>
        </div>

        <div className="flex items-center space-x-3">
          {/* Freshness Indicator (§15.3) */}
          <StaleBadge isStale={isStale} isConnected={true} />

          {/* Prominent Emergency Stop button (§15.1, §15.3 - always reachable for operators) */}
          {!isViewer && (
            <button
              data-testid="emergency-stop"
              onClick={onEmergencyStop}
              className="px-3 py-1 bg-rose-600 hover:bg-rose-700 text-white rounded text-xs font-bold transition-colors flex items-center gap-1.5 shadow-sm"
            >
              <ShieldAlert className="w-4 h-4" />
              Emergency stop
            </button>
          )}

          {/* Theme Toggle */}
          <button
            onClick={toggleTheme}
            aria-label="Toggle theme"
            className={`p-1.5 rounded transition-colors ${darkMode ? 'hover:bg-slate-800 text-slate-400' : 'hover:bg-slate-100 text-slate-500'}`}
          >
            {darkMode ? <Sun className="w-4 h-4 text-amber-400" /> : <Moon className="w-4 h-4" />}
          </button>
        </div>
      </header>

      {/* Stale Connection Warning Alert Banner (§15.3) */}
      {isStale && (
        <div
          data-testid="stale-banner"
          className="bg-amber-500 text-slate-950 px-4 py-1.5 text-xs font-semibold flex items-center justify-between shadow-inner"
        >
          <span className="flex items-center gap-2">
            <ShieldAlert className="w-4 h-4" />
            Backend update stream latency exceeds 3 seconds. Real-time projections may be stale. Ordinary risk controls disabled.
          </span>
          <span className="underline cursor-pointer" onClick={() => window.location.reload()}>
            Refresh connection
          </span>
        </div>
      )}

      {/* Main Container: Left Nav Rail + Content Area */}
      <div className="flex flex-1 overflow-hidden">
        {/* Navigation Rail */}
        <nav
          aria-label="Sidebar"
          className={`w-56 border-r flex flex-col p-3 space-y-1 overflow-y-auto ${
            darkMode ? 'bg-slate-900/60 border-slate-800' : 'bg-white border-slate-200'
          }`}
        >
          {navItems.map((item) => {
            const Icon = item.icon;
            const isSelected = activeTab === item.name || activeTab === item.id;
            return (
              <a
                key={item.id}
                href={`#${item.id.toLowerCase()}`}
                onClick={(e) => {
                  e.preventDefault();
                  setActiveTab(item.name);
                }}
                className={`flex items-center gap-3 px-3 py-2 text-sm rounded-lg transition-all ${
                  isSelected
                    ? darkMode
                      ? 'bg-indigo-950/80 text-indigo-400 font-semibold border border-indigo-900/50'
                      : 'bg-indigo-50 text-indigo-700 font-semibold border border-indigo-100'
                    : darkMode
                    ? 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
                    : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                }`}
              >
                <Icon className={`w-4 h-4 ${isSelected ? 'text-indigo-600 dark:text-indigo-400' : 'text-slate-400'}`} />
                <span>{item.name}</span>
              </a>
            );
          })}
        </nav>

        {/* Workspace Content */}
        <main className="flex-1 p-6 overflow-y-auto">
          {children}
        </main>
      </div>
    </div>
  );
};
