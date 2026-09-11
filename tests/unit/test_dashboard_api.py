"""
Testes Unitários da API Assíncrona do Dashboard do Vertex-bot.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.dashboard.server import create_dashboard_app
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
from src.database.repository import OrdersRepository, PositionsRepository, TokensRepository


@pytest.mark.asyncio
async def test_dashboard_api_full_flow(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test_dash.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    tokens_repo = TokensRepository(db)
    positions_repo = PositionsRepository(db)
    orders_repo = OrdersRepository(db)

    # Inserir token aprovado
    token_appr = TokenMetadata(
        address="TOKEN_APPR_111",
        symbol="WIN",
        name="Winner Token",
        dex="raydium",
        initial_liquidity_usd=Decimal("10000.0"),
    )
    await tokens_repo.save_detected_token(token_appr)
    audit_appr = SecurityAuditResult(
        token_address=token_appr.address,
        status=SecurityStatus.APPROVED,
        security_score=100.0,
        is_mint_revoked=True,
        is_freeze_revoked=True,
        is_lp_burned_or_locked=True,
        lp_burn_percentage=100.0,
        top10_holder_percentage=10.0,
        is_honeypot=False,
        buy_tax_percentage=0.0,
        sell_tax_percentage=0.0,
    )
    await tokens_repo.update_audit_result(audit_appr)

    # Inserir token rejeitado
    token_rej = TokenMetadata(
        address="TOKEN_REJ_222",
        symbol="RUG",
        name="Rug Token",
        dex="raydium",
        initial_liquidity_usd=Decimal("1200.0"),
    )
    await tokens_repo.save_detected_token(token_rej)
    audit_rej = SecurityAuditResult(
        token_address=token_rej.address,
        status=SecurityStatus.REJECTED,
        security_score=0.0,
        is_mint_revoked=True,
        is_freeze_revoked=True,
        is_lp_burned_or_locked=False,
        lp_burn_percentage=0.0,
        top10_holder_percentage=20.0,
        is_honeypot=False,
        buy_tax_percentage=0.0,
        sell_tax_percentage=0.0,
        rejection_reason="LP não bloqueada ou queimada insuficientemente (0.0% < 98%)",
    )
    await tokens_repo.update_audit_result(audit_rej)

    # Inserir posição e ordem
    pos = PositionState(
        token_address=token_appr.address,
        mode=ExecutionMode.PAPER,
        entry_price=Decimal("1.0"),
        initial_token_amount=Decimal("100.0"),
        allocated_capital_usd=Decimal("100.0"),
        status=PositionStatus.CLOSED,
        realized_pnl_usd=Decimal("25.50"),
    )
    pos_id = await positions_repo.create_position(pos)

    order = OrderExecution(
        position_id=pos_id,
        order_type=OrderType.BUY,
        mode=ExecutionMode.PAPER,
        price=Decimal("1.0"),
        amount=Decimal("100.0"),
        total_usd=Decimal("100.0"),
    )
    await orders_repo.record_order(order)

    # Instanciar app aiohttp e client de teste
    app = create_dashboard_app(db)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # 1. Testar GET /
        resp_index = await client.get("/")
        assert resp_index.status == 200
        html = await resp_index.text()
        assert "VERTEX" in html

        # 2. Testar GET /api/summary
        resp_summary = await client.get("/api/summary")
        assert resp_summary.status == 200
        summary_data = await resp_summary.json()
        assert summary_data["status"] == "success"
        pnl = summary_data["data"]["pnl"]
        scanner = summary_data["data"]["scanner"]
        assert pnl["total_positions"] == 1
        assert pnl["total_pnl_usd"] == 25.50
        assert scanner["total_scanned"] == 2
        assert scanner["approved"] == 1
        assert scanner["rejected"] == 1

        # 3. Testar GET /api/positions
        resp_pos = await client.get("/api/positions")
        assert resp_pos.status == 200
        pos_data = await resp_pos.json()
        assert len(pos_data["data"]) == 1
        assert pos_data["data"][0]["token_address"] == "TOKEN_APPR_111"

        # 4. Testar GET /api/orders
        resp_orders = await client.get("/api/orders")
        assert resp_orders.status == 200
        orders_data = await resp_orders.json()
        assert len(orders_data["data"]) == 1
        assert orders_data["data"][0]["order_type"] == "BUY"

        # 5. Testar GET /api/tokens com filtro
        resp_tokens_all = await client.get("/api/tokens")
        assert resp_tokens_all.status == 200
        tokens_all = await resp_tokens_all.json()
        assert len(tokens_all["data"]) == 2

        resp_tokens_rej = await client.get("/api/tokens?status=REJECTED")
        tokens_rej = await resp_tokens_rej.json()
        assert len(tokens_rej["data"]) == 1
        assert tokens_rej["data"][0]["symbol"] == "RUG"

        resp_tokens_search = await client.get("/api/tokens?search=WIN")
        tokens_search = await resp_tokens_search.json()
        assert len(tokens_search["data"]) == 1
        assert tokens_search["data"][0]["symbol"] == "WIN"

    finally:
        await client.close()
        await server.close()
        await db.close()

