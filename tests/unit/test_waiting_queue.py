"""
Testes unitários para a Fila de Espera de Tokens Aprovados e Alocação Dinâmica Estrita de Compra.
"""

import asyncio
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from main import VertexBotOrchestrator
from src.config.settings import Settings
from src.dashboard.server import create_dashboard_app
from src.database.connection import DatabaseManager
from src.database.models import PositionStatus, TokenMetadata


@pytest.fixture
def tmp_db_and_settings(tmp_path: Path) -> tuple[DatabaseManager, Settings]:
    db_file = tmp_path / "test_waiting.db"
    settings = Settings(
        SQLITE_DB_PATH=str(db_file),
        EXECUTION_MODE="PAPER",
        TRADING_STRATEGY_MODE="SCALP_ONLY",
        PAPER_INITIAL_WALLET_USD=Decimal("10.0"),
        PAPER_BUY_AMOUNT_USD=Decimal("0.50"),
        MAX_CONCURRENT_POSITIONS=2,
    )
    db = DatabaseManager(str(db_file))
    return db, settings


@pytest.mark.asyncio
async def test_strict_buy_amount_allocated(tmp_db_and_settings: tuple[DatabaseManager, Settings], tmp_path: Path) -> None:
    """Verifica que o valor de compra definido nos Ajustes é estritamente alocado."""
    db, settings = tmp_db_and_settings
    orchestrator = VertexBotOrchestrator(settings)
    await orchestrator.initialize()

    # Define o valor de compra para $0.35
    orchestrator.update_dynamic_config({"paper_buy_amount_usd": 0.35})
    assert orchestrator.settings.PAPER_BUY_AMOUNT_USD == Decimal("0.35")

    token = TokenMetadata(
        address="TokenBuyAmountTest1111111111111111111111111111",
        symbol="TEST1",
        name="Test 1",
        initial_liquidity_usd=Decimal("10000.0"),
    )

    await orchestrator.tokens_repo.save_detected_token(token)
    with patch.object(orchestrator.price_feed, "fetch_prices", new=AsyncMock(return_value={token.address: Decimal("1.0")})):
        await orchestrator._evaluate_and_execute_entry(token)

    assert len(orchestrator.position_tracker.active_positions) == 1
    pos = list(orchestrator.position_tracker.active_positions.values())[0]
    assert pos.allocated_capital_usd == Decimal("0.35")


@pytest.mark.asyncio
async def test_waiting_queue_enqueues_when_slots_full(
    tmp_db_and_settings: tuple[DatabaseManager, Settings],
    tmp_path: Path,
) -> None:
    """Verifica que tokens aprovados entram na fila de espera quando os slots estão esgotados."""
    db, settings = tmp_db_and_settings
    orchestrator = VertexBotOrchestrator(settings)
    orchestrator._get_waiting_tokens_path = lambda: tmp_path / "waiting_tokens.json"  # type: ignore[method-assign]
    orchestrator._get_config_path = lambda: tmp_path / "bot_config.json"  # type: ignore[method-assign]
    await orchestrator.initialize()

    orchestrator.settings.MAX_CONCURRENT_POSITIONS = 1
    orchestrator.settings.PAPER_BUY_AMOUNT_USD = Decimal("0.50")

    token1 = TokenMetadata(
        address="TokenSlotFull1111111111111111111111111111111",
        symbol="SLOT1",
        name="Slot Token 1",
        initial_liquidity_usd=Decimal("10000.0"),
    )
    token2 = TokenMetadata(
        address="TokenSlotFull2222222222222222222222222222222",
        symbol="SLOT2",
        name="Slot Token 2",
        initial_liquidity_usd=Decimal("8000.0"),
    )
    await orchestrator.tokens_repo.save_detected_token(token1)
    await orchestrator.tokens_repo.save_detected_token(token2)

    with patch.object(orchestrator.price_feed, "fetch_prices", new=AsyncMock(return_value={token1.address: Decimal("1.0"), token2.address: Decimal("2.0")})):
        # Entra no slot único disponível
        await orchestrator._evaluate_and_execute_entry(token1)
        assert len(orchestrator.position_tracker.active_positions) == 1
        assert len(orchestrator.waiting_tokens) == 0

        # Segundo token aprovado encontra slots cheios e entra na fila de espera
        await orchestrator._evaluate_and_execute_entry(token2)
        assert len(orchestrator.position_tracker.active_positions) == 1
        assert len(orchestrator.waiting_tokens) == 1
        assert token2.address in orchestrator.waiting_tokens
        assert orchestrator.waiting_tokens[token2.address]["waiting_reason"] == "AGUARDANDO_SLOT"


@pytest.mark.asyncio
async def test_waiting_queue_drained_when_slot_freed(
    tmp_db_and_settings: tuple[DatabaseManager, Settings],
    tmp_path: Path,
) -> None:
    """Verifica que quando uma posição é fechada, a fila de espera é imediatamente processada."""
    db, settings = tmp_db_and_settings
    orchestrator = VertexBotOrchestrator(settings)
    orchestrator._get_waiting_tokens_path = lambda: tmp_path / "waiting_tokens.json"  # type: ignore[method-assign]
    orchestrator._get_config_path = lambda: tmp_path / "bot_config.json"  # type: ignore[method-assign]
    orchestrator.is_running = True
    await orchestrator.initialize()

    orchestrator.settings.MAX_CONCURRENT_POSITIONS = 1
    orchestrator.settings.PAPER_BUY_AMOUNT_USD = Decimal("0.50")

    token1 = TokenMetadata(
        address="TokenDrainTest11111111111111111111111111111111",
        symbol="DRN1",
        name="Drain 1",
        initial_liquidity_usd=Decimal("10000.0"),
    )
    token2 = TokenMetadata(
        address="TokenDrainTest22222222222222222222222222222222",
        symbol="DRN2",
        name="Drain 2",
        initial_liquidity_usd=Decimal("10000.0"),
    )
    await orchestrator.tokens_repo.save_detected_token(token1)
    await orchestrator.tokens_repo.save_detected_token(token2)

    prices = {token1.address: Decimal("1.0"), token2.address: Decimal("1.5")}

    with patch.object(orchestrator.price_feed, "fetch_prices", new=AsyncMock(return_value=prices)):
        await orchestrator._evaluate_and_execute_entry(token1)
        await orchestrator._evaluate_and_execute_entry(token2)
        assert len(orchestrator.waiting_tokens) == 1

        # Fecha a posição do token 1 simulando Trailing Stop
        pos1 = next(p for p in orchestrator.position_tracker.active_positions.values() if p.token_address == token1.address)
        pos1.status = PositionStatus.CLOSED
        if pos1.id is not None:
            orchestrator.position_tracker.active_positions.pop(pos1.id, None)

        # Notifica encerramento da posição (deve disparar preenchimento da vaga)
        await orchestrator._handle_position_closed(pos1)
        for _ in range(20):
            if len(orchestrator.position_tracker.active_positions) == 1:
                break
            await asyncio.sleep(0.05)

        # Token 2 deve ter saído da fila e aberto posição
        assert len(orchestrator.waiting_tokens) == 0
        assert len(orchestrator.position_tracker.active_positions) == 1
        assert any(p.token_address == token2.address for p in orchestrator.position_tracker.active_positions.values())


@pytest.mark.asyncio
async def test_waiting_tokens_api_endpoint(tmp_db_and_settings: tuple[DatabaseManager, Settings]) -> None:
    """Testa o endpoint GET /api/waiting_tokens da API do Dashboard."""
    db, settings = tmp_db_and_settings
    orchestrator = VertexBotOrchestrator(settings)
    await orchestrator.initialize()

    orchestrator.waiting_tokens["TestWaitingAddr123"] = {
        "address": "TestWaitingAddr123",
        "symbol": "WAIT1",
        "name": "Waiting Token 1",
        "chain": "solana",
        "dex": "raydium",
        "initial_liquidity_usd": 15000.0,
        "waiting_reason": "AGUARDANDO_SLOT",
        "enqueued_at": "2026-09-12T16:00:00Z",
        "last_price": 0.05,
    }

    app = create_dashboard_app(db=db, orchestrator=orchestrator)
    client = TestClient(TestServer(app))
    await client.start_server()

    try:
        resp = await client.get("/api/waiting_tokens")
        assert resp.status == 200
        json_data = await resp.json()
        assert json_data["status"] == "success"
        assert len(json_data["data"]) == 1
        assert json_data["data"][0]["symbol"] == "WAIT1"
    finally:
        await client.close()

