"""
Testes Unitários para Limpeza de Simulação (PAPER) sem afetar inteligência de tokens_catalogados.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

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
from src.engine.paper import PaperExecutionEngine


@pytest.mark.asyncio
async def test_clear_paper_trading_data_preserves_tokens(tmp_path: Any) -> None:
    db_path = str(tmp_path / "test_cleanup.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    tokens_repo = TokensRepository(db)
    pos_repo = PositionsRepository(db)
    orders_repo = OrdersRepository(db)

    # 1. Cria um token catalogado e rejeitado (ex: blacklist / não negociável)
    token = TokenMetadata(
        address="BadToken111111111111111111111111111111111111",
        symbol="BAD",
        name="Bad Rug Token",
        initial_liquidity_usd=Decimal("1000.0"),
    )
    from src.database.models import SecurityAuditResult, SecurityStatus

    await tokens_repo.save_detected_token(token)
    await tokens_repo.update_audit_result(
        SecurityAuditResult(
            token_address=token.address,
            status=SecurityStatus.REJECTED,
            security_score=10.0,
            is_mint_revoked=True,
            is_freeze_revoked=True,
            is_lp_burned_or_locked=True,
            lp_burn_percentage=100.0,
            top10_holder_percentage=55.0,
            is_honeypot=False,
            buy_tax_percentage=0.0,
            sell_tax_percentage=0.0,
            rejection_reason="Top 10 holders excessivo",
        )
    )

    # 2. Cria um token aprovado com posição e ordem PAPER
    token_ok = TokenMetadata(
        address="GoodToken22222222222222222222222222222222222",
        symbol="GOOD",
        name="Good Gem Token",
        initial_liquidity_usd=Decimal("25000.0"),
    )
    await tokens_repo.save_detected_token(token_ok)

    pos = PositionState(
        token_address=token_ok.address,
        mode=ExecutionMode.PAPER,
        entry_price=Decimal("0.001"),
        initial_token_amount=Decimal("1000.0"),
        allocated_capital_usd=Decimal("1.0"),
        trailing_drop_pct=Decimal("0.12"),
        status=PositionStatus.OPEN,
    )
    pos_id = await pos_repo.create_position(pos)

    order = OrderExecution(
        position_id=pos_id,
        order_type=OrderType.BUY,
        mode=ExecutionMode.PAPER,
        price=Decimal("0.001"),
        amount=Decimal("1000.0"),
        total_usd=Decimal("1.0"),
        executed_at=datetime.now(UTC),
    )
    await orders_repo.record_order(order)

    # Verifica que antes do cleanup temos 2 tokens, 1 posição paper e 1 ordem paper
    tokens_before = await tokens_repo.get_recent_tokens(limit=10)
    pos_before = await pos_repo.get_all_positions(limit=10)
    orders_before = await orders_repo.get_recent_orders(limit=10)
    assert len(tokens_before) == 2
    assert len(pos_before) == 1
    assert len(orders_before) == 1

    # 3. Executa a limpeza do modo de simulação
    await pos_repo.clear_paper_trading_data()

    # 4. Valida que as posições e ordens de simulação foram zeradas
    pos_after = await pos_repo.get_all_positions(limit=10)
    orders_after = await orders_repo.get_recent_orders(limit=10)
    assert len(pos_after) == 0
    assert len(orders_after) == 0

    # 5. Valida que os tokens_catalogados (inteligência de triagem) continuam 100% salvos!
    tokens_after = await tokens_repo.get_recent_tokens(limit=10)
    assert len(tokens_after) == 2
    bad_token = next(t for t in tokens_after if t["address"] == token.address)
    assert bad_token["security_status"] == "REJECTED"
    assert bad_token["rejection_reason"] == "Top 10 holders excessivo"

    # 6. Valida que o próximo ID de posição e ordem criados iniciam em #1 (sequência autoincrement resetada)
    pos_new = PositionState(
        token_address=token_ok.address,
        mode=ExecutionMode.PAPER,
        entry_price=Decimal("0.05"),
        initial_token_amount=Decimal("100.0"),
        allocated_capital_usd=Decimal("5.0"),
        status=PositionStatus.OPEN,
    )
    new_pos_id = await pos_repo.create_position(pos_new)
    assert new_pos_id == 1

    await db.close()


@pytest.mark.asyncio
async def test_paper_engine_real_price_acquisition(tmp_path: Any) -> None:
    """Valida que o PaperExecutionEngine adota o preço real do token via raw_event ou price_feed em vez de 0.001 fixo."""
    db = DatabaseManager(str(tmp_path / "test_engine_prices.db"))
    await db.initialize()
    tokens_repo = TokensRepository(db)
    pos_repo = PositionsRepository(db)
    orders_repo = OrdersRepository(db)

    # Caso 1: Preço real presente no raw_event (ex: vindo do scanner)
    engine_no_feed = PaperExecutionEngine(
        positions_repo=pos_repo,
        orders_repo=orders_repo,
        initial_balance_usd=Decimal("100.0"),
    )
    token_with_raw_price = TokenMetadata(
        address="REAL_PRICE_TOKEN_1",
        symbol="REAL1",
        name="Real Token One",
        initial_liquidity_usd=Decimal("50000.0"),
        raw_event={"priceUsd": "0.042500"},
    )
    await tokens_repo.save_detected_token(token_with_raw_price)
    pos1 = await engine_no_feed.execute_buy(token_with_raw_price, amount_usd=Decimal("10.0"))
    assert pos1 is not None
    # Preço de entrada deve ser próximo a 0.0425 (com ligeiro slippage) e NÃO o fallback de 0.001
    assert pos1.entry_price > Decimal("0.040")
    assert pos1.entry_price < Decimal("0.045")

    # Caso 2: Cotação obtida via PriceFeed em tempo real
    mock_feed = MagicMock()
    mock_feed.fetch_prices = AsyncMock(return_value={"REAL_PRICE_TOKEN_2": Decimal("0.8520")})
    engine_with_feed = PaperExecutionEngine(
        positions_repo=pos_repo,
        orders_repo=orders_repo,
        initial_balance_usd=Decimal("100.0"),
        price_feed=mock_feed,
    )
    token_live = TokenMetadata(
        address="REAL_PRICE_TOKEN_2",
        symbol="LIVE2",
        name="Live Token Two",
        initial_liquidity_usd=Decimal("100000.0"),
    )
    await tokens_repo.save_detected_token(token_live)
    pos2 = await engine_with_feed.execute_buy(token_live, amount_usd=Decimal("10.0"))
    assert pos2 is not None
    # Preço de entrada deve refletir a cotação retornada pelo PriceFeed (~0.852)
    assert pos2.entry_price > Decimal("0.85")
    assert pos2.entry_price < Decimal("0.86")

    await db.close()


@pytest.mark.asyncio
async def test_paper_wallet_zero_default_and_reset(tmp_path: Any) -> None:
    """Valida que a simulação inicia com carteira zerada ($0.00) e retorna a zero ao encerrar/reiniciar."""
    from main import VertexBotOrchestrator
    from src.config.settings import Settings

    settings = Settings(
        SQLITE_DB_PATH=str(tmp_path / "test_zero_wallet.db"),
        EXECUTION_MODE="PAPER",
    )
    # Default das configurações deve ser zero
    assert settings.PAPER_INITIAL_WALLET_USD == Decimal("0.0")

    db = DatabaseManager(settings.SQLITE_DB_PATH)
    await db.initialize()

    orch = VertexBotOrchestrator(settings, db=db, start_enabled=False)
    orch._get_config_path = lambda: tmp_path / "bot_config.json"  # type: ignore[method-assign]
    orch._get_paper_session_path = lambda: tmp_path / "paper_session.json"  # type: ignore[method-assign]
    orch._get_status_path = lambda: tmp_path / "bot_status.json"  # type: ignore[method-assign]

    await orch.initialize()

    # Verifica saldo inicial zerado
    assert orch.settings.PAPER_INITIAL_WALLET_USD == Decimal("0.0")
    assert orch.paper_engine.balance_usd == Decimal("0.0")

    # Realiza depósito de $20 simulados
    orch.deposit_wallet(Decimal("20.0"))
    assert orch.paper_engine.balance_usd == Decimal("20.0")
    assert orch.settings.PAPER_INITIAL_WALLET_USD == Decimal("20.0")

    # Encerra simulação: deve zerar o saldo
    await orch.stop_paper()
    assert orch.settings.PAPER_INITIAL_WALLET_USD == Decimal("0.0")
    assert orch.paper_engine.balance_usd == Decimal("0.0")

    # Deposita novamente e testa reiniciar sessão
    orch.deposit_wallet(Decimal("15.0"))
    assert orch.paper_engine.balance_usd == Decimal("15.0")
    await orch.restart_paper_session()
    assert orch.settings.PAPER_INITIAL_WALLET_USD == Decimal("0.0")
    assert orch.paper_engine.balance_usd == Decimal("0.0")

    await orch.stop()
    await db.close()
