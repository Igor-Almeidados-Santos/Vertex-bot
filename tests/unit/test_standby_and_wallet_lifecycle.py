"""
Testes Unitários para Inicialização Neutra (Standby), Gerenciamento de Carteiras On-Chain e Ciclo de Vida Duplo.

Verifica:
1. Inicialização padrão em Standby (nem PAPER nem LIVE ativos).
2. Hard gate: Início do modo LIVE bloqueado sem carteira conectada.
3. Conexão/Desconexão de carteiras Solana e EVM em memória volátil.
4. Execução simultânea independente de PAPER e LIVE.
5. Encerramento de PAPER zera posições e saldo simulado sem desligar o bot ou afetar LIVE.
"""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from solders.keypair import Keypair

from main import VertexBotOrchestrator
from src.database.connection import DatabaseManager
from src.database.models import ExecutionMode, PositionState, PositionStatus, TokenMetadata
from src.database.repository import PositionsRepository, TokensRepository
from src.engine.live import LiveExecutionEngine


@pytest.mark.asyncio
async def test_orchestrator_standby_mode(tmp_path: Any) -> None:
    """Verifica que o bot inicia em modo neutro (STANDBY) quando start_enabled=False."""
    db_path = str(tmp_path / "standby.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    orch = VertexBotOrchestrator(db=db, start_enabled=False)

    assert orch.paper_enabled is False
    assert orch.live_enabled is False
    assert orch.is_running is False
    assert orch.paper_engine.mode == ExecutionMode.PAPER
    assert orch.live_engine.mode == ExecutionMode.LIVE

    await db.close()


@pytest.mark.asyncio
async def test_start_live_requires_wallet_hard_gate(tmp_path: Any) -> None:
    """Verifica trava que impede ativação do LIVE sem nenhuma carteira conectada."""
    db_path = str(tmp_path / "live_gate.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    orch = VertexBotOrchestrator(db=db, start_enabled=False)

    assert orch.live_engine.has_connected_wallet() is False

    with pytest.raises(ValueError, match="Nenhuma carteira real"):
        await orch.start_live()

    assert orch.live_enabled is False

    await db.close()


@pytest.mark.asyncio
async def test_wallet_connection_and_disconnection(tmp_path: Any) -> None:
    """Testa conexão e desconexão de carteiras Solana e EVM no LiveExecutionEngine."""
    db_path = str(tmp_path / "wallet_conn.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    pos_repo = PositionsRepository(db)
    from src.database.repository import OrdersRepository
    orders_repo = OrdersRepository(db)

    engine = LiveExecutionEngine(positions_repo=pos_repo, orders_repo=orders_repo)

    assert engine.has_connected_wallet() is False
    initial_wallets = await engine.get_connected_wallets_info()
    assert len(initial_wallets) >= 2
    assert all(not w["is_connected"] for w in initial_wallets)

    # 1. Conecta Solana
    keypair = Keypair()
    priv_base58 = str(keypair)
    pub_expected = str(keypair.pubkey())

    success, addr, bal, msg = await engine.connect_solana_wallet(priv_base58)
    assert success is True
    assert addr == pub_expected
    assert engine.has_connected_wallet() is True

    wallets = await engine.get_connected_wallets_info()
    sol_w = next(w for w in wallets if w["chain"] == "solana")
    assert sol_w["is_connected"] is True
    assert sol_w["address"] == pub_expected

    # 2. Conecta EVM com chave hex válida (32 bytes)
    evm_priv = "0x4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f361322"
    success_evm, addr_evm, _, _ = await engine.connect_evm_wallet(evm_priv)
    assert success_evm is True
    assert addr_evm.startswith("0x")

    wallets = await engine.get_connected_wallets_info()
    evm_w = next(w for w in wallets if w["chain"] in ("ethereum", "arbitrum"))
    assert evm_w["is_connected"] is True

    # 3. Desconecta Solana
    assert engine.disconnect_wallet("solana") is True
    wallets_after_sol = await engine.get_connected_wallets_info()
    sol_after = next(w for w in wallets_after_sol if w["chain"] == "solana")
    assert sol_after["is_connected"] is False
    assert engine.has_connected_wallet() is True

    # 4. Desconecta EVM
    assert engine.disconnect_wallet("arbitrum") is True
    wallets_after_evm = await engine.get_connected_wallets_info()
    assert all(not w["is_connected"] for w in wallets_after_evm)
    assert engine.has_connected_wallet() is False

    await db.close()


@pytest.mark.asyncio
async def test_simultaneous_execution_and_paper_cleanup(tmp_path: Any) -> None:
    """Testa execução concorrente de PAPER e LIVE e limpeza de PAPER sem desligar o bot."""
    db_path = str(tmp_path / "simultaneous.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    orch = VertexBotOrchestrator(db=db, start_enabled=False)
    orch.is_running = True

    # Conecta carteira para permitir LIVE
    keypair = Keypair()
    await orch.live_engine.connect_solana_wallet(str(keypair))

    # Ativa ambos os modos simultaneamente
    await orch.start_paper()
    await orch.start_live()

    assert orch.paper_enabled is True
    assert orch.live_enabled is True
    assert orch.is_running is True

    # Cria posição simulada no banco
    tokens_repo = TokensRepository(db)
    pos_repo = PositionsRepository(db)
    tok = TokenMetadata(
        address="SimToken111111111111111111111111111111111",
        symbol="SIM",
        initial_liquidity_usd=Decimal("5000.0"),
    )
    await tokens_repo.save_detected_token(tok)
    pos = PositionState(
        token_address=tok.address,
        symbol=tok.symbol,
        mode=ExecutionMode.PAPER,
        entry_price=Decimal("1.0"),
        token_amount=Decimal("10.0"),
        initial_token_amount=Decimal("10.0"),
        allocated_capital_usd=Decimal("10.0"),
        stop_loss_price=Decimal("0.8"),
        take_profit_price=Decimal("2.0"),
        trailing_stop_price=Decimal("0.85"),
        highest_price_seen=Decimal("1.0"),
        ratchet_floor_price=Decimal("0.0"),
        ratchet_tier=0,
        strategy_type="SCALP",
        status=PositionStatus.OPEN,
    )
    pos_id = await pos_repo.create_position(pos)
    assert pos_id > 0

    # Pausa e retoma individualmente
    orch.pause_paper()
    assert orch.paper_paused is True
    assert orch.live_paused is False

    orch.resume_paper()
    assert orch.paper_paused is False

    # Encerra simulação (stop_paper)
    await orch.stop_paper()

    # O bot continua rodando!
    assert orch.is_running is True
    # O modo PAPER foi desativado e limpo
    assert orch.paper_enabled is False
    paper_positions = await pos_repo.get_open_positions(mode="PAPER")
    assert len(paper_positions) == 0

    # O modo LIVE continua 100% ativo
    assert orch.live_enabled is True

    await db.close()


@pytest.mark.asyncio
async def test_ipc_commands_mode_routing(tmp_path: Any) -> None:
    """Verifica roteamento de comandos IPC para start, pause, resume e stop segregados por modo."""
    db_path = str(tmp_path / "ipc_routing.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    orch = VertexBotOrchestrator(db=db, start_enabled=False)
    orch.is_running = True

    # 1. Start Paper via IPC
    await orch._dispatch_ipc_command("start", {"mode": "PAPER"})
    assert orch.paper_enabled is True
    assert orch.live_enabled is False

    # 2. Pause Paper via IPC
    await orch._dispatch_ipc_command("pause", {"mode": "PAPER"})
    assert orch.paper_paused is True

    # 3. Resume Paper via IPC
    await orch._dispatch_ipc_command("resume", {"mode": "PAPER"})
    assert orch.paper_paused is False

    # 4. Stop Paper via IPC
    await orch._dispatch_ipc_command("stop", {"mode": "PAPER"})
    assert orch.paper_enabled is False
    assert orch.is_running is True

    await db.close()


@pytest.mark.asyncio
async def test_wallet_key_formats_and_error_handling(tmp_path: Any) -> None:
    """Testa suporte a formatos JSON array, aspas, e mensagens explicativas para endereço público."""
    db_path = str(tmp_path / "wallet_formats.db")
    db = DatabaseManager(db_path)
    await db.initialize()

    pos_repo = PositionsRepository(db)
    from src.database.repository import OrdersRepository
    orders_repo = OrdersRepository(db)

    engine = LiveExecutionEngine(positions_repo=pos_repo, orders_repo=orders_repo)
    kp = Keypair()

    # 1. JSON byte array format [1, 2, ...]
    succ_json, pub_json, _, _ = await engine.connect_solana_wallet(kp.to_json())
    assert succ_json is True
    assert pub_json == str(kp.pubkey())

    # 2. Quoted Base58 format
    succ_quote, pub_quote, _, _ = await engine.connect_solana_wallet(f'"{str(kp)}"')
    assert succ_quote is True
    assert pub_quote == str(kp.pubkey())

    # 3. Public address pasted instead of private key -> friendly error
    succ_pub, _, _, err_pub = await engine.connect_solana_wallet(str(kp.pubkey()))
    assert succ_pub is False
    assert "Endereço Público" in err_pub
    assert "Phantom" in err_pub

    # 4. Empty key
    succ_empty, _, _, err_empty = await engine.connect_solana_wallet("   ")
    assert succ_empty is False
    assert "vazia" in err_empty

    # 5. EVM public address pasted instead of private key
    succ_evm_addr, _, _, err_evm_addr = await engine.connect_evm_wallet("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    assert succ_evm_addr is False
    assert "Endereço Público EVM" in err_evm_addr

    # 6. EVM valid private key with quotes
    evm_priv = '"0x4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f361322"'
    succ_evm_ok, addr_evm_ok, _, _ = await engine.connect_evm_wallet(evm_priv)
    assert succ_evm_ok is True
    assert addr_evm_ok.startswith("0x")

    await db.close()

