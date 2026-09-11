"""
Testes Unitários para o Cliente de Roteamento Jupiter Swap v6.
"""

import asyncio
import pytest

from src.engine.jupiter import JupiterSwapClient, WSOL_MINT


@pytest.mark.asyncio
async def test_jupiter_get_quote_mock():
    client = JupiterSwapClient()

    mock_quote_response = {
        "inputMint": WSOL_MINT,
        "outputMint": "TargetTokenMintAddress123",
        "inAmount": "100000000",  # 0.1 SOL em lamports
        "outAmount": "50000000000",
        "priceImpactPct": "0.12",
        "routePlan": [{"swapInfo": {"ammKey": "RaydiumPoolKey", "label": "Raydium"}}],
    }

    quote = await client.get_quote(
        input_mint=WSOL_MINT,
        output_mint="TargetTokenMintAddress123",
        amount_lamports=100000000,
        slippage_bps=150,
        mock_override=mock_quote_response,
    )

    assert quote is not None
    assert quote["inputMint"] == WSOL_MINT
    assert quote["outAmount"] == "50000000000"
    assert quote["routePlan"][0]["swapInfo"]["label"] == "Raydium"


@pytest.mark.asyncio
async def test_jupiter_build_swap_transaction_mock():
    client = JupiterSwapClient()

    mock_quote = {"inAmount": "100000000", "outAmount": "50000000000"}
    mock_swap_tx_response = {
        "swapTransaction": "AQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==",
    }

    swap_tx = await client.build_swap_transaction(
        quote_response=mock_quote,
        user_public_key="UserWalletPublicKey1111111111111111111111111",
        priority_fee_lamports=50000,
        mock_override=mock_swap_tx_response,
    )

    assert swap_tx is not None
    assert swap_tx.startswith("AQAAAA")
