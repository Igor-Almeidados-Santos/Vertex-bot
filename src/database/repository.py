"""
Repositórios de Dados (Repository Pattern) do Vertex-bot.
Isola a camada de persistência e expõe operações de domínio atômicas.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from src.database.connection import DatabaseManager
from src.database.models import (
    ExecutionMode,
    OrderExecution,
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

    async def get_by_address(self, address: str) -> dict[str, Any] | None:
        """Busca registro de token por endereço."""
        query = "SELECT * FROM tokens_catalogados WHERE address = ?"
        row = await self.db.fetchone(query, (address,))
        return dict(row) if row else None

    async def get_recent_tokens(
        self,
        limit: int = 100,
        status_filter: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retorna lista dos tokens mais recentes com filtros opcionais."""
        conditions: list[str] = []
        params: list[Any] = []

        if status_filter and status_filter.upper() != "ALL":
            conditions.append("security_status = ?")
            params.append(status_filter.upper())

        if search:
            search_pattern = f"%{search}%"
            conditions.append("(address LIKE ? OR symbol LIKE ? OR name LIKE ?)")
            params.extend([search_pattern, search_pattern, search_pattern])

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
        SELECT address, symbol, name, chain, dex, pool_address,
               initial_liquidity_usd, detection_timestamp,
               security_status, security_score, rejection_reason
        FROM tokens_catalogados
        {where_clause}
        ORDER BY detection_timestamp DESC
        LIMIT ?
        """
        params.append(limit)
        rows = await self.db.fetchall(query, tuple(params))
        return [dict(r) for r in rows]

    async def get_tokens_summary(self) -> dict[str, Any]:
        """Calcula métricas agregadas de triagem de tokens."""
        total_query = "SELECT COUNT(*) as count FROM tokens_catalogados"
        approved_query = "SELECT COUNT(*) as count FROM tokens_catalogados WHERE security_status = 'APPROVED'"
        rejected_query = "SELECT COUNT(*) as count FROM tokens_catalogados WHERE security_status = 'REJECTED'"
        reasons_query = """
        SELECT rejection_reason, COUNT(*) as count
        FROM tokens_catalogados
        WHERE rejection_reason IS NOT NULL AND rejection_reason != ''
        GROUP BY rejection_reason
        ORDER BY count DESC
        LIMIT 10
        """

        total_row = await self.db.fetchone(total_query)
        appr_row = await self.db.fetchone(approved_query)
        rej_row = await self.db.fetchone(rejected_query)
        reasons_rows = await self.db.fetchall(reasons_query)

        total = total_row[0] if total_row else 0
        approved = appr_row[0] if appr_row else 0
        rejected = rej_row[0] if rej_row else 0
        reasons_dict = {str(row[0]): int(row[1]) for row in reasons_rows}

        return {
            "total_scanned": total,
            "approved": approved,
            "rejected": rejected,
            "approval_rate_pct": round((approved / total * 100.0), 1) if total > 0 else 0.0,
            "rejection_reasons": reasons_dict,
        }


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
            datetime.now(UTC).isoformat(),
            position_id,
        )
        await self.db.execute(query, params)

    async def get_open_positions(self) -> list[PositionState]:
        """Carrega todas as posições em aberto ou parcialmente fechadas."""
        query = "SELECT * FROM posicoes WHERE status IN ('OPEN', 'PARTIALLY_CLOSED')"
        rows = await self.db.fetchall(query)
        positions: list[PositionState] = []
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

    async def get_all_positions(self, limit: int = 100) -> list[dict[str, Any]]:
        """Retorna todas as posições registradas com metadados do token (symbol e name)."""
        query = """
        SELECT p.id, p.token_address, p.status, p.mode, p.entry_price, p.initial_token_amount,
               p.remaining_token_amount, p.allocated_capital_usd, p.realized_pnl_usd,
               p.highest_price_seen, p.break_even_triggered, p.trailing_stop_price,
               p.opened_at, p.closed_at,
               t.symbol, t.name
        FROM posicoes p
        LEFT JOIN tokens_catalogados t ON p.token_address = t.address
        ORDER BY p.id DESC
        LIMIT ?
        """
        rows = await self.db.fetchall(query, (limit,))
        return [dict(r) for r in rows]

    async def get_pnl_summary(self) -> dict[str, Any]:
        """Calcula métricas financeiras agregadas de PnL e taxa de acerto."""
        query = """
        SELECT
            COUNT(*) as total_positions,
            SUM(CASE WHEN status IN ('OPEN', 'PARTIALLY_CLOSED') THEN 1 ELSE 0 END) as active_positions,
            SUM(CASE WHEN status IN ('CLOSED', 'STOPPED') THEN 1 ELSE 0 END) as closed_positions,
            SUM(CASE WHEN status IN ('CLOSED', 'STOPPED') AND realized_pnl_usd > 0 THEN 1 ELSE 0 END) as win_positions,
            SUM(CASE WHEN break_even_triggered = 1 THEN 1 ELSE 0 END) as break_evens,
            SUM(realized_pnl_usd) as total_pnl_usd,
            SUM(allocated_capital_usd) as total_allocated_usd
        FROM posicoes
        """
        row = await self.db.fetchone(query)
        if not row:
            return {
                "total_positions": 0,
                "active_positions": 0,
                "closed_positions": 0,
                "win_positions": 0,
                "win_rate_pct": 0.0,
                "break_evens": 0,
                "total_pnl_usd": 0.0,
                "total_allocated_usd": 0.0,
            }

        total_pos = int(row[0] or 0)
        active_pos = int(row[1] or 0)
        closed_pos = int(row[2] or 0)
        win_pos = int(row[3] or 0)
        break_evens = int(row[4] or 0)
        total_pnl = float(row[5] or 0.0)
        total_allocated = float(row[6] or 0.0)
        win_rate = round((win_pos / closed_pos * 100.0), 1) if closed_pos > 0 else 0.0

        initial_wallet = 5.0
        current_equity = initial_wallet + total_pnl

        return {
            "total_positions": total_pos,
            "active_positions": active_pos,
            "closed_positions": closed_pos,
            "win_positions": win_pos,
            "win_rate_pct": win_rate,
            "break_evens": break_evens,
            "total_pnl_usd": round(total_pnl, 2),
            "total_allocated_usd": round(total_allocated, 2),
            "initial_wallet_usd": round(initial_wallet, 2),
            "current_equity_usd": round(current_equity, 2),
        }


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
            object.__setattr__(order, "id", order_id)
            return order_id
        except Exception as exc:
            raise DatabaseError(f"Erro ao registrar ordem para posição {order.position_id}: {exc}") from exc

    async def get_recent_orders(self, limit: int = 200) -> list[dict[str, Any]]:
        """Retorna histórico cronológico de execuções de ordens."""
        query = """
        SELECT id, position_id, order_type, mode, price, amount, total_usd,
               tx_hash, fee_cost_usd, slippage_realized, notes, executed_at
        FROM ordens_executadas
        ORDER BY id DESC
        LIMIT ?
        """
        rows = await self.db.fetchall(query, (limit,))
        return [dict(r) for r in rows]

    async def get_orders_by_position(self, position_id: int) -> list[dict[str, Any]]:
        """Retorna todas as ordens vinculadas a uma posição específica."""
        query = """
        SELECT id, position_id, order_type, mode, price, amount, total_usd,
               tx_hash, fee_cost_usd, slippage_realized, notes, executed_at
        FROM ordens_executadas
        WHERE position_id = ?
        ORDER BY id ASC
        """
        rows = await self.db.fetchall(query, (position_id,))
        return [dict(r) for r in rows]
