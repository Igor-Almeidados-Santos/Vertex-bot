"""
Cliente Assíncrono para o Roteador Jupiter Swap v6 (Solana DEX Aggregator).
Prepara o bot para execução com as melhores rotas de liquidez e slippage dinâmico.
"""

import asyncio

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from src.utils.logger import setup_logger

logger = setup_logger("vertex.engine.jupiter")

WSOL_MINT = "So11111111111111111111111111111111111111112"


class JupiterSwapClient:
    """Interação assíncrona com a API v6 de cotação e roteamento da Jupiter."""

    def __init__(
        self,
        quote_api_url: str = "https://quote-api.jup.ag/v6/quote",
        swap_api_url: str = "https://quote-api.jup.ag/v6/swap",
        timeout_seconds: float = 6.0,
    ) -> None:
        self.quote_api_url: str = quote_api_url
        self.swap_api_url: str = swap_api_url
        self.timeout_seconds: float = timeout_seconds
        self._session: Any | None = None

    async def _get_session(self) -> Any:
        if HAS_AIOHTTP:
            if self._session is None or getattr(self._session, "closed", True):
                timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
                self._session = aiohttp.ClientSession(timeout=timeout)
            return self._session
        return None

    def _sync_http_get(self, url: str) -> dict[str, Any]:
        """GET HTTP síncrono para a API da Jupiter (fallback)."""
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Vertex-bot/1.0",
                "Accept": "application/json",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
            data = resp.read()
            return json.loads(data.decode("utf-8"))  # type: ignore

    def _sync_http_post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST HTTP síncrono para a API da Jupiter (fallback)."""
        payload_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload_bytes,
            headers={
                "User-Agent": "Vertex-bot/1.0",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
            data = resp.read()
            return json.loads(data.decode("utf-8"))  # type: ignore

    async def _async_get(self, url: str) -> dict[str, Any]:
        if HAS_AIOHTTP:
            session = await self._get_session()
            headers = {"User-Agent": "Vertex-bot/1.0", "Accept": "application/json"}
            async with session.get(url, headers=headers) as resp:
                return await resp.json()  # type: ignore
        return await asyncio.to_thread(self._sync_http_get, url)

    async def _async_post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if HAS_AIOHTTP:
            session = await self._get_session()
            headers = {
                "User-Agent": "Vertex-bot/1.0",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            async with session.post(url, json=payload, headers=headers) as resp:
                return await resp.json()  # type: ignore
        return await asyncio.to_thread(self._sync_http_post, url, payload)

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_lamports: int,
        slippage_bps: int = 150,  # 150 bps = 1.5%
        mock_override: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Obtém a melhor rota e cotação para o par na Jupiter.
        Retorna o dicionário de quote ou None se a rota não estiver disponível.
        """
        if mock_override is not None:
            return mock_override

        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_lamports),
            "slippageBps": str(slippage_bps),
            "onlyDirectRoutes": "false",
        }
        query_string = urllib.parse.urlencode(params)
        full_url = f"{self.quote_api_url}?{query_string}"

        try:
            quote_data = await self._async_get(full_url)
            if "outAmount" in quote_data:
                logger.info(
                    "Cotação Jupiter obtida com sucesso! In: %s -> Out: %s | OutAmount: %s",
                    input_mint[:8],
                    output_mint[:8],
                    quote_data.get("outAmount"),
                )
                return quote_data
            return None
        except Exception as exc:
            logger.debug("Falha ao obter cotação na Jupiter (%s -> %s): %s", input_mint, output_mint, exc)
            return None

    async def build_swap_transaction(
        self,
        quote_response: dict[str, Any],
        user_public_key: str,
        priority_fee_lamports: int = 50000,
        mock_override: dict[str, Any] | None = None,
    ) -> str | None:
        """
        Solicita a serialização da transação de swap pronta para assinatura.
        Retorna a transação serializada em base64.
        """
        if mock_override is not None:
            return mock_override.get("swapTransaction")

        payload = {
            "quoteResponse": quote_response,
            "userPublicKey": user_public_key,
            "wrapAndUnwrapSol": True,
            "computeUnitPriceMicroLamports": priority_fee_lamports,
        }

        try:
            res = await self._async_post(self.swap_api_url, payload)
            swap_tx: str | None = res.get("swapTransaction")
            if swap_tx:
                logger.info("Transação de swap Jupiter gerada com sucesso para %s", user_public_key[:8])
                return swap_tx
            return None
        except Exception as exc:
            logger.warning("Falha ao montar transação de swap na Jupiter: %s", exc)
            return None

    async def close(self) -> None:
        """Encerra a sessão HTTP se aberta."""
        if self._session and not getattr(self._session, "closed", True):
            await self._session.close()
            self._session = None
