"""
Scanner de Feeds Indexados de Alta Velocidade (Photon-sol / DexScreener / Helius).
Consome eventos já estruturados e enriquecidos, eliminando a sobrecarga de parsing de RPC bruto.
"""

import asyncio
import json

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

import urllib.error
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from src.database.models import TokenMetadata
from src.utils.logger import setup_logger

logger = setup_logger("vertex.scanner.indexed")


class IndexedFeedScanner:
    """Consome feeds de novos pares e micro-caps já indexados no ecossistema Solana."""

    def __init__(
        self,
        detection_queue: asyncio.Queue[TokenMetadata],
        base_url: str = "https://api.dexscreener.com",
        poll_interval_seconds: float = 2.0,
        max_seen_cache: int = 10000,
    ) -> None:
        self.detection_queue: asyncio.Queue[TokenMetadata] = detection_queue
        self.base_url: str = base_url.rstrip("/")
        self.poll_interval: float = poll_interval_seconds
        self.max_seen_cache: int = max_seen_cache
        self._seen_addresses: set[str] = set()
        self.is_running: bool = False
        self._task: asyncio.Task[None] | None = None
        self._session: Any | None = None

    async def _get_session(self) -> Any:
        if HAS_AIOHTTP:
            if self._session is None or getattr(self._session, "closed", True):
                timeout = aiohttp.ClientTimeout(total=5.0)
                self._session = aiohttp.ClientSession(timeout=timeout)
            return self._session
        return None

    async def start(self) -> None:
        """Inicia o loop assíncrono de consumo do feed indexado."""
        self.is_running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "Scanner de Feeds Indexados iniciado (Provedor: %s, Intervalo: %.1fs).",
            self.base_url,
            self.poll_interval,
        )

    async def stop(self) -> None:
        """Finaliza o scanner controladamente."""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._session and not getattr(self._session, "closed", True):
            await self._session.close()
            self._session = None
        logger.info("Scanner de Feeds Indexados finalizado.")

    def release_token(self, token_addr: str) -> None:
        """Remove o token do cache de vistos para permitir nova captura."""
        self._seen_addresses.discard(token_addr)
        logger.info("🔄 [TOKEN LIBERADO PARA REANÁLISE] Endereço %s removido de IndexedFeedScanner.", token_addr)

    async def _poll_loop(self) -> None:
        """Loop contínuo de polling com proteção contra falhas e reconexão."""
        while self.is_running:
            try:
                pairs = await self._fetch_latest_pairs()
                for pair_data in pairs:
                    if not self.is_running:
                        break
                    token = self.parse_indexed_pair(pair_data)
                    if token and token.address not in self._seen_addresses:
                        self._seen_addresses.add(token.address)
                        if len(self._seen_addresses) > self.max_seen_cache:
                            self._seen_addresses.pop()

                        # Enriquece com dados detalhados da pool e DEX real
                        token = await self.enrich_token_pair(token)

                        logger.info(
                            "Novo par indexado detectado! Token: %s (%s) | DEX: %s | Pool: %s | Liquidez: $%.2f",
                            token.address,
                            token.symbol or "N/A",
                            token.dex,
                            token.pool_address or "N/A (Bonding Curve)",
                            token.initial_liquidity_usd,
                            extra={
                                "event": "INDEXED_TOKEN_DETECTED",
                                "token_address": token.address,
                                "symbol": token.symbol,
                                "dex": token.dex,
                                "pool_address": token.pool_address,
                            },
                        )
                        await self.detection_queue.put(token)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Falha transitória no polling do feed indexado: %s", exc)

            await asyncio.sleep(self.poll_interval)

    def _sync_http_get(self, url: str) -> list[dict[str, Any]]:
        """Requisição HTTP GET síncrona com headers adequados (fallback)."""
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Vertex-bot/1.0 (High-Speed Dex Indexer)",
                "Accept": "application/json",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = resp.read()
            res = json.loads(data.decode("utf-8"))
            if isinstance(res, list):
                return cast(list[dict[str, Any]], res)
            return []

    def _sync_http_get_dict(self, url: str) -> dict[str, Any]:
        """Requisição HTTP GET síncrona retornando dicionário JSON (fallback)."""
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Vertex-bot/1.0 (High-Speed Dex Indexer)",
                "Accept": "application/json",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = resp.read()
            res = json.loads(data.decode("utf-8"))
            if isinstance(res, dict):
                return cast(dict[str, Any], res)
            return {}

    async def _query_pair_api(self, token_address: str) -> dict[str, Any] | None:
        """Consulta o endpoint de pares para um endereço específico."""
        url = f"{self.base_url}/latest/dex/tokens/{token_address}"
        try:
            if HAS_AIOHTTP:
                session = await self._get_session()
                headers = {
                    "User-Agent": "Vertex-bot/1.0 (High-Speed Dex Indexer)",
                    "Accept": "application/json",
                }
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return None
                    payload = await resp.json()
            else:
                payload = await asyncio.to_thread(self._sync_http_get_dict, url)

            if isinstance(payload, dict):
                pairs = payload.get("pairs", [])
                if pairs and isinstance(pairs, list):
                    return cast(dict[str, Any], pairs[0])
        except Exception as exc:
            logger.debug("Falha na requisição de par para %s: %s", token_address, exc)
        return None

    async def enrich_token_pair(self, token: TokenMetadata) -> TokenMetadata:
        """
        Enriquece os metadados do token consultando o endpoint de pares do DexScreener.
        Obtém poolAddress real, dexId correto (raydium/pumpfun), liquidez USD e cotação.
        """
        is_pump = token.address.lower().endswith("pump")
        pair_info = await self._query_pair_api(token.address)

        if not pair_info:
            if is_pump and token.dex != "pumpfun":
                return TokenMetadata(
                    address=token.address,
                    chain=token.chain,
                    dex="pumpfun",
                    pool_address=token.pool_address,
                    initial_liquidity_usd=token.initial_liquidity_usd,
                    symbol=token.symbol,
                    name=token.name,
                    detection_timestamp=token.detection_timestamp,
                    raw_event=token.raw_event,
                )
            return token

        dex_from_api = str(pair_info.get("dexId", "pumpfun" if is_pump else token.dex)).lower()
        if is_pump and dex_from_api != "raydium":
            dex_from_api = "pumpfun"

        pool_address = pair_info.get("pairAddress") or token.pool_address
        liq_dict = pair_info.get("liquidity", {})
        liq_usd = token.initial_liquidity_usd
        if isinstance(liq_dict, dict) and "usd" in liq_dict:
            liq_usd = Decimal(str(liq_dict.get("usd") or 0.0))

        symbol = pair_info.get("baseToken", {}).get("symbol") or token.symbol
        name = pair_info.get("baseToken", {}).get("name") or token.name

        return TokenMetadata(
            address=token.address,
            chain="solana",
            dex=dex_from_api,
            pool_address=pool_address,
            initial_liquidity_usd=liq_usd,
            symbol=symbol,
            name=name,
            detection_timestamp=token.detection_timestamp,
            raw_event=pair_info,
        )

    async def _fetch_single_endpoint(self, path: str) -> list[dict[str, Any]]:
        """Consulta um endpoint específico do provedor indexado."""
        url = f"{self.base_url}{path}"
        try:
            if HAS_AIOHTTP:
                session = await self._get_session()
                headers = {
                    "User-Agent": "Vertex-bot/1.0 (High-Speed Dex Indexer)",
                    "Accept": "application/json",
                }
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        if isinstance(res, list):
                            return cast(list[dict[str, Any]], res)
                    return []
            else:
                res = await asyncio.to_thread(self._sync_http_get, url)
                if isinstance(res, list):
                    return res
                return []
        except Exception as exc:
            logger.debug("Falha transitória na leitura do feed %s: %s", path, exc)
            return []

    async def _fetch_latest_pairs(self) -> list[dict[str, Any]]:
        """Consulta múltiplos endpoints em busca de novos perfis e boosts recentes."""
        endpoints = [
            "/token-profiles/latest/v1",
            "/token-boosts/latest/v1",
            "/token-boosts/top/v1",
        ]
        tasks = [self._fetch_single_endpoint(ep) for ep in endpoints]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        merged: list[dict[str, Any]] = []
        for res in results:
            if isinstance(res, list):
                merged.extend(res)
        return merged

    @staticmethod
    def parse_indexed_pair(data: dict[str, Any]) -> TokenMetadata | None:
        """Normaliza o payload recebido do feed indexado para TokenMetadata."""
        try:
            chain_id = data.get("chainId", "").lower()
            token_address = data.get("tokenAddress")

            # Filtrar exclusivamente pares da Solana
            if chain_id != "solana" or not token_address:
                return None

            # Metadados opcionais enriquecidos
            symbol = data.get("symbol")
            name = data.get("name")
            dex = data.get("dexId", "raydium").lower()
            pool_address = data.get("poolAddress")

            # Extração defensiva de liquidez inicial em USD
            liquidity_usd = Decimal("0.0")
            if "liquidity" in data and isinstance(data["liquidity"], dict):
                raw_liq = data["liquidity"].get("usd", 0.0)
                liquidity_usd = Decimal(str(raw_liq or 0.0))
            elif "initialLiquidityUsd" in data:
                liquidity_usd = Decimal(str(data["initialLiquidityUsd"]))
            else:
                # Se o feed indexado indicar criação recente sem cotação fechada, define baseline
                liquidity_usd = Decimal("7500.0")

            return TokenMetadata(
                address=token_address,
                chain="solana",
                dex=dex,
                pool_address=pool_address,
                initial_liquidity_usd=liquidity_usd,
                symbol=symbol,
                name=name,
                detection_timestamp=datetime.now(UTC),
                raw_event=data,
            )
        except Exception as exc:
            logger.debug("Erro ao decodificar par indexado: %s", exc)
            return None
