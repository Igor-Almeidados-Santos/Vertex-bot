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
from src.utils.logger import setup_logger

logger = setup_logger("vertex.database.repository")


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
            initial_liquidity_usd = excluded.initial_liquidity_usd,
            detection_timestamp = excluded.detection_timestamp
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

    async def update_token_metadata(
        self, address: str, symbol: str | None = None, name: str | None = None
    ) -> None:
        """Atualiza symbol e name de um token catalogado caso ainda não estejam definidos."""
        if not symbol and not name:
            return
        query = """
        UPDATE tokens_catalogados
        SET symbol = COALESCE(?, symbol),
            name = COALESCE(?, name)
        WHERE address = ?
        """
        try:
            await self.db.execute(query, (symbol, name, address))
        except Exception as exc:
            logger.debug("Falha ao enriquecer metadados do token %s: %s", address, exc)

    async def get_by_address(self, address: str) -> dict[str, Any] | None:
        """Busca registro de token por endereço."""
        query = "SELECT * FROM tokens_catalogados WHERE address = ?"
        row = await self.db.fetchone(query, (address,))
        return dict(row) if row else None

    async def get_token_metadata_by_address(self, address: str) -> TokenMetadata | None:
        """Busca registro de token por endereço e converte para TokenMetadata."""
        row = await self.get_by_address(address)
        if not row:
            return None
        return TokenMetadata(
            address=row["address"],
            chain=row.get("chain") or "solana",
            dex=row.get("dex") or "raydium",
            pool_address=row.get("pool_address"),
            initial_liquidity_usd=Decimal(str(row.get("initial_liquidity_usd") or "0.0")),
            symbol=row.get("symbol"),
            name=row.get("name"),
            detection_timestamp=datetime.now(UTC),
        )

    async def get_recent_tokens(
        self,
        limit: int = 100,
        status_filter: str | None = None,
        search: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Retorna lista dos tokens mais recentes com filtros opcionais de status, busca e timestamp."""
        conditions: list[str] = []
        params: list[Any] = []

        if status_filter and status_filter.upper() != "ALL":
            status_map = {
                "APROVADO": "APPROVED",
                "REJEITADO": "REJECTED",
                "PENDENTE": "PENDING",
            }
            norm_status = status_map.get(status_filter.upper(), status_filter.upper())
            conditions.append("security_status = ?")
            params.append(norm_status)

        if search:
            search_pattern = f"%{search}%"
            conditions.append("(address LIKE ? OR symbol LIKE ? OR name LIKE ?)")
            params.extend([search_pattern, search_pattern, search_pattern])

        if since is not None:
            conditions.append("detection_timestamp >= ?")
            params.append(since.isoformat())

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
        tokens: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["status"] = d.get("security_status") or "PENDING"
            d["cataloged_at"] = d.get("detection_timestamp") or ""
            d["score"] = d.get("security_score")
            tokens.append(d)
        return tokens

    async def get_approved_watchlist_candidates(
        self,
        exclude_addresses: list[str] | None = None,
        limit: int = 50,
    ) -> list[TokenMetadata]:
        """Retorna tokens com status APPROVED elegíveis para entrada ou reentrada na watchlist ativa."""
        conditions: list[str] = ["security_status = 'APPROVED'"]
        params: list[Any] = []

        if exclude_addresses:
            placeholders = ",".join("?" for _ in exclude_addresses)
            conditions.append(f"address NOT IN ({placeholders})")
            params.extend(exclude_addresses)

        where_clause = f"WHERE {' AND '.join(conditions)}"
        query = f"""
        SELECT address, symbol, name, chain, dex, pool_address,
               initial_liquidity_usd, detection_timestamp
        FROM tokens_catalogados
        {where_clause}
        ORDER BY security_score DESC, detection_timestamp DESC
        LIMIT ?
        """
        params.append(limit)
        rows = await self.db.fetchall(query, tuple(params))
        candidates: list[TokenMetadata] = []
        for r in rows:
            row = dict(r)
            candidates.append(
                TokenMetadata(
                    address=row["address"],
                    chain=row.get("chain") or "solana",
                    dex=row.get("dex") or "raydium",
                    pool_address=row.get("pool_address"),
                    initial_liquidity_usd=Decimal(str(row.get("initial_liquidity_usd") or "0.0")),
                    symbol=row.get("symbol"),
                    name=row.get("name"),
                    detection_timestamp=datetime.now(UTC),
                )
            )
        return candidates


    async def get_tokens_summary(self, since: datetime | None = None) -> dict[str, Any]:
        """Calcula métricas agregadas de triagem de tokens, com suporte opcional a filtro de sessão."""
        all_time_total_query = "SELECT COUNT(*) as count FROM tokens_catalogados"
        all_time_appr_query = "SELECT COUNT(*) as count FROM tokens_catalogados WHERE security_status = 'APPROVED'"
        all_time_rej_query = "SELECT COUNT(*) as count FROM tokens_catalogados WHERE security_status = 'REJECTED'"

        all_time_row = await self.db.fetchone(all_time_total_query)
        all_time_appr_row = await self.db.fetchone(all_time_appr_query)
        all_time_rej_row = await self.db.fetchone(all_time_rej_query)

        all_time_total = all_time_row[0] if all_time_row else 0
        all_time_approved = all_time_appr_row[0] if all_time_appr_row else 0
        all_time_rejected = all_time_rej_row[0] if all_time_rej_row else 0

        since_iso = since.isoformat() if since is not None else None

        if since_iso:
            total_query = "SELECT COUNT(*) as count FROM tokens_catalogados WHERE detection_timestamp >= ?"
            approved_query = "SELECT COUNT(*) as count FROM tokens_catalogados WHERE security_status = 'APPROVED' AND detection_timestamp >= ?"
            rejected_query = "SELECT COUNT(*) as count FROM tokens_catalogados WHERE security_status = 'REJECTED' AND detection_timestamp >= ?"
            reasons_query = """
            SELECT rejection_reason, COUNT(*) as count
            FROM tokens_catalogados
            WHERE rejection_reason IS NOT NULL AND rejection_reason != '' AND detection_timestamp >= ?
            GROUP BY rejection_reason
            ORDER BY count DESC
            LIMIT 10
            """
            total_row = await self.db.fetchone(total_query, (since_iso,))
            appr_row = await self.db.fetchone(approved_query, (since_iso,))
            rej_row = await self.db.fetchone(rejected_query, (since_iso,))
            reasons_rows = await self.db.fetchall(reasons_query, (since_iso,))
            total = total_row[0] if total_row else 0
            approved = appr_row[0] if appr_row else 0
            rejected = rej_row[0] if rej_row else 0
        else:
            total = all_time_total
            approved = all_time_approved
            rejected = all_time_rejected
            reasons_query = """
            SELECT rejection_reason, COUNT(*) as count
            FROM tokens_catalogados
            WHERE rejection_reason IS NOT NULL AND rejection_reason != ''
            GROUP BY rejection_reason
            ORDER BY count DESC
            LIMIT 10
            """
            reasons_rows = await self.db.fetchall(reasons_query)

        reasons_dict = {str(row[0]): int(row[1]) for row in reasons_rows}

        return {
            "total_scanned": total,
            "approved": approved,
            "rejected": rejected,
            "total_approved": approved,
            "total_rejected": rejected,
            "all_time_cataloged": all_time_total,
            "all_time_approved": all_time_approved,
            "all_time_rejected": all_time_rejected,
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
            token_address, status, mode, strategy_type, entry_price, initial_token_amount,
            remaining_token_amount, allocated_capital_usd, realized_pnl_usd,
            highest_price_seen, break_even_triggered, trailing_stop_price,
            ratchet_tier, ratchet_floor_price, opened_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            pos.token_address,
            pos.status.value,
            pos.mode.value,
            pos.strategy_type,
            float(pos.entry_price),
            float(pos.initial_token_amount),
            float(pos.remaining_token_amount),
            float(pos.allocated_capital_usd),
            float(pos.realized_pnl_usd),
            float(pos.highest_price_seen),
            1 if pos.break_even_triggered else 0,
            float(pos.trailing_stop_price),
            pos.ratchet_tier,
            float(pos.ratchet_floor_price),
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
        ratchet_tier: int = 0,
        ratchet_floor_price: Decimal = Decimal("0.0"),
    ) -> None:
        """Atualiza as máximas de preço, o valor do trailing stop e os degraus de ratchet."""
        query = """
        UPDATE posicoes
        SET highest_price_seen = ?,
            trailing_stop_price = ?,
            ratchet_tier = ?,
            ratchet_floor_price = ?
        WHERE id = ?
        """
        params = (
            float(highest_price),
            float(trailing_stop_price),
            ratchet_tier,
            float(ratchet_floor_price),
            position_id,
        )
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

    async def clear_paper_trading_data(self) -> None:
        """Limpa posições e ordens do modo de simulação (PAPER) e reseta os contadores de ID."""
        query_orders = "DELETE FROM ordens_executadas WHERE mode = 'PAPER'"
        query_positions = "DELETE FROM posicoes WHERE mode = 'PAPER'"
        query_seq = "DELETE FROM sqlite_sequence WHERE name IN ('posicoes', 'ordens_executadas')"
        await self.db.execute(query_orders)
        await self.db.execute(query_positions)
        try:
            await self.db.execute(query_seq)
        except Exception:
            pass
        logger.info("Dados de simulação (PAPER) limpos com sucesso e IDs resetados para 1. Tokens catalogados preservados.")

    async def get_open_positions(self) -> list[PositionState]:
        """Carrega todas as posições em aberto ou parcialmente fechadas com tokens remanescentes."""
        query = "SELECT * FROM posicoes WHERE status IN ('OPEN', 'PARTIALLY_CLOSED') AND remaining_token_amount > 0"
        rows = await self.db.fetchall(query)
        positions: list[PositionState] = []
        for r in rows:
            pos_dict = dict(r)
            pos = PositionState(
                id=pos_dict["id"],
                token_address=pos_dict["token_address"],
                status=PositionStatus(pos_dict["status"]),
                mode=ExecutionMode(pos_dict["mode"]),
                strategy_type=pos_dict.get("strategy_type") or "SCALP",
                entry_price=Decimal(str(pos_dict["entry_price"])),
                initial_token_amount=Decimal(str(pos_dict["initial_token_amount"])),
                remaining_token_amount=Decimal(str(pos_dict["remaining_token_amount"])),
                allocated_capital_usd=Decimal(str(pos_dict["allocated_capital_usd"])),
                realized_pnl_usd=Decimal(str(pos_dict["realized_pnl_usd"])),
                highest_price_seen=Decimal(str(pos_dict["highest_price_seen"])),
                break_even_triggered=bool(pos_dict["break_even_triggered"]),
                trailing_stop_price=Decimal(str(pos_dict["trailing_stop_price"])),
                ratchet_tier=int(pos_dict.get("ratchet_tier") or 0),
                ratchet_floor_price=Decimal(str(pos_dict.get("ratchet_floor_price") or "0.0")),
                opened_at=datetime.fromisoformat(pos_dict["opened_at"]),
                closed_at=(
                    datetime.fromisoformat(pos_dict["closed_at"])
                    if pos_dict.get("closed_at")
                    else None
                ),
            )
            positions.append(pos)
        return positions

    async def get_all_positions(
        self,
        limit: int = 100,
        status_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retorna todas as posições registradas com metadados do token (symbol e name), com filtro opcional de status."""
        where_clause = ""
        params: list[Any] = []

        if status_filter:
            norm = status_filter.lower()
            if norm == "open":
                where_clause = "WHERE p.status IN ('OPEN', 'PARTIALLY_CLOSED') AND p.remaining_token_amount > 0"
            elif norm == "closed":
                where_clause = "WHERE (p.status IN ('CLOSED', 'STOPPED') OR p.remaining_token_amount <= 0)"
            else:
                where_clause = "WHERE p.status = ?"
                params.append(status_filter.upper())

        query = f"""
        SELECT p.id, p.token_address, p.status, p.mode, p.strategy_type, p.entry_price, p.initial_token_amount,
               p.remaining_token_amount, p.allocated_capital_usd, p.realized_pnl_usd,
               p.highest_price_seen, p.break_even_triggered, p.trailing_stop_price,
               p.ratchet_tier, p.ratchet_floor_price,
               p.opened_at, p.closed_at,
               t.symbol, t.name
        FROM posicoes p
        LEFT JOIN tokens_catalogados t ON p.token_address = t.address
        {where_clause}
        ORDER BY p.id DESC
        LIMIT ?
        """
        params.append(limit)
        rows = await self.db.fetchall(query, tuple(params))
        positions: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["address"] = d.get("token_address") or ""
            d["token_symbol"] = d.get("symbol") or ""
            positions.append(d)
        return positions

    async def get_pnl_summary(
        self,
        initial_wallet_usd: float = 5.0,
        current_cash_usd: float | None = None,
    ) -> dict[str, Any]:
        """Calcula métricas financeiras agregadas de PnL e taxa de acerto."""
        query = """
        SELECT
            COUNT(*) as total_positions,
            SUM(CASE WHEN status IN ('OPEN', 'PARTIALLY_CLOSED') THEN 1 ELSE 0 END) as active_positions,
            SUM(CASE WHEN status IN ('CLOSED', 'STOPPED') THEN 1 ELSE 0 END) as closed_positions,
            SUM(CASE WHEN status IN ('CLOSED', 'STOPPED') AND realized_pnl_usd > 0 THEN 1 ELSE 0 END) as win_positions,
            SUM(CASE WHEN break_even_triggered = 1 THEN 1 ELSE 0 END) as break_evens,
            SUM(realized_pnl_usd) as total_pnl_usd,
            SUM(allocated_capital_usd) as total_allocated_usd,
            SUM(CASE WHEN status IN ('OPEN', 'PARTIALLY_CLOSED') THEN allocated_capital_usd ELSE 0 END) as active_capital_usd
        FROM posicoes
        """
        row = await self.db.fetchone(query)
        if not row:
            cash = current_cash_usd if current_cash_usd is not None else initial_wallet_usd
            return {
                "total_positions": 0,
                "active_positions": 0,
                "closed_positions": 0,
                "win_positions": 0,
                "win_rate_pct": 0.0,
                "break_evens": 0,
                "total_pnl_usd": 0.0,
                "total_allocated_usd": 0.0,
                "active_capital_usd": 0.0,
                "initial_wallet_usd": initial_wallet_usd,
                "current_equity_usd": cash,
                "current_cash_usd": cash,
            }

        total_pos = int(row[0] or 0)
        active_pos = int(row[1] or 0)
        closed_pos = int(row[2] or 0)
        win_pos = int(row[3] or 0)
        break_evens = int(row[4] or 0)
        total_pnl = float(row[5] or 0.0)
        total_allocated = float(row[6] or 0.0)
        active_capital = float(row[7] or 0.0)
        win_rate = round((win_pos / closed_pos * 100.0), 1) if closed_pos > 0 else 0.0

        if current_cash_usd is not None:
            current_cash = max(0.0, current_cash_usd)
            current_equity = current_cash + active_capital
        else:
            current_equity = initial_wallet_usd + total_pnl
            current_cash = max(0.0, current_equity - active_capital)

        return {
            "total_positions": total_pos,
            "active_positions": active_pos,
            "closed_positions": closed_pos,
            "win_positions": win_pos,
            "win_rate_pct": win_rate,
            "break_evens": break_evens,
            "total_pnl_usd": round(total_pnl, 2),
            "total_allocated_usd": round(total_allocated, 2),
            "active_capital_usd": round(active_capital, 2),
            "initial_wallet_usd": round(initial_wallet_usd, 2),
            "current_equity_usd": round(current_equity, 2),
            "current_cash_usd": round(current_cash, 2),
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
        SELECT o.id, o.position_id, o.order_type, o.mode, o.price, o.amount, o.total_usd,
               o.tx_hash, o.fee_cost_usd, o.slippage_realized, o.notes, o.executed_at,
               p.token_address, t.symbol, t.name
        FROM ordens_executadas o
        LEFT JOIN posicoes p ON o.position_id = p.id
        LEFT JOIN tokens_catalogados t ON p.token_address = t.address
        ORDER BY o.id DESC
        LIMIT ?
        """
        rows = await self.db.fetchall(query, (limit,))
        orders: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["token_address"] = d.get("token_address") or ""
            d["token_symbol"] = d.get("symbol") or ""
            d["tokens_amount"] = d.get("amount") or 0.0
            d["amount_usd"] = d.get("total_usd") or 0.0
            d["timestamp"] = d.get("executed_at") or ""
            d["reason"] = d.get("notes") or ""
            orders.append(d)
        return orders

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
