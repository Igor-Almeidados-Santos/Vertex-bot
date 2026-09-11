"""
Modelos de Domínio e Estado em Memória do Vertex-bot.
Compatível com Pydantic v2 (context/DATA_SCHEMA.md) e fallback nativo.
"""

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import json
from typing import Any, Dict, Optional

try:
    from pydantic import BaseModel, ConfigDict, Field

    HAS_PYDANTIC = True

    class _PydanticFrozenModel(BaseModel):
        model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

        def to_dict(self) -> Dict[str, Any]:
            return self.model_dump()

    class _PydanticMutableModel(BaseModel):
        model_config = ConfigDict(frozen=False, arbitrary_types_allowed=True)

        def to_dict(self) -> Dict[str, Any]:
            return self.model_dump()

except ImportError:
    HAS_PYDANTIC = False

    class _PydanticFrozenModel:  # type: ignore
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                object.__setattr__(self, k, v)

        def model_dump(self) -> Dict[str, Any]:
            return {
                k: (v.value if isinstance(v, Enum) else v)
                for k, v in self.__dict__.items()
                if not k.startswith("_")
            }

        def model_dump_json(self) -> str:
            def _serialize(obj: Any) -> Any:
                if isinstance(obj, (Decimal, datetime)):
                    return str(obj)
                if isinstance(obj, Enum):
                    return obj.value
                return obj

            return json.dumps(self.model_dump(), default=_serialize)

        def to_dict(self) -> Dict[str, Any]:
            return self.model_dump()

    class _PydanticMutableModel(_PydanticFrozenModel):  # type: ignore
        def __setattr__(self, name: str, value: Any) -> None:
            super().__setattr__(name, value)


class SecurityStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"
    STOPPED = "STOPPED"


class OrderType(str, Enum):
    BUY = "BUY"
    TAKE_PROFIT_PARTIAL = "TAKE_PROFIT_PARTIAL"
    TRAILING_STOP_EXIT = "TRAILING_STOP_EXIT"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"


class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class TokenMetadata(_PydanticFrozenModel):
    """Metadados de um token detectado pelo Scanner On-Chain."""

    address: str
    chain: str = "solana"
    dex: str = "raydium"
    pool_address: Optional[str] = None
    initial_liquidity_usd: Decimal = Decimal("0.0")
    symbol: Optional[str] = None
    name: Optional[str] = None
    detection_timestamp: Optional[datetime] = None
    raw_event: Optional[Dict[str, Any]] = None

    def __init__(
        self,
        address: str,
        chain: str = "solana",
        dex: str = "raydium",
        pool_address: Optional[str] = None,
        initial_liquidity_usd: Decimal = Decimal("0.0"),
        symbol: Optional[str] = None,
        name: Optional[str] = None,
        detection_timestamp: Optional[datetime] = None,
        raw_event: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            address=address,
            chain=chain,
            dex=dex,
            pool_address=pool_address,
            initial_liquidity_usd=initial_liquidity_usd,
            symbol=symbol,
            name=name,
            detection_timestamp=detection_timestamp or datetime.now(timezone.utc),
            raw_event=raw_event or {},
            **kwargs,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "address": self.address,
            "chain": self.chain,
            "dex": self.dex,
            "pool_address": self.pool_address,
            "initial_liquidity_usd": float(self.initial_liquidity_usd),
            "symbol": self.symbol,
            "name": self.name,
            "detection_timestamp": (
                self.detection_timestamp.isoformat() if self.detection_timestamp else ""
            ),
        }


class SecurityAuditResult(_PydanticFrozenModel):
    """Laudo detalhado de auditoria de segurança e triagem (Hard Gates)."""

    token_address: str
    status: SecurityStatus
    security_score: float
    is_mint_revoked: bool
    is_freeze_revoked: bool
    is_lp_burned_or_locked: bool
    lp_burn_percentage: float
    top10_holder_percentage: float
    is_honeypot: bool
    buy_tax_percentage: float
    sell_tax_percentage: float
    rejection_reason: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    def __init__(
        self,
        token_address: str,
        status: SecurityStatus,
        security_score: float,
        is_mint_revoked: bool,
        is_freeze_revoked: bool,
        is_lp_burned_or_locked: bool,
        lp_burn_percentage: float,
        top10_holder_percentage: float,
        is_honeypot: bool,
        buy_tax_percentage: float,
        sell_tax_percentage: float,
        rejection_reason: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            token_address=token_address,
            status=status,
            security_score=security_score,
            is_mint_revoked=is_mint_revoked,
            is_freeze_revoked=is_freeze_revoked,
            is_lp_burned_or_locked=is_lp_burned_or_locked,
            lp_burn_percentage=lp_burn_percentage,
            top10_holder_percentage=top10_holder_percentage,
            is_honeypot=is_honeypot,
            buy_tax_percentage=buy_tax_percentage,
            sell_tax_percentage=sell_tax_percentage,
            rejection_reason=rejection_reason,
            details=details or {},
            **kwargs,
        )

    @property
    def is_approved(self) -> bool:
        return self.status == SecurityStatus.APPROVED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "token_address": self.token_address,
            "status": self.status.value,
            "security_score": self.security_score,
            "is_mint_revoked": self.is_mint_revoked,
            "is_freeze_revoked": self.is_freeze_revoked,
            "is_lp_burned_or_locked": self.is_lp_burned_or_locked,
            "lp_burn_percentage": self.lp_burn_percentage,
            "top10_holder_percentage": self.top10_holder_percentage,
            "is_honeypot": self.is_honeypot,
            "buy_tax_percentage": self.buy_tax_percentage,
            "sell_tax_percentage": self.sell_tax_percentage,
            "rejection_reason": self.rejection_reason,
            "details": self.details or {},
        }


class PositionState(_PydanticMutableModel):
    """Estado mutável de uma posição ativa ou encerrada."""

    token_address: str
    mode: ExecutionMode
    entry_price: Decimal
    initial_token_amount: Decimal
    allocated_capital_usd: Decimal
    trailing_drop_pct: Decimal = Decimal("0.12")
    id: Optional[int] = None
    status: PositionStatus = PositionStatus.OPEN
    remaining_token_amount: Decimal = Decimal("0.0")
    realized_pnl_usd: Decimal = Decimal("0.0")
    highest_price_seen: Decimal = Decimal("0.0")
    break_even_triggered: bool = False
    trailing_stop_price: Decimal = Decimal("0.0")
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    def __init__(
        self,
        token_address: str,
        mode: ExecutionMode,
        entry_price: Decimal,
        initial_token_amount: Decimal,
        allocated_capital_usd: Decimal,
        trailing_drop_pct: Decimal = Decimal("0.12"),
        id: Optional[int] = None,
        status: PositionStatus = PositionStatus.OPEN,
        remaining_token_amount: Optional[Decimal] = None,
        realized_pnl_usd: Decimal = Decimal("0.0"),
        highest_price_seen: Optional[Decimal] = None,
        break_even_triggered: bool = False,
        trailing_stop_price: Optional[Decimal] = None,
        opened_at: Optional[datetime] = None,
        closed_at: Optional[datetime] = None,
        **kwargs: Any,
    ) -> None:
        rem = remaining_token_amount if remaining_token_amount is not None else initial_token_amount
        high = highest_price_seen if highest_price_seen is not None else entry_price
        stop = (
            trailing_stop_price
            if trailing_stop_price is not None
            else entry_price * (Decimal("1.0") - trailing_drop_pct)
        )
        super().__init__(
            token_address=token_address,
            mode=mode,
            entry_price=entry_price,
            initial_token_amount=initial_token_amount,
            allocated_capital_usd=allocated_capital_usd,
            trailing_drop_pct=trailing_drop_pct,
            id=id,
            status=status,
            remaining_token_amount=rem,
            realized_pnl_usd=realized_pnl_usd,
            highest_price_seen=high,
            break_even_triggered=break_even_triggered,
            trailing_stop_price=stop,
            opened_at=opened_at or datetime.now(timezone.utc),
            closed_at=closed_at,
            **kwargs,
        )

    def update_price_and_trailing_stop(self, current_price: Decimal) -> None:
        """Atualiza a máxima histórica registrada e eleva o trailing stop dinâmico."""
        if current_price > self.highest_price_seen:
            self.highest_price_seen = current_price
            self.trailing_stop_price = current_price * (Decimal("1.0") - self.trailing_drop_pct)

    def update_high_and_stop(self, current_price: Decimal, trailing_drop_pct: Decimal) -> None:
        """Compatibilidade direta com DATA_SCHEMA.md."""
        self.trailing_drop_pct = trailing_drop_pct
        self.update_price_and_trailing_stop(current_price)

    def trigger_break_even(self, execution_price: Decimal) -> Decimal:
        """
        Executa a venda de 50% dos tokens originais para retorno de 100% do capital inicial.
        Retorna a quantidade vendida.
        """
        if self.break_even_triggered:
            raise ValueError("Break-even já foi acionado nesta posição.")

        sell_amount = self.initial_token_amount / Decimal("2.0")
        if sell_amount > self.remaining_token_amount:
            sell_amount = self.remaining_token_amount

        revenue_usd = sell_amount * execution_price
        cost_basis_sold = self.allocated_capital_usd / Decimal("2.0")
        pnl_usd = revenue_usd - cost_basis_sold

        self.remaining_token_amount -= sell_amount
        self.realized_pnl_usd += pnl_usd
        self.break_even_triggered = True
        self.status = PositionStatus.PARTIALLY_CLOSED
        return sell_amount

    def close_position(self, execution_price: Decimal, reason: str = "TRAILING_STOP") -> Decimal:
        """Encerra 100% dos tokens remanescentes e computa o PnL final."""
        sell_amount = self.remaining_token_amount
        revenue_usd = sell_amount * execution_price

        # Se o break-even já ocorreu, o restante é lucro puro (custo base remanescente = 0)
        # Se não ocorreu, o custo base remanescente é o capital inicial
        remaining_cost_basis = (
            Decimal("0.0") if self.break_even_triggered else self.allocated_capital_usd
        )
        pnl_usd = revenue_usd - remaining_cost_basis

        self.remaining_token_amount = Decimal("0.0")
        self.realized_pnl_usd += pnl_usd
        self.status = PositionStatus.CLOSED if reason != "STOPPED" else PositionStatus.STOPPED
        self.closed_at = datetime.now(timezone.utc)
        return sell_amount


class OrderExecution(_PydanticFrozenModel):
    """Registro contábil e imutável de execução de ordem."""

    position_id: int
    order_type: OrderType
    mode: ExecutionMode
    price: Decimal
    amount: Decimal
    total_usd: Decimal
    id: Optional[int] = None
    tx_hash: Optional[str] = None
    fee_cost_usd: Decimal = Decimal("0.0")
    slippage_realized: float = 0.0
    notes: Optional[str] = None
    executed_at: Optional[datetime] = None

    def __init__(
        self,
        position_id: int,
        order_type: OrderType,
        mode: ExecutionMode,
        price: Decimal,
        amount: Decimal,
        total_usd: Decimal,
        id: Optional[int] = None,
        tx_hash: Optional[str] = None,
        fee_cost_usd: Decimal = Decimal("0.0"),
        slippage_realized: float = 0.0,
        notes: Optional[str] = None,
        executed_at: Optional[datetime] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            position_id=position_id,
            order_type=order_type,
            mode=mode,
            price=price,
            amount=amount,
            total_usd=total_usd,
            id=id,
            tx_hash=tx_hash,
            fee_cost_usd=fee_cost_usd,
            slippage_realized=slippage_realized,
            notes=notes,
            executed_at=executed_at or datetime.now(timezone.utc),
            **kwargs,
        )
