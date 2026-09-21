"""
Testes unitários para renegociação contínua de tokens aprovados/negociados
e distribuição inteligente entre estratégias (Scalp vs Swing).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from main import VertexBotOrchestrator
from src.config.settings import Settings
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


@pytest.mark.asyncio
async def test_reentry_candidates_prioritizes_previously_traded(tmp_path: Any) -> None:
    """Valida que tokens com histórico de negociação encerrada têm prioridade na consulta de reentrada."""
    db = DatabaseManager(str(tmp_path / "test_reentry_prio.db"))
    await db.initialize()
    tokens_repo = TokensRepository(db)
    pos_repo = PositionsRepository(db)

    # 1. Token A: Aprovado, mas nunca negociado (Score 98)
    t_a = TokenMetadata(address="TOKEN_NEVER_TRADED", initial_liquidity_usd=Decimal("30000.0"))
    await tokens_repo.save_detected_token(t_a)
    await tokens_repo.update_audit_result(
        SecurityAuditResult(
            token_address=t_a.address,
            status=SecurityStatus.APPROVED,
            security_score=98.0,
            is_mint_revoked=True,
            is_freeze_revoked=True,
            is_lp_burned_or_locked=True,
            lp_burn_percentage=100.0,
            top10_holder_percentage=5.0,
            is_honeypot=False,
            buy_tax_percentage=0.0,
            sell_tax_percentage=0.0,
        )
    )

    # 2. Token B: Aprovado e já negociado/encerrado (Score 90)
    t_b = TokenMetadata(address="TOKEN_PREVIOUSLY_TRADED", initial_liquidity_usd=Decimal("40000.0"))
    await tokens_repo.save_detected_token(t_b)
    await tokens_repo.update_audit_result(
        SecurityAuditResult(
            token_address=t_b.address,
            status=SecurityStatus.APPROVED,
            security_score=90.0,
            is_mint_revoked=True,
            is_freeze_revoked=True,
            is_lp_burned_or_locked=True,
            lp_burn_percentage=100.0,
            top10_holder_percentage=8.0,
            is_honeypot=False,
            buy_tax_percentage=0.0,
            sell_tax_percentage=0.0,
        )
    )

    # Abre e encerra posição para o Token B
    pos_b = PositionState(
        token_address=t_b.address,
        mode=ExecutionMode.PAPER,
        entry_price=Decimal("0.10"),
        initial_token_amount=Decimal("100.0"),
        allocated_capital_usd=Decimal("10.0"),
        strategy_type="SCALP",
    )
    pos_id = await pos_repo.create_position(pos_b)
    await pos_repo.close_position(pos_id, final_pnl=Decimal("5.0"), status=PositionStatus.CLOSED)

    # Consulta candidatos da watchlist
    candidates = await tokens_repo.get_approved_watchlist_candidates(limit=10)
    assert len(candidates) == 2
    # Token B já negociado DEVE vir em primeiro lugar
    assert candidates[0].address == "TOKEN_PREVIOUSLY_TRADED"
    assert candidates[1].address == "TOKEN_NEVER_TRADED"

    await db.close()


@pytest.mark.asyncio
async def test_reentry_swing_promotion_after_scalp_closed() -> None:
    """Valida que um token que operou Scalp e amadureceu para 2.5h com $25k de liquidez é distribuído para Swing."""
    settings = Settings()
    settings.TRADING_STRATEGY_MODE = "DUAL"
    settings.MIN_TOKEN_AGE_HOURS_SWING = 2.0
    settings.MIN_TOKEN_AGE_HOURS_SWING = 3.0
    settings.MAX_TOKEN_AGE_HOURS_SWING = 6.0
    settings.MIN_LIQUIDITY_SWING_USD = Decimal("20000.0")
    settings.PAPER_INITIAL_WALLET_USD = Decimal("10.0")

    orchestrator = VertexBotOrchestrator(settings)
    orchestrator.execution_engine.balance_usd = Decimal("10.0")
    orchestrator.is_running = True

    # Token que encerrou Scalp há 10 minutos (cool-off cumprido)
    token_addr = "REENTRY_PROMOTED_SWING_1"
    now_ts = asyncio.get_running_loop().time()
    now_utc = datetime.now(UTC)
    now_ms = int(now_utc.timestamp() * 1000)
    created_ms = now_ms - int(2.5 * 3600 * 1000)  # 2.5 horas de vida
    created_ms = now_ms - int(3.5 * 3600 * 1000)  # 3.5 horas de vida

    # Registra saída do token há 600 segundos (cool-off é 300s)
    orchestrator.reentry_manager.record_exit(
        token_address=token_addr,
        exit_price=Decimal("1.00"),
        exit_reason="TRAILING_STOP",
        timestamp=now_ts - 600.0,
    )

    # Simula dados do par na DexScreener (liquidez consolidada $25k)
    pair_data = {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "pool_reentry_123",
        "pairCreatedAt": created_ms,
        "baseToken": {
            "address": token_addr,
            "symbol": "SWINGBOUNCE",
            "name": "Swing Bounce Coin",
        },
        "priceUsd": "1.080000",  # Repique de +8% acima da saída
        "liquidity": {"usd": 25000.0},
        "volume": {"h1": 15000.0},
        "priceChange": {"m5": 2.5},
        "txns": {"m5": {"buys": 12, "sells": 3}},
    }

    orchestrator.price_feed._pair_data_cache[token_addr] = pair_data  # type: ignore

    cand = TokenMetadata(
        address=token_addr,
        symbol="SWINGBOUNCE",
        name="Swing Bounce Coin",
        initial_liquidity_usd=Decimal("25000.0"),
    )

    orchestrator.tokens_repo.get_approved_watchlist_candidates = AsyncMock(return_value=[cand])  # type: ignore
    orchestrator.price_feed.fetch_prices = AsyncMock(return_value={token_addr: Decimal("1.080000")})  # type: ignore

    last_eval_time: dict[str, float] = {}
    await orchestrator._process_watchlist_cycle(
        max_positions=5,
        min_trade=Decimal("1.0"),
        cooldown_sec=60.0,
        last_eval_time=last_eval_time,
    )

    # Token deve ser enfileirado na detection_queue com pair_data enriquecido
    assert orchestrator.detection_queue.qsize() == 1
    enqueued = await orchestrator.detection_queue.get()
    assert enqueued.address == token_addr
    assert enqueued.raw_event["pair_data"]["pairCreatedAt"] == created_ms

    # Avaliação de métricas de mercado para o token enriquecido
    metrics = orchestrator.market_validator.extract_metrics(enqueued)
    assert metrics["eligible_swing"] is True
    assert metrics["eligible_scalp"] is True


@pytest.mark.asyncio
async def test_waiting_queue_stores_eligible_strategy() -> None:
    """Valida que tokens aguardando vagas armazenam explicitamente a estratégia elegível (SWING/SCALP)."""
    settings = Settings()
    orchestrator = VertexBotOrchestrator(settings)

    token = TokenMetadata(
        address="TOKEN_WAITING_SWING",
        symbol="WAIT1",
        initial_liquidity_usd=Decimal("25000.0"),
    )

    # Enfileira aguardando vaga para SWING
    orchestrator._enqueue_waiting_token(token, reason="AGUARDANDO_SLOT", eligible_strategy="SWING")
    item = orchestrator.waiting_tokens["TOKEN_WAITING_SWING"]
    assert item["eligible_strategy"] == "SWING"
    assert item["waiting_reason"] == "AGUARDANDO_SLOT"

    # Enfileira outro aguardando saldo para SCALP
    token_scalp = TokenMetadata(
        address="TOKEN_WAITING_SCALP",
        symbol="WAIT2",
        initial_liquidity_usd=Decimal("8000.0"),
    )
    orchestrator._enqueue_waiting_token(token_scalp, reason="AGUARDANDO_SALDO", eligible_strategy="SCALP")
    item_scalp = orchestrator.waiting_tokens["TOKEN_WAITING_SCALP"]
    assert item_scalp["eligible_strategy"] == "SCALP"
    assert item_scalp["reason_pending"] == "AGUARDANDO_SALDO"

