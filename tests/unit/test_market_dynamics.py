"""
Testes Unitários para o MarketDynamicsValidator e Gate 7 de Dinâmica de Mercado.
Verifica rejeições por Volume 1h insuficiente, Faca Caindo (5m negativo),
Pressão Vendedora dominante (Buy Ratio < 50%) e Idade Mínima de Proteção.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from src.database.connection import DatabaseManager
from src.database.models import SecurityStatus, TokenMetadata
from src.database.repository import TokensRepository
from src.security.market_dynamics import MarketDynamicsValidator
from src.security.validator import SecurityValidator


def create_token_with_market_data(
    address: str = "TEST_TOKEN_XYZ",
    age_hours: float = 1.0,
    volume_1h: float = 25000.0,
    price_change_5m: float = 3.5,
    buys_5m: int = 40,
    sells_5m: int = 10,
    liquidity_usd: float = 15000.0,
) -> TokenMetadata:
    """Gera um TokenMetadata com metadados sintéticos de par DexScreener."""
    pair_data: dict[str, Any] = {
        "volume": {"h1": volume_1h},
        "priceChange": {"m5": price_change_5m},
        "txns": {"m5": {"buys": buys_5m, "sells": sells_5m}},
        "liquidity": {"usd": liquidity_usd},
    }
    return TokenMetadata(
        address=address,
        chain="solana",
        dex="raydium",
        initial_liquidity_usd=Decimal(str(liquidity_usd)),
        symbol="TEST",
        name="Test Token",
        raw_event={
            "age_hours": age_hours,
            "pair_data": pair_data,
        },
    )


def test_market_dynamics_low_volume_rejected():
    """Token com volume de 1h de $2.000 (abaixo de $15.000) deve ser reprovado."""
    validator = MarketDynamicsValidator(min_volume_1h_usd=Decimal("15000.0"))
    token = create_token_with_market_data(volume_1h=2000.0)

    is_safe, reason, details = validator.evaluate(token, strategy_mode="DUAL")
    assert is_safe is False
    assert reason is not None
    assert "Volume 1h" in reason
    assert "insuficiente" in reason


def test_market_dynamics_falling_knife_rejected():
    """Token com queda de -8% nos últimos 5m (faca caindo) deve ser reprovado."""
    validator = MarketDynamicsValidator(min_price_change_5m_pct=Decimal("-2.0"))
    token = create_token_with_market_data(price_change_5m=-8.0)

    is_safe, reason, details = validator.evaluate(token, strategy_mode="DUAL")
    assert is_safe is False
    assert reason is not None
    assert "Faca caindo detectada" in reason


def test_market_dynamics_dominant_selling_rejected():
    """Token com mais vendas que compras (ex: 5 compras e 25 vendas = 16.7%) deve ser reprovado."""
    validator = MarketDynamicsValidator(min_buy_ratio_5m_pct=Decimal("50.0"))
    token = create_token_with_market_data(buys_5m=5, sells_5m=25)

    is_safe, reason, details = validator.evaluate(token, strategy_mode="DUAL")
    assert is_safe is False
    assert reason is not None
    assert "Pressão vendedora dominante" in reason


def test_market_dynamics_ultra_young_token_rejected():
    """Token com 12 minutos de existência (< 30 min) deve ser reprovado por risco de sniper dump."""
    validator = MarketDynamicsValidator(min_age_hours_scalp=0.5)
    token = create_token_with_market_data(age_hours=0.2)  # 12 min

    is_safe, reason, details = validator.evaluate(token, strategy_mode="SCALP_ONLY")
    assert is_safe is False
    assert reason is not None
    assert "sniper dump" in reason


def test_market_dynamics_scalp_eligibility():
    """Token com 45m de existência, volume forte e compras em alta deve ser aprovado para SCALP."""
    validator = MarketDynamicsValidator(
        min_age_hours_scalp=0.5,
        max_age_hours_scalp=4.0,
        min_age_hours_swing=2.0,
        min_liquidity_swing_usd=Decimal("20000.0"),
    )
    token = create_token_with_market_data(
        age_hours=0.75,  # 45 min
        volume_1h=30000.0,
        price_change_5m=4.0,
        buys_5m=50,
        sells_5m=10,
        liquidity_usd=12000.0,
    )

    is_safe, reason, details = validator.evaluate(token, strategy_mode="DUAL")
    assert is_safe is True
    assert reason is None
    assert details["eligible_scalp"] is True
    assert details["eligible_swing"] is False  # Não tem 2h ainda nem $20k liq


def test_market_dynamics_swing_and_scalp_eligibility():
    """Token com 3h de existência e liquidez de $35k deve ser aprovado para AMBOS (Dual-Track)."""
    validator = MarketDynamicsValidator(
        min_age_hours_scalp=0.5,
        max_age_hours_scalp=4.0,
        min_age_hours_swing=2.0,
        max_age_hours_swing=48.0,
        min_liquidity_swing_usd=Decimal("20000.0"),
    )
    token = create_token_with_market_data(
        age_hours=3.0,
        volume_1h=50000.0,
        price_change_5m=2.0,
        buys_5m=80,
        sells_5m=20,
        liquidity_usd=35000.0,
    )

    is_safe, reason, details = validator.evaluate(token, strategy_mode="DUAL")
    assert is_safe is True
    assert reason is None
    assert details["eligible_scalp"] is True
    assert details["eligible_swing"] is True


@pytest.mark.asyncio
async def test_security_validator_gate7_integration(tmp_path: Path):
    """Verifica se o SecurityValidator aciona o Gate 7 e persiste detalhes e rejeição corretamente."""
    db_path = str(tmp_path / "test_gate7.db")
    db = DatabaseManager(db_path)
    await db.initialize()
    tokens_repo = TokensRepository(db)

    market_validator = MarketDynamicsValidator(min_volume_1h_usd=Decimal("15000.0"))
    sec_validator = SecurityValidator(
        tokens_repo=tokens_repo,
        min_liquidity_usd=Decimal("5000.0"),
        market_validator=market_validator,
        strategy_mode="DUAL",
    )

    # Token com contrato perfeito mas volume seco ($1.500)
    token = create_token_with_market_data(
        address="LOW_VOL_SAFE_CONTRACT",
        volume_1h=1500.0,
    )
    await tokens_repo.save_detected_token(token)

    # Mock de contrato seguro
    audit = await sec_validator.audit_token(
        token,
        mock_overrides={
            "is_mint_revoked": True,
            "is_freeze_revoked": True,
            "lp_burn_pct": 100.0,
            "top10_pct": 10.0,
            "taxes": (0.0, 0.0, False),
            "market_dynamics": None,  # Permite que o MarketDynamicsValidator real avalie!
        },
    )

    assert audit.status == SecurityStatus.REJECTED
    assert audit.rejection_reason is not None
    assert "Volume 1h" in audit.rejection_reason

    # Verifica persistência no SQLite
    saved = await tokens_repo.get_by_address(token.address)
    assert saved is not None
    assert saved["security_status"] == SecurityStatus.REJECTED.value
    assert "Volume 1h" in saved["rejection_reason"]

    await db.close()

