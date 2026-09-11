"""
Gerenciador Dinâmico de Risco do Vertex-bot (Camada de Saída Automatizada).
Implementa regras matemáticas estritas de Break-Even e Trailing Stop contínuo.
"""

from decimal import Decimal

from src.database.models import PositionState
from src.utils.logger import setup_logger

logger = setup_logger("vertex.engine.risk")


class RiskManager:
    """Aplica regras quantitativas de saída e preservação de capital a cada tick de preço."""

    def __init__(
        self,
        break_even_gain_pct: Decimal = Decimal("100.0"),  # +100% (2x)
        trailing_drop_pct: Decimal = Decimal("0.12"),     # -12% de recuo da máxima
        emergency_stop_loss_pct: Decimal = Decimal("0.20"),  # -20% de perda máxima inicial
    ) -> None:
        self.break_even_multiplier: Decimal = Decimal("1.0") + (break_even_gain_pct / Decimal("100.0"))
        self.trailing_drop_pct: Decimal = trailing_drop_pct
        self.emergency_stop_multiplier: Decimal = Decimal("1.0") - emergency_stop_loss_pct

    def evaluate_price_tick(
        self,
        position: PositionState,
        current_price: Decimal,
    ) -> tuple[str, Decimal] | None:
        """
        Avalia o preço atual contra o estado da posição.
        Retorna (motivo_saida, quantidade_tokens_a_vender) se houver gatilho de saída; caso contrário, None.
        """
        # 1. Checagem de Stop Loss Emergencial (quando o ativo ainda não atingiu Break-Even nem registrou ganhos)
        if not position.break_even_triggered:
            stop_loss_price = position.entry_price * self.emergency_stop_multiplier
            if current_price <= stop_loss_price:
                logger.warning(
                    "STOP LOSS EMERGENCIAL DISPARADO! Token %s | Preço Atual: $%.6f <= Stop: $%.6f",
                    position.token_address,
                    current_price,
                    stop_loss_price,
                    extra={"event": "EMERGENCY_STOP_TRIGGERED", "token_address": position.token_address},
                )
                return "EMERGENCY_STOP", position.remaining_token_amount

        # 2. Atualização de Máxima Histórica e Linha de Trailing Stop
        position.update_price_and_trailing_stop(current_price)

        # 3. Checagem de Break-Even (+100% / 2x do preço de entrada)
        break_even_target = position.entry_price * self.break_even_multiplier
        if not position.break_even_triggered and current_price >= break_even_target:
            tokens_to_sell = position.initial_token_amount / Decimal("2.0")
            logger.info(
                "GATILHO DE BREAK-EVEN ATINGIDO (+%.0f%%)! Token: %s | Vendendo 50%% da posição",
                (self.break_even_multiplier - Decimal("1.0")) * Decimal("100.0"),
                position.token_address,
                extra={"event": "BREAK_EVEN_TRIGGERED", "token_address": position.token_address},
            )
            return "BREAK_EVEN", tokens_to_sell

        # 4. Avaliação de Trailing Stop Contínuo
        # O Trailing Stop é ativado após o Break-Even ou quando a máxima histórica ultrapassa o preço de entrada
        is_trailing_active = position.break_even_triggered or (position.highest_price_seen > position.entry_price)
        if is_trailing_active and current_price <= position.trailing_stop_price:
            logger.info(
                "TRAILING STOP ACIONADO! Token %s | Preço Atual: $%.6f <= Linha de Stop: $%.6f (Máxima: $%.6f)",
                position.token_address,
                current_price,
                position.trailing_stop_price,
                position.highest_price_seen,
                extra={"event": "TRAILING_STOP_TRIGGERED", "token_address": position.token_address},
            )
            return "TRAILING_STOP", position.remaining_token_amount

        return None
