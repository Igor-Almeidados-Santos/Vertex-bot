"""
Testes Unitários para o Cliente RPC Resiliente e Tratamento de Erros de Rede na Auditoria.
Valida filtragem de URLs dummy, rotação circular de nós e transparência nos laudos de rejeição.
"""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch
import pytest

from src.database.models import SecurityStatus, TokenMetadata
from src.database.repository import TokensRepository
from src.scanner.client import ResilientRPCClient, is_valid_rpc_url
from src.security.validator import SecurityValidator
from src.utils.exceptions import RPCConnectionError


def test_is_valid_rpc_url() -> None:
    """Valida a detecção e descarte de URLs de placeholder."""
    assert not is_valid_rpc_url(None)
    assert not is_valid_rpc_url("")
    assert not is_valid_rpc_url("notaurl")
    assert not is_valid_rpc_url("https://solana-mainnet.g.alchemy.com/v2/YOUR-KEY")
    assert not is_valid_rpc_url("https://solana-mainnet.g.alchemy.com/v2/YOUR_KEY")
    assert not is_valid_rpc_url("https://mainnet.helius-rpc.com/?api-key=CHANGEME")
    assert is_valid_rpc_url("https://api.mainnet-beta.solana.com")
    assert is_valid_rpc_url("https://solana-rpc.publicnode.com")


def test_resilient_rpc_endpoints_construction() -> None:
    """Valida que URLs inválidas são descartadas e fallbacks públicos são incluídos."""
    client = ResilientRPCClient(
        primary_url="https://api.mainnet-beta.solana.com",
        secondary_url="https://solana-mainnet.g.alchemy.com/v2/YOUR-KEY",
    )
    # A URL secundária dummy não deve entrar no pool
    assert "https://solana-mainnet.g.alchemy.com/v2/YOUR-KEY" not in client.endpoints
    # Deve conter o nó primário e os fallbacks válidos
    assert "https://api.mainnet-beta.solana.com" in client.endpoints
    assert "https://solana-rpc.publicnode.com" in client.endpoints
    assert "https://rpc.ankr.com/solana" not in client.endpoints
    assert len(client.endpoints) >= 2


@pytest.mark.asyncio
async def test_rpc_rotation_on_failure() -> None:
    """Valida que em caso de falha no primeiro nó, o cliente comuta para o próximo."""
    client = ResilientRPCClient(
        primary_url="https://failing-node.example.com",
        secondary_url="https://working-node.example.com",
        max_retries=3,
    )

    call_history: list[str] = []

    async def mock_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        call_history.append(url)
        if "failing-node" in url:
            raise RPCConnectionError("Connection refused")
        return {"result": {"value": 12345}}

    with patch.object(client, "_post_json", side_effect=mock_post):
        res = await client.call("getSlot", [])
        assert res.get("result", {}).get("value") == 12345
        # O primeiro nó falhou e o segundo teve sucesso
        assert len(call_history) == 2
        assert "failing-node.example.com" in call_history[0]
        assert "working-node.example.com" in call_history[1]


@pytest.mark.asyncio
async def test_rpc_handles_rate_limit_error_json() -> None:
    """Valida que erro de rate-limit (429 / -32005) no JSON-RPC aciona rotação imediata."""
    client = ResilientRPCClient(
        primary_url="https://rate-limited.example.com",
        secondary_url="https://ok-node.example.com",
        max_retries=3,
    )

    call_count = 0

    async def mock_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if "rate-limited" in url:
            return {"error": {"code": 429, "message": "Too Many Requests"}}
        return {"result": {"value": {"mintAuthority": None}}}

    with patch.object(client, "_post_json", side_effect=mock_post):
        res = await client.call("getAccountInfo", ["Token1111111111111111111111111111111111111111"])
        assert "result" in res
        assert call_count >= 2


@pytest.mark.asyncio
async def test_security_validator_reports_rpc_failure_transparently() -> None:
    """Valida que falha de rede RPC não é mascarada como 'Mint Authority ATIVA'."""
    tokens_repo = AsyncMock(spec=TokensRepository)
    tokens_repo.get_by_address.return_value = None
    tokens_repo.update_audit_result.return_value = None

    rpc_client = ResilientRPCClient(
        primary_url="https://all-failing.example.com",
        max_retries=3,
    )

    async def mock_failing_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RPCConnectionError(f"HTTP 429 de {url}: Too many requests")

    with patch.object(rpc_client, "_post_json", side_effect=mock_failing_post):
        validator = SecurityValidator(
            tokens_repo=tokens_repo,
            rpc_client=rpc_client,
        )

        dummy_token = TokenMetadata(
            address="TokenTestFailingRPC111111111111111111111111111",
            name="Test Token",
            symbol="TEST",
            decimals=9,
            pool_address="PoolTest111111111111111111111111111111111111",
            initial_liquidity_usd=Decimal("10000.0"),
            dex="raydium",
            created_at_ts=1700000000,
        )

        result = await validator.audit_token(dummy_token)

        assert result.status == SecurityStatus.REJECTED
        # O motivo NÃO deve ser a autoridade de mint ativa, mas explicitamente erro de RPC
        assert "Mint Authority ATIVA" not in (result.rejection_reason or "")
        assert "Erro de conexão com nós RPC Solana" in (result.rejection_reason or "")


@pytest.mark.asyncio
async def test_rpc_cooldown_on_429() -> None:
    """Valida que nó que respondeu 429 entra em cooldown e é ignorado em requisições seguintes."""
    client = ResilientRPCClient(
        primary_url="https://node-429.example.com",
        secondary_url="https://node-healthy.example.com",
        max_retries=3,
    )

    history: list[str] = []

    async def mock_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        history.append(url)
        if "node-429" in url:
            raise RPCConnectionError(f"HTTP 429 de {url}: Too many requests")
        return {"result": {"value": "ok"}}

    with patch.object(client, "_post_json", side_effect=mock_post):
        # 1ª chamada: falha no node-429, entra em cooldown, comuta para node-healthy
        res1 = await client.call("getHealth", [])
        assert res1.get("result", {}).get("value") == "ok"
        assert "node-429.example.com" in history[0]
        assert "node-healthy.example.com" in history[1]
        assert client._is_cooling_down("https://node-429.example.com")

        # 2ª chamada: deve ir DIRETO para node-healthy sem tentar node-429 novamente
        history.clear()
        res2 = await client.call("getHealth", [])
        assert res2.get("result", {}).get("value") == "ok"
        assert len(history) == 1
        assert "node-healthy.example.com" in history[0]

