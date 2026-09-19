import {
  AgenticStatus,
  BacktestJobRequest,
  BootstrapRequest,
  CommandPreviewRequest,
  CommandSubmissionRequest,
  DatasetImportRequest,
  LoginRequest,
  MacroRadarData,
  MacroReport,
  PositionItem,
  ReflexEvent,
  ReflexStatus,
  SystemStatus,
  TrainingJobRequest,
  WhalePositioning,
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
  getCredentialsStatus: (venue?: string) =>
    request<{
      has_credentials: boolean;
      key_fingerprint: string | null;
      active_venue?: string;
      mexc?: { has_credentials: boolean; key_fingerprint: string | null };
      bitget?: { has_credentials: boolean; key_fingerprint: string | null };
    }>(
      `/api/v1/settings/credentials/status${venue ? `?venue=${venue}` : ''}`
    ),
  saveCredentials: (creds: { venue?: string; api_key: string; secret_key: string; passphrase?: string }) =>
    request<{ status: string; key_fingerprint: string; message: string; venue?: string }>(
      '/api/v1/settings/credentials',
      {
        method: 'POST',
        body: JSON.stringify(creds),
      }
    ),
  testConnection: (creds?: { venue?: string; api_key: string; secret_key: string; passphrase?: string } | null) =>
    request<{
      success: boolean;
      message: string;
      account_level?: string;
      total_equity?: string;
      available_usdt?: string;
      data?: any;
    }>(
      '/api/v1/settings/test-connection',
      {
        method: 'POST',
        body: JSON.stringify(creds || {}),
      }
    ),
  getTradingCapitalConfig: () =>
    request<{
      capital_usdt: string;
      leverage: string;
      margin_per_leg: string;
      notional_per_leg: string;
    }>('/api/v1/settings/trading-capital'),
  saveTradingCapitalConfig: (payload: { capital_usdt: number; leverage: number }) =>
    request<{
      capital_usdt: string;
      leverage: string;
      margin_per_leg: string;
      notional_per_leg: string;
    }>('/api/v1/settings/trading-capital', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

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
  getPerformance: () =>
    request<{
      total_equity: string;
      initial_equity: string;
      available_cash: string;
      locked_margin: string;
      total_profit: string;
      total_return_pct: string;
      realized_pnl: string;
      unrealized_pnl: string;
      total_trades: number;
      win_rate_pct: string;
      instruments: Record<
        string,
        {
          realized_pnl: string;
          unrealized_pnl: string;
          total_profit: string;
          trades_count: number;
          win_rate_pct: string;
        }
      >;
    }>('/api/v1/performance'),
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
  getStrategyTelemetry: (symbol: string = 'BTCUSDT') =>
    request<{
      symbol: string;
      depth5_imbalance: number | null;
      depth20_imbalance: number | null;
      microprice: number | null;
      mid: number | null;
      spread_bps: number | null;
      l1_ofi: number | null;
      volume_1s_signed: number | null;
      cvd: number | null;
      atr14: number | null;
      timestamp_ns: number;
    }>(`/api/v1/strategies/telemetry?symbol=${encodeURIComponent(symbol)}`),
  getAgenticStatus: (symbol: string = 'BTCUSDT') =>
    request<AgenticStatus>(`/api/v1/trading/agentic-status?symbol=${encodeURIComponent(symbol)}`),
  getStrategyDecisions: () =>

    request<
      Array<{
        decision_id: string;
        timestamp_ns: number;
        strategy_id: string;
        instrument_id: string;
        action: string;
        reason: string;
        status: string;
      }>
    >('/api/v1/strategies/decisions'),
  toggleStrategy: (strategyId: string, running: boolean) =>
    request<{ strategy_id: string; status: string }>(
      `/api/v1/strategies/${encodeURIComponent(strategyId)}/toggle?running=${running}`,
      { method: 'POST' }
    ),
  flattenPosition: (symbol: string = 'BTCUSDT') =>
    request<{ symbol: string; status: string }>(
      `/api/v1/trading/flatten?symbol=${encodeURIComponent(symbol)}`,
      { method: 'POST' }
    ),
  pauseAllTrading: () =>
    request<{ status: string; engine_state: string }>('/api/v1/trading/pause', {
      method: 'POST',
    }),
  resumeAllTrading: () =>
    request<{ status: string; engine_state: string }>('/api/v1/trading/resume', {
      method: 'POST',
    }),
  emergencyStopTrading: () =>
    request<{ status: string; engine_state: string; positions: string }>('/api/v1/trading/emergency-stop', {
      method: 'POST',
    }),
  triggerDiagnosticSignal: (strategyId: string = 'unified-btc', symbol: string = 'BTCUSDT', side: string = 'BUY') =>
    request<{ status: string; strategy_id: string; symbol: string; side: string }>(
      `/api/v1/strategies/trigger-diagnostic?strategy_id=${encodeURIComponent(strategyId)}&symbol=${encodeURIComponent(symbol)}&side=${encodeURIComponent(side)}`,
      { method: 'POST' }
    ),
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
  cancelJob: (jobId: string) => request<{ job_id: string; status: string }>(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
  }),

  // AI Fault Review & Strategy Discovery
  runAIFaultReview: () =>
    request<{
      session_duration_approx: string;
      total_actions: number;
      total_round_trips: number;
      gross_market_pnl_usd: number;
      total_fees_paid_usd: number;
      net_session_pnl_usd: number;
      fee_drag_ratio_pct: number;
      primary_root_cause: string;
      strategy_breakdown: Array<{
        strategy_id: string;
        instrument_id: string;
        total_actions: number;
        round_trips: number;
        gross_pnl_usd: number;
        fees_paid_usd: number;
        net_pnl_usd: number;
        win_rate_pct: number;
        avg_fee_per_trade: number;
        avg_net_pnl_per_trade: number;
        fee_drag_pct: number;
      }>;
      learned_rules: Array<{
        rule_id: string;
        title: string;
        category: string;
        diagnosis: string;
        solution: string;
        recommended_parameters: Record<string, any>;
        projected_impact: string;
      }>;
      timestamp_ns: number;
      ai_summary: string;
    }>('/api/v1/research/ai-fault-review', { method: 'POST' }),

  getLatestAIFaultReview: () =>
    request<{
      session_duration_approx: string;
      total_actions: number;
      total_round_trips: number;
      gross_market_pnl_usd: number;
      total_fees_paid_usd: number;
      net_session_pnl_usd: number;
      fee_drag_ratio_pct: number;
      primary_root_cause: string;
      strategy_breakdown: Array<any>;
      learned_rules: Array<any>;
      timestamp_ns: number;
      ai_summary: string;
    }>('/api/v1/research/ai-fault-review/latest'),

  applyAIStrategy: (config?: {
    symbol?: string;
    maker_only_mode?: boolean;
    entry_cooldown_s?: number;
    max_session_drawdown_pct?: number;
    atr_target_multiplier?: number;
    depth5_imbalance_threshold?: number;
    ml_gate_enabled?: boolean;
    reset_capital?: boolean;
  }) =>
    request<{
      status: string;
      maker_only_mode: boolean;
      entry_cooldown_s: number;
      max_session_drawdown_pct: number;
      atr_target_multiplier: number;
      ml_gate_enabled: boolean;
      circuit_breaker_tripped: boolean;
      equity: string;
      instruments?: Record<string, any>;
    }>('/api/v1/research/apply-ai-strategy', {
      method: 'POST',
      body: JSON.stringify(config || {}),
    }),

  // Event-Driven AI Reflex Engine (§15.2)
  getReflexStatus: (symbol: string = 'BTCUSDT') =>
    request<ReflexStatus>(`/api/v1/trading/reflex-status?symbol=${encodeURIComponent(symbol)}`),
  toggleReflexTuner: (enabled: boolean, symbol: string = 'all') =>
    request<ReflexStatus>(
      `/api/v1/trading/reflex-toggle?enabled=${enabled}&symbol=${encodeURIComponent(symbol)}`,
      {
        method: 'POST',
      }
    ),
  triggerReflexAudit: (actionType: string = 'MICRO_AUDIT', symbol: string = 'all') =>
    request<ReflexStatus>(
      `/api/v1/trading/reflex-trigger?action_type=${encodeURIComponent(actionType)}&symbol=${encodeURIComponent(symbol)}`,
      {
        method: 'POST',
      }
    ),

  // Macro Net Liquidity, Tether Dominance & Whale Positioning Radar
  getMacroRadar: () => request<MacroRadarData>('/api/v1/trading/macro-radar'),
  refreshMacroRadar: (params?: { walcl?: number; tga?: number; rrp?: number; usdt_d?: number }) =>
    request<MacroRadarData>(
      `/api/v1/trading/macro-radar/refresh${
        params?.walcl ? `?walcl=${params.walcl}` : ''
      }${params?.usdt_d ? `&usdt_d=${params.usdt_d}` : ''}`,
      {
        method: 'POST',
      }
    ),
};

export type { MacroRadarData, MacroReport, ReflexEvent, ReflexStatus, WhalePositioning };

