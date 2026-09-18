"""
Testes Unitários do Motor de Reentrada Inteligente (Anti-Falling-Knife).
"""

from decimal import Decimal

from src.engine.reentry import ReentryRiskManager


def test_first_entry_allowed() -> None:
    """Token sem histórico de saída deve ser liberado imediatamente."""
    manager = ReentryRiskManager()
    allowed, reason = manager.can_reenter(
        token_address="NEW_TOKEN_123",
        current_price=Decimal("1.00"),
        current_liquidity_usd=Decimal("10000.0"),
    )
    assert allowed is True
    assert "Primeira entrada" in reason


def test_cooloff_trailing_stop() -> None:
    """Verifica tempo mínimo de descanso de 5 min (300s) após Trailing Stop."""
    manager = ReentryRiskManager(trailing_cooloff_sec=300.0)
    base_time = 1000.0
    manager.record_exit(
        token_address="TOKEN_TRAIL",
        exit_price=Decimal("2.00"),
        exit_reason="TRAILING_STOP",
        timestamp=base_time,
    )

    # 100 segundos depois -> deve ser rejeitado por cool-off
    allowed, reason = manager.can_reenter(
        token_address="TOKEN_TRAIL",
        current_price=Decimal("1.90"),
        current_liquidity_usd=Decimal("15000.0"),
        now=base_time + 100.0,
    )
    assert allowed is False
    assert "descanso pós-TRAILING_STOP" in reason

    # 350 segundos depois -> cool-off expirado, sem queda severa -> aprovado
    allowed, reason = manager.can_reenter(
        token_address="TOKEN_TRAIL",
        current_price=Decimal("1.90"),
        current_liquidity_usd=Decimal("15000.0"),
        now=base_time + 350.0,
    )
    assert allowed is True
    assert "Tempo de cool-off superado" in reason


def test_cooloff_emergency_stop_loss() -> None:
    """Verifica descanso estendido de 30 min (1800s) após Stop-Loss de emergência."""
    manager = ReentryRiskManager(stoploss_cooloff_sec=1800.0)
    base_time = 1000.0
    manager.record_exit(
        token_address="TOKEN_STOP",
        exit_price=Decimal("0.80"),
        exit_reason="EMERGENCY_STOP",
        timestamp=base_time,
    )

    # 1000 segundos depois (< 1800s) -> ainda em quarentena
    allowed, reason = manager.can_reenter(
        token_address="TOKEN_STOP",
        current_price=Decimal("0.85"),
        current_liquidity_usd=Decimal("10000.0"),
        now=base_time + 1000.0,
    )
    assert allowed is False
    assert "descanso pós-EMERGENCY_STOP" in reason

    # 1850 segundos depois -> liberado se métricas estiverem estáveis
    allowed, reason = manager.can_reenter(
        token_address="TOKEN_STOP",
        current_price=Decimal("0.85"),
        current_liquidity_usd=Decimal("10000.0"),
        now=base_time + 1850.0,
    )
    assert allowed is True


def test_anti_falling_knife_detection() -> None:
    """Rejeita compra se o token caiu > 25% do exit e não repicou pelo menos 3%."""
    manager = ReentryRiskManager(
        trailing_cooloff_sec=100.0,
        min_bounce_pct=Decimal("3.0"),
        max_post_exit_drop_pct=Decimal("25.0"),
    )
    base_time = 1000.0
    manager.record_exit(
        token_address="FALLING_TOKEN",
        exit_price=Decimal("10.00"),
        exit_reason="TRAILING_STOP",
        timestamp=base_time,
    )

    # Token cai de $10.00 para $7.00 (-30% queda). lowest_price_seen = $7.00.
    # Preço atual = $7.05 (+0.7% de repique a partir de $7.00) -> deve rejeitar!
    allowed, reason = manager.can_reenter(
        token_address="FALLING_TOKEN",
        current_price=Decimal("7.05"),
        current_liquidity_usd=Decimal("20000.0"),
        now=base_time + 200.0,
    )
    assert allowed is False
    assert "queda livre" in reason

    # Agora o token repica de $7.00 para $7.35 (+5% acima da mínima de $7.00) -> deve aprovar!
    allowed, reason = manager.can_reenter(
        token_address="FALLING_TOKEN",
        current_price=Decimal("7.35"),
        current_liquidity_usd=Decimal("20000.0"),
        now=base_time + 210.0,
    )
    assert allowed is True
    assert "Repique de suporte confirmado" in reason


def test_liquidity_gate() -> None:
    """Rejeita reentrada se liquidez caiu abaixo do mínimo seguro ($5.000)."""
    manager = ReentryRiskManager(min_liquidity_usd=Decimal("5000.0"), trailing_cooloff_sec=60.0)
    base_time = 1000.0
    manager.record_exit(
        token_address="LOW_LIQ_TOKEN",
        exit_price=Decimal("1.00"),
        exit_reason="TRAILING_STOP",
        timestamp=base_time,
    )

    allowed, reason = manager.can_reenter(
        token_address="LOW_LIQ_TOKEN",
        current_price=Decimal("1.05"),
        current_liquidity_usd=Decimal("3500.0"),
        now=base_time + 120.0,
    )
    assert allowed is False
    assert "Liquidez atual" in reason


def test_update_config_and_clear_history() -> None:
    """Testa atualização dinâmica de parâmetros e limpeza de histórico."""
    manager = ReentryRiskManager(trailing_cooloff_sec=300.0)
    manager.record_exit(
        token_address="TEST_TOKEN",
        exit_price=Decimal("1.00"),
        exit_reason="TRAILING_STOP",
    )
    assert len(manager._exit_records) == 1

    manager.update_config(trailing_cooloff_sec=10.0, min_bounce_pct=Decimal("5.0"))
    assert manager.trailing_cooloff_sec == 10.0
    assert manager.min_bounce_pct == Decimal("5.0")

    manager.clear_history()
    assert len(manager._exit_records) == 0


def test_immediate_reentry_for_winner() -> None:
    """Trades vencedores (PnL > 0) devem ter reentrada IMEDIATA sem sofrer cool-off temporal."""
    manager = ReentryRiskManager(trailing_cooloff_sec=300.0)
    base_time = 1000.0
    manager.record_exit(
        token_address="WINNING_TOKEN",
        exit_price=Decimal("2.50"),
        exit_reason="TRAILING_STOP",
        realized_pnl=Decimal("1.50"),
        is_winner=True,
        timestamp=base_time,
    )

    # Apenas 5 segundos depois -> reentrada deve ser APROVADA imediatamente!
    allowed, reason = manager.can_reenter(
        token_address="WINNING_TOKEN",
        current_price=Decimal("2.48"),
        current_liquidity_usd=Decimal("25000.0"),
        now=base_time + 5.0,
    )
    assert allowed is True
    assert "Reentrada imediata liberada" in reason


def test_can_scale_in_pyramiding() -> None:
    """Verifica autorização de piramidação (scale-in) apenas para posições lucrativas."""
    from unittest.mock import MagicMock

    manager = ReentryRiskManager()

    # 1. Posição no prejuízo -> piramidação rejeitada
    losing_pos = MagicMock()
    losing_pos.roi_pct = Decimal("-2.5")
    losing_pos.break_even_triggered = False

    allowed, reason = manager.can_scale_in(
        token_address="TOKEN_SCALE",
        active_positions_for_token=[losing_pos],
        max_positions_per_token=2,
        min_profit_pct=Decimal("5.0"),
    )
    assert allowed is False
    assert "lucro mínimo para piramidação" in reason

    # 2. Posição no lucro (+8.0%) -> piramidação autorizada
    winning_pos = MagicMock()
    winning_pos.roi_pct = Decimal("8.0")
    winning_pos.break_even_triggered = True

    allowed, reason = manager.can_scale_in(
        token_address="TOKEN_SCALE",
        active_positions_for_token=[winning_pos],
        max_positions_per_token=2,
        min_profit_pct=Decimal("5.0"),
    )
    assert allowed is True
    assert "Piramidação autorizada" in reason

    # 3. Limite de posições atingido (2/2) -> rejeitado
    allowed, reason = manager.can_scale_in(
        token_address="TOKEN_SCALE",
        active_positions_for_token=[winning_pos, winning_pos],
        max_positions_per_token=2,
        min_profit_pct=Decimal("5.0"),
    )
    assert allowed is False
    assert "Limite máximo de posições" in reason


def test_can_promote_to_swing() -> None:
    """Verifica promoção automática de SCALP lucrativo para perna de SWING."""
    from unittest.mock import MagicMock

    manager = ReentryRiskManager()
    scalp_pos = MagicMock()
    scalp_pos.roi_pct = Decimal("12.5")
    scalp_pos.break_even_triggered = True

    # Sucesso na promoção
    allowed, reason = manager.can_promote_to_swing(
        scalp_pos=scalp_pos,
        token_age_hours=4.5,
        liquidity_usd=Decimal("35000.0"),
        min_age_swing=3.0,
        max_age_swing=6.0,
        min_liquidity_swing=Decimal("20000.0"),
        swing_slots_available=True,
    )
    assert allowed is True
    assert "alta performance em Scalp" in reason

    # Falha: Sem slots de Swing
    allowed, reason = manager.can_promote_to_swing(
        scalp_pos=scalp_pos,
        token_age_hours=4.5,
        liquidity_usd=Decimal("35000.0"),
        swing_slots_available=False,
    )
    assert allowed is False
    assert "Sem slots de Swing disponíveis" in reason

    # Falha: Liquidez insuficiente para Swing (< $20k)
    allowed, reason = manager.can_promote_to_swing(
        scalp_pos=scalp_pos,
        token_age_hours=4.5,
        liquidity_usd=Decimal("12000.0"),
        swing_slots_available=True,
    )
    assert allowed is False
    assert "Liquidez atual" in reason
