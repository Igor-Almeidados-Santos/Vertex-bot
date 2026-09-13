"""
Gerenciador Dinâmico de Risco do Vertex-bot (Camada de Saída Automatizada).
Implementa regras quantitativas de:
1. SCALP: Janela temporal de até 1 hora, Alvo de saída com 100% de venda (+100%) e Trailing Stop contínuo.
2. SWING: Janela temporal de até 24 horas, Alvo de até +2.000%, Catraca de degraus (Ratchet Floors)
   e Avaliação Horária de Quedas (Hourly Health Check).
"""

from decimal import Decimal

from src.database.models import PositionState
from src.utils.logger import setup_logger

logger = setup_logger("vertex.engine.risk")


class RiskManager:
    """Aplica regras quantitativas de saída e preservação de capital a cada tick de preço."""

    def __init__(
        self,
        scalp_max_hold_seconds: float = 3600.0,            # 1 hora
        scalp_target_gain_pct: Decimal = Decimal("100.0"),  # +100% (2x) -> Venda de 100%
        swing_max_hold_seconds: float = 86400.0,           # 24 horas
        swing_target_gain_pct: Decimal = Decimal("2000.0"),# +2.000% (21x)
        swing_max_hourly_drop_pct: Decimal = Decimal("15.0"), # Queda máxima na hora
        trailing_drop_pct: Decimal = Decimal("0.12"),       # -12% de recuo para SCALP
        emergency_stop_loss_pct: Decimal = Decimal("0.20"),    # -20% stop loss inicial
        swing_initial_stop_loss_pct: Decimal = Decimal("0.0"), # Stop inicial SWING
        swing_tier1_mult: Decimal = Decimal("2.0"),         # +100% (2x): Piso na Entrada ($x / BE)
        swing_tier2_mult: Decimal = Decimal("4.0"),         # +300% (4x): Piso em 2x (+100% travado)
        swing_tier3_mult: Decimal = Decimal("6.0"),         # +500% (6x): Piso em 4x (+300% travado)
        swing_tier4_mult: Decimal = Decimal("11.0"),        # +1.000% (11x): Piso em 6x (+500% travado)
        swing_tier5_mult: Decimal = Decimal("21.0"),        # +2.000% (21x): Alvo mestre
        swing_trailing_drop_pct: Decimal = Decimal("0.25"), # Trailing stop elástico
        break_even_gain_pct: Decimal = Decimal("100.0"),    # Compatibilidade
    ) -> None:
        self.scalp_max_hold_seconds: float = scalp_max_hold_seconds
        self.scalp_target_gain_pct: Decimal = scalp_target_gain_pct
        self.scalp_target_multiplier: Decimal = Decimal("1.0") + (scalp_target_gain_pct / Decimal("100.0"))

        self.swing_max_hold_seconds: float = swing_max_hold_seconds
        self.swing_target_gain_pct: Decimal = swing_target_gain_pct
        self.swing_target_multiplier: Decimal = Decimal("1.0") + (swing_target_gain_pct / Decimal("100.0"))
        self.swing_max_hourly_drop_pct: Decimal = swing_max_hourly_drop_pct

        self.trailing_drop_pct: Decimal = trailing_drop_pct
        self.emergency_stop_multiplier: Decimal = Decimal("1.0") - emergency_stop_loss_pct

        if Decimal("0.0") < swing_initial_stop_loss_pct < Decimal("1.0"):
            self.swing_stop_multiplier: Decimal = Decimal("1.0") - swing_initial_stop_loss_pct
        else:
            self.swing_stop_multiplier = Decimal("0.0")

        self.swing_tier1_mult: Decimal = swing_tier1_mult
        self.swing_tier2_mult: Decimal = swing_tier2_mult
        self.swing_tier3_mult: Decimal = swing_tier3_mult
        self.swing_tier4_mult: Decimal = swing_tier4_mult
        self.swing_tier5_mult: Decimal = swing_tier5_mult
        self.swing_trailing_drop_pct: Decimal = swing_trailing_drop_pct
        self.break_even_multiplier: Decimal = Decimal("1.0") + (break_even_gain_pct / Decimal("100.0"))

    def evaluate_price_tick(
        self,
        position: PositionState,
        current_price: Decimal,
    ) -> tuple[str, Decimal] | None:
        """
        Avalia o preço atual e o tempo decorrido contra as regras da estratégia ativa.
        Retorna (motivo_saida, quantidade_tokens_a_vender) se houver gatilho; caso contrário, None.
        """
        if getattr(position, "strategy_type", "SCALP") == "SWING":
            return self._evaluate_swing_tick(position, current_price)
        return self._evaluate_scalp_tick(position, current_price)

    def _evaluate_scalp_tick(
        self,
        position: PositionState,
        current_price: Decimal,
    ) -> tuple[str, Decimal] | None:
        """
        Avaliação de risco da vertente SCALP:
        1. Janela temporal: Encerra se passar de 1 hora (libera capital).
        2. Alvo de lucro: Encerra com venda de 100% ao atingir +100% (2x).
        3. Stop emergencial e Trailing Stop contínuo durante a hora.
        """
        elapsed_sec = position.elapsed_seconds()

        # 1. Checagem de Tempo Máximo (Janela de 1 hora)
        if elapsed_sec >= self.scalp_max_hold_seconds:
            elapsed_min = elapsed_sec / 60.0
            logger.info(
                "⏰ [SCALP TEMPO ESGOTADO (1h)] Posição #%s (%s) atingiu %.1f minutos aberta | Encerrando trade a mercado ($%.6f)!",
                str(position.id),
                position.token_address,
                elapsed_min,
                current_price,
                extra={"event": "SCALP_TIMEOUT", "token_address": position.token_address},
            )
            return "SCALP_TIMEOUT", position.remaining_token_amount

        # 2. Checagem de Alvo de Lucro (+100% -> Venda de 100%)
        scalp_target_price = position.entry_price * self.scalp_target_multiplier
        if current_price >= scalp_target_price:
            logger.info(
                "🎯 [SCALP ALVO ATINGIDO (+%.0f%%)!] Token %s | Cotação $%.6f >= Alvo $%.6f | Venda de 100%% realizada!",
                self.scalp_target_gain_pct,
                position.token_address,
                current_price,
                scalp_target_price,
                extra={"event": "SCALP_TARGET_REACHED", "token_address": position.token_address},
            )
            return "SCALP_TARGET_REACHED", position.remaining_token_amount

        # 3. Checagem de Stop Loss Emergencial (antes de registrar lucro)
        if position.highest_price_seen <= position.entry_price:
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

        # 4. Trailing Stop Contínuo
        position.trailing_drop_pct = self.trailing_drop_pct
        position.update_price_and_trailing_stop(current_price)
        if position.highest_price_seen > Decimal("0.0"):
            dynamic_stop = position.highest_price_seen * (Decimal("1.0") - self.trailing_drop_pct)
            if dynamic_stop > position.trailing_stop_price:
                position.trailing_stop_price = dynamic_stop

        if position.highest_price_seen > position.entry_price and current_price <= position.trailing_stop_price:
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

    def _evaluate_swing_tick(
        self,
        position: PositionState,
        current_price: Decimal,
    ) -> tuple[str, Decimal] | None:
        """
        Avaliação de risco da vertente SWING:
        1. Janela temporal: Encerra ao atingir 24 horas aberta.
        2. Alvo mestre: Encerra ao atingir +2.000% (21x).
        3. Catraca de Degraus (Ratchet Floors): Eleva pisos nos degraus de 2x, 4x, 6x, 11x, 21x.
        4. Avaliação Horária de Quedas (Hourly Health Check): Avalia recuo na hora a cada ciclo de 1h.
        """
        if current_price > position.highest_price_seen:
            position.highest_price_seen = current_price

        if current_price > position.hourly_peak_price:
            position.hourly_peak_price = current_price

        elapsed_sec = position.elapsed_seconds()
        elapsed_hours = position.elapsed_hours()

        # 1. Checagem de Tempo Máximo (Janela de 24 horas)
        if elapsed_sec >= self.swing_max_hold_seconds:
            logger.info(
                "⏰ [SWING TEMPO ESGOTADO (24h)] Posição #%s (%s) atingiu 24h aberta | Encerrando trade a mercado ($%.6f)!",
                str(position.id),
                position.token_address,
                current_price,
                extra={"event": "SWING_TIMEOUT", "token_address": position.token_address},
            )
            return "SWING_TIMEOUT", position.remaining_token_amount

        # 2. Checagem de Alvo Mestre (+2.000% / 21x)
        swing_target_price = position.entry_price * self.swing_target_multiplier
        if current_price >= swing_target_price:
            logger.info(
                "🚀 [SWING SUPER ALVO ATINGIDO (+%.0f%%)!] Token %s | Cotação $%.6f >= Alvo $%.6f (21x) | Realizando 100%% de lucro!",
                self.swing_target_gain_pct,
                position.token_address,
                current_price,
                swing_target_price,
                extra={"event": "SWING_TARGET_REACHED", "token_address": position.token_address},
            )
            return "SWING_TARGET_REACHED", position.remaining_token_amount

        # 3. Atualização dos Degraus da Catraca (Ratchet Tiers)
        self._update_swing_ratchet_tier(position)

        # 4. Avaliação de Saída por Piso da Catraca
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
            # Degrau 0: Stop Loss de proteção se configurado
            if Decimal("0.0") < self.swing_stop_multiplier < Decimal("1.0"):
                stop_loss_price = position.entry_price * self.swing_stop_multiplier
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
            else:
                position.trailing_stop_price = Decimal("0.0")

        # 5. Avaliação Horária de Quedas (Hourly Health Check a cada 1 hora de posição)
        current_hour_idx = int(elapsed_hours)
        if current_hour_idx > position.last_hourly_eval_hour and current_hour_idx >= 1:
            peak = position.hourly_peak_price if position.hourly_peak_price > Decimal("0.0") else position.highest_price_seen
            if peak > Decimal("0.0"):
                drop_from_hour_peak_pct = ((peak - current_price) / peak) * Decimal("100.0")
                if drop_from_hour_peak_pct > self.swing_max_hourly_drop_pct:
                    logger.warning(
                        "📉 [SWING CHECK HORÁRIO #%d/24h REPROVADO] Token %s caiu %.1f%% na última hora (limite: %.1f%%) | Pico da hora: $%.6f -> Atual: $%.6f | Encerrando posição para evitar sangria!",
                        current_hour_idx,
                        position.token_address,
                        drop_from_hour_peak_pct,
                        self.swing_max_hourly_drop_pct,
                        peak,
                        current_price,
                        extra={"event": "SWING_HOURLY_DROP", "token_address": position.token_address},
                    )
                    return "SWING_HOURLY_DROP", position.remaining_token_amount

            # Passou no teste da hora: atualiza para o próximo ciclo e reseta o pico horário
            position.last_hourly_eval_hour = current_hour_idx
            position.hourly_peak_price = current_price
            logger.info(
                "⏱️ [SWING CHECK HORÁRIO #%d/24h APROVADO] Token %s saudável (Recuo na hora: %.1f%% <= %.1f%%) | Mantendo posição aberta para buscar até +2.000%%!",
                current_hour_idx,
                position.token_address,
                ((peak - current_price) / peak * Decimal("100.0")) if peak > Decimal("0.0") else Decimal("0.0"),
                self.swing_max_hourly_drop_pct,
            )

        return None

    def _update_swing_ratchet_tier(self, position: PositionState) -> None:
        """Avalia e promove os degraus da catraca visando até +2.000%."""
        entry = position.entry_price
        target_tier = 0
        new_floor = Decimal("0.0")

        if position.highest_price_seen >= entry * self.swing_tier5_mult:
            target_tier = 5
            # Degrau 5 (+2000% / 21x): Trava piso em 16x (+1500%)
            new_floor = entry * Decimal("16.0")
        elif position.highest_price_seen >= entry * self.swing_tier4_mult:
            target_tier = 4
            # Degrau 4 (+1000% / 11x): Trava piso no Degrau 3 (6x / +500%)
            new_floor = entry * self.swing_tier3_mult
        elif position.highest_price_seen >= entry * self.swing_tier3_mult:
            target_tier = 3
            # Degrau 3 (+500% / 6x): Trava piso no Degrau 2 (4x / +300%)
            new_floor = entry * self.swing_tier2_mult
        elif position.highest_price_seen >= entry * self.swing_tier2_mult:
            target_tier = 2
            # Degrau 2 (+300% / 4x): Trava piso no Degrau 1 (2x / +100%)
            new_floor = entry * self.swing_tier1_mult
        elif position.highest_price_seen >= entry * self.swing_tier1_mult:
            target_tier = 1
            # Degrau 1 (+100% / 2x): Trava piso na Entrada ($x / BE sem custo)
            new_floor = entry

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
