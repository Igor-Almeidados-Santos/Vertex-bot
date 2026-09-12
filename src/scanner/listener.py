"""
Listener Assíncrono de WebSockets para Streaming On-Chain.
Monitora criação de pools e alimenta a fila de triagem com backoff de reconexão.
"""

import asyncio
import json
from decimal import Decimal
from typing import Any

try:
    from websockets.client import connect as ws_connect
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

from src.database.models import TokenMetadata
from src.scanner.parser import (
    PUMPFUN_PROGRAM_ID,
    RAYDIUM_AMM_V4_PROGRAM_ID,
    OnChainLogParser,
)
from src.utils.logger import setup_logger

logger = setup_logger("vertex.scanner.listener")


class WebSocketScanner:
    """Escuta contínua de logs e novos pares em nós RPC da Solana."""

    def __init__(
        self,
        ws_url: str,
        detection_queue: asyncio.Queue[TokenMetadata],
        reconnect_interval_seconds: float = 3.0,
    ) -> None:
        self.ws_url: str = ws_url
        self.detection_queue: asyncio.Queue[TokenMetadata] = detection_queue
        self.reconnect_interval: float = reconnect_interval_seconds
        self.is_running: bool = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Inicia a rotina assíncrona de escuta."""
        self.is_running = True
        self._task = asyncio.create_task(self._listen_loop())
        logger.info("Scanner On-Chain iniciado. Conectando a %s", self.ws_url)

    async def stop(self) -> None:
        """Encerra a rotina de escuta controladamente."""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scanner On-Chain finalizado.")

    async def _listen_loop(self) -> None:
        """Loop resiliente com reconexão contínua em caso de falha de socket."""
        if not HAS_WEBSOCKETS:
            logger.warning("Módulo 'websockets' não disponível; modo listener simulado ativado.")
            return

        while self.is_running:
            try:
                async with ws_connect(self.ws_url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info("Conexão WebSocket estabelecida com sucesso!")
                    await self._subscribe(ws)

                    async for message in ws:
                        if not self.is_running:
                            break
                        try:
                            payload: dict[str, Any] = json.loads(message)
                            token = OnChainLogParser.parse_log_notification(payload)
                            if token:
                                await self.detection_queue.put(token)
                        except Exception as parse_exc:
                            logger.debug("Erro ao processar mensagem do WebSocket: %s", parse_exc)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(
                    "Conexão WebSocket interrompida (%s). Reconectando em %.1fs...",
                    exc,
                    self.reconnect_interval,
                )
                await asyncio.sleep(self.reconnect_interval)

    async def _subscribe(self, ws: Any) -> None:
        """Envia requisições de subscrição para Raydium e Pump.fun."""
        # Subscrição para logs de Raydium AMM v4
        raydium_sub = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [RAYDIUM_AMM_V4_PROGRAM_ID]},
                {"commitment": "processed"},
            ],
        }
        await ws.send(json.dumps(raydium_sub))

        # Subscrição para logs de Pump.fun
        pump_sub = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [PUMPFUN_PROGRAM_ID]},
                {"commitment": "processed"},
            ],
        }
        await ws.send(json.dumps(pump_sub))
        logger.info("Subscrições on-chain enviadas (Raydium v4 & Pump.fun).")


def create_scanner(
    settings: Any,
    detection_queue: asyncio.Queue[TokenMetadata],
) -> Any:
    """Factory que instancia o scanner apropriado com base no provedor configurado."""
    provider = getattr(settings, "SCANNER_PROVIDER", "HYBRID")

    if provider == "HYBRID":
        from src.scanner.composite_scanner import CompositeScanner
        from src.scanner.graduation_scanner import RaydiumGraduationScanner
        from src.scanner.mature_scanner import MatureTokenScanner

        logger.info(
            "⚡ [MODO HÍBRIDO ATIVADO] Instanciando MatureTokenScanner (%.2fh a %.1fh) + RaydiumGraduationScanner em paralelo.",
            float(getattr(settings, "MIN_TOKEN_AGE_HOURS", 0.25)),
            float(getattr(settings, "MAX_TOKEN_AGE_HOURS", 720.0)),
        )
        mature_scanner = MatureTokenScanner(
            detection_queue=detection_queue,
            min_age_hours=float(getattr(settings, "MIN_TOKEN_AGE_HOURS", 0.25)),
            max_age_hours=float(getattr(settings, "MAX_TOKEN_AGE_HOURS", 720.0)),
            poll_interval_seconds=float(getattr(settings, "MATURE_POOLS_POLL_INTERVAL_SEC", 5.0)),
            dexscreener_base_url=getattr(settings, "DEXSCREENER_API_BASE_URL", "https://api.dexscreener.com"),
            geckoterminal_base_url=getattr(settings, "GECKOTERMINAL_API_BASE_URL", "https://api.geckoterminal.com"),
            enable_established_pools=bool(getattr(settings, "ENABLE_ESTABLISHED_POOLS", True)),
        )
        graduation_scanner = RaydiumGraduationScanner(
            detection_queue=detection_queue,
            ws_url=getattr(settings, "GRADUATION_WS_URL", getattr(settings, "PUMPPORTAL_WS_URL", "wss://pumpportal.fun/api/data")),
            sol_price_usd=getattr(settings, "ESTIMATED_SOL_PRICE_USD", Decimal("150.0")),
        )
        return CompositeScanner([mature_scanner, graduation_scanner])

    elif provider == "GRADUATIONS":
        from src.scanner.graduation_scanner import RaydiumGraduationScanner

        logger.info("Instanciando provedor de scanner de GRADUAÇÕES RAYDIUM (WebSocket em Tempo Real).")
        return RaydiumGraduationScanner(
            detection_queue=detection_queue,
            ws_url=getattr(settings, "GRADUATION_WS_URL", getattr(settings, "PUMPPORTAL_WS_URL", "wss://pumpportal.fun/api/data")),
            sol_price_usd=getattr(settings, "ESTIMATED_SOL_PRICE_USD", Decimal("150.0")),
        )

    elif provider == "MATURE_POOLS":
        from src.scanner.mature_scanner import MatureTokenScanner

        logger.info(
            "Instanciando provedor de scanner de TOKENS MADUROS/CONSOLIDADOS (Janela de %.2fh a %.1fh).",
            float(getattr(settings, "MIN_TOKEN_AGE_HOURS", 0.25)),
            float(getattr(settings, "MAX_TOKEN_AGE_HOURS", 720.0)),
        )
        return MatureTokenScanner(
            detection_queue=detection_queue,
            min_age_hours=float(getattr(settings, "MIN_TOKEN_AGE_HOURS", 0.25)),
            max_age_hours=float(getattr(settings, "MAX_TOKEN_AGE_HOURS", 720.0)),
            poll_interval_seconds=float(getattr(settings, "MATURE_POOLS_POLL_INTERVAL_SEC", 5.0)),
            dexscreener_base_url=getattr(settings, "DEXSCREENER_API_BASE_URL", "https://api.dexscreener.com"),
            geckoterminal_base_url=getattr(settings, "GECKOTERMINAL_API_BASE_URL", "https://api.geckoterminal.com"),
            enable_established_pools=bool(getattr(settings, "ENABLE_ESTABLISHED_POOLS", True)),
        )
    elif provider == "PUMPPORTAL":
        from src.scanner.pumpportal import PumpPortalScanner

        logger.info("Instanciando provedor de scanner STREAMING EM TEMPO REAL (PumpPortal WebSocket).")
        return PumpPortalScanner(
            detection_queue=detection_queue,
            ws_url=getattr(settings, "PUMPPORTAL_WS_URL", "wss://pumpportal.fun/api/data"),
        )
    elif provider == "INDEXED":
        from src.scanner.indexed_feed import IndexedFeedScanner

        logger.info("Instanciando provedor de scanner INDEXADO (Photon/DexScreener).")
        return IndexedFeedScanner(
            detection_queue=detection_queue,
            base_url=getattr(settings, "DEXSCREENER_API_BASE_URL", "https://api.dexscreener.com"),
            poll_interval_seconds=float(getattr(settings, "DEXSCREENER_POLL_INTERVAL_SEC", 2.0)),
        )
    else:
        logger.info("Instanciando provedor de scanner RAW_RPC (WebSocket).")
        return WebSocketScanner(
            ws_url=settings.PRIMARY_RPC_WS_URL,
            detection_queue=detection_queue,
        )

