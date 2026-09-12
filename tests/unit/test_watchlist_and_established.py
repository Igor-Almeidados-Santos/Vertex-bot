"""
Testes unitários para:
1. Watchlist ativa de tokens aprovados para entrada e reentrada contínua.
2. Expansão de janela para tokens consolidados de 1 dia a 1 mês (24h a 720h).
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.database.connection import DatabaseManager
from src.database.models import SecurityAuditResult, SecurityStatus, TokenMetadata
from src.database.repository import TokensRepository
from src.scanner.mature_scanner import MatureTokenScanner


@pytest.mark.asyncio
async def test_approved_watchlist_candidates_repository(tmp_path: Any) -> None:
    """Valida que get_approved_watchlist_candidates filtra apenas APPROVED e respeita exclusões."""
    db = DatabaseManager(str(tmp_path / "test_watchlist.db"))
    await db.initialize()
    repo = TokensRepository(db)

    # Token 1: Aprovado (Score 95)
    t1 = TokenMetadata(
        address="TOKEN_APPR_1",
        symbol="APP1",
        name="Approved One",
        initial_liquidity_usd=Decimal("50000.0"),
    )
    await repo.save_detected_token(t1)
    await repo.update_audit_result(
        SecurityAuditResult(
            token_address=t1.address,
            status=SecurityStatus.APPROVED,
            security_score=95.0,
            is_mint_revoked=True,
            is_freeze_revoked=True,
            is_lp_burned_or_locked=True,
            lp_burn_percentage=100.0,
            top10_holder_percentage=10.0,
            is_honeypot=False,
            buy_tax_percentage=0.0,
            sell_tax_percentage=0.0,
        )
    )

    # Token 2: Aprovado (Score 90)
    t2 = TokenMetadata(
        address="TOKEN_APPR_2",
        symbol="APP2",
        name="Approved Two",
        initial_liquidity_usd=Decimal("60000.0"),
    )
    await repo.save_detected_token(t2)
    await repo.update_audit_result(
        SecurityAuditResult(
            token_address=t2.address,
            status=SecurityStatus.APPROVED,
            security_score=90.0,
            is_mint_revoked=True,
            is_freeze_revoked=True,
            is_lp_burned_or_locked=True,
            lp_burn_percentage=100.0,
            top10_holder_percentage=12.0,
            is_honeypot=False,
            buy_tax_percentage=0.0,
            sell_tax_percentage=0.0,
        )
    )

    # Token 3: Rejeitado
    t3 = TokenMetadata(
        address="TOKEN_REJ_3",
        symbol="REJ3",
        name="Rejected Three",
        initial_liquidity_usd=Decimal("1000.0"),
    )
    await repo.save_detected_token(t3)
    await repo.update_audit_result(
        SecurityAuditResult(
            token_address=t3.address,
            status=SecurityStatus.REJECTED,
            rejection_reason="Liquidez insuficiente",
            security_score=0.0,
            is_mint_revoked=True,
            is_freeze_revoked=True,
            is_lp_burned_or_locked=False,
            lp_burn_percentage=0.0,
            top10_holder_percentage=50.0,
            is_honeypot=False,
            buy_tax_percentage=0.0,
            sell_tax_percentage=0.0,
        )
    )

    # 1. Sem exclusão: Deve retornar apenas os 2 aprovados ordenados por score desc
    candidates = await repo.get_approved_watchlist_candidates(limit=10)
    assert len(candidates) == 2
    assert candidates[0].address == "TOKEN_APPR_1"
    assert candidates[1].address == "TOKEN_APPR_2"

    # 2. Com exclusão (TOKEN_APPR_1 já com posição aberta): Deve retornar apenas TOKEN_APPR_2
    filtered = await repo.get_approved_watchlist_candidates(
        exclude_addresses=["TOKEN_APPR_1"],
        limit=10,
    )
    assert len(filtered) == 1
    assert filtered[0].address == "TOKEN_APPR_2"

    await db.close()


@pytest.mark.asyncio
async def test_established_tokens_age_window() -> None:
    """Valida que a janela de 720h aceita tokens consolidados entre 1 dia (24h) e 1 mês (720h)."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    scanner = MatureTokenScanner(
        detection_queue=queue,
        min_age_hours=0.25,
        max_age_hours=720.0,  # 30 dias
    )

    now_ms = int(datetime.now(UTC).timestamp() * 1000)

    # Token 1: 2 dias de existência (48 horas) -> Deve ser aceito!
    age_2d = MatureTokenScanner.calculate_age_from_ms(now_ms - (48 * 3600 * 1000), now_ms)
    assert scanner.min_age_hours <= age_2d <= scanner.max_age_hours

    # Token 2: 20 dias de existência (480 horas) -> Deve ser aceito!
    age_20d = MatureTokenScanner.calculate_age_from_ms(now_ms - (480 * 3600 * 1000), now_ms)
    assert scanner.min_age_hours <= age_20d <= scanner.max_age_hours

    # Token 3: 40 dias de existência (960 horas) -> Rejeitado (> 30 dias)
    age_40d = MatureTokenScanner.calculate_age_from_ms(now_ms - (960 * 3600 * 1000), now_ms)
    assert not (scanner.min_age_hours <= age_40d <= scanner.max_age_hours)


@pytest.mark.asyncio
async def test_evaluate_and_enrich_established_token() -> None:
    """Valida enriquecimento de token consolidado de 5 dias emitido para a fila de triagem."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    scanner = MatureTokenScanner(
        detection_queue=queue,
        min_age_hours=0.25,
        max_age_hours=720.0,
    )

    now_utc = datetime.now(UTC)
    now_ms = int(now_utc.timestamp() * 1000)
    created_ms = now_ms - (5 * 24 * 3600 * 1000)  # 5 dias atrás

    pair_data = {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "pool_established_123",
        "pairCreatedAt": created_ms,
        "baseToken": {
            "address": "ESTABLISHED_TOKEN_1",
            "symbol": "ESTAB",
            "name": "Established Coin",
        },
        "priceUsd": "1.250000",
        "liquidity": {"usd": 150000.0},
    }

    token, permanent = await scanner._evaluate_and_enrich_token(
        token_address="ESTABLISHED_TOKEN_1",
        hint={"pair_data": pair_data},
    )

    assert permanent is True
    assert token is not None
    assert token.symbol == "ESTAB"
    assert token.initial_liquidity_usd == Decimal("150000.0")
    assert token.raw_event["priceUsd"] == "1.250000"
    assert token.raw_event["age_hours"] >= 119.0


@pytest.mark.asyncio
async def test_fetch_geckoterminal_established_pools() -> None:
    """Valida a consulta e parsing de pools consolidadas e trending do GeckoTerminal."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    scanner = MatureTokenScanner(detection_queue=queue)

    mock_response = {
        "data": [
            {
                "attributes": {
                    "name": "TOP / SOL",
                    "address": "pool_top_sol_address",
                    "reserve_in_usd": "250000.0",
                    "pool_created_at": "2026-09-01T12:00:00Z",
                },
                "relationships": {
                    "base_token": {
                        "data": {
                            "id": "solana_TOP_TOKEN_ADDRESS_GECKO"
                        }
                    }
                }
            }
        ]
    }

    scanner._http_get_json = AsyncMock(return_value=mock_response)  # type: ignore

    pools = await scanner._fetch_geckoterminal_established_pools()
    assert len(pools) == 2  # 1 de /pools e 1 de /trending_pools
    addr, hint = pools[0]
    assert addr == "TOP_TOKEN_ADDRESS_GECKO"
    assert hint["reserve_usd"] == "250000.0"
    assert hint["name"] == "TOP / SOL"


@pytest.mark.asyncio
async def test_orchestrator_process_watchlist_cycle() -> None:
    """Valida que o ciclo da watchlist identifica tokens aprovados sem posição e enfileira na detection_queue com preço atual."""
    from main import VertexBotOrchestrator
    from src.config.settings import Settings

    settings = Settings()
    settings.PAPER_INITIAL_WALLET_USD = Decimal("10.0")
    orchestrator = VertexBotOrchestrator(settings)
    orchestrator.is_running = True

    cand = TokenMetadata(
        address="WATCHLIST_WINNER_1",
        symbol="WIN1",
        name="Winner One",
        initial_liquidity_usd=Decimal("50000.0"),
    )

    orchestrator.tokens_repo.get_approved_watchlist_candidates = AsyncMock(  # type: ignore
        return_value=[cand]
    )
    orchestrator.price_feed.fetch_prices = AsyncMock(  # type: ignore
        return_value={"WATCHLIST_WINNER_1": Decimal("0.045000")}
    )

    last_eval_time: dict[str, float] = {}
    await orchestrator._process_watchlist_cycle(
        max_positions=5,
        min_trade=Decimal("1.0"),
        cooldown_sec=60.0,
        last_eval_time=last_eval_time,
    )

    assert orchestrator.detection_queue.qsize() == 1
    enqueued = await orchestrator.detection_queue.get()
    assert enqueued.address == "WATCHLIST_WINNER_1"
    assert enqueued.raw_event["priceUsd"] == "0.045000"
    assert "WATCHLIST_WINNER_1" in last_eval_time

    # Segundo ciclo imediato: cooldown deve evitar spam do mesmo token
    await orchestrator._process_watchlist_cycle(
        max_positions=5,
        min_trade=Decimal("1.0"),
        cooldown_sec=60.0,
        last_eval_time=last_eval_time,
    )
    assert orchestrator.detection_queue.empty()

