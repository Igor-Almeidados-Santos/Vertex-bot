"""
Provedor de Cotações Contínuas em Tempo Real para Posições Abertas.
Consulta a API da DexScreener para obter o preço em USD (priceUsd) dos tokens ativos.
"""

import asyncio
import json
from decimal import Decimal
from typing import Any

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

import urllib.request

from src.utils.logger import setup_logger

logger = setup_logger("vertex.engine.price_feed")


class DexScreenerPriceFeed:
    """Consulta de cotações em tempo real para ativos da Solana."""

    def __init__(self, base_url: str = "https://api.dexscreener.com") -> None:
        self.base_url: str = base_url.rstrip("/")
        self._session: Any | None = None
        self._metadata_cache: dict[str, tuple[str | None, str | None]] = {}
        self._pair_data_cache: dict[str, dict[str, Any]] = {}

    async def _get_session(self) -> Any:
        if HAS_AIOHTTP:
            if self._session is None or getattr(self._session, "closed", True):
                timeout = aiohttp.ClientTimeout(total=4.0)
                self._session = aiohttp.ClientSession(timeout=timeout)
            return self._session
        return None

    async def close(self) -> None:
        """Fecha a sessão HTTP se estiver aberta."""
        if self._session and not getattr(self._session, "closed", True):
            await self._session.close()
            self._session = None

    def _sync_fetch_json(self, url: str) -> dict[str, Any]:
        """Fallback síncrono para requisição HTTP GET."""
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Vertex-bot/1.0", "Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            data = resp.read()
            res = json.loads(data.decode("utf-8"))
            if isinstance(res, dict):
                return res
            return {}

    def get_metadata(self, address: str) -> tuple[str | None, str | None] | None:
        """Retorna tupla (symbol, name) do cache para o endereço informado se disponível."""
        return self._metadata_cache.get(address)

    def get_pair_data(self, address: str) -> dict[str, Any] | None:
        """Retorna o dicionário completo do par mais recente em cache para o token."""
        return self._pair_data_cache.get(address)

    def _parse_pair_item(self, pair: dict[str, Any], prices: dict[str, Decimal]) -> None:
        """Extrai cotação e metadados de um par retornado pela DexScreener."""
        base_token = pair.get("baseToken", {})
        token_addr = str(base_token.get("address", ""))
        raw_price = pair.get("priceUsd")
        sym = base_token.get("symbol")
        nm = base_token.get("name")
        if token_addr:
            self._pair_data_cache[token_addr] = pair
        if token_addr and (sym or nm):
            self._metadata_cache[token_addr] = (sym, nm)

        if token_addr and raw_price and token_addr not in prices:
            try:
                prices[token_addr] = Decimal(str(raw_price))
            except Exception as parse_err:
                logger.debug("Preço inválido para %s: %s", token_addr, parse_err)

    async def fetch_prices(self, addresses: list[str]) -> dict[str, Decimal]:
        """Obtém cotações em USD para uma lista de endereços de tokens e extrai metadados."""
        if not addresses:
            return {}

        prices: dict[str, Decimal] = {}
        chunk_size = 30
        for i in range(0, len(addresses), chunk_size):
            chunk = addresses[i : i + chunk_size]
            url = f"{self.base_url}/latest/dex/tokens/{','.join(chunk)}"
            payload: dict[str, Any] = {}
            try:
                if HAS_AIOHTTP:
                    session = await self._get_session()
                    headers = {"User-Agent": "Vertex-bot/1.0", "Accept": "application/json"}
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            payload = await resp.json()
                else:
                    payload = await asyncio.to_thread(self._sync_fetch_json, url)

                pairs = payload.get("pairs", [])
                if isinstance(pairs, list):
                    for pair in pairs:
                        if isinstance(pair, dict):
                            self._parse_pair_item(pair, prices)
            except Exception as exc:
                logger.debug("Erro ao consultar cotações de lote: %s", exc)

        return prices


