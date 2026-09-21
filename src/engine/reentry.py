"""
Motor de Desempenho, Reentrada Imediata e Escalonamento de Posições (PerformanceScalingManager).
Permite reentrada imediata em tokens de alta performance (vencedores), quarentena protetiva
em ativos liquidados por stop-loss, piramidação (scale-in) e promoção automática de Scalp para Swing.
"""

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.utils.logger import setup_logger

logger = setup_logger("vertex.engine.reentry")


@dataclass
class TokenExitRecord:
    """Registro de liquidação de posição anterior para análise de desempenho e reentrada."""

    token_address: str
    exit_price: Decimal
    exit_timestamp: float
    exit_reason: str
    lowest_price_seen: Decimal
    realized_pnl: Decimal = Decimal("0.0")
    is_winner: bool = False


class PerformanceScalingManager:
    """
    Controla reentrada imediata para ativos lucrativos, quarentena defensiva para perdas,
    piramidação inteligente em posições lucrativas e promoção estratégica de Scalp para Swing.
    """

    def __init__(
        self,
        trailing_cooloff_sec: float = 300.0,  # 5 minutos padrão para saídas neutras
        stoploss_cooloff_sec: float = 1800.0,  # 30 minutos após Stop Loss de Emergência
        min_bounce_pct: Decimal = Decimal("3.0"),  # Repique mínimo de 3% a partir do fundo
        max_post_exit_drop_pct: Decimal = Decimal("25.0"),  # Queda máxima tolerada sem repique
        min_liquidity_usd: Decimal = Decimal("5000.0"),  # Liquidez mínima de segurança
    ) -> None:
        self.trailing_cooloff_sec: float = trailing_cooloff_sec
        self.stoploss_cooloff_sec: float = stoploss_cooloff_sec
        self.min_bounce_pct: Decimal = min_bounce_pct
        self.max_post_exit_drop_pct: Decimal = max_post_exit_drop_pct
        self.min_liquidity_usd: Decimal = min_liquidity_usd
        self._exit_records: dict[str, TokenExitRecord] = {}

    def record_exit(
        self,
        token_address: str,
        exit_price: Decimal,
        exit_reason: str,
        realized_pnl: Decimal = Decimal("0.0"),
        is_winner: bool | None = None,
        timestamp: float | None = None,
    ) -> None:
        """
        Registra a liquidação de uma posição.
        Se o trade foi lucrativo (winner), autoriza reentrada imediata (cool-off = 0s).
        Se foi prejuízo (stop-loss), aplica quarentena protetiva.
        """
        now = time.time() if timestamp is None else timestamp
        if is_winner is not None:
            winner = is_winner
        else:
            winner = realized_pnl > Decimal("0.0")

        record = TokenExitRecord(
            token_address=token_address,
            exit_price=exit_price,
            exit_timestamp=now,
            exit_reason=exit_reason,
            lowest_price_seen=exit_price,
            realized_pnl=realized_pnl,
            is_winner=winner,
        )
        self._exit_records[token_address] = record

        if winner:
            logger.info(
                "🚀 [VENCEDOR / REENTRADA IMEDIATA] Token %s encerrou com lucro (+$%.4f | %s). Liberado para reentrada imediata!",
                token_address,
                realized_pnl,
                exit_reason,
            )
        else:
            logger.info(
                "🛡️ [QUARENTENA PROTETIVA] Token %s encerrou no prejuízo/stop (%s | PnL: $%.4f). Quarentena de %.0fs ativada.",
                token_address,
                exit_reason,
                realized_pnl,
                self.stoploss_cooloff_sec,
            )

    def authorize_immediate_reanalysis(self, token_address: str) -> None:
        """Libera o token para reanálise e reentrada imediata sem restrições de cool-off temporal."""
        record = self._exit_records.get(token_address)
        if record is not None:
            record.is_winner = True
            record.exit_reason = "REANALYSIS_AUTHORIZED"
            logger.info("🔓 [REANÁLISE IMEDIATA] Token %s liberado para reavaliação instantânea sem cool-off.", token_address)

    def update_post_exit_price(self, token_address: str, current_price: Decimal) -> None:
        """Atualiza a mínima histórica observada após o encerramento da posição."""
        record = self._exit_records.get(token_address)
        if record is None:
            return
        if current_price < record.lowest_price_seen:
            record.lowest_price_seen = current_price

    def _check_cooloff(self, record: TokenExitRecord, elapsed_sec: float) -> tuple[bool, str] | None:
        """Verifica se o tempo mínimo pós-saída foi respeitado."""
        if record.is_winner:
            return None  # Ativos vencedores não sofrem cool-off temporal

        is_stop_loss = record.exit_reason in ("EMERGENCY_STOP", "STOP_LOSS")
        required_cooloff = self.stoploss_cooloff_sec if is_stop_loss else self.trailing_cooloff_sec
        if elapsed_sec < required_cooloff:
            remaining_min = (required_cooloff - elapsed_sec) / 60.0
            return False, f"Em período de descanso pós-{record.exit_reason} (restam {remaining_min:.1f} min)."
        return None

    def _check_falling_knife(self, record: TokenExitRecord, current_price: Decimal) -> tuple[bool, str] | None:
        """Verifica se o ativo está em queda livre sem repique a partir do fundo pós-saída."""
        if record.exit_price <= Decimal("0.0"):
            return None
        drop_from_exit_pct = ((record.exit_price - current_price) / record.exit_price) * Decimal("100.0")
        if drop_from_exit_pct >= self.max_post_exit_drop_pct and record.lowest_price_seen > Decimal("0.0"):
            bounce_pct = ((current_price - record.lowest_price_seen) / record.lowest_price_seen) * Decimal("100.0")
            if bounce_pct < self.min_bounce_pct:
                return (
                    False,
                    f"Ativo em queda livre pós-saída (-{drop_from_exit_pct:.1f}%). "
                    f"Repique atual ({bounce_pct:.1f}%) inferior ao mínimo exigido ({self.min_bounce_pct:.1f}%).",
                )
        return None

    def can_reenter(
        self,
        token_address: str,
        current_price: Decimal,
        current_liquidity_usd: Decimal | None = None,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """
        Avalia se um token que já teve posição encerrada está apto para reentrada.
        Vencedores têm reentrada imediata garantida desde que a liquidez permaneça válida.
        """
        record = self._exit_records.get(token_address)
        if record is None:
            return True, "Primeira entrada no token (sem histórico de saída)."

        current_time = time.time() if now is None else now
        elapsed_sec = current_time - record.exit_timestamp

        # 1. Validação de Liquidez Mínima
        if current_liquidity_usd is not None and current_liquidity_usd < self.min_liquidity_usd:
            return False, f"Liquidez atual (${current_liquidity_usd:.2f}) abaixo do mínimo (${self.min_liquidity_usd:.2f})."

        # 2. Ativos Vencedores: Reentrada Imediata
        if record.is_winner:
            knife_res = self._check_falling_knife(record, current_price)
            if knife_res is not None:
                return knife_res
            return True, "Reentrada imediata liberada: ativo vencedor de alta performance."

        # 3. Cool-off Temporal para Trades Não Vencedores (Perdas / Stops)
        cooloff_res = self._check_cooloff(record, elapsed_sec)
        if cooloff_res is not None:
            return cooloff_res

        # 4. Atualiza mínima histórica pós-saída
        if current_price < record.lowest_price_seen:
            record.lowest_price_seen = current_price

        # 5. Anti-Falling-Knife
        knife_res = self._check_falling_knife(record, current_price)
        if knife_res is not None:
            return knife_res

        # 6. Confirmação de Suporte
        if record.lowest_price_seen > Decimal("0.0") and current_price > record.lowest_price_seen:
            bounce_pct = ((current_price - record.lowest_price_seen) / record.lowest_price_seen) * Decimal("100.0")
            if bounce_pct >= self.min_bounce_pct:
                logger.info(
                    "✅ [REENTRADA APROVADA] Token %s confirmou suporte com repique de +%.1f%% acima da mínima pós-venda.",
                    token_address,
                    bounce_pct,
                )
                return True, f"Repique de suporte confirmado (+{bounce_pct:.1f}% acima da mínima pós-saída)."

        return True, "Tempo de cool-off superado e condições estáveis."

    def can_scale_in(
        self,
        token_address: str,
        active_positions_for_token: list[Any],
        max_positions_per_token: int = 2,
        min_profit_pct: Decimal = Decimal("5.0"),
    ) -> tuple[bool, str]:
        """
        Avalia se é permitido abrir uma nova posição no mesmo token (Scale-In / Piramidação).
        Permitido apenas se as posições já existentes estiverem no verde com lucro confirmado.
        """
        if not active_positions_for_token:
            return True, "Nenhuma posição ativa para o token. Entrada autorizada."

        if len(active_positions_for_token) >= max_positions_per_token:
            return (
                False,
                f"Limite máximo de posições por token atingido ({len(active_positions_for_token)}/{max_positions_per_token}).",
            )

        # Todas as posições ativas anteriores devem estar em lucro
        for pos in active_positions_for_token:
            roi = getattr(pos, "roi_pct", Decimal("0.0"))
            be = getattr(pos, "break_even_triggered", False)
            if not be and roi < min_profit_pct:
                return (
                    False,
                    f"Posição ativa anterior ainda não atingiu o lucro mínimo para piramidação (ROI: {roi:.2f}% < +{min_profit_pct:.1f}%).",
                )

        return True, f"Ativo vencedor confirmado com lucro em todas as posições (>= +{min_profit_pct:.1f}%). Piramidação autorizada."

    def can_promote_to_swing(
        self,
        scalp_pos: Any,
        token_age_hours: float | None,
        liquidity_usd: Decimal,
        min_age_swing: float = 3.0,
        max_age_swing: float = 6.0,
        min_liquidity_swing: Decimal = Decimal("20000.0"),
        swing_slots_available: bool = True,
    ) -> tuple[bool, str]:
        """
        Avalia se uma posição SCALP lucrativa deve ser promovida para abrir uma posição adicional de SWING.
        """
        if not swing_slots_available:
            return False, "Sem slots de Swing disponíveis na carteira."

        roi = getattr(scalp_pos, "roi_pct", Decimal("0.0"))
        be = getattr(scalp_pos, "break_even_triggered", False)
        if not be and roi < Decimal("5.0"):
            return False, f"Posição Scalp ainda sem lucro consolidado (ROI: {roi:.2f}% < +5.0%) para promoção."

        if liquidity_usd < min_liquidity_swing:
            return (
                False,
                f"Liquidez atual (${liquidity_usd:,.2f}) abaixo do piso exigido para Swing (${min_liquidity_swing:,.2f}).",
            )

        if token_age_hours is not None and (token_age_hours < min_age_swing or token_age_hours > max_age_swing):
            return (
                False,
                f"Idade do token ({token_age_hours:.1f}h) fora da janela de consolidação de Swing ({min_age_swing:.1f}h a {max_age_swing:.1f}h).",
            )

        return (
            True,
            f"Token de alta performance em Scalp (ROI: +{roi:.2f}%) e parâmetros consolidados para Swing.",
        )

    def clear_history(self) -> None:
        """Limpa o histórico de saídas (utilizado no reset da simulação)."""
        self._exit_records.clear()
        logger.info("Histórico do PerformanceScalingManager limpo com sucesso.")

    def update_config(self, **kwargs: Any) -> None:
        """Atualiza dinamicamente parâmetros de reentrada a partir do painel de controle."""
        if "trailing_cooloff_sec" in kwargs and kwargs["trailing_cooloff_sec"] is not None:
            self.trailing_cooloff_sec = float(kwargs["trailing_cooloff_sec"])
        if "stoploss_cooloff_sec" in kwargs and kwargs["stoploss_cooloff_sec"] is not None:
            self.stoploss_cooloff_sec = float(kwargs["stoploss_cooloff_sec"])
        if "min_bounce_pct" in kwargs and kwargs["min_bounce_pct"] is not None:
            self.min_bounce_pct = Decimal(str(kwargs["min_bounce_pct"]))
        if "max_post_exit_drop_pct" in kwargs and kwargs["max_post_exit_drop_pct"] is not None:
            self.max_post_exit_drop_pct = Decimal(str(kwargs["max_post_exit_drop_pct"]))
        if "min_liquidity_usd" in kwargs and kwargs["min_liquidity_usd"] is not None:
            self.min_liquidity_usd = Decimal(str(kwargs["min_liquidity_usd"]))
        logger.info(
            "Configurações do PerformanceScalingManager atualizadas: TrailingCooloff=%.0fs | StopLossCooloff=%.0fs | MinBounce=%.1f%%",
            self.trailing_cooloff_sec,
            self.stoploss_cooloff_sec,
            float(self.min_bounce_pct),
        )


# Alias para retrocompatibilidade
ReentryRiskManager = PerformanceScalingManager
