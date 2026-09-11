"""
Rastreador Assíncrono do Ciclo de Vida das Posições (Finite State Machine).
"""

import asyncio
from decimal import Decimal
from typing import Dict, Optional

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
    ) -> None:
        self.engine: IExecutionEngine = engine
        self.positions_repo: PositionsRepository = positions_repo
        self.risk_manager: RiskManager = risk_manager
        self.active_positions: Dict[int, PositionState] = {}
        self.is_running: bool = False

    async def register_position(self, position: PositionState) -> None:
        """Adiciona uma nova posição ao rastreador de risco."""
        if position.id is not None:
            self.active_positions[position.id] = position
            logger.info("Posição ID #%d (%s) registrada no Tracker de Risco.", position.id, position.token_address)

    async def process_price_tick(self, position_id: int, current_price: Decimal) -> None:
        """Processa um novo tick de preço para a posição informada."""
        position = self.active_positions.get(position_id)
        if not position or position.status in (PositionStatus.CLOSED, PositionStatus.STOPPED):
            return

        decision = self.risk_manager.evaluate_price_tick(position, current_price)
        if not decision:
            # Apenas atualiza a máxima e linha de stop no banco
            await self.positions_repo.update_tracking(
                position_id,
                position.highest_price_seen,
                position.trailing_stop_price,
            )
            return

        action, tokens_to_sell = decision

        if action == "BREAK_EVEN":
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
            )

        elif action in ("TRAILING_STOP", "EMERGENCY_STOP"):
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
            status = PositionStatus.CLOSED if action == "TRAILING_STOP" else PositionStatus.STOPPED
            await self.positions_repo.close_position(
                position_id,
                position.realized_pnl_usd,
                status=status,
            )
            # 4. Remove das posições ativas
            self.active_positions.pop(position_id, None)
            logger.info("Posição ID #%d encerrada com sucesso por %s.", position_id, action)
