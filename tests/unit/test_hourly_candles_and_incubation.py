"""
Testes unitários dedicados à auditoria de velas de 1 hora (mínimo 3 velas de 1h)
e redirecionamento automático de tokens com dados insuficientes para a Incubadora.
"""

import asyncio
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.config.settings import Settings
from main import VertexBotOrchestrator
from src.database.models import TokenMetadata
from src.scanner.mature_scanner import MatureTokenScanner
from src.security.chart_auditor import ChartHealthAuditor


@pytest.mark.asyncio
async def test_chart_auditor_defaults_to_1h_candles() -> None:
    """Valida que o ChartHealthAuditor foi configurado para o timeframe de 1 hora por padrão."""
    auditor = ChartHealthAuditor()
    assert auditor.candle_timeframe == "hour"
    assert auditor.candle_aggregate == 1
    assert auditor.min_candles_required == 3

    # Testa rejeição quando há apenas 2 velas de 1h
    two_candles = [
        [1789840800, 10.0, 12.0, 9.0, 11.0, 50000.0],
        [1789837200, 9.0, 10.5, 8.5, 10.0, 45000.0],
    ]
    is_ok, reason, details = await auditor.audit_ohlcv_candles(
        chain="arbitrum",
        pool_address="0xpool123",
        mock_candles=two_candles,
    )
    assert is_ok is False
    assert details["is_insufficient_candles"] is True
    assert details["candle_count"] == 2
    assert details["candle_timeframe"] == "hour"
    assert details["candle_aggregate"] == 1
    assert "mínimo exigido: 3 velas fechadas de 1h" in (reason or "")


@pytest.mark.asyncio
async def test_chart_auditor_approves_healthy_3_hourly_candles() -> None:
    """Valida aprovação quando há 3 velas saudáveis de 1 hora fechadas."""
    auditor = ChartHealthAuditor()
    three_healthy_candles = [
        [1789844400, 11.0, 11.5, 10.8, 11.2, 55000.0],
        [1789840800, 10.2, 11.2, 10.0, 11.0, 50000.0],
        [1789837200, 9.5, 10.5, 9.2, 10.2, 45000.0],
    ]
    is_ok, reason, details = await auditor.audit_ohlcv_candles(
        chain="arbitrum",
        pool_address="0xpool123",
        mock_candles=three_healthy_candles,
    )
    assert is_ok is True
    assert reason is None
    assert details["candle_count"] == 3


@pytest.mark.asyncio
async def test_insufficient_candles_redirects_to_incubator(tmp_path: Path) -> None:
    """Valida que quando um token tem menos de 3 velas de 1h, o bot o redireciona para a Incubadora."""
    settings = Settings(
        SQLITE_DB_PATH=str(tmp_path / "test_incub_candles.db"),
        EXECUTION_MODE="PAPER",
        TRADING_STRATEGY_MODE="DUAL",
        MIN_TOKEN_AGE_HOURS=3.0,
        MIN_TOKEN_AGE_HOURS_SCALP=3.0,
        CHART_CANDLE_TIMEFRAME="hour",
        CHART_CANDLE_AGGREGATE=1,
        CHART_MIN_CANDLES=3,
    )
    orch = VertexBotOrchestrator(settings)

    token = TokenMetadata(
        address="0xTokenInsufficientCandles1234567890abcdef",
        dex="camelot",
        chain="arbitrum",
        initial_liquidity_usd=Decimal("25000.0"),
        symbol="CANDLE_PENDING",
        raw_event={
            "age_hours": 3.2,
            "pair_data": {
                "pairAddress": "0xpool123",
                "volume": {"h1": 30000.0},
                "priceChange": {"m5": 1.0, "h1": 2.0, "h24": 5.0},
                "txns": {"m5": {"buys": 10, "sells": 2}, "h1": {"buys": 40, "sells": 10}},
                "liquidity": {"usd": 25000.0},
            },
        },
    )

    # Simula auditor gráfico reprovando por falta de 3 velas de 1h
    orch.chart_auditor.audit_token_pre_entry = AsyncMock(  # type: ignore[method-assign]
        return_value=(
            False,
            "Histórico gráfico insuficiente: apenas 2 vela(s) de 1h",
            {"is_insufficient_candles": True, "candle_count": 2},
        )
    )

    await orch._evaluate_and_execute_entry(token)

    # Verifica se foi colocado na incubadora do scanner
    incubator_tokens = orch.get_incubator_tokens()
    assert any(t["address"] == token.address for t in incubator_tokens)
    target = next(t for t in incubator_tokens if t["address"] == token.address)
    assert target["waiting_reason"] == "AGUARDANDO_3_VELAS_1H"
    assert "Aguardando 3 Velas de 1h" in target["reason_pending"]


@pytest.mark.asyncio
async def test_pending_goplus_indexing_redirects_to_incubator(tmp_path: Path) -> None:
    """Valida que quando o GoPlus ainda está indexando o token, o bot o redireciona para a Incubadora."""
    settings = Settings(
        SQLITE_DB_PATH=str(tmp_path / "test_incub_goplus.db"),
        EXECUTION_MODE="PAPER",
        TRADING_STRATEGY_MODE="DUAL",
        MIN_TOKEN_AGE_HOURS=3.0,
        MIN_TOKEN_AGE_HOURS_SCALP=3.0,
    )
    orch = VertexBotOrchestrator(settings)

    token = TokenMetadata(
        address="0xTokenGoPlusPending1234567890abcdef1234",
        dex="uniswap_v3",
        chain="arbitrum",
        initial_liquidity_usd=Decimal("30000.0"),
        symbol="GOPLUS_PENDING",
        raw_event={
            "age_hours": 3.5,
            "pair_data": {"liquidity": {"usd": 30000.0}},
        },
    )

    # Coloca o token na fila de detecção e simula reprovação por GoPlus pendente
    mock_audit = AsyncMock()
    mock_audit.is_approved = False
    mock_audit.rejection_reason = "Laudo de segurança EVM indisponível para a rede arbitrum: Token ainda não indexado"
    mock_audit.details = {"is_indexing_pending": True}

    await orch.db.initialize()
    orch.is_running = True

    orch.validator.audit_token = AsyncMock(return_value=mock_audit)  # type: ignore[method-assign]
    await orch.detection_queue.put(token)

    # Executa uma iteração do worker de segurança
    task = asyncio.create_task(orch._security_worker())
    await orch.detection_queue.join()
    task.cancel()

    # Confirma que o token foi enviado para a incubadora
    incubator_tokens = orch.get_incubator_tokens()
    assert any(t["address"] == token.address for t in incubator_tokens)
    target = next(t for t in incubator_tokens if t["address"] == token.address)
    assert target["waiting_reason"] == "AGUARDANDO_LAUDO_GOPLUS"
    assert "Aguardando Laudo GoPlus" in target["reason_pending"]
