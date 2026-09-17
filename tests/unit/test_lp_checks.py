"""
Testes Unitários para Verificação Real de LP e Resolução de Pools (Raydium & Pump.fun).
"""

import asyncio
import base64
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from solders.pubkey import Pubkey

from src.database.models import TokenMetadata
from src.scanner.indexed_feed import IndexedFeedScanner
from src.security.checks import SecurityChecks


@pytest.mark.asyncio
async def test_lp_pumpfun_bonding_curve_rejected() -> None:
    """Tokens em curva de bonding do Pump.fun sem graduação para AMM são reprovados."""
    is_safe, burn_pct = await SecurityChecks.check_lp_status(
        pool_address=None,
        token_address="8R9WxZRuXocQDij1VMvLHahzFVxH9quKiUvLT7yqpump",
        dex="pumpfun",
    )
    assert is_safe is False
    assert burn_pct == 0.0


@pytest.mark.asyncio
async def test_lp_pumpswap_amm_approved() -> None:
    """Tokens graduados no AMM PumpSwap têm liquidez bloqueada nativamente no contrato."""
    is_safe, burn_pct = await SecurityChecks.check_lp_status(
        pool_address=None,
        token_address="CeEitaSeF2dUsPmiR44Arp6GRq3TsGNNKKCyFBZ2pump",
        dex="pumpswap",
    )
    assert is_safe is True
    assert burn_pct == 100.0


@pytest.mark.asyncio
async def test_lp_raydium_burned_on_chain() -> None:
    """Pool Raydium AMM v4 com 100% de LP queimada em 11111111111111111111111111111111."""
    # Gera bytes simulados de conta Raydium AMM v4 (752 bytes)
    raw_data = bytearray(752)
    # Coloca uma pubkey de LP mint no offset 464:496
    lp_mint_pubkey = Pubkey.from_string("So11111111111111111111111111111111111111112")
    raw_data[464:496] = bytes(lp_mint_pubkey)

    data_b64 = base64.b64encode(raw_data).decode("utf-8")

    mock_rpc = AsyncMock()

    async def mock_call(method: str, params: list[Any]) -> dict[str, Any]:
        if method == "getAccountInfo":
            return {"result": {"value": {"data": [data_b64, "base64"]}}}
        elif method == "getTokenSupply":
            return {"result": {"value": {"uiAmount": 1000.0}}}
        elif method == "getTokenLargestAccounts":
            return {
                "result": {
                    "value": [
                        {
                            "address": "11111111111111111111111111111111",
                            "uiAmount": 1000.0,
                        }
                    ]
                }
            }
        return {"result": {}}

    mock_rpc.call.side_effect = mock_call

    is_safe, burn_pct = await SecurityChecks.check_lp_status(
        pool_address="PoolRaydiumValid1111111111111111111111111111",
        token_address="TokenValidRaydium1111111111111111111111111111",
        dex="raydium",
        rpc_client=mock_rpc,
    )
    assert is_safe is True
    assert burn_pct == 100.0


@pytest.mark.asyncio
async def test_lp_raydium_unburned_rejected() -> None:
    """Pool Raydium AMM v4 com LP retida por carteira não-queimada (rugpull risk)."""
    raw_data = bytearray(752)
    lp_mint_pubkey = Pubkey.from_string("So11111111111111111111111111111111111111112")
    raw_data[464:496] = bytes(lp_mint_pubkey)
    data_b64 = base64.b64encode(raw_data).decode("utf-8")

    mock_rpc = AsyncMock()

    async def mock_call(method: str, params: list[Any]) -> dict[str, Any]:
        if method == "getAccountInfo":
            # Para a conta do token holder, não é conta de queima
            return {
                "result": {
                    "value": {
                        "data": {
                            "parsed": {
                                "info": {"owner": "DevCreatorWallet111111111111111111111111111"}
                            }
                        }
                    }
                }
            }
        elif method == "getTokenSupply":
            return {"result": {"value": {"uiAmount": 1000.0}}}
        elif method == "getTokenLargestAccounts":
            return {
                "result": {
                    "value": [
                        {
                            "address": "TokenAccountOfDev11111111111111111111111111",
                            "uiAmount": 1000.0,
                        }
                    ]
                }
            }
        return {"result": {}}

    # Quando chamado para a pool, retorna os bytes da pool
    async def dispatch_call(method: str, params: list[Any]) -> dict[str, Any]:
        if method == "getAccountInfo" and params[0] == "PoolRaydiumUnburned":
            return {"result": {"value": {"data": [data_b64, "base64"]}}}
        return await mock_call(method, params)

    mock_rpc.call.side_effect = dispatch_call

    is_safe, burn_pct = await SecurityChecks.check_lp_status(
        pool_address="PoolRaydiumUnburned",
        token_address="TokenRaydiumUnburned",
        dex="raydium",
        rpc_client=mock_rpc,
    )
    assert is_safe is False
    assert burn_pct < 98.0


@pytest.mark.asyncio
async def test_enrich_token_pair_logic() -> None:
    """Verifica enriquecimento de metadados com resolução de poolAddress e dexId."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    scanner = IndexedFeedScanner(detection_queue=queue)

    token = TokenMetadata(
        address="TestAddressPump1111111111111111111111111pump",
        chain="solana",
        dex="raydium",  # Initial fallback
        initial_liquidity_usd=Decimal("7500.0"),
    )

    # Simula resposta enriquecida da API
    mock_pair_data = {
        "pairs": [
            {
                "pairAddress": "PoolPump1111111111111111111111111111111111111",
                "dexId": "pumpfun",
                "liquidity": {"usd": 12500.0},
                "baseToken": {"symbol": "TESTPUMP", "name": "Test Pump Token"},
            }
        ]
    }

    class MockResponse:
        status = 200

        async def json(self) -> dict[str, Any]:
            return mock_pair_data

        async def __aenter__(self) -> "MockResponse":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

    from unittest.mock import MagicMock
    mock_session = MagicMock()
    mock_session.get.return_value = MockResponse()
    scanner._get_session = AsyncMock(return_value=mock_session)

    enriched = await scanner.enrich_token_pair(token)
    assert enriched.dex == "pumpfun"
    assert enriched.pool_address == "PoolPump1111111111111111111111111111111111111"
    assert enriched.initial_liquidity_usd == Decimal("12500.0")
    assert enriched.symbol == "TESTPUMP"
