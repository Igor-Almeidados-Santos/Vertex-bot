"""
Gerenciador Dinâmico de Risco do Vertex-bot (Camada de Saída Automatizada).
Implementa regras matemáticas estritas de Break-Even e Trailing Stop contínuo.
"""

from decimal import Decimal

from src.database.models import PositionState
from src.utils.logger import setup_logger

logger = setup_logger("vertex.engine.risk")


class RiskManager:
    """Aplica regras quantitativas de saída e preservação de capital a cada tick de preço (Scalp e Swing Ratchet)."""

    def __init__(
        self,
        break_even_gain_pct: Decimal = Decimal("100.0"),  # +100% (2x) para SCALP
        trailing_drop_pct: Decimal = Decimal("0.12"),     # -12% de recuo para SCALP
        emergency_stop_loss_pct: Decimal = Decimal("0.20"),  # -20% stop loss inicial para SCALP
        swing_initial_stop_loss_pct: Decimal = Decimal("0.20"),  # -20% stop loss inicial para SWING
        swing_tier1_mult: Decimal = Decimal("2.0"),   # Degrau 1: 2x (piso = 1.0x entrada / Break-Even)
        swing_tier2_mult: Decimal = Decimal("3.0"),   # Degrau 2: 3x (piso = 2.0x / +100% travado)
        swing_tier3_mult: Decimal = Decimal("5.0"),   # Degrau 3: 5x (piso = 3.0x / +200% travado)
        swing_tier4_mult: Decimal = Decimal("10.0"),  # Degrau 4: 10x (piso = 5.0x / +400% travado)
        swing_trailing_drop_pct: Decimal = Decimal("0.25"),  # Trailing stop elástico de apoio no SWING (-25%)
    ) -> None:
        self.break_even_multiplier: Decimal = Decimal("1.0") + (break_even_gain_pct / Decimal("100.0"))
        self.trailing_drop_pct: Decimal = trailing_drop_pct
        self.emergency_stop_multiplier: Decimal = Decimal("1.0") - emergency_stop_loss_pct

        # Parâmetros de Swing Ratchet
        self.swing_stop_multiplier: Decimal = Decimal("1.0") - swing_initial_stop_loss_pct
        self.swing_tier1_mult: Decimal = swing_tier1_mult
        self.swing_tier2_mult: Decimal = swing_tier2_mult
        self.swing_tier3_mult: Decimal = swing_tier3_mult
        self.swing_tier4_mult: Decimal = swing_tier4_mult
        self.swing_trailing_drop_pct: Decimal = swing_trailing_drop_pct

    def evaluate_price_tick(
        self,
        position: PositionState,
        current_price: Decimal,
    ) -> tuple[str, Decimal] | None:
        """
        Avalia o preço atual contra o estado da posição (SCALP ou SWING RATCHET).
        Retorna (motivo_saida, quantidade_tokens_a_vender) se houver gatilho de saída; caso contrário, None.
        """
        if getattr(position, "strategy_type", "SCALP") == "SWING":
            return self._evaluate_swing_tick(position, current_price)
        return self._evaluate_scalp_tick(position, current_price)

    def _evaluate_swing_tick(
        self,
        position: PositionState,
        current_price: Decimal,
    ) -> tuple[str, Decimal] | None:
        """Avaliação de risco para a vertente SWING RATCHET (Catraca de Degraus)."""
        # 1. Atualiza a máxima histórica vista
        if current_price > position.highest_price_seen:
            position.highest_price_seen = current_price

        # 2. Avalia degraus da catraca (Ratchet Tiers) com base na máxima registrada
        entry = position.entry_price
        target_tier = 0
        new_floor = Decimal("0.0")

        if position.highest_price_seen >= entry * self.swing_tier4_mult:
            target_tier = 4
            new_floor = entry * self.swing_tier3_mult
        elif position.highest_price_seen >= entry * self.swing_tier3_mult:
            target_tier = 3
            new_floor = entry * self.swing_tier2_mult
        elif position.highest_price_seen >= entry * self.swing_tier2_mult:
            target_tier = 2
            new_floor = entry * self.swing_tier1_mult
        elif position.highest_price_seen >= entry * self.swing_tier1_mult:
            target_tier = 1
            new_floor = entry

        # Promove o degrau caso um novo patamar tenha sido conquistado
        if target_tier > position.ratchet_tier:
            position.promote_ratchet_tier(target_tier, new_floor)
            logger.info(
                "🌊 [SWING RATCHET DEGRAU #%d] Token %s | Máxima $%.6f | Piso Garantido Elevado para: $%.6f!",
                target_tier,
                position.token_address,
                position.highest_price_seen,
                new_floor,
                extra={"event": "SWING_RATCHET_TIER_PROMOTED", "token_address": position.token_address},
            )

        # 3. Atualiza linha de stop visível
        if position.ratchet_tier >= 1:
            position.trailing_stop_price = position.ratchet_floor_price
            if current_price <= position.ratchet_floor_price:
                logger.info(
                    "🛑 [SWING RATCHET STOP ACIONADO] Token %s | Cotação $%.6f <= Piso Degrau #%d ($%.6f) | Realizando Lucro Travado!",
                    position.token_address,
                    current_price,
                    position.ratchet_tier,
                    position.ratchet_floor_price,
                    extra={"event": "SWING_RATCHET_STOP_TRIGGERED", "token_address": position.token_address},
                )
                return "SWING_RATCHET_STOP", position.remaining_token_amount
        else:
            # Degrau 0: Antes do Degrau 1, aplica Stop Loss de proteção
            stop_loss_price = entry * self.swing_stop_multiplier
            position.trailing_stop_price = stop_loss_price
            if current_price <= stop_loss_price:
                logger.warning(
                    "🛑 [SWING STOP LOSS INICIAL DISPARADO] Token %s | Preço Atual: $%.6f <= Stop: $%.6f",
                    position.token_address,
                    current_price,
                    stop_loss_price,
                    extra={"event": "EMERGENCY_STOP_TRIGGERED", "token_address": position.token_address},
                )
                return "EMERGENCY_STOP", position.remaining_token_amount

        return None

    def _evaluate_scalp_tick(
        self,
        position: PositionState,
        current_price: Decimal,
    ) -> tuple[str, Decimal] | None:
        """Avaliação de risco para a vertente SCALP (Break-Even 50% + Trailing Stop contínuo)."""
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

        # 2. Sincronização dinâmica da taxa de Trailing Stop e recálculo de linha
        position.trailing_drop_pct = self.trailing_drop_pct
        position.update_price_and_trailing_stop(current_price)
        if position.highest_price_seen > Decimal("0.0"):
            dynamic_stop = position.highest_price_seen * (Decimal("1.0") - self.trailing_drop_pct)
            if dynamic_stop > position.trailing_stop_price:
                position.trailing_stop_price = dynamic_stop

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
