import React, { useState } from 'react';
import { AlertTriangle, CheckCircle, Loader2, X } from 'lucide-react';
import { api } from '../services/apiClient';

export interface CommandDialogProps {
  isOpen: boolean;
  onClose: () => void;
  commandType: string;
  target?: Record<string, any>;
  payload?: Record<string, any>;
  onSuccess?: (result: any) => void;
}

export const CommandDialog: React.FC<CommandDialogProps> = ({
  isOpen,
  onClose,
  commandType,
  target = {},
  payload = {},
  onSuccess,
}) => {
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<any>(null);
  const [confirmText, setConfirmText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [completed, setCompleted] = useState<any>(null);

  React.useEffect(() => {
    if (isOpen) {
      setLoading(true);
      setError(null);
      setCompleted(null);
      setConfirmText('');

      api.createCommandPreview({
        type: commandType,
        target,
        payload,
      })
        .then((res) => {
          setPreview(res);
          setLoading(false);
        })
        .catch((err) => {
          setError(err.message);
          setLoading(false);
        });
    }
  }, [isOpen, commandType]);

  if (!isOpen) return null;

  const handleConfirm = async () => {
    setLoading(true);
    setError(null);
    const commandId = `cmd-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
    try {
      const res = await api.submitCommand({
        command_id: commandId,
        type: commandType,
        target,
        payload,
        confirmation_token: preview?.preview_id,
        confirmation_text: confirmText || undefined,
        expected_state_version: preview?.current_version,
      });
      setCompleted(res);
      if (onSuccess) onSuccess(res);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const isConfirmationRequired = Boolean(preview?.required_confirmation_text);
  const canConfirm = !isConfirmationRequired || confirmText === preview?.required_confirmation_text;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 backdrop-blur-sm p-4">
      <div className="bg-white dark:bg-slate-900 rounded-xl shadow-2xl max-w-lg w-full border border-slate-200 dark:border-slate-800 overflow-hidden">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 dark:border-slate-800">
          <h3 className="font-bold text-base flex items-center gap-2 text-slate-900 dark:text-slate-100">
            <AlertTriangle className="w-5 h-5 text-amber-500" />
            Confirm Action: {commandType}
          </h3>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-6 space-y-4 text-sm text-slate-600 dark:text-slate-300">
          {loading && !preview && (
            <div className="flex items-center justify-center py-8 gap-2">
              <Loader2 className="w-5 h-5 animate-spin text-indigo-600" />
              <span>Generating action preview...</span>
            </div>
          )}

          {error && (
            <div className="p-3 bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-400 border border-rose-200 dark:border-rose-900 rounded-lg text-xs font-mono">
              {error}
            </div>
          )}

          {completed && (
            <div className="p-4 bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-900 rounded-lg text-sm flex items-center gap-3">
              <CheckCircle className="w-5 h-5" />
              <div>
                <p className="font-semibold">Command Dispatched ({completed.status})</p>
                <p className="text-xs font-mono">ID: {completed.command_id}</p>
              </div>
            </div>
          )}

          {preview && !completed && (
            <>
              <div className="bg-slate-50 dark:bg-slate-800/50 p-3 rounded-lg space-y-1 text-xs">
                <p><strong>Action:</strong> {preview.action_summary}</p>
                <p><strong>Target:</strong> {JSON.stringify(preview.target)}</p>
                <p><strong>Resource Revision (CAS):</strong> {preview.current_version}</p>
              </div>

              {isConfirmationRequired && (
                <div className="space-y-2 pt-2 border-t border-slate-100 dark:border-slate-800">
                  <p className="text-xs text-rose-600 dark:text-rose-400 font-semibold">
                    Critical operation requires verification. Please type exact phrase below to proceed:
                  </p>
                  <div className="px-2.5 py-1.5 bg-slate-100 dark:bg-slate-800 font-mono text-xs font-bold rounded select-all text-slate-800 dark:text-slate-200">
                    {preview.required_confirmation_text}
                  </div>
                  <input
                    type="text"
                    value={confirmText}
                    onChange={(e) => setConfirmText(e.target.value)}
                    placeholder={`Type "${preview.required_confirmation_text}"`}
                    className="w-full px-3 py-2 border rounded-md text-sm bg-white dark:bg-slate-900 border-slate-300 dark:border-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono"
                  />
                </div>
              )}
            </>
          )}
        </div>

        <div className="flex justify-end gap-3 px-6 py-4 bg-slate-50 dark:bg-slate-900/50 border-t border-slate-100 dark:border-slate-800">
          <button
            onClick={onClose}
            className="px-4 py-2 border border-slate-300 dark:border-slate-700 rounded-lg text-sm text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          >
            Cancel
          </button>
          {!completed && (
            <button
              onClick={handleConfirm}
              disabled={!canConfirm || loading}
              className={`px-4 py-2 rounded-lg text-sm font-semibold text-white transition-colors flex items-center gap-2 ${
                canConfirm && !loading
                  ? 'bg-indigo-600 hover:bg-indigo-700'
                  : 'bg-slate-300 dark:bg-slate-700 cursor-not-allowed'
              }`}
            >
              {loading && <Loader2 className="w-4 h-4 animate-spin" />}
              Submit Command
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
