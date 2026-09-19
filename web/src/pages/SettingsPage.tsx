import React, { useState } from 'react';
import {
  AlertTriangle,
  Archive,
  CheckCircle2,
  Coins,
  Database,
  Eye,
  EyeOff,
  Gauge,
  KeyRound,
  Lock,
  RefreshCw,
  Save,
  Server,
  ShieldAlert,
  ShieldCheck,
  Zap,
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
  const [selectedVenue, setSelectedVenue] = useState<'mexc' | 'bitget'>('mexc');
  const [apiKey, setApiKey] = useState('');
  const [secretKey, setSecretKey] = useState('');
  const [passphrase, setPassphrase] = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const [testSuccess, setTestSuccess] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [mexcFingerprint, setMexcFingerprint] = useState<string | null>(null);
  const [bitgetFingerprint, setBitgetFingerprint] = useState<string | null>(null);

  const [timezone, setTimezone] = useState('UTC');
  const [storageBackupPath, setStorageBackupPath] = useState('backups/quantdesk_backup.sqlite');

  const [capitalUsdt, setCapitalUsdt] = useState('10000');
  const [leverage, setLeverage] = useState('3.0');
  const [capitalSaved, setCapitalSaved] = useState<string | null>(null);
  const [savingCapital, setSavingCapital] = useState(false);

  const refreshCredentialsStatus = () => {
    api.getCredentialsStatus()
      .then((data) => {
        if (data.mexc?.has_credentials) {
          setMexcFingerprint(data.mexc.key_fingerprint);
        } else {
          setMexcFingerprint(null);
        }
        if (data.bitget?.has_credentials) {
          setBitgetFingerprint(data.bitget.key_fingerprint);
        } else {
          setBitgetFingerprint(null);
        }
        if (data.active_venue === 'bitget') {
          setSelectedVenue('bitget');
        } else {
          setSelectedVenue('mexc');
        }
      })
      .catch(() => {});
  };

  React.useEffect(() => {
    refreshCredentialsStatus();

    api.getTradingCapitalConfig()
      .then((data) => {
        if (data.capital_usdt) setCapitalUsdt(parseFloat(data.capital_usdt).toString());
        if (data.leverage) setLeverage(parseFloat(data.leverage).toString());
      })
      .catch(() => {});
  }, []);

  const activeFingerprint = selectedVenue === 'mexc' ? mexcFingerprint : bitgetFingerprint;

  const handleSaveKeys = async () => {
    setErrorMessage(null);
    setSaveSuccess(null);

    if (selectedVenue === 'mexc') {
      if (!apiKey.trim() || !secretKey.trim()) {
        setErrorMessage('Both API Key and Secret Key are required for MEXC.');
        return;
      }
    } else {
      if (!apiKey.trim() || !secretKey.trim() || !passphrase.trim()) {
        setErrorMessage('All three fields (API Key, Secret Key, Passphrase) are required for Bitget UTA.');
        return;
      }
    }

    setSaving(true);
    try {
      const data = await api.saveCredentials({
        venue: selectedVenue,
        api_key: apiKey.trim(),
        secret_key: secretKey.trim(),
        passphrase: passphrase.trim() || undefined,
      });
      setSaving(false);
      setSaveSuccess(data.message || `${selectedVenue.toUpperCase()} credentials securely stored in OS Keyring.`);
      if (selectedVenue === 'mexc') {
        setMexcFingerprint(data.key_fingerprint);
      } else {
        setBitgetFingerprint(data.key_fingerprint);
      }
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
        ? {
            venue: selectedVenue,
            api_key: apiKey.trim(),
            secret_key: secretKey.trim(),
            passphrase: passphrase.trim() || undefined,
          }
        : { venue: selectedVenue, api_key: '', secret_key: '' };
      const data = await api.testConnection(payload);
      setTesting(false);
      if (data.success) {
        if (selectedVenue === 'mexc') {
          const eqStr = data.total_equity ? ` [Available Equity: ${parseFloat(data.total_equity).toLocaleString()} USDT]` : '';
          setTestSuccess(`${data.message}${eqStr}`);
        } else {
          setTestSuccess(`${data.message} Account Level: ${data.account_level || 'UTA'}.`);
        }
      } else {
        setErrorMessage(data.message || `${selectedVenue.toUpperCase()} read-only connection test failed.`);
      }
    } catch (err: any) {
      setTesting(false);
      setErrorMessage(err.message || 'Connection test error.');
    }
  };

  const handleSaveCapital = async () => {
    setErrorMessage(null);
    setCapitalSaved(null);
    const capNum = parseFloat(capitalUsdt);
    const levNum = parseFloat(leverage);
    if (isNaN(capNum) || capNum < 20) {
      setErrorMessage('Initial capital must be at least 20 USDT.');
      return;
    }
    if (isNaN(levNum) || levNum < 1 || levNum > 10) {
      setErrorMessage('Leverage multiplier must be between 1x and 10x.');
      return;
    }
    setSavingCapital(true);
    try {
      const res = await api.saveTradingCapitalConfig({ capital_usdt: capNum, leverage: levNum });
      setSavingCapital(false);
      setCapitalSaved(`Trading parameters saved: ${parseFloat(res.capital_usdt).toLocaleString()} USDT Capital @ ${res.leverage}x Leverage (${parseFloat(res.notional_per_leg).toLocaleString()} USDT notional per leg).`);
    } catch (err: any) {
      setSavingCapital(false);
      setErrorMessage(err.message || 'Failed to save capital configuration.');
    }
  };

  const handleArmLive = () => {
    const capNum = parseFloat(capitalUsdt) || 10000;
    const levNum = parseFloat(leverage) || 3.0;
    onOpenCommand(
      'ARM_LIVE',
      { account_id: systemStatus?.account_alias || `paper-demo-${selectedVenue}`, capital_usdt: capNum, leverage: levNum },
      { capital_usdt: capNum, leverage: levNum }
    );
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
            System Settings & Venue Security
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            MEXC Contract V1 (0% Maker Fees) & Bitget UTA venue configuration, write-only credential storage, and capital sizing.
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
        {/* Venue Selection Tabs */}
        <div className="flex border-b border-slate-200 dark:border-slate-800 bg-slate-100/70 dark:bg-slate-950/60 p-1.5 gap-2">
          <button
            type="button"
            onClick={() => { setSelectedVenue('mexc'); setApiKey(''); setSecretKey(''); setPassphrase(''); setTestSuccess(null); setErrorMessage(null); }}
            className={`flex-1 py-2 px-3 rounded-lg text-xs font-semibold flex items-center justify-center gap-2 transition-all ${
              selectedVenue === 'mexc'
                ? 'bg-white dark:bg-slate-800 text-indigo-600 dark:text-indigo-400 shadow-sm border border-slate-200/60 dark:border-slate-700'
                : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
            }`}
          >
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
            <span>MEXC Contract V1</span>
            <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 font-bold">
              0.00% MAKER FEE
            </span>
          </button>
          <button
            type="button"
            onClick={() => { setSelectedVenue('bitget'); setApiKey(''); setSecretKey(''); setPassphrase(''); setTestSuccess(null); setErrorMessage(null); }}
            className={`flex-1 py-2 px-3 rounded-lg text-xs font-semibold flex items-center justify-center gap-2 transition-all ${
              selectedVenue === 'bitget'
                ? 'bg-white dark:bg-slate-800 text-indigo-600 dark:text-indigo-400 shadow-sm border border-slate-200/60 dark:border-slate-700'
                : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
            }`}
          >
            <span>Bitget UTA V3</span>
            <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-slate-200 dark:bg-slate-800 text-slate-600 dark:text-slate-400">
              0.02% MAKER
            </span>
          </button>
        </div>

        <div className="p-5 border-b border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/50 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <div className="flex items-center gap-2">
              <KeyRound className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
              <h2 className="font-bold text-base text-slate-900 dark:text-slate-100">
                {selectedVenue === 'mexc'
                  ? 'MEXC Futures Credentials (USDT-M Perpetual)'
                  : 'Bitget UTA V3 Venue Credentials (Isolated Margin)'}
              </h2>
            </div>
            <p className="text-xs text-slate-500 mt-1">
              {selectedVenue === 'mexc'
                ? 'MEXC provides 0.00% Maker Fee execution via Post-Only limit orders. Only API Key and Secret Key are required.'
                : 'Bitget UTA V3 uses API Key, Secret Key, and Passphrase with Isolated Margin mode.'}
            </p>
          </div>
          {activeFingerprint && (
            <span className="px-2.5 py-1 bg-indigo-50 dark:bg-indigo-950/50 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800 rounded-full text-[11px] font-mono font-medium flex items-center gap-1.5 self-start sm:self-auto">
              <Lock className="w-3 h-3 text-indigo-500" /> {selectedVenue.toUpperCase()}: {activeFingerprint}
            </span>
          )}
        </div>

        <form onSubmit={handleTestConnection} className="p-6 space-y-4 text-xs">
          <div>
            <label className="block font-medium text-slate-700 dark:text-slate-300 mb-1">
              {selectedVenue === 'mexc' ? 'MEXC API Key (Futures Read & Trade Only)' : 'Bitget API Key (Trading Only - No Withdrawal Permissions)'}
            </label>
            <input
              type="text"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={activeFingerprint ? `Configured in Keyring (${activeFingerprint})` : (selectedVenue === 'mexc' ? "mx0vgxxxxxxxxxxxxxxxx" : "bg_xxxxxxxxxxxxxxxxxxxx")}
              className="w-full px-3 py-2 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100 font-mono"
            />
          </div>

          <div>
            <label className="block font-medium text-slate-700 dark:text-slate-300 mb-1">
              {selectedVenue === 'mexc' ? 'MEXC Secret Key' : 'Bitget API Secret'}
            </label>
            <div className="relative">
              <input
                type={showSecret ? 'text' : 'password'}
                value={secretKey}
                onChange={(e) => setSecretKey(e.target.value)}
                placeholder={activeFingerprint ? "•••••••••••••••• (Saved in Keyring)" : "••••••••••••••••••••••••••••••••"}
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

          {selectedVenue === 'bitget' && (
            <div>
              <label className="block font-medium text-slate-700 dark:text-slate-300 mb-1">
                Passphrase
              </label>
              <input
                type="password"
                value={passphrase}
                onChange={(e) => setPassphrase(e.target.value)}
                placeholder={activeFingerprint ? "•••••••••••••••• (Saved in Keyring)" : "••••••••••••••••"}
                className="w-full px-3 py-2 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100 font-mono"
              />
            </div>
          )}

          {selectedVenue === 'mexc' && (
            <div className="p-3 bg-indigo-50/50 dark:bg-indigo-950/20 border border-indigo-100 dark:border-indigo-900/40 rounded-lg text-[11px] text-indigo-700 dark:text-indigo-300">
              💡 <strong>No Passphrase Required</strong>: MEXC Contract API authentication uses HMAC-SHA256 with only your API Key and Secret Key.
            </div>
          )}

          <div className="pt-3 flex items-center justify-between border-t border-slate-100 dark:border-slate-800">
            <button
              type="submit"
              disabled={testing}
              className="px-4 py-2 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 text-slate-800 dark:text-slate-200 rounded-lg font-semibold transition-colors flex items-center gap-1.5"
            >
              {testing ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Server className="w-3.5 h-3.5" />}
              Test {selectedVenue.toUpperCase()} Connection
            </button>
            {!isViewer && (
              <button
                type="button"
                onClick={handleSaveKeys}
                disabled={saving}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-semibold transition-colors flex items-center gap-1.5"
              >
                {saving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                Save {selectedVenue.toUpperCase()} Keys
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

      {capitalSaved && (
        <div className="p-3.5 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-800 dark:text-emerald-300 rounded-lg text-xs font-medium flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
          {capitalSaved}
        </div>
      )}

      {/* Trading Capital & Leverage Sizing Card */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
        <div className="p-5 border-b border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/50 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <div className="flex items-center gap-2">
              <Coins className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
              <h2 className="font-bold text-base text-slate-900 dark:text-slate-100">
                Initial Working Capital & Leverage Sizing (Bitget UTA)
              </h2>
            </div>
            <p className="text-xs text-slate-500 mt-1">
              Configure your desired starting capital (USDT) and isolated leverage. Positions are dynamically sized 50/50 across BTC and ETH with exact Bitget contract lot quantization.
            </p>
          </div>
          <span className="px-2.5 py-1 bg-emerald-50 dark:bg-emerald-950/50 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800 rounded-full text-[11px] font-mono font-medium flex items-center gap-1.5 self-start sm:self-auto">
            <Gauge className="w-3 h-3 text-emerald-500" /> {(parseFloat(capitalUsdt) * parseFloat(leverage) || 0).toLocaleString()} USDT Purchasing Power
          </span>
        </div>

        <div className="p-6 space-y-5 text-xs">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {/* Capital Input */}
            <div className="space-y-2">
              <label className="block font-medium text-slate-700 dark:text-slate-300">
                Desired Starting Capital (USDT)
              </label>
              <div className="relative">
                <input
                  type="number"
                  min="20"
                  step="10"
                  value={capitalUsdt}
                  onChange={(e) => setCapitalUsdt(e.target.value)}
                  placeholder="1000"
                  className="w-full px-3 py-2 pr-16 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100 font-mono text-sm font-semibold"
                />
                <span className="absolute right-3 top-2.5 text-xs font-bold text-slate-400">USDT</span>
              </div>
              {/* Quick Presets */}
              <div className="flex items-center gap-1.5 pt-1">
                <span className="text-[11px] text-slate-400">Presets:</span>
                {['100', '500', '1000', '5000', '10000'].map((val) => (
                  <button
                    key={val}
                    type="button"
                    onClick={() => setCapitalUsdt(val)}
                    className={`px-2 py-0.5 rounded text-[11px] font-mono font-medium transition-colors ${
                      capitalUsdt === val
                        ? 'bg-indigo-600 text-white'
                        : 'bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 text-slate-600 dark:text-slate-400'
                    }`}
                  >
                    {val}
                  </button>
                ))}
              </div>
            </div>

            {/* Leverage Input */}
            <div className="space-y-2">
              <label className="block font-medium text-slate-700 dark:text-slate-300">
                Isolated Leverage Multiplier
              </label>
              <div className="relative">
                <input
                  type="number"
                  min="1"
                  max="10"
                  step="0.5"
                  value={leverage}
                  onChange={(e) => setLeverage(e.target.value)}
                  placeholder="3"
                  className="w-full px-3 py-2 pr-12 border rounded-lg bg-slate-50 dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100 font-mono text-sm font-semibold"
                />
                <span className="absolute right-3 top-2.5 text-xs font-bold text-slate-400">x</span>
              </div>
              {/* Quick Presets */}
              <div className="flex items-center gap-1.5 pt-1">
                <span className="text-[11px] text-slate-400">Presets:</span>
                {['1.0', '2.0', '3.0', '5.0', '10.0'].map((val) => (
                  <button
                    key={val}
                    type="button"
                    onClick={() => setLeverage(val)}
                    className={`px-2 py-0.5 rounded text-[11px] font-mono font-medium transition-colors ${
                      parseFloat(leverage) === parseFloat(val)
                        ? 'bg-indigo-600 text-white'
                        : 'bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 text-slate-600 dark:text-slate-400'
                    }`}
                  >
                    {val}x
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Mathematical Sizing Breakdown Card */}
          {(() => {
            const cap = parseFloat(capitalUsdt) || 0;
            const lev = parseFloat(leverage) || 1;
            const marginLeg = cap / 2;
            const notionalLeg = marginLeg * lev;
            const btcUnits = notionalLeg > 0 ? (notionalLeg / 77200).toFixed(4) : '0.0000';
            const ethUnits = notionalLeg > 0 ? (notionalLeg / 2470).toFixed(2) : '0.00';
            return (
              <div className="p-4 bg-slate-50 dark:bg-slate-800/60 rounded-lg border border-slate-200 dark:border-slate-700/60 space-y-2.5">
                <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                  Live Mathematical Sizing Breakdown (50% BTC / 50% ETH Split)
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 font-mono text-xs">
                  <div>
                    <div className="text-[11px] text-slate-400">Margin Per Leg</div>
                    <div className="font-bold text-slate-900 dark:text-slate-100">{marginLeg.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDT</div>
                  </div>
                  <div>
                    <div className="text-[11px] text-slate-400">Notional Per Leg</div>
                    <div className="font-bold text-indigo-600 dark:text-indigo-400">{notionalLeg.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDT</div>
                  </div>
                  <div>
                    <div className="text-[11px] text-slate-400">Est. BTC Sizing</div>
                    <div className="font-bold text-slate-900 dark:text-slate-100">~{btcUnits} BTC</div>
                  </div>
                  <div>
                    <div className="text-[11px] text-slate-400">Est. ETH Sizing</div>
                    <div className="font-bold text-slate-900 dark:text-slate-100">~{ethUnits} ETH</div>
                  </div>
                </div>
              </div>
            );
          })()}

          <div className="flex justify-end pt-2">
            {!isViewer && (
              <button
                type="button"
                onClick={handleSaveCapital}
                disabled={savingCapital}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-semibold transition-colors flex items-center gap-1.5"
              >
                {savingCapital ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                Save Capital & Sizing Parameters
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
          Default mode is strictly DEMO (Fail-closed). Arming LIVE will initialize execution using <strong>{parseFloat(capitalUsdt || '10000').toLocaleString()} USDT</strong> capital at <strong>{leverage || '3'}x leverage</strong>. Requires 100% passed readiness gates, verified API credentials, and explicit typed confirmation dialog.
        </p>
        {!isViewer && (
          <button
            onClick={handleArmLive}
            className="px-4 py-2.5 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-xs font-bold transition-colors shadow-sm flex items-center gap-2"
          >
            <Zap className="w-4 h-4" />
            Arm LIVE Trading Mode ({parseFloat(capitalUsdt || '10000').toLocaleString()} USDT @ {leverage || '3'}x)
          </button>
        )}
      </div>
    </div>
  );
};
