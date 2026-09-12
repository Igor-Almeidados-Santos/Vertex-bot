"""
Scanner de Streaming em Tempo Real via PumpPortal WebSocket.
Conecta a wss://pumpportal.fun/api/data para capturar 100% dos novos tokens no milissegundo de lançamento.
"""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

try:
    from websockets.client import connect as ws_connect
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

from src.database.models import TokenMetadata
from src.utils.logger import setup_logger

logger = setup_logger("vertex.scanner.pumpportal")


class PumpPortalScanner:
    """Consome o feed aberto de alta velocidade do PumpPortal em tempo real."""

    def __init__(
        self,
        detection_queue: asyncio.Queue[TokenMetadata],
        ws_url: str = "wss://pumpportal.fun/api/data",
        reconnect_interval_seconds: float = 3.0,
    ) -> None:
        self.detection_queue: asyncio.Queue[TokenMetadata] = detection_queue
        self.ws_url: str = ws_url
        self.reconnect_interval: float = reconnect_interval_seconds
        self.is_running: bool = False
        self._task: asyncio.Task[None] | None = None
        self._seen_mints: set[str] = set()

    async def start(self) -> None:
        """Inicia o loop assíncrono de streaming WebSocket."""
        self.is_running = True
        self._task = asyncio.create_task(self._listen_loop())
        logger.info("Scanner PumpPortal iniciado. Conectando a %s", self.ws_url)

    async def stop(self) -> None:
        """Finaliza a conexão controladamente."""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scanner PumpPortal finalizado.")

    async def _listen_loop(self) -> None:
        """Loop de conexão contínua com reconexão automática resiliente."""
        if not HAS_WEBSOCKETS:
            logger.warning("Módulo 'websockets' indisponível; PumpPortal Scanner inoperante.")
            return

        while self.is_running:
            try:
                async with ws_connect(self.ws_url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info("Conexão WebSocket com PumpPortal estabelecida com sucesso!")
                    # Inscreve-se no evento de criação de novos tokens
                    sub_payload = {"method": "subscribeNewToken"}
                    await ws.send(json.dumps(sub_payload))
                    logger.info("Subscrição 'subscribeNewToken' ativa no PumpPortal.")

                    async for message in ws:
                        if not self.is_running:
                            break
                        try:
                            payload: dict[str, Any] = json.loads(message)
                            token = self.parse_pumpportal_event(payload)
                            if token and token.address not in self._seen_mints:
                                self._seen_mints.add(token.address)
                                if len(self._seen_mints) > 10000:
                                    self._seen_mints.pop()

                                logger.info(
                                    "⚡ [NOVO PAR ON-CHAIN] Token: %s (%s) | DEX: pumpfun | Curva: %s | Liq: $%.2f",
                                    token.symbol or "N/A",
                                    token.address,
                                    token.pool_address or "N/A",
                                    token.initial_liquidity_usd,
                                    extra={
                                        "event": "PUMPPORTAL_TOKEN_DETECTED",
                                        "token_address": token.address,
                                        "symbol": token.symbol,
                                    },
                                )
                                await self.detection_queue.put(token)
                        except Exception as parse_err:
                            logger.debug("Erro ao decodificar mensagem PumpPortal: %s", parse_err)

            except asyncio.CancelledError:
                break
            except Exception as conn_err:
                logger.warning(
                    "Conexão PumpPortal interrompida (%s). Reconectando em %.1fs...",
                    conn_err,
                    self.reconnect_interval,
                )
                await asyncio.sleep(self.reconnect_interval)

    @staticmethod
    def parse_pumpportal_event(data: dict[str, Any]) -> TokenMetadata | None:
        """Normaliza o payload de criação de token da PumpPortal para TokenMetadata."""
        try:
            mint = data.get("mint")
            if not mint or not isinstance(mint, str):
                return None

            # Liquidez inicial baseada nas reservas da curva (30 SOL virtual baseline)
            v_sol = data.get("vSolInBondingCurve")
            if v_sol is not None:
                try:
                    liquidity_usd = Decimal(str(v_sol)) * Decimal("150.0")
                except Exception:
                    liquidity_usd = Decimal("5000.0")
            else:
                liquidity_usd = Decimal("5000.0")

            symbol = data.get("symbol")
            name = data.get("name")
            bonding_curve = data.get("bondingCurveKey")

            return TokenMetadata(
                address=mint,
                chain="solana",
                dex="pumpfun",
                pool_address=bonding_curve,
                initial_liquidity_usd=liquidity_usd,
                symbol=symbol,
                name=name,
                detection_timestamp=datetime.now(UTC),
                raw_event=data,
            )
        except Exception as exc:
            logger.debug("Erro no parsing de evento PumpPortal: %s", exc)
            return None

