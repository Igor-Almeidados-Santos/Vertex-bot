"""
Repositórios de Dados (Repository Pattern) do Vertex-bot.
Isola a camada de persistência e expõe operações de domínio atômicas.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, List, Optional

from src.database.connection import DatabaseManager
from src.database.models import (
    ExecutionMode,
    OrderExecution,
    OrderType,
    PositionState,
    PositionStatus,
    SecurityAuditResult,
    SecurityStatus,
    TokenMetadata,
)
from src.utils.exceptions import DatabaseError


class TokensRepository:
    """Repositório de tokens catalogados e laudos de segurança."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db: DatabaseManager = db

    async def save_detected_token(self, token: TokenMetadata) -> int:
        """Salva novo token detectado com status inicial PENDING."""
        query = """
        INSERT INTO tokens_catalogados (
            address, symbol, name, chain, dex, pool_address, 
            initial_liquidity_usd, detection_timestamp, security_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(address) DO UPDATE SET
            initial_liquidity_usd = excluded.initial_liquidity_usd
        """
        params = (
            token.address,
            token.symbol,
            token.name,
            token.chain,
            token.dex,
            token.pool_address,
            float(token.initial_liquidity_usd),
            token.detection_timestamp.isoformat(),
            SecurityStatus.PENDING.value,
        )
        try:
            return await self.db.execute(query, params)
        except Exception as exc:
            raise DatabaseError(f"Erro ao salvar token detectado {token.address}: {exc}") from exc

    async def update_audit_result(self, audit: SecurityAuditResult) -> None:
        """Atualiza o laudo de segurança de um token."""
        query = """
        UPDATE tokens_catalogados
        SET security_status = ?,
            security_score = ?,
            rejection_reason = ?,
            audit_details_json = ?
        WHERE address = ?
        """
        audit_json = (
            audit.model_dump_json()
            if hasattr(audit, "model_dump_json")
            else json.dumps(audit.to_dict())
        )
        params = (
            audit.status.value,
            audit.security_score,
            audit.rejection_reason,
            audit_json,
            audit.token_address,
        )
        try:
            await self.db.execute(query, params)
        except Exception as exc:
            raise DatabaseError(f"Erro ao atualizar auditoria do token {audit.token_address}: {exc}") from exc

    async def get_by_address(self, address: str) -> Optional[dict[str, Any]]:
        """Busca registro de token por endereço."""
        query = "SELECT * FROM tokens_catalogados WHERE address = ?"
        row = await self.db.fetchone(query, (address,))
        return dict(row) if row else None


class PositionsRepository:
    """Repositório para posições abertas e gerenciamento de risco."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db: DatabaseManager = db

    async def create_position(self, pos: PositionState) -> int:
        """Registra abertura de uma nova posição financeira."""
        query = """
        INSERT INTO posicoes (
            token_address, status, mode, entry_price, initial_token_amount,
            remaining_token_amount, allocated_capital_usd, realized_pnl_usd,
            highest_price_seen, break_even_triggered, trailing_stop_price, opened_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            pos.token_address,
            pos.status.value,
            pos.mode.value,
            float(pos.entry_price),
            float(pos.initial_token_amount),
            float(pos.remaining_token_amount),
            float(pos.allocated_capital_usd),
            float(pos.realized_pnl_usd),
            float(pos.highest_price_seen),
            1 if pos.break_even_triggered else 0,
            float(pos.trailing_stop_price),
            pos.opened_at.isoformat(),
        )
        try:
            pos_id = await self.db.execute(query, params)
            pos.id = pos_id
            return pos_id
        except Exception as exc:
            raise DatabaseError(f"Erro ao criar posição para {pos.token_address}: {exc}") from exc

    async def update_tracking(
        self,
        position_id: int,
        highest_price: Decimal,
        trailing_stop_price: Decimal,
    ) -> None:
        """Atualiza as máximas de preço e o valor do trailing stop."""
        query = """
        UPDATE posicoes
        SET highest_price_seen = ?,
            trailing_stop_price = ?
        WHERE id = ?
        """
        params = (float(highest_price), float(trailing_stop_price), position_id)
        await self.db.execute(query, params)

    async def mark_break_even(
        self,
        position_id: int,
        remaining_amount: Decimal,
        realized_pnl: Decimal,
    ) -> None:
        """Registra a execução da venda parcial de Break-Even."""
        query = """
        UPDATE posicoes
        SET status = ?,
            remaining_token_amount = ?,
            realized_pnl_usd = ?,
            break_even_triggered = 1
        WHERE id = ?
        """
        params = (
            PositionStatus.PARTIALLY_CLOSED.value,
            float(remaining_amount),
            float(realized_pnl),
            position_id,
        )
        await self.db.execute(query, params)

    async def close_position(
        self,
        position_id: int,
        final_pnl: Decimal,
        status: PositionStatus = PositionStatus.CLOSED,
    ) -> None:
        """Encerra a posição no banco de dados com registro do PnL final."""
        query = """
        UPDATE posicoes
        SET status = ?,
            remaining_token_amount = 0.0,
            realized_pnl_usd = ?,
            closed_at = ?
        WHERE id = ?
        """
        params = (
            status.value,
            float(final_pnl),
            datetime.now(timezone.utc).isoformat(),
            position_id,
        )
        await self.db.execute(query, params)

    async def get_open_positions(self) -> List[PositionState]:
        """Carrega todas as posições em aberto ou parcialmente fechadas."""
        query = "SELECT * FROM posicoes WHERE status IN ('OPEN', 'PARTIALLY_CLOSED')"
        rows = await self.db.fetchall(query)
        positions: List[PositionState] = []
        for r in rows:
            pos_dict = dict(r)
            pos = PositionState(
                id=pos_dict["id"],
                token_address=pos_dict["token_address"],
                status=PositionStatus(pos_dict["status"]),
                mode=ExecutionMode(pos_dict["mode"]),
                entry_price=Decimal(str(pos_dict["entry_price"])),
                initial_token_amount=Decimal(str(pos_dict["initial_token_amount"])),
                remaining_token_amount=Decimal(str(pos_dict["remaining_token_amount"])),
                allocated_capital_usd=Decimal(str(pos_dict["allocated_capital_usd"])),
                realized_pnl_usd=Decimal(str(pos_dict["realized_pnl_usd"])),
                highest_price_seen=Decimal(str(pos_dict["highest_price_seen"])),
                break_even_triggered=bool(pos_dict["break_even_triggered"]),
                trailing_stop_price=Decimal(str(pos_dict["trailing_stop_price"])),
                opened_at=datetime.fromisoformat(pos_dict["opened_at"]),
            )
            positions.append(pos)
        return positions


class OrdersRepository:
    """Repositório imutável para auditoria de ordens de trade."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db: DatabaseManager = db

    async def record_order(self, order: OrderExecution) -> int:
        """Insere um novo registro imutável de execução."""
        query = """
        INSERT INTO ordens_executadas (
            position_id, order_type, mode, price, amount, total_usd,
            tx_hash, fee_cost_usd, slippage_realized, notes, executed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            order.position_id,
            order.order_type.value,
            order.mode.value,
            float(order.price),
            float(order.amount),
            float(order.total_usd),
            order.tx_hash,
            float(order.fee_cost_usd),
            order.slippage_realized,
            order.notes,
            order.executed_at.isoformat(),
        )
        try:
            order_id = await self.db.execute(query, params)
            order.id = order_id
            return order_id
        except Exception as exc:
            raise DatabaseError(f"Erro ao registrar ordem para posição {order.position_id}: {exc}") from exc
