"""
Scanner de Graduações Raydium em Tempo Real via WebSocket.
Captura instantaneamente tokens que concluíram a curva no Pump.fun e migraram
para a Raydium com liquidez garantida (~$60k a $85k USD) e 100% de LP queimada.
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

logger = setup_logger("vertex.scanner.graduations")


class RaydiumGraduationScanner:
    """Consome o stream de criação de novas pools na Raydium (graduações Pump.fun) em tempo real."""

    def __init__(
        self,
        detection_queue: asyncio.Queue[TokenMetadata],
        ws_url: str = "wss://pumpportal.fun/api/data",
        reconnect_interval_seconds: float = 3.0,
        sol_price_usd: Decimal = Decimal("150.0"),
        max_seen_cache: int = 10000,
        incubator_scanner: Any | None = None,
    ) -> None:
        self.detection_queue: asyncio.Queue[TokenMetadata] = detection_queue
        self.ws_url: str = ws_url
        self.reconnect_interval: float = reconnect_interval_seconds
        self.sol_price_usd: Decimal = sol_price_usd
        self.max_seen_cache: int = max_seen_cache
        self.incubator_scanner: Any | None = incubator_scanner
        self.is_running: bool = False
        self._task: asyncio.Task[None] | None = None
        self._seen_addresses: set[str] = set()

    async def start(self) -> None:
        """Inicia o loop assíncrono de streaming de graduações."""
        self.is_running = True
        self._task = asyncio.create_task(self._listen_loop())
        logger.info("Scanner de Graduações Raydium iniciado. Conectando a %s", self.ws_url)

    async def stop(self) -> None:
        """Encerra a conexão WebSocket controladamente."""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scanner de Graduações Raydium finalizado.")

    def _remember_seen_address(self, token_addr: str) -> None:
        """Registra endereço no cache de vistos para evitar duplicações."""
        self._seen_addresses.add(token_addr)
        if len(self._seen_addresses) > self.max_seen_cache:
            self._seen_addresses.pop()

    def release_token(self, token_addr: str) -> None:
        """Remove o token do cache de vistos para permitir nova captura/avaliação."""
        self._seen_addresses.discard(token_addr)
        logger.info(
            "🔄 [TOKEN LIBERADO PARA REANÁLISE] Endereço %s removido do cache de vistos do RaydiumGraduationScanner.",
            token_addr,
        )

    async def _listen_loop(self) -> None:
        """Loop de conexão contínua com subscrição ao feed de liquidez da Raydium."""
        if not HAS_WEBSOCKETS:
            logger.warning("Módulo 'websockets' indisponível; Raydium Graduation Scanner inoperante.")
            return

        while self.is_running:
            try:
                async with ws_connect(self.ws_url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info("Conexão WebSocket com Raydium Graduation Stream estabelecida!")
                    # Inscreve-se especificamente nas novas pools da Raydium
                    sub_payload = {"method": "subscribeRaydiumLiquidity"}
                    await ws.send(json.dumps(sub_payload))
                    logger.info("Subscrição 'subscribeRaydiumLiquidity' ativa. Aguardando migrações...")

                    async for message in ws:
                        if not self.is_running:
                            break
                        try:
                            payload: dict[str, Any] = json.loads(message)
                            token = self.parse_graduation_event(payload, self.sol_price_usd)
                            if token and token.address not in self._seen_addresses:
                                self._remember_seen_address(token.address)

                                logger.info(
                                    "🎓 [GRADUAÇÃO RAYDIUM DETECTADA] Token: %s (%s) | Pool: %s | Liq: $%.2f",
                                    token.symbol or "N/A",
                                    token.address,
                                    token.pool_address or "N/A",
                                    token.initial_liquidity_usd,
                                    extra={
                                        "event": "RAYDIUM_GRADUATION_DETECTED",
                                        "token_address": token.address,
                                        "pool_address": token.pool_address,
                                        "liquidity_usd": str(token.initial_liquidity_usd),
                                    },
                                )
                                if self.incubator_scanner and hasattr(self.incubator_scanner, "_register_maturing_candidate"):
                                    self.incubator_scanner._register_maturing_candidate(
                                        token.address,
                                        0.0,
                                        None,
                                        {
                                            "pool_address": token.pool_address,
                                            "name": token.name,
                                            "symbol": token.symbol,
                                            "initial_liquidity_usd": str(token.initial_liquidity_usd),
                                        },
                                    )
                                    logger.info(
                                        "🎓 [GRADUAÇÃO RAYDIUM INCUBADA] Token: %s (%s) | Adicionado à incubadora de maturação para evitar sniper dump.",
                                        token.symbol or "N/A",
                                        token.address,
                                    )
                                else:
                                    logger.info(
                                        "🎓 [GRADUAÇÃO RAYDIUM DETECTADA] Token: %s (%s) | Pool: %s | Liq: $%.2f",
                                        token.symbol or "N/A",
                                        token.address,
                                        token.pool_address or "N/A",
                                        token.initial_liquidity_usd,
                                        extra={
                                            "event": "RAYDIUM_GRADUATION_DETECTED",
                                            "token_address": token.address,
                                            "pool_address": token.pool_address,
                                            "liquidity_usd": str(token.initial_liquidity_usd),
                                        },
                                    )
                                    await self.detection_queue.put(token)
                        except Exception as parse_err:
                            logger.debug("Erro ao decodificar evento de graduação: %s", parse_err)

            except asyncio.CancelledError:
                break
            except Exception as conn_err:
                logger.warning(
                    "Conexão Raydium Graduation interrompida (%s). Reconectando em %.1fs...",
                    conn_err,
                    self.reconnect_interval,
                )
                await asyncio.sleep(self.reconnect_interval)

    @classmethod
    def parse_graduation_event(
        cls,
        data: dict[str, Any],
        sol_price_usd: Decimal = Decimal("150.0"),
    ) -> TokenMetadata | None:
        """Normaliza o payload de graduação / criação de pool na Raydium para TokenMetadata."""
        try:
            mint = data.get("mint")
            if not mint or not isinstance(mint, str):
                return None

            pool_addr = data.get("pool") or data.get("pairAddress") or data.get("marketId")
            symbol = data.get("symbol")
            name = data.get("name")

            # Cálculo de liquidez real em USD da pool Raydium recém-criada
            # Uma graduação do pump.fun deposita ~80-85 SOL de base mais a contraparte em tokens
            # Total Liquidity = 2 * (SOL amount * SOL price)
            sol_amount = data.get("solAmount")
            if sol_amount is not None:
                try:
                    liquidity_usd = Decimal(str(sol_amount)) * sol_price_usd * Decimal("2.0")
                except Exception:
                    liquidity_usd = Decimal("65000.0")
            else:
                liquidity_usd = Decimal("65000.0")

            # Preço real por token na graduação (SOL depositado * SOL price / tokens depositados)
            token_amount = data.get("tokenAmount")
            price_usd: str | None = None
            if sol_amount is not None and token_amount is not None:
                try:
                    price_val = (Decimal(str(sol_amount)) * sol_price_usd) / Decimal(str(token_amount))
                    if price_val > Decimal("0"):
                        price_usd = str(price_val)
                except Exception:
                    pass

            raw_event = dict(data)
            if price_usd:
                raw_event["priceUsd"] = price_usd

            return TokenMetadata(
                address=mint,
                chain="solana",
                dex="raydium",
                pool_address=pool_addr,
                initial_liquidity_usd=liquidity_usd,
                symbol=symbol,
                name=name,
                detection_timestamp=datetime.now(UTC),
                raw_event=raw_event,
            )
        except Exception as exc:
            logger.debug("Erro no parsing de graduação Raydium: %s", exc)
            return None
