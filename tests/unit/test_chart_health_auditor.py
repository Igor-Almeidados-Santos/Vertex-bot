"""
Testes Unitários para o ChartHealthAuditor, Watchdog de Posições e Normalização de Endereços.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from src.database.models import ExecutionMode, PositionState, PositionStatus, TokenMetadata
from src.engine.paper import PaperExecutionEngine
from src.engine.price_feed import DexScreenerPriceFeed
from src.engine.risk import RiskManager
from src.engine.tracker import PositionTracker
from src.security.chart_auditor import ChartHealthAuditor


@pytest.mark.asyncio
async def test_audit_ohlcv_rejects_insufficient_candles() -> None:
    """Valida rejeição de tokens com menos de 3 velas fechadas."""
    auditor = ChartHealthAuditor(min_candles_required=3)
    single_candle = [
        [1789754400, 0.0001, 0.0005, 0.00005, 0.00005, 50000.0],
    ]
    is_safe, reason, details = await auditor.audit_ohlcv_candles(
        chain="bsc",
        pool_address="0xpool123",
        mock_candles=single_candle,
    )
    assert not is_safe
    assert reason is not None
    assert "apenas 1 vela(s)" in reason
    assert details["candle_count"] == 1


@pytest.mark.asyncio
async def test_audit_ohlcv_rejects_severe_drawdown() -> None:
    """Valida rejeição de tokens em colapso gráfico com recuo > 45% a partir do topo recente."""
    auditor = ChartHealthAuditor(min_candles_required=3)
    # [timestamp, open, high, low, close, volume]
    collapsed_candles = [
        [1789754600, 0.0004, 0.00042, 0.00020, 0.00021, 10000.0],  # Recuo de ~79% da máxima de 0.0010
        [1789754500, 0.0007, 0.00095, 0.00040, 0.00041, 30000.0],
        [1789754400, 0.0002, 0.00100, 0.00018, 0.00070, 50000.0],
    ]
    is_safe, reason, details = await auditor.audit_ohlcv_candles(
        chain="bsc",
        pool_address="0xpool123",
        mock_candles=collapsed_candles,
    )
    assert not is_safe
    assert reason is not None
    assert "Colapso gráfico em andamento" in reason


@pytest.mark.asyncio
async def test_audit_ohlcv_rejects_shooting_star_dump() -> None:
    """Valida rejeição de pavio superior gigante na vela anterior denunciando dev dump."""
    auditor = ChartHealthAuditor(min_candles_required=3)
    dump_wick_candles = [
        [1789754600, 0.00070, 0.00075, 0.00065, 0.00068, 5000.0],
        # Vela anterior com alta em 0.0010, abertura em 0.00070 e fechamento em 0.00069 (pavio de 93% com perda da abertura)
        [1789754500, 0.00070, 0.00100, 0.00068, 0.00069, 40000.0],
        [1789754400, 0.00060, 0.00072, 0.00058, 0.00070, 15000.0],
    ]
    is_safe, reason, details = await auditor.audit_ohlcv_candles(
        chain="bsc",
        pool_address="0xpool123",
        mock_candles=dump_wick_candles,
    )
    assert not is_safe
    assert reason is not None
    assert "Vela de despejo anômala" in reason


@pytest.mark.asyncio
async def test_audit_ohlcv_accepts_healthy_candles() -> None:
    """Valida aprovação de histórico gráfico saudável com 4 velas em tendência/consolidação."""
    auditor = ChartHealthAuditor(min_candles_required=3)
    healthy_candles = [
        [1789754700, 0.00030, 0.00035, 0.00028, 0.00034, 12000.0],
        [1789754600, 0.00025, 0.00031, 0.00024, 0.00030, 15000.0],
        [1789754500, 0.00020, 0.00026, 0.00019, 0.00025, 18000.0],
        [1789754400, 0.00015, 0.00021, 0.00014, 0.00020, 20000.0],
    ]
    is_safe, reason, details = await auditor.audit_ohlcv_candles(
        chain="base",
        pool_address="0xpoolHealthy",
        mock_candles=healthy_candles,
    )
    assert is_safe
    assert reason is None
    assert details["candle_count"] == 4


@pytest.mark.asyncio
async def test_audit_token_pre_entry_rejects_drained_liquidity() -> None:
    """Valida que o gate de pré-entrada descarta tokens cuja liquidez foi drenada."""
    auditor = ChartHealthAuditor(min_liquidity_usd=Decimal("5000.0"))
    token = TokenMetadata(
        address="0x814d9c72a9e7885efae44029726b99b30c5c79bc",
        symbol="SIL",
        chain="bsc",
        initial_liquidity_usd=Decimal("15000.0"),
    )
    drained_pair = {
        "chainId": "bsc",
        "pairAddress": "0xpool123",
        "liquidity": {"usd": 1200.0},  # Drenada para $1.200 (< $5.000)
        "volume": {"h1": 50000.0},
        "txns": {"m5": {"buys": 5, "sells": 2}, "h1": {"buys": 30, "sells": 10}},
        "priceChange": {"m5": 0.0, "h1": 5.0, "h24": 10.0},
    }
    is_safe, reason, _ = await auditor.audit_token_pre_entry(
        token=token,
        fresh_pair_data=drained_pair,
        bypass_candles_for_testing=True,
    )
    assert not is_safe
    assert reason is not None
    assert "abaixo do piso de segurança" in reason


@pytest.mark.asyncio
async def test_audit_token_pre_entry_rejects_inactive_5m() -> None:
    """Valida rejeição de tokens que não registraram nenhuma transação nos últimos 5 minutos."""
    auditor = ChartHealthAuditor(min_txns_5m=2)
    token = TokenMetadata(
        address="0x814d9c72a9e7885efae44029726b99b30c5c79bc",
        symbol="SIL",
        chain="bsc",
        initial_liquidity_usd=Decimal("10000.0"),
    )
    dormant_pair = {
        "chainId": "bsc",
        "pairAddress": "0xpool123",
        "liquidity": {"usd": 12000.0},
        "volume": {"h1": 20000.0},
        "txns": {"m5": {"buys": 0, "sells": 0}, "h1": {"buys": 20, "sells": 5}},  # 0 txns em 5m
        "priceChange": {"m5": 0.0, "h1": 2.0, "h24": 10.0},
    }
    is_safe, reason, _ = await auditor.audit_token_pre_entry(
        token=token,
        fresh_pair_data=dormant_pair,
        bypass_candles_for_testing=True,
    )
    assert not is_safe
    assert reason is not None
    assert "Negociação inativa nos últimos 5 minutos" in reason


@pytest.mark.asyncio
async def test_case_insensitive_price_feed_resolution() -> None:
    """Valida que o DexScreenerPriceFeed indexa e resolve cotações em minúsculas e checksummed."""
    feed = DexScreenerPriceFeed()
    mock_pairs_payload = {
        "baseToken": {
            "address": "0x814d9C72a9e7885efaE44029726B99b30c5C79Bc",  # Checksummed
            "symbol": "SIL",
            "name": "SillyChain",
        },
        "priceUsd": "0.00032840",
    }
    prices: dict[str, Decimal] = {}
    feed._parse_pair_item(mock_pairs_payload, prices)

    # Consulta com endereço em minúsculas (formato do banco de dados)
    lower_addr = "0x814d9c72a9e7885efae44029726b99b30c5c79bc"
    assert lower_addr in prices
    assert prices[lower_addr] == Decimal("0.00032840")

    # Metadados e pair data acessíveis em ambas as variações
    assert feed.get_metadata(lower_addr) == ("SIL", "SillyChain")
    assert feed.get_pair_data(lower_addr) is not None


@pytest.mark.asyncio
async def test_watchdog_triggers_scalp_timeout_without_new_ticks() -> None:
    """Valida que o Watchdog de Posições encerra posições SCALP que ultrapassaram 1h mesmo sem ticks."""
    from unittest.mock import AsyncMock, MagicMock

    engine = MagicMock()
    engine.execute_sell = AsyncMock()
    positions_repo = MagicMock()
    positions_repo.close_position = AsyncMock()

    risk_manager = RiskManager(scalp_max_hold_seconds=3600.0)  # 1h = 3600s
    tracker = PositionTracker(
        engine=engine,
        positions_repo=positions_repo,
        risk_manager=risk_manager,
    )

    # Cria posição aberta há 65 minutos (ultrapassou 1h)
    opened_time = datetime.now(UTC) - timedelta(minutes=65)
    pos = PositionState(
        id=10,
        token_address="0x814d9c72a9e7885efae44029726b99b30c5c79bc",
        strategy_type="SCALP",
        mode=ExecutionMode.PAPER,
        entry_price=Decimal("0.000328"),
        highest_price_seen=Decimal("0.000328"),
        initial_token_amount=Decimal("1000.0"),
        remaining_token_amount=Decimal("1000.0"),
        allocated_capital_usd=Decimal("1.0"),
        opened_at=opened_time,
        status=PositionStatus.OPEN,
    )
    await tracker.register_position(pos)

    # Executa o watchdog autônomo sem nenhum tick de preço prévio
    closed_ids = await tracker.check_positions_watchdog()

    assert 10 in closed_ids
    assert 10 not in tracker.active_positions
    engine.execute_sell.assert_awaited_once()
    assert engine.execute_sell.call_args[1]["reason"] == "SCALP_TIMEOUT"
    positions_repo.close_position.assert_awaited_once()
