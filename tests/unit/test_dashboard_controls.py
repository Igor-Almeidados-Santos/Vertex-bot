"""
Testes Unitários das Rotas de Controle Operacional do Dashboard.
Cobre endpoints de Pause, Resume, Restart, Stop, Configurações e Depósito de Saldo.
"""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.dashboard.server import create_dashboard_app
from src.database.connection import DatabaseManager
from src.engine.reentry import ReentryRiskManager


class MockOrchestrator:
    """Mock do VertexBotOrchestrator para isolamento de testes da API."""

    def __init__(self) -> None:
        self.is_running: bool = True
        self.is_paused: bool = False
        self.stop_called: bool = False
        self.restart_called: bool = False
        self.reentry_manager = ReentryRiskManager()
        self.settings = SimpleNamespace(
            EXECUTION_MODE="PAPER",
            MAX_CONCURRENT_POSITIONS=5,
            PAPER_BUY_AMOUNT_USD=Decimal("1.0"),
            MIN_TRADE_AMOUNT_USD=Decimal("1.0"),
            BREAK_EVEN_GAIN_PCT=Decimal("100.0"),
            TRAILING_STOP_DROP_PCT=Decimal("12.0"),
            EMERGENCY_STOP_LOSS_PCT=Decimal("20.0"),
            MAX_SLIPPAGE_PCT=Decimal("1.5"),
            REENTRY_TRAILING_COOLOFF_SEC=300.0,
            REENTRY_STOPLOSS_COOLOFF_SEC=1800.0,
            REENTRY_MIN_BOUNCE_PCT=Decimal("3.0"),
        )
        self.execution_engine = SimpleNamespace(balance_usd=Decimal("5.00"))
        self.position_tracker = SimpleNamespace(active_positions={})

    def pause(self) -> None:
        self.is_paused = True

    def resume(self) -> None:
        self.is_paused = False

    def deposit_wallet(self, amount_usd: Decimal) -> Decimal:
        self.execution_engine.balance_usd += amount_usd
        return Decimal(str(self.execution_engine.balance_usd))

    async def restart_paper_session(self, new_balance: Decimal | None = None) -> None:
        self.restart_called = True
        if new_balance is not None:
            self.execution_engine.balance_usd = new_balance
        else:
            self.execution_engine.balance_usd = Decimal("5.00")

    async def close_position_manually(self, position_id: int) -> bool:
        return position_id == 1

    async def open_position_manually(
        self,
        position_id: int | None = None,
        token_address: str | None = None,
        strategy_type: str = "SCALP",
    ) -> tuple[bool, str]:
        if position_id == 1:
            return True, f"Posição #{position_id} comprada com sucesso"
        return False, "Posição não encontrada"

    async def initialize(self) -> None:
        pass

    async def start(self) -> None:
        self.is_running = True

    async def stop(self) -> None:
        self.is_running = False
        self.stop_called = True

    def update_dynamic_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "max_concurrent_positions" in payload and payload["max_concurrent_positions"] is not None:
            self.settings.MAX_CONCURRENT_POSITIONS = int(payload["max_concurrent_positions"])
        if "paper_buy_amount_usd" in payload and payload["paper_buy_amount_usd"] is not None:
            self.settings.PAPER_BUY_AMOUNT_USD = Decimal(str(payload["paper_buy_amount_usd"]))
        return {
            "max_concurrent_positions": self.settings.MAX_CONCURRENT_POSITIONS,
            "paper_buy_amount_usd": float(self.settings.PAPER_BUY_AMOUNT_USD),
        }


@pytest.mark.asyncio
async def test_dashboard_control_endpoints(tmp_path: Path) -> None:
    db_path = str(tmp_path / "test_dash_controls.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    mock_orch = MockOrchestrator()
    app = create_dashboard_app(db, orchestrator=mock_orch)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # 1. GET /api/bot/status
        resp_status = await client.get("/api/bot/status")
        assert resp_status.status == 200
        data_status = await resp_status.json()
        assert data_status["status"] == "success"
        assert data_status["data"]["is_running"] is True
        assert data_status["data"]["is_paused"] is False
        assert data_status["data"]["wallet_balance_usd"] == 5.0
        assert data_status["data"]["settings"]["max_concurrent_positions"] == 5

        # 2. POST /api/bot/pause
        resp_pause = await client.post("/api/bot/pause")
        assert resp_pause.status == 200
        data_pause = await resp_pause.json()
        assert data_pause["status"] == "success"
        assert data_pause["is_paused"] is True
        assert mock_orch.is_paused is True

        # 3. POST /api/bot/resume
        resp_resume = await client.post("/api/bot/resume")
        assert resp_resume.status == 200
        data_resume = await resp_resume.json()
        assert data_resume["status"] == "success"
        assert data_resume["is_paused"] is False
        assert mock_orch.is_paused is False

        # 4. POST /api/wallet/deposit
        resp_deposit = await client.post("/api/wallet/deposit", json={"amount_usd": 25.0})
        assert resp_deposit.status == 200
        data_deposit = await resp_deposit.json()
        assert data_deposit["status"] == "success"
        assert data_deposit["wallet_balance_usd"] == 30.0
        assert mock_orch.execution_engine.balance_usd == Decimal("30.0")

        # 5. POST /api/bot/config
        resp_cfg = await client.post(
            "/api/bot/config",
            json={"max_concurrent_positions": 8, "paper_buy_amount_usd": 2.5},
        )
        assert resp_cfg.status == 200
        data_cfg = await resp_cfg.json()
        assert data_cfg["status"] == "success"
        assert data_cfg["data"]["max_concurrent_positions"] == 8
        assert data_cfg["data"]["paper_buy_amount_usd"] == 2.5
        assert mock_orch.settings.MAX_CONCURRENT_POSITIONS == 8

        # 6. POST /api/bot/restart
        resp_restart = await client.post("/api/bot/restart", json={"wallet_balance_usd": 15.0})
        assert resp_restart.status == 200
        data_restart = await resp_restart.json()
        assert data_restart["status"] == "success"
        assert mock_orch.restart_called is True
        assert mock_orch.execution_engine.balance_usd == Decimal("15.0")

        # 7. POST /api/bot/stop
        resp_stop = await client.post("/api/bot/stop")
        assert resp_stop.status == 200
        data_stop = await resp_stop.json()
        assert data_stop["status"] == "success"
        assert mock_orch.stop_called is True
        assert mock_orch.is_running is False

        # 8. POST /api/bot/start
        resp_start = await client.post("/api/bot/start")
        assert resp_start.status == 200
        data_start = await resp_start.json()
        assert data_start["status"] == "success"
        assert mock_orch.is_running is True

        # 9. POST /api/positions/1/close
        resp_close = await client.post("/api/positions/1/close")
        assert resp_close.status == 200
        data_close = await resp_close.json()
        assert data_close["status"] == "success"
        assert "fechar a posição #1" in data_close["message"]

        # 10. POST /api/positions/1/buy_more
        resp_buy = await client.post("/api/positions/1/buy_more")
        assert resp_buy.status == 200
        data_buy = await resp_buy.json()
        assert data_buy["status"] == "success"
        assert "comprada com sucesso" in data_buy["message"]
    finally:
        await client.close()
        await server.close()
        await db.close()
