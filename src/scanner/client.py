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

import time
import urllib.error
import urllib.request
from typing import Any, cast

from src.utils.exceptions import RPCConnectionError
from src.utils.logger import setup_logger

logger = setup_logger("vertex.scanner.rpc")


def is_valid_rpc_url(url: str | None) -> bool:
    """Valida se a URL fornecida é válida e não contém placeholders padrão."""
    if not url or not isinstance(url, str):
        return False
    clean = url.strip()
    if not (clean.startswith("http://") or clean.startswith("https://")):
        return False
    placeholders = (
        "YOUR-KEY",
        "YOUR_KEY",
        "YOUR-API-KEY",
        "YOUR_API_KEY",
        "CHANGEME",
        "<KEY>",
        "<API_KEY>",
        "INSERT_KEY",
    )
    if any(ph in clean for ph in placeholders):
        return False
    return True


PUBLIC_FALLBACK_RPCS: list[str] = [
    "https://solana-rpc.publicnode.com",
    "https://rpc.ankr.com/solana",
    "https://api.mainnet-beta.solana.com",
]


class ResilientRPCClient:
    """Cliente RPC com comutação automática entre endpoints e retries resilientes."""

    def __init__(
        self,
        primary_url: str,
        secondary_url: str | None = None,
        max_retries: int = 3,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.max_retries: int = max(max_retries, 3)
        self.timeout_seconds: float = timeout_seconds
        self._request_id: int = 1
        self._session: Any | None = None

        # Monta pool ordenado e deduplicado de endpoints
        endpoints: list[str] = []
        if is_valid_rpc_url(primary_url):
            endpoints.append(primary_url.strip())
        if is_valid_rpc_url(secondary_url):
            sec = str(secondary_url).strip()
            if sec not in endpoints:
                endpoints.append(sec)

        for fb in PUBLIC_FALLBACK_RPCS:
            if fb not in endpoints:
                endpoints.append(fb)

        self.endpoints: list[str] = endpoints
        self._endpoint_index: int = 0
        self.primary_url: str = self.endpoints[0]
        self.secondary_url: str | None = self.endpoints[1] if len(self.endpoints) > 1 else None
        self._current_url: str = self.endpoints[0]
        self._cooldowns: dict[str, float] = {}
        self._warned_about_helius: bool = False

    def _is_cooling_down(self, url: str) -> bool:
        """Verifica se um nó RPC está em período de resfriamento (cooldown)."""
        return time.monotonic() < self._cooldowns.get(url, 0.0)

    def _rotate_endpoint(self) -> str:
        """Comuta para o próximo endpoint disponível no pool circular, priorizando nós sem cooldown."""
        if len(self.endpoints) <= 1:
            return self._current_url

        now = time.monotonic()
        # Procura o próximo nó saudável (fora de cooldown)
        for i in range(1, len(self.endpoints) + 1):
            cand_idx = (self._endpoint_index + i) % len(self.endpoints)
            cand_url = self.endpoints[cand_idx]
            if now >= self._cooldowns.get(cand_url, 0.0):
                self._endpoint_index = cand_idx
                self._current_url = cand_url
                logger.info("Comutando para nó RPC saudável no pool: %s", self._current_url)
                return self._current_url

        # Se todos estiverem em cooldown, escolhe o que expira mais cedo
        best_url = min(self.endpoints, key=lambda ep: self._cooldowns.get(ep, 0.0))
        self._endpoint_index = self.endpoints.index(best_url)
        self._current_url = best_url
        logger.info("Todos os nós RPC em cooldown. Selecionando nó com menor tempo restante: %s", self._current_url)
        return self._current_url

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
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                if resp.status != 200:
                    raise RPCConnectionError(f"HTTP {resp.status} retornado pelo nó RPC {url}")
                data = resp.read()
                return cast(dict[str, Any], json.loads(data.decode("utf-8")))
        except urllib.error.HTTPError as err:
            raise RPCConnectionError(f"HTTP {err.code} retornado pelo nó RPC {url}: {err.reason}") from err

    async def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Envia requisição JSON usando aiohttp se disponível ou fallback."""
        if HAS_AIOHTTP:
            session = await self._get_session()
            headers = {"Content-Type": "application/json", "User-Agent": "Vertex-bot/1.0"}
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    text_preview = (await resp.text())[:200]
                    raise RPCConnectionError(f"HTTP {resp.status} de {url}: {text_preview}")
                data = await resp.json()
                return cast(dict[str, Any], data)
        else:
            payload_bytes = json.dumps(payload).encode("utf-8")
            return await asyncio.to_thread(self._sync_post, url, payload_bytes)

    async def call(self, method: str, params: list[Any]) -> dict[str, Any]:
        """Realiza chamada JSON-RPC com retries, cooldown de 429 e failover automático entre nós."""
        payload = {
            "jsonrpc": "2.0",
            "id": self._get_next_id(),
            "method": method,
            "params": params,
        }

        # Garante que não inicia em um nó atualmente sob cooldown
        if self._is_cooling_down(self._current_url):
            self._rotate_endpoint()

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            target_url = self._current_url
            try:
                response = await self._post_json(target_url, payload)
                if "error" in response:
                    err_obj = response["error"]
                    if isinstance(err_obj, dict):
                        code = err_obj.get("code")
                        msg = str(err_obj.get("message", "")).lower()
                        if code in (429, -32005, -32429, -32603) or any(
                            w in msg
                            for w in (
                                "rate limit",
                                "too many requests",
                                "behind",
                                "limit reached",
                                "unauthorized",
                                "forbidden",
                            )
                        ):
                            raise RPCConnectionError(f"Erro de capacidade/limite no RPC {target_url}: {err_obj}")
                    logger.warning("Aviso retornado pelo nó RPC (%s): %s", target_url, err_obj)
                return response
            except Exception as exc:
                last_error = exc
                err_str = str(exc).lower()
                is_rate_limit = (
                    "429" in err_str
                    or "rate limit" in err_str
                    or "too many requests" in err_str
                    or "-32005" in err_str
                )
                if is_rate_limit:
                    self._cooldowns[target_url] = time.monotonic() + 60.0
                    logger.warning(
                        "Falha na tentativa %d/%d com nó RPC %s: Rate-limit (429) detectado. Nó em resfriamento por 60s. (%s)",
                        attempt,
                        self.max_retries,
                        target_url,
                        exc,
                    )
                    if "api.mainnet-beta.solana.com" in target_url and not self._warned_about_helius:
                        self._warned_about_helius = True
                        logger.info(
                            "💡 DICA: O nó público padrão da Solana bloqueia provedores de nuvem/VPS. "
                            "Configure HELIUS_API_KEY no arquivo .env para conexão RPC privada com alta performance."
                        )
                else:
                    logger.warning(
                        "Falha na tentativa %d/%d com nó RPC %s: %s",
                        attempt,
                        self.max_retries,
                        target_url,
                        exc,
                    )
                self._rotate_endpoint()

                if attempt == self.max_retries:
                    raise RPCConnectionError(
                        f"Todos os {self.max_retries} retries falharam no pool RPC (último: {target_url}): {exc}"
                    ) from exc

                delay = (0.25 * (2 ** (attempt - 1))) + random.uniform(0.05, 0.15)
                await asyncio.sleep(delay)

        raise RPCConnectionError(f"Falha inesperada no fluxo RPC: {last_error}")

    async def close(self) -> None:
        """Encerra a sessão HTTP se aberta."""
        if self._session and not getattr(self._session, "closed", True):
            await self._session.close()
            self._session = None
