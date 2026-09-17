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
  instrument_id: string;
  lots: number;
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
