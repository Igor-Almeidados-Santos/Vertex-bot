"""
Testes unitários para cálculo e atualização automática da porcentagem de lucro (ROI%)
e PnL em posições ativas e encerradas.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from src.dashboard.server import DashboardServer
from src.database.connection import DatabaseManager
from src.database.models import ExecutionMode, PositionState, PositionStatus
from src.database.repository import PositionsRepository, TokensRepository


def test_position_state_live_roi_properties() -> None:
    """Valida que PositionState calcula roi_pct e unrealized_pnl_usd dinamicamente com base no current_price."""
    pos = PositionState(
        token_address="LIVE_TOKEN_1",
        mode=ExecutionMode.PAPER,
        entry_price=Decimal("1.00"),
        initial_token_amount=Decimal("100.0"),
        allocated_capital_usd=Decimal("100.0"),
    )

    # Inicialmente, current_price é igual ao entry_price -> ROI 0%
    assert pos.current_price == Decimal("1.00")
    assert pos.unrealized_pnl_usd == Decimal("0.0")
    assert pos.roi_pct == Decimal("0.0")

    # Preço sobe 50% ($1.50)
    pos.current_price = Decimal("1.50")
    assert pos.unrealized_pnl_usd == Decimal("50.0")
    assert pos.roi_pct == Decimal("50.0")

    # Preço dobra (+100%, $2.00)
    pos.current_price = Decimal("2.00")
    assert pos.unrealized_pnl_usd == Decimal("100.0")
    assert pos.roi_pct == Decimal("100.0")

    # Preço cai 20% abaixo da entrada ($0.80)
    pos.current_price = Decimal("0.80")
    assert pos.unrealized_pnl_usd == Decimal("-20.0")
    assert pos.roi_pct == Decimal("-20.0")


@pytest.mark.asyncio
async def test_dashboard_handle_positions_enrichment() -> None:
    """Valida que handle_positions no DashboardServer enriquece posições abertas com métricas em tempo real."""
    mock_db = MagicMock(spec=DatabaseManager)
    server = DashboardServer(db=mock_db)

    # Simula retorno do repositório de posições (uma OPEN e uma CLOSED)
    raw_positions = [
        {
            "id": 1,
            "token_address": "TOKEN_OPEN_1",
            "symbol": "OPEN1",
            "status": "OPEN",
            "strategy_type": "SCALP",
            "entry_price": 0.05,
            "initial_token_amount": 1000.0,
            "remaining_token_amount": 1000.0,
            "allocated_capital_usd": 50.0,
            "highest_price_seen": 0.05,
            "trailing_stop_price": 0.044,
            "ratchet_tier": 0,
            "ratchet_floor_price": 0.0,
            "realized_pnl_usd": 0.0,
        },
        {
            "id": 2,
            "token_address": "TOKEN_CLOSED_2",
            "symbol": "CLOSED2",
            "status": "CLOSED",
            "strategy_type": "SWING",
            "entry_price": 1.0,
            "initial_token_amount": 10.0,
            "remaining_token_amount": 0.0,
            "allocated_capital_usd": 10.0,
            "highest_price_seen": 2.2,
            "trailing_stop_price": 1.8,
            "ratchet_tier": 2,
            "ratchet_floor_price": 2.0,
            "realized_pnl_usd": 8.5,
            "exit_price": 1.85,
            "close_reason": "SWING_RATCHET_STOP",
        },
    ]

    server.positions_repo.get_all_positions = AsyncMock(return_value=raw_positions)  # type: ignore

    # Cria estado em memória da posição 1 com cotação atualizada para $0.075 (+50%)
    active_pos = PositionState(
        id=1,
        token_address="TOKEN_OPEN_1",
        mode=ExecutionMode.PAPER,
        entry_price=Decimal("0.05"),
        initial_token_amount=Decimal("1000.0"),
        allocated_capital_usd=Decimal("50.0"),
        highest_price_seen=Decimal("0.08"),
        trailing_stop_price=Decimal("0.0704"),
    )
    active_pos.current_price = Decimal("0.075")

    mock_orchestrator = MagicMock()
    mock_orchestrator.position_tracker.active_positions = {1: active_pos}
    server.orchestrator = mock_orchestrator

    request = make_mocked_request("GET", "/api/positions")
    resp = await server.handle_positions(request)

    assert resp.status == 200
    import json

    body = json.loads(resp.text)
    assert body["status"] == "success"
    data = body["data"]
    assert len(data) == 2

    # Posição Aberta (OPEN)
    p_open = data[0]
    assert p_open["id"] == 1
    assert p_open["current_price"] == 0.075
    assert p_open["highest_price_seen"] == 0.08
    assert p_open["trailing_stop_price"] == 0.0704
    assert p_open["unrealized_pnl_usd"] == 25.0  # (0.075 - 0.05) * 1000 = 25.0
    assert p_open["unrealized_pnl_pct"] == 50.0  # +50% ROI

    # Posição Fechada (CLOSED)
    p_closed = data[1]
    assert p_closed["id"] == 2
    assert p_closed["current_price"] == 1.85
    assert p_closed["close_reason"] == "SWING_RATCHET_STOP"
    assert p_closed["unrealized_pnl_usd"] == 8.5
    assert p_closed["unrealized_pnl_pct"] == 85.0  # (8.5 / 10.0) * 100 = 85.0%

