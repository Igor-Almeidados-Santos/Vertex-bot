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
        return self._metadata_cache.get(address) or self._metadata_cache.get(address.lower())

    def get_pair_data(self, address: str) -> dict[str, Any] | None:
        """Retorna o dicionário completo do par mais recente em cache para o token."""
        return self._pair_data_cache.get(address) or self._pair_data_cache.get(address.lower())

    def _parse_pair_item(self, pair: dict[str, Any], prices: dict[str, Decimal]) -> None:
        """Extrai cotação e metadados de um par retornado pela DexScreener, priorizando sempre a pool principal com maior liquidez."""
        base_token = pair.get("baseToken", {})
        raw_addr = str(base_token.get("address", "")).strip()
        raw_price = pair.get("priceUsd")
        sym = base_token.get("symbol")
        nm = base_token.get("name")
        pair_liq = float((pair.get("liquidity") or {}).get("usd") or 0.0)

        if raw_addr:
            existing_pair = self._pair_data_cache.get(raw_addr)
            existing_pair_addr = str(existing_pair.get("pairAddress", "")).strip() if existing_pair else ""
            new_pair_addr = str(pair.get("pairAddress", "")).strip()
            existing_liq = float((existing_pair.get("liquidity") or {}).get("usd") or 0.0) if existing_pair else -1.0

            # Atualiza se:
            # 1. Não havia par em cache
            # 2. É o MESMO par primário (atualizando cotação e liquidez recente da pool principal)
            # 3. É um par com liquidez maior do que a pool que estava em cache
            is_same_pool = bool(new_pair_addr and new_pair_addr == existing_pair_addr)
            if existing_pair is None or is_same_pool or pair_liq >= existing_liq:
                self._pair_data_cache[raw_addr] = pair
                self._pair_data_cache[raw_addr.lower()] = pair
                if sym or nm:
                    self._metadata_cache[raw_addr] = (sym, nm)
                    self._metadata_cache[raw_addr.lower()] = (sym, nm)
                if raw_price:
                    try:
                        dec_price = Decimal(str(raw_price))
                        prices[raw_addr] = dec_price
                        prices[raw_addr.lower()] = dec_price
                    except Exception as parse_err:
                        logger.debug("Preço inválido para %s: %s", raw_addr, parse_err)
        elif raw_addr and (sym or nm):
            if raw_addr not in self._metadata_cache:
                self._metadata_cache[raw_addr] = (sym, nm)
                self._metadata_cache[raw_addr.lower()] = (sym, nm)

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
                    # Ordena pares por liquidez decrescente para que a pool principal seja sempre priorizada
                    valid_pairs = [p for p in pairs if isinstance(p, dict)]
                    valid_pairs.sort(
                        key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0.0),
                        reverse=True,
                    )
                    for pair in valid_pairs:
                        self._parse_pair_item(pair, prices)
            except Exception as exc:
                logger.debug("Erro ao consultar cotações de lote: %s", exc)

        return prices


