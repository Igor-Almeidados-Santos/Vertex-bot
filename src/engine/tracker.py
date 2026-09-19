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

    async def close_position_manually(self, position_id: int) -> bool:
        """Encerra manualmente uma posição ativa a mercado a pedido do usuário."""
        position = self.active_positions.get(position_id)
        if not position:
            db_pos = await self.positions_repo.get_by_id(position_id)
            if not db_pos or db_pos.status in (PositionStatus.CLOSED, PositionStatus.STOPPED):
                logger.warning("Tentativa de fechar manualmente posição #%d que não está aberta.", position_id)
                return False
            position = db_pos

        current_price = (
            position.current_price
            if position.current_price is not None and position.current_price > Decimal("0.0")
            else (
                position.highest_price_seen
                if position.highest_price_seen > Decimal("0.0")
                else position.entry_price
            )
        )
        tokens_to_sell = position.remaining_token_amount
        if tokens_to_sell <= Decimal("0.0"):
            logger.warning("Posição #%d possui saldo restante de tokens zerado.", position_id)
            return False

        logger.info(
            "🛑 [ENCERRAMENTO MANUAL] Posição #%d (%s) | Cotação: $%.8f | Liquidando 100%% dos tokens remanescentes (%s)...",
            position_id,
            position.token_address,
            current_price,
            tokens_to_sell,
        )

        # 1. Executa venda a mercado
        await self.engine.execute_sell(
            position,
            tokens_to_sell,
            reason="MANUAL_CLOSE",
            execution_price=current_price,
        )

        # 2. Atualiza estado em memória
        position.close_position(current_price, reason="MANUAL_CLOSE")

        # 3. Persiste no SQLite
        await self.positions_repo.close_position(
            position_id,
            position.realized_pnl_usd,
            status=PositionStatus.CLOSED,
        )

        # 4. Remove das posições ativas
        self.active_positions.pop(position_id, None)
        logger.info(
            "Posição ID #%d encerrada manualmente com sucesso | PnL Realizado: $%.2f.",
            position_id,
            position.realized_pnl_usd,
        )

        # 5. Notifica encerramento para liberar token para monitoramento
        if self.on_position_closed is not None:
            try:
                await self.on_position_closed(position)
            except Exception as cb_err:
                logger.warning(
                    "Erro ao executar callback on_position_closed para %s: %s",
                    position.token_address,
                    cb_err,
                )

        return True

    async def check_positions_watchdog(
        self,
        get_fresh_pair_data: Callable[[str], dict[str, Any] | None] | None = None,
    ) -> list[int]:
        """
        Watchdog autônomo e independente de novos ticks de cotação.
        Encerra posições estagnadas que atingiram tempo máximo (1h Scalp, 24h Swing)
        ou cuja liquidez na DEX foi drenada.
        """
        closed_ids: list[int] = []
        for pos_id, pos in list(self.active_positions.items()):
            if pos.status in (PositionStatus.CLOSED, PositionStatus.STOPPED):
                continue

            strat = getattr(pos, "strategy_type", "SCALP")
            elapsed_sec = pos.elapsed_seconds()
            tokens_to_sell = pos.remaining_token_amount
            if tokens_to_sell <= Decimal("0.0"):
                continue

            current_price = (
                pos.current_price
                if pos.current_price is not None and pos.current_price > Decimal("0.0")
                else (pos.highest_price_seen if pos.highest_price_seen > Decimal("0.0") else pos.entry_price)
            )

            # 1. Scalp Timeout (1 hora estrita)
            if strat == "SCALP" and elapsed_sec >= self.risk_manager.scalp_max_hold_seconds:
                elapsed_min = elapsed_sec / 60.0
                logger.warning(
                    "⏰ [WATCHDOG SCALP TIMEOUT (1h)] Posição #%d (%s) atingiu %.1f minutos aberta | Forçando encerramento imediato ($%.8f)!",
                    pos_id,
                    pos.token_address,
                    elapsed_min,
                    current_price,
                )
                await self.engine.execute_sell(pos, tokens_to_sell, reason="SCALP_TIMEOUT", execution_price=current_price)
                pos.close_position(current_price, reason="SCALP_TIMEOUT")
                await self.positions_repo.close_position(pos_id, pos.realized_pnl_usd, status=PositionStatus.CLOSED)
                self.active_positions.pop(pos_id, None)
                closed_ids.append(pos_id)
                if self.on_position_closed:
                    try:
                        await self.on_position_closed(pos)
                    except Exception as cb_err:
                        logger.warning("Erro em callback on_position_closed: %s", cb_err)
                continue

            # 2. Swing Timeout (24h)
            if strat == "SWING" and elapsed_sec >= self.risk_manager.swing_max_hold_seconds:
                logger.warning(
                    "⏰ [WATCHDOG SWING TIMEOUT (24h)] Posição #%d (%s) atingiu %.1f horas aberta | Forçando encerramento imediato ($%.8f)!",
                    pos_id,
                    pos.token_address,
                    elapsed_sec / 3600.0,
                    current_price,
                )
                await self.engine.execute_sell(pos, tokens_to_sell, reason="SWING_TIMEOUT", execution_price=current_price)
                pos.close_position(current_price, reason="SWING_TIMEOUT")
                await self.positions_repo.close_position(pos_id, pos.realized_pnl_usd, status=PositionStatus.CLOSED)
                self.active_positions.pop(pos_id, None)
                closed_ids.append(pos_id)
                if self.on_position_closed:
                    try:
                        await self.on_position_closed(pos)
                    except Exception as cb_err:
                        logger.warning("Erro em callback on_position_closed: %s", cb_err)
                continue

            # 3. Liquidez Drenada / Token Abandonado
            if get_fresh_pair_data is not None:
                pair = get_fresh_pair_data(pos.token_address)
                if pair:
                    liq_usd = float(pair.get("liquidity", {}).get("usd") or 0.0)
                    if 0.0 < liq_usd < 2500.0:
                        logger.warning(
                            "🚨 [WATCHDOG DRENAGEM DE LIQUIDEZ] Posição #%d (%s) | Liquidez da pool colapsou para $%.2f | Fechando posição de emergência!",
                            pos_id,
                            pos.token_address,
                            liq_usd,
                        )
                        await self.engine.execute_sell(pos, tokens_to_sell, reason="EMERGENCY_STOP", execution_price=current_price)
                        pos.close_position(current_price, reason="EMERGENCY_STOP")
                        await self.positions_repo.close_position(pos_id, pos.realized_pnl_usd, status=PositionStatus.STOPPED)
                        self.active_positions.pop(pos_id, None)
                        closed_ids.append(pos_id)
                        if self.on_position_closed:
                            try:
                                await self.on_position_closed(pos)
                            except Exception as cb_err:
                                logger.warning("Erro em callback on_position_closed: %s", cb_err)
                        continue

        return closed_ids

