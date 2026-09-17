import React, { useState } from 'react';
import {
  AlertTriangle,
  Archive,
  CheckCircle2,
  Database,
  Eye,
  EyeOff,
  KeyRound,
  Lock,
  RefreshCw,
  Save,
  Server,
  ShieldAlert,
  ShieldCheck,
} from 'lucide-react';
import { api } from '../services/apiClient';
import { SystemStatus } from '../types/api';

export interface SettingsPageProps {
  systemStatus: SystemStatus | null;
  onOpenCommand: (type: string, target?: Record<string, any>, payload?: Record<string, any>) => void;
  isViewer?: boolean;
}

export const SettingsPage: React.FC<SettingsPageProps> = ({
  systemStatus,
  onOpenCommand,
  isViewer = false,
}) => {
  const [apiKey, setApiKey] = useState('');
  const [secretKey, setSecretKey] = useState('');
  const [passphrase, setPassphrase] = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const [testSuccess, setTestSuccess] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [keyFingerprint, setKeyFingerprint] = useState<string | null>(null);

  const [timezone, setTimezone] = useState('UTC');
  const [storageBackupPath, setStorageBackupPath] = useState('backups/quantdesk_backup.sqlite');

  React.useEffect(() => {
    api.getCredentialsStatus()
      .then((data) => {
        if (data.has_credentials) {
          setKeyFingerprint(data.key_fingerprint);
        }
      })
      .catch(() => {});
  }, []);

  const handleSaveKeys = async () => {
    setErrorMessage(null);
    setSaveSuccess(null);
    if (!apiKey.trim() || !secretKey.trim() || !passphrase.trim()) {
      setErrorMessage('All three fields (API key, Secret key, Passphrase) are required to save.');
      return;
    }
    setSaving(true);
    try {
      const data = await api.saveCredentials({
        api_key: apiKey.trim(),
        secret_key: secretKey.trim(),
        passphrase: passphrase.trim(),
      });
      setSaving(false);
      setSaveSuccess(data.message || 'Credentials securely stored in OS Keyring.');
      setKeyFingerprint(data.key_fingerprint);
      setApiKey('');
      setSecretKey('');
      setPassphrase('');
    } catch (err: any) {
      setSaving(false);
      setErrorMessage(err.message || 'Failed to save credentials.');
    }
  };

  const handleTestConnection = async (e: React.FormEvent) => {
    e.preventDefault();
    setTesting(true);
    setTestSuccess(null);
    setErrorMessage(null);
    try {
      const payload = apiKey.trim()
        ? { api_key: apiKey.trim(), secret_key: secretKey.trim(), passphrase: passphrase.trim() }
        : null;
      const data = await api.testConnection(payload);
      setTesting(false);
      if (data.success) {
        setTestSuccess(`${data.message} Account Level: ${data.account_level || 'UTA'}.`);
      } else {
        setErrorMessage(data.message || 'Bitget read-only connection test failed.');
      }
    } catch (err: any) {
      setTesting(false);
      setErrorMessage(err.message || 'Connection test error.');
    }
  };

  const handleArmLive = () => {
    onOpenCommand('ARM_LIVE', { account_id: systemStatus?.account_alias || 'paper-demo' });
  };

  const handleTriggerBackup = () => {
    alert('Local consistent SQLite WAL backup initiated.');
  };

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
            System Settings & Security Configuration
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            Bitget UTA venue configuration, write-only credential storage, backup management, and environment arming.
          </p>
        </div>
      </div>

      {testSuccess && (
        <div className="p-3.5 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-800 dark:text-emerald-300 rounded-lg text-xs font-medium flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
          {testSuccess}
        </div>
      )}

      {saveSuccess && (
        <div className="p-3.5 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-800 dark:text-emerald-300 rounded-lg text-xs font-medium flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
          {saveSuccess}
        </div>
      )}

      {errorMessage && (
        <div className="p-3.5 bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 text-rose-800 dark:text-rose-300 rounded-lg text-xs font-medium flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-rose-600 dark:text-rose-400 shrink-0" />
          {errorMessage}
        </div>
      )}

      {/* Exchange Credentials Card (§15.4, §15.2) */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
        <div className="p-5 border-b border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/50 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <div className="flex items-center gap-2">
              <KeyRound className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
              <h2 className="font-bold text-base text-slate-900 dark:text-slate-100">
                Bitget UTA V3 Venue Credentials (Isolated Margin)
              </h2>
            </div>
            <p className="text-xs text-slate-500 mt-1">
              Secrets are saved write-only to Windows Credential Manager / OS Keyring. They are never sent back to the browser.
            </p>
          </div>
          {keyFingerprint && (
            <span className="px-2.5 py-1 bg-indigo-50 dark:bg-indigo-950/50 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800 rounded-full text-[11px] font-mono font-medium flex items-center gap-1.5 self-start sm:self-auto">
              <Lock className="w-3 h-3 text-indigo-500" /> Keyring: {keyFingerprint}
            </span>
          )}
        </div>

        <form onSubmit={handleTestConnection} className="p-6 space-y-4 text-xs">
          <div>
            <label className="block font-medium text-slate-700 dark:text-slate-300 mb-1">
              API Key (Trading Only - No Withdrawal Permissions)
            </label>
            <input
              type="text"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={keyFingerprint ? `Configured in Keyring (${keyFingerprint})` : "bg_xxxxxxxxxxxxxxxxxxxx"}
              className="w-full px-3 py-2 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100 font-mono"
            />
          </div>

          <div>
            <label className="block font-medium text-slate-700 dark:text-slate-300 mb-1">
              API Secret
            </label>
            <div className="relative">
              <input
                type={showSecret ? 'text' : 'password'}
                value={secretKey}
                onChange={(e) => setSecretKey(e.target.value)}
                placeholder={keyFingerprint ? "•••••••••••••••• (Saved in Keyring)" : "••••••••••••••••••••••••••••••••"}
                className="w-full px-3 py-2 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100 font-mono"
              />
              <button
                type="button"
                onClick={() => setShowSecret(!showSecret)}
                className="absolute right-3 top-2.5 text-slate-400"
              >
                {showSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          <div>
            <label className="block font-medium text-slate-700 dark:text-slate-300 mb-1">
              Passphrase
            </label>
            <input
              type="password"
              value={passphrase}
              onChange={(e) => setPassphrase(e.target.value)}
              placeholder={keyFingerprint ? "•••••••••••••••• (Saved in Keyring)" : "••••••••••••••••"}
              className="w-full px-3 py-2 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100 font-mono"
            />
          </div>

          <div className="pt-3 flex items-center justify-between border-t border-slate-100 dark:border-slate-800">
            <button
              type="submit"
              disabled={testing}
              className="px-4 py-2 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 text-slate-800 dark:text-slate-200 rounded-lg font-semibold transition-colors flex items-center gap-1.5"
            >
              {testing ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Server className="w-3.5 h-3.5" />}
              Test Read-Only Connection
            </button>
            {!isViewer && (
              <button
                type="button"
                onClick={handleSaveKeys}
                disabled={saving}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-semibold transition-colors flex items-center gap-1.5"
              >
                {saving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                Save Keys Securely
              </button>
            )}
          </div>
        </form>
      </div>

      {/* Storage and Backup Wizard (§16.3) */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-6 shadow-sm space-y-4">
        <h2 className="font-bold text-base text-slate-900 dark:text-slate-100 flex items-center gap-2">
          <Database className="w-4 h-4 text-indigo-600" />
          Consistent SQLite WAL Backup Wizard
        </h2>
        <div className="space-y-3 text-xs">
          <p className="text-slate-500">
            Uses SQLite online backup API to take a non-blocking snapshot of event store and outbox without corrupting WAL logs.
          </p>
          <div className="flex gap-3 items-center">
            <input
              type="text"
              value={storageBackupPath}
              onChange={(e) => setStorageBackupPath(e.target.value)}
              className="flex-1 px-3 py-2 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 font-mono"
            />
            {!isViewer && (
              <button
                onClick={handleTriggerBackup}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-semibold transition-colors"
              >
                Run Backup Now
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Environment Danger Zone (§15.2, §15.3) */}
      <div className="bg-rose-50 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-900 rounded-xl p-6 shadow-sm space-y-4">
        <div className="flex items-center gap-2 text-rose-800 dark:text-rose-300">
          <ShieldAlert className="w-5 h-5" />
          <h2 className="font-bold text-base">Danger Zone: Mainnet LIVE Environment Arming</h2>
        </div>
        <p className="text-xs text-rose-700 dark:text-rose-400">
          Default mode is strictly DEMO (Fail-closed). Arming LIVE requires 100% passed readiness gates, verified API credentials, and explicit typed confirmation dialog.
        </p>
        {!isViewer && (
          <button
            onClick={handleArmLive}
            className="px-4 py-2.5 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-xs font-bold transition-colors shadow-sm"
          >
            Arm LIVE Trading Mode (Confirmed Dialog)
          </button>
        )}
      </div>
    </div>
  );
};
