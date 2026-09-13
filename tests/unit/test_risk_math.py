"""
Testes Unitários para Regras Matemáticas de Risco (Break-Even e Trailing Stop).
Garante precisão decimal sem imprecisão de ponto flutuante.
"""

from decimal import Decimal

from src.database.models import ExecutionMode, PositionState, PositionStatus
from src.engine.risk import RiskManager


def test_scalp_target_exact_math():
    """Testa se o alvo de +100% no SCALP encerra 100% dos tokens conforme especificado."""
    entry_price = Decimal("0.05")
    capital_usd = Decimal("100.0")
    initial_tokens = capital_usd / entry_price  # 2000 tokens

    pos = PositionState(
        token_address="TestToken111",
        mode=ExecutionMode.PAPER,
        strategy_type="SCALP",
        entry_price=entry_price,
        initial_token_amount=initial_tokens,
        allocated_capital_usd=capital_usd,
        trailing_drop_pct=Decimal("0.12"),
    )

    risk_manager = RiskManager(
        scalp_target_gain_pct=Decimal("100.0"),  # +100% -> 2x
        trailing_drop_pct=Decimal("0.12"),
    )

    # 1. Preço sobe 50% (não deve disparar alvo)
    price_1_5x = Decimal("0.075")
    decision = risk_manager.evaluate_price_tick(pos, price_1_5x)
    assert decision is None

    # 2. Preço sobe para 2x (+100%) -> Dispara SCALP_TARGET_REACHED com 100% de venda!
    price_2x = Decimal("0.10")
    decision = risk_manager.evaluate_price_tick(pos, price_2x)
    assert decision is not None
    action, tokens_to_sell = decision
    assert action == "SCALP_TARGET_REACHED"
    assert tokens_to_sell == Decimal("2000.0")  # 100% de 2000 tokens


def test_trailing_stop_calculation():
    """Testa se o Trailing Stop acompanha a máxima e dispara no recuo percentual exato."""
    entry_price = Decimal("1.0")
    capital_usd = Decimal("100.0")
    pos = PositionState(
        token_address="TestToken222",
        mode=ExecutionMode.PAPER,
        strategy_type="SCALP",
        entry_price=entry_price,
        initial_token_amount=Decimal("100.0"),
        allocated_capital_usd=capital_usd,
        trailing_drop_pct=Decimal("0.12"),  # -12%
    )

    # Alvo em +200% para testar oscilação de trailing stop até 1.80 sem antecipar o alvo
    risk_manager = RiskManager(
        scalp_target_gain_pct=Decimal("200.0"),
        trailing_drop_pct=Decimal("0.12"),
    )

    # Máxima sobe para 1.50 -> Stop sobe para 1.50 * 0.88 = 1.32
    pos.update_price_and_trailing_stop(Decimal("1.50"))
    assert pos.highest_price_seen == Decimal("1.50")
    assert pos.trailing_stop_price == Decimal("1.32")

    # Preço sobe para 1.80 -> Stop sobe para 1.80 * 0.88 = 1.584
    pos.update_price_and_trailing_stop(Decimal("1.80"))
    assert pos.highest_price_seen == Decimal("1.80")
    assert pos.trailing_stop_price == Decimal("1.584")

    # Preço recua para 1.65 (acima do stop 1.584) -> Não aciona
    decision = risk_manager.evaluate_price_tick(pos, Decimal("1.65"))
    assert decision is None

    # Preço recua para 1.50 (abaixo do stop 1.584) -> Aciona Trailing Stop!
    decision = risk_manager.evaluate_price_tick(pos, Decimal("1.50"))
    assert decision is not None
    action, amount = decision
    assert action == "TRAILING_STOP"
    assert amount == pos.remaining_token_amount


def test_emergency_stop_loss():
    """Testa o Stop Loss de proteção se o token cair 20% sem atingir o break-even."""
    entry_price = Decimal("1.0")
    pos = PositionState(
        token_address="TestToken333",
        mode=ExecutionMode.PAPER,
        entry_price=entry_price,
        initial_token_amount=Decimal("100.0"),
        allocated_capital_usd=Decimal("100.0"),
    )

    risk_manager = RiskManager(emergency_stop_loss_pct=Decimal("0.20"))

    # Queda para 0.85 (-15%) -> Não dispara
    decision = risk_manager.evaluate_price_tick(pos, Decimal("0.85"))
    assert decision is None

    # Queda para 0.79 (-21%) -> Dispara Emergency Stop!
    decision = risk_manager.evaluate_price_tick(pos, Decimal("0.79"))
    assert decision is not None
    action, amount = decision
    assert action == "EMERGENCY_STOP"
    assert amount == Decimal("100.0")


def test_scalp_timeout():
    """Testa se a posição SCALP que atinge a janela limite de 1h encerra a mercado."""
    from datetime import UTC, datetime, timedelta

    entry_price = Decimal("1.0")
    pos = PositionState(
        token_address="ScalpTimeoutToken",
        mode=ExecutionMode.PAPER,
        strategy_type="SCALP",
        entry_price=entry_price,
        initial_token_amount=Decimal("100.0"),
        allocated_capital_usd=Decimal("100.0"),
        opened_at=datetime.now(UTC) - timedelta(minutes=65),  # 65 min aberta
    )

    risk_manager = RiskManager(scalp_max_hold_seconds=3600.0)  # 60 min limite
    decision = risk_manager.evaluate_price_tick(pos, Decimal("1.10"))
    assert decision is not None
    action, amount = decision
    assert action == "SCALP_TIMEOUT"
    assert amount == Decimal("100.0")


def test_swing_timeout():
    """Testa se a posição SWING que ultrapassa 24h aberta encerra a mercado."""
    from datetime import UTC, datetime, timedelta

    entry_price = Decimal("1.0")
    pos = PositionState(
        token_address="SwingTimeoutToken",
        mode=ExecutionMode.PAPER,
        strategy_type="SWING",
        entry_price=entry_price,
        initial_token_amount=Decimal("100.0"),
        allocated_capital_usd=Decimal("100.0"),
        opened_at=datetime.now(UTC) - timedelta(hours=25),  # 25h aberta
    )

    risk_manager = RiskManager(swing_max_hold_seconds=86400.0)  # 24h limite
    decision = risk_manager.evaluate_price_tick(pos, Decimal("1.50"))
    assert decision is not None
    action, amount = decision
    assert action == "SWING_TIMEOUT"
    assert amount == Decimal("100.0")


def test_swing_target_gain_2000_pct():
    """Testa se o alvo mestre do SWING de +2.000% (21x) encerra 100% da posição com lucro extremo."""
    entry_price = Decimal("1.0")
    pos = PositionState(
        token_address="SwingMoonbagToken",
        mode=ExecutionMode.PAPER,
        strategy_type="SWING",
        entry_price=entry_price,
        initial_token_amount=Decimal("100.0"),
        allocated_capital_usd=Decimal("100.0"),
    )

    risk_manager = RiskManager(
        swing_target_gain_pct=Decimal("2000.0"),  # 21x
    )

    # 1. Preço sobe para 15x (+1400%) -> Não atinge alvo mestre
    decision = risk_manager.evaluate_price_tick(pos, Decimal("15.0"))
    assert decision is None

    # 2. Preço sobe para 21.5x (> +2000%) -> Dispara SWING_TARGET_REACHED!
    decision = risk_manager.evaluate_price_tick(pos, Decimal("21.50"))
    assert decision is not None
    action, amount = decision
    assert action == "SWING_TARGET_REACHED"
    assert amount == Decimal("100.0")


def test_swing_hourly_drop_check():
    """Testa a checagem de saúde horária do SWING: recuo > 15% na hora fecha a posição."""
    from datetime import UTC, datetime, timedelta

    entry_price = Decimal("1.0")
    pos = PositionState(
        token_address="SwingHourlyToken",
        mode=ExecutionMode.PAPER,
        strategy_type="SWING",
        entry_price=entry_price,
        initial_token_amount=Decimal("100.0"),
        allocated_capital_usd=Decimal("100.0"),
        opened_at=datetime.now(UTC) - timedelta(hours=1, minutes=5),  # 1 hora e 5 min aberta
        hourly_peak_price=Decimal("2.0"),  # Pico na hora foi 2.00
        last_hourly_eval_hour=0,
    )

    risk_manager = RiskManager(
        swing_max_hourly_drop_pct=Decimal("15.0"),  # Queda máx 15%
    )

    # Preço cai de 2.0 para 1.60 (recuo de 20%, > 15% da máxima da hora) -> Dispara SWING_HOURLY_DROP!
    decision = risk_manager.evaluate_price_tick(pos, Decimal("1.60"))
    assert decision is not None
    action, amount = decision
    assert action == "SWING_HOURLY_DROP"
    assert amount == Decimal("100.0")

