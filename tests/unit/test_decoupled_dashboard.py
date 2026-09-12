"""
Testes Unitários da Execução Desacoplada e Comunicação IPC entre Dashboard e Bot.
Valida funcionamento autônomo de run_dashboard.py / server.py com orchestrator=None,
detecção de liveness por heartbeat (data/bot_status.json) e despacho de comandos IPC (data/bot_control.json).
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from main import VertexBotOrchestrator
from src.config.settings import Settings
from src.dashboard.server import create_dashboard_app
from src.database.connection import DatabaseManager


@pytest.fixture
def decoupled_settings(tmp_path: Path) -> Settings:
    """Configurações isoladas para testes de IPC."""
    return Settings(
        DATABASE_PATH=str(tmp_path / "test_ipc.db"),
        EXECUTION_MODE="PAPER",
        PAPER_INITIAL_WALLET_USD=Decimal("10.0"),
        PAPER_BUY_AMOUNT_USD=Decimal("1.0"),
        MAX_CONCURRENT_POSITIONS=5,
        MIN_TRADE_AMOUNT_USD=Decimal("1.0"),
        BREAK_EVEN_GAIN_PCT=Decimal("100.0"),
        TRAILING_STOP_DROP_PCT=Decimal("12.0"),
        EMERGENCY_STOP_LOSS_PCT=Decimal("20.0"),
        MAX_SLIPPAGE_PCT=Decimal("1.5"),
    )


@pytest.mark.asyncio
async def test_standalone_dashboard_status_offline_and_online(tmp_path: Path) -> None:
    """Valida leitura de status pelo dashboard desacoplado (offline vs heartbeat online)."""
    db_path = str(tmp_path / "test_dash_decoupled.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    status_file = tmp_path / "bot_status.json"
    control_file = tmp_path / "bot_control.json"
    config_file = tmp_path / "bot_config.json"
    session_file = tmp_path / "paper_session.json"

    with (
        patch("src.dashboard.server.STATUS_FILE", status_file),
        patch("src.dashboard.server.CONTROL_FILE", control_file),
        patch("src.dashboard.server.CONFIG_FILE", config_file),
        patch("src.dashboard.server.SESSION_FILE", session_file),
    ):
        # Dashboard iniciado sem orquestrador local (orchestrator=None)
        app = create_dashboard_app(db, orchestrator=None)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()

        try:
            # 1. Sem arquivo de status -> Bot Offline
            resp_off = await client.get("/api/bot/status")
            assert resp_off.status == 200
            data_off = await resp_off.json()
            assert data_off["data"]["is_running"] is False
            assert data_off["data"]["is_paused"] is False
            assert data_off["data"]["wallet_balance_usd"] == 0.0

            # 2. Emite heartbeat recente -> Bot Online
            status_payload = {
                "is_running": True,
                "is_paused": False,
                "wallet_balance_usd": 42.50,
                "active_positions_count": 3,
                "pid": 9999,
                "timestamp": datetime.now(UTC).timestamp(),
            }
            status_file.write_text(json.dumps(status_payload), encoding="utf-8")

            resp_on = await client.get("/api/bot/status")
            assert resp_on.status == 200
            data_on = await resp_on.json()
            assert data_on["data"]["is_running"] is True
            assert data_on["data"]["is_paused"] is False
            assert data_on["data"]["wallet_balance_usd"] == 42.50
            assert data_on["data"]["active_positions_count"] == 3

            # 3. Heartbeat expirado (>6 segundos) -> Bot detectado como offline
            old_payload = {
                "is_running": True,
                "is_paused": False,
                "wallet_balance_usd": 42.50,
                "active_positions_count": 3,
                "pid": 9999,
                "timestamp": datetime.now(UTC).timestamp() - 15.0,  # 15s atrás
            }
            status_file.write_text(json.dumps(old_payload), encoding="utf-8")

            resp_stale = await client.get("/api/bot/status")
            assert resp_stale.status == 200
            data_stale = await resp_stale.json()
            assert data_stale["data"]["is_running"] is False

        finally:
            await client.close()
            await db.close()


@pytest.mark.asyncio
async def test_standalone_dashboard_commands_dispatch_ipc(tmp_path: Path) -> None:
    """Valida se o Dashboard desacoplado grava comandos IPC em bot_control.json e atualiza bot_config.json."""
    db_path = str(tmp_path / "test_ipc_commands.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    status_file = tmp_path / "bot_status.json"
    control_file = tmp_path / "bot_control.json"
    config_file = tmp_path / "bot_config.json"
    session_file = tmp_path / "paper_session.json"

    with (
        patch("src.dashboard.server.STATUS_FILE", status_file),
        patch("src.dashboard.server.CONTROL_FILE", control_file),
        patch("src.dashboard.server.CONFIG_FILE", config_file),
        patch("src.dashboard.server.SESSION_FILE", session_file),
    ):
        app = create_dashboard_app(db, orchestrator=None)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()

        try:
            # 1. POST /api/bot/pause
            resp_pause = await client.post("/api/bot/pause")
            assert resp_pause.status == 200
            assert control_file.exists()
            cmd_pause = json.loads(control_file.read_text(encoding="utf-8"))
            assert cmd_pause["command"] == "pause"

            # 2. POST /api/bot/resume
            resp_resume = await client.post("/api/bot/resume")
            assert resp_resume.status == 200
            cmd_resume = json.loads(control_file.read_text(encoding="utf-8"))
            assert cmd_resume["command"] == "resume"

            # 3. POST /api/bot/config
            resp_cfg = await client.post(
                "/api/bot/config",
                json={"max_concurrent_positions": 7, "paper_buy_amount_usd": 3.0},
            )
            assert resp_cfg.status == 200
            cmd_cfg = json.loads(control_file.read_text(encoding="utf-8"))
            assert cmd_cfg["command"] == "reload_config"
            assert config_file.exists()
            saved_cfg = json.loads(config_file.read_text(encoding="utf-8"))
            assert saved_cfg["max_concurrent_positions"] == 7
            assert saved_cfg["paper_buy_amount_usd"] == 3.0

            # 4. POST /api/wallet/deposit
            resp_dep = await client.post("/api/wallet/deposit", json={"amount_usd": 20.0})
            assert resp_dep.status == 200
            cmd_dep = json.loads(control_file.read_text(encoding="utf-8"))
            assert cmd_dep["command"] == "deposit"
            assert cmd_dep["payload"]["amount_usd"] == 20.0

            # 5. POST /api/bot/restart
            resp_rest = await client.post("/api/bot/restart", json={"wallet_balance_usd": 50.0})
            assert resp_rest.status == 200
            cmd_rest = json.loads(control_file.read_text(encoding="utf-8"))
            assert cmd_rest["command"] == "restart"
            assert cmd_rest["payload"]["wallet_balance_usd"] == 50.0

            # 6. POST /api/bot/stop
            resp_stop = await client.post("/api/bot/stop")
            assert resp_stop.status == 200
            cmd_stop = json.loads(control_file.read_text(encoding="utf-8"))
            assert cmd_stop["command"] == "stop"

        finally:
            await client.close()
            await db.close()


@pytest.mark.asyncio
async def test_bot_orchestrator_ipc_handling_and_heartbeat(
    decoupled_settings: Settings,
    tmp_path: Path,
) -> None:
    """Valida recepção e execução de comandos IPC e emissão de heartbeat pelo VertexBotOrchestrator."""
    status_file = tmp_path / "bot_status.json"
    control_file = tmp_path / "bot_control.json"
    config_file = tmp_path / "bot_config.json"

    with (
        patch.object(VertexBotOrchestrator, "_get_config_path", return_value=config_file),
        patch("main.Path", side_effect=lambda p: status_file if "bot_status" in str(p) else (control_file if "bot_control" in str(p) else Path(p))),
    ):
        orch = VertexBotOrchestrator(decoupled_settings)
        orch.execution_engine.balance_usd = Decimal("15.00")

        # 1. Emissão de Heartbeat Online
        orch._write_heartbeat_sync(is_running=True)
        assert status_file.exists()
        hb = json.loads(status_file.read_text(encoding="utf-8"))
        assert hb["is_running"] is True
        assert hb["is_paused"] is False
        assert hb["wallet_balance_usd"] == 15.00

        # 2. Despacho de comando IPC: pause
        await orch._dispatch_ipc_command("pause", {})
        assert orch.is_paused is True

        # 3. Despacho de comando IPC: resume
        await orch._dispatch_ipc_command("resume", {})
        assert orch.is_paused is False

        # 4. Despacho de comando IPC: deposit
        await orch._dispatch_ipc_command("deposit", {"amount_usd": 25.0})
        assert orch.execution_engine.balance_usd == Decimal("40.00")

        # 5. Despacho de comando IPC: restart
        orch.restart_paper_session = AsyncMock()
        await orch._dispatch_ipc_command("restart", {"wallet_balance_usd": 30.0})
        orch.restart_paper_session.assert_awaited_once_with(new_balance=Decimal("30.0"))

        # 6. Despacho de comando IPC: stop
        orch.stop = AsyncMock()
        await orch._dispatch_ipc_command("stop", {})
        orch.stop.assert_awaited_once()
