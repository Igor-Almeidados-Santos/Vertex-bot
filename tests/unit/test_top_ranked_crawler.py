"""Testes unitários para o rastreador de tokens ranqueados (Top 100 / 200) e flexibilização de consolidados."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from src.database.models import TokenMetadata
from src.scanner.mature_scanner import MatureTokenScanner
from src.security.market_dynamics import MarketDynamicsValidator


@pytest.mark.asyncio
async def test_top_ranked_crawler_pagination_and_endpoints() -> None:
    """Valida a rotação sistemática de páginas (1-5 e 6-10) e endpoints de DEXes líderes."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    scanner = MatureTokenScanner(
        detection_queue=queue,
        target_chains=["solana", "arbitrum", "base"],
        enable_established_pools=True,
    )

    requested_urls: list[str] = []

    async def fake_http_get(url: str) -> dict[str, Any]:
        requested_urls.append(url)
        # Retorna lista vazia no formato GeckoTerminal
        return {"data": []}

    scanner._http_get_json = fake_http_get  # type: ignore[method-assign]

    # Ciclo 1: Deve solicitar páginas 1..5 do primeiro endpoint
    res1 = await scanner._fetch_geckoterminal_top_ranked_pools(pages_per_cycle=5)
    assert isinstance(res1, list)
    assert len(requested_urls) == 5
    # Verifica que as páginas 1 a 5 foram requisitadas
    assert any("page=1" in u for u in requested_urls)
    assert any("page=5" in u for u in requested_urls)

    requested_urls.clear()

    # Avança o índice do ciclo para testar a rotação para a segunda janela (offset 6)
    scanner._top_ranked_cycle_idx = 7  # Avança uma rodada completa de endpoints
    res2 = await scanner._fetch_geckoterminal_top_ranked_pools(pages_per_cycle=5)
    assert isinstance(res2, list)
    assert len(requested_urls) == 5
    assert any("page=6" in u for u in requested_urls)
    assert any("page=10" in u for u in requested_urls)


@pytest.mark.asyncio
async def test_batch_dexscreener_query_chunking() -> None:
    """Verifica se a consulta em lote na DexScreener particiona em blocos de até 30 endereços."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    scanner = MatureTokenScanner(detection_queue=queue, target_chains=["solana"])

    queried_batches: list[str] = []

    async def fake_http_get(url: str) -> dict[str, Any]:
        queried_batches.append(url)
        return {"pairs": []}

    scanner._http_get_json = fake_http_get  # type: ignore[method-assign]

    # Gera 65 endereços fictícios (deve resultar em 3 blocos: 30, 30, 5)
    fake_addresses = [f"TokenAddressFake{i:04d}" for i in range(65)]

    results = await scanner._query_dexscreener_pairs_batch(fake_addresses)
    assert isinstance(results, dict)
    assert len(queried_batches) == 3
    assert "TokenAddressFake0000" in queried_batches[0]
    assert "TokenAddressFake0030" in queried_batches[1]
    assert "TokenAddressFake0060" in queried_batches[2]


def test_market_dynamics_exempts_consolidated_and_top_ranked_from_caps() -> None:
    """Verifica se tokens consolidados e top_ranked são isentos de teto de liquidez ($250k) e idade máxima (720h)."""
    validator = MarketDynamicsValidator(
        min_liquidity_scalp_usd=Decimal("5000.0"),
        max_liquidity_usd=Decimal("250000.0"),
        max_age_hours_scalp=720.0,
    )

    now_utc = datetime.now(UTC)
    now_ms = now_utc.timestamp() * 1000.0
    created_5h_ago_ms = now_ms - (5 * 3600 * 1000.0)
    created_3y_ago_ms = now_ms - (3 * 365 * 24 * 3600 * 1000.0)

    # Caso A: Token jovem comum (5h de vida) com liquidez excessiva ($1.000.000 > $250.000)
    # DEVE SER REPROVADO por exceder max_liquidity_usd
    token_young_regular = TokenMetadata(
        address="YoungToken111111111111111111111111111111111",
        chain="solana",
        dex="raydium",
        initial_liquidity_usd=Decimal("1000000.0"),
        symbol="YOUNG",
        detection_timestamp=now_utc,
        raw_event={
            "pair_data": {
                "pairCreatedAt": created_5h_ago_ms,
                "liquidity": {"usd": 1000000.0},
                "volume": {"h24": 500000.0},
                "txns": {"h24": {"buys": 1000, "sells": 800}},
            },
        },
    )
    metrics_young = validator.extract_metrics(token_young_regular)
    assert not metrics_young.get("is_consolidated")
    assert not metrics_young.get("eligible_scalp")
    is_safe, reason, _ = validator.evaluate(token_young_regular)
    assert not is_safe
    assert reason is not None and "Liquidez excessiva" in reason

    # Caso B: Token consolidado/histórico (ex: Raydium com 3 anos de vida e $10M de liquidez)
    # DEVE SER APROVADO sem bloqueio de teto de liquidez ou idade
    token_consolidated = TokenMetadata(
        address="ConsolidatedRaydium11111111111111111111111111",
        chain="solana",
        dex="raydium",
        initial_liquidity_usd=Decimal("10000000.0"),
        symbol="RAY",
        detection_timestamp=now_utc,
        raw_event={
            "token_tier": "CONSOLIDATED",
            "is_top_ranked": True,
            "pair_data": {
                "pairCreatedAt": created_3y_ago_ms,
                "liquidity": {"usd": 10000000.0},
                "volume": {"h24": 5000000.0, "h1": 50000.0},
                "priceChange": {"m5": 0.5, "h1": 2.0, "h6": 1.0, "h24": 5.0},
                "txns": {
                    "m5": {"buys": 20, "sells": 10},
                    "h24": {"buys": 1000, "sells": 800},
                },
            },
        },
    )
    metrics_consolidated = validator.extract_metrics(token_consolidated)
    assert metrics_consolidated.get("is_consolidated") is True
    assert metrics_consolidated.get("eligible_scalp") is True
    assert metrics_consolidated.get("eligible_swing") is True
    is_safe_cons, _, _ = validator.evaluate(token_consolidated)
    assert is_safe_cons is True

