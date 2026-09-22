"""
Testes Unitários para liberação de tokens nos scanners, ciclo de reanálise imediata
e reabertura de posições ao fechar (conforme regras do sistema).
"""

import asyncio
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from main import VertexBotOrchestrator
from src.database.connection import DatabaseManager
from src.database.models import (
    ExecutionMode,
    PositionState,
    PositionStatus,
    SecurityAuditResult,
    SecurityStatus,
    TokenMetadata,
)
from src.database.repository import PositionsRepository, TokensRepository
from src.engine.reentry import PerformanceScalingManager
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


def test_authorize_immediate_reanalysis_clears_cooloff() -> None:
    """Valida que authorize_immediate_reanalysis remove o cool-off temporal no PerformanceScalingManager."""
    mgr = PerformanceScalingManager(stoploss_cooloff_sec=1800.0)
    token_addr = "TokenStopLoss123"

    # Registra saída em stop-loss (prejuízo)
    mgr.record_exit(
        token_address=token_addr,
        exit_price=Decimal("1.0"),
        exit_reason="STOP_LOSS",
        realized_pnl=Decimal("-2.0"),
        is_winner=False,
    )

    # Cool-off ativo impede reentrada normal
    can_reenter_before, reason = mgr.can_reenter(token_addr, current_price=Decimal("1.0"))
    assert can_reenter_before is False
    assert "Em período de descanso" in reason

    # Autoriza reanálise imediata
    mgr.authorize_immediate_reanalysis(token_addr)

    # Reentrada liberada instantaneamente
    can_reenter_after, _ = mgr.can_reenter(token_addr, current_price=Decimal("1.0"))
    assert can_reenter_after is True


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


@pytest.mark.asyncio
async def test_handle_position_closed_triggers_immediate_reanalysis_and_reentry(tmp_path: Any) -> None:
    """Valida que o fechamento de uma posição reanalisa imediatamente o token e reabre posição se conforme."""
    db_path = str(tmp_path / "reentry_reopen.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    orch = VertexBotOrchestrator(db=db, start_enabled=True)
    orch.is_running = True
    orch.paper_enabled = True
    orch.paper_paused = False
    orch.paper_engine.balance_usd = Decimal("10.0")
    orch.execution_engine.balance_usd = Decimal("10.0")

    token_addr = "TokenReopen111111111111111111111111111111111"
    tok = TokenMetadata(
        address=token_addr,
        symbol="REOPEN",
        initial_liquidity_usd=Decimal("15000.0"),
    )
    await orch.tokens_repo.save_detected_token(tok)

    # Configura mocks para auditoria e execução
    audit_approved = SecurityAuditResult(
        token_address=token_addr,
        status=SecurityStatus.APPROVED,
        security_score=100.0,
        is_mint_revoked=True,
        is_freeze_revoked=True,
        is_lp_burned_or_locked=True,
        lp_burn_percentage=100.0,
        top10_holder_percentage=5.0,
        is_honeypot=False,
        buy_tax_percentage=0.0,
        sell_tax_percentage=0.0,
    )
    orch.validator.audit_token = AsyncMock(return_value=audit_approved)  # type: ignore[method-assign]
    orch.chart_auditor.audit_token_pre_entry = AsyncMock(return_value=(True, None, {}))  # type: ignore[method-assign]
    orch.price_feed.fetch_prices = AsyncMock(return_value={token_addr: Decimal("1.25")})  # type: ignore[method-assign]

    buy_executed_for: list[str] = []

    async def mock_execute_buy(token: TokenMetadata, **kwargs: Any) -> PositionState:
        buy_executed_for.append(token.address)
        return PositionState(
            id=99,
            token_address=token.address,
            mode=ExecutionMode.PAPER,
            entry_price=Decimal("1.25"),
            initial_token_amount=Decimal("10.0"),
            allocated_capital_usd=Decimal("10.0"),
        )

    orch.paper_engine.execute_buy = AsyncMock(side_effect=mock_execute_buy)  # type: ignore[method-assign]

    pos_closed = PositionState(
        id=1,
        token_address=token_addr,
        mode=ExecutionMode.PAPER,
        entry_price=Decimal("1.0"),
        initial_token_amount=Decimal("10.0"),
        allocated_capital_usd=Decimal("10.0"),
        trailing_stop_price=Decimal("1.5"),
        realized_pnl_usd=Decimal("5.0"),
        status=PositionStatus.CLOSED,
    )

    # Executa _reanalyze_and_reenter_token diretamente com delay=0 para teste determinístico
    await orch._reanalyze_and_reenter_token(token_addr, mode="PAPER", delay_seconds=0.0)

    # Valida que o token foi imediatamente reanalisado e abriu nova posição
    assert token_addr in buy_executed_for
    assert len(buy_executed_for) == 1

    await orch.stop()
    await db.close()


@pytest.mark.asyncio
async def test_handle_position_closed_rejects_reentry_if_criteria_not_met(tmp_path: Any) -> None:
    """Valida que se o token falhar na reanálise de segurança, a posição NÃO é reaberta."""
    db_path = str(tmp_path / "reentry_rejected.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    orch = VertexBotOrchestrator(db=db, start_enabled=True)
    orch.is_running = True
    orch.paper_enabled = True

    token_addr = "TokenRugged111111111111111111111111111111111"
    tok = TokenMetadata(
        address=token_addr,
        symbol="RUGGED",
        initial_liquidity_usd=Decimal("1000.0"),
    )
    await orch.tokens_repo.save_detected_token(tok)

    # Mock reprovado na auditoria pós-fechamento (ex: mint reativada / rug)
    audit_rejected = SecurityAuditResult(
        token_address=token_addr,
        status=SecurityStatus.REJECTED,
        security_score=20.0,
        is_mint_revoked=False,
        is_freeze_revoked=True,
        is_lp_burned_or_locked=False,
        lp_burn_percentage=0.0,
        top10_holder_percentage=25.0,
        is_honeypot=False,
        buy_tax_percentage=10.0,
        sell_tax_percentage=10.0,
        rejection_reason="Mint Authority ATIVA",
    )
    orch.validator.audit_token = AsyncMock(return_value=audit_rejected)  # type: ignore[method-assign]
    orch.price_feed.fetch_prices = AsyncMock(return_value={token_addr: Decimal("0.10")})  # type: ignore[method-assign]

    orch.paper_engine.execute_buy = AsyncMock()  # type: ignore[method-assign]

    # Dispara reanálise
    await orch._reanalyze_and_reenter_token(token_addr, mode="PAPER", delay_seconds=0.0)

    # Nenhuma compra deve ter sido executada
    assert orch.paper_engine.execute_buy.call_count == 0

    await orch.stop()
    await db.close()


@pytest.mark.asyncio
async def test_handle_position_closed_respects_quarantine_on_loss(tmp_path: Any) -> None:
    """Valida que posições encerradas em stop/prejuízo NÃO disparam reanálise imediata e respeitam quarentena."""
    db_path = str(tmp_path / "reentry_quarantine.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    orch = VertexBotOrchestrator(db=db, start_enabled=True)
    orch.is_running = True
    orch.paper_enabled = True

    token_addr = "TokenLoss111111111111111111111111111111111"
    tok = TokenMetadata(
        address=token_addr,
        symbol="LOSS",
        initial_liquidity_usd=Decimal("10000.0"),
    )
    await orch.tokens_repo.save_detected_token(tok)

    pos_loss = PositionState(
        id=10,
        token_address=token_addr,
        mode=ExecutionMode.PAPER,
        entry_price=Decimal("1.0"),
        initial_token_amount=Decimal("10.0"),
        allocated_capital_usd=Decimal("10.0"),
        trailing_stop_price=Decimal("0.8"),
        realized_pnl_usd=Decimal("-0.05"),  # Prejuízo!
        status=PositionStatus.STOPPED,
    )

    with patch.object(orch, "_reanalyze_and_reenter_token", new_callable=AsyncMock) as mock_reenter:
        await orch._handle_position_closed(pos_loss)
        await asyncio.sleep(0.05)
        # Não deve disparar reanálise imediata
        mock_reenter.assert_not_called()

    # Confirma que o reentry_manager colocou o token em quarentena
    can_reenter, reason = orch.reentry_manager.can_reenter(token_addr, current_price=Decimal("0.8"))
    assert can_reenter is False
    assert "Em período de descanso" in reason

    await orch.stop()
    await db.close()

