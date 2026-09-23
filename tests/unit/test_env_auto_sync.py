"""
Testes Unitários para Sincronização Automática do .env e Estado do Bot.
Valida persistência de chaves de carteira, modo de execução e flags de confirmação.
"""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from solders.keypair import Keypair  # type: ignore[import-untyped]

from src.config.settings import update_env_file
from src.dashboard.server import create_dashboard_app
from src.database.connection import DatabaseManager
from src.engine.live import LiveExecutionEngine
from src.engine.reentry import ReentryRiskManager


def test_update_env_file_basic_and_preservation(tmp_path: Path) -> None:
    """Testa atualização do .env preservando comentários e formatando aspas."""
    env_file = tmp_path / ".env"
    initial_content = (
        "# Comentário inicial do Vertex-bot\n"
        "DATABASE_PATH=\"data/vertex.db\"\n"
        "# WALLET_PRIVATE_KEY_BASE58=\n"
        "EXECUTION_MODE=\"PAPER\"\n"
        "\n"
        "# Outra seção\n"
        "CONFIRM_LIVE_TRADING=\"false\"\n"
    )
    env_file.write_text(initial_content, encoding="utf-8")

    # 1. Atualiza chaves existentes e preenche a comentada
    res = update_env_file(
        {
            "WALLET_PRIVATE_KEY_BASE58": "solana_fake_private_key_123",
            "SOLANA_PRIVATE_KEY_BASE58": "solana_fake_private_key_123",
            "EXECUTION_MODE": "LIVE",
            "CONFIRM_LIVE_TRADING": "true",
            "NEW_SETTING": "awesome_value",
        },
        filepath=str(env_file),
    )
    assert res is True

    updated_text = env_file.read_text(encoding="utf-8")
    assert "# Comentário inicial do Vertex-bot" in updated_text
    assert 'WALLET_PRIVATE_KEY_BASE58="solana_fake_private_key_123"' in updated_text
    assert 'SOLANA_PRIVATE_KEY_BASE58="solana_fake_private_key_123"' in updated_text
    assert 'EXECUTION_MODE="LIVE"' in updated_text
    assert 'CONFIRM_LIVE_TRADING="true"' in updated_text
    assert 'NEW_SETTING="awesome_value"' in updated_text
    assert "# Outra seção" in updated_text

    # 2. Limpa uma chave (como no disconnect)
    res_clear = update_env_file(
        {
            "WALLET_PRIVATE_KEY_BASE58": "",
            "SOLANA_PRIVATE_KEY_BASE58": "",
        },
        filepath=str(env_file),
    )
    assert res_clear is True
    cleared_text = env_file.read_text(encoding="utf-8")
    assert 'WALLET_PRIVATE_KEY_BASE58=""' in cleared_text
    assert 'SOLANA_PRIVATE_KEY_BASE58=""' in cleared_text


def test_update_env_file_creates_if_not_exists(tmp_path: Path) -> None:
    """Testa criação automática de novo arquivo .env se não existir."""
    env_file = tmp_path / ".env.new"
    assert not env_file.exists()

    res = update_env_file({"KEY_A": "VAL_A", "KEY_B": "VAL_B"}, filepath=str(env_file))
    assert res is True
    assert env_file.exists()

    content = env_file.read_text(encoding="utf-8")
    assert 'KEY_A="VAL_A"' in content
    assert 'KEY_B="VAL_B"' in content


@pytest.mark.asyncio
async def test_dashboard_wallet_and_start_env_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Testa fluxo completo de conectar carteira e iniciar bot via dashboard persistindo no .env."""
    env_file = tmp_path / ".env"
    env_file.write_text('EXECUTION_MODE="PAPER"\nCONFIRM_LIVE_TRADING="false"\n', encoding="utf-8")

    # Redireciona o caminho padrão do update_env_file para o temp
    orig_update_env_file = update_env_file

    def custom_update_env_file(updates: dict[str, str], filepath: str = ".env") -> bool:
        return orig_update_env_file(updates, filepath=str(env_file))

    monkeypatch.setattr("src.dashboard.server.update_env_file", custom_update_env_file)

    db_path = str(tmp_path / "test_env_sync.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    from src.database.repository import OrdersRepository, PositionsRepository
    pos_repo = PositionsRepository(db)
    orders_repo = OrdersRepository(db)
    live_engine = LiveExecutionEngine(positions_repo=pos_repo, orders_repo=orders_repo)

    # Mock do Orchestrator
    class MockSyncOrchestrator:
        def __init__(self) -> None:
            self.is_running = False
            self.live_engine = live_engine
            self.settings = SimpleNamespace(
                EXECUTION_MODE="PAPER",
                CONFIRM_LIVE_TRADING=False,
                MAX_CONCURRENT_POSITIONS=5,
                PAPER_BUY_AMOUNT_USD=Decimal("1.0"),
                LIVE_BUY_AMOUNT_USD=Decimal("5.0"),
                MIN_TRADE_AMOUNT_USD=Decimal("1.0"),
                BREAK_EVEN_GAIN_PCT=Decimal("100.0"),
                TRAILING_STOP_DROP_PCT=Decimal("12.0"),
                EMERGENCY_STOP_LOSS_PCT=Decimal("20.0"),
                MAX_SLIPPAGE_PCT=Decimal("1.5"),
            )
            self.execution_engine = SimpleNamespace(balance_usd=Decimal("10.0"))
            self.position_tracker = SimpleNamespace(active_positions={})
            self.reentry_manager = ReentryRiskManager()

        async def start_live(self) -> None:
            self.is_running = True

        async def start_paper(self) -> None:
            self.is_running = True

        def pause(self) -> None:
            pass

        def resume(self) -> None:
            pass

    orch = MockSyncOrchestrator()
    app = create_dashboard_app(db=db, orchestrator=orch)  # type: ignore[arg-type]
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # 1. Conectar carteira Solana
        kp = Keypair()
        priv_sol = str(kp)
        resp = await client.post("/api/wallet/connect", json={"chain": "solana", "private_key": priv_sol})
        assert resp.status == 200
        env_content = env_file.read_text(encoding="utf-8")
        assert f'SOLANA_PRIVATE_KEY_BASE58="{priv_sol}"' in env_content
        assert f'WALLET_PRIVATE_KEY_BASE58="{priv_sol}"' in env_content

        # 2. Conectar carteira EVM
        evm_key = "0x4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f361322"
        resp_evm = await client.post("/api/wallet/connect", json={"chain": "base", "private_key": evm_key})
        assert resp_evm.status == 200
        env_content = env_file.read_text(encoding="utf-8")
        assert f'EVM_PRIVATE_KEY="{evm_key}"' in env_content
        assert f'EVM_WALLET_PRIVATE_KEY="{evm_key}"' in env_content

        # 3. Iniciar bot no modo LIVE
        resp_start_live = await client.post("/api/bot/start?mode=live")
        assert resp_start_live.status == 200
        env_content = env_file.read_text(encoding="utf-8")
        assert 'EXECUTION_MODE="LIVE"' in env_content
        assert 'CONFIRM_LIVE_TRADING="true"' in env_content
        assert orch.settings.EXECUTION_MODE == "LIVE"
        assert orch.settings.CONFIRM_LIVE_TRADING is True
        assert live_engine.confirm_live_trading is True

        # 4. Iniciar bot no modo PAPER
        resp_start_paper = await client.post("/api/bot/start?mode=paper")
        assert resp_start_paper.status == 200
        env_content = env_file.read_text(encoding="utf-8")
        assert 'EXECUTION_MODE="PAPER"' in env_content
        assert orch.settings.EXECUTION_MODE == "PAPER"

        # 5. Desconectar Solana
        resp_disc = await client.post("/api/wallet/disconnect", json={"chain": "solana"})
        assert resp_disc.status == 200
        env_content = env_file.read_text(encoding="utf-8")
        assert 'SOLANA_PRIVATE_KEY_BASE58=""' in env_content
        assert 'WALLET_PRIVATE_KEY_BASE58=""' in env_content

        # 6. Desconectar EVM
        resp_disc_evm = await client.post("/api/wallet/disconnect", json={"chain": "base"})
        assert resp_disc_evm.status == 200
        env_content = env_file.read_text(encoding="utf-8")
        assert 'EVM_PRIVATE_KEY=""' in env_content
        assert 'EVM_WALLET_PRIVATE_KEY=""' in env_content

    finally:
        await client.close_server()
        await db.close()

