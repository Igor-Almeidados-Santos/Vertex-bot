"""Testes unitários para a divisão de slots 50/50 e teto de até 2 posições por token prioritário."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from main import VertexBotOrchestrator
from src.config.settings import Settings
from src.database.connection import DatabaseManager
from src.database.models import ExecutionMode, PositionState, TokenMetadata
from src.engine.priority_pool import PriorityPoolManager


def make_position(
    pos_id: int,
    address: str,
    entry_price: Decimal = Decimal("10.0"),
    current_price: Decimal = Decimal("10.0"),
    amount: Decimal = Decimal("5.0"),
    allocated_usd: Decimal = Decimal("50.0"),
    strategy_type: str = "SCALP",
    break_even: bool = False,
) -> PositionState:
    """Helper para criar PositionState válido em testes."""
    return PositionState(
        id=pos_id,
        token_address=address,
        mode=ExecutionMode.PAPER,
        entry_price=entry_price,
        current_price=current_price,
        highest_price_seen=current_price,
        initial_token_amount=amount,
        remaining_token_amount=amount,
        allocated_capital_usd=allocated_usd,
        strategy_type=strategy_type,
        break_even_triggered=break_even,
    )


@pytest.fixture
def tmp_db_and_settings(tmp_path: Path) -> tuple[DatabaseManager, Settings]:
    db_file = tmp_path / "test_slots.db"
    settings = Settings(
        SQLITE_DB_PATH=str(db_file),
        EXECUTION_MODE="PAPER",
        TRADING_STRATEGY_MODE="DUAL",
        PAPER_INITIAL_WALLET_USD=Decimal("100.0"),
        PAPER_BUY_AMOUNT_USD=Decimal("5.0"),
        MAX_CONCURRENT_POSITIONS=4,
        LIVE_MAX_CONCURRENT_POSITIONS=4,
    )
    db = DatabaseManager(str(db_file))
    return db, settings


@pytest.mark.asyncio
async def test_slot_quotas_calculation_even_and_odd(
    tmp_db_and_settings: tuple[DatabaseManager, Settings],
    tmp_path: Path,
) -> None:
    """Verifica o particionamento 50/50 para números pares e ímpares de slots máximos."""
    _, settings = tmp_db_and_settings
    orch = VertexBotOrchestrator(settings)
    await orch.initialize()

    # Teste 1: Par (4 slots -> 2 prioridades, 2 novos)
    orch.settings.MAX_CONCURRENT_POSITIONS = 4
    quotas = orch.get_slot_quotas("PAPER")
    assert quotas["max_positions"] == 4
    assert quotas["priority_slots_max"] == 2
    assert quotas["new_tokens_slots_max"] == 2
    assert quotas["priority_slots_used"] == 0
    assert quotas["new_tokens_slots_used"] == 0

    # Teste 2: Ímpar (5 slots -> 3 prioridades, 2 novos)
    orch.settings.MAX_CONCURRENT_POSITIONS = 5
    quotas_odd = orch.get_slot_quotas("PAPER")
    assert quotas_odd["max_positions"] == 5
    assert quotas_odd["priority_slots_max"] == 3
    assert quotas_odd["new_tokens_slots_max"] == 2


@pytest.mark.asyncio
async def test_slot_quotas_usage_classification(
    tmp_db_and_settings: tuple[DatabaseManager, Settings],
    tmp_path: Path,
) -> None:
    """Classifica corretamente posições ativas entre prioritárias e novas."""
    _, settings = tmp_db_and_settings
    orch = VertexBotOrchestrator(settings)
    await orch.initialize()

    orch.settings.MAX_CONCURRENT_POSITIONS = 4

    prio_addr = "PrioToken1111111111111111111111111111111111"
    new_addr = "NewToken11111111111111111111111111111111111"

    # Registra prio_addr na lista de prioridades
    prio_token = TokenMetadata(
        address=prio_addr,
        chain="solana",
        dex="raydium",
        initial_liquidity_usd=Decimal("50000.0"),
        symbol="PRIO",
        detection_timestamp=datetime.now(UTC),
    )
    orch.priority_pool.register_executed_token(prio_token, Decimal("10.0"), "SCALP")

    # Cria 1 posição para prio_addr e 1 para new_addr no tracker
    pos_prio = make_position(pos_id=1, address=prio_addr, entry_price=Decimal("10.0"), strategy_type="SCALP")
    pos_new = make_position(pos_id=2, address=new_addr, entry_price=Decimal("1.0"), strategy_type="SCALP")

    orch.paper_tracker.active_positions = {1: pos_prio, 2: pos_new}

    quotas = orch.get_slot_quotas("PAPER")
    assert quotas["priority_slots_used"] == 1
    assert quotas["new_tokens_slots_used"] == 1
    assert quotas["total_active"] == 2


@pytest.mark.asyncio
async def test_priority_token_allows_up_to_two_simultaneous_positions(
    tmp_db_and_settings: tuple[DatabaseManager, Settings],
    tmp_path: Path,
) -> None:
    """Garante que token prioritário pode ter até 2 posições e bloqueia a 3ª."""
    _, settings = tmp_db_and_settings
    orch = VertexBotOrchestrator(settings)
    await orch.initialize()

    prio_addr = "PrioDual1111111111111111111111111111111111"

    prio_token = TokenMetadata(
        address=prio_addr,
        chain="solana",
        dex="raydium",
        initial_liquidity_usd=Decimal("60000.0"),
        symbol="PRIODUAL",
        detection_timestamp=datetime.now(UTC),
        raw_event={
            "priceUsd": "10.0",
            "age_hours": 1000.0,
            "pair_data": {"liquidity": {"usd": 60000.0}, "volume": {"h24": 50000.0}},
        },
    )
    await orch.tokens_repo.save_detected_token(prio_token)
    orch.priority_pool.register_executed_token(prio_token, Decimal("10.0"), "SCALP")

    # Mock market validator e chart auditor para aprovar entrada
    orch.market_validator = MagicMock()
    orch.market_validator.evaluate.return_value = (True, "OK", {})
    orch.market_validator.extract_metrics.return_value = {
        "eligible_scalp": True,
        "eligible_swing": True,
    }
    orch.chart_auditor = AsyncMock()
    orch.chart_auditor.audit_token_pre_entry.return_value = (True, "OK", {})

    # Mock execution engine execute_buy
    buy_mock = AsyncMock(
        return_value=make_position(
            pos_id=2,
            address=prio_addr,
            entry_price=Decimal("11.0"),
            strategy_type="SWING",
        )
    )
    orch.paper_engine.execute_buy = buy_mock

    # 1ª Posição já existente com lucro (+10%) para autorizar scale-in
    pos1 = make_position(
        pos_id=1,
        address=prio_addr,
        entry_price=Decimal("10.0"),
        current_price=Decimal("11.0"),
        strategy_type="SCALP",
    )
    orch.paper_tracker.active_positions[1] = pos1

    # Executa 2ª entrada -> DEVE SER AUTORIZADA
    with patch.object(orch.price_feed, "fetch_prices", new=AsyncMock(return_value={prio_addr: Decimal("11.0")})):
        await orch._locked_evaluate_and_execute_entry(prio_token, mode="PAPER")
    assert buy_mock.call_count == 1

    # Simula abertura da 2ª posição no tracker
    pos2 = make_position(
        pos_id=2,
        address=prio_addr,
        entry_price=Decimal("11.0"),
        current_price=Decimal("11.0"),
        strategy_type="SWING",
    )
    orch.paper_tracker.active_positions[2] = pos2

    # Tenta 3ª entrada -> DEVE SER BLOQUEADA (máx 2 posições por prioritário)
    buy_mock.reset_mock()
    with patch.object(orch.price_feed, "fetch_prices", new=AsyncMock(return_value={prio_addr: Decimal("12.0")})):
        await orch._locked_evaluate_and_execute_entry(prio_token, mode="PAPER")
    assert buy_mock.call_count == 0


@pytest.mark.asyncio
async def test_new_token_strictly_limited_to_one_position(
    tmp_db_and_settings: tuple[DatabaseManager, Settings],
    tmp_path: Path,
) -> None:
    """Garante que tokens novos em análise não podem abrir mais de 1 posição simultânea."""
    _, settings = tmp_db_and_settings
    orch = VertexBotOrchestrator(settings)
    await orch.initialize()

    new_addr = "NewSingle111111111111111111111111111111111"

    new_token = TokenMetadata(
        address=new_addr,
        chain="solana",
        dex="raydium",
        initial_liquidity_usd=Decimal("25000.0"),
        symbol="NEWSINGLE",
        detection_timestamp=datetime.now(UTC),
        raw_event={
            "priceUsd": "2.0",
            "age_hours": 10.0,
            "pair_data": {"liquidity": {"usd": 25000.0}, "volume": {"h24": 15000.0}},
        },
    )
    await orch.tokens_repo.save_detected_token(new_token)

    orch.market_validator = MagicMock()
    orch.market_validator.evaluate.return_value = (True, "OK", {})
    orch.market_validator.extract_metrics.return_value = {
        "eligible_scalp": True,
        "eligible_swing": False,
    }
    orch.chart_auditor = AsyncMock()
    orch.chart_auditor.audit_token_pre_entry.return_value = (True, "OK", {})

    buy_mock = AsyncMock(
        return_value=make_position(pos_id=2, address=new_addr, entry_price=Decimal("2.0"))
    )
    orch.paper_engine.execute_buy = buy_mock

    # 1ª Posição já ativa
    pos1 = make_position(
        pos_id=1,
        address=new_addr,
        entry_price=Decimal("2.0"),
        current_price=Decimal("2.2"),
        strategy_type="SCALP",
    )
    orch.paper_tracker.active_positions[1] = pos1

    # Tenta abrir 2ª posição para token novo -> DEVE SER BLOQUEADA
    with patch.object(orch.price_feed, "fetch_prices", new=AsyncMock(return_value={new_addr: Decimal("2.0")})):
        await orch._locked_evaluate_and_execute_entry(new_token, mode="PAPER")
    assert buy_mock.call_count == 0


@pytest.mark.asyncio
async def test_slot_quotas_enqueuing_when_category_cap_reached(
    tmp_db_and_settings: tuple[DatabaseManager, Settings],
    tmp_path: Path,
) -> None:
    """Verifica que quando a cota de uma categoria está lotada, o token vai para fila com motivo específico."""
    _, settings = tmp_db_and_settings
    orch = VertexBotOrchestrator(settings)
    await orch.initialize()

    orch.settings.MAX_CONCURRENT_POSITIONS = 4
    # priority_slots_max = 2, new_tokens_slots_max = 2

    orch.market_validator = MagicMock()
    orch.market_validator.evaluate.return_value = (True, "OK", {})
    orch.market_validator.extract_metrics.return_value = {
        "eligible_scalp": True,
        "eligible_swing": True,
    }
    orch.chart_auditor = AsyncMock()
    orch.chart_auditor.audit_token_pre_entry.return_value = (True, "OK", {})

    # Preenche 2 posições de novos tokens (lotando a cota de novos: 1 scalp, 1 swing)
    pos_new1 = make_position(pos_id=1, address="AddrNew1", entry_price=Decimal("1.0"), strategy_type="SCALP")
    pos_new2 = make_position(pos_id=2, address="AddrNew2", entry_price=Decimal("1.0"), strategy_type="SWING")
    orch.paper_tracker.active_positions = {1: pos_new1, 2: pos_new2}

    # Novo token tenta entrar -> Cota de novos lotada (2/2)
    token_new3 = TokenMetadata(
        address="AddrNew3",
        chain="solana",
        dex="raydium",
        initial_liquidity_usd=Decimal("20000.0"),
        symbol="NEW3",
        detection_timestamp=datetime.now(UTC),
        raw_event={"priceUsd": "1.0", "age_hours": 8.0},
    )
    await orch.tokens_repo.save_detected_token(token_new3)

    await orch._locked_evaluate_and_execute_entry(token_new3, mode="PAPER")
    assert "AddrNew3" in orch.waiting_tokens
    assert orch.waiting_tokens["AddrNew3"]["waiting_reason"] == "AGUARDANDO_SLOT_NOVOS"

    # Agora token prioritário tenta entrar -> Cota de prioritários livre (0/2)
    prio_addr = "AddrPrio1"
    token_prio = TokenMetadata(
        address=prio_addr,
        chain="solana",
        dex="raydium",
        initial_liquidity_usd=Decimal("80000.0"),
        symbol="PRIO1",
        detection_timestamp=datetime.now(UTC),
        raw_event={"priceUsd": "50.0", "age_hours": 5000.0},
    )
    await orch.tokens_repo.save_detected_token(token_prio)
    orch.priority_pool.register_executed_token(token_prio, Decimal("50.0"), "SCALP")

    buy_mock = AsyncMock(
        return_value=make_position(
            pos_id=3,
            address=prio_addr,
            entry_price=Decimal("50.0"),
            strategy_type="SCALP",
        )
    )
    orch.paper_engine.execute_buy = buy_mock

    with patch.object(orch.price_feed, "fetch_prices", new=AsyncMock(return_value={prio_addr: Decimal("50.0")})):
        await orch._locked_evaluate_and_execute_entry(token_prio, mode="PAPER")
    # Deve ser executado pois a cota de prioritários tem vaga!
    assert buy_mock.call_count >= 1

