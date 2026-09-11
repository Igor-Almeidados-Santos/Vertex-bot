"""
Teste de Integração Ponta a Ponta:
Detecção -> Auditoria Hard Gates -> Paper Buy -> Break-Even -> Trailing Stop -> DB.
"""

import asyncio
import os
from decimal import Decimal
import pytest

from src.database.connection import DatabaseManager
from src.database.models import (
    ExecutionMode,
    PositionStatus,
    TokenMetadata,
)
from src.database.repository import OrdersRepository, PositionsRepository, TokensRepository
from src.engine.paper import PaperExecutionEngine
from src.engine.risk import RiskManager
from src.engine.tracker import PositionTracker
from src.security.validator import SecurityValidator


async def test_full_pipeline_mock():
    db_path = "data/test_integration.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = DatabaseManager(db_path)
    await db.initialize()

    tokens_repo = TokensRepository(db)
    positions_repo = PositionsRepository(db)
    orders_repo = OrdersRepository(db)

    validator = SecurityValidator(tokens_repo=tokens_repo)

    engine = PaperExecutionEngine(
        positions_repo=positions_repo,
        orders_repo=orders_repo,
        initial_balance_usd=Decimal("1000.0"),
        simulated_latency_ms=10,
        trailing_drop_pct=Decimal("0.12"),
    )

    risk_manager = RiskManager(
        break_even_gain_pct=Decimal("100.0"),
        trailing_drop_pct=Decimal("0.12"),
    )

    tracker = PositionTracker(
        engine=engine,
        positions_repo=positions_repo,
        risk_manager=risk_manager,
    )

    # 1. Ingestão de Token
    token = TokenMetadata(
        address="TEST_INTEGRATION_TOKEN_123",
        dex="raydium",
        initial_liquidity_usd=Decimal("10000.0"),
    )
    await tokens_repo.save_detected_token(token)

    # 2. Auditoria com Hard Gates Aprovados
    audit = await validator.audit_token(
        token,
        mock_overrides={
            "is_mint_revoked": True,
            "is_freeze_revoked": True,
            "lp_burn_pct": 100.0,
            "top10_pct": 10.0,
            "taxes": (0.0, 0.0, False),
        },
    )
    assert audit.is_approved is True

    # 3. Execução de Compra Paper
    buy_amount = Decimal("50.0")
    position = await engine.execute_buy(token, amount_usd=buy_amount)
    assert position is not None
    assert position.id is not None
    await tracker.register_position(position)

    pos_id = position.id

    # 4. Tick de Preço para +110% (Gatilho de Break-Even)
    price_be = position.entry_price * Decimal("2.10")
    await tracker.process_price_tick(pos_id, price_be)

    # Verifica se Break-Even foi registrado
    updated_pos = (await positions_repo.get_open_positions())[0]
    assert updated_pos.break_even_triggered is True
    assert updated_pos.status == PositionStatus.PARTIALLY_CLOSED
    expected_half = position.initial_token_amount / Decimal("2.0")
    assert abs(updated_pos.remaining_token_amount - expected_half) < Decimal("0.0001")

    # 5. Preço sobe mais para 2.5x e depois recua 15% (Gatilho de Trailing Stop)
    price_high = position.entry_price * Decimal("2.50")
    await tracker.process_price_tick(pos_id, price_high)

    price_drop = price_high * Decimal("0.85")
    await tracker.process_price_tick(pos_id, price_drop)

    # 6. Verifica encerramento da posição e auditoria contábil
    open_positions = await positions_repo.get_open_positions()
    assert len(open_positions) == 0  # Posição foi completamente encerrada!

    # Consulta ordens executadas e valida preços de execução reais
    orders = await db.fetchall(
        "SELECT * FROM ordens_executadas WHERE position_id = ? ORDER BY id ASC",
        (pos_id,),
    )
    assert len(orders) == 3  # BUY, TAKE_PROFIT_PARTIAL, TRAILING_STOP_EXIT
    order_dicts = [dict(o) for o in orders]
    assert order_dicts[0]["order_type"] == "BUY"
    assert order_dicts[1]["order_type"] == "TAKE_PROFIT_PARTIAL"
    assert abs(Decimal(str(order_dicts[1]["price"])) - price_be) < Decimal("0.0001")
    assert order_dicts[2]["order_type"] == "TRAILING_STOP_EXIT"
    assert abs(Decimal(str(order_dicts[2]["price"])) - price_drop) < Decimal("0.0001")

    # Limpeza
    await db.close()
    if os.path.exists(db_path):
        os.remove(db_path)
