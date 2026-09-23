"""
Servidor Web Assíncrono do Dashboard do Vertex-bot.
Construído com aiohttp.web para leitura concorrente em SQLite (WAL mode).
"""

import asyncio
import json
import math
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp.typedefs import Handler

from src.config.settings import update_env_file
from src.database.connection import DatabaseManager
from src.database.repository import OrdersRepository, PositionsRepository, TokensRepository
from src.utils.logger import setup_logger

logger = setup_logger("vertex.dashboard")

STATIC_DIR = Path(__file__).parent / "static"
STATUS_FILE = Path("data/bot_status.json")
CONTROL_FILE = Path("data/bot_control.json")
CONFIG_FILE = Path("data/bot_config.json")
SESSION_FILE = Path("data/paper_session.json")


def get_status_file(mode: str = "paper") -> Path:
    """Retorna caminho de status isolado por modo com fallback seguro."""
    if STATUS_FILE != Path("data/bot_status.json"):
        return STATUS_FILE
    m = mode.strip().lower()
    p = Path(f"data/{m}/bot_status.json")
    if not p.exists() and m == "paper":
        if STATUS_FILE.exists():
            return STATUS_FILE
    return p


def get_control_file(mode: str = "paper") -> Path:
    """Retorna caminho de controle IPC isolado por modo."""
    if CONTROL_FILE != Path("data/bot_control.json"):
        return CONTROL_FILE
    m = mode.strip().lower()
    return Path(f"data/{m}/bot_control.json")


def get_config_file(mode: str = "paper") -> Path:
    """Retorna caminho de configuração persistida isolado por modo com fallback seguro."""
    if CONFIG_FILE != Path("data/bot_config.json"):
        return CONFIG_FILE
    m = mode.strip().lower()
    p = Path(f"data/{m}/bot_config.json")
    if not p.exists() and m == "paper":
        if CONFIG_FILE.exists():
            return CONFIG_FILE
    return p


def get_session_file(mode: str = "paper") -> Path:
    """Retorna caminho do arquivo de sessão com fallback seguro."""
    if SESSION_FILE != Path("data/paper_session.json"):
        return SESSION_FILE
    m = mode.strip().lower()
    p = Path(f"data/{m}/paper_session.json")
    if not p.exists() and m == "paper":
        if SESSION_FILE.exists():
            return SESSION_FILE
    return p


def _safe_float(val: Any, default: float) -> float:
    """Converte valor para float de forma defensiva contra None, NaN ou tipos inválidos."""
    if val is None:
        return default
    try:
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else f
    except (ValueError, TypeError):
        return default


def _safe_int(val: Any, default: int) -> int:
    """Converte valor para int de forma defensiva contra None ou tipos inválidos."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _open_runtime_log_file() -> Any:
    log_dir = Path("data")
    log_dir.mkdir(parents=True, exist_ok=True)
    return open(log_dir / "bot_runtime.log", "a", encoding="utf-8")


class DashboardServer:
    """Gerenciador de rotas e ciclo de vida da API e interface do Dashboard."""

    _bot_subprocess: asyncio.subprocess.Process | None = None

    def __init__(self, db: DatabaseManager, orchestrator: Any | None = None) -> None:
        self.db: DatabaseManager = db
        self.orchestrator: Any | None = orchestrator
        self.tokens_repo: TokensRepository = TokensRepository(db)
        self.positions_repo: PositionsRepository = PositionsRepository(db)
        self.orders_repo: OrdersRepository = OrdersRepository(db)
        self._active_ws_clients: set[web.WebSocketResponse] = set()
        self._fallback_live_engine: Any | None = None

    def _get_live_engine(self) -> Any:
        """Obtém o LiveExecutionEngine do orquestrador ou inicializa instância fallback segura."""
        if self.orchestrator and hasattr(self.orchestrator, "live_engine"):
            return self.orchestrator.live_engine
        if self._fallback_live_engine is None:
            from src.config.settings import get_settings
            from src.engine.live import LiveExecutionEngine
            settings = get_settings()
            evm_rpcs = {
                "base": settings.BASE_RPC_URL,
                "arbitrum": settings.ARBITRUM_RPC_URL,
                "bsc": settings.BSC_RPC_URL,
                "polygon": settings.POLYGON_RPC_URL,
                "ethereum": settings.ETHEREUM_RPC_URL,
                "avalanche": settings.AVALANCHE_RPC_URL,
                "optimism": settings.OPTIMISM_RPC_URL,
                "blast": settings.BLAST_RPC_URL,
            }
            self._fallback_live_engine = LiveExecutionEngine(
                positions_repo=self.positions_repo,
                orders_repo=self.orders_repo,
                tokens_repo=self.tokens_repo,
                solana_rpc_url=settings.PRIMARY_RPC_HTTP_URL,
                solana_private_key_base58=settings.SOLANA_PRIVATE_KEY_BASE58 or settings.WALLET_PRIVATE_KEY_BASE58,
                evm_rpc_urls=evm_rpcs,
                evm_private_key=settings.EVM_PRIVATE_KEY or settings.EVM_WALLET_PRIVATE_KEY,
                confirm_live_trading=settings.CONFIRM_LIVE_TRADING,
                evm_min_gas_reserve_usd=settings.EVM_MIN_GAS_RESERVE_USD,
            )
        return self._fallback_live_engine


    def _get_session_start(self, mode: str = "paper") -> datetime | None:
        """Lê o timestamp de início da sessão simulada para zeragem visual."""
        try:
            sess_file = get_session_file(mode)
            if sess_file.exists():
                data = json.loads(sess_file.read_text(encoding="utf-8"))
                start_str = data.get("session_start")
                if start_str and isinstance(start_str, str):
                    return datetime.fromisoformat(start_str)
        except Exception:
            pass
        return None

    def _write_ipc_command(
        self,
        command: str,
        payload: dict[str, Any] | None = None,
        mode: str = "paper",
    ) -> None:
        """Grava comando atômico de controle para o bot em processo independente no modo alvo."""
        try:
            ctrl_file = get_control_file(mode)
            ctrl_file.parent.mkdir(parents=True, exist_ok=True)
            cmd_data = {
                "command": command,
                "command_id": str(uuid.uuid4()),
                "mode": mode.upper(),
                "payload": payload or {},
                "timestamp": datetime.now(UTC).timestamp(),
            }
            tmp_path = ctrl_file.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(cmd_data, indent=2), encoding="utf-8")
            tmp_path.replace(ctrl_file)

            if mode.lower() == "paper":
                try:
                    CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True)
                    legacy_tmp = CONTROL_FILE.with_suffix(".tmp")
                    legacy_tmp.write_text(json.dumps(cmd_data, indent=2), encoding="utf-8")
                    legacy_tmp.replace(CONTROL_FILE)
                except Exception:
                    pass
            logger.info("📡 [IPC ENVIADO (%s)] Comando '%s' registrado para o bot.", mode.upper(), command)
        except Exception as exc:
            logger.error("Falha ao gravar comando IPC '%s' (%s): %s", command, mode, exc)

    def _save_config_file(self, cfg: dict[str, Any], mode: str = "paper") -> None:
        """Salva arquivo de configuração persistida em disco para o modo."""
        try:
            cfg_file = get_config_file(mode)
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            if mode.lower() == "paper":
                try:
                    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
                    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Falha ao gravar %s: %s", cfg_file, exc)

    def _reset_paper_session_file(self, initial_balance: float | None = None, mode: str = "paper") -> None:
        """Reinicia timestamp da sessão simulada."""
        try:
            sess_file = get_session_file(mode)
            sess_file.parent.mkdir(parents=True, exist_ok=True)
            bal = initial_balance if initial_balance is not None else 10.0
            payload = {
                "session_start": datetime.now(UTC).isoformat(),
                "mode": mode.upper(),
                "initial_wallet_usd": bal,
            }
            sess_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            if mode.lower() == "paper":
                try:
                    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                    SESSION_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Falha ao gravar paper_session.json: %s", exc)

    def _update_paper_session_initial_wallet(self, initial_balance: float, mode: str = "paper") -> None:
        """Atualiza a banca inicial em paper_session.json preservando o timestamp de início."""
        try:
            sess_file = get_session_file(mode)
            sess_file.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {}
            if sess_file.exists():
                try:
                    data = json.loads(sess_file.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data["initial_wallet_usd"] = initial_balance
            if "session_start" not in data:
                data["session_start"] = datetime.now(UTC).isoformat()
            data["mode"] = mode.upper()
            sess_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            if mode.lower() == "paper":
                try:
                    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                    SESSION_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Falha ao atualizar banca inicial em data/paper_session.json: %s", exc)

    def _get_bot_status_from_orchestrator(self, mode: str = "paper") -> tuple[bool, bool, float, int]:
        """Obtém status diretamente da memória quando executando acoplado."""
        if not self.orchestrator:
            return False, False, 0.0, 0
        m = mode.strip().lower()
        if m == "live":
            if hasattr(self.orchestrator, "live_enabled"):
                is_running = bool(getattr(self.orchestrator, "is_running", True) and self.orchestrator.live_enabled)
                is_paused = bool(getattr(self.orchestrator, "live_paused", False))
            else:
                is_running = False
                is_paused = False
            wallet_usd = 0.0
            engine = getattr(self.orchestrator, "live_engine", None)
            if engine:
                wallet_usd = float(getattr(engine, "_last_known_balance_usd", 0.0))
            tracker = getattr(self.orchestrator, "live_tracker", None)
            active_positions = getattr(tracker, "active_positions", None)
            active_positions_count = len(active_positions) if isinstance(active_positions, dict) else 0
            return is_running, is_paused, wallet_usd, active_positions_count
        else:
            if hasattr(self.orchestrator, "paper_enabled"):
                is_running = bool(getattr(self.orchestrator, "is_running", True) and self.orchestrator.paper_enabled)
                is_paused = bool(getattr(self.orchestrator, "paper_paused", False))
            else:
                is_running = bool(getattr(self.orchestrator, "is_running", True))
                is_paused = bool(getattr(self.orchestrator, "is_paused", False))
            wallet_usd = 0.0
            engine = getattr(self.orchestrator, "paper_engine", getattr(self.orchestrator, "execution_engine", None))
            if engine and hasattr(engine, "balance_usd"):
                try:
                    wallet_usd = float(engine.balance_usd)
                except (ValueError, TypeError):
                    wallet_usd = 0.0
            elif hasattr(self.orchestrator, "settings"):
                wallet_usd = float(getattr(self.orchestrator.settings, "PAPER_INITIAL_WALLET_USD", 0.0))
            tracker = getattr(self.orchestrator, "paper_tracker", getattr(self.orchestrator, "position_tracker", None))
            active_positions = getattr(tracker, "active_positions", None)
            active_positions_count = len(active_positions) if isinstance(active_positions, dict) else 0
            return is_running, is_paused, wallet_usd, active_positions_count

    def _get_bot_status(self, mode: str = "paper") -> tuple[bool, bool, float, int]:
        """Retorna tupla (is_running, is_paused, wallet_usd, active_positions_count) filtrada por modo."""
        m = mode.strip().lower()
        if self.orchestrator and getattr(self.orchestrator, "execution_mode", "PAPER").lower() == m:
            return self._get_bot_status_from_orchestrator()
        if self.orchestrator:
            return self._get_bot_status_from_orchestrator(mode=m)

        fallback_wallet = self._get_decoupled_initial_wallet(mode=m)
        status_file = get_status_file(m)
        if status_file.exists():
            try:
                data = json.loads(status_file.read_text(encoding="utf-8"))
                ts = float(data.get("timestamp", 0.0))
                now_ts = datetime.now(UTC).timestamp()
                wallet_val = _safe_float(data.get("wallet_balance_usd", fallback_wallet), fallback_wallet)
                active_count = int(data.get("active_positions_count", 0))
                is_running = bool(data.get("is_running", False))
                is_paused = bool(data.get("is_paused", False))
                if (now_ts - ts) <= 6.0 and is_running:
                    return (
                        True,
                        is_paused,
                        wallet_val,
                        active_count,
                    )
                return False, is_paused, wallet_val, active_count
            except Exception as exc:
                logger.debug("Erro ao ler %s: %s", status_file, exc)

        return False, False, 0.0, 0

    def _get_waiting_tokens(self, mode: str = "paper") -> list[dict[str, Any]]:
        """Retorna lista de tokens aprovados aguardando liberação de slots ou em maturação na incubadora."""
        m = mode.strip().lower()
        raw_list: list[dict[str, Any]] = []
        if self.orchestrator and getattr(self.orchestrator, "execution_mode", "PAPER").lower() == m:
            waiting = getattr(self.orchestrator, "waiting_tokens", None)
            if isinstance(waiting, dict):
                raw_list.extend(list(waiting.values()))
            if hasattr(self.orchestrator, "get_incubator_tokens"):
                try:
                    inc_tokens = self.orchestrator.get_incubator_tokens()
                    if isinstance(inc_tokens, list):
                        raw_list.extend(inc_tokens)
                except Exception as exc:
                    logger.debug("Erro ao coletar tokens da incubadora via orchestrator: %s", exc)
        else:
            w_path = Path(f"data/{m}/waiting_tokens.json")
            if not w_path.exists() and m == "paper":
                w_path = Path("data/waiting_tokens.json")
            if w_path.exists():
                try:
                    raw = json.loads(w_path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        raw_list.extend(list(raw.values()))
                except Exception:
                    pass
            inc_path = Path(f"data/{m}/incubator_tokens.json")
            if not inc_path.exists() and m == "paper":
                inc_path = Path("data/incubator_tokens.json")
            if inc_path.exists():
                try:
                    inc_raw = json.loads(inc_path.read_text(encoding="utf-8"))
                    if isinstance(inc_raw, list):
                        raw_list.extend(inc_raw)
                    elif isinstance(inc_raw, dict):
                        raw_list.extend(list(inc_raw.values()))
                except Exception:
                    pass

        # Fallback de leitura do arquivo data/incubator_tokens.json se orchestrator não retornou incubados
        if self.orchestrator is None and Path("data/incubator_tokens.json").exists() and not any("MATURA" in str(x.get("waiting_reason", "")) for x in raw_list) and m == "paper":
            try:
                inc_raw = json.loads(Path("data/incubator_tokens.json").read_text(encoding="utf-8"))
                if isinstance(inc_raw, list):
                    raw_list.extend(inc_raw)
                elif isinstance(inc_raw, dict):
                    raw_list.extend(list(inc_raw.values()))
            except Exception:
                pass

        normalized_map: dict[str, dict[str, Any]] = {}
        for t in raw_list:
            item = dict(t)
            addr = str(item.get("address") or item.get("token_address") or "").strip()
            if not addr:
                continue
            sym = str(item.get("symbol") or item.get("token_symbol") or (addr[:8] if addr else "N/A"))
            name = str(item.get("name") or "N/A")
            chain = str(item.get("chain") or "solana").lower()
            dex = str(item.get("dex") or "raydium")
            liq = _safe_float(item.get("initial_liquidity_usd") or item.get("liquidity_usd"), 0.0)
            reason = str(item.get("waiting_reason") or item.get("reason_pending") or "AGUARDANDO_SLOT")
            reason_pending = str(item.get("reason_pending") or reason)
            enq = str(item.get("enqueued_at") or item.get("added_at") or "")
            age = _safe_float(item.get("age_hours"), 0.0) if item.get("age_hours") is not None else None
            strat = str(item.get("eligible_strategy") or "DUAL")
            last_p = _safe_float(item.get("last_price"), 0.0) if item.get("last_price") is not None else None

            item["address"] = addr
            item["token_address"] = addr
            item["symbol"] = sym
            item["token_symbol"] = sym
            item["name"] = name
            item["chain"] = chain
            item["dex"] = dex
            item["initial_liquidity_usd"] = liq
            item["liquidity_usd"] = liq
            item["waiting_reason"] = reason
            item["reason_pending"] = reason_pending
            item["enqueued_at"] = enq
            item["added_at"] = enq
            item["age_hours"] = age
            item["eligible_strategy"] = strat
            item["last_price"] = last_p

            normalized_map[addr.lower()] = item

        return list(normalized_map.values())


    def _get_active_settings(self, mode: str = "paper") -> dict[str, Any]:
        """Retorna dicionário com os parâmetros ativos do bot para o modo especificado."""
        m = mode.strip().lower()
        if self.orchestrator and getattr(self.orchestrator, "execution_mode", "PAPER").lower() == m:
            settings_obj = getattr(self.orchestrator, "settings", None)
            if settings_obj:
                return {
                    "execution_mode": m.upper(),
                    "max_concurrent_positions": _safe_int(
                        getattr(settings_obj, "LIVE_MAX_CONCURRENT_POSITIONS" if m == "live" else "MAX_CONCURRENT_POSITIONS", 50),
                        50,
                    ),
                    "paper_buy_amount_usd": _safe_float(getattr(settings_obj, "PAPER_BUY_AMOUNT_USD", 1.0), 1.0),
                    "live_buy_amount_usd": _safe_float(getattr(settings_obj, "LIVE_BUY_AMOUNT_USD", 5.0), 5.0),
                    "wallet_balance_usd": _safe_float(
                        getattr(
                            self.orchestrator.execution_engine,
                            "balance_usd",
                            getattr(self.orchestrator.execution_engine, "_last_known_balance_usd", 10.0),
                        ),
                        10.0,
                    ),
                    "paper_initial_wallet_usd": _safe_float(getattr(settings_obj, "PAPER_INITIAL_WALLET_USD", 0.0), 0.0),
                    "min_trade_amount_usd": _safe_float(getattr(settings_obj, "MIN_TRADE_AMOUNT_USD", 1.0), 1.0),
                    "max_token_age_hours": _safe_float(getattr(settings_obj, "MAX_TOKEN_AGE_HOURS_SCALP", getattr(settings_obj, "MAX_TOKEN_AGE_HOURS", 720.0)), 720.0),
                    "break_even_gain_pct": _safe_float(getattr(settings_obj, "BREAK_EVEN_GAIN_PCT", 100.0), 100.0),
                    "trailing_stop_drop_pct": _safe_float(getattr(settings_obj, "TRAILING_STOP_DROP_PCT", 12.0), 12.0),
                    "emergency_stop_loss_pct": _safe_float(getattr(settings_obj, "EMERGENCY_STOP_LOSS_PCT", 20.0), 20.0),
                    "max_slippage_pct": _safe_float(
                        getattr(settings_obj, "LIVE_MAX_SLIPPAGE_PCT" if m == "live" else "MAX_SLIPPAGE_PCT", 1.5),
                        1.5,
                    ),
                    "live_max_slippage_pct": _safe_float(getattr(settings_obj, "LIVE_MAX_SLIPPAGE_PCT", 1.5), 1.5),
                    "live_max_concurrent_positions": _safe_int(getattr(settings_obj, "LIVE_MAX_CONCURRENT_POSITIONS", 3), 3),
                    "live_jito_tip_lamports": _safe_int(getattr(settings_obj, "LIVE_JITO_TIP_LAMPORTS", 100000), 100000),
                    "reentry_trailing_cooloff_min": _safe_float(getattr(settings_obj, "REENTRY_TRAILING_COOLOFF_SEC", 300.0), 300.0) / 60.0,
                    "reentry_stoploss_cooloff_min": _safe_float(getattr(settings_obj, "REENTRY_STOPLOSS_COOLOFF_SEC", 1800.0), 1800.0) / 60.0,
                    "reentry_min_bounce_pct": _safe_float(getattr(settings_obj, "REENTRY_MIN_BOUNCE_PCT", 3.0), 3.0),
                    "trading_strategy_mode": str(getattr(settings_obj, "TRADING_STRATEGY_MODE", "DUAL")),
                    "scalp_max_hold_minutes": _safe_float(getattr(settings_obj, "SCALP_MAX_HOLD_MINUTES", 60.0), 60.0),
                    "scalp_target_gain_pct": _safe_float(getattr(settings_obj, "SCALP_TARGET_GAIN_PCT", 100.0), 100.0),
                    "swing_max_hold_hours": _safe_float(getattr(settings_obj, "SWING_MAX_HOLD_HOURS", 24.0), 24.0),
                    "swing_target_gain_pct": _safe_float(getattr(settings_obj, "SWING_TARGET_GAIN_PCT", 2000.0), 2000.0),
                    "swing_max_hourly_drop_pct": _safe_float(getattr(settings_obj, "SWING_MAX_HOURLY_DROP_PCT", 15.0), 15.0),
                    "swing_initial_stop_loss_pct": _safe_float(getattr(settings_obj, "SWING_INITIAL_STOP_LOSS_PCT", 0.0), 0.0),
                    "swing_tier1_mult": _safe_float(getattr(settings_obj, "SWING_TIER1_TARGET_MULT", 2.0), 2.0),
                    "swing_tier2_mult": _safe_float(getattr(settings_obj, "SWING_TIER2_TARGET_MULT", 4.0), 4.0),
                    "swing_tier3_mult": _safe_float(getattr(settings_obj, "SWING_TIER3_TARGET_MULT", 6.0), 6.0),
                    "swing_tier4_mult": _safe_float(getattr(settings_obj, "SWING_TIER4_TARGET_MULT", 11.0), 11.0),
                    "swing_tier5_mult": _safe_float(getattr(settings_obj, "SWING_TIER5_TARGET_MULT", 21.0), 21.0),
                    "swing_trailing_drop_pct": _safe_float(getattr(settings_obj, "SWING_TRAILING_DROP_PCT", 25.0), 25.0),
                    "min_token_age_scalp_min": _safe_float(getattr(settings_obj, "MIN_TOKEN_AGE_HOURS_SCALP", 3.0), 3.0) * 60.0,
                    "min_token_age_swing_hours": _safe_float(getattr(settings_obj, "MIN_TOKEN_AGE_HOURS_SWING", 3.0), 3.0),
                    "max_token_age_swing_hours": _safe_float(getattr(settings_obj, "MAX_TOKEN_AGE_HOURS_SWING", 6.0), 6.0),
                    "min_volume_1h_usd": _safe_float(getattr(settings_obj, "MIN_VOLUME_1H_USD", 15000.0), 15000.0),
                    "min_buy_ratio_5m_pct": _safe_float(getattr(settings_obj, "MIN_BUY_RATIO_5M_PCT", 50.0), 50.0),
                    "min_price_change_5m_pct": _safe_float(getattr(settings_obj, "MIN_PRICE_CHANGE_5M_PCT", -2.0), -2.0),
                    "min_liquidity_swing_usd": _safe_float(getattr(settings_obj, "MIN_LIQUIDITY_SWING_USD", 20000.0), 20000.0),
                    "max_top10_holders_pct": _safe_float(getattr(settings_obj, "MAX_TOP10_HOLDERS_PCT", 15.0), 15.0),
                    "min_liquidity_usd": _safe_float(getattr(settings_obj, "MIN_LIQUIDITY_USD", 5000.0), 5000.0),
                }

        cfg_file = get_config_file(m)
        cfg: dict[str, Any] = {}
        if cfg_file.exists():
            try:
                raw = json.loads(cfg_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    cfg = raw
            except Exception:
                pass

        default_bal = 10.0 if m == "paper" else 0.0
        return {
            "execution_mode": m.upper(),
            "max_concurrent_positions": _safe_int(cfg.get("live_max_concurrent_positions" if m == "live" else "max_concurrent_positions"), 3 if m == "live" else 50),
            "live_max_concurrent_positions": _safe_int(cfg.get("live_max_concurrent_positions"), 3),
            "paper_buy_amount_usd": _safe_float(cfg.get("paper_buy_amount_usd"), 1.0),
            "live_buy_amount_usd": _safe_float(cfg.get("live_buy_amount_usd"), 5.0),
            "wallet_balance_usd": _safe_float(
                cfg.get("wallet_balance_usd", cfg.get("paper_initial_wallet_usd")),
                default_bal,
            ),
            "paper_initial_wallet_usd": _safe_float(
                cfg.get("paper_initial_wallet_usd", cfg.get("wallet_balance_usd")),
                default_bal,
            ),
            "min_trade_amount_usd": _safe_float(cfg.get("min_trade_amount_usd"), 1.0),
            "max_token_age_hours": _safe_float(cfg.get("max_token_age_hours"), 3.0),
            "break_even_gain_pct": _safe_float(cfg.get("break_even_gain_pct"), 100.0),
            "trailing_stop_drop_pct": _safe_float(cfg.get("trailing_stop_drop_pct"), 12.0),
            "emergency_stop_loss_pct": _safe_float(cfg.get("emergency_stop_loss_pct"), 20.0),
            "max_slippage_pct": _safe_float(cfg.get("live_max_slippage_pct" if m == "live" else "max_slippage_pct"), 1.5),
            "live_max_slippage_pct": _safe_float(cfg.get("live_max_slippage_pct"), 1.5),
            "reentry_trailing_cooloff_min": _safe_float(cfg.get("reentry_trailing_cooloff_min"), 5.0),
            "reentry_stoploss_cooloff_min": _safe_float(cfg.get("reentry_stoploss_cooloff_min"), 30.0),
            "reentry_min_bounce_pct": _safe_float(cfg.get("reentry_min_bounce_pct"), 3.0),
            "trading_strategy_mode": cfg.get("trading_strategy_mode") or "DUAL",
            "scalp_max_hold_minutes": _safe_float(cfg.get("scalp_max_hold_minutes"), 60.0),
            "scalp_target_gain_pct": _safe_float(cfg.get("scalp_target_gain_pct"), 100.0),
            "swing_max_hold_hours": _safe_float(cfg.get("swing_max_hold_hours"), 24.0),
            "swing_target_gain_pct": _safe_float(cfg.get("swing_target_gain_pct"), 2000.0),
            "swing_max_hourly_drop_pct": _safe_float(cfg.get("swing_max_hourly_drop_pct"), 15.0),
            "swing_initial_stop_loss_pct": _safe_float(cfg.get("swing_initial_stop_loss_pct"), 0.0),
            "swing_tier1_mult": _safe_float(cfg.get("swing_tier1_mult"), 2.0),
            "swing_tier2_mult": _safe_float(cfg.get("swing_tier2_mult"), 4.0),
            "swing_tier3_mult": _safe_float(cfg.get("swing_tier3_mult"), 6.0),
            "swing_tier4_mult": _safe_float(cfg.get("swing_tier4_mult"), 11.0),
            "swing_tier5_mult": _safe_float(cfg.get("swing_tier5_mult"), 21.0),
            "swing_trailing_drop_pct": _safe_float(cfg.get("swing_trailing_drop_pct"), 25.0),
            "min_token_age_scalp_min": _safe_float(cfg.get("min_token_age_scalp_min"), 120.0),
            "min_token_age_swing_hours": _safe_float(cfg.get("min_token_age_swing_hours"), 3.0),
            "max_token_age_swing_hours": _safe_float(cfg.get("max_token_age_swing_hours"), 6.0),
            "min_volume_1h_usd": _safe_float(cfg.get("min_volume_1h_usd"), 15000.0),
            "min_buy_ratio_5m_pct": _safe_float(cfg.get("min_buy_ratio_5m_pct"), 50.0),
            "min_price_change_5m_pct": _safe_float(cfg.get("min_price_change_5m_pct"), -2.0),
            "min_liquidity_swing_usd": _safe_float(cfg.get("min_liquidity_swing_usd"), 20000.0),
            "max_top10_holders_pct": _safe_float(cfg.get("max_top10_holders_pct"), 15.0),
            "min_liquidity_usd": _safe_float(cfg.get("min_liquidity_usd"), 5000.0),
        }

    async def handle_index(self, _request: web.Request) -> web.Response:
        """Serve a interface Single-Page Application (SPA)."""
        html_file = STATIC_DIR / "index.html"
        if not html_file.exists():
            return web.Response(
                text="<h1>Vertex-bot Dashboard</h1><p>Interface index.html não encontrada.</p>",
                content_type="text/html",
                status=404,
            )
        content = html_file.read_text(encoding="utf-8")
        return web.Response(text=content, content_type="text/html")

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        """Endpoint WebSocket para streaming assíncrono de eventos e status."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._active_ws_clients.add(ws)
        try:
            await ws.send_json({"type": "connected", "message": "WebSocket conectado ao Vertex-bot"})
            async for _ in ws:
                pass
        finally:
            self._active_ws_clients.discard(ws)
        return ws

    async def broadcast_event(self, event_type: str, data: Any) -> None:
        """Transmite um evento JSON para todos os clientes conectados ao WebSocket."""
        if not self._active_ws_clients:
            return
        payload = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        coros = [
            client.send_json(payload)
            for client in list(self._active_ws_clients)
            if not client.closed
        ]
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)

    def _get_decoupled_initial_wallet(self, mode: str = "paper") -> float:
        """Obtém a banca inicial configurada quando em modo desacoplado."""
        cfg_file = get_config_file(mode)
        if cfg_file.exists():
            try:
                raw = json.loads(cfg_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    if "wallet_balance_usd" in raw and raw["wallet_balance_usd"] is not None:
                        return _safe_float(raw["wallet_balance_usd"], 0.0)
                    if "paper_initial_wallet_usd" in raw and raw["paper_initial_wallet_usd"] is not None:
                        return _safe_float(raw["paper_initial_wallet_usd"], 0.0)
            except Exception:
                pass
        sess_file = get_session_file(mode)
        if sess_file.exists():
            try:
                sess_data = json.loads(sess_file.read_text(encoding="utf-8"))
                if isinstance(sess_data, dict) and sess_data.get("initial_wallet_usd") is not None:
                    return _safe_float(sess_data["initial_wallet_usd"], 0.0)
            except Exception:
                pass
        return 0.0

    def _get_active_initial_wallet(self, mode: str = "paper") -> float:
        """Retorna a banca inicial configurada da sessão."""
        m = mode.strip().lower()
        if self.orchestrator and getattr(self.orchestrator, "execution_mode", "PAPER").lower() == m:
            settings_obj = getattr(self.orchestrator, "settings", None)
            return float(getattr(settings_obj, "PAPER_INITIAL_WALLET_USD" if m == "paper" else "LIVE_BUY_AMOUNT_USD", 0.0))
        return self._get_decoupled_initial_wallet(mode=m)

    def _resolve_initial_wallet_and_cash(self, mode: str = "paper") -> tuple[float, float | None]:
        """Resolve banca inicial e saldo em caixa atual (sincronamente para execução em worker thread)."""
        m = mode.strip().lower()
        try:
            initial_wallet = self._get_active_initial_wallet(mode=m)
        except TypeError:
            initial_wallet = self._get_active_initial_wallet()

        if self.orchestrator and getattr(self.orchestrator, "execution_mode", "PAPER").lower() == m:
            engine = getattr(self.orchestrator, "paper_engine", getattr(self.orchestrator, "execution_engine", None))
            current_cash = float(engine.balance_usd) if engine and hasattr(engine, "balance_usd") else initial_wallet
            return initial_wallet, current_cash

        try:
            _, _, wallet_usd, _ = self._get_bot_status(mode=m)
        except TypeError:
            _, _, wallet_usd, _ = self._get_bot_status()
        current_cash = wallet_usd if wallet_usd is not None else initial_wallet
        return initial_wallet, current_cash

    def _resolve_slot_quotas(self, mode: str = "paper") -> dict[str, int]:
        """Obtém ou calcula a distribuição de cotas 50/50 de slots de posições."""
        m = mode.strip().lower()
        if self.orchestrator and hasattr(self.orchestrator, "get_slot_quotas"):
            try:
                quotas = self.orchestrator.get_slot_quotas(m)
                if isinstance(quotas, dict):
                    return {
                        "max_positions": int(quotas.get("max_positions", 10)),
                        "priority_slots_max": int(quotas.get("priority_slots_max", 5)),
                        "priority_slots_used": int(quotas.get("priority_slots_used", 0)),
                        "new_tokens_slots_max": int(quotas.get("new_tokens_slots_max", 5)),
                        "new_tokens_slots_used": int(quotas.get("new_tokens_slots_used", 0)),
                        "total_active": int(quotas.get("total_active", 0)),
                    }
            except Exception:
                pass

        st_file = get_status_file(m)
        if st_file.exists():
            try:
                data = json.loads(st_file.read_text(encoding="utf-8"))
                raw_quotas = data.get("slot_quotas")
                if isinstance(raw_quotas, dict) and "priority_slots_max" in raw_quotas:
                    return {
                        "max_positions": int(raw_quotas.get("max_positions", 10)),
                        "priority_slots_max": int(raw_quotas.get("priority_slots_max", 5)),
                        "priority_slots_used": int(raw_quotas.get("priority_slots_used", 0)),
                        "new_tokens_slots_max": int(raw_quotas.get("new_tokens_slots_max", 5)),
                        "new_tokens_slots_used": int(raw_quotas.get("new_tokens_slots_used", 0)),
                        "total_active": int(raw_quotas.get("total_active", 0)),
                    }
            except Exception:
                pass

        settings_dict = self._get_active_settings(mode=m)
        max_pos = int(settings_dict.get("live_max_concurrent_positions" if m == "live" else "max_concurrent_positions", 10))
        p_cap = max(1, (max_pos + 1) // 2)
        n_cap = max(1, max_pos - p_cap)
        return {
            "max_positions": max_pos,
            "priority_slots_max": p_cap,
            "priority_slots_used": 0,
            "new_tokens_slots_max": n_cap,
            "new_tokens_slots_used": 0,
            "total_active": 0,
        }

    async def handle_summary(self, request: web.Request) -> web.Response:
        """Retorna resumo consolidado de métricas e KPIs filtrado pelo modo (PAPER ou LIVE)."""
        try:
            mode = request.query.get("mode", "paper").lower()
            session_start = None
            if mode == "paper":
                try:
                    session_start = self._get_session_start(mode=mode)
                except TypeError:
                    session_start = self._get_session_start()
            tokens_summary = await self.tokens_repo.get_tokens_summary(since=session_start)

            initial_wallet, current_cash = await asyncio.to_thread(self._resolve_initial_wallet_and_cash, mode)

            pnl_summary = await self.positions_repo.get_pnl_summary(
                mode=mode.upper(),
                initial_wallet_usd=initial_wallet,
                current_cash_usd=current_cash,
            )

            try:
                waiting_tokens_count = len(self._get_waiting_tokens(mode=mode))
            except TypeError:
                waiting_tokens_count = len(self._get_waiting_tokens())

            slot_quotas = self._resolve_slot_quotas(mode=mode)

            payload = {
                "status": "success",
                "data": {
                    "pnl": pnl_summary,
                    "scanner": tokens_summary,
                    "session_mode": mode.upper(),
                    "waiting_tokens_count": waiting_tokens_count,
                    "slot_quotas": slot_quotas,
                },
            }
            return web.json_response(
                payload,
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
        except Exception as exc:
            logger.error("Erro ao gerar resumo no dashboard: %s", exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=500)

    async def handle_positions(self, request: web.Request) -> web.Response:
        """Retorna posições recentes com detalhes contábeis e filtro opcional de status e modo."""
        try:
            limit_param = request.query.get("limit", "100")
            limit = int(limit_param) if limit_param.isdigit() else 100
            status_filter = request.query.get("status")
            mode = request.query.get("mode", "paper").upper()
            positions = await self.positions_repo.get_all_positions(
                limit=limit,
                status_filter=status_filter,
                mode=mode,
            )

            # Enriquecimento em tempo real com memória de execuções ativas e cotações
            active_map: dict[int, Any] = {}
            if self.orchestrator and hasattr(self.orchestrator, "position_tracker"):
                orch_mode = getattr(self.orchestrator, "execution_mode", "PAPER")
                if not isinstance(orch_mode, str) or orch_mode.upper() == mode:
                    active_map = getattr(self.orchestrator.position_tracker, "active_positions", {})
            if self.orchestrator:
                tracker = None
                if mode == "LIVE":
                    tracker = getattr(self.orchestrator, "live_tracker", None)
                else:
                    tracker = getattr(self.orchestrator, "paper_tracker", None)

                if tracker is None or not isinstance(getattr(tracker, "active_positions", None), dict):
                    tracker = getattr(self.orchestrator, "position_tracker", None)

                if tracker and isinstance(getattr(tracker, "active_positions", None), dict):
                    active_map = tracker.active_positions

            for p in positions:
                pos_id = p.get("id")
                active_pos = active_map.get(pos_id) if isinstance(pos_id, int) else None
                entry_val = Decimal(str(p.get("entry_price") or 0.0))

                if active_pos and p.get("status") in ("OPEN", "PARTIALLY_CLOSED"):
                    curr_price = (
                        getattr(active_pos, "current_price", None)
                        or getattr(active_pos, "highest_price_seen", None)
                        or active_pos.entry_price
                    )
                    p["current_price"] = float(curr_price)
                    p["highest_price_seen"] = float(active_pos.highest_price_seen)
                    p["trailing_stop_price"] = float(active_pos.trailing_stop_price)
                    p["ratchet_tier"] = active_pos.ratchet_tier
                    p["active_tier"] = active_pos.ratchet_tier
                    p["ratchet_floor_price"] = float(active_pos.ratchet_floor_price)
                    p["break_even_triggered"] = bool(active_pos.break_even_triggered)

                    rem_tokens = getattr(active_pos, "remaining_token_amount", Decimal("0.0"))
                    unrealized_usd = (curr_price - entry_val) * rem_tokens
                    unrealized_pct = (
                        ((curr_price - entry_val) / entry_val * Decimal("100.0"))
                        if entry_val > Decimal("0.0")
                        else Decimal("0.0")
                    )

                    p["unrealized_pnl_usd"] = round(float(unrealized_usd), 4)
                    p["unrealized_pnl_pct"] = round(float(unrealized_pct), 2)
                else:
                    # Posição encerrada ou arquivada
                    exit_p = p.get("exit_price")
                    if exit_p is not None and float(exit_p) > 0.0:
                        p["current_price"] = float(exit_p)
                    else:
                        p["current_price"] = float(p.get("highest_price_seen") or p.get("entry_price") or 0.0)

                    allocated = Decimal(str(p.get("allocated_capital_usd") or 1.0))
                    realized = Decimal(str(p.get("realized_pnl_usd") or 0.0))
                    ret_pct = (realized / allocated * Decimal("100.0")) if allocated > Decimal("0.0") else Decimal("0.0")
                    p["unrealized_pnl_usd"] = round(float(realized), 4)
                    p["unrealized_pnl_pct"] = round(float(ret_pct), 2)

            return web.json_response(
                {"status": "success", "data": positions},
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
        except Exception as exc:
            logger.error("Erro ao buscar posições no dashboard: %s", exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=500)

    async def handle_orders(self, request: web.Request) -> web.Response:
        """Retorna histórico cronológico de ordens executadas."""
        try:
            limit_param = request.query.get("limit", "200")
            limit = int(limit_param) if limit_param.isdigit() else 200
            mode = request.query.get("mode", "paper").upper()
            orders = await self.orders_repo.get_recent_orders(limit=limit, mode=mode)

            return web.json_response({"status": "success", "data": orders})
        except Exception as exc:
            logger.error("Erro ao buscar ordens no dashboard: %s", exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=500)

    async def handle_tokens(self, request: web.Request) -> web.Response:
        """Retorna tokens catalogados com filtros de status, busca e sessão."""
        try:
            limit_param = request.query.get("limit", "100")
            limit = int(limit_param) if limit_param.isdigit() else 100
            status_filter = request.query.get("status")
            search = request.query.get("search")
            all_time = request.query.get("all_time", "").lower() in ("true", "1")
            session_only = request.query.get("session_only", "").lower() in ("true", "1")
            mode = request.query.get("mode", "paper").lower()
            session_start = None
            if session_only or not all_time:
                try:
                    session_start = self._get_session_start(mode=mode)
                except TypeError:
                    session_start = self._get_session_start()

            tokens = await self.tokens_repo.get_recent_tokens(
                limit=limit,
                status_filter=status_filter,
                search=search,
                since=session_start,
            )

            if not tokens and session_start is not None and not session_only:
                tokens = await self.tokens_repo.get_recent_tokens(
                    limit=limit,
                    status_filter=status_filter,
                    search=search,
                    since=None,
                )

            return web.json_response({"status": "success", "data": tokens})
        except Exception as exc:
            logger.error("Erro ao buscar tokens no dashboard: %s", exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=500)

    async def handle_priority_tokens(self, request: web.Request) -> web.Response:
        """Retorna os tokens registrados na Lista de Prioridades (aprovados e negociados com saúde ativa)."""
        try:
            mode = request.query.get("mode", "paper").lower().strip()
            pool_manager = None
            if self.orchestrator and hasattr(self.orchestrator, "priority_pool"):
                orch_mode = str(getattr(self.orchestrator, "execution_mode", "PAPER")).lower()
                if orch_mode == mode:
                    pool_manager = self.orchestrator.priority_pool
                elif hasattr(self.orchestrator, "live_orchestrator") and mode == "live":
                    pool_manager = getattr(self.orchestrator.live_orchestrator, "priority_pool", None)
                elif hasattr(self.orchestrator, "paper_orchestrator") and mode == "paper":
                    pool_manager = getattr(self.orchestrator.paper_orchestrator, "priority_pool", None)

            if pool_manager is None:
                from src.engine.priority_pool import PriorityPoolManager
                pool_manager = PriorityPoolManager(mode=mode)

            tokens = pool_manager.get_all_priority_tokens()
            return web.json_response({
                "status": "success",
                "data": tokens,
                "total": len(tokens),
                "mode": mode,
            })
        except Exception as exc:
            logger.error("Erro ao consultar Lista de Prioridades no dashboard: %s", exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=500)

    async def handle_purge_priority_tokens(self, request: web.Request) -> web.Response:
        """
        Executa faxina na Lista de Prioridades removendo tokens mortos ou sem liquidez/volume.
        Suporta o parâmetro 'mode' via query param ou body JSON (padrão: 'paper').
        """
        try:
            mode: str | None = request.query.get("mode")
            if not mode and request.can_read_body:
                try:
                    body = await request.json()
                    if isinstance(body, dict) and "mode" in body:
                        mode = str(body["mode"])
                except Exception:
                    pass

            selected_mode = (mode or "paper").lower().strip()

            # 1. Se o orchestrator estiver disponível com o método dedicado
            if self.orchestrator and hasattr(self.orchestrator, "audit_and_purge_priority_pool"):
                res: dict[str, Any] = await self.orchestrator.audit_and_purge_priority_pool(selected_mode)
                return web.json_response({"status": "success", "data": res})

            # 2. Modo fallback: standalone sem orchestrator em memória
            from src.engine.priority_pool import PriorityPoolManager
            from src.engine.price_feed import DexScreenerPriceFeed

            pool_manager = PriorityPoolManager(mode=selected_mode)
            tokens = pool_manager.get_all_priority_tokens()
            if not tokens:
                return web.json_response({
                    "status": "success",
                    "data": {
                        "mode": selected_mode.upper(),
                        "purged_count": 0,
                        "remaining_count": 0,
                        "purged": [],
                    },
                })

            addresses = [str(t["token_address"]) for t in tokens]
            price_feed = DexScreenerPriceFeed()
            try:
                prices = await price_feed.fetch_prices(addresses)
            except Exception as e:
                logger.warning("Falha ao buscar preços na faxina avulsa do dashboard: %s", e)
                prices = {}

            pairs_data: dict[str, dict[str, Any]] = {}
            for addr in addresses:
                pd = price_feed.get_pair_data(addr)
                if pd:
                    pairs_data[addr] = pd

            await price_feed.close()
            purged = pool_manager.purge_dead_or_unprofitable_tokens(prices, pairs_data)

            return web.json_response({
                "status": "success",
                "data": {
                    "mode": selected_mode.upper(),
                    "purged_count": len(purged),
                    "remaining_count": pool_manager.count(),
                    "purged": purged,
                },
            })
        except Exception as exc:
            logger.error("Erro ao executar faxina na Lista de Prioridades no dashboard: %s", exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=500)

    async def handle_get_wallets(self, request: web.Request) -> web.Response:
        """Retorna as carteiras reais conectadas e seus saldos on-chain."""
        wallets: list[dict[str, Any]] = []
        if self.orchestrator and hasattr(self.orchestrator, "live_engine"):
            wallets = await self.orchestrator.live_engine.get_connected_wallets_info()
        elif self._fallback_live_engine is not None:
            wallets = await self._fallback_live_engine.get_connected_wallets_info()
        else:
            st_file = get_status_file("live")
            if st_file.exists():
                try:
                    data = json.loads(st_file.read_text(encoding="utf-8"))
                    raw_w = data.get("wallets")
                    if isinstance(raw_w, list):
                        wallets = raw_w
                except Exception:
                    pass
            if not wallets:
                wallets = [
                    {"chain": "solana", "name": "Solana Mainnet", "address": "", "is_connected": False, "balance_native": 0.0, "native_symbol": "SOL", "balance_usd": 0.0},
                    {"chain": "arbitrum", "name": "Arbitrum One / Base (EVM)", "address": "", "is_connected": False, "balance_native": 0.0, "native_symbol": "ETH", "balance_usd": 0.0},
                ]
        return web.json_response({"status": "success", "data": wallets})

    async def handle_connect_wallet(self, request: web.Request) -> web.Response:
        """Conecta chave privada em memória volátil de forma segura."""
        try:
            try:
                body = await request.json()
            except Exception:
                return web.json_response({"status": "error", "message": "Payload JSON inválido."}, status=400)

            chain = str(body.get("chain", "solana")).lower().strip()
            private_key = str(body.get("private_key", "")).strip()
            if not private_key:
                return web.json_response({"status": "error", "message": "Chave privada não fornecida."}, status=400)

            engine = self._get_live_engine()
            if chain == "solana":
                success, pubkey, bal_usd, err = await engine.connect_solana_wallet(private_key)
                if not success:
                    return web.json_response({"status": "error", "message": err or "Chave privada Solana inválida."}, status=400)
                update_env_file({
                    "SOLANA_PRIVATE_KEY_BASE58": private_key,
                    "WALLET_PRIVATE_KEY_BASE58": private_key,
                })
                logger.info("💾 [CONFIG] Chave Solana persistida automaticamente no .env")
                return web.json_response({
                    "status": "success",
                    "message": f"Carteira Solana conectada e salva no .env ({pubkey[:4]}...{pubkey[-4:]})!",
                    "data": {"chain": "solana", "address": pubkey, "balance_usd": float(bal_usd)},
                })
            elif chain in ("evm", "arbitrum", "base", "ethereum", "bsc", "polygon"):
                success, addr, bal_usd, err = await engine.connect_evm_wallet(private_key)
                if not success:
                    return web.json_response({"status": "error", "message": err or "Chave privada EVM inválida."}, status=400)
                update_env_file({
                    "EVM_PRIVATE_KEY": private_key,
                    "EVM_WALLET_PRIVATE_KEY": private_key,
                })
                logger.info("💾 [CONFIG] Chave EVM persistida automaticamente no .env")
                return web.json_response({
                    "status": "success",
                    "message": f"Carteira EVM conectada e salva no .env ({addr[:6]}...{addr[-4:]})!",
                    "data": {"chain": "evm", "address": addr, "balance_usd": float(bal_usd)},
                })
            else:
                return web.json_response({"status": "error", "message": f"Rede '{chain}' não suportada."}, status=400)
        except BaseException as exc:
            logger.error("Erro inesperado ao conectar carteira: %s", exc)
            return web.json_response({"status": "error", "message": f"Erro ao processar chave privada: {exc}"}, status=400)


    async def handle_disconnect_wallet(self, request: web.Request) -> web.Response:
        """Desconecta a carteira da rede especificada limpando da memória e do .env."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        chain = str(body.get("chain", "solana")).lower().strip()
        if self.orchestrator and hasattr(self.orchestrator, "live_engine"):
            self.orchestrator.live_engine.disconnect_wallet(chain)
        if self._fallback_live_engine is not None:
            self._fallback_live_engine.disconnect_wallet(chain)

        if chain == "solana":
            update_env_file({
                "SOLANA_PRIVATE_KEY_BASE58": "",
                "WALLET_PRIVATE_KEY_BASE58": "",
            })
            logger.info("💾 [CONFIG] Chave Solana removida do .env")
        elif chain in ("evm", "arbitrum", "base", "ethereum", "bsc", "polygon"):
            update_env_file({
                "EVM_PRIVATE_KEY": "",
                "EVM_WALLET_PRIVATE_KEY": "",
            })
            logger.info("💾 [CONFIG] Chave EVM removida do .env")

        return web.json_response({"status": "success", "message": f"Carteira {chain.upper()} desconectada e limpa do .env."})

    async def handle_transfer_wallet(self, request: web.Request) -> web.Response:
        """Processa transferência ou saque de fundos (SOL e EVM) on-chain."""
        try:
            try:
                body = await request.json()
            except Exception:
                return web.json_response({"status": "error", "message": "Payload JSON inválido."}, status=400)

            chain = str(body.get("chain", "solana")).lower().strip()
            recipient = str(body.get("recipient_address", "")).strip()
            if not recipient:
                return web.json_response({"status": "error", "message": "Endereço destinatário não informado."}, status=400)

            send_all = bool(body.get("send_all", False))
            private_key = str(body.get("private_key", "")).strip() or None
            engine = self._get_live_engine()

            if chain == "solana":
                raw_amount = body.get("amount_sol") or body.get("amount")
                amount_sol = Decimal(str(raw_amount)) if (raw_amount is not None and str(raw_amount).strip() != "") else None
                success, result_msg, solscan_url = await engine.transfer_sol(
                    recipient_address=recipient,
                    amount_sol=amount_sol,
                    send_all=send_all,
                    private_key=private_key,
                )
                if not success:
                    return web.json_response({"status": "error", "message": result_msg}, status=400)

                return web.json_response({
                    "status": "success",
                    "message": "Transferência Solana realizada com sucesso!",
                    "data": {
                        "tx_hash": result_msg,
                        "solscan_url": solscan_url,
                        "recipient": recipient,
                    },
                })

            elif chain in ("evm", "base", "arbitrum", "bsc", "polygon", "ethereum"):
                target_chain = "base" if chain == "evm" else chain
                raw_amount = body.get("amount_native") or body.get("amount_eth") or body.get("amount")
                amount_native = Decimal(str(raw_amount)) if (raw_amount is not None and str(raw_amount).strip() != "") else None
                success, result_msg, explorer_url = await engine.transfer_evm(
                    chain=target_chain,
                    recipient_address=recipient,
                    amount_native=amount_native,
                    send_all=send_all,
                    private_key=private_key,
                )
                if not success:
                    return web.json_response({"status": "error", "message": result_msg}, status=400)

                return web.json_response({
                    "status": "success",
                    "message": f"Transferência {target_chain.upper()} realizada com sucesso!",
                    "data": {
                        "tx_hash": result_msg,
                        "solscan_url": explorer_url,
                        "explorer_url": explorer_url,
                        "recipient": recipient,
                    },
                })

            else:
                return web.json_response({"status": "error", "message": f"Transferência para rede '{chain}' não suportada atualmente."}, status=400)
        except Exception as exc:
            logger.error("Erro no processamento de transferência: %s", exc)
            return web.json_response({"status": "error", "message": f"Erro interno na transferência: {exc}"}, status=500)



    async def handle_bot_status(self, request: web.Request) -> web.Response:
        """Retorna o estado operacional do bot e saldo em carteira."""
        """Retorna o estado operacional do bot, saldo em carteira e status de conexão de carteiras."""
        mode = request.query.get("mode", "paper").lower()
        try:
            is_running, is_paused, wallet_usd, active_positions_count = self._get_bot_status(mode=mode)
        except TypeError:
            is_running, is_paused, wallet_usd, active_positions_count = self._get_bot_status()

        try:
            settings_dict = self._get_active_settings(mode=mode)
        except TypeError:
            settings_dict = self._get_active_settings()

        try:
            initial_wallet = self._get_active_initial_wallet(mode=mode)
        except TypeError:
            initial_wallet = self._get_active_initial_wallet()

        try:
            waiting_tokens_count = len(self._get_waiting_tokens(mode=mode))
        except TypeError:
            waiting_tokens_count = len(self._get_waiting_tokens())

        has_wallet = False
        wallets_list: list[dict[str, Any]] = []
        if self.orchestrator and hasattr(self.orchestrator, "live_engine"):
            has_wallet = self.orchestrator.live_engine.has_connected_wallet()
            wallets_list = await self.orchestrator.live_engine.get_connected_wallets_info()
        else:
            st_file = get_status_file("live")
            if st_file.exists():
                try:
                    data = json.loads(st_file.read_text(encoding="utf-8"))
                    has_wallet = bool(data.get("has_connected_wallet", False))
                    raw_w = data.get("wallets")
                    if isinstance(raw_w, list):
                        wallets_list = raw_w
                except Exception:
                    pass

        slot_quotas = self._resolve_slot_quotas(mode=mode)

        payload = {
            "status": "success",
            "data": {
                "mode": mode.upper(),
                "is_running": is_running,
                "is_paused": is_paused,
                "has_wallet": has_wallet,
                "wallet_balance_usd": wallet_usd,
                "initial_wallet_usd": initial_wallet,
                "active_positions_count": active_positions_count,
                "waiting_tokens_count": waiting_tokens_count,
                "slot_quotas": slot_quotas,
                "priority_slots_max": slot_quotas["priority_slots_max"],
                "priority_slots_used": slot_quotas["priority_slots_used"],
                "new_tokens_slots_max": slot_quotas["new_tokens_slots_max"],
                "new_tokens_slots_used": slot_quotas["new_tokens_slots_used"],
                "wallets": wallets_list,
                "settings": settings_dict,
            },
        }
        return web.json_response(payload, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    async def handle_waiting_tokens(self, request: web.Request) -> web.Response:
        """Retorna lista de tokens aprovados aguardando liberação de slots."""
        mode = request.query.get("mode", "paper").lower()

        def _fetch_waiting() -> list[dict[str, Any]]:
            try:
                return self._get_waiting_tokens(mode=mode)
            except TypeError:
                return self._get_waiting_tokens()

        tokens = await asyncio.to_thread(_fetch_waiting)
        return web.json_response(
            {"status": "success", "data": tokens},
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    async def handle_bot_start(self, request: web.Request) -> web.Response:
        """Inicia o bot no modo especificado (PAPER ou LIVE)."""
        mode = request.query.get("mode", "paper").lower()
        if self.orchestrator:
            if mode == "live":
                if not hasattr(self.orchestrator, "live_engine") or not self.orchestrator.live_engine.has_connected_wallet():
                    return web.json_response({
                        "status": "error",
                        "message": "Nenhuma carteira real conectada. Conecte ao menos uma carteira Solana ou EVM antes de iniciar operações reais.",
                    }, status=400)
                update_env_file({
                    "EXECUTION_MODE": "LIVE",
                    "CONFIRM_LIVE_TRADING": "true",
                })
                if hasattr(self.orchestrator, "settings"):
                    self.orchestrator.settings.EXECUTION_MODE = "LIVE"
                    self.orchestrator.settings.CONFIRM_LIVE_TRADING = True
                if hasattr(self.orchestrator, "live_engine") and self.orchestrator.live_engine:
                    self.orchestrator.live_engine.confirm_live_trading = True
                logger.info("💾 [CONFIG] Modo LIVE e CONFIRM_LIVE_TRADING persistidos no .env")

                if hasattr(self.orchestrator, "start_live"):
                    await self.orchestrator.start_live()
                elif hasattr(self.orchestrator, "start"):
                    await self.orchestrator.start()
                return web.json_response({
                    "status": "success",
                    "message": "Operações reais (LIVE) iniciadas com sucesso!",
                    "is_running": True,
                    "mode": "LIVE",
                })
            else:
                update_env_file({
                    "EXECUTION_MODE": "PAPER",
                })
                if hasattr(self.orchestrator, "settings"):
                    self.orchestrator.settings.EXECUTION_MODE = "PAPER"
                logger.info("💾 [CONFIG] Modo PAPER persistido no .env")

                if hasattr(self.orchestrator, "start_paper"):
                    await self.orchestrator.start_paper()
                elif hasattr(self.orchestrator, "start"):
                    await self.orchestrator.start()
                return web.json_response({
                    "status": "success",
                    "message": "Simulação (PAPER) iniciada com sucesso!",
                    "is_running": True,
                    "mode": "PAPER",
                })
        # Modo desacoplado via IPC
        if mode == "live":
            st_file = get_status_file("live")
            has_wallet = False
            if st_file.exists():
                try:
                    data = json.loads(st_file.read_text(encoding="utf-8"))
                    has_wallet = bool(data.get("has_connected_wallet", False))
                except Exception:
                    pass
            if not has_wallet:
                return web.json_response({
                    "status": "error",
                    "message": "Nenhuma carteira real conectada. Conecte ao menos uma carteira com saldo antes de iniciar.",
                }, status=400)
            update_env_file({
                "EXECUTION_MODE": "LIVE",
                "CONFIRM_LIVE_TRADING": "true",
            })
            logger.info("💾 [CONFIG] Modo LIVE e CONFIRM_LIVE_TRADING persistidos no .env (IPC)")
            self._write_ipc_command("start", mode="live")
        else:
            update_env_file({
                "EXECUTION_MODE": "PAPER",
            })
            logger.info("💾 [CONFIG] Modo PAPER persistido no .env (IPC)")
            self._write_ipc_command("start", mode="paper")

        # Modo desacoplado: verifica se já está ativo via status
        is_running, _, _, _ = self._get_bot_status(mode=mode)
        if is_running:
            return web.json_response({
                "status": "info",
                "message": f"O bot ({mode.upper()}) já está em execução ativa.",
                "is_running": True,
            })
        return web.json_response({
            "status": "success",
            "message": f"Comando de início enviado para ({mode.upper()}).",
            "is_running": True,
            "mode": mode.upper(),
        })

    async def handle_bot_pause(self, request: web.Request) -> web.Response:
        """Pausa abertura de novas posições no modo especificado."""
        mode = request.query.get("mode", "paper").lower()
        if self.orchestrator and getattr(self.orchestrator, "execution_mode", "PAPER").lower() == mode:
            self.orchestrator.pause()
        if self.orchestrator:
            if mode == "paper" and hasattr(self.orchestrator, "pause_paper"):
                self.orchestrator.pause_paper()
            elif mode == "live" and hasattr(self.orchestrator, "pause_live"):
                self.orchestrator.pause_live()
            elif hasattr(self.orchestrator, "pause"):
                self.orchestrator.pause()
        else:
            self._write_ipc_command("pause", mode=mode)
        return web.json_response({"status": "success", "message": f"Bot ({mode.upper()}) pausado com sucesso.", "is_paused": True, "mode": mode.upper()})

    async def handle_bot_resume(self, request: web.Request) -> web.Response:
        """Retoma as operações normais do bot no modo especificado."""
        mode = request.query.get("mode", "paper").lower()
        if self.orchestrator and getattr(self.orchestrator, "execution_mode", "PAPER").lower() == mode:
            self.orchestrator.resume()
        if self.orchestrator:
            if mode == "paper" and hasattr(self.orchestrator, "resume_paper"):
                self.orchestrator.resume_paper()
            elif mode == "live" and hasattr(self.orchestrator, "resume_live"):
                self.orchestrator.resume_live()
            elif hasattr(self.orchestrator, "resume"):
                self.orchestrator.resume()
        else:
            self._write_ipc_command("resume", mode=mode)
        return web.json_response({"status": "success", "message": f"Bot ({mode.upper()}) retomado com sucesso.", "is_paused": False, "mode": mode.upper()})

    async def handle_bot_restart(self, request: web.Request) -> web.Response:
        """Reinicia a simulação: limpa posições/ordens, zera sequência para #1 e reinicia sessão. Apenas para PAPER."""
        mode = request.query.get("mode", "paper").lower()
        if mode == "live":
            return web.json_response(
                {"status": "error", "message": "Operação proibida para modo LIVE. Não é permitido zerar histórico real."},
                status=400,
            )
        try:
            body = await request.json() if request.can_read_body else {}
        except Exception:
            body = {}
        new_balance_raw = (
            body.get("initial_wallet_usd")
            if body.get("initial_wallet_usd") is not None
            else (
                body.get("wallet_balance_usd")
                if body.get("wallet_balance_usd") is not None
                else body.get("paper_initial_wallet_usd")
            )
        )
        new_balance = Decimal(str(new_balance_raw)) if new_balance_raw is not None else None

        if self.orchestrator:
            await self.orchestrator.restart_paper_session(new_balance=new_balance)
        else:
            await self.positions_repo.clear_paper_trading_data()
            bal_float = float(new_balance) if new_balance is not None else self._get_decoupled_initial_wallet(mode="paper")
            await asyncio.to_thread(self._reset_paper_session_file, bal_float, "paper")
            cfg = self._get_active_settings(mode="paper")
            cfg["wallet_balance_usd"] = bal_float
            cfg["paper_initial_wallet_usd"] = bal_float
            await asyncio.to_thread(self._save_config_file, cfg, "paper")
            try:
                st_file = get_status_file("paper")
                if st_file.exists():
                    st_data = json.loads(st_file.read_text(encoding="utf-8"))
                    st_data["wallet_balance_usd"] = bal_float
                    st_data["initial_wallet_usd"] = bal_float
                    st_file.write_text(json.dumps(st_data, indent=2), encoding="utf-8")
            except Exception:
                pass
            self._write_ipc_command(
                "restart",
                {
                    "wallet_balance_usd": bal_float,
                    "initial_wallet_usd": bal_float,
                    "paper_initial_wallet_usd": bal_float,
                },
                mode="paper",
            )

        active_bal = float(new_balance) if new_balance is not None else self._get_active_initial_wallet(mode="paper")
        return web.json_response({
            "status": "success",
            "message": "Simulação reiniciada com sucesso. Trades limpos e contador iniciado em #1.",
            "data": {
                "wallet_balance_usd": active_bal,
                "initial_wallet_usd": active_bal,
            },
        })

    async def handle_bot_stop(self, request: web.Request) -> web.Response:
        """Encerra com segurança a execução do bot no modo especificado."""
        """Encerra a simulação (limpa dados e saldo da simulação sem fechar o bot) ou encerra operações reais."""
        mode = request.query.get("mode", "paper").lower()
        if self.orchestrator and getattr(self.orchestrator, "execution_mode", "PAPER").lower() == mode:
            await self.orchestrator.stop()
        if self.orchestrator:
            if mode == "paper":
                if hasattr(self.orchestrator, "stop_paper"):
                    await self.orchestrator.stop_paper()
                elif hasattr(self.orchestrator, "stop"):
                    res = self.orchestrator.stop()
                    if asyncio.iscoroutine(res):
                        await res
                return web.json_response({
                    "status": "success",
                    "message": "Simulação encerrada com sucesso! Dados e saldo da simulação foram zerados. O bot permanece em execução.",
                    "is_running": False,
                    "mode": "PAPER",
                })
            elif mode == "live":
                if hasattr(self.orchestrator, "stop_live"):
                    await self.orchestrator.stop_live()
                elif hasattr(self.orchestrator, "stop"):
                    res = self.orchestrator.stop()
                    if asyncio.iscoroutine(res):
                        await res
                return web.json_response({
                    "status": "success",
                    "message": "Operações reais encerradas. Novas compras em modo real desativadas.",
                    "is_running": False,
                    "mode": "LIVE",
                })
        else:
            if mode == "paper":
                await self.positions_repo.clear_paper_trading_data()
                bal_float = self._get_decoupled_initial_wallet(mode="paper")
                await asyncio.to_thread(self._reset_paper_session_file, bal_float, "paper")
                self._write_ipc_command("stop", mode="paper")
            else:
                self._write_ipc_command("stop", mode="live")

        return web.json_response({"status": "success", "message": f"Operações ({mode.upper()}) encerradas com sucesso.", "is_running": False, "mode": mode.upper()})

    async def handle_bot_config(self, request: web.Request) -> web.Response:
        """Atualiza dinamicamente as configurações de risco e execução do bot."""
        try:
            raw_payload = await request.json()
            payload = {k: v for k, v in raw_payload.items() if v is not None}
            mode = request.query.get("mode", str(payload.get("mode", "paper"))).lower()
            if mode == "paper":
                wallet_raw = (
                    payload.get("paper_initial_wallet_usd")
                    if payload.get("paper_initial_wallet_usd") is not None
                    else (
                        payload.get("wallet_balance_usd")
                        if payload.get("wallet_balance_usd") is not None
                        else payload.get("initial_wallet_usd")
                    )
                )
                if wallet_raw is not None:
                    try:
                        bal_val = float(wallet_raw)
                        payload["wallet_balance_usd"] = bal_val
                        payload["paper_initial_wallet_usd"] = bal_val
                        self._update_paper_session_initial_wallet(bal_val, mode=mode)
                        st_file = get_status_file(mode)
                        if not self.orchestrator and st_file.exists():
                            try:
                                st_data = json.loads(st_file.read_text(encoding="utf-8"))
                                st_data["wallet_balance_usd"] = bal_val
                                st_data["initial_wallet_usd"] = bal_val
                                st_file.write_text(json.dumps(st_data, indent=2), encoding="utf-8")
                            except Exception:
                                pass
                    except (ValueError, TypeError):
                        pass

            if self.orchestrator and getattr(self.orchestrator, "execution_mode", "PAPER").lower() == mode:
                updated = self.orchestrator.update_dynamic_config(payload)
            else:
                cfg = self._get_active_settings(mode=mode)
                cfg.update(payload)
                await asyncio.to_thread(self._save_config_file, cfg, mode)
                self._write_ipc_command("reload_config", payload, mode=mode)
                updated = cfg
            return web.json_response({"status": "success", "message": f"Configurações atualizadas para {mode.upper()}.", "data": updated})
        except Exception as exc:
            logger.error("Erro ao atualizar configurações: %s", exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=400)

    async def handle_wallet_deposit(self, request: web.Request) -> web.Response:
        """Adiciona saldo à carteira do modo de simulação."""
        mode = request.query.get("mode", "paper").lower()
        if mode == "live":
            return web.json_response(
                {"status": "error", "message": "Operação de depósito simulado indisponível para modo LIVE. Transfira fundos diretamente para a carteira on-chain."},
                status=400,
            )
        try:
            payload = await request.json()
            amount_usd = Decimal(str(payload.get("amount_usd", "10.0")))
            if self.orchestrator and getattr(self.orchestrator, "execution_mode", "PAPER").lower() == "paper":
                new_balance = self.orchestrator.deposit_wallet(amount_usd)
            else:
                cfg = self._get_active_settings(mode="paper")
                curr_balance = Decimal(str(cfg.get("wallet_balance_usd", "5.0")))
                new_balance = curr_balance + amount_usd
                cfg["wallet_balance_usd"] = float(new_balance)
                await asyncio.to_thread(self._save_config_file, cfg, "paper")
                self._write_ipc_command("deposit", {"amount_usd": float(amount_usd)}, mode="paper")
            return web.json_response({
                "status": "success",
                "message": f"Depósito de ${amount_usd:.2f} efetuado com sucesso!",
                "wallet_balance_usd": float(new_balance),
            })
        except Exception as exc:
            logger.error("Erro ao realizar depósito: %s", exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=400)

    async def handle_position_close(self, request: web.Request) -> web.Response:
        """Encerra e vende manualmente uma posição ativa a mercado."""
        try:
            raw_id = request.match_info.get("id")
            if not raw_id or not raw_id.isdigit():
                return web.json_response({"status": "error", "message": "ID de posição inválido."}, status=400)
            position_id = int(raw_id)
            mode = request.query.get("mode", "paper").lower()

            if self.orchestrator and getattr(self.orchestrator, "execution_mode", "PAPER").lower() == mode:
                success = await self.orchestrator.close_position_manually(position_id)
            if self.orchestrator:
                try:
                    success = await self.orchestrator.close_position_manually(position_id, mode=mode.upper())
                except TypeError:
                    success = await self.orchestrator.close_position_manually(position_id)
                if not success:
                    return web.json_response(
                        {"status": "error", "message": f"Não foi possível fechar a posição #{position_id} (pode já estar fechada)."},
                        status=400,
                    )
            else:
                self._write_ipc_command("close_position", {"position_id": position_id}, mode=mode)
                self._write_ipc_command("close_position", {"position_id": position_id, "mode": mode.upper()}, mode=mode)

            await self.broadcast_event("trade", {"action": "manual_close", "position_id": position_id, "mode": mode.upper()})
            return web.json_response({
                "status": "success",
                "message": f"Ordem de venda para fechar a posição #{position_id} executada com sucesso!",
                "position_id": position_id,
            })
        except Exception as exc:
            logger.error("Erro ao fechar posição manualmente: %s", exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=500)

    async def handle_position_buy_more(self, request: web.Request) -> web.Response:
        """Abre uma nova posição para o token informado ou compra novamente na estratégia do card."""
        try:
            raw_id = request.match_info.get("id")
            if not raw_id or not raw_id.isdigit():
                return web.json_response({"status": "error", "message": "ID de posição inválido."}, status=400)
            position_id = int(raw_id)
            mode = request.query.get("mode", "paper").lower()

            if self.orchestrator and getattr(self.orchestrator, "execution_mode", "PAPER").lower() == mode:
                success, msg = await self.orchestrator.open_position_manually(position_id=position_id)
            if self.orchestrator:
                try:
                    success, msg = await self.orchestrator.open_position_manually(position_id=position_id, mode=mode.upper())
                except TypeError:
                    success, msg = await self.orchestrator.open_position_manually(position_id=position_id)
                if not success:
                    return web.json_response({"status": "error", "message": msg}, status=400)
                resp_msg = msg
            else:
                self._write_ipc_command("buy_more", {"position_id": position_id}, mode=mode)
                self._write_ipc_command("buy_more", {"position_id": position_id, "mode": mode.upper()}, mode=mode)
                resp_msg = f"Comando de nova compra para o ativo #{position_id} enviado ao bot com sucesso."

            await self.broadcast_event("trade", {"action": "manual_buy", "position_id": position_id, "mode": mode.upper()})
            return web.json_response({
                "status": "success",
                "message": resp_msg,
                "position_id": position_id,
            })
        except Exception as exc:
            logger.error("Erro ao abrir nova posição manualmente: %s", exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=500)



@web.middleware
async def cors_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Habilita CORS para permitir desenvolvimento e integração desacoplada do frontend."""
    if request.method == "OPTIONS":
        resp: web.StreamResponse = web.Response(status=200)
    else:
        try:
            resp = await handler(request)
        except Exception as exc:
            logger.error("Erro não tratado na rota %s: %s", request.path, exc)
            resp = web.json_response({"status": "error", "message": f"Erro interno no servidor: {exc}"}, status=500)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp



def create_dashboard_app(db: DatabaseManager, orchestrator: Any | None = None) -> web.Application:
    """Fábrica para instanciar a aplicação web com todas as rotas configuradas."""
    server = DashboardServer(db, orchestrator=orchestrator)
    app = web.Application(middlewares=[cors_middleware])

    app.router.add_get("/", server.handle_index)
    app.router.add_get("/ws", server.handle_ws)
    app.router.add_get("/api/summary", server.handle_summary)
    app.router.add_get("/api/positions", server.handle_positions)
    app.router.add_post("/api/positions/{id}/close", server.handle_position_close)
    app.router.add_post("/api/positions/{id}/buy_more", server.handle_position_buy_more)
    app.router.add_get("/api/orders", server.handle_orders)
    app.router.add_get("/api/tokens", server.handle_tokens)
    app.router.add_get("/api/tokens/priority", server.handle_priority_tokens)
    app.router.add_post("/api/tokens/priority/purge", server.handle_purge_priority_tokens)
    app.router.add_get("/api/waiting_tokens", server.handle_waiting_tokens)
    app.router.add_get("/api/waiting-tokens", server.handle_waiting_tokens)

    # Rotas de carteiras reais (LIVE)
    app.router.add_get("/api/wallets", server.handle_get_wallets)
    app.router.add_post("/api/wallets/connect", server.handle_connect_wallet)
    app.router.add_post("/api/wallets/disconnect", server.handle_disconnect_wallet)
    app.router.add_post("/api/wallets/transfer", server.handle_transfer_wallet)

    # Rotas de controle operacional e configurações
    app.router.add_get("/api/bot/status", server.handle_bot_status)
    app.router.add_post("/api/bot/start", server.handle_bot_start)
    app.router.add_post("/api/bot/pause", server.handle_bot_pause)
    app.router.add_post("/api/bot/resume", server.handle_bot_resume)
    app.router.add_post("/api/bot/restart", server.handle_bot_restart)
    app.router.add_post("/api/bot/stop", server.handle_bot_stop)
    app.router.add_post("/api/bot/config", server.handle_bot_config)
    app.router.add_post("/api/wallet/deposit", server.handle_wallet_deposit)

    # Suporte a arquivos estáticos do bundle Next.js
    if (STATIC_DIR / "_next").exists():
        app.router.add_static("/_next", STATIC_DIR / "_next")
    if STATIC_DIR.exists():
        app.router.add_static("/static", STATIC_DIR)

    return app


async def run_dashboard_server(
    db_path: str = "data/vertex_bot.db",
    host: str = "0.0.0.0",
    port: int = 8080,
) -> None:
    """Inicializa e executa o servidor web do Dashboard como processo independente."""
    db = DatabaseManager(db_path)
    await db.initialize()

    app = create_dashboard_app(db)
    runner = web.AppRunner(app)
    await runner.setup()

    bound_port = port
    for p in range(port, port + 20):
        try:
            site = web.TCPSite(runner, host, p)
            await site.start()
            bound_port = p
            break
        except OSError as exc:
            if exc.errno == 98 and p < port + 19:
                continue
            raise

    logger.info("==================================================")
    logger.info(" VERTEX-BOT DASHBOARD OPERACIONAL!")
    logger.info(" Acesse no navegador: http://localhost:%d", bound_port)
    logger.info("==================================================")

    # Mantém o servidor ativo até sinal de cancelamento
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()
        await db.close()
        logger.info("Dashboard finalizado.")
