"""
Ponto de Entrada Unificado do Vertex-bot.
Orquestra os pipelines assíncronos de Scanner, Auditoria de Segurança,
Execução (Paper/Live) e Gerenciamento Contínuo de Risco.
"""

import argparse
import asyncio
import json
import os
import signal
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from src.config.settings import Settings, get_settings
from src.database.connection import DatabaseManager
from src.database.models import PositionState, TokenMetadata
from src.database.repository import OrdersRepository, PositionsRepository, TokensRepository
from src.engine.paper import PaperExecutionEngine
from src.engine.price_feed import DexScreenerPriceFeed
from src.engine.reentry import ReentryRiskManager
from src.engine.risk import RiskManager
from src.engine.tracker import PositionTracker
from src.scanner.client import ResilientRPCClient
from src.scanner.listener import create_scanner
from src.security.market_dynamics import MarketDynamicsValidator
from src.security.validator import SecurityValidator
from src.utils.logger import setup_logger
from src.utils.metrics import TelemetryCollector

logger = setup_logger("vertex.main")


class VertexBotOrchestrator:
    """Orquestrador central do ciclo de vida assíncrono do Vertex-bot."""

    def __init__(self, settings: Settings) -> None:
        self.settings: Settings = settings
        self.db: DatabaseManager = DatabaseManager(settings.SQLITE_DB_PATH)
        self.tokens_repo: TokensRepository = TokensRepository(self.db)
        self.positions_repo: PositionsRepository = PositionsRepository(self.db)
        self.orders_repo: OrdersRepository = OrdersRepository(self.db)

        # Provedor de Cotações Contínuas
        self.price_feed: DexScreenerPriceFeed = DexScreenerPriceFeed(
            base_url=settings.DEXSCREENER_API_BASE_URL,
        )

        # Cliente RPC Resiliente
        self.rpc_client: ResilientRPCClient = ResilientRPCClient(
            primary_url=settings.PRIMARY_RPC_HTTP_URL,
            secondary_url=settings.SECONDARY_RPC_HTTP_URL,
        )

        # Camada de Segurança
        # Camada de Segurança e Dinâmica de Mercado
        self.market_validator: MarketDynamicsValidator = MarketDynamicsValidator(
            min_volume_1h_usd=settings.MIN_VOLUME_1H_USD,
            min_buy_ratio_5m_pct=settings.MIN_BUY_RATIO_5M_PCT,
            min_price_change_5m_pct=settings.MIN_PRICE_CHANGE_5M_PCT,
            min_age_hours_scalp=float(settings.MIN_TOKEN_AGE_HOURS_SCALP),
            max_age_hours_scalp=float(settings.MAX_TOKEN_AGE_HOURS_SCALP),
            min_age_hours_swing=float(settings.MIN_TOKEN_AGE_HOURS_SWING),
            max_age_hours_swing=float(settings.MAX_TOKEN_AGE_HOURS_SWING),
            min_liquidity_scalp_usd=settings.MIN_LIQUIDITY_USD,
            min_liquidity_swing_usd=settings.MIN_LIQUIDITY_SWING_USD,
            max_liquidity_usd=settings.MAX_LIQUIDITY_USD,
            max_seller_to_buyer_ratio=settings.MAX_SELLER_TO_BUYER_RATIO,
            min_liquidity_to_volume_ratio=settings.MIN_LIQUIDITY_TO_VOLUME_RATIO,
            min_unique_traders_24h=settings.MIN_UNIQUE_TRADERS_24H,
            max_parabolic_1h_gain_pct=settings.MAX_PARABOLIC_1H_GAIN_PCT,
        )
        self.validator: SecurityValidator = SecurityValidator(
            tokens_repo=self.tokens_repo,
            rpc_client=self.rpc_client,
            min_liquidity_usd=settings.MIN_LIQUIDITY_USD,
            max_top10_pct=float(settings.MAX_TOP10_HOLDERS_PCT),
            max_tax_pct=float(settings.MAX_BUY_TAX_PCT),
            market_validator=self.market_validator,
            strategy_mode=settings.TRADING_STRATEGY_MODE,
        )

        # Camada de Execução (Paper Trading por padrão)
        self.execution_engine: PaperExecutionEngine = PaperExecutionEngine(
            positions_repo=self.positions_repo,
            orders_repo=self.orders_repo,
            initial_balance_usd=settings.PAPER_INITIAL_WALLET_USD,
            simulated_latency_ms=settings.PAPER_SIMULATED_LATENCY_MS,
            trailing_drop_pct=settings.TRAILING_STOP_DROP_PCT / Decimal("100.0"),
            price_feed=self.price_feed,
        )

        # Gestão de Risco
        self.risk_manager: RiskManager = RiskManager(
            scalp_max_hold_seconds=float(getattr(settings, "SCALP_MAX_HOLD_MINUTES", 60.0)) * 60.0,
            scalp_target_gain_pct=getattr(settings, "SCALP_TARGET_GAIN_PCT", Decimal("100.0")),
            swing_max_hold_seconds=float(getattr(settings, "SWING_MAX_HOLD_HOURS", 24.0)) * 3600.0,
            swing_target_gain_pct=getattr(settings, "SWING_TARGET_GAIN_PCT", Decimal("2000.0")),
            swing_max_hourly_drop_pct=getattr(settings, "SWING_MAX_HOURLY_DROP_PCT", Decimal("15.0")),
            trailing_drop_pct=settings.TRAILING_STOP_DROP_PCT / Decimal("100.0"),
            emergency_stop_loss_pct=settings.EMERGENCY_STOP_LOSS_PCT / Decimal("100.0"),
            swing_initial_stop_loss_pct=settings.SWING_INITIAL_STOP_LOSS_PCT / Decimal("100.0"),
            swing_tier1_mult=settings.SWING_TIER1_TARGET_MULT,
            swing_tier2_mult=settings.SWING_TIER2_TARGET_MULT,
            swing_tier3_mult=settings.SWING_TIER3_TARGET_MULT,
            swing_tier4_mult=settings.SWING_TIER4_TARGET_MULT,
            swing_tier5_mult=getattr(settings, "SWING_TIER5_TARGET_MULT", Decimal("21.0")),
            swing_trailing_drop_pct=settings.SWING_TRAILING_DROP_PCT / Decimal("100.0"),
            break_even_gain_pct=settings.BREAK_EVEN_GAIN_PCT,
        )
        self.position_tracker: PositionTracker = PositionTracker(
            engine=self.execution_engine,
            positions_repo=self.positions_repo,
            risk_manager=self.risk_manager,
            on_position_closed=self._handle_position_closed,
        )

        # Telemetria Local
        self.telemetry: TelemetryCollector = TelemetryCollector()

        # Filas Assíncronas Desacopladas com Backpressure
        self.detection_queue: asyncio.Queue[TokenMetadata] = asyncio.Queue(maxsize=1000)

        # Scanner (Indexado estilo Photon/DexScreener ou RAW_RPC via WebSocket)
        self.scanner = create_scanner(
            settings=settings,
            detection_queue=self.detection_queue,
        )

        # Gestor de Reentrada Inteligente (Anti-Falling-Knife)
        self.reentry_manager: ReentryRiskManager = ReentryRiskManager(
            trailing_cooloff_sec=float(getattr(settings, "REENTRY_TRAILING_COOLOFF_SEC", 300.0)),
            stoploss_cooloff_sec=float(getattr(settings, "REENTRY_STOPLOSS_COOLOFF_SEC", 1800.0)),
            min_bounce_pct=getattr(settings, "REENTRY_MIN_BOUNCE_PCT", Decimal("3.0")),
            max_post_exit_drop_pct=getattr(settings, "REENTRY_MAX_DROP_PCT", Decimal("25.0")),
            min_liquidity_usd=settings.MIN_LIQUIDITY_USD,
        )

        self.is_running: bool = False
        self.is_paused: bool = False
        self.run_mock_stream: bool = False
        self._tasks: list[asyncio.Task[None]] = []
        self.stop_event: asyncio.Event | None = None
        self._last_handled_ipc_id: str | None = None

        # Fila de Espera de Tokens Aprovados (Aguardando Vaga ou Saldo)
        self.waiting_tokens: dict[str, dict[str, Any]] = {}
        self._load_waiting_tokens()

        # Restaura configurações persistidas anteriormente se existirem em disco
        self._load_persisted_config()

    def _get_waiting_tokens_path(self) -> Path:
        """Caminho do arquivo JSON de tokens aprovados em fila de espera."""
        db_path = Path(getattr(self.settings, "SQLITE_DB_PATH", "data/vertex_bot.db"))
        if db_path.parent != Path("data") and db_path.parent != Path("."):
            return db_path.parent / "waiting_tokens.json"
        return Path("data/waiting_tokens.json")

    def _load_waiting_tokens(self) -> None:
        """Carrega tokens aprovados em espera salvos em disco."""
        w_file = self._get_waiting_tokens_path()
        if not w_file.exists():
            return
        try:
            raw = json.loads(w_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.waiting_tokens = raw
                logger.info("⏳ [FILA DE ESPERA RESTAURADA] %d tokens carregados de %s", len(self.waiting_tokens), w_file)
        except Exception as exc:
            logger.debug("Falha ao ler %s: %s", w_file, exc)

    def _save_waiting_tokens(self) -> None:
        """Salva a fila de espera em waiting_tokens.json de forma atômica."""
        w_file = self._get_waiting_tokens_path()
        try:
            w_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = w_file.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(self.waiting_tokens, indent=2), encoding="utf-8")
            tmp_path.replace(w_file)
        except Exception as exc:
            logger.debug("Falha ao salvar %s: %s", w_file, exc)

    def _get_config_path(self) -> Path:
        """Caminho do arquivo JSON de configurações persistidas."""
        db_path = Path(getattr(self.settings, "SQLITE_DB_PATH", "data/vertex_bot.db"))
        if db_path.parent != Path("data") and db_path.parent != Path("."):
            return db_path.parent / "bot_config.json"
        return Path("data/bot_config.json")

    def _get_paper_session_path(self) -> Path:
        """Caminho do arquivo de sessão simulada."""
        db_path = Path(getattr(self.settings, "SQLITE_DB_PATH", "data/vertex_bot.db"))
        if db_path.parent != Path("data") and db_path.parent != Path("."):
            return db_path.parent / "paper_session.json"
        return Path("data/paper_session.json")

    def _load_persisted_config(self) -> None:
        """Carrega e aplica configurações previamente salvas pelo usuário no Dashboard."""
        cfg_file = self._get_config_path()
        if not cfg_file.exists():
            return
        try:
            raw = json.loads(cfg_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                strat_mode = str(getattr(self.settings, "TRADING_STRATEGY_MODE", "DUAL")).upper()
                if raw.get("max_token_age_hours") == 3.0 and strat_mode in ("DUAL", "SCALP_ONLY"):
                    raw["max_token_age_hours"] = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SCALP", 720.0))
                self._apply_config_payload(raw, save_to_disk=False)
                logger.info("⚙️ [CONFIGURAÇÕES PERSISTIDAS RESTAURADAS] data/bot_config.json aplicado com sucesso.")
        except Exception as exc:
            logger.warning("Falha ao restaurar data/bot_config.json: %s", exc)

    def _save_persisted_config(self) -> None:
        """Salva as configurações atuais em data/bot_config.json para sobrevivência a reinicializações."""
        cfg_file = self._get_config_path()
        try:
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "paper_buy_amount_usd": float(self.settings.PAPER_BUY_AMOUNT_USD),
                "max_concurrent_positions": int(self.settings.MAX_CONCURRENT_POSITIONS),
                "wallet_balance_usd": float(self.execution_engine.balance_usd),
                "paper_initial_wallet_usd": float(self.settings.PAPER_INITIAL_WALLET_USD),
                "min_trade_amount_usd": float(self.settings.MIN_TRADE_AMOUNT_USD),
                "max_token_age_hours": float(self.settings.MAX_TOKEN_AGE_HOURS),
                "break_even_gain_pct": float(self.settings.BREAK_EVEN_GAIN_PCT),
                "trailing_stop_drop_pct": float(self.settings.TRAILING_STOP_DROP_PCT),
                "emergency_stop_loss_pct": float(self.settings.EMERGENCY_STOP_LOSS_PCT),
                "max_slippage_pct": float(self.settings.MAX_SLIPPAGE_PCT),
                "reentry_trailing_cooloff_min": float(self.settings.REENTRY_TRAILING_COOLOFF_SEC) / 60.0,
                "reentry_stoploss_cooloff_min": float(self.settings.REENTRY_STOPLOSS_COOLOFF_SEC) / 60.0,
                "reentry_min_bounce_pct": float(self.settings.REENTRY_MIN_BOUNCE_PCT),
                "trading_strategy_mode": str(self.settings.TRADING_STRATEGY_MODE),
                "scalp_max_hold_minutes": float(getattr(self.settings, "SCALP_MAX_HOLD_MINUTES", 60.0)),
                "scalp_target_gain_pct": float(getattr(self.settings, "SCALP_TARGET_GAIN_PCT", 100.0)),
                "swing_max_hold_hours": float(getattr(self.settings, "SWING_MAX_HOLD_HOURS", 24.0)),
                "swing_target_gain_pct": float(getattr(self.settings, "SWING_TARGET_GAIN_PCT", 2000.0)),
                "swing_max_hourly_drop_pct": float(getattr(self.settings, "SWING_MAX_HOURLY_DROP_PCT", 15.0)),
                "swing_initial_stop_loss_pct": float(self.settings.SWING_INITIAL_STOP_LOSS_PCT),
                "swing_tier1_mult": float(self.settings.SWING_TIER1_TARGET_MULT),
                "swing_tier2_mult": float(self.settings.SWING_TIER2_TARGET_MULT),
                "swing_tier3_mult": float(self.settings.SWING_TIER3_TARGET_MULT),
                "swing_tier4_mult": float(self.settings.SWING_TIER4_TARGET_MULT),
                "swing_tier5_mult": float(getattr(self.settings, "SWING_TIER5_TARGET_MULT", 21.0)),
                "swing_trailing_drop_pct": float(self.settings.SWING_TRAILING_DROP_PCT),
                "min_token_age_scalp_min": float(getattr(self.settings, "MIN_TOKEN_AGE_HOURS_SCALP", 2.0)) * 60.0,
                "min_token_age_swing_hours": float(getattr(self.settings, "MIN_TOKEN_AGE_HOURS_SWING", 3.0)),
                "max_token_age_swing_hours": float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SWING", 6.0)),
                "min_volume_1h_usd": float(getattr(self.settings, "MIN_VOLUME_1H_USD", 15000.0)),
                "min_buy_ratio_5m_pct": float(getattr(self.settings, "MIN_BUY_RATIO_5M_PCT", 50.0)),
                "min_price_change_5m_pct": float(getattr(self.settings, "MIN_PRICE_CHANGE_5M_PCT", -2.0)),
                "min_liquidity_swing_usd": float(getattr(self.settings, "MIN_LIQUIDITY_SWING_USD", 20000.0)),
                "max_top10_holders_pct": float(getattr(self.settings, "MAX_TOP10_HOLDERS_PCT", 15.0)),
                "min_liquidity_usd": float(getattr(self.settings, "MIN_LIQUIDITY_USD", 5000.0)),
            }
            cfg_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            logger.info("💾 Configurações persistidas salvas em data/bot_config.json")
        except Exception as exc:
            logger.warning("Falha ao gravar data/bot_config.json: %s", exc)

    async def initialize(self) -> None:
        """Inicializa banco de dados e carrega posições abertas pré-existentes."""
        await self.db.initialize()

        # No modo PAPER, inicia sempre um novo teste de simulação do zero (limpando posições e ordens),
        # mas preservando os tokens_catalogados (inteligência de triagem e rejeições).
        if self.settings.EXECUTION_MODE == "PAPER":
            logger.info("Modo PAPER: iniciando novo teste simulado limpo (zerando posições e ordens anteriores)...")
            await self.positions_repo.clear_paper_trading_data()
            self._write_paper_session_state()

        open_positions = await self.positions_repo.get_open_positions()
        for pos in open_positions:
            await self.position_tracker.register_position(pos)
        await self.reconcile_wallet_balance()
        logger.info(
            "Inicialização concluída. Modo: %s | Posições ativas restauradas: %d",
            self.settings.EXECUTION_MODE,
            len(open_positions),
        )

    def _write_paper_session_state(self, initial_balance: Decimal | None = None) -> None:
        """Salva timestamp de início e banca inicial da sessão simulada para zeragem visual no Dashboard."""
        try:
            state_file = self._get_paper_session_path()
            state_file.parent.mkdir(parents=True, exist_ok=True)
            bal = float(initial_balance) if initial_balance is not None else float(self.settings.PAPER_INITIAL_WALLET_USD)
            payload = {
                "session_start": datetime.now(UTC).isoformat(),
                "mode": "PAPER",
                "initial_wallet_usd": bal,
            }
            state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            logger.info("Sessão simulada registrada em %s: %s (Banca Inicial: $%.2f)", state_file, payload["session_start"], bal)
        except Exception as exc:
            logger.debug("Não foi possível gravar paper_session.json: %s", exc)

    def pause(self) -> None:
        """Pausa a abertura de novas posições pelo bot."""
        self.is_paused = True
        logger.info("⏸️ [BOT PAUSADO] Novas entradas suspensas. Monitoramento de posições abertas permanece ativo.")

    def resume(self) -> None:
        """Retoma as operações normais do bot."""
        self.is_paused = False
        logger.info("▶️ [BOT RETOMADO] Abertura de posições e triagem reativadas com sucesso.")

    def deposit_wallet(self, amount_usd: Decimal) -> Decimal:
        """Adiciona capital simulado à carteira no modo PAPER."""
        self.execution_engine.balance_usd += amount_usd
        self.settings.PAPER_INITIAL_WALLET_USD += amount_usd
        self._write_paper_session_state(initial_balance=self.settings.PAPER_INITIAL_WALLET_USD)
        self._save_persisted_config()
        logger.info(
            "💵 [DEPÓSITO SIMULADO] +$%.2f adicionados à carteira. Saldo atual: $%.2f (Banca Base: $%.2f)",
            amount_usd,
            self.execution_engine.balance_usd,
            self.settings.PAPER_INITIAL_WALLET_USD,
        )
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.create_task(self._try_fill_slots_from_waiting_queue())
        except RuntimeError:
            pass
        return self.execution_engine.balance_usd

    def _get_session_start_datetime(self) -> datetime | None:
        """Lê o timestamp de início da sessão simulada de data/paper_session.json."""
        state_file = self._get_paper_session_path()
        if not state_file.exists():
            return None
        try:
            raw = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("session_start"):
                return datetime.fromisoformat(raw["session_start"])
        except Exception:
            pass
        return None

    async def reconcile_wallet_balance(self) -> Decimal:
        """Reconcilia o saldo de caixa em memória garantindo exatidão contábil (Caixa = Banca + PnL - Alocado)."""
        if self.settings.EXECUTION_MODE != "PAPER":
            return self.execution_engine.balance_usd

        try:
            pnl_data = await self.positions_repo.get_pnl_summary(
                initial_wallet_usd=float(self.settings.PAPER_INITIAL_WALLET_USD),
            )
            active_capital = Decimal(str(pnl_data.get("active_capital_usd", 0.0)))
            realized_pnl = Decimal(str(pnl_data.get("total_pnl_usd", 0.0)))
            true_cash = max(Decimal("0.0"), self.settings.PAPER_INITIAL_WALLET_USD + realized_pnl - active_capital)
            self.execution_engine.balance_usd = true_cash
            self._write_heartbeat_sync(self.is_running)
            return true_cash
        except Exception as exc:
            logger.warning("Falha ao reconciliar saldo de carteira: %s", exc)
            return self.execution_engine.balance_usd

    async def restart_paper_session(self, new_balance: Decimal | None = None) -> None:
        """Reinicia a sessão simulada: limpa posições, ordens, zera IDs para #1 e reinicia balanço."""
        logger.info("🔄 [REINÍCIO DE SIMULAÇÃO] Limpando dados de trades e reiniciando sessão pelo Dashboard...")
        self.position_tracker.active_positions.clear()
        await self.positions_repo.clear_paper_trading_data()
        self.reentry_manager.clear_history()
        self.waiting_tokens.clear()
        self._save_waiting_tokens()

        target_balance = new_balance if new_balance is not None else self.settings.PAPER_INITIAL_WALLET_USD
        self.settings.PAPER_INITIAL_WALLET_USD = target_balance
        self.execution_engine.balance_usd = target_balance
        self._write_paper_session_state(initial_balance=target_balance)
        self._save_persisted_config()
        self._write_heartbeat_sync(self.is_running)
        logger.info("Sessão simulada reiniciada com sucesso. Saldo disponível: $%.2f", target_balance)

    def update_dynamic_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Atualiza dinamicamente as configurações de risco e execução do bot."""
        res = self._apply_config_payload(payload, save_to_disk=True)
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.create_task(self._try_fill_slots_from_waiting_queue())
        except RuntimeError:
            pass
        return res

    def _update_scanner_max_age(self, max_age_hours: float) -> None:
        """Propaga o limite de idade máxima do token para os scanners ativos."""
        self.settings.MAX_TOKEN_AGE_HOURS = max_age_hours
        if hasattr(self, "scanner"):
            scanners = getattr(self.scanner, "scanners", [self.scanner])
            for s in scanners:
                if hasattr(s, "max_age_hours"):
                    s.max_age_hours = max_age_hours
                    logger.info(
                        "⚙️ [SCANNER IDADE MÁXIMA ATUALIZADA] Scanner configurado para tokens <= %.1fh.",
                        max_age_hours,
                    )

    def _update_scanner_min_age(self, min_age_hours: float) -> None:
        """Propaga o limite de idade mínima do token para os scanners ativos e incubadora."""
        self.settings.MIN_TOKEN_AGE_HOURS = min_age_hours
        if hasattr(self, "scanner"):
            scanners = getattr(self.scanner, "scanners", [self.scanner])
            for s in scanners:
                if hasattr(s, "min_age_hours"):
                    s.min_age_hours = min_age_hours
                    logger.info(
                        "⚙️ [SCANNER IDADE MÍNIMA ATUALIZADA] Scanner configurado para tokens >= %.2fh (%.0f min).",
                        min_age_hours,
                        min_age_hours * 60.0,
                    )

    def _update_scanner_swing_age(self, min_age: float, max_age: float) -> None:
        """Propaga a janela de Swing para os scanners ativos e incubadora de Swing."""
        self.settings.MIN_TOKEN_AGE_HOURS_SWING = min_age
        self.settings.MAX_TOKEN_AGE_HOURS_SWING = max_age
        if hasattr(self, "scanner"):
            scanners = getattr(self.scanner, "scanners", [self.scanner])
            for s in scanners:
                if hasattr(s, "min_age_hours_swing"):
                    s.min_age_hours_swing = min_age
                if hasattr(s, "max_age_hours_swing"):
                    s.max_age_hours_swing = max_age
            logger.info(
                "⚙️ [SCANNER JANELA SWING ATUALIZADA] Scanner configurado para Swing entre %.1fh e %.1fh.",
                min_age,
                max_age,
            )

    def _apply_execution_config(self, payload: dict[str, Any]) -> None:
        """Aplica parâmetros de execução e dimensionamento de ordens."""
        if payload.get("paper_buy_amount_usd") is not None:
            buy_val = Decimal(str(payload["paper_buy_amount_usd"]))
            if buy_val > Decimal("0.0"):
                self.settings.PAPER_BUY_AMOUNT_USD = buy_val
                self.settings.MIN_TRADE_AMOUNT_USD = min(self.settings.MIN_TRADE_AMOUNT_USD, buy_val)
        if payload.get("max_concurrent_positions") is not None:
            self.settings.MAX_CONCURRENT_POSITIONS = int(payload["max_concurrent_positions"])
        wallet_val_raw = payload.get("paper_initial_wallet_usd") if payload.get("paper_initial_wallet_usd") is not None else (
            payload.get("wallet_balance_usd") if payload.get("wallet_balance_usd") is not None else payload.get("initial_wallet_usd")
        )
        if wallet_val_raw is not None:
            try:
                new_wallet = Decimal(str(wallet_val_raw))
                if new_wallet > Decimal("0.0"):
                    self.settings.PAPER_INITIAL_WALLET_USD = new_wallet
                    allocated = sum(
                        (p.allocated_capital_usd for p in self.position_tracker.active_positions.values()),
                        Decimal("0.0"),
                    )
                    self.execution_engine.balance_usd = max(Decimal("0.0"), new_wallet - allocated)
                    self._write_paper_session_state(initial_balance=new_wallet)
                    self._write_heartbeat_sync(self.is_running)
                    logger.info("💰 Saldo da carteira atualizado para $%.2f (Disponível: $%.2f, Alocado: $%.2f)", new_wallet, self.execution_engine.balance_usd, allocated)
            except Exception as exc:
                logger.warning("Falha ao atualizar saldo da carteira: %s", exc)
        if payload.get("min_trade_amount_usd") is not None:
            req_min = Decimal(str(payload["min_trade_amount_usd"]))
            self.settings.MIN_TRADE_AMOUNT_USD = min(req_min, self.settings.PAPER_BUY_AMOUNT_USD)
        if payload.get("max_slippage_pct") is not None:
            self.settings.MAX_SLIPPAGE_PCT = Decimal(str(payload["max_slippage_pct"]))
            self.execution_engine.max_slippage_pct = self.settings.MAX_SLIPPAGE_PCT / Decimal("100.0")
        if payload.get("max_token_age_hours") is not None:
            self.settings.MAX_TOKEN_AGE_HOURS = float(payload["max_token_age_hours"])
            self._update_scanner_max_age(self.settings.MAX_TOKEN_AGE_HOURS)

    def _apply_risk_config(self, payload: dict[str, Any]) -> None:
        """Aplica parâmetros de risco (break-even, trailing stop, stop loss, scalp timing/alvo, dual-track e swing ratchet)."""
        if "scalp_max_hold_minutes" in payload and payload["scalp_max_hold_minutes"] is not None:
            self.settings.SCALP_MAX_HOLD_MINUTES = float(payload["scalp_max_hold_minutes"])
            self.risk_manager.scalp_max_hold_seconds = self.settings.SCALP_MAX_HOLD_MINUTES * 60.0
        if "scalp_target_gain_pct" in payload and payload["scalp_target_gain_pct"] is not None:
            self.settings.SCALP_TARGET_GAIN_PCT = Decimal(str(payload["scalp_target_gain_pct"]))
            self.risk_manager.scalp_target_gain_pct = self.settings.SCALP_TARGET_GAIN_PCT
            self.risk_manager.scalp_target_multiplier = Decimal("1.0") + (self.settings.SCALP_TARGET_GAIN_PCT / Decimal("100.0"))
        if "break_even_gain_pct" in payload and payload["break_even_gain_pct"] is not None:
            self.settings.BREAK_EVEN_GAIN_PCT = Decimal(str(payload["break_even_gain_pct"]))
            self.risk_manager.break_even_multiplier = Decimal("1.0") + (self.settings.BREAK_EVEN_GAIN_PCT / Decimal("100.0"))
        if "trailing_stop_drop_pct" in payload and payload["trailing_stop_drop_pct"] is not None:
            self.settings.TRAILING_STOP_DROP_PCT = Decimal(str(payload["trailing_stop_drop_pct"]))
            new_trailing_drop = self.settings.TRAILING_STOP_DROP_PCT / Decimal("100.0")
            self.risk_manager.trailing_drop_pct = new_trailing_drop
            self.execution_engine.trailing_drop_pct = new_trailing_drop
            # Atualiza posições ativas em memória com a nova taxa de trailing stop
            for pos in self.position_tracker.active_positions.values():
                if getattr(pos, "strategy_type", "SCALP") == "SCALP":
                    pos.trailing_drop_pct = new_trailing_drop
                    if pos.highest_price_seen > Decimal("0.0"):
                        pos.trailing_stop_price = pos.highest_price_seen * (Decimal("1.0") - new_trailing_drop)
        if "emergency_stop_loss_pct" in payload and payload["emergency_stop_loss_pct"] is not None:
            self.settings.EMERGENCY_STOP_LOSS_PCT = Decimal(str(payload["emergency_stop_loss_pct"]))
            self.risk_manager.emergency_stop_multiplier = Decimal("1.0") - (self.settings.EMERGENCY_STOP_LOSS_PCT / Decimal("100.0"))
        if "max_slippage_pct" in payload and payload["max_slippage_pct"] is not None:
            self.settings.MAX_SLIPPAGE_PCT = Decimal(str(payload["max_slippage_pct"]))
        if "max_top10_holders_pct" in payload and payload["max_top10_holders_pct"] is not None:
            self.settings.MAX_TOP10_HOLDERS_PCT = Decimal(str(payload["max_top10_holders_pct"]))
            self.validator.max_top10_pct = float(self.settings.MAX_TOP10_HOLDERS_PCT)
        if "min_liquidity_usd" in payload and payload["min_liquidity_usd"] is not None:
            self.settings.MIN_LIQUIDITY_USD = Decimal(str(payload["min_liquidity_usd"]))
            self.validator.min_liquidity_usd = self.settings.MIN_LIQUIDITY_USD

        self._apply_swing_risk_config(payload)

    def _apply_swing_risk_config(self, payload: dict[str, Any]) -> None:
        """Aplica parâmetros específicos da estratégia Dual-Track e Swing Ratchet."""
        if "trading_strategy_mode" in payload and payload["trading_strategy_mode"]:
            mode_val = str(payload["trading_strategy_mode"]).upper()
            if mode_val in ("DUAL", "SCALP_ONLY", "SWING_ONLY"):
                self.settings.TRADING_STRATEGY_MODE = mode_val  # type: ignore[assignment]
                if hasattr(self, "validator"):
                    self.validator.strategy_mode = mode_val
                if "max_token_age_hours" not in payload:
                    if mode_val == "SWING_ONLY":
                        self._update_scanner_min_age(float(self.settings.MIN_TOKEN_AGE_HOURS_SWING))
                        self._update_scanner_max_age(float(self.settings.MAX_TOKEN_AGE_HOURS_SWING))
                    elif mode_val == "SCALP_ONLY":
                        self._update_scanner_min_age(float(self.settings.MIN_TOKEN_AGE_HOURS_SCALP))
                        self._update_scanner_max_age(float(self.settings.MAX_TOKEN_AGE_HOURS_SCALP))
                    else:  # "DUAL"
                        self._update_scanner_min_age(float(self.settings.MIN_TOKEN_AGE_HOURS_SCALP))
                        self._update_scanner_max_age(float(self.settings.MAX_TOKEN_AGE_HOURS_SCALP))
        if "swing_max_hold_hours" in payload and payload["swing_max_hold_hours"] is not None:
            self.settings.SWING_MAX_HOLD_HOURS = float(payload["swing_max_hold_hours"])
            self.risk_manager.swing_max_hold_seconds = self.settings.SWING_MAX_HOLD_HOURS * 3600.0
        if "swing_target_gain_pct" in payload and payload["swing_target_gain_pct"] is not None:
            self.settings.SWING_TARGET_GAIN_PCT = Decimal(str(payload["swing_target_gain_pct"]))
            self.risk_manager.swing_target_gain_pct = self.settings.SWING_TARGET_GAIN_PCT
            self.risk_manager.swing_target_multiplier = Decimal("1.0") + (self.settings.SWING_TARGET_GAIN_PCT / Decimal("100.0"))
        if "swing_max_hourly_drop_pct" in payload and payload["swing_max_hourly_drop_pct"] is not None:
            self.settings.SWING_MAX_HOURLY_DROP_PCT = Decimal(str(payload["swing_max_hourly_drop_pct"]))
            self.risk_manager.swing_max_hourly_drop_pct = self.settings.SWING_MAX_HOURLY_DROP_PCT
        if "swing_initial_stop_loss_pct" in payload and payload["swing_initial_stop_loss_pct"] is not None:
            self.settings.SWING_INITIAL_STOP_LOSS_PCT = Decimal(str(payload["swing_initial_stop_loss_pct"]))
            pct_val = self.settings.SWING_INITIAL_STOP_LOSS_PCT / Decimal("100.0")
            if Decimal("0.0") < pct_val < Decimal("1.0"):
                self.risk_manager.swing_stop_multiplier = Decimal("1.0") - pct_val
            else:
                self.risk_manager.swing_stop_multiplier = Decimal("0.0")
        if "swing_tier1_mult" in payload and payload["swing_tier1_mult"] is not None:
            self.settings.SWING_TIER1_TARGET_MULT = Decimal(str(payload["swing_tier1_mult"]))
            self.risk_manager.swing_tier1_mult = self.settings.SWING_TIER1_TARGET_MULT
        if "swing_tier2_mult" in payload and payload["swing_tier2_mult"] is not None:
            self.settings.SWING_TIER2_TARGET_MULT = Decimal(str(payload["swing_tier2_mult"]))
            self.risk_manager.swing_tier2_mult = self.settings.SWING_TIER2_TARGET_MULT
        if "swing_tier3_mult" in payload and payload["swing_tier3_mult"] is not None:
            self.settings.SWING_TIER3_TARGET_MULT = Decimal(str(payload["swing_tier3_mult"]))
            self.risk_manager.swing_tier3_mult = self.settings.SWING_TIER3_TARGET_MULT
        if "swing_tier4_mult" in payload and payload["swing_tier4_mult"] is not None:
            self.settings.SWING_TIER4_TARGET_MULT = Decimal(str(payload["swing_tier4_mult"]))
            self.risk_manager.swing_tier4_mult = self.settings.SWING_TIER4_TARGET_MULT
        if "swing_tier5_mult" in payload and payload["swing_tier5_mult"] is not None:
            self.settings.SWING_TIER5_TARGET_MULT = Decimal(str(payload["swing_tier5_mult"]))
            self.risk_manager.swing_tier5_mult = self.settings.SWING_TIER5_TARGET_MULT
        if "swing_trailing_drop_pct" in payload and payload["swing_trailing_drop_pct"] is not None:
            self.settings.SWING_TRAILING_DROP_PCT = Decimal(str(payload["swing_trailing_drop_pct"]))
            self.risk_manager.swing_trailing_drop_pct = self.settings.SWING_TRAILING_DROP_PCT / Decimal("100.0")

    def _apply_market_dynamics_config(self, payload: dict[str, Any]) -> None:
        """Aplica parâmetros de filtros quantitativos de mercado e anti-dump."""
        strat_mode = str(getattr(self.settings, "TRADING_STRATEGY_MODE", "DUAL")).upper()

        if "min_token_age_scalp_min" in payload and payload["min_token_age_scalp_min"] is not None:
            self.settings.MIN_TOKEN_AGE_HOURS_SCALP = float(payload["min_token_age_scalp_min"]) / 60.0
            if hasattr(self, "market_validator"):
                self.market_validator.min_age_hours_scalp = self.settings.MIN_TOKEN_AGE_HOURS_SCALP
            if strat_mode != "SWING_ONLY":
                self._update_scanner_min_age(self.settings.MIN_TOKEN_AGE_HOURS_SCALP)

        if "min_token_age_swing_hours" in payload and payload["min_token_age_swing_hours"] is not None:
            self.settings.MIN_TOKEN_AGE_HOURS_SWING = float(payload["min_token_age_swing_hours"])
            if hasattr(self, "market_validator"):
                self.market_validator.min_age_hours_swing = self.settings.MIN_TOKEN_AGE_HOURS_SWING
            if strat_mode == "SWING_ONLY":
                self._update_scanner_min_age(self.settings.MIN_TOKEN_AGE_HOURS_SWING)
            self._update_scanner_swing_age(self.settings.MIN_TOKEN_AGE_HOURS_SWING, float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SWING", 6.0)))

        if "max_token_age_swing_hours" in payload and payload["max_token_age_swing_hours"] is not None:
            self.settings.MAX_TOKEN_AGE_HOURS_SWING = float(payload["max_token_age_swing_hours"])
            if hasattr(self, "market_validator"):
                self.market_validator.max_age_hours_swing = self.settings.MAX_TOKEN_AGE_HOURS_SWING
            if strat_mode == "SWING_ONLY":
                self._update_scanner_max_age(self.settings.MAX_TOKEN_AGE_HOURS_SWING)
            self._update_scanner_swing_age(float(getattr(self.settings, "MIN_TOKEN_AGE_HOURS_SWING", 2.0)), self.settings.MAX_TOKEN_AGE_HOURS_SWING)
            self._update_scanner_swing_age(float(getattr(self.settings, "MIN_TOKEN_AGE_HOURS_SWING", 3.0)), self.settings.MAX_TOKEN_AGE_HOURS_SWING)

        if "min_volume_1h_usd" in payload and payload["min_volume_1h_usd"] is not None:
            self.settings.MIN_VOLUME_1H_USD = Decimal(str(payload["min_volume_1h_usd"]))
            if hasattr(self, "market_validator"):
                self.market_validator.min_volume_1h_usd = self.settings.MIN_VOLUME_1H_USD
        if "min_buy_ratio_5m_pct" in payload and payload["min_buy_ratio_5m_pct"] is not None:
            self.settings.MIN_BUY_RATIO_5M_PCT = Decimal(str(payload["min_buy_ratio_5m_pct"]))
            if hasattr(self, "market_validator"):
                self.market_validator.min_buy_ratio_5m_pct = self.settings.MIN_BUY_RATIO_5M_PCT
        if "min_price_change_5m_pct" in payload and payload["min_price_change_5m_pct"] is not None:
            self.settings.MIN_PRICE_CHANGE_5M_PCT = Decimal(str(payload["min_price_change_5m_pct"]))
            if hasattr(self, "market_validator"):
                self.market_validator.min_price_change_5m_pct = self.settings.MIN_PRICE_CHANGE_5M_PCT
        if "min_liquidity_swing_usd" in payload and payload["min_liquidity_swing_usd"] is not None:
            self.settings.MIN_LIQUIDITY_SWING_USD = Decimal(str(payload["min_liquidity_swing_usd"]))
            if hasattr(self, "market_validator"):
                self.market_validator.min_liquidity_swing_usd = self.settings.MIN_LIQUIDITY_SWING_USD
        if "trading_strategy_mode" in payload and hasattr(self, "validator"):
            self.validator.strategy_mode = str(self.settings.TRADING_STRATEGY_MODE)

    def _apply_config_payload(self, payload: dict[str, Any], save_to_disk: bool = True) -> dict[str, Any]:
        """Aplica alterações nas configurações em todos os módulos ativos (Settings, Risk, Engine, Reentry, Market)."""
        self._apply_execution_config(payload)
        self._apply_risk_config(payload)
        self._update_reentry_config(payload)
        self._apply_market_dynamics_config(payload)

        logger.info("⚙️ Configurações dinâmicas atualizadas com sucesso via Dashboard API.")
        if save_to_disk:
            self._save_persisted_config()

        logger.info("⚙️ Configurações dinâmicas aplicadas com sucesso.")
        return {
            "paper_buy_amount_usd": float(self.settings.PAPER_BUY_AMOUNT_USD),
            "max_concurrent_positions": self.settings.MAX_CONCURRENT_POSITIONS,
            "wallet_balance_usd": float(self.execution_engine.balance_usd),
            "max_token_age_hours": float(self.settings.MAX_TOKEN_AGE_HOURS),
            "break_even_gain_pct": float(self.settings.BREAK_EVEN_GAIN_PCT),
            "trailing_stop_drop_pct": float(self.settings.TRAILING_STOP_DROP_PCT),
            "emergency_stop_loss_pct": float(self.settings.EMERGENCY_STOP_LOSS_PCT),
            "max_slippage_pct": float(self.settings.MAX_SLIPPAGE_PCT),
            "reentry_trailing_cooloff_min": float(self.settings.REENTRY_TRAILING_COOLOFF_SEC) / 60.0,
            "reentry_stoploss_cooloff_min": float(self.settings.REENTRY_STOPLOSS_COOLOFF_SEC) / 60.0,
            "reentry_min_bounce_pct": float(self.settings.REENTRY_MIN_BOUNCE_PCT),
            "trading_strategy_mode": str(self.settings.TRADING_STRATEGY_MODE),
            "scalp_max_hold_minutes": float(getattr(self.settings, "SCALP_MAX_HOLD_MINUTES", 60.0)),
            "scalp_target_gain_pct": float(getattr(self.settings, "SCALP_TARGET_GAIN_PCT", 100.0)),
            "swing_max_hold_hours": float(getattr(self.settings, "SWING_MAX_HOLD_HOURS", 24.0)),
            "swing_target_gain_pct": float(getattr(self.settings, "SWING_TARGET_GAIN_PCT", 2000.0)),
            "swing_max_hourly_drop_pct": float(getattr(self.settings, "SWING_MAX_HOURLY_DROP_PCT", 15.0)),
            "swing_initial_stop_loss_pct": float(self.settings.SWING_INITIAL_STOP_LOSS_PCT),
            "swing_tier1_mult": float(self.settings.SWING_TIER1_TARGET_MULT),
            "swing_tier2_mult": float(self.settings.SWING_TIER2_TARGET_MULT),
            "swing_tier3_mult": float(self.settings.SWING_TIER3_TARGET_MULT),
            "swing_tier4_mult": float(self.settings.SWING_TIER4_TARGET_MULT),
            "swing_tier5_mult": float(getattr(self.settings, "SWING_TIER5_TARGET_MULT", 21.0)),
            "swing_trailing_drop_pct": float(self.settings.SWING_TRAILING_DROP_PCT),
            "min_token_age_scalp_min": float(getattr(self.settings, "MIN_TOKEN_AGE_HOURS_SCALP", 2.0)) * 60.0,
            "min_token_age_swing_hours": float(getattr(self.settings, "MIN_TOKEN_AGE_HOURS_SWING", 3.0)),
            "max_token_age_swing_hours": float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SWING", 6.0)),
            "min_volume_1h_usd": float(getattr(self.settings, "MIN_VOLUME_1H_USD", 15000.0)),
            "min_buy_ratio_5m_pct": float(getattr(self.settings, "MIN_BUY_RATIO_5M_PCT", 50.0)),
            "min_price_change_5m_pct": float(getattr(self.settings, "MIN_PRICE_CHANGE_5M_PCT", -2.0)),
            "min_liquidity_swing_usd": float(getattr(self.settings, "MIN_LIQUIDITY_SWING_USD", 20000.0)),
        }

    def _update_reentry_config(self, payload: dict[str, Any]) -> None:
        """Aplica parâmetros de reentrada inteligente dinamicamente."""
        reentry_kwargs: dict[str, Any] = {}
        if "reentry_trailing_cooloff_min" in payload and payload["reentry_trailing_cooloff_min"] is not None:
            reentry_kwargs["trailing_cooloff_sec"] = float(payload["reentry_trailing_cooloff_min"]) * 60.0
            self.settings.REENTRY_TRAILING_COOLOFF_SEC = reentry_kwargs["trailing_cooloff_sec"]
        if "reentry_stoploss_cooloff_min" in payload and payload["reentry_stoploss_cooloff_min"] is not None:
            reentry_kwargs["stoploss_cooloff_sec"] = float(payload["reentry_stoploss_cooloff_min"]) * 60.0
            self.settings.REENTRY_STOPLOSS_COOLOFF_SEC = reentry_kwargs["stoploss_cooloff_sec"]
        if "reentry_min_bounce_pct" in payload and payload["reentry_min_bounce_pct"] is not None:
            reentry_kwargs["min_bounce_pct"] = Decimal(str(payload["reentry_min_bounce_pct"]))
            self.settings.REENTRY_MIN_BOUNCE_PCT = reentry_kwargs["min_bounce_pct"]

        if reentry_kwargs:
            self.reentry_manager.update_config(**reentry_kwargs)

    def _write_heartbeat_sync(self, is_running: bool) -> None:
        """Emite arquivo de status atômico em disco para detecção de liveness pelo Dashboard avulso."""
        try:
            status_file = Path("data/bot_status.json")
            status_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "is_running": is_running,
                "is_paused": self.is_paused,
                "wallet_balance_usd": float(self.execution_engine.balance_usd),
                "initial_wallet_usd": float(self.settings.PAPER_INITIAL_WALLET_USD),
                "active_positions_count": len(self.position_tracker.active_positions),
                "pid": os.getpid(),
                "timestamp": datetime.now(UTC).timestamp(),
            }
            tmp_file = status_file.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp_file.replace(status_file)
        except Exception as exc:
            logger.debug("Erro ao emitir heartbeat do bot: %s", exc)

    async def _heartbeat_worker(self) -> None:
        """Emite periodicamente o estado de integridade (heartbeat) para o Dashboard avulso."""
        while self.is_running:
            await asyncio.to_thread(self._write_heartbeat_sync, True)
            await asyncio.sleep(1.5)

    def _read_ipc_command_sync(self) -> dict[str, Any] | None:
        """Lê o arquivo de comando IPC se existir."""
        control_file = Path("data/bot_control.json")
        if not control_file.exists():
            return None
        try:
            content = control_file.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logger.debug("Erro ao ler data/bot_control.json: %s", exc)
        return None

    async def _ipc_command_worker(self) -> None:
        """Escuta comandos IPC gravados pelo Dashboard executando em processo avulso."""
        while self.is_running:
            try:
                cmd_data = await asyncio.to_thread(self._read_ipc_command_sync)
                if cmd_data:
                    cmd_id = cmd_data.get("command_id")
                    cmd_name = cmd_data.get("command")
                    payload = cmd_data.get("payload", {})
                    if cmd_id and cmd_id != self._last_handled_ipc_id:
                        self._last_handled_ipc_id = str(cmd_id)
                        logger.info("📩 [IPC RECEBIDO] Executando comando '%s' do Dashboard...", cmd_name)
                        await self._dispatch_ipc_command(cmd_name, payload)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Erro no worker de comando IPC: %s", exc)
            await asyncio.sleep(0.5)

    def _handle_ipc_reload_config(self, payload: dict[str, Any]) -> None:
        """Processa comando de recarga de configuração."""
        if payload:
            self._apply_config_payload(payload, save_to_disk=False)
        else:
            self._load_persisted_config()
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.create_task(self.reconcile_wallet_balance())
                asyncio.create_task(self._try_fill_slots_from_waiting_queue())
        except RuntimeError:
            pass

    async def _dispatch_ipc_command(self, command: str | None, payload: dict[str, Any]) -> None:
        """Despacha a execução do comando IPC recebido."""
        if command == "pause":
            self.pause()
        elif command == "resume":
            self.resume()
        elif command == "reload_config":
            self._handle_ipc_reload_config(payload)
        elif command == "deposit":
            amt_str = payload.get("amount_usd")
            if amt_str is not None:
                self.deposit_wallet(Decimal(str(amt_str)))
        elif command == "restart":
            bal_str = (
                payload.get("wallet_balance_usd")
                if payload.get("wallet_balance_usd") is not None
                else (
                    payload.get("initial_wallet_usd")
                    if payload.get("initial_wallet_usd") is not None
                    else payload.get("paper_initial_wallet_usd")
                )
            )
            bal = Decimal(str(bal_str)) if bal_str is not None else None
            await self.restart_paper_session(new_balance=bal)
        elif command == "stop":
            await self.stop()

    async def start(self, run_mock_stream: bool = False) -> None:
        """Inicia todas as tarefas cooperativas do bot."""
        self.is_running = True
        self.run_mock_stream = run_mock_stream
        rpc_display = self.settings.PRIMARY_RPC_HTTP_URL.split("?")[0]
        has_key = "api-key" in self.settings.PRIMARY_RPC_HTTP_URL or bool(self.settings.HELIUS_API_KEY)
        key_info = "🔑 Helius RPC dedicada configurada" if "helius" in rpc_display and has_key else f"Nó: {rpc_display}"
        logger.info("=== VERTEX-BOT OPERACIONAL | %s ===", key_info)

        # Tarefa 1: Processador da fila de auditoria de segurança
        self._tasks.append(asyncio.create_task(self._security_worker()))

        # Tarefa 2: Monitor contínuo de cotações e saídas (Break-Even / Trailing Stop)
        self._tasks.append(asyncio.create_task(self._price_monitor_worker()))

        # Tarefa 3: Loop de telemetria periódica
        self._tasks.append(asyncio.create_task(self._telemetry_worker()))

        # Tarefa 4: Ingestão de Tokens (Real ou Mock para testes locais)
        if run_mock_stream:
            self._tasks.append(asyncio.create_task(self._mock_stream_producer()))
        else:
            await self.scanner.start()

        # Tarefa 5: Watchlist Ativa de Tokens Aprovados (Reavaliação Contínua para Entrada/Reentrada)
        self._tasks.append(asyncio.create_task(self._approved_watchlist_worker()))

        # Tarefa 6: Emissão contínua de status/heartbeat para o Dashboard avulso
        self._tasks.append(asyncio.create_task(self._heartbeat_worker()))

        # Tarefa 7: Escuta e execução de comandos IPC gravados pelo Dashboard
        self._tasks.append(asyncio.create_task(self._ipc_command_worker()))

    async def _security_worker(self) -> None:
        """Consome tokens da fila de detecção e executa a triagem de segurança com gestão dinâmica de banca."""
        # Em modo de teste simulado, usa mock de autoridades para focar no ciclo de vida de trades
        mock_overrides = (
            {
                "is_mint_revoked": True,
                "is_freeze_revoked": True,
                "lp_burn_pct": 100.0,
                "top10_pct": 10.0,
                "taxes": (0.0, 0.0, False),
            }
            if self.run_mock_stream
            else None
        )

        while self.is_running:
            try:
                token = await self.detection_queue.get()
                self.telemetry.record_detection()
                await self.tokens_repo.save_detected_token(token)

                if self.is_paused:
                    logger.debug("⏸️ [BOT PAUSADO] Ignorando abertura de posição para token %s.", token.address)
                    self.detection_queue.task_done()
                    continue

                # Auditoria com os 6 Hard Gates
                audit = await self.validator.audit_token(token, mock_overrides=mock_overrides)

                if audit.is_approved:
                    self.telemetry.record_approval()
                    await self._evaluate_and_execute_entry(token)
                else:
                    self.telemetry.record_rejection(audit.rejection_reason or "Desconhecido")

                self.detection_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Erro inesperado no worker de segurança: %s", exc, exc_info=True)

    def _sync_config_from_disk_if_present(self) -> None:
        """Sincroniza parâmetros de risco e execução diretamente de bot_config.json se disponível."""
        cfg_file = self._get_config_path()
        if not cfg_file.exists():
            return
        try:
            raw_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            if isinstance(raw_cfg, dict):
                # Não sobrescreve o saldo livre da carteira durante sincronizações de rotina de entrada
                sync_payload = {
                    k: v for k, v in raw_cfg.items()
                    if k not in ("wallet_balance_usd", "paper_initial_wallet_usd", "initial_wallet_usd")
                }
                self._apply_config_payload(sync_payload, save_to_disk=False)
        except Exception:
            pass

    def _extract_age_from_timestamps(self, raw_event: dict[str, Any], now_utc: datetime) -> float | None:
        """Extrai idade via timestamp de criação explícito (pairCreatedAt ou pool_created_at)."""
        pair_created = raw_event.get("pairCreatedAt")
        if pair_created is None and isinstance(raw_event.get("pair_data"), dict):
            pair_created = raw_event["pair_data"].get("pairCreatedAt")

        if pair_created is not None:
            try:
                created_ms = float(pair_created)
                if created_ms > 0:
                    now_ms = now_utc.timestamp() * 1000.0
                    return max(0.0, (now_ms - created_ms) / (1000.0 * 3600.0))
            except (ValueError, TypeError):
                pass

        hint = raw_event.get("hint")
        pool_iso = hint.get("pool_created_at") if isinstance(hint, dict) else raw_event.get("pool_created_at")
        if pool_iso:
            try:
                clean_ts = str(pool_iso).replace("Z", "+00:00")
                dt = datetime.fromisoformat(clean_ts)
                return max(0.0, (now_utc - dt).total_seconds() / 3600.0)
            except Exception:
                pass

        return None

    def _extract_age_from_raw_fields(
        self,
        raw_event: dict[str, Any],
        detection_ts: datetime,
        now_utc: datetime,
    ) -> float | None:
        """Extrai idade a partir de age_hours já computado ou evento de migração/graduação."""
        if "age_hours" in raw_event:
            try:
                base_age = float(raw_event["age_hours"])
                elapsed = max(0.0, (now_utc - detection_ts).total_seconds() / 3600.0)
                return max(0.0, base_age + elapsed)
            except (ValueError, TypeError):
                pass

        if raw_event.get("txType") == "migration" or raw_event.get("event") == "raydium_graduation":
            elapsed = max(0.0, (now_utc - detection_ts).total_seconds() / 3600.0)
            return max(0.0, elapsed)

        return None

    def _extract_token_age_hours(self, token: TokenMetadata) -> float | None:
        """Extrai ou estima a idade do token em horas desde a criação do par/pool."""
        if not isinstance(token.raw_event, dict):
            return None

        now_utc = datetime.now(UTC)
        age_from_ts = self._extract_age_from_timestamps(token.raw_event, now_utc)
        if age_from_ts is not None:
            return age_from_ts

        return self._extract_age_from_raw_fields(token.raw_event, token.detection_timestamp, now_utc)

    def _enqueue_waiting_token(
        self, token: TokenMetadata, reason: str, eligible_strategy: str = "DUAL"
    ) -> None:
        """Adiciona ou atualiza um token aprovado na fila de espera por slots/saldo."""
        raw_price: float | None = None
        if isinstance(token.raw_event, dict) and "priceUsd" in token.raw_event:
            try:
                raw_price = float(token.raw_event["priceUsd"])
            except (ValueError, TypeError):
                raw_price = None

        token_age = self._extract_token_age_hours(token)
        existing = self.waiting_tokens.get(token.address)
        enqueued_at = existing["enqueued_at"] if existing else datetime.now(UTC).isoformat()

        self.waiting_tokens[token.address] = {
            "address": token.address,
            "token_address": token.address,
            "symbol": token.symbol or token.address[:8],
            "token_symbol": token.symbol or token.address[:8],
            "name": token.name or "N/A",
            "chain": token.chain,
            "dex": token.dex,
            "eligible_strategy": eligible_strategy,
            "initial_liquidity_usd": float(token.initial_liquidity_usd),
            "liquidity_usd": float(token.initial_liquidity_usd),
            "waiting_reason": reason,
            "reason_pending": reason,
            "enqueued_at": enqueued_at,
            "added_at": enqueued_at,
            "last_price": raw_price,
            "age_hours": round(token_age, 2) if token_age is not None else None,
            "raw_event": token.raw_event if isinstance(token.raw_event, dict) else {},
        }
        self._save_waiting_tokens()

    def _is_waiting_token_expired(
        self,
        item: dict[str, Any],
        max_age_hours: float,
        now_utc: datetime,
    ) -> bool:
        """Verifica se um token na fila de espera ultrapassou o limite de idade permitido."""
        base_age = item.get("age_hours")
        if base_age is None:
            return False

        queued_hours = 0.0
        enqueued_at_str = item.get("enqueued_at")
        if enqueued_at_str:
            try:
                eq_dt = datetime.fromisoformat(enqueued_at_str)
                queued_hours = max(0.0, (now_utc - eq_dt).total_seconds() / 3600.0)
            except Exception:
                pass

        current_age = float(base_age) + queued_hours
        if current_age > max_age_hours:
            logger.warning(
                "⌛ [FILA EXPIRADA] Token %s excedeu idade máxima permitida (%.2fh > %.1fh) aguardando na fila. Descartando.",
                item.get("symbol", item["address"][:8]),
                current_age,
                max_age_hours,
            )
            return True
        return False

    def _is_waiting_token_dumped(self, item: dict[str, Any], live_price: Decimal | None) -> bool:
        """Verifica se o token despencou >30% enquanto aguardava na fila de espera."""
        last_price = item.get("last_price")
        if last_price and live_price and live_price < Decimal(str(last_price)) * Decimal("0.70"):
            logger.warning(
                "⚠️ [FILA DESQUALIFICADA] Token %s despencou >30%% na fila de espera ($%.6f -> $%.6f). Descartando.",
                item.get("symbol", item["address"][:8]),
                last_price,
                live_price,
            )
            return True
        return False

    def _can_process_waiting_queue(self) -> tuple[bool, int, Decimal, int]:
        """Avalia condições prévias (status, slots e saldo) para consumo da fila de espera."""
        if not self.waiting_tokens or self.is_paused or not self.is_running:
            return False, 0, Decimal("0.0"), 0

        active_count = len(self.position_tracker.active_positions)
        max_positions = int(getattr(self.settings, "MAX_CONCURRENT_POSITIONS", 50))
        if active_count >= max_positions:
            return False, 0, Decimal("0.0"), 0

        configured_buy = Decimal(str(getattr(self.settings, "PAPER_BUY_AMOUNT_USD", "1.0")))
        min_trade = Decimal(str(getattr(self.settings, "MIN_TRADE_AMOUNT_USD", "1.0")))
        min_required = max(Decimal("0.05"), min(configured_buy, min_trade) - Decimal("0.05"))

        if self.execution_engine.balance_usd < min_required:
            return False, 0, Decimal("0.0"), 0

        return True, 1, min_required, max_positions

    def _build_waiting_token_meta(self, item: dict[str, Any], price: Decimal | None) -> TokenMetadata:
        """Reconstrói TokenMetadata a partir do item da fila de espera."""
        raw_event = dict(item.get("raw_event") or {})
        if price:
            raw_event["priceUsd"] = str(price)

        # Enriquecimento com par da DexScreener se disponível em cache
        cached_pair = getattr(self.price_feed, "get_pair_data", lambda _: None)(item["address"])
        liq_val = Decimal(str(item.get("initial_liquidity_usd", "0.0")))
        if cached_pair:
            raw_event["pair_data"] = cached_pair
            if "pairCreatedAt" in cached_pair:
                raw_event["pairCreatedAt"] = cached_pair["pairCreatedAt"]
            pair_liq = Decimal(str(cached_pair.get("liquidity", {}).get("usd") or 0.0))
            if pair_liq > Decimal("0.0"):
                liq_val = pair_liq

        return TokenMetadata(
            address=item["address"],
            chain=item.get("chain", "solana"),
            dex=item.get("dex", "raydium"),
            initial_liquidity_usd=liq_val,
            symbol=item.get("symbol"),
            name=item.get("name"),
            detection_timestamp=datetime.now(UTC),
            raw_event=raw_event,
        )

    async def _try_fill_slots_from_waiting_queue(self) -> None:
        """Processa a fila de espera de tokens aprovados e abre posições se houver vagas e saldo."""
        can_run, _, min_required, max_positions = self._can_process_waiting_queue()
        if not can_run:
            return

        strat_mode = str(getattr(self.settings, "TRADING_STRATEGY_MODE", "DUAL")).upper()
        if strat_mode == "SWING_ONLY":
            max_age_hours = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SWING", 6.0))
        elif strat_mode == "SCALP_ONLY":
            max_age_hours = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SCALP", 720.0))
        else:
            max_age_hours = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SCALP", 720.0))
        now_utc = datetime.now(UTC)

        candidates = list(self.waiting_tokens.values())
        addrs = [c["address"] for c in candidates]
        live_prices = await self.price_feed.fetch_prices(addrs)

        for item in candidates:
            if not self.is_running or self.is_paused:
                break
            if len(self.position_tracker.active_positions) >= max_positions:
                break
            if self.execution_engine.balance_usd < min_required:
                break

            addr = item["address"]
            if self._is_waiting_token_expired(item, max_age_hours, now_utc):
                self.waiting_tokens.pop(addr, None)
                self._save_waiting_tokens()
                continue

            price = live_prices.get(addr)
            if self._is_waiting_token_dumped(item, price):
                self.waiting_tokens.pop(addr, None)
                self._save_waiting_tokens()
                continue

            token_meta = self._build_waiting_token_meta(item, price)
            await self._evaluate_and_execute_entry(token_meta)

    async def _evaluate_and_execute_entry(self, token: TokenMetadata) -> None:
        """Avalia limites de slots, saldo e executa a compra do token aprovado com parâmetros dinâmicos."""
        # 0. Sincroniza configurações mais recentes do disco para garantir conformidade com ajustes
        self._sync_config_from_disk_if_present()

        # 1. Validação estrita de idade do token no mercado (Scalp: 30m-720h, Swing: 2h-4h)
        # 1. Validação estrita de idade do token no mercado (Scalp: 2h-720h, Swing: 3h-6h)
        strat_mode = str(getattr(self.settings, "TRADING_STRATEGY_MODE", "DUAL")).upper()
        if strat_mode == "SWING_ONLY":
            min_age_hours = float(getattr(self.settings, "MIN_TOKEN_AGE_HOURS_SWING", 3.0))
            max_age_hours = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SWING", 6.0))
        elif strat_mode == "SCALP_ONLY":
            min_age_hours = float(getattr(self.settings, "MIN_TOKEN_AGE_HOURS_SCALP", 2.0))
            max_age_hours = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SCALP", 720.0))
        else:
            # No modo DUAL, aceita tokens dentro da janela ampla de Scalp (2.0h a 720h)
            min_age_hours = float(getattr(self.settings, "MIN_TOKEN_AGE_HOURS_SCALP", 2.0))
            max_age_hours = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SCALP", 720.0))

        token_age = self._extract_token_age_hours(token)
        if token_age is not None and (token_age < min_age_hours or token_age > max_age_hours):
            logger.warning(
                "⌛ [IDADE FORA DA JANELA] Token %s (%s) possui %.2fh de mercado (janela permitida: %.1fh a %.1fh no modo %s). Entrada descartada.",
                token.symbol or "N/A",
                token.address,
                token_age,
                min_age_hours,
                max_age_hours,
                strat_mode,
            )
            if token.address in self.waiting_tokens:
                self.waiting_tokens.pop(token.address, None)
                self._save_waiting_tokens()
            return

        # 2. Determina elegibilidade de cada perna da estratégia de forma estrita e isolada
        metrics = self.market_validator.extract_metrics(token) if hasattr(self, "market_validator") else {}
        eligible_scalp = bool(metrics.get("eligible_scalp", False))
        eligible_swing = bool(metrics.get("eligible_swing", False))

        if strat_mode == "SCALP_ONLY":
            eligible_swing = False
        elif strat_mode == "SWING_ONLY":
            eligible_scalp = False

        if not eligible_scalp and not eligible_swing:
            logger.warning(
                "⚠️ [INELIGÍVEL] Token %s não cumpre requisitos nem de Scalp (30m-720h, Liq $%.0f+) nem de Swing (2h-4h, Liq $%.0f+). Entrada descartada.",
                token.symbol or token.address[:8],
                float(getattr(self.settings, "MIN_LIQUIDITY_USD", 5000.0)),
                float(getattr(self.settings, "MIN_LIQUIDITY_SWING_USD", 20000.0)),
            )
            if token.address in self.waiting_tokens:
                self.waiting_tokens.pop(token.address, None)
                self._save_waiting_tokens()
            return

        # 3. Verifica posições ativas e capacidade de slots por estratégia
        max_positions = int(getattr(self.settings, "MAX_CONCURRENT_POSITIONS", 10))
        active_positions = list(self.position_tracker.active_positions.values())
        active_count = len(active_positions)
        active_scalp = sum(1 for p in active_positions if getattr(p, "strategy_type", "SCALP") == "SCALP")
        active_swing = sum(1 for p in active_positions if getattr(p, "strategy_type", "SCALP") == "SWING")

        existing_strategies_for_token = {
            getattr(p, "strategy_type", "SCALP")
            for p in active_positions
            if p.token_address == token.address
        }
        if "SCALP" in existing_strategies_for_token and "SWING" in existing_strategies_for_token:
            logger.info(
                "⏸️ [POSIÇÕES JÁ ABERTA] O token %s (%s) já possui posições ativas em ambas as estratégias.",
                token.symbol or "N/A",
                token.address,
            )
            return

        if strat_mode == "DUAL":
            max_scalp_slots = max(1, max_positions // 2)
            max_swing_slots = max(1, max_positions - max_scalp_slots)
        elif strat_mode == "SCALP_ONLY":
            max_scalp_slots = max_positions
            max_swing_slots = 0
        else:  # SWING_ONLY
            max_scalp_slots = 0
            max_swing_slots = max_positions

        can_open_scalp = (
            eligible_scalp
            and ("SCALP" not in existing_strategies_for_token)
            and (active_scalp < max_scalp_slots)
            and (active_count < max_positions)
        )
        can_open_swing = (
            eligible_swing
            and ("SWING" not in existing_strategies_for_token)
            and (active_swing < max_swing_slots)
            and (active_count < max_positions)
        )

        open_scalp = False
        open_swing = False

        if can_open_scalp and can_open_swing and (active_count + 2 <= max_positions):
            open_scalp = True
            open_swing = True
        elif can_open_swing:
            open_swing = True
        elif can_open_scalp:
            open_scalp = True

        target_strat = "DUAL" if (eligible_scalp and eligible_swing) else ("SWING" if eligible_swing else "SCALP")

        if not open_scalp and not open_swing:
            waiting_reason = "AGUARDANDO_SLOT"
            self._enqueue_waiting_token(token, reason=waiting_reason, eligible_strategy=target_strat)
            logger.info(
                "⏳ [FILA DE ESPERA] Token aprovado %s (%s) aguardando slot (%s). Posições: %d/%d (Scalp: %d/%d, Swing: %d/%d)",
                token.symbol or token.address[:8],
                token.address,
                target_strat,
                active_count,
                max_positions,
                active_scalp,
                max_scalp_slots,
                active_swing,
                max_swing_slots,
            )
            return

        # 4. Dimensionamento de capital por posição e validação de saldo
        required_slots = (1 if open_scalp else 0) + (1 if open_swing else 0)
        min_trade = Decimal(str(getattr(self.settings, "MIN_TRADE_AMOUNT_USD", "1.0")))
        configured_buy = Decimal(str(getattr(self.settings, "PAPER_BUY_AMOUNT_USD", "1.0")))
        available_cash = self.execution_engine.balance_usd

        if configured_buy and configured_buy > Decimal("0.0"):
            base_buy_amount = configured_buy
            base_min_required = max(Decimal("0.05"), min(configured_buy, min_trade) - Decimal("0.05"))
        else:
            active_invested = sum(
                (p.allocated_capital_usd for p in active_positions),
                Decimal("0.0"),
            )
            total_portfolio = available_cash + active_invested
            target_per_slot = total_portfolio / Decimal(max_positions)
            base_buy_amount = max(min_trade, target_per_slot)
            base_min_required = max(Decimal("0.50"), min_trade - Decimal("0.05"))

        required_total_min = base_min_required * Decimal(required_slots)
        target_total_capital = base_buy_amount * Decimal(required_slots)

        if available_cash < required_total_min or available_cash < target_total_capital:
            waiting_reason = "AGUARDANDO_SALDO"
            self._enqueue_waiting_token(token, reason=waiting_reason, eligible_strategy=target_strat)
            logger.info(
                "⏳ [FILA DE ESPERA] Token aprovado %s (%s) aguardando saldo (%s). Caixa: $%.2f (Necessário: $%.2f)",
                token.symbol or token.address[:8],
                token.address,
                target_strat,
                available_cash,
                target_total_capital,
            )
            return

        # 5. Se slots e saldo foram liberados, remove da fila de espera
        if token.address in self.waiting_tokens:
            self.waiting_tokens.pop(token.address, None)
            self._save_waiting_tokens()

        buy_amount_usd = base_buy_amount

        logger.info(
            "📊 [GESTÃO DE CARTEIRA] Caixa: $%.2f | Alocando $%.2f por perna (Scalp: %s, Swing: %s | Modo: %s) na Posição #%d/%d (%s)",
            available_cash,
            buy_amount_usd,
            "SIM" if open_scalp else "NÃO",
            "SIM" if open_swing else "NÃO",
            strat_mode,
            active_count + 1,
            max_positions,
            token.symbol or token.address[:8],
        )

        # 6. Dispara ordens para as pernas selecionadas
        if open_scalp and open_swing:
            await self._execute_dual_track_entry(token, buy_amount_usd)
        elif open_scalp:
            logger.info(
                "🎯 [ESTRATÉGIA SCALP] Token %s qualificado para Scalp (30m-720h). Abrindo perna de Scalp.",
                token.symbol or token.address[:8],
            )
            pos_scalp = await self.execution_engine.execute_buy(
                token,
                amount_usd=buy_amount_usd,
                strategy_type="SCALP",
            )
            if pos_scalp:
                self.telemetry.record_trade_opened()
                await self.position_tracker.register_position(pos_scalp)
        elif open_swing:
            logger.info(
                "🏛️ [ESTRATÉGIA SWING] Token %s consolidado para Swing (2h-4h). Abrindo perna de Swing.",
                token.symbol or token.address[:8],
            )
            pos_swing = await self.execution_engine.execute_buy(
                token,
                amount_usd=buy_amount_usd,
                strategy_type="SWING",
            )
            if pos_swing:
                self.telemetry.record_trade_opened()
                await self.position_tracker.register_position(pos_swing)

    async def _execute_dual_track_entry(self, token: TokenMetadata, buy_amount_usd: Decimal) -> None:
        """Abre posições simultâneas SCALP e SWING no modo Dual-Track com valor integral em cada perna."""
        logger.info(
            "⚡ [DUAL-TRACK ENTRY] Abrindo Posição SCALP ($%.2f) e Posição SWING ($%.2f) para %s",
            buy_amount_usd,
            buy_amount_usd,
            token.symbol or token.address[:8],
        )
        pos_scalp = await self.execution_engine.execute_buy(
            token,
            amount_usd=buy_amount_usd,
            strategy_type="SCALP",
        )
        if pos_scalp:
            self.telemetry.record_trade_opened()
            await self.position_tracker.register_position(pos_scalp)

        pos_swing = await self.execution_engine.execute_buy(
            token,
            amount_usd=buy_amount_usd,
            strategy_type="SWING",
        )
        if pos_swing:
            self.telemetry.record_trade_opened()
            await self.position_tracker.register_position(pos_swing)


    async def _handle_position_closed(self, position: PositionState) -> None:
        """Notificado quando uma posição é 100% liquidada (Trailing Stop ou Stop Loss)."""
        logger.info(
            "🔄 [POSIÇÃO 100%% ENCERRADA] Registrando saída do token %s para monitoramento de reentrada segura.",
            position.token_address,
        )
        # 1. Registra no ReentryRiskManager para ativar o cool-off e monitor de repique
        exit_price = position.trailing_stop_price if position.trailing_stop_price > Decimal("0.0") else position.entry_price
        exit_reason = "TRAILING_STOP" if position.status.value == "CLOSED" else "EMERGENCY_STOP"
        self.reentry_manager.record_exit(
            token_address=position.token_address,
            exit_price=exit_price,
            exit_reason=exit_reason,
        )

        # 2. Libera token nos caches de vistos dos scanners para permitir nova detecção
        if hasattr(self.scanner, "release_token"):
            try:
                self.scanner.release_token(position.token_address)
            except Exception as exc:
                logger.debug("Erro ao chamar release_token no scanner: %s", exc)

        # 3. Dispara verificação imediata da fila de espera para preencher o slot recém-liberado
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.create_task(self._try_fill_slots_from_waiting_queue())
        except RuntimeError:
            pass

    async def _schedule_token_reentry_check(self, token_address: str, delay_seconds: float = 15.0) -> None:
        """Compatibilidade: reentradas são gerenciadas com segurança pelo ReentryRiskManager na watchlist."""
        pass

    async def _approved_watchlist_worker(self) -> None:
        """
        Monitor de Watchlist de Tokens Aprovados.
        Consulta periodicamente tokens já validados pelo SecurityValidator para identificar
        oportunidades de entrada quando surgem slots livres e caixa disponível.
        """
        logger.info("🔭 [WATCHLIST ATIVA] Monitor contínuo de tokens aprovados iniciado.")
        poll_interval = float(getattr(self.settings, "WATCHLIST_POLL_INTERVAL_SEC", 25.0))
        cooldown_sec = float(getattr(self.settings, "WATCHLIST_RETRY_COOLDOWN_SEC", 60.0))

        last_eval_time: dict[str, float] = {}
        await asyncio.sleep(3.0)

        while self.is_running:
            try:
                # Prioridade 1: Preenche slots com tokens aprovados em espera
                await self._try_fill_slots_from_waiting_queue()

                # Prioridade 2: Ciclo de watchlist geral
                current_max_positions = int(getattr(self.settings, "MAX_CONCURRENT_POSITIONS", 5))
                current_min_trade = Decimal(str(getattr(self.settings, "MIN_TRADE_AMOUNT_USD", "1.0")))
                await self._process_watchlist_cycle(
                    max_positions=current_max_positions,
                    min_trade=current_min_trade,
                    cooldown_sec=cooldown_sec,
                    last_eval_time=last_eval_time,
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Falha transitória na watchlist de aprovados: %s", exc)

            await asyncio.sleep(poll_interval)

    async def _process_watchlist_cycle(
        self,
        max_positions: int,
        min_trade: Decimal,
        cooldown_sec: float,
        last_eval_time: dict[str, float],
    ) -> None:
        """Executa um ciclo único de verificação de slots e cotações para a watchlist de tokens aprovados."""
        if self.is_paused:
            return

        active_count = len(self.position_tracker.active_positions)
        available_cash = self.execution_engine.balance_usd
        configured_buy = Decimal(str(getattr(self.settings, "PAPER_BUY_AMOUNT_USD", "1.0")))
        if configured_buy and configured_buy > Decimal("0.0"):
            min_required = max(Decimal("0.05"), min(configured_buy, min_trade) - Decimal("0.05"))
        else:
            min_required = max(Decimal("0.50"), min_trade - Decimal("0.05"))

        if active_count >= max_positions or available_cash < min_required:
            return

        active_addresses = [p.token_address for p in self.position_tracker.active_positions.values()]
        candidates = await self.tokens_repo.get_approved_watchlist_candidates(
            exclude_addresses=active_addresses,
            limit=20,
        )
        if not candidates:
            return

        now = asyncio.get_running_loop().time()
        eligible = [c for c in candidates if (now - last_eval_time.get(c.address, 0.0)) >= cooldown_sec]
        if not eligible:
            return

        candidate_addrs = [c.address for c in eligible]
        prices = await self.price_feed.fetch_prices(candidate_addrs)

        for cand in eligible:
            if not self.is_running or self.is_paused:
                break
            if len(self.position_tracker.active_positions) >= max_positions:
                break
            if self.execution_engine.balance_usd < min_required:
                break

            await self._try_enqueue_watchlist_candidate(
                cand=cand,
                price=prices.get(cand.address),
                now=now,
                last_eval_time=last_eval_time,
            )

    async def _try_enqueue_watchlist_candidate(
        self,
        cand: TokenMetadata,
        price: Decimal | None,
        now: float,
        last_eval_time: dict[str, float],
    ) -> None:
        """Avalia um candidato individual da watchlist para reentrada e enfileira se aprovado."""
        if not price or price <= Decimal("0.0"):
            return

        raw_event = dict(cand.raw_event or {})
        raw_event["priceUsd"] = str(price)
        liq_val = cand.initial_liquidity_usd

        # Enriquecimento com par da DexScreener se disponível em cache
        cached_pair = getattr(self.price_feed, "get_pair_data", lambda _: None)(cand.address)
        if cached_pair:
            raw_event["pair_data"] = cached_pair
            if "pairCreatedAt" in cached_pair:
                raw_event["pairCreatedAt"] = cached_pair["pairCreatedAt"]
            pair_liq = Decimal(str(cached_pair.get("liquidity", {}).get("usd") or 0.0))
            if pair_liq > Decimal("0.0"):
                liq_val = pair_liq

        cand = cand.model_copy(update={"raw_event": raw_event, "initial_liquidity_usd": liq_val})

        # Valida reentrada inteligente (Cool-off e Anti-Falling-Knife)
        can_reenter, reentry_reason = self.reentry_manager.can_reenter(
            token_address=cand.address,
            current_price=price,
            current_liquidity_usd=cand.initial_liquidity_usd,
        )
        if not can_reenter:
            logger.debug(
                "⏸️ [REENTRADA EM ESPERA] %s (%s): %s",
                cand.symbol or "N/A",
                cand.address[:8],
                reentry_reason,
            )
            return

        # Validação de dinâmica de mercado (momentum e fluxo)
        strat_mode = str(getattr(self.settings, "TRADING_STRATEGY_MODE", "DUAL")).upper()
        if hasattr(self, "market_validator"):
            is_market_ok, reject_reason, _ = self.market_validator.evaluate(cand, strategy_mode=strat_mode)
            if not is_market_ok:
                logger.debug(
                    "⏸️ [WATCHLIST DINÂMICA] Token %s reprovado no momentum atual: %s",
                    cand.symbol or cand.address[:8],
                    reject_reason,
                )
                return

        last_eval_time[cand.address] = now
        if cand.raw_event is None:
            cand.raw_event = {}
        cand.raw_event["priceUsd"] = str(price)

        logger.info(
            "✨ [WATCHLIST OPORTUNIDADE] Token aprovado %s (%s) com cotação ativa ($%.6f). Reenfileirando para entrada!",
            cand.symbol or "N/A",
            cand.address,
            price,
        )
        await self.detection_queue.put(cand)
        await asyncio.sleep(0.5)

    async def _mock_stream_producer(self) -> None:
        """Produz eventos simulados de novos tokens para teste local de ponta a ponta."""
        logger.info("Iniciando gerador de eventos simulados (Mock Stream)...")
        sample_tokens = [
            # 1. Token Aprovado
            TokenMetadata(
                address="SoL11111111111111111111111111111111111111111",
                dex="raydium",
                initial_liquidity_usd=Decimal("15000.0"),
            ),
            # 2. Token Reprovado por baixa liquidez
            TokenMetadata(
                address="RUG22222222222222222222222222222222222222222",
                dex="pumpfun",
                initial_liquidity_usd=Decimal("1200.0"),
            ),
            # 3. Outro Token Aprovado para testar Break-Even e Trailing Stop
            TokenMetadata(
                address="WIN33333333333333333333333333333333333333333",
                dex="raydium",
                initial_liquidity_usd=Decimal("25000.0"),
            ),
        ]

        for token in sample_tokens:
            if not self.is_running:
                break
            await asyncio.sleep(1.0)
            logger.info("Mock Event: Novo par lançado on-chain -> %s", token.address)
            await self.detection_queue.put(token)

        # Simula evolução de preço para o WIN333...
        await asyncio.sleep(2.0)
        for pos_id, pos in list(self.position_tracker.active_positions.items()):
            if "WIN3" in pos.token_address:
                # 1. Preço sobe para 2x (+100%) -> Ativa Break-Even!
                logger.info(">> Simulação de Mercado: Preço de %s disparou para 2x!", pos.token_address)
                await self.position_tracker.process_price_tick(pos_id, pos.entry_price * Decimal("2.05"))
                self.telemetry.record_break_even()

                # 2. Preço sobe mais (+150%)
                logger.info(">> Simulação de Mercado: Preço de %s subiu para 2.5x!", pos.token_address)
                await self.position_tracker.process_price_tick(pos_id, pos.entry_price * Decimal("2.50"))

                # 3. Preço recua 15% a partir do topo -> Dispara Trailing Stop!
                logger.info(">> Simulação de Mercado: Preço de %s recuou 15%% a partir do topo!", pos.token_address)
                drop_price = (pos.entry_price * Decimal("2.50")) * Decimal("0.85")
                await self.position_tracker.process_price_tick(pos_id, drop_price)
                self.telemetry.record_trade_closed(pos.realized_pnl_usd, reason="TRAILING_STOP")

    async def _process_active_position_tick(
        self, pos_id: int, pos: PositionState, prices: dict[str, Decimal]
    ) -> None:
        """Processa tick de preço e enriquecimento de metadados para uma posição ativa."""
        current_price = prices.get(pos.token_address)
        if current_price is not None and current_price > Decimal("0"):
            self.reentry_manager.update_post_exit_price(pos.token_address, current_price)
            await self.position_tracker.process_price_tick(pos_id, current_price)

            # Notifica clientes do dashboard se houver WebSocket conectado
            if hasattr(self, "dashboard_server") and self.dashboard_server:
                broadcast = getattr(self.dashboard_server, "broadcast_event", None)
                if callable(broadcast):
                    try:
                        asyncio.create_task(
                            broadcast(
                                "position_tick",
                                {
                                    "id": pos_id,
                                    "token_address": pos.token_address,
                                    "current_price": float(current_price),
                                    "highest_price_seen": float(pos.highest_price_seen),
                                    "trailing_stop_price": float(pos.trailing_stop_price),
                                    "unrealized_pnl_usd": round(float(pos.unrealized_pnl_usd), 4),
                                    "unrealized_pnl_pct": round(float(pos.roi_pct), 2),
                                },
                            )
                        )
                    except Exception:
                        pass

        meta_getter = getattr(self.price_feed, "get_metadata", None)
        if callable(meta_getter):
            meta = meta_getter(pos.token_address)
            if meta:
                sym, nm = meta
                if sym or nm:
                    await self.tokens_repo.update_token_metadata(pos.token_address, sym, nm)

    async def _price_monitor_worker(self) -> None:
        """Monitora as cotações em tempo real das posições abertas para disparar saídas automatizadas."""
        logger.info("Monitor de Cotações Contínuas em Tempo Real (Price Poller) iniciado.")
        poll_interval = float(getattr(self.settings, "PRICE_POLL_INTERVAL_SEC", 3.0))

        while self.is_running:
            try:
                active_pos = list(self.position_tracker.active_positions.items())
                if active_pos:
                    addresses = list({pos.token_address for _, pos in active_pos})
                    prices = await self.price_feed.fetch_prices(addresses)

                    for pos_id, pos in active_pos:
                        if not self.is_running:
                            break
                        await self._process_active_position_tick(pos_id, pos, prices)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Falha transitória na checagem de cotações: %s", exc)

            await asyncio.sleep(poll_interval)

    async def _telemetry_worker(self) -> None:
        """Imprime relatório de telemetria periodicamente."""
        while self.is_running:
            try:
                await asyncio.sleep(30.0)
                self.telemetry.print_summary()
            except asyncio.CancelledError:
                break

    async def stop(self) -> None:
        """Executa graceful shutdown salvando todo o estado pendente."""
        logger.info("Iniciando graceful shutdown do Vertex-bot...")
        self.is_running = False

        if self.stop_event and not self.stop_event.is_set():
            self.stop_event.set()

        # Para o scanner
        await self.scanner.stop()

        # Cancela tarefas em execução
        for task in self._tasks:
            if not task.done():
                task.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)

        if self.settings.EXECUTION_MODE == "PAPER":
            logger.info("Encerrando modo PAPER: limpando dados de posições e ordens do teste simulado...")
            await self.positions_repo.clear_paper_trading_data()

        # Emite sinal de encerramento no heartbeat
        await asyncio.to_thread(self._write_heartbeat_sync, False)

        # Fecha conexões de rede e banco de dados
        await self.price_feed.close()
        await self.rpc_client.close()
        await self.db.close()

        # Exibe relatório final consolidado
        self.telemetry.print_summary()
        logger.info("Vertex-bot finalizado com segurança.")


async def _start_background_dashboard(
    db: DatabaseManager,
    port: int,
    orchestrator: Any | None = None,
) -> tuple[object, int]:
    """Inicia o servidor aiohttp do dashboard em background buscando porta livre."""
    from aiohttp import web

    from src.dashboard.server import create_dashboard_app

    dash_app = create_dashboard_app(db, orchestrator=orchestrator)
    runner = web.AppRunner(dash_app)
    await runner.setup()
    bound_port = port
    for p in range(port, port + 20):
        try:
            dash_site = web.TCPSite(runner, "0.0.0.0", p)
            await dash_site.start()
            bound_port = p
            break
        except OSError as exc:
            if exc.errno == 98 and p < port + 19:
                continue
    logger.info("Dashboard Web ativo em: http://localhost:%d", bound_port)
    return runner, bound_port


def _parse_cli_and_settings() -> tuple[argparse.Namespace, Any]:
    """Processa argumentos de linha de comando e aplica overrides de configuração."""
    parser = argparse.ArgumentParser(description="Vertex-bot Trading & Scanning Engine")
    parser.add_argument(
        "--simulate-mock-stream",
        action="store_true",
        help="Executa com streaming mockado local para testes e demonstração do ciclo completo",
    )
    parser.add_argument(
        "--provider",
        choices=["hybrid", "mature", "graduations", "pumpportal", "indexed", "rpc"],
        help="Substitui o provedor de ingestão de tokens (hybrid, mature, graduations, pumpportal, indexed, rpc)",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Inicia o Dashboard Web em segundo plano (padrão na porta 8080)",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8080,
        help="Porta para o Dashboard Web (padrão: 8080)",
    )
    parser.add_argument(
        "--dashboard-only",
        action="store_true",
        help="Inicia apenas o Dashboard Web em processo avulso (mesmo comportamento de run_dashboard.py)",
    )
    args = parser.parse_args()

    settings = get_settings()
    provider_map: dict[str, Literal["MATURE_POOLS", "INDEXED", "RAW_RPC", "PUMPPORTAL", "HYBRID", "GRADUATIONS"]] = {
        "hybrid": "HYBRID",
        "mature": "MATURE_POOLS",
        "graduations": "GRADUATIONS",
        "pumpportal": "PUMPPORTAL",
        "indexed": "INDEXED",
        "rpc": "RAW_RPC",
    }
    if args.provider and args.provider in provider_map:
        settings.SCANNER_PROVIDER = provider_map[args.provider]

    return args, settings


async def main() -> None:
    args, settings = _parse_cli_and_settings()

    if args.dashboard_only:
        from src.dashboard.server import run_dashboard_server

        logger.info("Iniciando Dashboard Web de forma avulsa (--dashboard-only) na porta %d...", args.dashboard_port)
        await run_dashboard_server(
            db_path=settings.SQLITE_DB_PATH,
            port=args.dashboard_port,
        )
        return

    orchestrator = VertexBotOrchestrator(settings)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    orchestrator.stop_event = stop_event

    def handle_exit() -> None:
        logger.info("Sinal de encerramento recebido. Desligando...")
        stop_event.set()
        asyncio.create_task(orchestrator.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_exit)
        except NotImplementedError:
            pass

    await orchestrator.initialize()

    dashboard_runner: Any = None
    if args.dashboard:
        dashboard_runner, _ = await _start_background_dashboard(
            orchestrator.db,
            args.dashboard_port,
            orchestrator=orchestrator,
        )

    try:
        await orchestrator.start(run_mock_stream=args.simulate_mock_stream)

        if args.simulate_mock_stream:
            # No modo mock de demonstração, aguarda 8 segundos para a simulação completar e encerra
            await asyncio.sleep(8.0)
            await orchestrator.stop()
        else:
            # No modo normal, aguarda sinal de interrupção
            try:
                await stop_event.wait()
            except asyncio.CancelledError:
                pass
    finally:
        if dashboard_runner:
            await dashboard_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
