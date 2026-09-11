"""
Motor de Simulação e Homologação (Paper Trading Mode).
Executa ordens virtuais contra cotações e liquidez reais sem exposição de capital.
"""

import asyncio
from datetime import datetime
from decimal import Decimal

from src.database.models import (
    ExecutionMode,
    OrderExecution,
    OrderType,
    PositionState,
    PositionStatus,
    TokenMetadata,
)
from src.database.repository import OrdersRepository, PositionsRepository
from src.engine.interface import IExecutionEngine
from src.utils.logger import setup_logger

logger = setup_logger("vertex.engine.paper")


class PaperExecutionEngine(IExecutionEngine):
    """Motor de execução simulada com cálculo dinâmico de slippage e latência virtual."""

    def __init__(
        self,
        positions_repo: PositionsRepository,
        orders_repo: OrdersRepository,
        initial_balance_usd: Decimal = Decimal("1500.0"),  # ~10 SOL
        simulated_latency_ms: int = 250,
        trailing_drop_pct: Decimal = Decimal("0.12"),
    ) -> None:
        self.positions_repo: PositionsRepository = positions_repo
        self.orders_repo: OrdersRepository = orders_repo
        self.balance_usd: Decimal = initial_balance_usd
        self.simulated_latency_ms: int = simulated_latency_ms
        self.trailing_drop_pct: Decimal = trailing_drop_pct

    @property
    def mode(self) -> ExecutionMode:
        return ExecutionMode.PAPER

    async def execute_buy(
        self,
        token: TokenMetadata,
        amount_usd: Decimal,
    ) -> PositionState | None:
        """Simula compra a mercado com cálculo de slippage proporcional à liquidez."""
        if amount_usd > self.balance_usd:
            logger.warning(
                "Saldo virtual insuficiente para compra: Saldo=$%.2f, Necessário=$%.2f",
                self.balance_usd,
                amount_usd,
            )
            return None

        # Simulação de latência de confirmação de rede
        await asyncio.sleep(self.simulated_latency_ms / 1000.0)

        # Preço base hipotético de lançamento (ou derivado da liquidez)
        # Ex: $0.001 inicial
        base_price = Decimal("0.001")

        # Modelo de slippage: slippage = (ordem / liquidez_inicial) * 0.5
        liquidity = token.initial_liquidity_usd if token.initial_liquidity_usd > Decimal("0") else Decimal("5000.0")
        slippage_ratio = (amount_usd / liquidity) * Decimal("0.5")
        if slippage_ratio > Decimal("0.05"):
            slippage_ratio = Decimal("0.05")

        execution_price = base_price * (Decimal("1.0") + slippage_ratio)
        tokens_received = amount_usd / execution_price

        # Debita saldo virtual
        self.balance_usd -= amount_usd

        # Inicializa estado da posição
        position = PositionState(
            token_address=token.address,
            mode=ExecutionMode.PAPER,
            entry_price=execution_price,
            initial_token_amount=tokens_received,
            allocated_capital_usd=amount_usd,
            trailing_drop_pct=self.trailing_drop_pct,
            status=PositionStatus.OPEN,
        )

        pos_id = await self.positions_repo.create_position(position)
        position.id = pos_id

        # Registra ordem imutável
        order = OrderExecution(
            position_id=pos_id,
            order_type=OrderType.BUY,
            mode=ExecutionMode.PAPER,
            price=execution_price,
            amount=tokens_received,
            total_usd=amount_usd,
            tx_hash=f"paper_buy_{token.address[:8]}_{datetime.now().timestamp()}",
            fee_cost_usd=Decimal("0.005"),  # Taxa estimada de rede
            slippage_realized=float(slippage_ratio * Decimal("100")),
            notes="Execução simulada Paper Trading",
        )
        await self.orders_repo.record_order(order)

        logger.info(
            "COMPRA PAPER EXECUTADA! Token: %s | Preço: $%.6f | Qtd: %.2f | Alocado: $%.2f | Saldo Restante: $%.2f",
            token.address,
            execution_price,
            tokens_received,
            amount_usd,
            self.balance_usd,
            extra={
                "event": "PAPER_BUY_FILLED",
                "token_address": token.address,
                "price": str(execution_price),
            },
        )
        return position

    async def execute_sell(
        self,
        position: PositionState,
        amount_tokens: Decimal,
        reason: str,
        execution_price: Decimal,
    ) -> OrderExecution | None:
        """Simula venda a mercado e atualiza saldo virtual e banco de dados."""
        if position.id is None:
            raise ValueError("Posição sem ID registrado.")

        await asyncio.sleep(self.simulated_latency_ms / 1000.0)

        gross_usd = amount_tokens * execution_price

        # Credita saldo virtual
        self.balance_usd += gross_usd

        if reason == "BREAK_EVEN":
            order_type = OrderType.TAKE_PROFIT_PARTIAL
        elif reason == "EMERGENCY_STOP":
            order_type = OrderType.EMERGENCY_EXIT
        else:
            order_type = OrderType.TRAILING_STOP_EXIT

        order = OrderExecution(
            position_id=position.id,
            order_type=order_type,
            mode=ExecutionMode.PAPER,
            price=execution_price,
            amount=amount_tokens,
            total_usd=gross_usd,
            tx_hash=f"paper_sell_{position.token_address[:8]}_{datetime.now().timestamp()}",
            fee_cost_usd=Decimal("0.005"),
            slippage_realized=0.5,
            notes=f"Venda simulada Paper Trading motivo: {reason}",
        )
        await self.orders_repo.record_order(order)

        logger.info(
            "VENDA PAPER EXECUTADA (%s)! Token: %s | Qtd: %.2f | Preço: $%.6f | Total: $%.2f | Novo Saldo: $%.2f",
            reason,
            position.token_address,
            amount_tokens,
            execution_price,
            gross_usd,
            self.balance_usd,
            extra={
                "event": "PAPER_SELL_FILLED",
                "token_address": position.token_address,
                "reason": reason,
            },
        )
        return order
