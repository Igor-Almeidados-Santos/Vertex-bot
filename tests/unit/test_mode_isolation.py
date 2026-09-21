"""
Testes Unitários para Isolamento Estrito entre Modos PAPER e LIVE.
Garante que configurações, bancos de dados, queries, PnL e ações de controle
de um modo jamais contaminem ou afetem o outro.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request
from solders.keypair import Keypair

from src.dashboard.server import DashboardServer
from src.database.connection import DatabaseManager
from src.database.models import (
    ExecutionMode,
    OrderExecution,
    OrderType,
    PositionState,
    PositionStatus,
    TokenMetadata,
)
from src.database.repository import OrdersRepository, PositionsRepository, TokensRepository
from src.engine.live import LiveExecutionEngine


@pytest.mark.asyncio
async def test_clear_paper_trading_data_preserves_live_records(tmp_path: Any) -> None:
    """Verifica que a limpeza do sandbox PAPER nunca deleta ordens ou posições LIVE."""
    db_path = str(tmp_path / "test_isolation.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    tokens_repo = TokensRepository(db)
    pos_repo = PositionsRepository(db)
    orders_repo = OrdersRepository(db)

    # 1. Cria 2 tokens
    token_paper = TokenMetadata(
        address="PaperToken111111111111111111111111111111111",
        symbol="PAPER1",
        name="Paper Token",
        initial_liquidity_usd=Decimal("10000.0"),
    )
    token_live = TokenMetadata(
        address="LiveToken2222222222222222222222222222222222",
        symbol="LIVE1",
        name="Real Live Token",
        initial_liquidity_usd=Decimal("50000.0"),
    )
    await tokens_repo.save_detected_token(token_paper)
    await tokens_repo.save_detected_token(token_live)

    # 2. Cria posição e ordem no modo PAPER
    pos_paper = PositionState(
        token_address=token_paper.address,
        symbol=token_paper.symbol,
        mode=ExecutionMode.PAPER,
        entry_price=Decimal("1.0"),
        token_amount=Decimal("100.0"),
        initial_token_amount=Decimal("100.0"),
        allocated_capital_usd=Decimal("100.0"),
        stop_loss_price=Decimal("0.8"),
        take_profit_price=Decimal("2.0"),
        trailing_stop_price=Decimal("0.85"),
        highest_price_seen=Decimal("1.0"),
        ratchet_floor_price=Decimal("0.0"),
        ratchet_tier=0,
        strategy_type="SCALP",
        status=PositionStatus.OPEN,
    )
    pos_paper_id = await pos_repo.create_position(pos_paper)
    await orders_repo.record_order(
        OrderExecution(
            position_id=pos_paper_id,
            mode=ExecutionMode.PAPER,
            order_type=OrderType.BUY,
            price=Decimal("1.0"),
            amount=Decimal("100.0"),
            total_usd=Decimal("100.0"),
            reason="Compra Simulada",
        )
    )

    # 3. Cria posição e ordem no modo LIVE
    pos_live = PositionState(
        token_address=token_live.address,
        symbol=token_live.symbol,
        mode=ExecutionMode.LIVE,
        entry_price=Decimal("5.0"),
        token_amount=Decimal("20.0"),
        initial_token_amount=Decimal("20.0"),
        allocated_capital_usd=Decimal("100.0"),
        stop_loss_price=Decimal("4.0"),
        take_profit_price=Decimal("10.0"),
        trailing_stop_price=Decimal("4.5"),
        highest_price_seen=Decimal("5.0"),
        ratchet_floor_price=Decimal("0.0"),
        ratchet_tier=0,
        strategy_type="SWING",
        status=PositionStatus.OPEN,
    )
    pos_live_id = await pos_repo.create_position(pos_live)
    await orders_repo.record_order(
        OrderExecution(
            position_id=pos_live_id,
            mode=ExecutionMode.LIVE,
            order_type=OrderType.BUY,
            price=Decimal("5.0"),
            amount=Decimal("20.0"),
            total_usd=Decimal("100.0"),
            tx_hash="5xLiveTxHash999999999999999999999999999999",
            reason="Compra Real On-Chain",
        )
    )

    # 4. Executa a limpeza de PAPER
    await pos_repo.clear_paper_trading_data()

    # 5. Verifica que PAPER foi limpo
    paper_open = await pos_repo.get_open_positions(mode="PAPER")
    paper_orders = await orders_repo.get_recent_orders(limit=10, mode="PAPER")
    assert len(paper_open) == 0
    assert len(paper_orders) == 0

    # 6. Verifica que LIVE permanece 100% intacto
    live_open = await pos_repo.get_open_positions(mode="LIVE")
    live_orders = await orders_repo.get_recent_orders(limit=10, mode="LIVE")
    assert len(live_open) == 1
    assert live_open[0].id == pos_live_id
    assert live_open[0].token_address == token_live.address
    assert live_open[0].mode == ExecutionMode.LIVE

    assert len(live_orders) == 1
    assert live_orders[0]["position_id"] == pos_live_id
    assert live_orders[0]["tx_hash"] == "5xLiveTxHash999999999999999999999999999999"
    assert live_orders[0]["mode"] == "LIVE"

    await db.close()


@pytest.mark.asyncio
async def test_positions_and_pnl_query_isolation_by_mode(tmp_path: Any) -> None:
    """Garante que queries de posições e resumo de PnL não cruzam dados entre PAPER e LIVE."""
    db_path = str(tmp_path / "test_pnl_isolation.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    tokens_repo = TokensRepository(db)
    pos_repo = PositionsRepository(db)

    token_p = TokenMetadata(
        address="PaperTokenAAA",
        symbol="PAAA",
        name="Token PAAA",
        initial_liquidity_usd=Decimal("10000.0"),
    )
    token_l = TokenMetadata(
        address="LiveTokenBBB",
        symbol="LBBB",
        name="Token LBBB",
        initial_liquidity_usd=Decimal("20000.0"),
    )
    await tokens_repo.save_detected_token(token_p)
    await tokens_repo.save_detected_token(token_l)

    # Cria posição fechada com lucro em PAPER (+ $20)
    pos_paper = PositionState(
        token_address=token_p.address,
        symbol=token_p.symbol,
        mode=ExecutionMode.PAPER,
        entry_price=Decimal("1.0"),
        token_amount=Decimal("100.0"),
        initial_token_amount=Decimal("100.0"),
        allocated_capital_usd=Decimal("100.0"),
        stop_loss_price=Decimal("0.8"),
        take_profit_price=Decimal("2.0"),
        trailing_stop_price=Decimal("0.85"),
        highest_price_seen=Decimal("1.2"),
        ratchet_floor_price=Decimal("0.0"),
        ratchet_tier=0,
        strategy_type="SCALP",
        status=PositionStatus.CLOSED,
        realized_pnl_usd=Decimal("20.0"),
        exit_price=Decimal("1.2"),
        closed_at=datetime.now(UTC),
    )
    await pos_repo.create_position(pos_paper)

    # Cria posição fechada com lucro em LIVE (+ $150)
    pos_live = PositionState(
        token_address=token_l.address,
        symbol=token_l.symbol,
        mode=ExecutionMode.LIVE,
        entry_price=Decimal("10.0"),
        token_amount=Decimal("50.0"),
        initial_token_amount=Decimal("50.0"),
        allocated_capital_usd=Decimal("500.0"),
        stop_loss_price=Decimal("8.0"),
        take_profit_price=Decimal("20.0"),
        trailing_stop_price=Decimal("8.5"),
        highest_price_seen=Decimal("13.0"),
        ratchet_floor_price=Decimal("0.0"),
        ratchet_tier=0,
        strategy_type="SWING",
        status=PositionStatus.CLOSED,
        realized_pnl_usd=Decimal("150.0"),
        exit_price=Decimal("13.0"),
        closed_at=datetime.now(UTC),
    )
    await pos_repo.create_position(pos_live)

    # PnL Summary em PAPER: deve conter apenas os $20
    pnl_paper = await pos_repo.get_pnl_summary(mode="PAPER", initial_wallet_usd=10.0, current_cash_usd=30.0)
    assert pnl_paper["total_pnl_usd"] == 20.0
    assert pnl_paper["total_positions"] == 1
    assert pnl_paper["win_positions"] == 1

    # PnL Summary em LIVE: deve conter apenas os $150
    pnl_live = await pos_repo.get_pnl_summary(mode="LIVE", initial_wallet_usd=500.0, current_cash_usd=650.0)
    assert pnl_live["total_pnl_usd"] == 150.0
    assert pnl_live["total_positions"] == 1
    assert pnl_live["win_positions"] == 1

    # get_all_positions isolado
    all_paper = await pos_repo.get_all_positions(mode="PAPER")
    all_live = await pos_repo.get_all_positions(mode="LIVE")
    assert len(all_paper) == 1
    assert all_paper[0]["token_symbol"] == "PAAA"
    assert len(all_live) == 1
    assert all_live[0]["token_symbol"] == "LBBB"

    await db.close()


@pytest.mark.asyncio
async def test_live_execution_engine_hard_safety_gate(tmp_path: Any) -> None:
    """Verifica trava de segurança eliminatória que bloqueia execução real sem confirm_live_trading=True."""
    db_path = str(tmp_path / "test_engine_gate.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    pos_repo = PositionsRepository(db)
    orders_repo = OrdersRepository(db)

    # Gera chave Solana válida em base58 para o teste
    valid_key = str(Keypair())

    engine = LiveExecutionEngine(
        positions_repo=pos_repo,
        orders_repo=orders_repo,
        confirm_live_trading=False,
        solana_private_key_base58=valid_key,
    )
    assert engine.mode == ExecutionMode.LIVE

    token = TokenMetadata(
        address="TestGateToken1111111111111111111111111",
        symbol="TGT",
        initial_liquidity_usd=Decimal("10000.0"),
    )

    # Compra deve falhar imediatamente sem submeter ordens
    buy_res = await engine.execute_buy(token=token, amount_usd=Decimal("5.0"))
    assert buy_res is None

    # Venda deve falhar imediatamente
    pos = PositionState(
        id=1,
        token_address=token.address,
        mode=ExecutionMode.LIVE,
        entry_price=Decimal("1.0"),
        token_amount=Decimal("5.0"),
        initial_token_amount=Decimal("5.0"),
        allocated_capital_usd=Decimal("5.0"),
        stop_loss_price=Decimal("0.8"),
        take_profit_price=Decimal("2.0"),
        trailing_stop_price=Decimal("0.85"),
        highest_price_seen=Decimal("1.0"),
        ratchet_floor_price=Decimal("0.0"),
        ratchet_tier=0,
        strategy_type="SCALP",
        status=PositionStatus.OPEN,
    )
    sell_res = await engine.execute_sell(
        position=pos,
        amount_tokens=Decimal("5.0"),
        reason="Teste",
        execution_price=Decimal("1.0"),
    )
    assert sell_res is None

    await db.close()


@pytest.mark.asyncio
async def test_dashboard_restart_endpoint_forbids_live_mode(tmp_path: Any) -> None:
    """Verifica que o endpoint /api/bot/restart recusa terminantemente reset quando mode=live."""
    db_path = str(tmp_path / "test_dash_gate.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    server = DashboardServer(db=db, orchestrator=None)

    # Requisição simulada com mode=live
    req = make_mocked_request("POST", "/api/bot/restart?mode=live")
    resp = await server.handle_bot_restart(req)

    # Deve retornar HTTP 400 e mensagem de proibição
    assert resp.status == 400
    data = json.loads(resp.text)
    assert data["status"] == "error"
    assert "proibida para modo LIVE" in data["message"]

    await db.close()
