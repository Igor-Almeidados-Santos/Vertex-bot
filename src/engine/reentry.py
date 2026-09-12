"""
Motor de Avaliação e Mitigação de Risco para Reentrada Inteligente (Anti-Falling-Knife).
Impede o reinvestimento precipitado em ativos recém-liquidados, aplicando cool-off
temporal e confirmação de estabilização/repique de suporte pós-saída.
"""

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.utils.logger import setup_logger

logger = setup_logger("vertex.engine.reentry")


@dataclass
class TokenExitRecord:
    """Registro de liquidação de posição anterior para análise de reentrada."""

    token_address: str
    exit_price: Decimal
    exit_timestamp: float
    exit_reason: str
    lowest_price_seen: Decimal


class ReentryRiskManager:
    """
    Controla janelas de quarentena, cool-off e métricas de repique de suporte
    para evitar compras durante quedas em cascata (falling knife).
    """

    def __init__(
        self,
        trailing_cooloff_sec: float = 300.0,  # 5 minutos após Trailing Stop
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
        timestamp: float | None = None,
    ) -> None:
        """Registra a liquidação de uma posição para iniciar a janela de monitoramento pós-saída."""
        now = time.time() if timestamp is None else timestamp
        record = TokenExitRecord(
            token_address=token_address,
            exit_price=exit_price,
            exit_timestamp=now,
            exit_reason=exit_reason,
            lowest_price_seen=exit_price,
        )
        self._exit_records[token_address] = record
        logger.info(
            "🛡️ [REENTRADA MONITORADA] Token %s registrado pós-saída (%s) a $%.8f. Cool-off ativado.",
            token_address,
            exit_reason,
            exit_price,
        )

    def update_post_exit_price(self, token_address: str, current_price: Decimal) -> None:
        """Atualiza a mínima histórica observada após o encerramento da posição."""
        record = self._exit_records.get(token_address)
        if record is None:
            return
        if current_price < record.lowest_price_seen:
            record.lowest_price_seen = current_price

    def _check_cooloff(self, record: TokenExitRecord, elapsed_sec: float) -> tuple[bool, str] | None:
        """Verifica se o tempo mínimo pós-saída foi respeitado."""
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
        Retorna (is_allowed: bool, reason: str).
        """
        record = self._exit_records.get(token_address)
        if record is None:
            return True, "Primeira entrada no token (sem histórico de saída)."

        current_time = time.time() if now is None else now
        elapsed_sec = current_time - record.exit_timestamp

        # 1. Validação de Liquidez Mínima
        if current_liquidity_usd is not None and current_liquidity_usd < self.min_liquidity_usd:
            return False, f"Liquidez atual (${current_liquidity_usd:.2f}) abaixo do mínimo (${self.min_liquidity_usd:.2f})."

        # 2. Cool-off Temporal
        cooloff_res = self._check_cooloff(record, elapsed_sec)
        if cooloff_res is not None:
            return cooloff_res

        # 3. Atualiza mínima histórica pós-saída
        if current_price < record.lowest_price_seen:
            record.lowest_price_seen = current_price

        # 4. Anti-Falling-Knife
        knife_res = self._check_falling_knife(record, current_price)
        if knife_res is not None:
            return knife_res

        # 5. Confirmação de Suporte
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

    def clear_history(self) -> None:
        """Limpa o histórico de saídas (utilizado no reset da simulação)."""
        self._exit_records.clear()
        logger.info("Histórico do ReentryRiskManager limpo com sucesso.")

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
            "Configurações do ReentryRiskManager atualizadas: TrailingCooloff=%.0fs | StopLossCooloff=%.0fs | MinBounce=%.1f%%",
            self.trailing_cooloff_sec,
            self.stoploss_cooloff_sec,
            float(self.min_bounce_pct),
        )
