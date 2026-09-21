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
from typing import Any, Literal, cast

from src.config.settings import Settings, get_settings
from src.database.connection import DatabaseManager
from src.database.models import PositionState, TokenMetadata
from src.database.repository import OrdersRepository, PositionsRepository, TokensRepository
from src.engine.interface import IExecutionEngine
from src.engine.interface import IExecutionEngine as ExecutionEngine
from src.engine.live import LiveExecutionEngine
from src.engine.paper import PaperExecutionEngine
from src.engine.price_feed import DexScreenerPriceFeed
from src.engine.priority_pool import PriorityPoolManager
from src.engine.reentry import ReentryRiskManager
from src.engine.risk import RiskManager
from src.engine.tracker import PositionTracker
from src.scanner.client import ResilientRPCClient
from src.scanner.listener import create_scanner
from src.security.chart_auditor import ChartHealthAuditor
from src.security.market_dynamics import MarketDynamicsValidator
from src.security.validator import SecurityValidator
from src.utils.logger import setup_logger
from src.utils.metrics import TelemetryCollector

logger = setup_logger("vertex.main")


class VertexBotOrchestrator:
    """Orquestrador central do ciclo de vida assíncrono do Vertex-bot."""

    def __init__(
        self,
        settings: Settings | None = None,
        execution_mode: Literal["PAPER", "LIVE"] | None = None,
        start_enabled: bool | None = None,
        db: DatabaseManager | None = None,
    ) -> None:
        self.settings: Settings = settings if settings is not None else Settings()
        self.execution_mode: Literal["PAPER", "LIVE"] = (
            execution_mode if execution_mode is not None else self.settings.EXECUTION_MODE
        )
        self.db: DatabaseManager = db if db is not None else DatabaseManager(self.settings.SQLITE_DB_PATH)
        self.tokens_repo: TokensRepository = TokensRepository(self.db)
        self.positions_repo: PositionsRepository = PositionsRepository(self.db)
        self.orders_repo: OrdersRepository = OrdersRepository(self.db)

        # Provedor de Cotações Contínuas
        self.price_feed: DexScreenerPriceFeed = DexScreenerPriceFeed(
            base_url=self.settings.DEXSCREENER_API_BASE_URL,
        )

        # Cliente RPC Resiliente
        self.rpc_client: ResilientRPCClient = ResilientRPCClient(
            primary_url=self.settings.PRIMARY_RPC_HTTP_URL,
            secondary_url=self.settings.SECONDARY_RPC_HTTP_URL,
        )

        # Camada de Segurança e Dinâmica de Mercado
        self.market_validator: MarketDynamicsValidator = MarketDynamicsValidator(
            min_volume_1h_usd=self.settings.MIN_VOLUME_1H_USD,
            min_buy_ratio_5m_pct=self.settings.MIN_BUY_RATIO_5M_PCT,
            min_price_change_5m_pct=self.settings.MIN_PRICE_CHANGE_5M_PCT,
            min_age_hours_scalp=float(self.settings.MIN_TOKEN_AGE_HOURS_SCALP),
            max_age_hours_scalp=float(self.settings.MAX_TOKEN_AGE_HOURS_SCALP),
            min_age_hours_swing=float(self.settings.MIN_TOKEN_AGE_HOURS_SWING),
            max_age_hours_swing=float(self.settings.MAX_TOKEN_AGE_HOURS_SWING),
            min_liquidity_scalp_usd=self.settings.MIN_LIQUIDITY_USD,
            min_liquidity_swing_usd=self.settings.MIN_LIQUIDITY_SWING_USD,
            max_liquidity_usd=self.settings.MAX_LIQUIDITY_USD,
            max_seller_to_buyer_ratio=self.settings.MAX_SELLER_TO_BUYER_RATIO,
            min_liquidity_to_volume_ratio=self.settings.MIN_LIQUIDITY_TO_VOLUME_RATIO,
            min_unique_traders_24h=self.settings.MIN_UNIQUE_TRADERS_24H,
            max_parabolic_1h_gain_pct=self.settings.MAX_PARABOLIC_1H_GAIN_PCT,
        )
        self.validator: SecurityValidator = SecurityValidator(
            tokens_repo=self.tokens_repo,
            rpc_client=self.rpc_client,
            min_liquidity_usd=self.settings.MIN_LIQUIDITY_USD,
            max_top10_pct=float(self.settings.MAX_TOP10_HOLDERS_PCT),
            max_tax_pct=float(self.settings.MAX_BUY_TAX_PCT),
            market_validator=self.market_validator,
            strategy_mode=self.settings.TRADING_STRATEGY_MODE,
        )

        # Auditor de Saúde Gráfica e Estrutura de Velas (Anti-Dump / 3 Velas Mínimas de 1h)
        self.chart_auditor: ChartHealthAuditor = ChartHealthAuditor(
            geckoterminal_base_url=getattr(self.settings, "GECKOTERMINAL_API_BASE_URL", "https://api.geckoterminal.com"),
            dexscreener_base_url=getattr(self.settings, "DEXSCREENER_API_BASE_URL", "https://api.dexscreener.com"),
            min_candles_required=int(getattr(self.settings, "CHART_MIN_CANDLES", 3)),
            candle_timeframe=str(getattr(self.settings, "CHART_CANDLE_TIMEFRAME", "hour")),
            candle_aggregate=int(getattr(self.settings, "CHART_CANDLE_AGGREGATE", 1)),
            min_volume_1h_usd=Decimal(str(getattr(self.settings, "MIN_VOLUME_1H_USD", "3000.0"))),
            min_liquidity_usd=self.settings.MIN_LIQUIDITY_USD,
        )

        # Motores de Execução (Simulação e Real On-Chain)
        self.paper_engine: PaperExecutionEngine = PaperExecutionEngine(
            positions_repo=self.positions_repo,
            orders_repo=self.orders_repo,
            initial_balance_usd=self.settings.PAPER_INITIAL_WALLET_USD,
            simulated_latency_ms=self.settings.PAPER_SIMULATED_LATENCY_MS,
            trailing_drop_pct=self.settings.TRAILING_STOP_DROP_PCT / Decimal("100.0"),
            price_feed=self.price_feed,
        )
        self.live_engine: LiveExecutionEngine = LiveExecutionEngine(
            positions_repo=self.positions_repo,
            orders_repo=self.orders_repo,
            solana_rpc_url=self.settings.PRIMARY_RPC_HTTP_URL,
            solana_private_key_base58=self.settings.SOLANA_PRIVATE_KEY_BASE58 or self.settings.WALLET_PRIVATE_KEY_BASE58,
            evm_private_key=self.settings.EVM_PRIVATE_KEY or self.settings.EVM_WALLET_PRIVATE_KEY,
            confirm_live_trading=self.settings.CONFIRM_LIVE_TRADING,
            price_feed=self.price_feed,
            max_slippage_pct=self.settings.LIVE_MAX_SLIPPAGE_PCT,
            jito_tip_lamports=self.settings.LIVE_JITO_TIP_LAMPORTS,
            trailing_drop_pct=self.settings.TRAILING_STOP_DROP_PCT / Decimal("100.0"),
            estimated_sol_price_usd=self.settings.ESTIMATED_SOL_PRICE_USD,
        )

        # Gestão de Risco
        self.risk_manager: RiskManager = RiskManager(
            scalp_max_hold_seconds=float(getattr(self.settings, "SCALP_MAX_HOLD_MINUTES", 60.0)) * 60.0,
            scalp_target_gain_pct=getattr(self.settings, "SCALP_TARGET_GAIN_PCT", Decimal("100.0")),
            swing_max_hold_seconds=float(getattr(self.settings, "SWING_MAX_HOLD_HOURS", 24.0)) * 3600.0,
            swing_target_gain_pct=getattr(self.settings, "SWING_TARGET_GAIN_PCT", Decimal("2000.0")),
            swing_max_hourly_drop_pct=getattr(self.settings, "SWING_MAX_HOURLY_DROP_PCT", Decimal("15.0")),
            trailing_drop_pct=self.settings.TRAILING_STOP_DROP_PCT / Decimal("100.0"),
            emergency_stop_loss_pct=self.settings.EMERGENCY_STOP_LOSS_PCT / Decimal("100.0"),
            swing_initial_stop_loss_pct=self.settings.SWING_INITIAL_STOP_LOSS_PCT / Decimal("100.0"),
            swing_tier1_mult=self.settings.SWING_TIER1_TARGET_MULT,
            swing_tier2_mult=self.settings.SWING_TIER2_TARGET_MULT,
            swing_tier3_mult=self.settings.SWING_TIER3_TARGET_MULT,
            swing_tier4_mult=self.settings.SWING_TIER4_TARGET_MULT,
            swing_tier5_mult=getattr(self.settings, "SWING_TIER5_TARGET_MULT", Decimal("21.0")),
            swing_trailing_drop_pct=self.settings.SWING_TRAILING_DROP_PCT / Decimal("100.0"),
            break_even_gain_pct=self.settings.BREAK_EVEN_GAIN_PCT,
        )
        self.paper_tracker: PositionTracker = PositionTracker(
            engine=self.paper_engine,
            positions_repo=self.positions_repo,
            risk_manager=self.risk_manager,
            on_position_closed=self._handle_position_closed,
        )
        self.live_tracker: PositionTracker = PositionTracker(
            engine=self.live_engine,
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
            settings=self.settings,
            detection_queue=self.detection_queue,
        )

        # Gestor de Reentrada Inteligente (Anti-Falling-Knife)
        self.reentry_manager: ReentryRiskManager = ReentryRiskManager(
            trailing_cooloff_sec=float(getattr(self.settings, "REENTRY_TRAILING_COOLOFF_SEC", 300.0)),
            stoploss_cooloff_sec=float(getattr(self.settings, "REENTRY_STOPLOSS_COOLOFF_SEC", 1800.0)),
            min_bounce_pct=getattr(self.settings, "REENTRY_MIN_BOUNCE_PCT", Decimal("3.0")),
            max_post_exit_drop_pct=getattr(self.settings, "REENTRY_MAX_DROP_PCT", Decimal("25.0")),
            min_liquidity_usd=self.settings.MIN_LIQUIDITY_USD,
        )

        # Gestor do Pool de Prioridades (Ativos Aprovados e Negociados com Precedência)
        self.priority_pool: PriorityPoolManager = PriorityPoolManager(
            mode=str(self.execution_mode).lower(),
            data_dir="data",
            min_alive_liquidity_usd=self.settings.MIN_LIQUIDITY_USD,
        )

        self.is_running: bool = False
        self.is_paused: bool = False
        self.run_mock_stream: bool = False
        self._tasks: list[asyncio.Task[None]] = []
        self.stop_event: asyncio.Event | None = None
        self._last_handled_ipc_id: str | None = None
        self._entry_lock: asyncio.Lock | None = None
        self._waiting_queue_lock: asyncio.Lock | None = None

        # Estados segregados de operação
        self.paper_enabled: bool = False
        self.live_enabled: bool = False
        is_test = self._is_test_env()
        if start_enabled is not None:
            self.paper_enabled = bool(start_enabled and self.execution_mode == "PAPER")
            self.live_enabled = bool(start_enabled and self.execution_mode == "LIVE")
        elif is_test:
            self.paper_enabled = (self.execution_mode == "PAPER")
            self.live_enabled = (self.execution_mode == "LIVE")

        self.paper_paused: bool = False
        self.live_paused: bool = False

        # Fila de Espera de Tokens Aprovados (Aguardando Vaga ou Saldo)
        self.waiting_tokens: dict[str, dict[str, Any]] = {}
        self._load_waiting_tokens()

        # Restaura configurações persistidas anteriormente se existirem em disco
        self._load_persisted_config()

    @property
    def execution_engine(self) -> ExecutionEngine:
        """Motor de execução primário (compatibilidade com interfaces e testes)."""
        return self.live_engine if self.execution_mode == "LIVE" else self.paper_engine

    @property
    def position_tracker(self) -> PositionTracker:
        """Tracker de posições primário (compatibilidade com interfaces e testes)."""
        return self.live_tracker if self.execution_mode == "LIVE" else self.paper_tracker

    def _is_test_env(self) -> bool:
        """Determina se o orquestrador está rodando em ambiente de teste automatizado."""
        if "PYTEST_CURRENT_TEST" in os.environ:
            return True
        db_path_str = str(self.settings.SQLITE_DB_PATH)
        return db_path_str == ":memory:" or db_path_str.startswith("/tmp") or "pytest" in db_path_str

    def _get_mode_dir(self) -> Path:
        """Retorna o diretório base de arquivos persistidos isolados pelo modo."""
        m = self.execution_mode.lower()
        if self._is_test_env():
            db_path_str = str(self.settings.SQLITE_DB_PATH)
            if db_path_str != ":memory:":
                d = Path(db_path_str).parent / m
                d.mkdir(parents=True, exist_ok=True)
                return d
        d = Path(f"data/{m}")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _get_waiting_tokens_path(self) -> Path:
        """Caminho do arquivo JSON de tokens aprovados em fila de espera do modo."""
        p = self._get_mode_dir() / "waiting_tokens.json"
        if not p.exists() and self.execution_mode == "PAPER" and not self._is_test_env():
            fallback = Path("data/waiting_tokens.json")
            if fallback.exists():
                return fallback
        return p

    def _load_waiting_tokens(self) -> None:
        """Carrega tokens aprovados em espera salvos em disco."""
        w_file = self._get_waiting_tokens_path()
        if not w_file.exists():
            return
        try:
            raw = json.loads(w_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.waiting_tokens = raw
                logger.info("⏳ [FILA DE ESPERA RESTAURADA (%s)] %d tokens carregados de %s", self.execution_mode, len(self.waiting_tokens), w_file)
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
            if self.execution_mode == "PAPER" and not self._is_test_env() and w_file != Path("data/waiting_tokens.json"):
                legacy = Path("data/waiting_tokens.json")
                legacy.write_text(json.dumps(self.waiting_tokens, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("Falha ao salvar %s: %s", w_file, exc)

    def _get_config_path(self) -> Path:
        """Caminho do arquivo JSON de configurações persistidas do modo."""
        p = self._get_mode_dir() / "bot_config.json"
        if not p.exists() and self.execution_mode == "PAPER" and not self._is_test_env():
            fallback = Path("data/bot_config.json")
            if fallback.exists():
                return fallback
        return p

    def _get_control_path(self) -> Path:
        """Caminho do arquivo de comando IPC do modo."""
        p = self._get_mode_dir() / "bot_control.json"
        if not p.exists() and self.execution_mode == "PAPER" and not self._is_test_env():
            fallback = Path("data/bot_control.json")
            if fallback.exists():
                return fallback
        return p

    def _get_status_path(self) -> Path:
        """Caminho do arquivo de status de telemetria do modo."""
        return self._get_mode_dir() / "bot_status.json"

    def _get_paper_session_path(self) -> Path:
        """Caminho do arquivo de sessão simulada."""
        p = self._get_mode_dir() / "paper_session.json"
        if not p.exists() and self.execution_mode == "PAPER" and not self._is_test_env():
            fallback = Path("data/paper_session.json")
            if fallback.exists():
                return fallback
        return p

    def _load_persisted_config(self) -> None:
        """Carrega e aplica configurações previamente salvas pelo usuário no Dashboard."""
        cfg_file = self._get_config_path()
        if self._is_test_env() and (not cfg_file.exists() or str(cfg_file).startswith("data/")):
            return
        if not cfg_file.exists():
            return
        try:
            raw = json.loads(cfg_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                strat_mode = str(getattr(self.settings, "TRADING_STRATEGY_MODE", "DUAL")).upper()
                if raw.get("max_token_age_hours") == 3.0 and strat_mode in ("DUAL", "SCALP_ONLY"):
                    raw["max_token_age_hours"] = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SCALP", 720.0))
                self._apply_config_payload(raw, save_to_disk=False)
                logger.info("⚙️ [CONFIGURAÇÕES PERSISTIDAS RESTAURADAS] %s aplicado com sucesso.", cfg_file)
        except Exception as exc:
            logger.warning("Falha ao restaurar %s: %s", cfg_file, exc)

    def _save_persisted_config(self) -> None:
        """Salva as configurações atuais em bot_config.json para sobrevivência a reinicializações."""
        cfg_file = self._get_config_path()
        try:
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            wallet_usd_val = float(
                getattr(
                    self.execution_engine,
                    "balance_usd",
                    getattr(self.execution_engine, "_last_known_balance_usd", Decimal("0.0")),
                )
            )
            payload = {
                "mode": self.execution_mode,
                "paper_buy_amount_usd": float(self.settings.PAPER_BUY_AMOUNT_USD),
                "live_buy_amount_usd": float(self.settings.LIVE_BUY_AMOUNT_USD),
                "max_concurrent_positions": int(self.settings.MAX_CONCURRENT_POSITIONS),
                "live_max_concurrent_positions": int(self.settings.LIVE_MAX_CONCURRENT_POSITIONS),
                "wallet_balance_usd": wallet_usd_val,
                "paper_initial_wallet_usd": float(self.settings.PAPER_INITIAL_WALLET_USD),
                "min_trade_amount_usd": float(self.settings.MIN_TRADE_AMOUNT_USD),
                "max_token_age_hours": float(self.settings.MAX_TOKEN_AGE_HOURS),
                "break_even_gain_pct": float(self.settings.BREAK_EVEN_GAIN_PCT),
                "trailing_stop_drop_pct": float(self.settings.TRAILING_STOP_DROP_PCT),
                "emergency_stop_loss_pct": float(self.settings.EMERGENCY_STOP_LOSS_PCT),
                "max_slippage_pct": float(self.settings.MAX_SLIPPAGE_PCT),
                "live_max_slippage_pct": float(self.settings.LIVE_MAX_SLIPPAGE_PCT),
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
            tmp_path = cfg_file.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp_path.replace(cfg_file)
            if self.execution_mode == "PAPER" and not self._is_test_env() and cfg_file != Path("data/bot_config.json"):
                legacy_file = Path("data/bot_config.json")
                legacy_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            logger.info("💾 Configurações persistidas salvas em %s", cfg_file)
        except Exception as exc:
            logger.warning("Falha ao gravar %s: %s", cfg_file, exc)

    async def initialize(self) -> None:
        """Inicializa banco de dados e carrega posições abertas pré-existentes."""
        """Inicializa banco de dados e carrega posições abertas pré-existentes de ambos os modos."""
        await self.db.initialize()
        self._load_waiting_tokens()

        # No modo PAPER, inicia sempre um novo teste de simulação do zero (limpando posições e ordens),
        # mas preservando os tokens_catalogados (inteligência de triagem e rejeições).
        if self.execution_mode == "PAPER":
            logger.info("Modo PAPER: iniciando novo teste simulado limpo (zerando posições, ordens e saldo da carteira)...")
            await self.positions_repo.clear_paper_trading_data()
            if not self._is_test_env():
                self.settings.PAPER_INITIAL_WALLET_USD = Decimal("0.0")
                if hasattr(self.execution_engine, "balance_usd"):
                    self.execution_engine.balance_usd = Decimal("0.0")
                self.paper_engine.balance_usd = Decimal("0.0")
                self._save_persisted_config()
            self._write_paper_session_state(initial_balance=self.paper_engine.balance_usd)
        # Restaura posições abertas para PAPER e LIVE de forma isolada
        open_paper = await self.positions_repo.get_open_positions(mode="PAPER")
        for pos in open_paper:
            await self.paper_tracker.register_position(pos)

        open_live = await self.positions_repo.get_open_positions(mode="LIVE")
        for pos in open_live:
            await self.live_tracker.register_position(pos)

        await self.reconcile_wallet_balance()
        logger.info(
            "Inicialização concluída. Posições ativas restauradas: Paper=%d, Live=%d",
            len(open_paper),
            len(open_live),
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

    async def start_paper(self) -> None:
        """Inicia operações simuladas (PAPER)."""
        logger.info("▶️ [MODO SIMULAÇÃO ATIVADO] Iniciando operações simuladas...")
        self.paper_enabled = True
        self.paper_paused = False
        await self.reconcile_wallet_balance()
        self._write_heartbeat_sync(self.is_running)

    def pause_paper(self) -> None:
        """Pausa abertura de novas posições simuladas."""
        self.paper_paused = True
        if self.execution_mode == "PAPER":
            self.is_paused = True
        logger.info("⏸️ [SIMULAÇÃO PAUSADA] Novas entradas simuladas suspensas.")
        self._write_heartbeat_sync(self.is_running)

    def resume_paper(self) -> None:
        """Retoma operações simuladas."""
        self.paper_paused = False
        if self.execution_mode == "PAPER":
            self.is_paused = False
        logger.info("▶️ [SIMULAÇÃO RETOMADA] Abertura de posições simuladas reativada.")
        self._write_heartbeat_sync(self.is_running)

    async def stop_paper(self) -> None:
        """Encerra a simulação: limpa dados simulados da carteira sem fechar o bot."""
        logger.info("⏹️ [SIMULAÇÃO ENCERRADA] Desativando modo simulado e zerando dados de simulação...")
        self.paper_enabled = False
        self.paper_paused = False
        if self.execution_mode == "PAPER":
            self.is_paused = False
        self.paper_tracker.active_positions.clear()
        await self.positions_repo.clear_paper_trading_data()
        self.reentry_manager.clear_history()
        self.waiting_tokens.clear()
        self._save_waiting_tokens()
        self.settings.PAPER_INITIAL_WALLET_USD = Decimal("0.0")
        if hasattr(self.execution_engine, "balance_usd"):
            self.execution_engine.balance_usd = Decimal("0.0")
        self.paper_engine.balance_usd = Decimal("0.0")
        self._write_paper_session_state(initial_balance=Decimal("0.0"))
        self._save_persisted_config()
        self._write_heartbeat_sync(self.is_running)
        logger.info("Banca de simulação zerada para $0.00. Bot permanece ativo.")

    async def start_live(self) -> None:
        """Inicia operações reais on-chain (LIVE) se houver carteira conectada."""
        if not self.live_engine.has_connected_wallet():
            raise ValueError("Nenhuma carteira real conectada. Conecte uma carteira Solana ou EVM antes de iniciar operações reais.")
        logger.info("🔴 [MODO REAL ATIVADO] Iniciando operações reais on-chain...")
        self.live_enabled = True
        self.live_paused = False
        if self.execution_mode == "LIVE":
            self.is_paused = False
        self._write_heartbeat_sync(self.is_running)

    def pause_live(self) -> None:
        """Pausa abertura de novas posições reais."""
        self.live_paused = True
        if self.execution_mode == "LIVE":
            self.is_paused = True
        logger.info("⏸️ [OPERAÇÕES REAIS PAUSADAS] Novas entradas reais suspensas.")
        self._write_heartbeat_sync(self.is_running)

    def resume_live(self) -> None:
        """Retoma operações reais."""
        self.live_paused = False
        if self.execution_mode == "LIVE":
            self.is_paused = False
        logger.info("▶️ [OPERAÇÕES REAIS RETOMADAS] Abertura de posições reais reativada.")
        self._write_heartbeat_sync(self.is_running)

    async def stop_live(self) -> None:
        """Encerra operações reais (desativa novas compras em modo real sem fechar o bot)."""
        logger.info("⏹️ [OPERAÇÕES REAIS ENCERRADAS] Novas compras em modo real desativadas.")
        self.live_enabled = False
        self.live_paused = False
        self._write_heartbeat_sync(self.is_running)

    def pause(self) -> None:
        """Pausa a abertura de novas posições pelo bot."""
        """Pausa a abertura de novas posições pelo bot em todos os modos."""
        self.is_paused = True
        self.paper_paused = True
        self.live_paused = True
        logger.info("⏸️ [BOT PAUSADO] Novas entradas suspensas. Monitoramento de posições abertas permanece ativo.")

    def resume(self) -> None:
        """Retoma as operações normais do bot."""
        self.is_paused = False
        self.paper_paused = False
        self.live_paused = False
        logger.info("▶️ [BOT RETOMADO] Abertura de posições e triagem reativadas com sucesso.")

    def deposit_wallet(self, amount_usd: Decimal) -> Decimal:
        """Adiciona capital simulado à carteira no modo PAPER."""
        self.paper_engine.balance_usd += amount_usd
        self.settings.PAPER_INITIAL_WALLET_USD += amount_usd
        self._write_paper_session_state(initial_balance=self.settings.PAPER_INITIAL_WALLET_USD)
        self._save_persisted_config()
        logger.info(
            "💵 [DEPÓSITO SIMULADO] +$%.2f adicionados à carteira simulada. Saldo atual: $%.2f (Banca Base: $%.2f)",
            amount_usd,
            self.paper_engine.balance_usd,
            self.settings.PAPER_INITIAL_WALLET_USD,
        )
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.create_task(self._try_fill_slots_from_waiting_queue())
        except RuntimeError:
            pass
        return Decimal(str(self.paper_engine.balance_usd))

    async def close_position_manually(self, position_id: int, mode: str | None = None) -> bool:
        """Encerra manualmente uma posição ativa a pedido do usuário pelo dashboard."""
        logger.info("🛑 [COMANDO MANUAL] Encerrando posição #%d a mercado...", position_id)
        if (mode and mode.upper() == "LIVE") or position_id in self.live_tracker.active_positions:
            success = await self.live_tracker.close_position_manually(position_id)
        else:
            success = await self.paper_tracker.close_position_manually(position_id)
        if success:
            if hasattr(self, "dashboard_server") and self.dashboard_server:
                broadcast = getattr(self.dashboard_server, "broadcast_event", None)
                if callable(broadcast):
                    try:
                        await broadcast("trade", {"action": "manual_close", "position_id": position_id})
                    except Exception:
                        pass
        return success

    async def open_position_manually(
        self,
        position_id: int | None = None,
        token_address: str | None = None,
        strategy_type: str = "SCALP",
        mode: str = "PAPER",
    ) -> tuple[bool, str]:
        """Abre manualmente uma nova posição para o token informado ou baseado em uma posição existente."""
        resolved_addr = token_address
        resolved_strat = strategy_type
        target_mode = mode.upper()

        if position_id is not None:
            db_pos = await self.positions_repo.get_by_id(position_id)
            if db_pos:
                resolved_addr = db_pos.token_address
                resolved_strat = db_pos.strategy_type or strategy_type
                if db_pos.mode:
                    target_mode = db_pos.mode.upper()

        if not resolved_addr:
            return False, "Endereço de token inválido ou não informado."

        tracker = self.live_tracker if target_mode == "LIVE" else self.paper_tracker
        engine = self.live_engine if target_mode == "LIVE" else self.paper_engine

        if target_mode == "LIVE" and not self.live_engine.has_connected_wallet():
            return False, "Nenhuma carteira real conectada para operações LIVE."

        max_positions = int(getattr(self.settings, "LIVE_MAX_CONCURRENT_POSITIONS" if target_mode == "LIVE" else "MAX_CONCURRENT_POSITIONS", 2))
        active_count = len(tracker.active_positions)
        if active_count >= max_positions:
            msg = f"Limite de posições concorrentes atingido ({active_count}/{max_positions}). Encerre uma posição antes de abrir outra."
            logger.warning("🚫 [COMPRA MANUAL BLOQUEADA (%s)] %s", target_mode, msg)
            return False, msg

        configured_buy = Decimal(str(getattr(self.settings, "LIVE_BUY_AMOUNT_USD" if target_mode == "LIVE" else "PAPER_BUY_AMOUNT_USD", "1.0")))
        min_trade = Decimal(str(getattr(self.settings, "MIN_TRADE_AMOUNT_USD", "1.0")))
        buy_amount = configured_buy if configured_buy > Decimal("0.0") else min_trade
        available_cash = engine.balance_usd

        if available_cash < buy_amount:
            msg = f"Saldo em caixa insuficiente: Disponível ${available_cash:.2f} < ${buy_amount:.2f} necessário."
            logger.warning("🚫 [COMPRA MANUAL BLOQUEADA (%s)] %s", target_mode, msg)
            return False, msg

        token_meta = await self.tokens_repo.get_token_metadata_by_address(resolved_addr)
        if not token_meta:
            token_meta = TokenMetadata(
                address=resolved_addr,
                symbol="MANUAL",
                name="Manual Entry Token",
                chain="solana",
                dex="raydium",
            )

        logger.info(
            "🚀 [COMPRA MANUAL (%s)] Abrindo posição %s para %s ($%.2f)...",
            target_mode,
            resolved_strat,
            token_meta.symbol or resolved_addr[:8],
            buy_amount,
        )

        pos = await engine.execute_buy(
            token_meta,
            amount_usd=buy_amount,
            strategy_type=resolved_strat,
        )

        if pos:
            self.telemetry.record_trade_opened()
            await tracker.register_position(pos)
            if hasattr(self, "dashboard_server") and self.dashboard_server:
                broadcast = getattr(self.dashboard_server, "broadcast_event", None)
                if callable(broadcast):
                    try:
                        await broadcast("trade", {"action": "manual_buy", "position_id": pos.id, "mode": target_mode})
                    except Exception:
                        pass
            return True, f"Posição #{pos.id} ({target_mode}) aberta com sucesso para {token_meta.symbol or resolved_addr[:8]} ({resolved_strat})!"
        else:
            return False, "Falha na execução da ordem de compra pelo motor de execução."

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
        if self.execution_mode != "PAPER":
            if hasattr(self.execution_engine, "get_wallet_balance_usd"):
                bal = await self.execution_engine.get_wallet_balance_usd()
                self._write_heartbeat_sync(self.is_running)
                return Decimal(str(bal))
            return Decimal("0.0")

        try:
            pnl_data = await self.positions_repo.get_pnl_summary(
                mode="PAPER",
                initial_wallet_usd=float(self.settings.PAPER_INITIAL_WALLET_USD),
            )
            active_capital = Decimal(str(pnl_data.get("active_capital_usd", 0.0)))
            realized_pnl = Decimal(str(pnl_data.get("total_pnl_usd", 0.0)))
            true_cash = max(Decimal("0.0"), self.settings.PAPER_INITIAL_WALLET_USD + realized_pnl - active_capital)
            if hasattr(self.execution_engine, "balance_usd"):
                self.execution_engine.balance_usd = true_cash
            self.paper_engine.balance_usd = true_cash

            if self.live_engine.has_connected_wallet():
                await self.live_engine.get_wallet_balance_usd()

            self._write_heartbeat_sync(self.is_running)
            return true_cash
        except Exception as exc:
            logger.warning("Falha ao reconciliar saldo de carteira: %s", exc)
            return getattr(self.execution_engine, "balance_usd", Decimal("0.0"))

    async def restart_paper_session(self, new_balance: Decimal | None = None) -> None:
        """Reinicia a sessão simulada: limpa posições, ordens, zera IDs para #1 e reinicia balanço."""
        if self.execution_mode != "PAPER":
            logger.warning("Tentativa de reiniciar sessão simulada em modo LIVE ignorada por segurança.")
            return

        logger.info("🔄 [REINÍCIO DE SIMULAÇÃO] Limpando dados de trades e reiniciando sessão pelo Dashboard...")
        self.position_tracker.active_positions.clear()
        self.paper_tracker.active_positions.clear()
        await self.positions_repo.clear_paper_trading_data()
        self.reentry_manager.clear_history()
        self.waiting_tokens.clear()
        self._save_waiting_tokens()

        target_balance = new_balance if new_balance is not None else Decimal("0.0")
        self.settings.PAPER_INITIAL_WALLET_USD = target_balance
        if hasattr(self.execution_engine, "balance_usd"):
            self.execution_engine.balance_usd = target_balance
        self.paper_engine.balance_usd = target_balance
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
                if new_wallet >= Decimal("0.0"):
                    self.settings.PAPER_INITIAL_WALLET_USD = new_wallet
                    allocated = sum(
                        (p.allocated_capital_usd for p in self.position_tracker.active_positions.values()),
                        Decimal("0.0"),
                    )
                    avail = max(Decimal("0.0"), new_wallet - allocated)
                    if hasattr(self.execution_engine, "balance_usd"):
                        self.execution_engine.balance_usd = avail
                    self.paper_engine.balance_usd = avail
                    self._write_paper_session_state(initial_balance=new_wallet)
                    self._write_heartbeat_sync(self.is_running)
                    logger.info("💰 Saldo da carteira atualizado para $%.2f (Disponível: $%.2f, Alocado: $%.2f)", new_wallet, avail, allocated)
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
        if payload.get("live_buy_amount_usd") is not None:
            live_buy = Decimal(str(payload["live_buy_amount_usd"]))
            if live_buy > Decimal("0.0"):
                self.settings.LIVE_BUY_AMOUNT_USD = live_buy
        if payload.get("live_max_concurrent_positions") is not None:
            self.settings.LIVE_MAX_CONCURRENT_POSITIONS = int(payload["live_max_concurrent_positions"])
        if payload.get("live_max_slippage_pct") is not None:
            self.settings.LIVE_MAX_SLIPPAGE_PCT = Decimal(str(payload["live_max_slippage_pct"]))
            if hasattr(self, "live_engine") and self.live_engine:
                self.live_engine.max_slippage_pct = self.settings.LIVE_MAX_SLIPPAGE_PCT / Decimal("100.0")
        if payload.get("live_jito_tip_lamports") is not None:
            self.settings.LIVE_JITO_TIP_LAMPORTS = int(payload["live_jito_tip_lamports"])
            if hasattr(self, "live_engine") and self.live_engine:
                self.live_engine.jito_tip_lamports = self.settings.LIVE_JITO_TIP_LAMPORTS


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
        """Emite arquivos de status atômicos em disco para PAPER e LIVE para consumo pelo Dashboard."""
        try:
            status_file = self._get_status_path()
            status_file.parent.mkdir(parents=True, exist_ok=True)
            wallet_usd_val = float(
                getattr(
                    self.execution_engine,
                    "balance_usd",
                    getattr(self.execution_engine, "_last_known_balance_usd", Decimal("0.0")),
                )
            )
            initial_val = float(
                self.settings.PAPER_INITIAL_WALLET_USD
                if self.execution_mode == "PAPER"
                else wallet_usd_val
            )
            payload = {
                "is_running": is_running,
                "is_paused": self.is_paused,
                "mode": self.execution_mode,
                "wallet_balance_usd": wallet_usd_val,
                "initial_wallet_usd": initial_val,
                "active_positions_count": len(self.position_tracker.active_positions),
                "pid": os.getpid(),
                "timestamp": datetime.now(UTC).timestamp(),
            }
            tmp_file = status_file.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp_file.replace(status_file)
            # 1. Status do Modo Simulação (PAPER)
            if not self._is_test_env() or self.execution_mode == "PAPER":
                paper_status_file = Path("data/paper/bot_status.json")
                paper_status_file.parent.mkdir(parents=True, exist_ok=True)
                paper_payload = {
                    "is_running": is_running and self.paper_enabled,
                    "is_paused": self.paper_paused,
                    "mode": "PAPER",
                    "wallet_balance_usd": float(self.paper_engine.balance_usd),
                    "initial_wallet_usd": float(self.settings.PAPER_INITIAL_WALLET_USD),
                    "active_positions_count": len(self.paper_tracker.active_positions),
                    "pid": os.getpid(),
                    "timestamp": datetime.now(UTC).timestamp(),
                }
                tmp_paper = paper_status_file.with_suffix(".tmp")
                tmp_paper.write_text(json.dumps(paper_payload, indent=2), encoding="utf-8")
                tmp_paper.replace(paper_status_file)

            if self.execution_mode == "PAPER":
                legacy_file = Path("data/bot_status.json")
                legacy_file.parent.mkdir(parents=True, exist_ok=True)
                legacy_tmp = legacy_file.with_suffix(".tmp")
                legacy_tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                legacy_tmp.write_text(json.dumps(paper_payload, indent=2), encoding="utf-8")
                legacy_tmp.replace(legacy_file)

            # 2. Status do Modo Operações Reais (LIVE)
            if not self._is_test_env() or self.execution_mode == "LIVE":
                live_status_file = Path("data/live/bot_status.json")
                live_status_file.parent.mkdir(parents=True, exist_ok=True)
                live_balance = float(getattr(self.live_engine, "_last_known_balance_usd", Decimal("0.0")))
                live_payload = {
                    "is_running": is_running and self.live_enabled,
                    "is_paused": self.live_paused,
                    "mode": "LIVE",
                    "has_connected_wallet": self.live_engine.has_connected_wallet(),
                    "wallet_balance_usd": live_balance,
                    "initial_wallet_usd": live_balance,
                    "active_positions_count": len(self.live_tracker.active_positions),
                    "wallets": [
                        {
                            "chain": "solana",
                            "address": self.live_engine.solana_public_key or "",
                            "is_connected": self.live_engine._solana_keypair is not None,
                        },
                        {
                            "chain": "evm",
                            "address": self.live_engine.evm_address or "",
                            "is_connected": self.live_engine._evm_private_key is not None,
                        },
                    ],
                    "pid": os.getpid(),
                    "timestamp": datetime.now(UTC).timestamp(),
                }
                tmp_live = live_status_file.with_suffix(".tmp")
                tmp_live.write_text(json.dumps(live_payload, indent=2), encoding="utf-8")
                tmp_live.replace(live_status_file)
        except Exception as exc:
            logger.debug("Erro ao emitir heartbeat do bot: %s", exc)

    async def _heartbeat_worker(self) -> None:
        """Emite periodicamente o estado de integridade (heartbeat) para o Dashboard avulso."""
        while self.is_running:
            await asyncio.to_thread(self._write_heartbeat_sync, True)
            await asyncio.sleep(1.5)

    def _read_ipc_command_sync(self) -> dict[str, Any] | None:
        """Lê o arquivo de comando IPC se existir com validação estrita de modo."""
        control_file = self._get_control_path()
        if not control_file.exists() and self.execution_mode == "PAPER":
            fallback = Path("data/bot_control.json")
            if fallback.exists():
                control_file = fallback
        if not control_file.exists():
            return None
        try:
            content = control_file.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict):
                # Se o comando IPC especificar um modo e for diferente deste orquestrador, ignora!
                cmd_mode = str(data.get("mode", "")).upper()
                if cmd_mode and cmd_mode != self.execution_mode:
                    return None
                return data
        except Exception as exc:
            logger.debug("Erro ao ler %s: %s", control_file, exc)
        """Lê o arquivo de comando IPC se existir com verificação de caminhos de controle."""
        candidates = [
            self._get_control_path(),
            Path("data/paper/bot_control.json"),
            Path("data/live/bot_control.json"),
            Path("data/bot_control.json"),
        ]
        for control_file in candidates:
            if control_file.exists():
                try:
                    content = control_file.read_text(encoding="utf-8")
                    data = json.loads(content)
                    if isinstance(data, dict):
                        return data
                except Exception as exc:
                    logger.debug("Erro ao ler %s: %s", control_file, exc)
        return None

    async def _ipc_command_worker(self) -> None:
        """Escuta comandos IPC gravados pelo Dashboard executando em processo avulso."""
        # Ignora comando que já estava gravado antes de ligar o bot para não disparar ações antigas
        initial_cmd = await asyncio.to_thread(self._read_ipc_command_sync)
        if initial_cmd and "command_id" in initial_cmd:
            self._last_handled_ipc_id = str(initial_cmd["command_id"])

        while self.is_running:
            try:
                cmd_data = await asyncio.to_thread(self._read_ipc_command_sync)
                if cmd_data:
                    cmd_id = cmd_data.get("command_id")
                    cmd_name = cmd_data.get("command")
                    payload = cmd_data.get("payload", {})
                    payload = dict(cmd_data.get("payload", {}))
                    if "mode" in cmd_data and "mode" not in payload:
                        payload["mode"] = cmd_data["mode"]
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
        """Despacha a execução do comando IPC recebido para os modos corretos."""
        mode = str(payload.get("mode", "")).lower()
        if command in ("start_paper", "start") and (mode == "paper" or not mode):
            await self.start_paper()
        elif command in ("start_live", "start") and mode == "live":
            await self.start_live()
        elif command == "stop_bot" or (command == "stop" and not mode):
            await self.stop()
        elif command in ("stop_paper", "stop") and mode == "paper":
            await self.stop_paper()
        elif command in ("stop_live", "stop") and mode == "live":
            await self.stop_live()
        elif command in ("pause_paper", "pause") and (mode == "paper" or not mode):
            self.pause_paper()
        elif command in ("pause_live", "pause") and mode == "live":
            self.pause_live()
        elif command in ("resume_paper", "resume") and (mode == "paper" or not mode):
            self.resume_paper()
        elif command in ("resume_live", "resume") and mode == "live":
            self.resume_live()
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
        elif command == "close_position":
            pos_id = payload.get("position_id")
            if pos_id is not None:
                await self.close_position_manually(int(pos_id), mode=mode or None)
        elif command == "buy_more":
            pos_id = payload.get("position_id")
            token_addr = payload.get("token_address")
            strat = payload.get("strategy_type", "SCALP")
            await self.open_position_manually(
                position_id=int(pos_id) if pos_id is not None else None,
                token_address=str(token_addr) if token_addr else None,
                strategy_type=str(strat),
                mode=mode or "PAPER",
            )

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
            token = None
            try:
                token = await self.detection_queue.get()
                self.telemetry.record_detection()
                await self.tokens_repo.save_detected_token(token)

                if self.is_paused:
                    logger.debug("⏸️ [BOT PAUSADO] Ignorando abertura de posição para token %s.", token.address)
                    continue

                # Auditoria com os 6 Hard Gates
                audit = await self.validator.audit_token(token, mock_overrides=mock_overrides)

                if audit.is_approved:
                    self.telemetry.record_approval()
                    executed = False
                    if self.paper_enabled and not self.paper_paused:
                        executed = True
                        asyncio.create_task(self._evaluate_and_execute_entry(token, mode="PAPER"))
                    if self.live_enabled and not self.live_paused:
                        if self.live_engine.has_connected_wallet():
                            executed = True
                            asyncio.create_task(self._evaluate_and_execute_entry(token, mode="LIVE"))
                        else:
                            logger.warning(
                                "⚠️ [LIVE BLOQUEADO] Nenhuma carteira conectada para operações reais. Ignorando entrada real para token %s.",
                                token.address,
                            )
                    if not executed and not self.paper_enabled and not self.live_enabled:
                        logger.info("⏸️ [STANDBY] Token %s aprovado na triagem. Bot em standby (inicie Paper ou Live pela interface).", token.symbol or token.address[:8])
                else:
                    is_pending = bool(audit.details.get("is_indexing_pending")) if isinstance(audit.details, dict) else False
                    if is_pending and hasattr(self, "scanner") and hasattr(self.scanner, "incubate_token"):
                        self.scanner.incubate_token(token, reason="AGUARDANDO_LAUDO_GOPLUS", wait_minutes=15.0)
                        logger.info(
                            "🍼 [INCUBADORA QUARENTENA] Token %s (%s) transferido para a incubadora aguardando laudo GoPlus.",
                            token.symbol or token.address[:8],
                            token.address,
                        )
                    else:
                        self.telemetry.record_rejection(audit.rejection_reason or "Desconhecido")

                await asyncio.sleep(0.15)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Erro inesperado no worker de segurança: %s", exc, exc_info=True)
            finally:
                if token is not None:
                    try:
                        self.detection_queue.task_done()
                    except ValueError:
                        pass

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

    def get_incubator_tokens(self) -> list[dict[str, Any]]:
        """Retorna tokens atualmente na incubadora de maturação através dos scanners ativos."""
        if hasattr(self, "scanner") and self.scanner is not None:
            if hasattr(self.scanner, "get_incubator_tokens"):
                try:
                    res = self.scanner.get_incubator_tokens()
                    if isinstance(res, list):
                        return cast(list[dict[str, Any]], res)
                except Exception as exc:
                    logger.debug("Falha ao consultar incubadora do scanner: %s", exc)
        return []

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
        if self._waiting_queue_lock is None:
            self._waiting_queue_lock = asyncio.Lock()

        if self._waiting_queue_lock.locked():
            return

        async with self._waiting_queue_lock:
            can_run, _, min_required, max_positions = self._can_process_waiting_queue()
            if not can_run:
                return

            strat_mode = str(getattr(self.settings, "TRADING_STRATEGY_MODE", "DUAL")).upper()
            if strat_mode == "SWING_ONLY":
                max_age_hours = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SWING", 6.0))
            elif strat_mode == "SCALP_ONLY":
                max_age_hours = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SCALP", 720.0))
                max_age_hours = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_CONSOLIDATED", 87600.0))
            else:
                max_age_hours = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SCALP", 720.0))
                max_age_hours = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_CONSOLIDATED", 87600.0))
            now_utc = datetime.now(UTC)

            # 1. Primeiro drena tokens que estavam ativamente aguardando vaga na fila de espera
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

                price = live_prices.get(addr) or live_prices.get(addr.lower())
                if self._is_waiting_token_dumped(item, price):
                    self.waiting_tokens.pop(addr, None)
                    self._save_waiting_tokens()
                    continue

                token_meta = self._build_waiting_token_meta(item, price)
                if hasattr(self, "chart_auditor") and self.chart_auditor is not None:
                    is_chart_safe, chart_reason, chart_details = await self.chart_auditor.audit_token_pre_entry(token_meta)
                    if not is_chart_safe:
                        is_candles_insufficient = bool(chart_details.get("is_insufficient_candles")) if isinstance(chart_details, dict) else False
                        if is_candles_insufficient and hasattr(self, "scanner") and hasattr(self.scanner, "incubate_token"):
                            self.scanner.incubate_token(token_meta, reason="AGUARDANDO_3_VELAS_1H", wait_minutes=30.0)
                            logger.info(
                                "🍼 [FILA -> INCUBADORA] Token %s (%s) transferido para a incubadora aguardando 3 velas de 1h.",
                                token_meta.symbol or addr[:8],
                                addr,
                            )
                        else:
                            logger.info(
                                "📉 [FILA DE ESPERA] Token %s descartado por auditoria gráfica: %s",
                                addr[:8],
                                chart_reason,
                            )
                        self.waiting_tokens.pop(addr, None)
                        self._save_waiting_tokens()
                        continue

                logger.info(
                    "🚀 [FILA DE ESPERA] Slot liberado! Executando entrada para token aprovado %s (%s)",
                    token_meta.symbol or addr[:8],
                    addr,
                )
                await self._locked_evaluate_and_execute_entry(token_meta)
                self.waiting_tokens.pop(addr, None)
                self._save_waiting_tokens()

            # 2. Precedência da Lista de Prioridades (Tokens Aprovados e Negociados que continuam vivos)
            # Se ainda restarem slots disponíveis e capital, atende candidatos da Lista de Prioridades
            if hasattr(self, "priority_pool") and self.priority_pool.count() > 0:
                active_addrs = {p.token_address for p in self.position_tracker.active_positions.values()}
                if hasattr(self, "live_tracker"):
                    active_addrs.update({p.token_address for p in self.live_tracker.active_positions.values()})
                if hasattr(self, "paper_tracker"):
                    active_addrs.update({p.token_address for p in self.paper_tracker.active_positions.values()})

                priority_list = self.priority_pool.get_all_priority_tokens()
                eligible_priority = [
                    p for p in priority_list
                    if p["address"] not in active_addrs and p.get("is_active_priority") and p.get("is_alive")
                ]

                if eligible_priority:
                    p_addrs = [p["address"] for p in eligible_priority]
                    p_prices = await self.price_feed.fetch_prices(p_addrs)

                    for p_item in eligible_priority:
                        if not self.is_running or self.is_paused:
                            break
                        if len(self.position_tracker.active_positions) >= max_positions:
                            break
                        if self.execution_engine.balance_usd < min_required:
                            break

                        p_addr = p_item["address"]
                        p_price = p_prices.get(p_addr) or p_prices.get(p_addr.lower())
                        if not p_price or p_price <= Decimal("0.0"):
                            continue

                        # Atualiza saúde e verifica se o token "continua vivo"
                        p_liq = Decimal(str(p_item.get("current_liquidity_usd", 0.0)))
                        is_alive = self.priority_pool.update_token_health(p_addr, p_price, p_liq)
                        if not is_alive:
                            continue

                        # Confirma se está em ponto de reentrada (repique de suporte ou cool-off encerrado)
                        can_reenter, _ = self.reentry_manager.can_reenter(
                            token_address=p_addr,
                            current_price=p_price,
                            current_liquidity_usd=p_liq,
                        )
                        if can_reenter:
                            logger.info(
                                "⭐ [PRECEDÊNCIA DE EXECUÇÃO] Token prioritário %s (%s) selecionado com prioridade máxima!",
                                p_item.get("symbol"),
                                p_addr[:8],
                            )
                            p_meta = self._build_waiting_token_meta(p_item, p_price)
                            await self._evaluate_and_execute_entry(p_meta)

    async def _evaluate_and_execute_entry(self, token: TokenMetadata, mode: str = "PAPER") -> None:
        """Avalia limites de slots, saldo e executa a compra do token aprovado com parâmetros dinâmicos."""
        if self._entry_lock is None:
            self._entry_lock = asyncio.Lock()

        async with self._entry_lock:
            await self._locked_evaluate_and_execute_entry(token, mode=mode)

    async def _locked_evaluate_and_execute_entry(self, token: TokenMetadata, mode: str = "PAPER") -> None:
        """Execução sincronizada com lock de entrada para prevenir posições duplicadas e validar mercado."""
        # 0. Sincroniza configurações mais recentes do disco para garantir conformidade com ajustes
        self._sync_config_from_disk_if_present()

        target_mode = mode.upper()
        engine: ExecutionEngine = self.live_engine if target_mode == "LIVE" else self.paper_engine
        tracker: PositionTracker = self.live_tracker if target_mode == "LIVE" else self.paper_tracker

        strat_mode = str(getattr(self.settings, "TRADING_STRATEGY_MODE", "DUAL")).upper()

        # Verificação atômica de posições já abertas para o token (Piramidação / Scale-In)
        active_positions = list(self.position_tracker.active_positions.values())
        active_positions = list(tracker.active_positions.values())
        existing_positions_for_token = [p for p in active_positions if p.token_address == token.address]
        max_positions_per_token = int(getattr(self.settings, "MAX_POSITIONS_PER_TOKEN", 2))
        min_scale_profit = Decimal(str(getattr(self.settings, "SCALE_IN_MIN_PROFIT_PCT", "5.0")))
        if existing_positions_for_token:
            can_scale, scale_reason = self.reentry_manager.can_scale_in(
                token_address=token.address,
                active_positions_for_token=existing_positions_for_token,
                max_positions_per_token=max_positions_per_token,
                min_profit_pct=min_scale_profit,
            )
            if not can_scale:
                logger.info(
                    "⏸️ [POSIÇÃO ATIVA EXISTENTE] Token %s (%s): %s Ignorando entrada duplicada.",
                    token.symbol or "N/A",
                    token.address,
                    scale_reason,
                )
                if token.address in self.waiting_tokens:
                    self.waiting_tokens.pop(token.address, None)
                    self._save_waiting_tokens()
                return
            logger.info(
                "📈 [PIRAMIDAÇÃO AUTORIZADA] Token %s (%s): %s",
                token.symbol or "N/A",
                token.address,
                scale_reason,
            )

        # Validação obrigatória de dinâmica de mercado se pair_data estiver presente
        if hasattr(self, "market_validator") and isinstance(token.raw_event, dict) and "pair_data" in token.raw_event:
            is_market_safe, market_reason, _ = self.market_validator.evaluate(token, strategy_mode=strat_mode)
            if not is_market_safe:
                logger.warning(
                    "⚠️ [MERCADO DESFAVORÁVEL] Token %s reprovado na validação de mercado: %s. Descartando entrada.",
                    token.symbol or token.address[:8],
                    market_reason,
                )
                if token.address in self.waiting_tokens:
                    self.waiting_tokens.pop(token.address, None)
                    self._save_waiting_tokens()
                return

        # Auditoria estrutural de velas e integridade gráfica pré-entrada (Last-Second Gate)
        if hasattr(self, "chart_auditor") and self.chart_auditor is not None:
            is_chart_safe, chart_reason, chart_details = await self.chart_auditor.audit_token_pre_entry(token)
            if not is_chart_safe:
                is_candles_insufficient = bool(chart_details.get("is_insufficient_candles")) if isinstance(chart_details, dict) else False
                if is_candles_insufficient and hasattr(self, "scanner") and hasattr(self.scanner, "incubate_token"):
                    self.scanner.incubate_token(token, reason="AGUARDANDO_3_VELAS_1H", wait_minutes=30.0)
                    logger.info(
                        "🍼 [INCUBADORA QUARENTENA] Token %s (%s) transferido para a incubadora aguardando 3 velas de 1h.",
                        token.symbol or token.address[:8],
                        token.address,
                    )
                else:
                    logger.warning(
                        "🚫 [AUDITORIA GRÁFICA / PRÉ-ENTRADA REPROVADA] Token %s (%s): %s. Entrada cancelada.",
                        token.symbol or token.address[:8],
                        token.address,
                        chart_reason,
                    )
                if token.address in self.waiting_tokens:
                    self.waiting_tokens.pop(token.address, None)
                    self._save_waiting_tokens()
                return

        # 1. Validação estrita de idade do token no mercado (Scalp: 3h-720h, Swing: 3h-6h)
        strat_mode = str(getattr(self.settings, "TRADING_STRATEGY_MODE", "DUAL")).upper()
        if strat_mode == "SWING_ONLY":
            min_age_hours = float(getattr(self.settings, "MIN_TOKEN_AGE_HOURS_SWING", 3.0))
            max_age_hours = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SWING", 6.0))
        elif strat_mode == "SCALP_ONLY":
            min_age_hours = float(getattr(self.settings, "MIN_TOKEN_AGE_HOURS_SCALP", 3.0))
            max_age_hours = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SCALP", 720.0))
        else:
            # No modo DUAL, aceita tokens dentro da janela ampla de Scalp (3.0h a 720h)
            min_age_hours = float(getattr(self.settings, "MIN_TOKEN_AGE_HOURS_SCALP", 3.0))
            max_age_hours = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SCALP", 720.0))

        token_age = self._extract_token_age_hours(token)
        if token_age is not None:
            if token_age < min_age_hours:
                if hasattr(self, "scanner") and hasattr(self.scanner, "incubate_token"):
                    self.scanner.incubate_token(token, reason="AGUARDANDO_3_VELAS_1H", wait_minutes=30.0)
                    logger.info(
                        "🍼 [INCUBADORA QUARENTENA] Token %s (%s) possui apenas %.1fh de mercado (mínimo: %.1fh). Transferido para incubadora.",
                        token.symbol or token.address[:8],
                        token.address,
                        token_age,
                        min_age_hours,
                    )
                else:
                    logger.warning(
                        "⌛ [IDADE INSUFICIENTE] Token %s (%s) possui %.2fh de mercado (mínimo: %.1fh). Entrada descartada.",
                        token.symbol or "N/A",
                        token.address,
                        token_age,
                        min_age_hours,
                    )
                if token.address in self.waiting_tokens:
                    self.waiting_tokens.pop(token.address, None)
                    self._save_waiting_tokens()
                return
            elif token_age > max_age_hours:
                logger.warning(
                    "⌛ [IDADE EXPIRADA] Token %s (%s) possui %.2fh de mercado (máximo: %.1fh). Entrada descartada.",
                    token.symbol or "N/A",
                    token.address,
                    token_age,
                    max_age_hours,
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
        max_positions = int(getattr(self.settings, "LIVE_MAX_CONCURRENT_POSITIONS" if target_mode == "LIVE" else "MAX_CONCURRENT_POSITIONS", 10))
        active_positions = list(tracker.active_positions.values())
        active_count = len(active_positions)
        active_scalp = sum(1 for p in active_positions if getattr(p, "strategy_type", "SCALP") == "SCALP")
        active_swing = sum(1 for p in active_positions if getattr(p, "strategy_type", "SCALP") == "SWING")

        existing_strategies_for_token = {
            getattr(p, "strategy_type", "SCALP")
            for p in existing_positions_for_token
        }
        if (
            "SCALP" in existing_strategies_for_token
            and "SWING" in existing_strategies_for_token
            and len(existing_positions_for_token) >= max_positions_per_token
        ):
            logger.info(
                "⏸️ [POSIÇÕES JÁ ABERTA (%s)] O token %s (%s) já atingiu o teto de posições ativas (%d).",
                target_mode,
                token.symbol or "N/A",
                token.address,
                max_positions_per_token,
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

        has_scalp = "SCALP" in existing_strategies_for_token
        has_swing = "SWING" in existing_strategies_for_token
        can_open_scalp = (
            eligible_scalp
            and (not has_scalp or len(existing_positions_for_token) < max_positions_per_token)
            and (active_scalp < max_scalp_slots)
            and (active_count < max_positions)
        )
        can_open_swing = (
            eligible_swing
            and (not has_swing or len(existing_positions_for_token) < max_positions_per_token)
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
                "⏳ [FILA DE ESPERA (%s)] Token aprovado %s (%s) aguardando slot (%s). Posições: %d/%d (Scalp: %d/%d, Swing: %d/%d)",
                target_mode,
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
        configured_buy = Decimal(str(getattr(self.settings, "LIVE_BUY_AMOUNT_USD" if target_mode == "LIVE" else "PAPER_BUY_AMOUNT_USD", "1.0")))
        available_cash = engine.balance_usd

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
                "⏳ [FILA DE ESPERA (%s)] Token aprovado %s (%s) aguardando saldo (%s). Caixa: $%.2f (Necessário: $%.2f)",
                target_mode,
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
            "📊 [GESTÃO DE CARTEIRA (%s)] Caixa: $%.2f | Alocando $%.2f por perna (Scalp: %s, Swing: %s | Modo: %s) na Posição #%d/%d (%s)",
            target_mode,
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
            await self._execute_dual_track_entry(token, buy_amount_usd, engine=engine, tracker=tracker)
        elif open_scalp:
            logger.info(
                "🎯 [ESTRATÉGIA SCALP (%s)] Token %s qualificado para Scalp (30m-720h). Abrindo perna de Scalp.",
                target_mode,
                token.symbol or token.address[:8],
            )
            pos_scalp = await engine.execute_buy(
                token,
                amount_usd=buy_amount_usd,
                strategy_type="SCALP",
            )
            if pos_scalp:
                self.telemetry.record_trade_opened()
                await tracker.register_position(pos_scalp)
                self.priority_pool.register_executed_token(token, pos_scalp.entry_price, "SCALP")
        elif open_swing:
            logger.info(
                "🏛️ [ESTRATÉGIA SWING (%s)] Token %s consolidado para Swing (2h-4h). Abrindo perna de Swing.",
                target_mode,
                token.symbol or token.address[:8],
            )
            pos_swing = await engine.execute_buy(
                token,
                amount_usd=buy_amount_usd,
                strategy_type="SWING",
            )
            if pos_swing:
                self.telemetry.record_trade_opened()
                await tracker.register_position(pos_swing)
                self.priority_pool.register_executed_token(token, pos_swing.entry_price, "SWING")

    async def _execute_dual_track_entry(
        self,
        token: TokenMetadata,
        buy_amount_usd: Decimal,
        engine: ExecutionEngine | None = None,
        tracker: PositionTracker | None = None,
    ) -> None:
        """Abre posições simultâneas SCALP e SWING no modo Dual-Track com valor integral em cada perna."""
        exec_eng = engine or self.execution_engine
        pos_trk = tracker or self.position_tracker
        mode_label = exec_eng.mode.value if hasattr(exec_eng, "mode") else "EXEC"
        logger.info(
            "⚡ [DUAL-TRACK ENTRY (%s)] Abrindo Posição SCALP ($%.2f) e Posição SWING ($%.2f) para %s",
            mode_label,
            buy_amount_usd,
            buy_amount_usd,
            token.symbol or token.address[:8],
        )
        pos_scalp = await exec_eng.execute_buy(
            token,
            amount_usd=buy_amount_usd,
            strategy_type="SCALP",
        )
        if pos_scalp:
            self.telemetry.record_trade_opened()
            await pos_trk.register_position(pos_scalp)
            self.priority_pool.register_executed_token(token, pos_scalp.entry_price, "SCALP")

        pos_swing = await exec_eng.execute_buy(
            token,
            amount_usd=buy_amount_usd,
            strategy_type="SWING",
        )
        if pos_swing:
            self.telemetry.record_trade_opened()
            await pos_trk.register_position(pos_swing)
            self.priority_pool.register_executed_token(token, pos_swing.entry_price, "SWING")


    async def _handle_position_closed(self, position: PositionState) -> None:
        """Notificado quando uma posição é 100% liquidada (Trailing Stop, Take Profit ou Stop Loss)."""
        logger.info(
            "🔄 [POSIÇÃO 100%% ENCERRADA] Registrando saída do token %s para reanálise e reentrada imediata.",
            position.token_address,
        )
        # 1. Registra no PerformanceScalingManager
        exit_price = position.trailing_stop_price if position.trailing_stop_price > Decimal("0.0") else position.entry_price
        exit_reason = "TRAILING_STOP" if position.status.value == "CLOSED" else "EMERGENCY_STOP"
        is_winner = (position.realized_pnl_usd > Decimal("0.0")) or position.break_even_triggered
        self.reentry_manager.record_exit(
            token_address=position.token_address,
            exit_price=exit_price,
            exit_reason=exit_reason,
            realized_pnl=position.realized_pnl_usd,
            is_winner=is_winner,
        )

        # 1.1 Atualiza estatísticas na Lista de Prioridades
        self.priority_pool.record_trade_result(
            token_address=position.token_address,
            realized_pnl_usd=position.realized_pnl_usd,
            exit_price=exit_price,
            exit_reason=exit_reason,
        )

        # 2. Libera token nos caches de vistos dos scanners para garantir detecção contínua
        if hasattr(self.scanner, "release_token"):
            try:
                self.scanner.release_token(position.token_address)
                logger.info("🔓 [SCANNER LIBERADO] Token %s liberado no scanner para recompras contínuas.", position.token_address)
            except Exception as exc:
                logger.debug("Erro ao chamar release_token no scanner: %s", exc)

        # 3. Autoriza reavaliação imediata no gestor de reentrada
        self.reentry_manager.authorize_immediate_reanalysis(position.token_address)

        # 4. Dispara imediatamente a reanálise do token fechado e a verificação de slots
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                # Reanálise imediata do próprio token recém-fechado para reabrir posição se estiver conforme
                target_mode_str = position.mode.value if hasattr(position.mode, "value") else str(position.mode)
                reanalysis_delay = 0.0 if self._is_test_env() else 0.5
                reanalyze_task = asyncio.create_task(
                    self._reanalyze_and_reenter_token(position.token_address, mode=target_mode_str, delay_seconds=reanalysis_delay)
                )
                self._tasks.append(reanalyze_task)
                # Preenchimento de slots com tokens da fila de espera
                fill_task = asyncio.create_task(self._try_fill_slots_from_waiting_queue())
                self._tasks.append(fill_task)
        except RuntimeError:
            pass

    async def _reanalyze_and_reenter_token(
        self,
        token_address: str,
        mode: str = "PAPER",
        delay_seconds: float = 0.5,
    ) -> None:
        """
        Reavalia imediatamente um token cuja posição acabou de ser encerrada.
        Se os critérios de segurança, liquidez, dinâmica de mercado e auditoria gráfica
        estiverem válidos ('de acordo'), abre uma nova posição imediatamente no modo correspondente.
        """
        if delay_seconds > 0.0:
            try:
                await asyncio.sleep(delay_seconds)
            except asyncio.CancelledError:
                return

        if not self.is_running:
            return

        target_mode = mode.upper()
        if target_mode == "PAPER":
            if not self.paper_enabled or self.paper_paused:
                logger.debug("⏸️ [REAVALIAÇÃO POST-FECHAMENTO] Modo PAPER inativo ou pausado para %s.", token_address)
                return
        elif target_mode == "LIVE":
            if not self.live_enabled or self.live_paused:
                logger.debug("⏸️ [REAVALIAÇÃO POST-FECHAMENTO] Modo LIVE inativo ou pausado para %s.", token_address)
                return
            if not self.live_engine.has_connected_wallet():
                logger.warning("⚠️ [REAVALIAÇÃO POST-FECHAMENTO] Nenhuma carteira real conectada para LIVE no token %s.", token_address)
                return

        logger.info(
            "🔍 [REAVALIAÇÃO IMEDIATA PÓS-FECHAMENTO (%s)] Analisando novamente o token %s...",
            target_mode,
            token_address,
        )

        # 1. Recupera metadados do token no banco de dados ou reconstrói registro padrão
        token_meta = await self.tokens_repo.get_token_metadata_by_address(token_address)
        if token_meta is None:
            token_meta = TokenMetadata(
                address=token_address,
                chain="solana",
                dex="raydium",
                initial_liquidity_usd=Decimal("5000.0"),
            )

        # 2. Busca cotação e dados frescos de liquidez/volume na DEX
        live_prices = await self.price_feed.fetch_prices([token_meta.address])
        fresh_price = live_prices.get(token_meta.address) or live_prices.get(token_meta.address.lower())
        cached_pair = getattr(self.price_feed, "get_pair_data", lambda _: None)(token_meta.address)

        raw_event = dict(token_meta.raw_event or {})
        if fresh_price is not None:
            raw_event["priceUsd"] = str(fresh_price)
        liq_val = token_meta.initial_liquidity_usd
        if cached_pair:
            raw_event["pair_data"] = cached_pair
            if "pairCreatedAt" in cached_pair:
                raw_event["pairCreatedAt"] = cached_pair["pairCreatedAt"]
            pair_liq = Decimal(str(cached_pair.get("liquidity", {}).get("usd") or 0.0))
            if pair_liq > Decimal("0.0"):
                liq_val = pair_liq

        token_meta = token_meta.model_copy(update={"raw_event": raw_event, "initial_liquidity_usd": liq_val})

        # 3. Auditoria de Segurança Completa (Hard Gates: Mint, Freeze, LP >= 98%, Tax <= 3%, Top 10 <= 15%)
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
        audit = await self.validator.audit_token(token_meta, mock_overrides=mock_overrides)
        if not audit.is_approved:
            logger.warning(
                "🛑 [REAVALIAÇÃO REPROVADA (%s)] Token %s (%s) desqualificado na auditoria de segurança pós-fechamento: %s",
                target_mode,
                token_meta.symbol or token_meta.address[:8],
                token_meta.address,
                audit.rejection_reason or "Reprovado nos critérios de segurança",
            )
            return

        # 4. Auditoria Gráfica e Estrutura de Velas (Anti-Dump / Velas Mínimas)
        if hasattr(self, "chart_auditor") and self.chart_auditor is not None:
            is_chart_safe, chart_reason, _ = await self.chart_auditor.audit_token_pre_entry(token_meta)
            if not is_chart_safe:
                logger.warning(
                    "🛑 [REAVALIAÇÃO GRÁFICA REPROVADA (%s)] Token %s (%s) desqualificado na auditoria gráfica: %s",
                    target_mode,
                    token_meta.symbol or token_meta.address[:8],
                    token_meta.address,
                    chart_reason,
                )
                return

        # 5. Se estiver 100% de acordo, abre posição novamente imediatamente
        logger.info(
            "🚀 [REAVALIAÇÃO APROVADA (%s)] Token %s (%s) aprovado em todos os critérios após fechamento. Abrindo nova posição imediatamente!",
            target_mode,
            token_meta.symbol or token_meta.address[:8],
            token_meta.address,
        )
        await self._evaluate_and_execute_entry(token_meta, mode=target_mode)

    async def _schedule_token_reentry_check(
        self,
        token_address: str,
        delay_seconds: float = 1.0,
        mode: str = "PAPER",
    ) -> None:
        """Agenda/executa reanálise e reentrada imediata para um token."""
        await self._reanalyze_and_reenter_token(token_address, mode=mode, delay_seconds=delay_seconds)

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
        if hasattr(self, "market_validator") and isinstance(cand.raw_event, dict) and "pair_data" in cand.raw_event:
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
        self,
        pos_id: int,
        pos: PositionState,
        prices: dict[str, Decimal],
        tracker: PositionTracker | None = None,
        engine: ExecutionEngine | None = None,
    ) -> None:
        """Processa tick de preço e enriquecimento de metadados para uma posição ativa."""
        pos_trk = tracker or self.position_tracker
        exec_eng = engine or self.execution_engine
        current_price = prices.get(pos.token_address) or prices.get(pos.token_address.lower())
        if current_price is not None and current_price > Decimal("0"):
            self.reentry_manager.update_post_exit_price(pos.token_address, current_price)
            await pos_trk.process_price_tick(pos_id, current_price)

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
                                    "ratchet_floor_price": float(pos.ratchet_floor_price),
                                    "active_tier": pos.ratchet_tier,
                                    "mode": getattr(pos, "mode", "PAPER"),
                                },
                            )
                        )
                    except Exception:
                        pass

        # Promoção automática de Scalp para Swing se posição estiver lucrativa
        if pos.status.value == "OPEN" and getattr(pos, "strategy_type", "SCALP") == "SCALP":
            if pos.break_even_triggered or pos.roi_pct >= Decimal("5.0"):
                try:
                    asyncio.create_task(self._evaluate_scalp_swing_promotion(pos))
                    asyncio.create_task(self._evaluate_scalp_swing_promotion(pos, tracker=pos_trk, engine=exec_eng))
                except Exception:
                    pass

        meta_getter = getattr(self.price_feed, "get_metadata", None)
        if callable(meta_getter):
            meta = meta_getter(pos.token_address)
            if meta:
                sym, nm = meta
                if sym or nm:
                    await self.tokens_repo.update_token_metadata(pos.token_address, sym, nm)

    async def _evaluate_scalp_swing_promotion(
        self,
        pos: PositionState,
        tracker: PositionTracker | None = None,
        engine: ExecutionEngine | None = None,
    ) -> None:
        """
        Avalia se uma posição SCALP lucrativa deve abrir automaticamente uma perna de SWING
        para potencializar o retorno em ativos com forte tendência de alta.
        """
        strat_mode = str(getattr(self.settings, "TRADING_STRATEGY_MODE", "DUAL")).upper()
        if strat_mode not in ("DUAL", "SWING_ONLY"):
            return

        pos_trk = tracker or self.position_tracker
        exec_eng = engine or self.execution_engine

        # Precisa estar lucrativo no SCALP (ROI >= +5% ou Break-Even ativo)
        if not pos.break_even_triggered and pos.roi_pct < Decimal("5.0"):
            return

        # Verifica se o token já possui posição de SWING ativa
        all_active = list(pos_trk.active_positions.values())
        has_swing = any(
            p.token_address == pos.token_address and getattr(p, "strategy_type", "SCALP") == "SWING"
            for p in all_active
        )
        if has_swing:
            return

        # Verifica capacidade de slots de SWING
        target_mode = getattr(pos, "mode", "PAPER")
        max_positions = int(getattr(self.settings, "LIVE_MAX_CONCURRENT_POSITIONS" if target_mode == "LIVE" else "MAX_CONCURRENT_POSITIONS", 10))
        active_count = len(all_active)
        if active_count >= max_positions:
            return

        max_swing_slots = max(1, max_positions // 2) if strat_mode == "DUAL" else max_positions
        active_swing = sum(1 for p in all_active if getattr(p, "strategy_type", "SCALP") == "SWING")
        if active_swing >= max_swing_slots:
            return

        # Dimensionamento de capital e saldo disponível
        configured_buy = Decimal(str(getattr(self.settings, "LIVE_BUY_AMOUNT_USD" if target_mode == "LIVE" else "PAPER_BUY_AMOUNT_USD", "1.0")))
        min_trade = Decimal(str(getattr(self.settings, "MIN_TRADE_AMOUNT_USD", "1.0")))
        buy_amount = configured_buy if configured_buy > Decimal("0.0") else min_trade
        if exec_eng.balance_usd < buy_amount:
            return

        # Extrai métricas atualizadas do par para verificar liquidez e maturidade de Swing
        pair_data = getattr(self.price_feed, "get_pair_data", lambda _: None)(pos.token_address)
        liq_usd = Decimal(str(pair_data.get("liquidity", {}).get("usd") or 0.0)) if pair_data else Decimal("0.0")
        if liq_usd <= Decimal("0.0"):
            liq_usd = Decimal(str(getattr(self.settings, "MIN_LIQUIDITY_SWING_USD", "20000.0")))

        age_hours: float | None = None
        if pair_data and pair_data.get("pairCreatedAt"):
            try:
                c_ms = float(pair_data["pairCreatedAt"])
                if c_ms > 0:
                    now_ts = datetime.now(UTC).timestamp() * 1000.0
                    age_hours = max(0.0, (now_ts - c_ms) / (1000.0 * 3600.0))
            except Exception:
                pass

        min_age_swing = float(getattr(self.settings, "MIN_TOKEN_AGE_HOURS_SWING", 3.0))
        max_age_swing = float(getattr(self.settings, "MAX_TOKEN_AGE_HOURS_SWING", 6.0))
        min_liq_swing = Decimal(str(getattr(self.settings, "MIN_LIQUIDITY_SWING_USD", "20000.0")))

        can_promote, reason = self.reentry_manager.can_promote_to_swing(
            scalp_pos=pos,
            token_age_hours=age_hours,
            liquidity_usd=liq_usd,
            min_age_swing=min_age_swing,
            max_age_swing=max_age_swing,
            min_liquidity_swing=min_liq_swing,
            swing_slots_available=(active_swing < max_swing_slots),
        )
        if not can_promote:
            return

        token_meta = await self.tokens_repo.get_token_metadata_by_address(pos.token_address)
        if not token_meta:
            token_meta = TokenMetadata(
                address=pos.token_address,
                chain="solana",
                dex="raydium",
                initial_liquidity_usd=liq_usd,
            )

        logger.info(
            "🚀 [PROMOÇÃO SCALP -> SWING (%s)] Token %s lucrativo (+%.2f%%). Abrindo posição complementar de SWING!",
            target_mode,
            token_meta.symbol or pos.token_address[:8],
            float(pos.roi_pct),
        )
        try:
            pos_swing = await exec_eng.execute_buy(
                token_meta,
                amount_usd=buy_amount,
                strategy_type="SWING",
            )
            if pos_swing:
                self.telemetry.record_trade_opened()
                await pos_trk.register_position(pos_swing)
                logger.info(
                    "✅ [POSIÇÃO SWING ABERTA (%s)] Posição complementar criada para %s ($%.2f USD alocados).",
                    target_mode,
                    token_meta.symbol or pos.token_address[:8],
                    float(pos_swing.allocated_capital_usd),
                )
        except Exception as exc:
            logger.error("Erro ao executar promoção automática para SWING no token %s: %s", pos.token_address, exc)

    async def _price_monitor_worker(self) -> None:
        """Monitora as cotações em tempo real das posições abertas para disparar saídas automatizadas."""
        logger.info("Monitor de Cotações Contínuas em Tempo Real (Price Poller) iniciado.")
        poll_interval = float(getattr(self.settings, "PRICE_POLL_INTERVAL_SEC", 3.0))

        while self.is_running:
            try:
                # 1. Posições de Simulação (PAPER)
                active_paper = list(self.paper_tracker.active_positions.items())
                if active_paper:
                    addresses_paper = list({pos.token_address for _, pos in active_paper})
                    prices_paper = await self.price_feed.fetch_prices(addresses_paper)
                    for pos_id, pos in active_paper:
                        if not self.is_running:
                            break
                        await self._process_active_position_tick(pos_id, pos, prices_paper, tracker=self.paper_tracker, engine=self.paper_engine)

                # 2. Posições de Operações Reais (LIVE)
                active_live = list(self.live_tracker.active_positions.items())
                if active_live:
                    addresses_live = list({pos.token_address for _, pos in active_live})
                    prices_live = await self.price_feed.fetch_prices(addresses_live)
                    for pos_id, pos in active_live:
                        if not self.is_running:
                            break
                        await self._process_active_position_tick(pos_id, pos, prices_live, tracker=self.live_tracker, engine=self.live_engine)

                # Watchdog autônomo de posições (ambos os modos)
                if self.paper_tracker.active_positions:
                    try:
                        await self.paper_tracker.check_positions_watchdog(
                            get_fresh_pair_data=getattr(self.price_feed, "get_pair_data", None)
                        )
                    except Exception as wd_err:
                        logger.debug("Erro no Watchdog de posições Paper: %s", wd_err)

                if self.live_tracker.active_positions:
                    try:
                        await self.live_tracker.check_positions_watchdog(
                            get_fresh_pair_data=getattr(self.price_feed, "get_pair_data", None)
                        )
                    except Exception as wd_err:
                        logger.debug("Erro no Watchdog de posições Live: %s", wd_err)

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

        # Cancela tarefas em execução (excluindo a tarefa atual para evitar recursão ou deadlock)
        current_task = asyncio.current_task()
        tasks_to_cancel = [t for t in self._tasks if t is not current_task and not t.done()]
        for task in tasks_to_cancel:
            task.cancel()

        tasks_to_wait = [t for t in self._tasks if t is not current_task]
        if tasks_to_wait:
            await asyncio.gather(*tasks_to_wait, return_exceptions=True)

        if self.execution_mode == "PAPER":
            logger.info("Encerrando modo PAPER: limpando dados de posições e ordens do teste simulado...")
            await self.positions_repo.clear_paper_trading_data()

        # Emite sinal de encerramento no heartbeat
        await asyncio.to_thread(self._write_heartbeat_sync, False)

        # Fecha conexões de rede e banco de dados
        await self.chart_auditor.close()
        if hasattr(self.execution_engine, "close") and callable(getattr(self.execution_engine, "close")):
            await self.execution_engine.close()
        if hasattr(self.paper_engine, "close") and callable(getattr(self.paper_engine, "close")):
            await self.paper_engine.close()
        if hasattr(self.live_engine, "close") and callable(getattr(self.live_engine, "close")):
            await self.live_engine.close()
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
        "--mode",
        choices=["paper", "live", "dual"],
        default=None,
        help="Modo de execução: paper (simulação isolada), live (operações reais) ou dual (ambos)",
    )
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

    mode_arg = (args.mode or settings.EXECUTION_MODE).lower()

    if mode_arg == "dual":
        logger.info("🚀 [MODO DUAL INICIADO] Executando instâncias independentes de SIMULAÇÃO (PAPER) e OPERAÇÕES REAIS (LIVE)...")
        paper_orch = VertexBotOrchestrator(settings, execution_mode="PAPER")
        live_orch = VertexBotOrchestrator(settings, execution_mode="LIVE")

        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()
        paper_orch.stop_event = stop_event
        live_orch.stop_event = stop_event

        def handle_exit_dual() -> None:
            logger.info("Sinal de encerramento recebido. Desligando instâncias Dual...")
            stop_event.set()
            asyncio.create_task(paper_orch.stop())
            asyncio.create_task(live_orch.stop())

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, handle_exit_dual)
            except NotImplementedError:
                pass

        await paper_orch.initialize()
        await live_orch.initialize()

        dashboard_runner: Any = None
        if args.dashboard:
            dashboard_runner, _ = await _start_background_dashboard(
                paper_orch.db,
                args.dashboard_port,
                orchestrator=paper_orch,
            )

        try:
            await asyncio.gather(
                paper_orch.start(run_mock_stream=args.simulate_mock_stream),
                live_orch.start(run_mock_stream=False),
            )
            if args.simulate_mock_stream:
                await asyncio.sleep(8.0)
                await paper_orch.stop()
                await live_orch.stop()
            else:
                try:
                    await stop_event.wait()
                except asyncio.CancelledError:
                    pass
        finally:
            if dashboard_runner:
                await dashboard_runner.cleanup()
        return

    execution_mode: Literal["PAPER", "LIVE"] = "LIVE" if mode_arg == "live" else "PAPER"
    start_enabled = True if args.simulate_mock_stream else False
    if start_enabled:
        logger.info("🚀 Iniciando Vertex-bot em modo %s (mock stream)...", execution_mode)
    else:
        logger.info("🚀 Iniciando Vertex-bot em modo STANDBY (aguardando ativação via interface)...")
    orchestrator = VertexBotOrchestrator(settings, execution_mode=execution_mode, start_enabled=start_enabled)

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

    dashboard_runner = None
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
