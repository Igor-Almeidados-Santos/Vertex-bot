"""
Testes Unitários para Regras Matemáticas de Risco (Break-Even e Trailing Stop).
Garante precisão decimal sem imprecisão de ponto flutuante.
"""

from decimal import Decimal

from src.database.models import ExecutionMode, PositionState, PositionStatus
from src.engine.risk import RiskManager


def test_break_even_exact_math():
    """Testa se a venda de 50% dos tokens ao atingir +100% retorna exatamente o capital inicial."""
    entry_price = Decimal("0.05")
    capital_usd = Decimal("100.0")
    initial_tokens = capital_usd / entry_price  # 2000 tokens

    pos = PositionState(
        token_address="TestToken111",
        mode=ExecutionMode.PAPER,
        entry_price=entry_price,
        initial_token_amount=initial_tokens,
        allocated_capital_usd=capital_usd,
        trailing_drop_pct=Decimal("0.12"),
    )

    risk_manager = RiskManager(
        break_even_gain_pct=Decimal("100.0"),
        trailing_drop_pct=Decimal("0.12"),
    )

    # 1. Preço sobe 50% (não deve disparar break-even)
    price_1_5x = Decimal("0.075")
    decision = risk_manager.evaluate_price_tick(pos, price_1_5x)
    assert decision is None
    assert not pos.break_even_triggered

    # 2. Preço sobe para 2x (+100%) -> Dispara Break-Even
    price_2x = Decimal("0.10")
    decision = risk_manager.evaluate_price_tick(pos, price_2x)
    assert decision is not None
    action, tokens_to_sell = decision
    assert action == "BREAK_EVEN"
    assert tokens_to_sell == Decimal("1000.0")  # 50% de 2000

    # Executa o break-even
    sold = pos.trigger_break_even(price_2x)
    assert sold == Decimal("1000.0")
    assert pos.remaining_token_amount == Decimal("1000.0")
    assert pos.realized_pnl_usd == Decimal("50.0")  # Ganho de $50 na metade vendida
    assert pos.status == PositionStatus.PARTIALLY_CLOSED
    assert pos.break_even_triggered is True


def test_trailing_stop_calculation():
    """Testa se o Trailing Stop acompanha a máxima e dispara no recuo percentual exato."""
    entry_price = Decimal("1.0")
    capital_usd = Decimal("100.0")
    pos = PositionState(
        token_address="TestToken222",
        mode=ExecutionMode.PAPER,
        entry_price=entry_price,
        initial_token_amount=Decimal("100.0"),
        allocated_capital_usd=capital_usd,
        trailing_drop_pct=Decimal("0.12"),  # -12%
    )

    risk_manager = RiskManager(
        break_even_gain_pct=Decimal("100.0"),
        trailing_drop_pct=Decimal("0.12"),
    )

    # Máxima sobe para 1.50 -> Stop sobe para 1.50 * 0.88 = 1.32
    pos.update_price_and_trailing_stop(Decimal("1.50"))
    assert pos.highest_price_seen == Decimal("1.50")
    assert pos.trailing_stop_price == Decimal("1.32")

    # Máxima sobe para 3.00 -> Stop sobe para 3.00 * 0.88 = 2.64
    pos.update_price_and_trailing_stop(Decimal("3.00"))
    assert pos.highest_price_seen == Decimal("3.00")
    assert pos.trailing_stop_price == Decimal("2.64")

    # Preço recua para 2.70 (acima do stop) -> Não aciona
    pos.break_even_triggered = True  # Simula já ter passado pelo BE
    decision = risk_manager.evaluate_price_tick(pos, Decimal("2.70"))
    assert decision is None

    # Preço recua para 2.60 (abaixo do stop 2.64) -> Aciona Trailing Stop!
    decision = risk_manager.evaluate_price_tick(pos, Decimal("2.60"))
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
