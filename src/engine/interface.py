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

    @property
    def balance_usd(self) -> Decimal:
        """Saldo disponível em caixa/carteira em USD."""
        return getattr(self, "_balance_usd", Decimal("0.0"))

    @balance_usd.setter
    def balance_usd(self, value: Decimal) -> None:
        self._balance_usd = value

    @property
    def max_slippage_pct(self) -> Decimal:
        """Tolerância máxima de slippage."""
        return getattr(self, "_max_slippage_pct", Decimal("0.015"))

    @max_slippage_pct.setter
    def max_slippage_pct(self, value: Decimal) -> None:
        self._max_slippage_pct = value

    @property
    def trailing_drop_pct(self) -> Decimal:
        """Percentual de trailing drop padrão."""
        return getattr(self, "_trailing_drop_pct", Decimal("0.12"))

    @trailing_drop_pct.setter
    def trailing_drop_pct(self, value: Decimal) -> None:
        self._trailing_drop_pct = value

    async def get_wallet_balance_usd(self) -> Decimal:
        """Consulta o saldo real ou virtual de carteira em USD."""
        return self.balance_usd

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
