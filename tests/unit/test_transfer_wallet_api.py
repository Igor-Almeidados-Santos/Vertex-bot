"""Testes unitários para a funcionalidade de transferência de SOL e endpoint da API."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase
from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from src.dashboard.server import create_dashboard_app
from src.database.connection import DatabaseManager
from src.database.repository import OrdersRepository, PositionsRepository, TokensRepository
from src.engine.live import LiveExecutionEngine


@pytest.mark.asyncio
async def test_transfer_sol_validation_errors() -> None:
    db = MagicMock(spec=DatabaseManager)
    pos_repo = MagicMock(spec=PositionsRepository)
    ord_repo = MagicMock(spec=OrdersRepository)

    engine = LiveExecutionEngine(
        positions_repo=pos_repo,
        orders_repo=ord_repo,
        solana_rpc_url="https://api.mainnet-beta.solana.com",
    )

    # 1. Sem carteira conectada e sem chave informada
    ok, err, url = await engine.transfer_sol("11111111111111111111111111111111", amount_sol=Decimal("0.1"))
    assert not ok
    assert "Nenhuma carteira" in err

    # 2. Usuário informou endereço público em vez de chave privada
    ok, err, url = await engine.transfer_sol(
        "11111111111111111111111111111111",
        amount_sol=Decimal("0.1"),
        private_key="11111111111111111111111111111111",
    )
    assert not ok
    assert "Endereço Público" in err

    # 3. Endereço destinatário inválido
    sender_kp = Keypair()
    ok, err, url = await engine.transfer_sol(
        "endereco_invalido_xyz",
        amount_sol=Decimal("0.1"),
        private_key=str(sender_kp),
    )
    assert not ok
    assert "inválido" in err.lower()

    # 4. Destinatário igual à origem
    ok, err, url = await engine.transfer_sol(
        str(sender_kp.pubkey()),
        amount_sol=Decimal("0.1"),
        private_key=str(sender_kp),
    )
    assert not ok
    assert "igual ao de origem" in err


@pytest.mark.asyncio
async def test_transfer_sol_mock_success() -> None:
    db = MagicMock(spec=DatabaseManager)
    pos_repo = MagicMock(spec=PositionsRepository)
    ord_repo = MagicMock(spec=OrdersRepository)

    sender_kp = Keypair()
    recipient_kp = Keypair()

    engine = LiveExecutionEngine(
        positions_repo=pos_repo,
        orders_repo=ord_repo,
        solana_rpc_url="https://api.mainnet-beta.solana.com",
        solana_private_key_base58=str(sender_kp),
    )

    mock_sol_client = AsyncMock()
    # Saldo de 1 SOL (1_000_000_000 lamports)
    mock_bal = MagicMock()
    mock_bal.value = 1_000_000_000
    mock_sol_client.get_balance.return_value = mock_bal

    mock_bh = MagicMock()
    mock_bh.value.blockhash = Hash.default()
    mock_sol_client.get_latest_blockhash.return_value = mock_bh

    mock_send = MagicMock()
    mock_send.value = "5HqZDummySignature123456789abcdef"
    mock_sol_client.send_raw_transaction.return_value = mock_send

    engine._solana_client = mock_sol_client

    # Envia 0.5 SOL
    ok, tx_hash, url = await engine.transfer_sol(
        recipient_address=str(recipient_kp.pubkey()),
        amount_sol=Decimal("0.5"),
    )
    assert ok
    assert tx_hash == "5HqZDummySignature123456789abcdef"
    assert "solscan.io/tx/" in url

    # Envia TUDO (send_all=True)
    ok2, tx_hash2, url2 = await engine.transfer_sol(
        recipient_address=str(recipient_kp.pubkey()),
        send_all=True,
    )
    assert ok2
    assert tx_hash2 == "5HqZDummySignature123456789abcdef"

