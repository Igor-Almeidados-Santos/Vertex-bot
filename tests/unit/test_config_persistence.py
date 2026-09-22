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
        MAX_TOKEN_AGE_HOURS=3.0,
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
        orch.chart_auditor.audit_token_pre_entry = AsyncMock(return_value=(True, None, {}))  # type: ignore[method-assign]

        token = TokenMetadata(
            address="SolTokenBuyTest1111111111111111111111111111",
            dex="raydium",
            initial_liquidity_usd=Decimal("15000.0"),
            symbol="TEST",
        )

        await orch._evaluate_and_execute_entry(token)

        # execute_buy deve ter sido chamado com exatamente 3.50
        assert orch.execution_engine.execute_buy.await_count == 1
        call_args = orch.execution_engine.execute_buy.await_args
        assert call_args.args[0] == token
        assert call_args.kwargs["amount_usd"] == Decimal("3.50")


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
        orch.chart_auditor.audit_token_pre_entry = AsyncMock(return_value=(True, None, {}))  # type: ignore[method-assign]

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

        # Aumenta limite dinamicamente para 4 posições (2 prioritárias, 2 novas)
        orch.update_dynamic_config({"max_concurrent_positions": 4})
        assert orch.settings.MAX_CONCURRENT_POSITIONS == 4

        # Agora deve permitir abrir a posição
        await orch._evaluate_and_execute_entry(new_token)
        orch.execution_engine.execute_buy.assert_awaited_once()


@pytest.mark.asyncio
async def test_config_persistence_with_max_token_age(mock_settings: Settings, tmp_path: Path) -> None:
    """Valida salvamento e restauração de max_token_age_hours."""
    custom_cfg_path = tmp_path / "bot_config.json"

    with patch.object(VertexBotOrchestrator, "_get_config_path", return_value=custom_cfg_path):
        orch1 = VertexBotOrchestrator(mock_settings)
        assert orch1.settings.MAX_TOKEN_AGE_HOURS == 3.0

        res = orch1.update_dynamic_config({"max_token_age_hours": 1.5})
        assert res["max_token_age_hours"] == 1.5
        assert orch1.settings.MAX_TOKEN_AGE_HOURS == 1.5

        # Recarrega em nova instância
        orch2 = VertexBotOrchestrator(mock_settings)
        assert orch2.settings.MAX_TOKEN_AGE_HOURS == 1.5


@pytest.mark.asyncio
async def test_evaluate_entry_rejects_tokens_older_than_max_age(
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Valida se tokens fora da janela da estratégia (SWING: 3h-6h, SCALP: 30m-720h) são descartados."""
    custom_cfg_path = tmp_path / "bot_config.json"

    with patch.object(VertexBotOrchestrator, "_get_config_path", return_value=custom_cfg_path):
        orch = VertexBotOrchestrator(mock_settings)
        orch.settings.TRADING_STRATEGY_MODE = "SWING_ONLY"  # type: ignore[assignment]
        orch.execution_engine.balance_usd = Decimal("50.00")
        orch.execution_engine.execute_buy = AsyncMock(return_value=None)  # type: ignore[method-assign]
        orch.chart_auditor.audit_token_pre_entry = AsyncMock(return_value=(True, None, {}))  # type: ignore[method-assign]

        # Token velho para Swing (8h de vida > 6.0h limite)
        old_token = TokenMetadata(
            address="OldToken111111111111111111111111111111111111",
            dex="raydium",
            initial_liquidity_usd=Decimal("25000.0"),
            symbol="OLD",
            raw_event={"age_hours": 8.0},
        )
        await orch._evaluate_and_execute_entry(old_token)
        orch.execution_engine.execute_buy.assert_not_awaited()

        # Token dentro da janela Swing (3.0h de vida, janela 2h-4h)
        fresh_token = TokenMetadata(
            address="FreshToken11111111111111111111111111111111111",
            dex="raydium",
            initial_liquidity_usd=Decimal("25000.0"),
            symbol="FRESH",
            raw_event={"age_hours": 3.0},
        )
        await orch._evaluate_and_execute_entry(fresh_token)
        orch.execution_engine.execute_buy.assert_awaited_once()


@pytest.mark.asyncio
async def test_waiting_queue_prunes_tokens_exceeding_max_age(
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Valida se tokens na fila de espera que ultrapassaram a idade máxima da estratégia são descartados."""
    custom_cfg_path = tmp_path / "bot_config.json"

    with patch.object(VertexBotOrchestrator, "_get_config_path", return_value=custom_cfg_path):
        orch = VertexBotOrchestrator(mock_settings)
        orch.settings.TRADING_STRATEGY_MODE = "SWING_ONLY"  # type: ignore[assignment]
        orch.settings.MAX_CONCURRENT_POSITIONS = 5
        orch.execution_engine.balance_usd = Decimal("50.00")
        orch.is_running = True
        orch.price_feed.fetch_prices = AsyncMock(return_value={})  # type: ignore[method-assign]

        # Token com 4.5h na fila (idade excedida para Swing: limite 4.0h)
        orch.waiting_tokens["StaleToken111111111111111111111111111111111"] = {
            "address": "StaleToken111111111111111111111111111111111",
            "symbol": "STALE",
            "age_hours": 4.5,
            "enqueued_at": "2026-09-13T10:00:00+00:00",
        }

        await orch._try_fill_slots_from_waiting_queue()
        assert "StaleToken111111111111111111111111111111111" not in orch.waiting_tokens


@pytest.mark.asyncio
async def test_wallet_balance_reconciliation_and_protection(
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Valida que configurações dinâmicas não resetam o saldo livre enquanto posições estão ativas e reconcilia perfeitamente."""
    custom_cfg_path = tmp_path / "bot_config.json"

    with patch.object(VertexBotOrchestrator, "_get_config_path", return_value=custom_cfg_path):
        orch = VertexBotOrchestrator(mock_settings)
        await orch.db.initialize()
        orch.is_running = True
        orch.settings.PAPER_INITIAL_WALLET_USD = Decimal("10.00")
        orch.execution_engine.balance_usd = Decimal("10.00")

        # Salva tokens primeiro para satisfazer FOREIGN KEY
        tok1 = TokenMetadata(address="TokenA111111111111111111111111111111111111", dex="raydium", initial_liquidity_usd=Decimal("10000"))
        tok2 = TokenMetadata(address="TokenB111111111111111111111111111111111111", dex="raydium", initial_liquidity_usd=Decimal("10000"))
        await orch.tokens_repo.save_detected_token(tok1)
        await orch.tokens_repo.save_detected_token(tok2)

        # Simula criação de 2 posições de $0.10 cada (total alocado = $0.20)
        pos1 = PositionState(
            token_address="TokenA111111111111111111111111111111111111",
            mode=ExecutionMode.PAPER,
            strategy_type="SCALP",
            entry_price=Decimal("1.0"),
            initial_token_amount=Decimal("0.1"),
            allocated_capital_usd=Decimal("0.10"),
            status=PositionStatus.OPEN,
        )
        pos2 = PositionState(
            token_address="TokenB111111111111111111111111111111111111",
            mode=ExecutionMode.PAPER,
            strategy_type="SWING",
            entry_price=Decimal("1.0"),
            initial_token_amount=Decimal("0.1"),
            allocated_capital_usd=Decimal("0.10"),
            status=PositionStatus.OPEN,
        )
        id1 = await orch.positions_repo.create_position(pos1)
        id2 = await orch.positions_repo.create_position(pos2)
        pos1.id = id1
        pos2.id = id2
        await orch.position_tracker.register_position(pos1)
        await orch.position_tracker.register_position(pos2)

        # Saldo livre legítimo deve ser $9.80 (10.00 - 0.20)
        reconciled = await orch.reconcile_wallet_balance()
        assert reconciled == Decimal("9.80")
        assert orch.execution_engine.balance_usd == Decimal("9.80")

        # Se bot_config.json contiver wallet_balance_usd = 10.0 (antigo saldo inicial)
        # uma sincronização ou reload de configuração NÃO PODE resetar o caixa livre para 10.0!
        custom_cfg_path.write_text(json.dumps({"wallet_balance_usd": 10.0, "max_concurrent_positions": 50}), encoding="utf-8")
        orch._sync_config_from_disk_if_present()

        assert orch.execution_engine.balance_usd == Decimal("9.80")
        assert orch.settings.MAX_CONCURRENT_POSITIONS == 50

