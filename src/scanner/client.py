"""
Cliente RPC Assíncrono com Suporte a Failover e Retry Exponencial.
"""

import asyncio
import json
import random
try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

import urllib.error
import urllib.request
from typing import Any, Optional

from src.utils.exceptions import RPCConnectionError
from src.utils.logger import setup_logger

logger = setup_logger("vertex.scanner.rpc")


class ResilientRPCClient:
    """Cliente RPC com comutação automática entre endpoints e retries resilientes."""

    def __init__(
        self,
        primary_url: str,
        secondary_url: Optional[str] = None,
        max_retries: int = 3,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.primary_url: str = primary_url
        self.secondary_url: Optional[str] = secondary_url
        self.max_retries: int = max_retries
        self.timeout_seconds: float = timeout_seconds
        self._current_url: str = primary_url
        self._request_id: int = 1
        self._session: Optional[Any] = None

    def _get_next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _get_session(self) -> Any:
        if HAS_AIOHTTP:
            if self._session is None or getattr(self._session, "closed", True):
                timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
                self._session = aiohttp.ClientSession(timeout=timeout)
            return self._session
        return None

    def _sync_post(self, url: str, payload_bytes: bytes) -> dict[str, Any]:
        """Executa POST HTTP síncrono via standard library (fallback)."""
        req = urllib.request.Request(
            url,
            data=payload_bytes,
            headers={"Content-Type": "application/json", "User-Agent": "Vertex-bot/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
            data = resp.read()
            return json.loads(data.decode("utf-8"))

    async def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Envia requisição JSON usando aiohttp se disponível ou fallback."""
        if HAS_AIOHTTP:
            session = await self._get_session()
            headers = {"Content-Type": "application/json", "User-Agent": "Vertex-bot/1.0"}
            async with session.post(url, json=payload, headers=headers) as resp:
                return await resp.json()  # type: ignore
        else:
            payload_bytes = json.dumps(payload).encode("utf-8")
            return await asyncio.to_thread(self._sync_post, url, payload_bytes)

    async def call(self, method: str, params: list[Any]) -> dict[str, Any]:
        """Realiza chamada JSON-RPC com retries e failover automático."""
        payload = {
            "jsonrpc": "2.0",
            "id": self._get_next_id(),
            "method": method,
            "params": params,
        }

        for attempt in range(1, self.max_retries + 1):
            target_url = self._current_url
            try:
                response = await self._post_json(target_url, payload)
                if "error" in response:
                    logger.warning("Erro retornado pelo nó RPC (%s): %s", target_url, response["error"])
                return response
            except Exception as exc:
                logger.warning(
                    "Falha na tentativa %d/%d com nó RPC %s: %s",
                    attempt,
                    self.max_retries,
                    target_url,
                    exc,
                )
                # Failover para o secundário se disponível
                if self.secondary_url and self._current_url == self.primary_url:
                    logger.info("Comutando para nó RPC secundário: %s", self.secondary_url)
                    self._current_url = self.secondary_url

                if attempt == self.max_retries:
                    raise RPCConnectionError(
                        f"Todos os retries falharam no RPC {target_url}: {exc}"
                    ) from exc

                # Delay com backoff exponencial e jitter
                delay = (0.3 * (2 ** (attempt - 1))) + random.uniform(0.05, 0.15)
                await asyncio.sleep(delay)

        raise RPCConnectionError("Falha inesperada no fluxo RPC.")

    async def close(self) -> None:
        """Encerra a sessão HTTP se aberta."""
        if self._session and not getattr(self._session, "closed", True):
            await self._session.close()
            self._session = None
