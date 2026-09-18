/* Auto-generated TypeScript contracts from QuantDesk OpenAPI schema (§15) */
/* Do not edit directly; regenerate with scripts/generate_api_types.py */

export interface BacktestJobRequest {
  dataset_id: string;
  initial_cash?: number;
  parameters?: Record<string, any>;
  strategy_id: string;
}

export interface BootstrapRequest {
  admin_password: string;
  bootstrap_token: string;
}

export interface CommandPreviewRequest {
  payload?: Record<string, any>;
  target?: Record<string, any>;
  type: string;
}

export interface CommandSubmissionRequest {
  command_id: string;
  confirmation_text?: any;
  confirmation_token?: any;
  expected_state_version?: any;
  payload?: Record<string, any>;
  target?: Record<string, any>;
  type: string;
}

export interface DatasetImportRequest {
  instrument_id: string;
  source_path: string;
  timeframe?: string;
}

export interface HTTPValidationError {
  detail?: ValidationError[];
}

export interface LoginRequest {
  password: string;
  username: string;
}

export interface TrainingJobRequest {
  algorithm?: string;
  dataset_id: string;
  parameters?: Record<string, any>;
}

export interface ValidationError {
  ctx?: Record<string, any>;
  input?: any;
  loc: any[];
  msg: string;
  type: string;
}

export type CommandStatus = 'QUEUED' | 'VALIDATING' | 'APPLIED' | 'REJECTED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'UNKNOWN';

export interface SystemStatus {
  mode: 'DEMO' | 'LIVE';
  live_enabled: boolean;
  venue: string;
  account_alias: string;
  engine_state: string;
  active_strategies: string[];
  unresolved_incidents: number;
  service_health: string;
  emergency_halted?: boolean;
  strategies_paused?: boolean;
  role?: string;
}

export interface PositionItem {
  strategy_id?: string;
  instrument_id: string;
  lots: number;
  units?: string;
  side: 'BUY' | 'SELL';

  entry_price: string;
  mark_price: string;
  unrealized_pnl: string;
  realized_pnl: string;
  margin_equity: string;
  initial_margin: string;
  maintenance_margin: string;
  currency: string;
  timestamp_ns: number;
}

export interface SSEEventEnvelope {
  id: string;
  topic: string;
  resource_version: string;
  projection_watermark: number;
  update_time_ns: number;
  payload: Record<string, any>;
}

export interface ReflexEvent {
  timestamp_ns: number;
  type: string;
  instrument_id: string;
  detail: string;
  action: string;
}

export interface InstrumentReflexParams {
  maker_only_mode: boolean;
  entry_cooldown_s: number;
  atr_target_multiplier: number;
  depth5_imbalance_threshold: number;
  spread_shock_active: boolean;
  ml_gate_enabled: boolean;
}

export interface ReflexStatus {
  symbol?: string;
  auto_tuner_enabled: boolean;
  maker_only_mode: boolean;
  current_atr_multiplier: number;
  entry_cooldown_s: number;
  depth5_threshold: number;
  circuit_breaker_pct: number;
  circuit_breaker_tripped: boolean;
  total_reflex_actions: number;
  spread_shock_active: boolean;
  instruments?: Record<string, InstrumentReflexParams>;
  recent_events: ReflexEvent[];
}

export interface MacroReport {
  regime: 'BULLISH_SIGNAL' | 'BEARISH_WARNING' | 'NEUTRAL_CHOP';
  warn_bearish: boolean;
  warn_bullish: boolean;
  warning_strength: number;
  strength_level: string;
  fed_snapshot: {
    walcl: number;
    tga: number;
    rrp: number;
    net_liquidity: number;
    net_liquidity_sma20: number;
    trend_direction: number;
    pct_change: number;
    is_bullish: boolean;
    z_score: number;
  } | null;
  usdt_snapshot: {
    usdt_dominance_pct: number;
    ema5: number;
    z_score: number;
    slope: number;
    trend_up: boolean;
  } | null;
  timestamp_ns: number;
}

export interface WhalePositioning {
  oi: number;
  lsr: number;
  long_contracts: number;
  short_contracts: number;
  d_long: number;
  d_short: number;
  net_flow_raw: number;
  net_flow_zscore: number;
  net_flow_direction: number;
  ratio_ln: number;
  macd_line: number;
  macd_signal: number;
  macd_hist: number;
  is_spike: boolean;
}

export interface MacroRadarData {
  macro_report: MacroReport | null;
  whale_positioning: Record<string, WhalePositioning>;
  timestamp_ns: number;
}

export interface AgenticStatus {
  instrument_id: string;
  strategy_id: string;
  current_bias: string;
  current_regime: string;
  tactical_state: string;
  dynamic_parameters: {
    indicator_weights: Record<string, number>;
    atr_target_mult: string;
    depth5_threshold: number;
    entry_cooldown_s: number;
    volatility_hurdle_bps: number;
    conviction_threshold: number;
    consecutive_losses: number;
  };
  rolling_ic: Record<string, number>;
  memory_summary: {
    total_episodes: number;
    win_rate_pct: string;
    total_net_pnl: string;
    recent_attributions: string[];
  };
  timestamp_ns: number;
}


