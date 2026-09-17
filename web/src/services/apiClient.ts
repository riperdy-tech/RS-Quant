import {
  BacktestJobRequest,
  BootstrapRequest,
  CommandPreviewRequest,
  CommandSubmissionRequest,
  DatasetImportRequest,
  LoginRequest,
  PositionItem,
  SystemStatus,
  TrainingJobRequest,
} from '../types/api';

function getCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
  return match ? decodeURIComponent(match[2]) : null;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const csrfToken = getCookie('csrf_token');
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };

  if (csrfToken) {
    headers['x-csrf-token'] = csrfToken;
  }
  if (!headers['x-quantdesk-role']) {
    const storedRole = typeof window !== 'undefined' ? localStorage.getItem('quantdesk-role') : null;
    headers['x-quantdesk-role'] = storedRole || 'operator';
  }

  const response = await fetch(path, {
    ...options,
    credentials: 'include',
    headers,
  });

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status} ${response.statusText}`;
    try {
      const errJson = await response.json();
      if (errJson.error?.message) {
        errorDetail = errJson.error.message;
      } else if (errJson.detail) {
        errorDetail = typeof errJson.detail === 'string' ? errJson.detail : JSON.stringify(errJson.detail);
      }
    } catch {
      // ignore JSON parse error
    }
    throw new Error(errorDetail);
  }

  return response.json();
}

export const api = {
  // System & Health
  getLiveness: () => request<{ status: string }>('/health/live'),
  getReadiness: () => request<{ items: Array<{ id: string; name: string; status: string }>; all_passed: boolean }>('/api/v1/readiness'),
  getSystemStatus: () => request<SystemStatus>('/api/v1/system'),

  // Settings & Credentials (§15.4)
  getCredentialsStatus: () =>
    request<{ has_credentials: boolean; key_fingerprint: string | null }>(
      '/api/v1/settings/credentials/status'
    ),
  saveCredentials: (creds: { api_key: string; secret_key: string; passphrase: string }) =>
    request<{ status: string; key_fingerprint: string; message: string }>(
      '/api/v1/settings/credentials',
      {
        method: 'POST',
        body: JSON.stringify(creds),
      }
    ),
  testConnection: (creds?: { api_key: string; secret_key: string; passphrase: string } | null) =>
    request<{ success: boolean; message: string; account_level?: string; data?: any }>(
      '/api/v1/settings/test-connection',
      {
        method: 'POST',
        body: JSON.stringify(creds || { api_key: '', secret_key: '', passphrase: '' }),
      }
    ),

  // Auth
  bootstrap: (req: BootstrapRequest) => request<{ status: string; username: string; role: string; csrf_token: string }>('/api/v1/auth/bootstrap', {
    method: 'POST',
    body: JSON.stringify(req),
  }),
  login: (req: LoginRequest) => request<{ username: string; role: string; csrf_token: string }>('/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify(req),
  }),
  logout: () => request<{ status: string }>('/api/v1/auth/logout', { method: 'POST' }),
  getProfile: () => request<{ username: string; role: string; csrf_token: string }>('/api/v1/auth/me'),

  // Trading Read Models
  getPositions: () => request<PositionItem[]>('/api/v1/positions'),
  getOrders: () => request<any[]>('/api/v1/orders'),
  getFills: () => request<any[]>('/api/v1/fills'),
  getBalances: () => request<any[]>('/api/v1/balances'),
  getRiskStatus: () => request<{
    max_position_lots: number;
    current_position_lots: number;
    daily_loss_limit: string;
    current_daily_loss: string;
    peak_drawdown_pct: string;
    max_drawdown_limit_pct: string;
    breakers_tripped: string[];
    emergency_flatten_active: boolean;
    mode: string;
  }>('/api/v1/risk'),
  getStrategies: () => request<Array<{
    strategy_id: string;
    name: string;
    instrument_id: string;
    status: string;
    capital_allocation: string;
    signal: string;
    active_model_id: string | null;
  }>>('/api/v1/strategies'),
  getOrderTrace: (orderId: string) => request<{
    order_id: string;
    trace_timeline: Array<{
      step: string;
      timestamp_ns: number;
      detail: string;
    }>;
  }>(`/api/v1/orders/${encodeURIComponent(orderId)}/trace`),

  // Live Market Data (Bitget UTA Feed)
  getMarketTicker: (symbol: string = 'BTCUSDT') =>
    request<any>(`/api/v1/market/ticker?symbol=${encodeURIComponent(symbol)}`),
  getMarketDepth: (symbol: string = 'BTCUSDT') =>
    request<{
      symbol: string;
      bids: Array<[string, string]>;
      asks: Array<[string, string]>;
      timestamp_ns: number;
    }>(`/api/v1/market/depth?symbol=${encodeURIComponent(symbol)}`),
  getMarketTrades: (symbol: string = 'BTCUSDT') =>
    request<
      Array<{
        trade_id: string;
        symbol: string;
        price: string;
        size: string;
        side: string;
        ts_ms: string;
        time_ns: number;
      }>
    >(`/api/v1/market/trades?symbol=${encodeURIComponent(symbol)}`),
  getMarketFeedStatus: () =>
    request<{
      is_running: boolean;
      is_connected: boolean;
      venue: string;
      symbols: string[];
      source: string;
    }>('/api/v1/market/status'),

  // Commands
  createCommandPreview: (req: CommandPreviewRequest) => request<{
    preview_id: string;
    type: string;
    target: Record<string, any>;
    current_version: string;
    required_confirmation_text: string | null;
    expires_at_ns: number;
    action_summary: string;
  }>('/api/v1/command-previews', {
    method: 'POST',
    body: JSON.stringify(req),
  }),
  submitCommand: (req: CommandSubmissionRequest) => request<{
    command_id: string;
    status: string;
    status_url: string;
  }>('/api/v1/commands', {
    method: 'POST',
    body: JSON.stringify(req),
  }),
  getCommandStatus: (commandId: string) => request<any>(`/api/v1/commands/${encodeURIComponent(commandId)}`),

  // Research
  getDatasets: () => request<any[]>('/api/v1/datasets'),
  importDataset: (req: DatasetImportRequest) => request<{ job_id: string; status: string }>('/api/v1/datasets/imports', {
    method: 'POST',
    body: JSON.stringify(req),
  }),
  getBacktests: () => request<any[]>('/api/v1/backtests'),
  runBacktest: (req: BacktestJobRequest) => request<{ job_id: string; status: string }>('/api/v1/backtests', {
    method: 'POST',
    body: JSON.stringify(req),
  }),
  getModels: () => request<any[]>('/api/v1/models'),
  trainModel: (req: TrainingJobRequest) => request<{ job_id: string; status: string }>('/api/v1/models/training', {
    method: 'POST',
    body: JSON.stringify(req),
  }),
  getJobs: (jobType?: string) => request<any[]>(`/api/v1/jobs${jobType ? `?job_type=${encodeURIComponent(jobType)}` : ''}`),
  getJob: (jobId: string) => request<any>(`/api/v1/jobs/${encodeURIComponent(jobId)}`),
  cancelJob: (jobId: string) => request<{ job_id: string; status: string }>(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
  }),
};
