"""
Ponto de Entrada Unificado do Vertex-bot.
Orquestra os pipelines assíncronos de Scanner, Auditoria de Segurança,
Execução (Paper/Live) e Gerenciamento Contínuo de Risco.
"""

import argparse
import asyncio
import signal
import sys
from decimal import Decimal
from typing import Any, List

from src.config.settings import Settings, get_settings
from src.database.connection import DatabaseManager
from src.database.models import TokenMetadata
from src.database.repository import OrdersRepository, PositionsRepository, TokensRepository
from src.engine.paper import PaperExecutionEngine
from src.engine.risk import RiskManager
from src.engine.tracker import PositionTracker
from src.scanner.client import ResilientRPCClient
from src.scanner.listener import WebSocketScanner, create_scanner
from src.security.validator import SecurityValidator
from src.utils.logger import mask_sensitive, setup_logger
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

        # Cliente RPC Resiliente
        self.rpc_client: ResilientRPCClient = ResilientRPCClient(
            primary_url=settings.PRIMARY_RPC_HTTP_URL,
            secondary_url=settings.SECONDARY_RPC_HTTP_URL,
        )

        # Camada de Segurança
        self.validator: SecurityValidator = SecurityValidator(
            tokens_repo=self.tokens_repo,
            rpc_client=self.rpc_client,
            min_liquidity_usd=settings.MIN_LIQUIDITY_USD,
            max_top10_pct=float(settings.MAX_TOP10_HOLDERS_PCT),
            max_tax_pct=float(settings.MAX_BUY_TAX_PCT),
        )

        # Camada de Execução (Paper Trading por padrão)
        self.execution_engine: PaperExecutionEngine = PaperExecutionEngine(
            positions_repo=self.positions_repo,
            orders_repo=self.orders_repo,
            initial_balance_usd=settings.PAPER_INITIAL_BALANCE_SOL * Decimal("150.0"),  # Conversão de SOL para USD base
            simulated_latency_ms=settings.PAPER_SIMULATED_LATENCY_MS,
            trailing_drop_pct=settings.TRAILING_STOP_DROP_PCT / Decimal("100.0"),
        )

        # Gestão de Risco
        self.risk_manager: RiskManager = RiskManager(
            break_even_gain_pct=settings.BREAK_EVEN_GAIN_PCT,
            trailing_drop_pct=settings.TRAILING_STOP_DROP_PCT / Decimal("100.0"),
            emergency_stop_loss_pct=settings.EMERGENCY_STOP_LOSS_PCT / Decimal("100.0"),
        )
        self.position_tracker: PositionTracker = PositionTracker(
            engine=self.execution_engine,
            positions_repo=self.positions_repo,
            risk_manager=self.risk_manager,
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

        self.is_running: bool = False
        self.run_mock_stream: bool = False
        self._tasks: List[asyncio.Task[None]] = []

    async def initialize(self) -> None:
        """Inicializa banco de dados e carrega posições abertas pré-existentes."""
        logger.info("Inicializando subsistemas do Vertex-bot...")
        await self.db.initialize()
        open_positions = await self.positions_repo.get_open_positions()
        for pos in open_positions:
            await self.position_tracker.register_position(pos)
        logger.info(
            "Inicialização concluída. Modo: %s | Posições ativas restauradas: %d",
            self.settings.EXECUTION_MODE,
            len(open_positions),
        )

    async def start(self, run_mock_stream: bool = False) -> None:
        """Inicia todas as tarefas cooperativas do bot."""
        self.is_running = True
        self.run_mock_stream = run_mock_stream
        logger.info("=== VERTEX-BOT OPERACIONAL ===")

        # Tarefa 1: Processador da fila de auditoria de segurança
        self._tasks.append(asyncio.create_task(self._security_worker()))

        # Tarefa 2: Loop de telemetria periódica
        self._tasks.append(asyncio.create_task(self._telemetry_worker()))

        # Tarefa 3: Ingestão de Tokens (Real ou Mock para testes locais)
        if run_mock_stream:
            self._tasks.append(asyncio.create_task(self._mock_stream_producer()))
        else:
            await self.scanner.start()

    async def _security_worker(self) -> None:
        """Consome tokens da fila de detecção e executa a triagem de segurança."""
        buy_amount_usd = self.settings.PAPER_DEFAULT_BUY_AMOUNT_SOL * Decimal("150.0")

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

                # Auditoria com os 6 Hard Gates
                audit = await self.validator.audit_token(token, mock_overrides=mock_overrides)

                if audit.is_approved:
                    self.telemetry.record_approval()
                    # Dispara compra via Execution Engine
                    position = await self.execution_engine.execute_buy(
                        token,
                        amount_usd=buy_amount_usd,
                    )
                    if position:
                        self.telemetry.record_trade_opened()
                        await self.position_tracker.register_position(position)
                else:
                    self.telemetry.record_rejection(audit.rejection_reason or "Desconhecido")

                self.detection_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Erro inesperado no worker de segurança: %s", exc, exc_info=True)

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

        # Para o scanner
        await self.scanner.stop()

        # Cancela tarefas em execução
        for task in self._tasks:
            if not task.done():
                task.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)

        # Fecha conexões de rede e banco de dados
        await self.rpc_client.close()
        await self.db.close()

        # Exibe relatório final consolidado
        self.telemetry.print_summary()
        logger.info("Vertex-bot finalizado com segurança.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Vertex-bot Trading & Scanning Engine")
    parser.add_argument(
        "--simulate-mock-stream",
        action="store_true",
        help="Executa com streaming mockado local para testes e demonstração do ciclo completo",
    )
    parser.add_argument(
        "--provider",
        choices=["indexed", "rpc"],
        help="Seleciona o provedor do scanner: 'indexed' (DexScreener/Photon) ou 'rpc' (WebSocket bruto)",
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.provider:
        settings.SCANNER_PROVIDER = "INDEXED" if args.provider == "indexed" else "RAW_RPC"

    orchestrator = VertexBotOrchestrator(settings)

    # Configuração de captura de sinais SIGINT e SIGTERM
    loop = asyncio.get_running_loop()

    def handle_exit() -> None:
        logger.info("Sinal de encerramento recebido. Desligando...")
        asyncio.create_task(orchestrator.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_exit)
        except NotImplementedError:
            pass  # Windows fallback

    await orchestrator.initialize()
    await orchestrator.start(run_mock_stream=args.simulate_mock_stream)

    if args.simulate_mock_stream:
        # No modo mock de demonstração, aguarda 8 segundos para a simulação completar e encerra
        await asyncio.sleep(8.0)
        await orchestrator.stop()
    else:
        # No modo normal, roda indefinidamente até sinal de interrupção
        try:
            while orchestrator.is_running:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
