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

from src.database.connection import DatabaseManager
from src.database.repository import OrdersRepository, PositionsRepository, TokensRepository
from src.utils.logger import setup_logger

logger = setup_logger("vertex.dashboard")

STATIC_DIR = Path(__file__).parent / "static"
STATUS_FILE = Path("data/bot_status.json")
CONTROL_FILE = Path("data/bot_control.json")
CONFIG_FILE = Path("data/bot_config.json")
SESSION_FILE = Path("data/paper_session.json")


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

    def _get_session_start(self) -> datetime | None:
        """Lê o timestamp de início da sessão simulada para zeragem visual."""
        try:
            if SESSION_FILE.exists():
                data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
                start_str = data.get("session_start")
                if start_str and isinstance(start_str, str):
                    return datetime.fromisoformat(start_str)
        except Exception:
            pass
        return None

    def _write_ipc_command(self, command: str, payload: dict[str, Any] | None = None) -> None:
        """Grava comando atômico de controle para o bot em processo independente."""
        try:
            CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True)
            cmd_data = {
                "command": command,
                "command_id": str(uuid.uuid4()),
                "payload": payload or {},
                "timestamp": datetime.now(UTC).timestamp(),
            }
            tmp_path = CONTROL_FILE.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(cmd_data, indent=2), encoding="utf-8")
            tmp_path.replace(CONTROL_FILE)
            logger.info("📡 [IPC ENVIADO] Comando '%s' registrado para o bot.", command)
        except Exception as exc:
            logger.error("Falha ao gravar comando IPC '%s': %s", command, exc)

    def _save_config_file(self, cfg: dict[str, Any]) -> None:
        """Salva arquivo de configuração persistida em disco."""
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Falha ao gravar data/bot_config.json: %s", exc)

    def _reset_paper_session_file(self, initial_balance: float | None = None) -> None:
        """Reinicia timestamp da sessão simulada."""
        try:
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            bal = initial_balance if initial_balance is not None else 10.0
            SESSION_FILE.write_text(
                json.dumps({
                    "session_start": datetime.now(UTC).isoformat(),
                    "mode": "PAPER",
                    "initial_wallet_usd": bal,
                }, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Falha ao gravar data/paper_session.json: %s", exc)

    def _update_paper_session_initial_wallet(self, initial_balance: float) -> None:
        """Atualiza a banca inicial em paper_session.json preservando o timestamp de início."""
        try:
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {}
            if SESSION_FILE.exists():
                try:
                    data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data["initial_wallet_usd"] = initial_balance
            if "session_start" not in data:
                data["session_start"] = datetime.now(UTC).isoformat()
            data["mode"] = "PAPER"
            SESSION_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Falha ao atualizar banca inicial em data/paper_session.json: %s", exc)

    def _get_bot_status_from_orchestrator(self) -> tuple[bool, bool, float, int]:
        """Obtém status diretamente da memória quando executando acoplado."""
        if not self.orchestrator:
            return False, False, 0.0, 0
        is_running = bool(getattr(self.orchestrator, "is_running", True))
        is_paused = bool(getattr(self.orchestrator, "is_paused", False))
        wallet_usd = 0.0
        active_positions_count = 0
        engine = getattr(self.orchestrator, "execution_engine", None)
        if engine and hasattr(engine, "balance_usd"):
            wallet_usd = float(engine.balance_usd)
        elif hasattr(self.orchestrator, "settings"):
            wallet_usd = float(getattr(self.orchestrator.settings, "PAPER_INITIAL_WALLET_USD", 10.0))
        tracker = getattr(self.orchestrator, "position_tracker", None)
        if tracker:
            active_positions_count = len(tracker.active_positions)
        return is_running, is_paused, wallet_usd, active_positions_count

    def _get_bot_status(self) -> tuple[bool, bool, float, int]:
        """Retorna tupla (is_running, is_paused, wallet_usd, active_positions_count)."""
        if self.orchestrator:
            return self._get_bot_status_from_orchestrator()

        fallback_wallet = self._get_decoupled_initial_wallet()
        if STATUS_FILE.exists():
            try:
                data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
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
                logger.debug("Erro ao ler data/bot_status.json: %s", exc)

        return False, False, 0.0, 0

    def _get_waiting_tokens(self) -> list[dict[str, Any]]:
        """Retorna lista de tokens aprovados aguardando liberação de slots."""
        raw_list: list[dict[str, Any]] = []
        if self.orchestrator:
            waiting = getattr(self.orchestrator, "waiting_tokens", None)
            if isinstance(waiting, dict):
                raw_list = list(waiting.values())
        elif Path("data/waiting_tokens.json").exists():
            try:
                raw = json.loads(Path("data/waiting_tokens.json").read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    raw_list = list(raw.values())
            except Exception:
                pass

        normalized: list[dict[str, Any]] = []
        for t in raw_list:
            item = dict(t)
            addr = str(item.get("address") or item.get("token_address") or "")
            sym = str(item.get("symbol") or item.get("token_symbol") or (addr[:8] if addr else "N/A"))
            name = str(item.get("name") or "N/A")
            liq = float(item.get("initial_liquidity_usd") or item.get("liquidity_usd") or 0.0)
            reason = str(item.get("waiting_reason") or item.get("reason_pending") or "AGUARDANDO_SLOT")
            enq = str(item.get("enqueued_at") or item.get("added_at") or "")

            item["address"] = addr
            item["token_address"] = addr
            item["symbol"] = sym
            item["token_symbol"] = sym
            item["name"] = name
            item["initial_liquidity_usd"] = liq
            item["liquidity_usd"] = liq
            item["waiting_reason"] = reason
            item["reason_pending"] = reason
            item["enqueued_at"] = enq
            item["added_at"] = enq
            normalized.append(item)

        return normalized

    def _get_active_settings(self) -> dict[str, Any]:
        """Retorna dicionário com os parâmetros ativos do bot."""
        if self.orchestrator:
            settings_obj = getattr(self.orchestrator, "settings", None)
            if settings_obj:
                return {
                    "execution_mode": getattr(settings_obj, "EXECUTION_MODE", "PAPER"),
                    "max_concurrent_positions": _safe_int(getattr(settings_obj, "MAX_CONCURRENT_POSITIONS", 50), 50),
                    "paper_buy_amount_usd": _safe_float(getattr(settings_obj, "PAPER_BUY_AMOUNT_USD", 1.0), 1.0),
                    "wallet_balance_usd": _safe_float(getattr(settings_obj, "PAPER_INITIAL_WALLET_USD", 10.0), 10.0),
                    "paper_initial_wallet_usd": _safe_float(getattr(settings_obj, "PAPER_INITIAL_WALLET_USD", 10.0), 10.0),
                    "min_trade_amount_usd": _safe_float(getattr(settings_obj, "MIN_TRADE_AMOUNT_USD", 1.0), 1.0),
                    "max_token_age_hours": _safe_float(getattr(settings_obj, "MAX_TOKEN_AGE_HOURS_SCALP", getattr(settings_obj, "MAX_TOKEN_AGE_HOURS", 720.0)), 720.0),
                    "break_even_gain_pct": _safe_float(getattr(settings_obj, "BREAK_EVEN_GAIN_PCT", 100.0), 100.0),
                    "trailing_stop_drop_pct": _safe_float(getattr(settings_obj, "TRAILING_STOP_DROP_PCT", 12.0), 12.0),
                    "emergency_stop_loss_pct": _safe_float(getattr(settings_obj, "EMERGENCY_STOP_LOSS_PCT", 20.0), 20.0),
                    "max_slippage_pct": _safe_float(getattr(settings_obj, "MAX_SLIPPAGE_PCT", 1.5), 1.5),
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
                    "min_token_age_scalp_min": _safe_float(getattr(settings_obj, "MIN_TOKEN_AGE_HOURS_SCALP", 2.0), 2.0) * 60.0,
                    "min_token_age_swing_hours": _safe_float(getattr(settings_obj, "MIN_TOKEN_AGE_HOURS_SWING", 3.0), 3.0),
                    "max_token_age_swing_hours": _safe_float(getattr(settings_obj, "MAX_TOKEN_AGE_HOURS_SWING", 6.0), 6.0),
                    "min_volume_1h_usd": _safe_float(getattr(settings_obj, "MIN_VOLUME_1H_USD", 15000.0), 15000.0),
                    "min_buy_ratio_5m_pct": _safe_float(getattr(settings_obj, "MIN_BUY_RATIO_5M_PCT", 50.0), 50.0),
                    "min_price_change_5m_pct": _safe_float(getattr(settings_obj, "MIN_PRICE_CHANGE_5M_PCT", -2.0), -2.0),
                    "min_liquidity_swing_usd": _safe_float(getattr(settings_obj, "MIN_LIQUIDITY_SWING_USD", 20000.0), 20000.0),
                    "max_top10_holders_pct": _safe_float(getattr(settings_obj, "MAX_TOP10_HOLDERS_PCT", 15.0), 15.0),
                    "min_liquidity_usd": _safe_float(getattr(settings_obj, "MIN_LIQUIDITY_USD", 5000.0), 5000.0),
                }

        cfg: dict[str, Any] = {}
        if CONFIG_FILE.exists():
            try:
                raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    cfg = raw
            except Exception:
                pass

        return {
            "execution_mode": cfg.get("execution_mode", "PAPER") or "PAPER",
            "max_concurrent_positions": _safe_int(cfg.get("max_concurrent_positions"), 50),
            "paper_buy_amount_usd": _safe_float(cfg.get("paper_buy_amount_usd"), 1.0),
            "wallet_balance_usd": _safe_float(
                cfg.get("wallet_balance_usd", cfg.get("paper_initial_wallet_usd")),
                10.0,
            ),
            "paper_initial_wallet_usd": _safe_float(
                cfg.get("paper_initial_wallet_usd", cfg.get("wallet_balance_usd")),
                10.0,
            ),
            "min_trade_amount_usd": _safe_float(cfg.get("min_trade_amount_usd"), 1.0),
            "max_token_age_hours": _safe_float(cfg.get("max_token_age_hours"), 3.0),
            "break_even_gain_pct": _safe_float(cfg.get("break_even_gain_pct"), 100.0),
            "trailing_stop_drop_pct": _safe_float(cfg.get("trailing_stop_drop_pct"), 12.0),
            "emergency_stop_loss_pct": _safe_float(cfg.get("emergency_stop_loss_pct"), 20.0),
            "max_slippage_pct": _safe_float(cfg.get("max_slippage_pct"), 1.5),
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

    def _get_decoupled_initial_wallet(self) -> float:
        """Obtém a banca inicial configurada quando em modo desacoplado."""
        if CONFIG_FILE.exists():
            try:
                raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    if "wallet_balance_usd" in raw and raw["wallet_balance_usd"] is not None:
                        return _safe_float(raw["wallet_balance_usd"], 10.0)
                    if "paper_initial_wallet_usd" in raw and raw["paper_initial_wallet_usd"] is not None:
                        return _safe_float(raw["paper_initial_wallet_usd"], 10.0)
            except Exception:
                pass
        if SESSION_FILE.exists():
            try:
                sess_data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
                if isinstance(sess_data, dict) and sess_data.get("initial_wallet_usd") is not None:
                    return _safe_float(sess_data["initial_wallet_usd"], 10.0)
            except Exception:
                pass
        return 10.0

    def _get_active_initial_wallet(self) -> float:
        """Retorna a banca inicial configurada da sessão."""
        if self.orchestrator:
            settings_obj = getattr(self.orchestrator, "settings", None)
            return float(getattr(settings_obj, "PAPER_INITIAL_WALLET_USD", 10.0))
        return self._get_decoupled_initial_wallet()

    def _resolve_initial_wallet_and_cash(self) -> tuple[float, float | None]:
        """Resolve banca inicial e saldo em caixa atual (sincronamente para execução em worker thread)."""
        initial_wallet = self._get_active_initial_wallet()
        if self.orchestrator:
            engine = getattr(self.orchestrator, "execution_engine", None)
            current_cash = float(engine.balance_usd) if engine and hasattr(engine, "balance_usd") else initial_wallet
            return initial_wallet, current_cash

        _, _, wallet_usd, _ = self._get_bot_status()
        current_cash = wallet_usd if wallet_usd > 0.0 else initial_wallet
        return initial_wallet, current_cash

    async def handle_summary(self, _request: web.Request) -> web.Response:
        """Retorna resumo consolidado de métricas e KPIs filtrado pela sessão ativa."""
        try:
            session_start = self._get_session_start()
            tokens_summary = await self.tokens_repo.get_tokens_summary(since=session_start)

            initial_wallet, current_cash = await asyncio.to_thread(self._resolve_initial_wallet_and_cash)

            pnl_summary = await self.positions_repo.get_pnl_summary(
                initial_wallet_usd=initial_wallet,
                current_cash_usd=current_cash,
            )

            payload = {
                "status": "success",
                "data": {
                    "pnl": pnl_summary,
                    "scanner": tokens_summary,
                    "session_mode": "PAPER" if session_start else "ALL",
                    "waiting_tokens_count": len(self._get_waiting_tokens()),
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
        """Retorna posições recentes com detalhes contábeis e filtro opcional de status (open/closed)."""
        try:
            limit_param = request.query.get("limit", "100")
            limit = int(limit_param) if limit_param.isdigit() else 100
            status_filter = request.query.get("status")
            positions = await self.positions_repo.get_all_positions(
                limit=limit,
                status_filter=status_filter,
            )

            # Enriquecimento em tempo real com memória de execuções ativas e cotações
            active_map: dict[int, Any] = {}
            if self.orchestrator and hasattr(self.orchestrator, "position_tracker"):
                active_map = getattr(self.orchestrator.position_tracker, "active_positions", {})

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
            orders = await self.orders_repo.get_recent_orders(limit=limit)

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
            session_start = self._get_session_start() if (session_only or not all_time) else None

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

    async def handle_bot_status(self, _request: web.Request) -> web.Response:
        """Retorna o estado operacional do bot e saldo em carteira."""
        is_running, is_paused, wallet_usd, active_positions_count = self._get_bot_status()
        settings_dict = self._get_active_settings()
        initial_wallet = self._get_active_initial_wallet()

        payload = {
            "status": "success",
            "data": {
                "is_running": is_running,
                "is_paused": is_paused,
                "wallet_balance_usd": wallet_usd,
                "initial_wallet_usd": initial_wallet,
                "active_positions_count": active_positions_count,
                "waiting_tokens_count": len(self._get_waiting_tokens()),
                "settings": settings_dict,
            },
        }
        return web.json_response(payload, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    async def handle_waiting_tokens(self, _request: web.Request) -> web.Response:
        """Retorna lista de tokens aprovados aguardando liberação de slots."""
        tokens = await asyncio.to_thread(self._get_waiting_tokens)
        return web.json_response(
            {"status": "success", "data": tokens},
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    async def handle_bot_start(self, _request: web.Request) -> web.Response:
        """Inicia ou reexecuta o bot."""
        if self.orchestrator:
            is_running = bool(getattr(self.orchestrator, "is_running", False))
            if is_running:
                return web.json_response({
                    "status": "info",
                    "message": "O bot já está em execução ativa.",
                    "is_running": True,
                })

            logger.info("▶️ [DASHBOARD] Reexecutando orquestrador do bot no mesmo processo...")
            self.orchestrator.stop_event = asyncio.Event()
            await self.orchestrator.initialize()
            asyncio.create_task(self.orchestrator.start())
            return web.json_response({
                "status": "success",
                "message": "Bot executado novamente com sucesso!",
                "is_running": True,
            })

        # Modo desacoplado: verifica se já está ativo via status
        is_running, _, _, _ = self._get_bot_status()
        if is_running:
            return web.json_response({
                "status": "info",
                "message": "O bot já está em execução ativa.",
                "is_running": True,
            })

        try:
            out_file = await asyncio.to_thread(_open_runtime_log_file)
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "main.py",
                stdout=out_file,
                stderr=out_file,
            )
            DashboardServer._bot_subprocess = proc
            logger.info("🚀 [BOT EXECUTADO NOVAMENTE] Subprocesso iniciado (PID: %d). Logs em data/bot_runtime.log", proc.pid)
            return web.json_response({
                "status": "success",
                "message": f"Bot executado com sucesso (PID: {proc.pid})!",
                "is_running": True,
            })
        except Exception as exc:
            logger.error("Falha ao iniciar processo do bot: %s", exc)
            return web.json_response({"status": "error", "message": f"Erro ao iniciar o bot: {exc}"}, status=500)

    async def handle_bot_pause(self, _request: web.Request) -> web.Response:
        """Pausa a abertura de novas posições pelo bot."""
        if self.orchestrator:
            self.orchestrator.pause()
        else:
            self._write_ipc_command("pause")
        return web.json_response({"status": "success", "message": "Bot pausado com sucesso.", "is_paused": True})

    async def handle_bot_resume(self, _request: web.Request) -> web.Response:
        """Retoma as operações normais do bot."""
        if self.orchestrator:
            self.orchestrator.resume()
        else:
            self._write_ipc_command("resume")
        return web.json_response({"status": "success", "message": "Bot retomado com sucesso.", "is_paused": False})

    async def handle_bot_restart(self, request: web.Request) -> web.Response:
        """Reinicia a simulação: limpa posições/ordens, zera sequência para #1 e reinicia sessão."""
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
            bal_float = float(new_balance) if new_balance is not None else self._get_decoupled_initial_wallet()
            await asyncio.to_thread(self._reset_paper_session_file, bal_float)
            cfg = self._get_active_settings()
            cfg["wallet_balance_usd"] = bal_float
            cfg["paper_initial_wallet_usd"] = bal_float
            await asyncio.to_thread(self._save_config_file, cfg)
            try:
                if STATUS_FILE.exists():
                    st_data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
                    st_data["wallet_balance_usd"] = bal_float
                    st_data["initial_wallet_usd"] = bal_float
                    STATUS_FILE.write_text(json.dumps(st_data, indent=2), encoding="utf-8")
            except Exception:
                pass
            self._write_ipc_command(
                "restart",
                {
                    "wallet_balance_usd": bal_float,
                    "initial_wallet_usd": bal_float,
                    "paper_initial_wallet_usd": bal_float,
                },
            )

        active_bal = float(new_balance) if new_balance is not None else self._get_active_initial_wallet()
        return web.json_response({
            "status": "success",
            "message": "Simulação reiniciada com sucesso. Trades limpos e contador iniciado em #1.",
            "data": {
                "wallet_balance_usd": active_bal,
                "initial_wallet_usd": active_bal,
            },
        })

    async def handle_bot_stop(self, _request: web.Request) -> web.Response:
        """Encerra com segurança a execução do bot."""
        if self.orchestrator:
            await self.orchestrator.stop()
        else:
            self._write_ipc_command("stop")
        return web.json_response({"status": "success", "message": "Comando de encerramento enviado."})

    async def handle_bot_config(self, request: web.Request) -> web.Response:
        """Atualiza dinamicamente as configurações de risco e execução do bot."""
        try:
            raw_payload = await request.json()
            payload = {k: v for k, v in raw_payload.items() if v is not None}
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
                    self._update_paper_session_initial_wallet(bal_val)
                    if not self.orchestrator and STATUS_FILE.exists():
                        try:
                            st_data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
                            st_data["wallet_balance_usd"] = bal_val
                            st_data["initial_wallet_usd"] = bal_val
                            STATUS_FILE.write_text(json.dumps(st_data, indent=2), encoding="utf-8")
                        except Exception:
                            pass
                except (ValueError, TypeError):
                    pass

            if self.orchestrator:
                updated = self.orchestrator.update_dynamic_config(payload)
            else:
                cfg = self._get_active_settings()
                cfg.update(payload)
                await asyncio.to_thread(self._save_config_file, cfg)
                self._write_ipc_command("reload_config", payload)
                updated = cfg
            return web.json_response({"status": "success", "message": "Configurações atualizadas.", "data": updated})
        except Exception as exc:
            logger.error("Erro ao atualizar configurações: %s", exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=400)

    async def handle_wallet_deposit(self, request: web.Request) -> web.Response:
        """Adiciona saldo à carteira do modo de simulação."""
        try:
            payload = await request.json()
            amount_usd = Decimal(str(payload.get("amount_usd", "10.0")))
            if self.orchestrator:
                new_balance = self.orchestrator.deposit_wallet(amount_usd)
            else:
                cfg = self._get_active_settings()
                curr_balance = Decimal(str(cfg.get("wallet_balance_usd", "5.0")))
                new_balance = curr_balance + amount_usd
                cfg["wallet_balance_usd"] = float(new_balance)
                await asyncio.to_thread(self._save_config_file, cfg)
                self._write_ipc_command("deposit", {"amount_usd": float(amount_usd)})
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

            if self.orchestrator:
                success = await self.orchestrator.close_position_manually(position_id)
                if not success:
                    return web.json_response(
                        {"status": "error", "message": f"Não foi possível fechar a posição #{position_id} (pode já estar fechada)."},
                        status=400,
                    )
            else:
                self._write_ipc_command("close_position", {"position_id": position_id})

            await self.broadcast_event("trade", {"action": "manual_close", "position_id": position_id})
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

            if self.orchestrator:
                success, msg = await self.orchestrator.open_position_manually(position_id=position_id)
                if not success:
                    return web.json_response({"status": "error", "message": msg}, status=400)
                resp_msg = msg
            else:
                self._write_ipc_command("buy_more", {"position_id": position_id})
                resp_msg = f"Comando de nova compra para o ativo #{position_id} enviado ao bot com sucesso."

            await self.broadcast_event("trade", {"action": "manual_buy", "position_id": position_id})
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
        resp = await handler(request)
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
    app.router.add_get("/api/waiting_tokens", server.handle_waiting_tokens)
    app.router.add_get("/api/waiting-tokens", server.handle_waiting_tokens)

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
