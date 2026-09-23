"""
Testes Unitários para o Executor Multichain EVM e Integração com LiveExecutionEngine.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from eth_account import Account

from src.database.models import (
    ExecutionMode,
    OrderExecution,
    OrderType,
    PositionState,
    PositionStatus,
    TokenMetadata,
)
from src.database.repository import OrdersRepository, PositionsRepository, TokensRepository
from src.engine.evm_executor import EVMExecutionClient, EVMSwapResult
from src.engine.live import LiveExecutionEngine


def test_evm_client_set_and_clear_private_key() -> None:
    client = EVMExecutionClient()
    assert not client.has_wallet
    assert client.address is None

    # Gera conta temporária válida
    test_acct = Account.create()
    test_key = test_acct.key.hex()

    success, addr, err = client.set_private_key(test_key)
    assert success
    assert err == ""
    assert addr == test_acct.address
    assert client.has_wallet
    assert client.address == test_acct.address

    # Limpeza de chave
    client.clear_private_key()
    assert not client.has_wallet
    assert client.address is None

    # Rejeição de endereço público inserido por engano
    success_pub, _, err_pub = client.set_private_key(test_acct.address)
    assert not success_pub
    assert "Endereço Público" in err_pub


@pytest.mark.asyncio
async def test_evm_client_native_balance() -> None:
    client = EVMExecutionClient()
    test_acct = Account.create()
    client.set_private_key(test_acct.key.hex())

    mock_w3 = MagicMock()
    mock_w3.eth.get_balance = AsyncMock(return_value=2_000_000_000_000_000_000)  # 2.0 ETH
    client._w3_instances["base"] = mock_w3

    wei, native = await client.get_native_balance("base")
    assert wei == 2_000_000_000_000_000_000
    assert native == Decimal("2.0")


@pytest.mark.asyncio
async def test_evm_client_gas_reserve_blocks_buy() -> None:
    """Valida que o bot impede compras se a reserva de gás ($5.00) for violada."""
    client = EVMExecutionClient(min_gas_reserve_usd=Decimal("5.0"))
    test_acct = Account.create()
    client.set_private_key(test_acct.key.hex())

    # Mock: carteira possui apenas 0.002 ETH (~$5.40 com ETH a $2700)
    client.get_native_balance = AsyncMock(return_value=(2_000_000_000_000_000, Decimal("0.002")))  # type: ignore[method-assign]
    client.get_native_price_usd = AsyncMock(return_value=Decimal("2700.0"))  # type: ignore[method-assign]

    # Tentativa de compra de $2.00 deixaria $3.40 (abaixo dos $5.00 da reserva de gás)
    result = await client.buy_token(
        chain="base",
        token_address="0x532f27101965dd16442E59d40670FaF5eBB142E4",
        amount_usd=Decimal("2.0"),
    )

    assert result is None  # Compra rejeitada pela trava de reserva de gás


@pytest.mark.asyncio
async def test_evm_client_buy_token_success() -> None:
    """Valida ciclo completo de compra simulando KyberSwap e Web3."""
    client = EVMExecutionClient(min_gas_reserve_usd=Decimal("5.0"))
    test_acct = Account.create()
    client.set_private_key(test_acct.key.hex())

    # Saldo abundante de 1.0 ETH (~$2700)
    client.get_native_balance = AsyncMock(return_value=(1_000_000_000_000_000_000, Decimal("1.0")))  # type: ignore[method-assign]
    client.get_native_price_usd = AsyncMock(return_value=Decimal("2700.0"))  # type: ignore[method-assign]
    client.get_token_decimals = AsyncMock(return_value=18)  # type: ignore[method-assign]

    # Mock da rota KyberSwap
    mock_route = {
        "amountIn": "1851851851851851",
        "amountOut": "500000000000000000000",  # 500 tokens
        "amountInUsd": "5.0",
        "gas": "150000",
    }
    client.get_kyber_route = AsyncMock(return_value=mock_route)  # type: ignore[method-assign]

    mock_build = {
        "routerAddress": "0x6131B5fae19EA4f9D964eAc0408E4408b66337b5",
        "data": "0x12345678",
        "gas": "150000",
    }
    client.build_kyber_swap = AsyncMock(return_value=mock_build)  # type: ignore[method-assign]

    # Mock de Web3 e envio de transação
    mock_w3 = MagicMock()
    mock_w3.eth.get_transaction_count = AsyncMock(return_value=1)
    client._w3_instances["base"] = mock_w3

    client.estimate_gas_and_fees = AsyncMock(side_effect=lambda c, tx, default_gas=350000: tx)  # type: ignore[method-assign]
    client.send_signed_transaction = AsyncMock(  # type: ignore[method-assign]
        return_value=(True, "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890", Decimal("0.005"))
    )

    result = await client.buy_token(
        chain="base",
        token_address="0x532f27101965dd16442E59d40670FaF5eBB142E4",
        amount_usd=Decimal("5.0"),
    )

    assert result is not None
    assert result.success
    assert result.amount_in == Decimal("5.0")
    assert result.amount_out == Decimal("500.0")
    assert result.effective_price == Decimal("0.01")  # $5.0 / 500
    assert result.fee_cost_usd == Decimal("0.005")
    assert "basescan.org" in result.explorer_url


@pytest.mark.asyncio
async def test_evm_client_sell_token_lifecycle() -> None:
    """Valida ciclo completo de venda EVM com verificação de allowance e swap de volta para moeda nativa."""
    client = EVMExecutionClient()
    test_acct = Account.create()
    client.set_private_key(test_acct.key.hex())

    client.get_token_decimals = AsyncMock(return_value=18)  # type: ignore[method-assign]
    client.get_token_balance = AsyncMock(  # type: ignore[method-assign]
        return_value=(500_000_000_000_000_000_000, Decimal("500.0"), 18)
    )

    # Mock de rota KyberSwap: 500 tokens -> 0.0037 ETH (~$10.00)
    mock_route = {
        "amountIn": "500000000000000000000",
        "amountOut": "3703703703703703",  # 0.0037 ETH
        "amountOutUsd": "10.0",
        "gas": "180000",
    }
    client.get_kyber_route = AsyncMock(return_value=mock_route)  # type: ignore[method-assign]

    mock_build = {
        "routerAddress": "0x6131B5fae19EA4f9D964eAc0408E4408b66337b5",
        "data": "0x99887766",
        "gas": "180000",
    }
    client.build_kyber_swap = AsyncMock(return_value=mock_build)  # type: ignore[method-assign]
    client.check_and_approve_allowance = AsyncMock(return_value=(True, "allowance_already_sufficient"))  # type: ignore[method-assign]

    mock_w3 = MagicMock()
    mock_w3.eth.get_transaction_count = AsyncMock(return_value=2)
    client._w3_instances["base"] = mock_w3

    client.estimate_gas_and_fees = AsyncMock(side_effect=lambda c, tx, default_gas=350000: tx)  # type: ignore[method-assign]
    client.send_signed_transaction = AsyncMock(  # type: ignore[method-assign]
        return_value=(True, "0x1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff", Decimal("0.004"))
    )
    client.get_native_price_usd = AsyncMock(return_value=Decimal("2700.0"))  # type: ignore[method-assign]

    result = await client.sell_token(
        chain="base",
        token_address="0x532f27101965dd16442E59d40670FaF5eBB142E4",
        amount_tokens=Decimal("500.0"),
    )

    assert result is not None
    assert result.success
    assert result.amount_in == Decimal("500.0")
    assert result.amount_out == Decimal("10.00")  # USD total recebido
    assert result.effective_price == Decimal("0.02")  # $10 / 500


@pytest.mark.asyncio
async def test_live_engine_unconfirmed_blocks_execution() -> None:
    """Trava de segurança crítica: rejeita operações reais se CONFIRM_LIVE_TRADING=False."""
    mock_pos_repo = MagicMock(spec=PositionsRepository)
    mock_ord_repo = MagicMock(spec=OrdersRepository)

    engine = LiveExecutionEngine(
        positions_repo=mock_pos_repo,
        orders_repo=mock_ord_repo,
        confirm_live_trading=False,  # TRAVA DESLIGADA
    )

    token = TokenMetadata(
        address="0x532f27101965dd16442E59d40670FaF5eBB142E4",
        chain="base",
        dex="uniswap_v3",
        initial_liquidity_usd=Decimal("50000.0"),
    )

    buy_res = await engine.execute_buy(token, amount_usd=Decimal("10.0"))
    assert buy_res is None

    pos = PositionState(
        id=1,
        token_address="0x532f27101965dd16442E59d40670FaF5eBB142E4",
        mode=ExecutionMode.LIVE,
        entry_price=Decimal("1.0"),
        initial_token_amount=Decimal("10.0"),
        allocated_capital_usd=Decimal("10.0"),
    )
    sell_res = await engine.execute_sell(pos, amount_tokens=Decimal("5.0"), reason="BREAK_EVEN", execution_price=Decimal("2.0"))
    assert sell_res is None


@pytest.mark.asyncio
async def test_live_engine_evm_buy_and_sell_integration() -> None:
    """Valida integração do LiveExecutionEngine com EVMExecutionClient para compra e venda real."""
    mock_pos_repo = MagicMock(spec=PositionsRepository)
    mock_pos_repo.create_position = AsyncMock(return_value=42)
    mock_ord_repo = MagicMock(spec=OrdersRepository)
    mock_ord_repo.record_order = AsyncMock(return_value=101)
    mock_tok_repo = MagicMock(spec=TokensRepository)

    engine = LiveExecutionEngine(
        positions_repo=mock_pos_repo,
        orders_repo=mock_ord_repo,
        tokens_repo=mock_tok_repo,
        confirm_live_trading=True,
    )

    # Conecta carteira EVM
    test_acct = Account.create()
    await engine.connect_evm_wallet(test_acct.key.hex())

    # Mock de swap de compra
    mock_swap_buy = EVMSwapResult(
        success=True,
        tx_hash="0xbuyhash123",
        amount_in=Decimal("5.0"),
        amount_out=Decimal("100.0"),
        effective_price=Decimal("0.05"),
        fee_cost_usd=Decimal("0.002"),
        explorer_url="https://basescan.org/tx/0xbuyhash123",
        chain="base",
    )
    engine.evm_client.buy_token = AsyncMock(return_value=mock_swap_buy)  # type: ignore[method-assign]

    token = TokenMetadata(
        address="0x532f27101965dd16442E59d40670FaF5eBB142E4",
        chain="base",
        dex="aerodrome",
        initial_liquidity_usd=Decimal("100000.0"),
    )

    pos = await engine.execute_buy(token, amount_usd=Decimal("5.0"), strategy_type="SCALP")
    assert pos is not None
    assert pos.id == 42
    assert pos.mode == ExecutionMode.LIVE
    assert pos.entry_price == Decimal("0.05")
    assert pos.initial_token_amount == Decimal("100.0")

    # Verifica se a ordem foi registrada
    mock_ord_repo.record_order.assert_called_once()
    saved_order: OrderExecution = mock_ord_repo.record_order.call_args[0][0]
    assert saved_order.tx_hash == "0xbuyhash123"
    assert saved_order.mode == ExecutionMode.LIVE
    assert saved_order.order_type == OrderType.BUY

    # Mock de swap de venda (Break-Even)
    mock_swap_sell = EVMSwapResult(
        success=True,
        tx_hash="0xsellhash456",
        amount_in=Decimal("50.0"),
        amount_out=Decimal("5.0"),  # $5.00 recuperados
        effective_price=Decimal("0.10"),  # +100% de lucro
        fee_cost_usd=Decimal("0.002"),
        explorer_url="https://basescan.org/tx/0xsellhash456",
        chain="base",
    )
    engine.evm_client.sell_token = AsyncMock(return_value=mock_swap_sell)  # type: ignore[method-assign]
    mock_tok_repo.get_token_metadata_by_address = AsyncMock(return_value=token)

    sell_order = await engine.execute_sell(
        position=pos,
        amount_tokens=Decimal("50.0"),
        reason="BREAK_EVEN",
        execution_price=Decimal("0.10"),
    )

    assert sell_order is not None
    assert sell_order.tx_hash == "0xsellhash456"
    assert sell_order.order_type == OrderType.TAKE_PROFIT_PARTIAL
    assert sell_order.price == Decimal("0.10")
    assert sell_order.total_usd == Decimal("5.0")
