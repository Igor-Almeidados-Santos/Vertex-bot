"""
Teste de Integração Ponta a Ponta:
Detecção -> Auditoria Hard Gates -> Paper Buy -> Break-Even -> Trailing Stop -> DB.
"""

from decimal import Decimal
from pathlib import Path

from src.database.connection import DatabaseManager
from src.database.models import (
    PositionStatus,
    TokenMetadata,
)
from src.database.repository import OrdersRepository, PositionsRepository, TokensRepository
from src.engine.paper import PaperExecutionEngine
from src.engine.risk import RiskManager
from src.engine.tracker import PositionTracker
from src.security.validator import SecurityValidator


async def test_full_pipeline_mock(tmp_path: Path):
    db_path = str(tmp_path / "test_integration.db")

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

    # 4. Tick de Preço para +110% (Gatilho de Alvo Scalp: Venda de 100% no alvo)
    price_target = position.entry_price * Decimal("2.10")
    await tracker.process_price_tick(pos_id, price_target)

    # 5. Verifica encerramento da posição Scalp e auditoria contábil
    open_positions = await positions_repo.get_open_positions()
    assert len(open_positions) == 0  # Posição Scalp foi 100% encerrada no alvo!

    # Consulta ordens executadas da posição Scalp
    orders = await db.fetchall(
        "SELECT * FROM ordens_executadas WHERE position_id = ? ORDER BY id ASC",
        (pos_id,),
    )
    assert len(orders) == 2  # BUY e saída a mercado por SCALP_TARGET_REACHED
    order_dicts = [dict(o) for o in orders]
    assert order_dicts[0]["order_type"] == "BUY"
    assert order_dicts[1]["order_type"] == "TRAILING_STOP_EXIT"
    assert abs(Decimal(str(order_dicts[1]["price"])) - price_target) < Decimal("0.0001")

    # 6. Valida também uma posição SWING no pipeline integrado
    pos_swing = await engine.execute_buy(token, amount_usd=buy_amount, strategy_type="SWING")
    assert pos_swing is not None
    assert pos_swing.id is not None
    await tracker.register_position(pos_swing)
    swing_id = pos_swing.id

    # SWING atinge 2.0x (Degrau 1) -> NÃO vende, apenas eleva o piso para o BE
    await tracker.process_price_tick(swing_id, pos_swing.entry_price * Decimal("2.05"))
    open_positions_swing = await positions_repo.get_open_positions()
    assert len(open_positions_swing) == 1
    assert pos_swing.ratchet_tier == 1
    assert pos_swing.ratchet_floor_price == pos_swing.entry_price

    # SWING recua abaixo do piso (BE) -> Encerra com SWING_RATCHET_STOP
    await tracker.process_price_tick(swing_id, pos_swing.entry_price * Decimal("0.95"))
    assert len(await positions_repo.get_open_positions()) == 0

    await db.close()

    # Limpeza
    await db.close()
