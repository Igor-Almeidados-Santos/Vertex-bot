"""
Rastreador Assíncrono do Ciclo de Vida das Posições (Finite State Machine).
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal

from src.database.models import PositionState, PositionStatus
from src.database.repository import PositionsRepository
from src.engine.interface import IExecutionEngine
from src.engine.risk import RiskManager
from src.utils.logger import setup_logger

logger = setup_logger("vertex.engine.tracker")


class PositionTracker:
    """Supervisiona o estado e as saídas automatizadas de posições abertas."""

    def __init__(
        self,
        engine: IExecutionEngine,
        positions_repo: PositionsRepository,
        risk_manager: RiskManager,
        on_position_closed: Callable[[PositionState], Awaitable[None]] | None = None,
    ) -> None:
        self.engine: IExecutionEngine = engine
        self.positions_repo: PositionsRepository = positions_repo
        self.risk_manager: RiskManager = risk_manager
        self.on_position_closed: Callable[[PositionState], Awaitable[None]] | None = on_position_closed
        self.active_positions: dict[int, PositionState] = {}
        self.is_running: bool = False

    async def register_position(self, position: PositionState) -> None:
        """Adiciona uma nova posição ao rastreador de risco."""
        if position.id is not None:
            if position.current_price is None:
                position.current_price = position.entry_price
            self.active_positions[position.id] = position
            logger.info("Posição ID #%d (%s) registrada no Tracker de Risco.", position.id, position.token_address)

    async def process_price_tick(self, position_id: int, current_price: Decimal) -> None:
        """Processa um novo tick de preço para a posição informada."""
        position = self.active_positions.get(position_id)
        if not position or position.status in (PositionStatus.CLOSED, PositionStatus.STOPPED):
            return

        position.current_price = current_price

        decision = self.risk_manager.evaluate_price_tick(position, current_price)
        if not decision:
            # Apenas atualiza a máxima, linha de stop e degraus de ratchet no banco
            await self.positions_repo.update_tracking(
                position_id,
                position.highest_price_seen,
                position.trailing_stop_price,
                ratchet_tier=position.ratchet_tier,
                ratchet_floor_price=position.ratchet_floor_price,
            )
            return

        action, tokens_to_sell = decision

        if action == "BREAK_EVEN":
            logger.info(
                "💰 [BREAK-EVEN ATIVADO!] Posição #%d (%s) | Cotação dobrou para $%.8f (+100%%) | Vendendo 50%% dos tokens para recuperar capital inicial!",
                position_id,
                position.token_address,
                current_price,
            )
            # 1. Executa venda de 50% com preço corrente de execução
            await self.engine.execute_sell(
                position,
                tokens_to_sell,
                reason="BREAK_EVEN",
                execution_price=current_price,
            )
            # 2. Atualiza estado em memória
            position.trigger_break_even(current_price)
            # 3. Persiste no SQLite
            await self.positions_repo.mark_break_even(
                position_id,
                position.remaining_token_amount,
                position.realized_pnl_usd,
            )
            await self.positions_repo.update_tracking(
                position_id,
                position.highest_price_seen,
                position.trailing_stop_price,
                ratchet_tier=position.ratchet_tier,
                ratchet_floor_price=position.ratchet_floor_price,
            )

        elif action in (
            "TRAILING_STOP",
            "EMERGENCY_STOP",
            "SWING_RATCHET_STOP",
            "SCALP_TARGET_REACHED",
            "SWING_TARGET_REACHED",
            "SCALP_TIMEOUT",
            "SWING_TIMEOUT",
            "SWING_HOURLY_DROP",
        ):
            if action == "SCALP_TARGET_REACHED":
                logger.info(
                    "🎯 [SCALP ALVO ATINGIDO (+100%%)!] Posição #%d (%s) | Cotação dobrou para $%.8f | Realizando 100%% de lucro!",
                    position_id,
                    position.token_address,
                    current_price,
                )
            elif action == "SWING_TARGET_REACHED":
                logger.info(
                    "🚀 [SWING ALVO MESTRE (+2.000%%)!] Posição #%d (%s) | Cotação atingiu 21x ($%.8f) | Realizando 100%% de lucro!",
                    position_id,
                    position.token_address,
                    current_price,
                )
            elif action == "SCALP_TIMEOUT":
                logger.info(
                    "⏰ [SCALP TIMEOUT (1h)] Posição #%d (%s) | Janela de 1h expirou | Encerrando posição a mercado ($%.8f)!",
                    position_id,
                    position.token_address,
                    current_price,
                )
            elif action == "SWING_TIMEOUT":
                logger.info(
                    "⏰ [SWING TIMEOUT (24h)] Posição #%d (%s) | Janela de 24h expirou | Encerrando posição a mercado ($%.8f)!",
                    position_id,
                    position.token_address,
                    current_price,
                )
            elif action == "SWING_HOURLY_DROP":
                logger.warning(
                    "📉 [SWING CHECK HORÁRIO REPROVADO] Posição #%d (%s) | Queda na última hora excedeu o limite | Encerrando trade ($%.8f)!",
                    position_id,
                    position.token_address,
                    current_price,
                )
            elif action == "SWING_RATCHET_STOP":
                logger.info(
                    "🌊 [SWING RATCHET STOP ATIVADO!] Posição #%d (%s) | Cotação recuou para o piso do Degrau #%d ($%.8f -> $%.8f) | Fechando posição com lucro travado!",
                    position_id,
                    position.token_address,
                    position.ratchet_tier,
                    position.highest_price_seen,
                    current_price,
                )
            elif action == "TRAILING_STOP":
                logger.info(
                    "🔴 [TRAILING STOP ATIVADO!] Posição #%d (%s) | Recuo de %.0f%% da máxima ($%.8f -> $%.8f) | Fechando posição!",
                    position_id,
                    position.token_address,
                    position.trailing_drop_pct * Decimal("100.0"),
                    position.highest_price_seen,
                    current_price,
                )
            else:
                logger.warning(
                    "🛑 [STOP LOSS DE EMERGÊNCIA!] Posição #%d (%s) | Queda severa ($%.8f) | Fechando posição para mitigar risco!",
                    position_id,
                    position.token_address,
                    current_price,
                )

            # 1. Executa venda de 100% dos restantes com preço corrente de execução
            await self.engine.execute_sell(
                position,
                tokens_to_sell,
                reason=action,
                execution_price=current_price,
            )
            # 2. Atualiza estado em memória
            position.close_position(current_price, reason=action)
            # 3. Persiste no SQLite
            status = (
                PositionStatus.CLOSED
                if action in (
                    "TRAILING_STOP",
                    "SWING_RATCHET_STOP",
                    "SCALP_TARGET_REACHED",
                    "SWING_TARGET_REACHED",
                    "SCALP_TIMEOUT",
                    "SWING_TIMEOUT",
                )
                else PositionStatus.STOPPED
            )
            await self.positions_repo.close_position(
                position_id,
                position.realized_pnl_usd,
                status=status,
            )
            # 4. Remove das posições ativas
            self.active_positions.pop(position_id, None)
            logger.info("Posição ID #%d encerrada com sucesso por %s | PnL Realizado: $%.2f.", position_id, action, position.realized_pnl_usd)

            # 5. Notifica encerramento para liberar token para nova análise/reentrada
            if self.on_position_closed is not None:
                try:
                    await self.on_position_closed(position)
                except Exception as cb_err:
                    logger.warning("Erro ao executar callback on_position_closed para %s: %s", position.token_address, cb_err)
