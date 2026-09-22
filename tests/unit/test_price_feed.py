"""
Testes Unitários para DexScreenerPriceFeed e Integração de Saídas no Tracker.
"""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.database.connection import DatabaseManager
from src.database.models import ExecutionMode, PositionState, PositionStatus, TokenMetadata
from src.database.repository import OrdersRepository, PositionsRepository, TokensRepository
from src.engine.paper import PaperExecutionEngine
from src.engine.price_feed import DexScreenerPriceFeed
from src.engine.risk import RiskManager
from src.engine.tracker import PositionTracker


@pytest.mark.asyncio
async def test_price_feed_empty_list() -> None:
    feed = DexScreenerPriceFeed()
    res = await feed.fetch_prices([])
    assert res == {}
    await feed.close()


@pytest.mark.asyncio
async def test_price_feed_parsing() -> None:
    feed = DexScreenerPriceFeed()
    mock_payload = {
        "pairs": [
            {
                "baseToken": {"address": "TokenA11111111111111111111111111111111111111"},
                "priceUsd": "0.002500",
            },
            {
                "baseToken": {"address": "TokenB22222222222222222222222222222222222222"},
                "priceUsd": "1.4590",
            },
        ]
    }

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_payload)

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp
    mock_cm.__aexit__.return_value = None

    mock_session = AsyncMock()
    mock_session.closed = False
    mock_session.get = MagicMock(return_value=mock_cm)
    feed._session = mock_session

    prices = await feed.fetch_prices([
        "TokenA11111111111111111111111111111111111111",
        "TokenB22222222222222222222222222222222222222",
    ])

    assert prices["TokenA11111111111111111111111111111111111111"] == Decimal("0.002500")
    assert prices["TokenB22222222222222222222222222222222222222"] == Decimal("1.4590")
    await feed.close()


@pytest.mark.asyncio
async def test_price_feed_caches_and_updates_token_metadata(tmp_path: Any) -> None:
    """Verifica se o price feed armazena symbol/name e enriquece tokens_catalogados."""
    feed = DexScreenerPriceFeed()
    mock_payload = {
        "pairs": [
            {
                "baseToken": {
                    "address": "TokenMeta111111111111111111111111111111111",
                    "symbol": "META",
                    "name": "Metadata Token",
                },
                "priceUsd": "0.050",
            },
        ]
    }
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_payload)

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp
    mock_cm.__aexit__.return_value = None

    mock_session = AsyncMock()
    mock_session.closed = False
    mock_session.get = MagicMock(return_value=mock_cm)
    feed._session = mock_session

    token_addr = "TokenMeta111111111111111111111111111111111"
    prices = await feed.fetch_prices([token_addr])
    assert prices[token_addr] == Decimal("0.050")

    meta = feed.get_metadata(token_addr)
    assert meta == ("META", "Metadata Token")
    await feed.close()

    # Testa atualização no banco via TokensRepository
    db = DatabaseManager(str(tmp_path / "meta_test.db"))
    await db.initialize()
    repo = TokensRepository(db)

    await repo.save_detected_token(
        TokenMetadata(
            address=token_addr,
            symbol=None,
            name=None,
            initial_liquidity_usd=Decimal("6000.0"),
        )
    )

    t_before = await repo.get_by_address(token_addr)
    assert t_before is not None
    assert t_before["symbol"] is None

    await repo.update_token_metadata(token_addr, symbol="META", name="Metadata Token")
    t_after = await repo.get_by_address(token_addr)
    assert t_after is not None
    assert t_after["symbol"] == "META"
    assert t_after["name"] == "Metadata Token"
    await db.close()



@pytest.mark.asyncio
async def test_live_price_tick_triggers_break_even_and_trailing_stop(tmp_path: Any) -> None:
    """Valida ciclo completo: tick dobra preço (+100%) -> Break Even -> tick recua 15% -> Trailing Stop."""
    db_path = str(tmp_path / "test_tracker.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    tokens_repo = TokensRepository(db)
    pos_repo = PositionsRepository(db)
    orders_repo = OrdersRepository(db)

    # Insere token pré-requisito para foreign key
    token_addr = "TokenWin111111111111111111111111111111111111"
    await tokens_repo.save_detected_token(
        TokenMetadata(
            address=token_addr,
            symbol="WIN",
            name="Winner Token",
            initial_liquidity_usd=Decimal("15000.0"),
        )
    )

    engine = PaperExecutionEngine(
        positions_repo=pos_repo,
        orders_repo=orders_repo,
        initial_balance_usd=Decimal("100.0"),
    )
    risk_manager = RiskManager(
        break_even_gain_pct=Decimal("100.0"),
        trailing_drop_pct=Decimal("0.12"),  # 12%
        emergency_stop_loss_pct=Decimal("0.20"),
    )
    tracker = PositionTracker(
        engine=engine,
        positions_repo=pos_repo,
        risk_manager=risk_manager,
    )

    # Cria posição de $5.00
    pos = PositionState(
        token_address="TokenWin111111111111111111111111111111111111",
        mode=ExecutionMode.PAPER,
        entry_price=Decimal("0.001"),
        initial_token_amount=Decimal("5000.0"),
        allocated_capital_usd=Decimal("5.0"),
        trailing_drop_pct=Decimal("0.12"),
        status=PositionStatus.OPEN,
    )
    pos_id = await pos_repo.create_position(pos)
    pos.id = pos_id
    await tracker.register_position(pos)

    # 1. Preço sobe para 2x ($0.00205) -> Scalp Target Atingido (Venda de 100%!)
    await tracker.process_price_tick(pos_id, Decimal("0.00205"))
    assert pos.status == PositionStatus.CLOSED
    assert pos_id not in tracker.active_positions

    # Verifica ordem executada de saída total
    orders = await orders_repo.get_orders_by_position(pos_id)
    assert len(orders) == 1
    assert orders[0]["order_type"] == "TRAILING_STOP_EXIT"

    # 2. Testa Trailing Stop em posição onde a cotação sobe mas não atinge 2x e recua
    pos2 = PositionState(
        token_address="TokenWin111111111111111111111111111111111111",
        mode=ExecutionMode.PAPER,
        entry_price=Decimal("0.001"),
        initial_token_amount=Decimal("5000.0"),
        allocated_capital_usd=Decimal("5.0"),
        trailing_drop_pct=Decimal("0.12"),
        status=PositionStatus.OPEN,
    )
    pos2_id = await pos_repo.create_position(pos2)
    pos2.id = pos2_id
    await tracker.register_position(pos2)

    # Sobe para $0.0015 (abaixo de 2x)
    await tracker.process_price_tick(pos2_id, Decimal("0.0015"))
    assert pos2.highest_price_seen == Decimal("0.0015")
    assert pos2.trailing_stop_price == Decimal("0.00132")

    # Recua para $0.00130 (abaixo do trailing stop $0.00132) -> Trailing Stop!
    await tracker.process_price_tick(pos2_id, Decimal("0.00130"))
    assert pos2.status == PositionStatus.CLOSED
    assert pos2_id not in tracker.active_positions

    orders2 = await orders_repo.get_orders_by_position(pos2_id)
    assert len(orders2) == 1
    assert orders2[0]["order_type"] == "TRAILING_STOP_EXIT"

    await db.close()


@pytest.mark.asyncio
async def test_price_feed_prioritizes_highest_liquidity_pair_and_ignores_micropools() -> None:
    """Valida que o PriceFeed preserva a pool primária e não é sobrescrito por micropools secundárias."""
    feed = DexScreenerPriceFeed()
    token_addr = "TokenMultiPool1111111111111111111111111111"

    # Simula resposta DexScreener com 3 pools:
    # 1. Raydium principal: $250.000 liquidez
    # 2. Meteora média: $50.000 liquidez
    # 3. Micro pool residual: $10 liquidez
    mock_payload = {
        "pairs": [
            {
                "baseToken": {"address": token_addr, "symbol": "MULTI", "name": "Multi Pool Token"},
                "dexId": "raydium",
                "pairAddress": "RaydiumMainPair1111111111111111111111111111",
                "priceUsd": "0.005000",
                "liquidity": {"usd": 250000.0},
            },
            {
                "baseToken": {"address": token_addr, "symbol": "MULTI", "name": "Multi Pool Token"},
                "dexId": "meteora",
                "pairAddress": "MeteoraSecondaryPair22222222222222222222222",
                "priceUsd": "0.004950",
                "liquidity": {"usd": 50000.0},
            },
            {
                "baseToken": {"address": token_addr, "symbol": "MULTI", "name": "Multi Pool Token"},
                "dexId": "meteora",
                "pairAddress": "MeteoraDeadPool3333333333333333333333333333",
                "priceUsd": "0.004800",
                "liquidity": {"usd": 10.0},
            },
        ]
    }

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_payload)

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp
    mock_cm.__aexit__.return_value = None

    mock_session = AsyncMock()
    mock_session.closed = False
    mock_session.get = MagicMock(return_value=mock_cm)
    feed._session = mock_session

    prices = await feed.fetch_prices([token_addr])
    assert prices[token_addr] == Decimal("0.005000")

    cached_pair = feed.get_pair_data(token_addr)
    assert cached_pair is not None
    assert cached_pair["dexId"] == "raydium"
    assert cached_pair["pairAddress"] == "RaydiumMainPair1111111111111111111111111111"
    assert cached_pair["liquidity"]["usd"] == 250000.0

    await feed.close()


