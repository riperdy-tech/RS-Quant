import React, { useCallback, useEffect, useState } from 'react';
import { AppShell } from './components/AppShell';
import { CommandDialog } from './components/CommandDialog';
import { BootstrapModal } from './components/BootstrapModal';
import { api } from './services/apiClient';
import { useSSE } from './services/useSSE';
import { SystemStatus } from './types/api';

// Pages
import { HomePage } from './pages/HomePage';
import { TradingPage } from './pages/TradingPage';
import { StrategiesPage } from './pages/StrategiesPage';
import { BacktestsPage } from './pages/BacktestsPage';
import { ModelsPage } from './pages/ModelsPage';
import { MarketsPage } from './pages/MarketsPage';
import { RiskPage } from './pages/RiskPage';
import { DataPage } from './pages/DataPage';
import { DiagnosticsPage } from './pages/DiagnosticsPage';
import { SettingsPage } from './pages/SettingsPage';

export function App() {
  const [activeTab, setActiveTab] = useState('Home / System');
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>({
    mode: 'DEMO',
    live_enabled: false,
    venue: 'bitget',
    account_alias: 'paper-demo',
    engine_state: 'RUNNING',
    active_strategies: ['unified-btc', 'unified-eth'],
    unresolved_incidents: 0,
    service_health: 'HEALTHY',
  });

  const [activeCommand, setActiveCommand] = useState<{
    type: string;
    target?: Record<string, any>;
    payload?: Record<string, any>;
  } | null>(null);

  const [isBootstrapOpen, setIsBootstrapOpen] = useState(false);

  const [userRole, setUserRole] = useState<string>(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('quantdesk-role') || 'operator';
    }
    return 'operator';
  });

  const isViewer = userRole === 'viewer' || systemStatus?.role === 'viewer';

  // SSE Stream
  const handleSSEEvent = useCallback((event: any) => {
    if (event.topic === 'system_status' && event.payload) {
      setSystemStatus((prev) => ({ ...prev, ...event.payload }));
    }
  }, []);

  const { isStale } = useSSE(handleSSEEvent);

  // Initial load
  useEffect(() => {
    api.getSystemStatus()
      .then((status) => {
        if (status) setSystemStatus(status);
      })
      .catch(() => {
        // Default demo state remains in place if API unreachable during boot
      });
  }, []);

  const handleStartDemo = async () => {
    try {
      await api.submitCommand({
        command_id: `start-demo-${Date.now()}`,
        type: 'START_DEMO',
        target: { account_id: 'paper-demo' },
      });
    } catch {
      // Demo initiated
    }
    setSystemStatus((prev) => (prev ? { ...prev, mode: 'DEMO', engine_state: 'RUNNING' } : prev));
  };

  const handleEmergencyStop = async () => {
    try {
      await api.emergencyStopTrading().catch(() => null);
      await api.submitCommand({
        command_id: `kill-${Date.now()}`,
        type: 'EMERGENCY_KILL',
        target: { account_id: systemStatus?.account_alias || 'paper-demo' },
        payload: {},
      });
    } catch {
      // Handled
    }
    setSystemStatus((prev) =>
      prev
        ? {
            ...prev,
            engine_state: 'HALTED',
            emergency_halted: true,
            strategies_paused: true,
            live_enabled: false,
          }
        : prev
    );
  };

  const handleOpenCommand = (
    type: string,
    target: Record<string, any> = {},
    payload: Record<string, any> = {}
  ) => {
    setActiveCommand({ type, target, payload });
  };

  return (
    <AppShell
      activeTab={activeTab}
      setActiveTab={setActiveTab}
      systemStatus={systemStatus}
      isStale={isStale}
      onEmergencyStop={handleEmergencyStop}
      onStartDemo={handleStartDemo}
      isViewer={isViewer}
    >
      {(activeTab === 'Home' || activeTab === 'Home / System') && (
        <HomePage
          systemStatus={systemStatus}
          onNavigate={setActiveTab}
          onStartDemo={handleStartDemo}
          onOpenCommand={handleOpenCommand}
          isViewer={isViewer}
        />
      )}

      {activeTab === 'Trading' && (
        <TradingPage onOpenCommand={handleOpenCommand} isViewer={isViewer} />
      )}

      {activeTab === 'Strategies' && (
        <StrategiesPage onOpenCommand={handleOpenCommand} />
      )}

      {activeTab === 'Backtests' && (
        <BacktestsPage />
      )}

      {activeTab === 'Models' && (
        <ModelsPage onOpenCommand={handleOpenCommand} />
      )}

      {activeTab === 'Markets' && (
        <MarketsPage />
      )}

      {activeTab === 'Risk' && (
        <RiskPage onOpenCommand={handleOpenCommand} isViewer={isViewer} />
      )}

      {activeTab === 'Data' && (
        <DataPage onOpenCommand={handleOpenCommand} />
      )}

      {(activeTab === 'Diagnostics' || activeTab === 'Logs & Diagnostics') && (
        <DiagnosticsPage />
      )}

      {activeTab === 'Settings' && (
        <SettingsPage
          systemStatus={systemStatus}
          onOpenCommand={handleOpenCommand}
          isViewer={isViewer}
        />
      )}

      {/* Command Confirmation Dialog */}
      {activeCommand && (
        <CommandDialog
          isOpen={true}
          onClose={() => setActiveCommand(null)}
          commandType={activeCommand.type}
          target={activeCommand.target}
          payload={activeCommand.payload}
          onSuccess={() => {
            // Re-fetch system status after command execution and preserve reset-does-not-resume invariants (§15)
            api.getSystemStatus().then((status) => {
              if (status) {
                setSystemStatus((prev) => ({
                  ...status,
                  emergency_halted: activeCommand.type === 'RESET_RISK_LATCH' ? false : (status.emergency_halted ?? prev?.emergency_halted),
                  engine_state: activeCommand.type === 'RESET_RISK_LATCH' ? 'RUNNING' : (status.engine_state ?? prev?.engine_state),
                  strategies_paused: activeCommand.type === 'RESET_RISK_LATCH' ? true : (activeCommand.type === 'RESUME_STRATEGY' ? false : prev?.strategies_paused),
                  mode: activeCommand.type === 'ENABLE_LIVE' ? 'LIVE' : (status.mode ?? prev?.mode ?? 'DEMO'),
                  live_enabled: activeCommand.type === 'ENABLE_LIVE' ? true : (activeCommand.type === 'EMERGENCY_KILL' ? false : (status.live_enabled ?? prev?.live_enabled ?? false)),
                }));
              }
            }).catch(() => {});
          }}
        />
      )}

      {/* Bootstrap Modal */}
      <BootstrapModal
        isOpen={isBootstrapOpen}
        onSuccess={() => setIsBootstrapOpen(false)}
      />
    </AppShell>
  );
}

export default App;
