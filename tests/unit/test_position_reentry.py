"""
Testes Unitários para liberação de tokens nos scanners e ciclo de reanálise de posição.
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.database.models import ExecutionMode, PositionState, PositionStatus
from src.engine.risk import RiskManager
from src.engine.tracker import PositionTracker
from src.scanner.composite_scanner import CompositeScanner
from src.scanner.graduation_scanner import RaydiumGraduationScanner
from src.scanner.mature_scanner import MatureTokenScanner


def test_scanner_release_token() -> None:
    """Valida que o método release_token remove o endereço dos caches de vistos."""
    queue: asyncio.Queue[object] = asyncio.Queue()
    mature = MatureTokenScanner(detection_queue=queue)  # type: ignore[arg-type]
    grad = RaydiumGraduationScanner(detection_queue=queue)  # type: ignore[arg-type]
    composite = CompositeScanner([mature, grad])

    token_addr = "TokenXYZ12345"

    # Simula token já visto em ambos os scanners
    mature._remember_seen_address(token_addr)
    grad._remember_seen_address(token_addr)
    assert token_addr in mature._seen_addresses
    assert token_addr in grad._seen_addresses

    # Chama release_token no composite
    composite.release_token(token_addr)

    assert token_addr not in mature._seen_addresses
    assert token_addr not in grad._seen_addresses


@pytest.mark.asyncio
async def test_position_tracker_invokes_on_closed_callback() -> None:
    """Valida que o PositionTracker invoca o callback on_position_closed ao fechar 100% da posição."""
    engine = MagicMock()
    engine.execute_sell = AsyncMock()
    positions_repo = MagicMock()
    positions_repo.close_position = AsyncMock()
    positions_repo.update_tracking = AsyncMock()
    risk_mgr = RiskManager()

    callback_called: list[PositionState] = []

    async def mock_callback(pos: PositionState) -> None:
        callback_called.append(pos)

    tracker = PositionTracker(
        engine=engine,
        positions_repo=positions_repo,
        risk_manager=risk_mgr,
        on_position_closed=mock_callback,
    )

    pos = PositionState(
        id=10,
        token_address="ADDR_TEST_CLOSE",
        mode=ExecutionMode.PAPER,
        entry_price=Decimal("1.0"),
        initial_token_amount=Decimal("100.0"),
        remaining_token_amount=Decimal("50.0"),
        allocated_capital_usd=Decimal("100.0"),
        highest_price_seen=Decimal("2.0"),
        trailing_stop_price=Decimal("1.76"),
        break_even_triggered=True,
        status=PositionStatus.PARTIALLY_CLOSED,
    )
    await tracker.register_position(pos)
    assert 10 in tracker.active_positions

    # Preço cai para disparar trailing stop (máxima 2.0, trailing stop em 1.76, preço cai para 1.50)
    await tracker.process_price_tick(10, Decimal("1.50"))

    # Verifica que posição foi fechada e removida das ativas
    assert 10 not in tracker.active_positions
    assert len(callback_called) == 1
    assert callback_called[0].token_address == "ADDR_TEST_CLOSE"
    assert callback_called[0].status == PositionStatus.CLOSED
