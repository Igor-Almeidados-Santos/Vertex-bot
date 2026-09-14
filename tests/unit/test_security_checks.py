"""
Testes Unitários para Auditoria Criptográfica e Cálculo de Top 10 Holders.
Garante a exclusão de cofres de liquidez DEX e carteiras de queima.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.security.checks import SecurityChecks


@pytest.mark.asyncio
async def test_check_top10_concentration_excludes_raydium_vault() -> None:
    """Valida que o cofre da Raydium (retendo 80% do supply) é excluído da soma dos Top 10."""
    total_supply = 1_000_000_000.0
    raydium_vault_amt = 800_000_000.0

    # 10 usuários privados detendo 10M, 9M, ..., 1M = 55M tokens (5.5% do supply)
    user_accounts = [
        {"address": f"UserTokenAcc{i:02d}111111111111111111111111111111", "uiAmount": float(10 - i) * 1_000_000.0}
        for i in range(10)
    ]
    all_accounts = [
        {"address": "RaydiumPoolVaultAcc111111111111111111111111111", "uiAmount": raydium_vault_amt}
    ] + user_accounts

    mock_rpc = AsyncMock()

    async def mock_call(method: str, params: list[Any]) -> dict[str, Any]:
        if method == "getTokenSupply":
            return {"result": {"value": {"uiAmount": total_supply}}}
        elif method == "getTokenLargestAccounts":
            return {"result": {"value": all_accounts}}
        elif method == "getMultipleAccounts":
            # Primeiro item é o cofre da Raydium (owner: 5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1)
            # Os demais são carteiras privadas normais
            addrs = params[0]
            vals: list[dict[str, Any]] = []
            for addr in addrs:
                if "RaydiumPoolVaultAcc" in addr:
                    vals.append({
                        "data": {
                            "parsed": {
                                "info": {"owner": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"}
                            }
                        }
                    })
                else:
                    vals.append({
                        "data": {
                            "parsed": {
                                "info": {"owner": f"UserWalletOwner_{addr[:10]}"}
                            }
                        }
                    })
            return {"result": {"value": vals}}
        return {"result": {}}

    mock_rpc.call.side_effect = mock_call

    pct = await SecurityChecks.check_top10_concentration(
        token_address="TokenRaydiumValid111111111111111111111111111",
        rpc_client=mock_rpc,
        dex="raydium",
    )

    # A soma dos usuários é 55M / 1B = 5.5%
    # Se o vault não fosse excluído, seria 855M / 1B = 85.5%
    assert pct == 5.5


@pytest.mark.asyncio
async def test_check_top10_concentration_excludes_pumpfun_bonding_curve() -> None:
    """Valida que a bonding curve do Pump.fun é excluída do Top 10."""
    total_supply = 1_000_000_000.0
    curve_amt = 900_000_000.0  # 90% retido na curva

    user_accounts = [
        {"address": f"PumpBuyerAcc{i:02d}11111111111111111111111111111111", "uiAmount": 4_000_000.0}
        for i in range(10)
    ]
    all_accounts = [
        {"address": "PumpCurveTokenAcc11111111111111111111111111111", "uiAmount": curve_amt}
    ] + user_accounts

    mock_rpc = AsyncMock()

    async def mock_call(method: str, params: list[Any]) -> dict[str, Any]:
        if method == "getTokenSupply":
            return {"result": {"value": {"uiAmount": total_supply}}}
        elif method == "getTokenLargestAccounts":
            return {"result": {"value": all_accounts}}
        elif method == "getMultipleAccounts":
            addrs = params[0]
            vals: list[dict[str, Any]] = []
            for addr in addrs:
                if "PumpCurveTokenAcc" in addr:
                    vals.append({
                        "data": {
                            "parsed": {
                                "info": {"owner": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"}
                            }
                        }
                    })
                else:
                    vals.append({
                        "data": {
                            "parsed": {
                                "info": {"owner": f"PumpUser_{addr[:10]}"}
                            }
                        }
                    })
            return {"result": {"value": vals}}
        return {"result": {}}

    mock_rpc.call.side_effect = mock_call

    pct = await SecurityChecks.check_top10_concentration(
        token_address="TokenPumpfunValid111111111111111111111111111pump",
        rpc_client=mock_rpc,
        dex="pumpfun",
    )

    # 10 * 4M = 40M / 1B = 4.0%
    assert pct == 4.0


@pytest.mark.asyncio
async def test_check_top10_concentration_excludes_burned_tokens() -> None:
    """Valida que tokens em carteiras de queima abatem o supply circulante e não entram no Top 10."""
    total_supply = 1_000_000_000.0
    burned_amt = 500_000_000.0  # 50% queimado
    pool_amt = 400_000_000.0    # 40% na pool

    # 10 usuários com 5M cada = 50M total
    user_accounts = [
        {"address": f"HolderAcc{i:02d}1111111111111111111111111111111111", "uiAmount": 5_000_000.0}
        for i in range(10)
    ]
    all_accounts = [
        {"address": "11111111111111111111111111111111", "uiAmount": burned_amt},
        {"address": "RaydiumPoolAcc11111111111111111111111111111111", "uiAmount": pool_amt},
    ] + user_accounts

    mock_rpc = AsyncMock()

    async def mock_call(method: str, params: list[Any]) -> dict[str, Any]:
        if method == "getTokenSupply":
            return {"result": {"value": {"uiAmount": total_supply}}}
        elif method == "getTokenLargestAccounts":
            return {"result": {"value": all_accounts}}
        elif method == "getMultipleAccounts":
            addrs = params[0]
            vals: list[dict[str, Any]] = []
            for addr in addrs:
                if addr == "11111111111111111111111111111111":
                    vals.append({"data": {"parsed": {"info": {"owner": "11111111111111111111111111111111"}}}})
                elif "RaydiumPoolAcc" in addr:
                    vals.append({"data": {"parsed": {"info": {"owner": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"}}}})
                else:
                    vals.append({"data": {"parsed": {"info": {"owner": f"Wallet_{addr[:8]}"}}}})
            return {"result": {"value": vals}}
        return {"result": {}}

    mock_rpc.call.side_effect = mock_call

    pct = await SecurityChecks.check_top10_concentration(
        token_address="TokenBurnRaydium1111111111111111111111111111",
        rpc_client=mock_rpc,
        dex="raydium",
    )

    # Circulating supply = 1B - 500M = 500M
    # Top 10 private = 50M
    # Concentration = 50M / 500M = 10.0%
    assert pct == 10.0

