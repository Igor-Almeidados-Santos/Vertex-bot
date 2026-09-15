"""
Modelos de Domínio e Estado em Memória do Vertex-bot.
Compatível com Pydantic v2 (context/DATA_SCHEMA.md).
"""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SecurityStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PositionStatus(StrEnum):
    OPEN = "OPEN"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"
    STOPPED = "STOPPED"


class OrderType(StrEnum):
    BUY = "BUY"
    TAKE_PROFIT_PARTIAL = "TAKE_PROFIT_PARTIAL"
    TRAILING_STOP_EXIT = "TRAILING_STOP_EXIT"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"


class ExecutionMode(StrEnum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class TokenMetadata(BaseModel):
    """Metadados de um token detectado pelo Scanner On-Chain."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    address: str
    chain: str = "solana"
    dex: str = "raydium"
    pool_address: str | None = None
    initial_liquidity_usd: Decimal = Decimal("0.0")
    symbol: str | None = None
    name: str | None = None
    detection_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_event: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _fill_defaults(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "detection_timestamp" in data and data["detection_timestamp"] is None:
                data["detection_timestamp"] = datetime.now(UTC)
            if "raw_event" in data and data["raw_event"] is None:
                data["raw_event"] = {}
        return data

    def to_dict(self) -> dict[str, Any]:
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


class SecurityAuditResult(BaseModel):
    """Laudo detalhado de auditoria de segurança e triagem (Hard Gates)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

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
    rejection_reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _fill_defaults(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "details" in data and data["details"] is None:
                data["details"] = {}
        return data

    @property
    def is_approved(self) -> bool:
        return self.status == SecurityStatus.APPROVED

    def to_dict(self) -> dict[str, Any]:
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
            "details": self.details,
        }


class PositionState(BaseModel):
    """Estado mutável de uma posição ativa ou encerrada."""

    model_config = ConfigDict(frozen=False, arbitrary_types_allowed=True)

    token_address: str
    mode: ExecutionMode
    entry_price: Decimal
    initial_token_amount: Decimal
    allocated_capital_usd: Decimal
    trailing_drop_pct: Decimal = Decimal("0.12")
    strategy_type: str = "SCALP"
    ratchet_tier: int = 0
    ratchet_floor_price: Decimal = Decimal("0.0")
    id: int | None = None
    status: PositionStatus = PositionStatus.OPEN
    remaining_token_amount: Decimal = Decimal("0.0")
    realized_pnl_usd: Decimal = Decimal("0.0")
    highest_price_seen: Decimal = Decimal("0.0")
    current_price: Decimal | None = None
    break_even_triggered: bool = False
    trailing_stop_price: Decimal = Decimal("0.0")
    last_hourly_eval_hour: int = 0
    hourly_peak_price: Decimal = Decimal("0.0")
    opened_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _set_dynamic_defaults(cls, data: Any) -> Any:
        if isinstance(data, dict):
            cls._apply_position_defaults(data)
        return data

    @classmethod
    def _apply_position_defaults(cls, data: dict[str, Any]) -> None:
        init_amount = data.get("initial_token_amount")
        if data.get("remaining_token_amount") is None and init_amount is not None:
            data["remaining_token_amount"] = init_amount

        entry = data.get("entry_price")
        if data.get("highest_price_seen") is None and entry is not None:
            data["highest_price_seen"] = entry

        if data.get("current_price") is None and entry is not None:
            data["current_price"] = entry

        strat = data.get("strategy_type", "SCALP")
        data.setdefault("strategy_type", strat)

        if data.get("trailing_stop_price") is None:
            if strat == "SWING":
                data["trailing_stop_price"] = Decimal("0.0")
            elif entry is not None:
                drop = Decimal(str(data.get("trailing_drop_pct", "0.12")))
                entry_dec = Decimal(str(entry))
                data["trailing_stop_price"] = entry_dec * (Decimal("1.0") - drop)

        data.setdefault("ratchet_tier", 0)
        floor = data.setdefault("ratchet_floor_price", Decimal("0.0"))
        if not isinstance(floor, Decimal):
            data["ratchet_floor_price"] = Decimal(str(floor))

        data.setdefault("last_hourly_eval_hour", 0)
        h_peak = data.setdefault("hourly_peak_price", entry if entry is not None else Decimal("0.0"))
        if not isinstance(h_peak, Decimal):
            data["hourly_peak_price"] = Decimal(str(h_peak))

        if data.get("opened_at") is None:
            data["opened_at"] = datetime.now(UTC)

    @property
    def unrealized_pnl_usd(self) -> Decimal:
        """Calcula o lucro/prejuízo flutuante em USD com precisão Decimal."""
        curr = self.current_price or self.highest_price_seen or self.entry_price
        if curr is None or self.entry_price <= Decimal("0.0"):
            return Decimal("0.0")
        return (curr - self.entry_price) * self.remaining_token_amount

    @property
    def roi_pct(self) -> Decimal:
        """Calcula o retorno percentual flutuante (ROI %) atual."""
        curr = self.current_price or self.highest_price_seen or self.entry_price
        if curr is None or self.entry_price <= Decimal("0.0"):
            return Decimal("0.0")
        return ((curr - self.entry_price) / self.entry_price) * Decimal("100.0")

    def elapsed_seconds(self, now_utc: datetime | None = None) -> float:
        """Retorna o tempo decorrido em segundos desde a abertura da posição."""
        ref = now_utc or datetime.now(UTC)
        opened = self.opened_at
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=UTC)
        return max(0.0, (ref - opened).total_seconds())

    def elapsed_hours(self, now_utc: datetime | None = None) -> float:
        """Retorna o tempo decorrido em horas desde a abertura da posição."""
        return self.elapsed_seconds(now_utc) / 3600.0

    def promote_ratchet_tier(self, new_tier: int, new_floor_price: Decimal) -> bool:
        """Promove o degrau da catraca e eleva o piso protegido monotonicamente."""
        promoted = False
        if new_tier > self.ratchet_tier:
            self.ratchet_tier = new_tier
            promoted = True
        if new_floor_price > self.ratchet_floor_price:
            self.ratchet_floor_price = new_floor_price
            promoted = True
        return promoted

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

        remaining_cost_basis = (
            Decimal("0.0") if self.break_even_triggered else self.allocated_capital_usd
        )
        pnl_usd = revenue_usd - remaining_cost_basis

        self.remaining_token_amount = Decimal("0.0")
        self.realized_pnl_usd += pnl_usd
        self.status = PositionStatus.CLOSED if reason != "STOPPED" else PositionStatus.STOPPED
        self.closed_at = datetime.now(UTC)
        return sell_amount

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class OrderExecution(BaseModel):
    """Registro contábil e imutável de execução de ordem."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    position_id: int
    order_type: OrderType
    mode: ExecutionMode
    price: Decimal
    amount: Decimal
    total_usd: Decimal
    id: int | None = None
    tx_hash: str | None = None
    fee_cost_usd: Decimal = Decimal("0.0")
    slippage_realized: float = 0.0
    notes: str | None = None
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="before")
    @classmethod
    def _fill_defaults(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "executed_at" in data and data["executed_at"] is None:
                data["executed_at"] = datetime.now(UTC)
        return data

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()
