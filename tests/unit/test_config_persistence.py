"""
Testes Unitários da Persistência de Configurações e Aplicação Dinâmica em Tempo Real.
Valida data/bot_config.json, restauração de parâmetros em novas instâncias,
dimensionamento de ordens via PAPER_BUY_AMOUNT_USD e propagação de risco (trailing stop).
"""

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from main import VertexBotOrchestrator
from src.config.settings import Settings
from src.database.models import ExecutionMode, PositionState, PositionStatus, TokenMetadata


@pytest.fixture
def mock_settings(tmp_path: Path) -> Settings:
    """Fixture fornecendo configurações isoladas para teste."""
    return Settings(
        SQLITE_DB_PATH=str(tmp_path / "test_persistence.db"),
        EXECUTION_MODE="PAPER",
        TRADING_STRATEGY_MODE="SCALP_ONLY",
        PAPER_INITIAL_WALLET_USD=Decimal("10.0"),
        PAPER_BUY_AMOUNT_USD=Decimal("1.0"),
        MAX_CONCURRENT_POSITIONS=5,
        MIN_TRADE_AMOUNT_USD=Decimal("1.0"),
        BREAK_EVEN_GAIN_PCT=Decimal("100.0"),
        TRAILING_STOP_DROP_PCT=Decimal("12.0"),
        EMERGENCY_STOP_LOSS_PCT=Decimal("20.0"),
        MAX_SLIPPAGE_PCT=Decimal("1.5"),
        REENTRY_TRAILING_COOLOFF_SEC=300.0,
        REENTRY_STOPLOSS_COOLOFF_SEC=1800.0,
        REENTRY_MIN_BOUNCE_PCT=Decimal("3.0"),
    )


def test_config_persistence_save_and_reload(mock_settings: Settings, tmp_path: Path) -> None:
    """Valida salvamento em JSON e recarga fiel dos parâmetros pelo VertexBotOrchestrator."""
    custom_cfg_path = tmp_path / "bot_config.json"

    with patch.object(VertexBotOrchestrator, "_get_config_path", return_value=custom_cfg_path):
        orch1 = VertexBotOrchestrator(mock_settings)

        # 1. Configurações iniciais são os defaults
        assert orch1.settings.MAX_CONCURRENT_POSITIONS == 5
        assert orch1.settings.PAPER_BUY_AMOUNT_USD == Decimal("1.0")
        assert orch1.settings.TRAILING_STOP_DROP_PCT == Decimal("12.0")

        # 2. Usuário altera ajustes no dashboard
        new_config = {
            "paper_buy_amount_usd": 2.50,
            "max_concurrent_positions": 8,
            "wallet_balance_usd": 25.00,
            "break_even_gain_pct": 50.0,
            "trailing_stop_drop_pct": 15.0,
            "emergency_stop_loss_pct": 18.0,
            "max_slippage_pct": 2.0,
            "reentry_trailing_cooloff_min": 10.0,
            "reentry_stoploss_cooloff_min": 45.0,
            "reentry_min_bounce_pct": 4.0,
        }
        res = orch1.update_dynamic_config(new_config)

        assert res["paper_buy_amount_usd"] == 2.50
        assert res["max_concurrent_positions"] == 8
        assert res["wallet_balance_usd"] == 25.00
        assert res["trailing_stop_drop_pct"] == 15.0

        # Verifica arquivo salvo em disco
        assert custom_cfg_path.exists()
        saved_data = json.loads(custom_cfg_path.read_text(encoding="utf-8"))
        assert saved_data["paper_buy_amount_usd"] == 2.50
        assert saved_data["max_concurrent_positions"] == 8
        assert saved_data["wallet_balance_usd"] == 25.00
        assert saved_data["trailing_stop_drop_pct"] == 15.0
        assert saved_data["max_slippage_pct"] == 2.0

        # 3. Simula novo encerramento e reinicialização do bot (nova instância do Orchestrator)
        orch2 = VertexBotOrchestrator(mock_settings)

        # Deve ter restaurado automaticamente de custom_cfg_path
        assert orch2.settings.PAPER_BUY_AMOUNT_USD == Decimal("2.5")
        assert orch2.settings.MAX_CONCURRENT_POSITIONS == 8
        assert orch2.execution_engine.balance_usd == Decimal("25.0")
        assert orch2.settings.TRAILING_STOP_DROP_PCT == Decimal("15.0")
        assert orch2.risk_manager.trailing_drop_pct == Decimal("0.15")
        assert orch2.execution_engine.trailing_drop_pct == Decimal("0.15")
        assert orch2.execution_engine.max_slippage_pct == Decimal("0.02")
        assert orch2.settings.BREAK_EVEN_GAIN_PCT == Decimal("50.0")
        assert orch2.risk_manager.break_even_multiplier == Decimal("1.50")
        assert orch2.settings.EMERGENCY_STOP_LOSS_PCT == Decimal("18.0")
        assert orch2.risk_manager.emergency_stop_multiplier == Decimal("0.82")


def test_dynamic_config_updates_active_positions(mock_settings: Settings, tmp_path: Path) -> None:
    """Valida se a atualização de trailing stop atualiza imediatamente as posições ativas em memória."""
    custom_cfg_path = tmp_path / "bot_config.json"

    with patch.object(VertexBotOrchestrator, "_get_config_path", return_value=custom_cfg_path):
        orch = VertexBotOrchestrator(mock_settings)

        # Cria uma posição ativa em memória
        active_pos = PositionState(
            id=1,
            token_address="TokenA11111111111111111111111111111111111",
            mode=ExecutionMode.PAPER,
            status=PositionStatus.OPEN,
            entry_price=Decimal("1.00"),
            initial_token_amount=Decimal("10.0"),
            remaining_token_amount=Decimal("10.0"),
            allocated_capital_usd=Decimal("10.00"),
            highest_price_seen=Decimal("2.00"),
            trailing_drop_pct=Decimal("0.12"),
            trailing_stop_price=Decimal("1.76"),  # 2.0 * (1 - 0.12)
        )
        orch.position_tracker.active_positions[1] = active_pos

        # Altera trailing_stop_drop_pct de 12% para 20%
        orch.update_dynamic_config({"trailing_stop_drop_pct": 20.0})

        # Verifica se o objeto de posição foi recalculado para o novo stop
        assert active_pos.trailing_drop_pct == Decimal("0.20")
        assert active_pos.trailing_stop_price == Decimal("1.60")  # 2.0 * (1 - 0.20)
        assert orch.risk_manager.trailing_drop_pct == Decimal("0.20")


@pytest.mark.asyncio
async def test_evaluate_and_execute_entry_respects_paper_buy_amount(
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Valida se _evaluate_and_execute_entry aloca exatamente PAPER_BUY_AMOUNT_USD."""
    custom_cfg_path = tmp_path / "bot_config.json"

    with patch.object(VertexBotOrchestrator, "_get_config_path", return_value=custom_cfg_path):
        orch = VertexBotOrchestrator(mock_settings)
        orch.settings.PAPER_BUY_AMOUNT_USD = Decimal("3.50")
        orch.execution_engine.balance_usd = Decimal("20.00")
        orch.execution_engine.execute_buy = AsyncMock(return_value=None)  # type: ignore[method-assign]

        token = TokenMetadata(
            address="SolTokenBuyTest1111111111111111111111111111",
            dex="raydium",
            initial_liquidity_usd=Decimal("15000.0"),
            symbol="TEST",
        )

        await orch._evaluate_and_execute_entry(token)

        # execute_buy deve ter sido chamado com exatamente 3.50
        orch.execution_engine.execute_buy.assert_awaited_once_with(
            token,
            amount_usd=Decimal("3.50"),
        )


@pytest.mark.asyncio
async def test_evaluate_and_execute_entry_respects_max_concurrent_positions(
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Valida se o limite dinâmico de posições simultâneas impede novas compras quando atingido."""
    custom_cfg_path = tmp_path / "bot_config.json"

    with patch.object(VertexBotOrchestrator, "_get_config_path", return_value=custom_cfg_path):
        orch = VertexBotOrchestrator(mock_settings)
        orch.settings.MAX_CONCURRENT_POSITIONS = 1
        orch.execution_engine.balance_usd = Decimal("50.00")
        orch.execution_engine.execute_buy = AsyncMock(return_value=None)  # type: ignore[method-assign]

        # Simula 1 posição já ativa
        mock_pos = PositionState(
            id=99,
            token_address="ExistingToken11111111111111111111111111111",
            mode=ExecutionMode.PAPER,
            status=PositionStatus.OPEN,
            entry_price=Decimal("1.0"),
            initial_token_amount=Decimal("10.0"),
            allocated_capital_usd=Decimal("10.0"),
        )
        orch.position_tracker.active_positions[99] = mock_pos

        new_token = TokenMetadata(
            address="NewTokenBuyTest11111111111111111111111111111",
            dex="raydium",
            initial_liquidity_usd=Decimal("15000.0"),
            symbol="NEW",
        )

        # Não deve abrir nova posição pois atingiu max_positions=1
        await orch._evaluate_and_execute_entry(new_token)
        orch.execution_engine.execute_buy.assert_not_awaited()

        # Aumenta limite dinamicamente para 2 posições
        orch.update_dynamic_config({"max_concurrent_positions": 2})
        assert orch.settings.MAX_CONCURRENT_POSITIONS == 2

        # Agora deve permitir abrir a posição
        await orch._evaluate_and_execute_entry(new_token)
        orch.execution_engine.execute_buy.assert_awaited_once()
