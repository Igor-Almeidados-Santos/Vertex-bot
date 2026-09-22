export interface BotSettings {
  execution_mode: "PAPER" | "LIVE";
  trading_strategy_mode: "DUAL" | "SCALP_ONLY" | "SWING_ONLY";
  paper_buy_amount_usd: number;
  max_concurrent_positions: number;
  wallet_balance_usd: number;
  paper_initial_wallet_usd: number;
  min_trade_amount_usd: number;
  max_token_age_hours: number;
  max_slippage_pct: number;
  max_top10_holders_pct: number;
  min_liquidity_usd: number;
  live_buy_amount_usd?: number;
  live_max_concurrent_positions?: number;
  live_max_slippage_pct?: number;
  live_jito_tip_lamports?: number;

  // Scalp
  scalp_max_hold_minutes: number;
  scalp_target_gain_pct: number;
  trailing_stop_drop_pct: number;
  emergency_stop_loss_pct: number;
  break_even_gain_pct: number;

  // Swing Ratchet
  swing_max_hold_hours: number;
  swing_target_gain_pct: number;
  swing_max_hourly_drop_pct: number;
  swing_initial_stop_loss_pct: number;
  swing_trailing_drop_pct: number;
  swing_tier1_mult: number;
  swing_tier2_mult: number;
  swing_tier3_mult: number;
  swing_tier4_mult: number;
  swing_tier5_mult: number;

  // Reentrada
  reentry_trailing_cooloff_min: number;
  reentry_stoploss_cooloff_min: number;
  reentry_min_bounce_pct: number;

  // Filtros de Mercado
  min_volume_1h_usd: number;
  min_buy_ratio_5m_pct: number;
  min_price_change_5m_pct: number;
  min_liquidity_swing_usd: number;
  min_token_age_scalp_min?: number;
  min_token_age_swing_hours?: number;
  max_token_age_swing_hours?: number;
}

export interface WalletInfo {
  chain: string;
  name?: string;
  address: string;
  is_connected: boolean;
  balance_native?: number;
  native_symbol?: string;
  balance_usd?: number;
}

export interface SlotQuotas {
  max_positions: number;
  priority_slots_max: number;
  priority_slots_used: number;
  new_tokens_slots_max: number;
  new_tokens_slots_used: number;
  total_active: number;
}

export interface BotStatusData {
  is_running: boolean;
  is_paused: boolean;
  wallet_balance_usd: number;
  initial_wallet_usd: number;
  active_positions_count: number;
  waiting_tokens_count: number;
  settings: BotSettings;
  mode?: string;
  modes?: {
    paper?: { running: boolean; paused: boolean };
    live?: { running: boolean; paused: boolean };
  };
  has_wallet?: boolean;
  has_connected_wallet?: boolean;
  wallets?: WalletInfo[];
  slot_quotas?: SlotQuotas;
  priority_slots_max?: number;
  priority_slots_used?: number;
  new_tokens_slots_max?: number;
  new_tokens_slots_used?: number;
}

export interface PnLSummary {
  realized_pnl_usd: number;
  unrealized_pnl_usd: number;
  total_pnl_usd: number;
  pnl_pct: number;
  current_cash_usd: number;
  equity_usd: number;
  initial_wallet_usd: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate_pct: number;
  open_positions_count: number;
}

export interface ScannerSummary {
  total_scanned: number;
  total_approved?: number;
  total_rejected?: number;
  approved?: number;
  rejected?: number;
  all_time_cataloged?: number;
  all_time_approved?: number;
  all_time_rejected?: number;
  session_scanned?: number;
  session_approved?: number;
  session_rejected?: number;
  approval_rate_pct: number;
  rejection_reasons?: Record<string, number>;
}

export interface SummaryData {
  pnl: PnLSummary;
  scanner: ScannerSummary;
  session_mode: string;
  waiting_tokens_count: number;
  slot_quotas?: SlotQuotas;
}

export interface PositionItem {
  id: number;
  token_address: string;
  address?: string;
  token_symbol?: string;
  symbol?: string;
  name?: string;
  chain?: string;
  strategy_type: "SCALP" | "SWING" | string;
  status: "OPEN" | "CLOSED" | "PARTIALLY_CLOSED" | "STOPPED" | string;
  mode?: string;
  entry_price: number;
  current_price: number;
  token_amount?: number;
  initial_token_amount?: number;
  remaining_token_amount?: number;
  allocated_capital_usd: number;
  unrealized_pnl_usd?: number;
  unrealized_pnl_pct?: number;
  highest_price_seen: number;
  trailing_stop_price: number;
  stop_loss_price?: number;
  ratchet_floor_price?: number;
  active_tier?: number;
  ratchet_tier?: number;
  break_even_triggered?: boolean;
  opened_at: string;
  closed_at?: string;
  close_reason?: string;
  exit_price?: number;
  realized_pnl_usd?: number;
}

export interface WaitingTokenItem {
  address: string;
  token_address?: string;
  symbol?: string;
  token_symbol?: string;
  name?: string;
  chain?: string;
  dex?: string;
  eligible_strategy?: "DUAL" | "SCALP" | "SWING" | string;
  initial_liquidity_usd?: number;
  liquidity_usd?: number;
  age_hours?: number;
  waiting_reason?: string;
  reason_pending?: "AGUARDANDO_SLOT" | "AGUARDANDO_SALDO" | string;
  enqueued_at?: string;
  added_at?: string;
  last_price?: number;
  expires_at?: string;
}

export interface OrderItem {
  id: number;
  position_id: number;
  token_address?: string;
  token_symbol?: string;
  symbol?: string;
  name?: string;
  chain?: string;
  order_type: "BUY" | "SELL" | string;
  price: number;
  amount_usd?: number;
  total_usd?: number;
  tokens_amount?: number;
  amount?: number;
  tx_hash?: string;
  mode?: string;
  timestamp?: string;
  executed_at?: string;
  reason?: string;
  notes?: string;
}

export interface CatalogTokenItem {
  address: string;
  symbol: string;
  name?: string;
  chain?: string;
  dex: string;
  pool_address?: string;
  initial_liquidity_usd: number;
  current_liquidity_usd?: number;
  status?: "APROVADO" | "REJEITADO" | "ANALISANDO" | "APPROVED" | "REJECTED" | "PENDING" | string;
  security_status?: string;
  rejection_reason?: string;
  score?: number;
  security_score?: number;
  cataloged_at?: string;
  detection_timestamp?: string;
}

export interface SystemLogItem {
  id: string;
  timestamp: string;
  level: "INFO" | "WARN" | "ERROR" | "DEBUG";
  module: string;
  message: string;
}

export interface PriorityToken {
  address: string;
  symbol: string;
  name: string;
  chain: string;
  dex: string;
  tier: "CONSOLIDATED" | "EMERGING" | string;
  initial_liquidity_usd: number;
  current_liquidity_usd: number;
  last_price: number;
  highest_price_seen: number;
  total_trades_count: number;
  successful_trades_count: number;
  total_realized_pnl_usd: number;
  is_active_priority: boolean;
  is_alive: boolean;
  origin_mode?: "LIVE" | "PAPER" | "BOTH" | string;
  added_at: string;
  last_traded_at: string;
  last_evaluated_at: string;
  raw_event?: Record<string, unknown>;
}

export interface PurgePriorityResult {
  mode: string;
  purged_count: number;
  remaining_count: number;
  purged: Array<{
    token_address: string;
    symbol?: string;
    name?: string;
    reason: string;
  }>;
}


