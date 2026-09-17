"""
Testes Unitários da API Assíncrona do Dashboard do Vertex-bot.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.dashboard import server as srv_mod
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

        # Inserir também uma posição aberta para testar filtros
        pos_open = PositionState(
            token_address=token_rej.address,
            mode=ExecutionMode.PAPER,
            entry_price=Decimal("0.5"),
            initial_token_amount=Decimal("200.0"),
            allocated_capital_usd=Decimal("100.0"),
            status=PositionStatus.OPEN,
        )
        await positions_repo.create_position(pos_open)

        # 3. Testar GET /api/positions (todas, abertas e fechadas)
        resp_pos = await client.get("/api/positions")
        assert resp_pos.status == 200
        pos_data = await resp_pos.json()
        assert len(pos_data["data"]) == 2

        resp_pos_open = await client.get("/api/positions?status=open")
        assert resp_pos_open.status == 200
        open_data = await resp_pos_open.json()
        assert len(open_data["data"]) == 1
        assert open_data["data"][0]["status"] == "OPEN"

        resp_pos_closed = await client.get("/api/positions?status=closed")
        assert resp_pos_closed.status == 200
        closed_data = await resp_pos_closed.json()
        assert len(closed_data["data"]) == 1
        assert closed_data["data"][0]["status"] == "CLOSED"
        assert closed_data["data"][0]["token_address"] == "TOKEN_APPR_111"

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


@pytest.mark.asyncio
async def test_dashboard_session_filtering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Valida que o resumo do dashboard inicia zerado visualmente quando há uma nova sessão simulada."""
    import json
    from datetime import UTC, datetime, timedelta

    db_path = str(tmp_path / "test_session_dash.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    tokens_repo = TokensRepository(db)

    # Inserir token auditado 1 hora atrás (sessão passada)
    one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    old_token = TokenMetadata(
        address="OLD_TOKEN_001",
        symbol="OLD",
        name="Old Scam",
        dex="raydium",
        initial_liquidity_usd=Decimal("5000.0"),
        detection_timestamp=one_hour_ago,
    )
    await tokens_repo.save_detected_token(old_token)
    audit_old = SecurityAuditResult(
        token_address=old_token.address,
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
        rejection_reason="LP não queimada",
    )
    await tokens_repo.update_audit_result(audit_old)

    # Simula arquivo de sessão simulada iniciado há 5 minutos
    session_start = datetime.now(UTC) - timedelta(minutes=5)
    session_file = tmp_path / "paper_session.json"
    session_file.write_text(json.dumps({
        "session_start": session_start.isoformat(),
        "mode": "PAPER",
    }))

    app = create_dashboard_app(db)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    # Monkeypatch do caminho do paper_session.json no server
    server_instance = [h for h in app.router.routes() if h.method == "GET"][0].handler.__self__  # type: ignore[attr-defined]
    monkeypatch.setattr(server_instance, "_get_session_start", lambda: session_start)

    try:
        # GET /api/summary deve mostrar 0 tokens na sessão ativa, mas 1 no banco geral
        resp = await client.get("/api/summary")
        assert resp.status == 200
        data = await resp.json()
        scanner = data["data"]["scanner"]
        assert scanner["total_scanned"] == 0
        assert scanner["approved"] == 0
        assert scanner["rejected"] == 0
        assert scanner["all_time_cataloged"] == 1

        # Agora adiciona um token novo detectado AGORA (nesta sessão)
        new_token = TokenMetadata(
            address="NEW_TOKEN_002",
            symbol="NEW",
            name="New Project",
            dex="raydium",
            initial_liquidity_usd=Decimal("25000.0"),
            detection_timestamp=datetime.now(UTC),
        )
        await tokens_repo.save_detected_token(new_token)
        audit_new = SecurityAuditResult(
            token_address=new_token.address,
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
        await tokens_repo.update_audit_result(audit_new)

        # Agora o dashboard deve mostrar 1 token nesta sessão (e 2 no total do banco)
        resp2 = await client.get("/api/summary")
        data2 = await resp2.json()
        scanner2 = data2["data"]["scanner"]
        assert scanner2["total_scanned"] == 1
        assert scanner2["approved"] == 1
        assert scanner2["rejected"] == 0
        assert scanner2["all_time_cataloged"] == 2

    finally:
        await client.close()
        await server.close()
        await db.close()


@pytest.mark.asyncio
async def test_bot_status_handles_null_config_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Valida se /api/bot/status é resiliente a valores null/None em bot_config.json."""
    db_path = str(tmp_path / "test_null_cfg.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    cfg_path = tmp_path / "bot_config.json"
    # Grava intencionalmente chaves com valor null
    cfg_path.write_text(
        json.dumps({
            "emergency_stop_loss_pct": None,
            "paper_buy_amount_usd": None,
            "max_concurrent_positions": None,
            "wallet_balance_usd": None,
            "max_token_age_hours": None,
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(srv_mod, "CONFIG_FILE", cfg_path)

    app = create_dashboard_app(db, orchestrator=None)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        resp = await client.get("/api/bot/status")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "success"
        settings = data["data"]["settings"]
        # Deve ter aplicado os defaults defensivos ao invés de lançar TypeError
        assert settings["emergency_stop_loss_pct"] == 20.0
        assert settings["paper_buy_amount_usd"] == 1.0
        assert settings["max_concurrent_positions"] == 50
        assert settings["max_token_age_hours"] == 3.0
    finally:
        await client.close()
        await server.close()
        await db.close()


@pytest.mark.asyncio
async def test_dashboard_position_close_and_buy_more(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Testa os endpoints POST /api/positions/{id}/close e POST /api/positions/{id}/buy_more."""
    db_path = str(tmp_path / "test_actions.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    tokens_repo = TokensRepository(db)
    token = TokenMetadata(
        address="POS_ACTION_TOKEN",
        symbol="ACT",
        name="Action Token",
        dex="raydium",
    )
    await tokens_repo.save_detected_token(token)

    positions_repo = PositionsRepository(db)
    pos = PositionState(
        token_address="POS_ACTION_TOKEN",
        mode=ExecutionMode.PAPER,
        strategy_type="SCALP",
        entry_price=Decimal("0.000005"),
        initial_token_amount=Decimal("200000.0"),
        allocated_capital_usd=Decimal("1.0"),
        highest_price_seen=Decimal("0.000005"),
        trailing_stop_price=Decimal("0.0000044"),
        status=PositionStatus.OPEN,
    )
    pos_id = await positions_repo.create_position(pos)

    # Teste em modo desacoplado (IPC write)
    control_path = tmp_path / "bot_control.json"
    monkeypatch.setattr(srv_mod, "CONTROL_FILE", control_path)

    app = create_dashboard_app(db, orchestrator=None)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # Fechar posição
        resp_close = await client.post(f"/api/positions/{pos_id}/close")
        assert resp_close.status == 200
        data_close = await resp_close.json()
        assert data_close["status"] == "success"
        assert control_path.exists()
        ipc_data = json.loads(control_path.read_text(encoding="utf-8"))
        assert ipc_data["command"] == "close_position"
        assert ipc_data["payload"]["position_id"] == pos_id

        # Comprar mais / nova posição
        resp_buy = await client.post(f"/api/positions/{pos_id}/buy_more")
        assert resp_buy.status == 200
        data_buy = await resp_buy.json()
        assert data_buy["status"] == "success"
        ipc_data_buy = json.loads(control_path.read_text(encoding="utf-8"))
        assert ipc_data_buy["command"] == "buy_more"
        assert ipc_data_buy["payload"]["position_id"] == pos_id
    finally:
        await client.close()
        await server.close()
        await db.close()



