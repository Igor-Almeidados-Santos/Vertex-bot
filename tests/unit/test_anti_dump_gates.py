"""
Testes Unitários para os Gates Anti-Fake CLMM, Anti-Wash Trading e Anti-Dev Dump.
Valida a rejeição automática de:
1. Impersonação de tokens oficiais (SOL, USDC, USDT falsos).
2. Piscinas CLMM com liquidez concentrada bilionária fictícia.
3. Preço congelado / Gráficos de código de barras (Wash Trading).
4. Despejo massivo de desenvolvedor e snipers (Caso Nike -99.94%).
5. Drenagem de liquidez e exaustão parabólica pós-pump.
"""

from decimal import Decimal
import pytest

from src.database.models import TokenMetadata
from src.security.market_dynamics import MarketDynamicsValidator


@pytest.fixture
def validator() -> MarketDynamicsValidator:
    return MarketDynamicsValidator(
        min_volume_1h_usd=Decimal("15000.0"),
        min_buy_ratio_5m_pct=Decimal("50.0"),
        min_price_change_5m_pct=Decimal("-2.0"),
        min_age_hours_scalp=2.0,
        max_age_hours_scalp=720.0,
        min_age_hours_swing=3.0,
        max_age_hours_swing=6.0,
        min_liquidity_scalp_usd=Decimal("5000.0"),
        min_liquidity_swing_usd=Decimal("20000.0"),
        max_liquidity_usd=Decimal("250000.0"),
        max_seller_to_buyer_ratio=Decimal("1.5"),
        min_liquidity_to_volume_ratio=Decimal("0.05"),
        min_unique_traders_24h=15,
        max_parabolic_1h_gain_pct=Decimal("250.0"),
    )


def test_anti_impersonation_fake_sol(validator: MarketDynamicsValidator) -> None:
    """Valida rejeição de token fingindo ser SOL com endereço falso (Print 2)."""
    fake_sol = TokenMetadata(
        address="FbLaA111111111111111111111111111111111131zC",
        name="Solana",
        symbol="SOL",
        decimals=9,
        pool_address="PoolFakeSol11111111111111111111111111111111",
        initial_liquidity_usd=Decimal("50000.0"),
        dex="raydium",
        created_at_ts=1700000000,
        raw_event={
            "pair_data": {
                "baseToken": {
                    "symbol": "SOL",
                    "address": "FbLaA111111111111111111111111111111111131zC",
                },
                "liquidity": {"usd": 50000.0},
            }
        },
    )

    is_ok, reason, _ = validator.evaluate(fake_sol)
    assert not is_ok
    assert reason is not None
    assert "Impersonação de token oficial detectada" in reason
    assert "SOL" in reason


def test_anti_fake_clmm_excessive_liquidity(validator: MarketDynamicsValidator) -> None:
    """Valida rejeição de tokens com liquidez surreal de CLMM ($110M / $2.17B) (Prints 1 e 2)."""
    clmm_token = TokenMetadata(
        address="TokenCLMMFake11111111111111111111111111111111",
        name="A Meme Coin",
        symbol="MEME",
        decimals=9,
        pool_address="PoolCLMM111111111111111111111111111111111111",
        initial_liquidity_usd=Decimal("110000000.0"),  # $110M
        dex="raydium",
        created_at_ts=1700000000,
        raw_event={
            "pair_data": {
                "liquidity": {"usd": 110000000.0},
            }
        },
    )

    is_ok, reason, _ = validator.evaluate(clmm_token)
    assert not is_ok
    assert reason is not None
    assert "Liquidez excessiva" in reason
    assert "$250,000" in reason


def test_anti_frozen_price_wash_trading(validator: MarketDynamicsValidator) -> None:
    """Valida rejeição de tokens com volume alto e preço congelado / barcode (Print 1)."""
    frozen_token = TokenMetadata(
        address="TokenFrozen111111111111111111111111111111111111",
        name="Frozen Wash",
        symbol="FROZEN",
        decimals=9,
        pool_address="PoolFrozen1111111111111111111111111111111111",
        initial_liquidity_usd=Decimal("50000.0"),
        dex="raydium",
        created_at_ts=1700000000,
        raw_event={
            "age_hours": 3.0,
            "pair_data": {
                "volume": {"h1": 50000.0, "h24": 180000.0},
                "priceChange": {"m5": 0.0, "h1": 0.0, "h24": 0.0},
                "liquidity": {"usd": 50000.0},
                "traders": {"buyers": 20, "sellers": 20, "total": 40},
                "txns": {"m5": {"buys": 10, "sells": 10}},
            }
        },
    )

    is_ok, reason, _ = validator.evaluate(frozen_token)
    assert not is_ok
    assert reason is not None
    assert "Preço congelado / Volume artificial" in reason


def test_anti_wash_trading_few_traders(validator: MarketDynamicsValidator) -> None:
    """Valida rejeição de tokens onde poucas carteiras simulam volume expressivo (Print 1 e 2)."""
    few_traders_token = TokenMetadata(
        address="TokenFewTraders11111111111111111111111111111111",
        name="Few Traders Scam",
        symbol="FEW",
        decimals=9,
        pool_address="PoolFew111111111111111111111111111111111111",
        initial_liquidity_usd=Decimal("40000.0"),
        dex="raydium",
        created_at_ts=1700000000,
        raw_event={
            "age_hours": 2.5,
            "pair_data": {
                "volume": {"h1": 30000.0, "h24": 100000.0},
                "priceChange": {"m5": 1.0, "h1": 2.0, "h24": 5.0},
                "liquidity": {"usd": 40000.0},
                "traders": {"buyers": 3, "sellers": 2, "total": 5},  # Apenas 5 traders
                "txns": {"m5": {"buys": 10, "sells": 5}},
            }
        },
    )

    is_ok, reason, _ = validator.evaluate(few_traders_token)
    assert not is_ok
    assert reason is not None
    assert "Volume artificial: apenas 5 traders únicos" in reason


def test_anti_dev_dump_excessive_sellers_ratio(validator: MarketDynamicsValidator) -> None:
    """Valida rejeição do padrão Nike onde vendedores esmagam compradores (Print 3)."""
    # No caso do Nike: Buyers: 486 vs Sellers: 2479 (razão 5.1x)
    nike_token = TokenMetadata(
        address="ASs3S111111111111111111111111111111111111111pump",
        name="Nike",
        symbol="Nike",
        decimals=6,
        pool_address="PoolNike111111111111111111111111111111111111",
        initial_liquidity_usd=Decimal("6000.0"),
        dex="pumpfun",
        created_at_ts=1700000000,
        raw_event={
            "age_hours": 0.7,
            "pair_data": {
                "volume": {"h1": 60000.0, "h24": 136000.0},
                "priceChange": {"m5": -10.0, "h1": -50.0, "h24": -50.0},
                "liquidity": {"usd": 6000.0},
                "traders": {"buyers": 486, "sellers": 2479, "total": 2486},
                "txns": {"m5": {"buys": 10, "sells": 100}},
            }
        },
    )

    is_ok, reason, _ = validator.evaluate(nike_token)
    assert not is_ok
    assert reason is not None
    assert "Pressão extrema de despejo" in reason
    assert "2479 vendedores para 486 compradores" in reason


def test_anti_liquidity_drain_ratio(validator: MarketDynamicsValidator) -> None:
    """Valida rejeição quando a liquidez restante foi drenada (< 5% do volume negociado) (Print 3)."""
    # No caso do Nike: Volume $136K vs Liquidez $1.7K (razão 1.25%)
    drained_token = TokenMetadata(
        address="TokenDrained1111111111111111111111111111111111",
        name="Drained Token",
        symbol="DRAIN",
        decimals=6,
        pool_address="PoolDrain11111111111111111111111111111111111",
        initial_liquidity_usd=Decimal("6000.0"),
        dex="raydium",
        created_at_ts=1700000000,
        raw_event={
            "age_hours": 1.0,
            "pair_data": {
                "volume": {"h1": 40000.0, "h24": 150000.0},
                "priceChange": {"m5": 0.5, "h1": -10.0, "h24": -20.0},
                "liquidity": {"usd": 5500.0},  # 5.5K / 150K = 3.6% (< 5%)
                "traders": {"buyers": 200, "sellers": 220, "total": 420},
                "txns": {"m5": {"buys": 20, "sells": 15}},
            }
        },
    )

    is_ok, reason, _ = validator.evaluate(drained_token)
    assert not is_ok
    assert reason is not None
    assert "Liquidez drenada" in reason
    assert "4.0%" in reason


def test_anti_parabolic_exhaustion(validator: MarketDynamicsValidator) -> None:
    """Valida bloqueio de compra no topo parabólico prestes a colapsar."""
    exhausted_token = TokenMetadata(
        address="TokenParabolic11111111111111111111111111111111",
        name="Moonshot Top",
        symbol="MOON",
        decimals=6,
        pool_address="PoolMoon111111111111111111111111111111111111",
        initial_liquidity_usd=Decimal("30000.0"),
        dex="raydium",
        created_at_ts=1700000000,
        raw_event={
            "age_hours": 1.2,
            "pair_data": {
                "volume": {"h1": 50000.0, "h24": 80000.0},
                "priceChange": {"m5": -3.5, "h1": 320.0, "h24": 500.0},  # +320% na hora, mas -3.5% em 5m
                "liquidity": {"usd": 30000.0},
                "traders": {"buyers": 150, "sellers": 100, "total": 250},
                "txns": {"m5": {"buys": 10, "sells": 15}},
            }
        },
    )

    is_ok, reason, _ = validator.evaluate(exhausted_token)
    assert not is_ok
    assert reason is not None
    assert "Exaustão parabólica pós-pump" in reason


def test_healthy_token_approved(validator: MarketDynamicsValidator) -> None:
    """Valida que um token saudável e orgânico passa normalmente por todos os gates."""
    healthy_token = TokenMetadata(
        address="TokenHealthy1111111111111111111111111111111111",
        name="Organic Alpha",
        symbol="ALPHA",
        decimals=6,
        pool_address="PoolAlpha11111111111111111111111111111111111",
        initial_liquidity_usd=Decimal("25000.0"),
        dex="raydium",
        created_at_ts=1700000000,
        raw_event={
            "age_hours": 3.0,
            "pair_data": {
                "volume": {"h1": 25000.0, "h24": 60000.0},
                "priceChange": {"m5": 2.5, "h1": 15.0, "h24": 40.0},
                "liquidity": {"usd": 25000.0, "quote": 160.0},
                "quoteToken": {"symbol": "SOL", "address": "So11111111111111111111111111111111111111112"},
                "traders": {"buyers": 180, "sellers": 120, "total": 300},
                "txns": {"m5": {"buys": 25, "sells": 10}},
            }
        },
    )

    is_ok, reason, details = validator.evaluate(healthy_token)
    assert is_ok
    assert reason is None
    assert details["eligible_scalp"] is True
    assert details["eligible_swing"] is True
