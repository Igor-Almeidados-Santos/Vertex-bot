"""
Interface Abstrata do Motor de Execução (Strategy Pattern).
Garante contrato idêntico para Paper Trading e Live Trading.
"""

from abc import ABC, abstractmethod
from decimal import Decimal

from src.database.models import ExecutionMode, OrderExecution, PositionState, TokenMetadata


class IExecutionEngine(ABC):
    """Interface formal para motores de execução de ordens."""

    @property
    @abstractmethod
    def mode(self) -> ExecutionMode:
        """Retorna o modo de operação do motor (PAPER ou LIVE)."""
        pass

    @abstractmethod
    async def execute_buy(
        self,
        token: TokenMetadata,
        amount_usd: Decimal,
        strategy_type: str = "SCALP",
    ) -> PositionState | None:
        """Executa ordem de compra e inicializa o estado da posição aberta."""
        pass

    @abstractmethod
    async def execute_sell(
        self,
        position: PositionState,
        amount_tokens: Decimal,
        reason: str,
        execution_price: Decimal,
    ) -> OrderExecution | None:
        """Executa ordem de venda parcial ou total de uma posição aberta."""
        pass
