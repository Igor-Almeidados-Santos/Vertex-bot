"""Testes unitários para a rotina de auditoria e eliminação (faxina) da Pool de Prioridades."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from main import VertexBotOrchestrator
from src.config.settings import Settings
from src.database.connection import DatabaseManager
from src.database.models import TokenMetadata
from src.engine.priority_pool import PriorityPoolManager


@pytest.fixture
def temp_pool(tmp_path: Path) -> PriorityPoolManager:
    """Instancia um PriorityPoolManager apontando para diretório temporário."""
    return PriorityPoolManager(mode="paper", data_dir=str(tmp_path), min_alive_liquidity_usd=Decimal("5000.0"))


def make_token_metadata(
    address: str,
    symbol: str,
    name: str,
    liquidity_usd: Decimal = Decimal("15000.0"),
    tier: str = "EMERGING",
) -> TokenMetadata:
    """Helper para criar instâncias de TokenMetadata válidas."""
    return TokenMetadata(
        address=address,
        chain="solana",
        dex="raydium",
        symbol=symbol,
        name=name,
        decimals=9,
        total_supply=Decimal("1000000000"),
        initial_liquidity_usd=liquidity_usd,
        detection_timestamp=datetime.now(UTC),
        raw_event={"age_hours": 30000.0 if tier == "CONSOLIDATED" else 24.0, "token_tier": tier},
    )


def test_remove_token_persists(temp_pool: PriorityPoolManager, tmp_path: Path) -> None:
    """Testa remoção individual de um token com persistência atômica em disco."""
    meta = make_token_metadata("Addr111111111111111111111111111111111111111", "TOK1", "Token Um")
    temp_pool.register_executed_token(meta, entry_price=Decimal("1.0"))
    assert temp_pool.count() == 1

    removed = temp_pool.remove_token("Addr111111111111111111111111111111111111111", reason="Liquidez drenada")
    assert removed is True
    assert temp_pool.count() == 0

    # Recarrega do disco para confirmar persistência
    reloaded = PriorityPoolManager(mode="paper", data_dir=str(tmp_path))
    assert reloaded.count() == 0


def test_evaluate_profitability_dead_no_price(temp_pool: PriorityPoolManager) -> None:
    """Tokens sem cotação de mercado ou com preço zero devem ser reprovados."""
    meta = make_token_metadata("DeadToken1111111111111111111111111111111111", "DEAD", "Dead Token")
    temp_pool.register_executed_token(meta, entry_price=Decimal("1.0"))
    rec = temp_pool.get_token("DeadToken1111111111111111111111111111111111")
    assert rec is not None

    viable, reason = temp_pool.evaluate_token_profitability(rec, None, None)
    assert viable is False
    assert "inexistente/zerada" in reason

    viable_zero, reason_zero = temp_pool.evaluate_token_profitability(rec, Decimal("0.0"), None)
    assert viable_zero is False
    assert "inexistente/zerada" in reason_zero


def test_evaluate_profitability_drained_liquidity(temp_pool: PriorityPoolManager) -> None:
    """Tokens cuja liquidez caiu abaixo de MIN_LIQUIDITY_USD ($5.000) devem ser eliminados."""
    meta = make_token_metadata("DrainToken1111111111111111111111111111111111", "DRAIN", "Drained Token")
    temp_pool.register_executed_token(meta, entry_price=Decimal("1.0"))
    rec = temp_pool.get_token("DrainToken1111111111111111111111111111111111")
    assert rec is not None

    pair_data = {
        "liquidity": {"usd": 1250.0},
        "volume": {"h24": 1500.0},
        "priceChange": {"h24": -10.0},
    }
    viable, reason = temp_pool.evaluate_token_profitability(rec, Decimal("1.20"), pair_data)
    assert viable is False
    assert "Liquidez drenada" in reason


def test_evaluate_profitability_catastrophic_collapse(temp_pool: PriorityPoolManager) -> None:
    """Tokens que colapsaram >= 75% do seu pico histórico com liquidez fraca devem ser eliminados."""
    meta = make_token_metadata("DumpToken11111111111111111111111111111111111", "DUMP", "Dump Token")
    temp_pool.register_executed_token(meta, entry_price=Decimal("10.0"))

    rec = temp_pool.get_token("DumpToken11111111111111111111111111111111111")
    assert rec is not None
    rec.highest_price_seen = 10.0

    # Preço atual caiu para $2.0 (-80% de queda) com liquidez de $12.000 (< $50.000)
    pair_data = {
        "liquidity": {"usd": 12000.0},
        "volume": {"h24": 2000.0},
        "priceChange": {"h24": -40.0},
    }
    viable, reason = temp_pool.evaluate_token_profitability(rec, Decimal("2.0"), pair_data)
    assert viable is False
    assert "Colapso catastrófico" in reason
    assert "-80.0%" in reason


def test_evaluate_profitability_consolidated_tier_protection(temp_pool: PriorityPoolManager) -> None:
    """Tokens CONSOLIDATED com alta liquidez (>= $50.000) não devem ser eliminados por correção normal."""
    meta = make_token_metadata("RayToken111111111111111111111111111111111111", "RAY", "Raydium", tier="CONSOLIDATED")
    temp_pool.register_executed_token(meta, entry_price=Decimal("10.0"))

    rec = temp_pool.get_token("RayToken111111111111111111111111111111111111")
    assert rec is not None
    rec.highest_price_seen = 10.0

    pair_data = {
        "liquidity": {"usd": 500000.0},
        "volume": {"h24": 250000.0},
        "priceChange": {"h24": -15.0},
    }
    # Preço caiu 76% do topo histórico mas tem $500k de liquidez
    viable, reason = temp_pool.evaluate_token_profitability(rec, Decimal("2.40"), pair_data)
    assert viable is True
    assert "saudável" in reason


def test_evaluate_profitability_dead_volume(temp_pool: PriorityPoolManager) -> None:
    """Tokens com volume 24h praticamente nulo (< $500) devem ser eliminados por abandono de mercado."""
    meta = make_token_metadata("GhostToken1111111111111111111111111111111111", "GHOST", "Ghost Token")
    temp_pool.register_executed_token(meta, entry_price=Decimal("1.50"))
    rec = temp_pool.get_token("GhostToken1111111111111111111111111111111111")
    assert rec is not None

    pair_data = {
        "liquidity": {"usd": 15000.0},
        "volume": {"h24": 150.0},
        "priceChange": {"h24": 0.0},
    }
    viable, reason = temp_pool.evaluate_token_profitability(rec, Decimal("1.50"), pair_data)
    assert viable is False
    assert "Volume 24h morto" in reason


def test_evaluate_profitability_steep_dump(temp_pool: PriorityPoolManager) -> None:
    """Tokens com queda abrupta diária (<-50% em 24h) com liquidez moderada devem ser eliminados."""
    meta = make_token_metadata("Dump24Token111111111111111111111111111111111", "D24", "Dump24 Token")
    temp_pool.register_executed_token(meta, entry_price=Decimal("1.50"))
    rec = temp_pool.get_token("Dump24Token111111111111111111111111111111111")
    assert rec is not None

    pair_data = {
        "liquidity": {"usd": 12000.0},
        "volume": {"h24": 15000.0},
        "priceChange": {"h24": -65.0},
    }
    viable, reason = temp_pool.evaluate_token_profitability(rec, Decimal("0.80"), pair_data)
    assert viable is False
    assert "Despejo contínuo em 24h" in reason


def test_purge_dead_or_unprofitable_tokens(temp_pool: PriorityPoolManager, tmp_path: Path) -> None:
    """Testa a limpeza em lote com preservação exclusiva de tokens saudáveis."""
    tok_healthy = make_token_metadata("Healthy111111111111111111111111111111111111", "HLT", "Healthy Token", tier="CONSOLIDATED")
    tok_dead = make_token_metadata("Dead1111111111111111111111111111111111111111", "DEAD", "Dead Token")
    tok_drain = make_token_metadata("Drain111111111111111111111111111111111111111", "DRN", "Drained Token")

    temp_pool.register_executed_token(tok_healthy, entry_price=Decimal("2.0"))
    temp_pool.register_executed_token(tok_dead, entry_price=Decimal("1.0"))
    temp_pool.register_executed_token(tok_drain, entry_price=Decimal("1.0"))
    assert temp_pool.count() == 3

    market_prices = {
        "Healthy111111111111111111111111111111111111": Decimal("2.0"),
        # Dead token não tem preço no feed
        "Drain111111111111111111111111111111111111111": Decimal("0.50"),
    }

    pairs_data = {
        "Healthy111111111111111111111111111111111111": {
            "liquidity": {"usd": 50000.0},
            "volume": {"h24": 30000.0},
            "priceChange": {"h24": 5.0},
        },
        "Drain111111111111111111111111111111111111111": {
            "liquidity": {"usd": 1500.0},  # Drenado
            "volume": {"h24": 200.0},
            "priceChange": {"h24": -20.0},
        },
    }

    purged = temp_pool.purge_dead_or_unprofitable_tokens(market_prices, pairs_data)
    assert len(purged) == 2
    purged_addrs = {p["token_address"] for p in purged}
    assert "Dead1111111111111111111111111111111111111111" in purged_addrs
    assert "Drain111111111111111111111111111111111111111" in purged_addrs

    assert temp_pool.count() == 1
    assert temp_pool.get_token("Healthy111111111111111111111111111111111111") is not None

    # Verifica persistência no disco
    reloaded = PriorityPoolManager(mode="paper", data_dir=str(tmp_path))
    assert reloaded.count() == 1
    assert reloaded.get_token("Healthy111111111111111111111111111111111111") is not None


@pytest.mark.asyncio
async def test_orchestrator_audit_and_purge_priority_pool(tmp_path: Path) -> None:
    """Testa integração de audit_and_purge_priority_pool no VertexBotOrchestrator."""
    db_file = tmp_path / "test_purge.db"
    settings = Settings(
        SQLITE_DB_PATH=str(db_file),
        EXECUTION_MODE="PAPER",
        MIN_LIQUIDITY_USD=Decimal("5000.0"),
    )
    db = DatabaseManager(str(db_file))
    await db.initialize()

    orch = VertexBotOrchestrator(settings=settings, db=db)
    # Configura priority_pool apontando para tmp_path
    orch.priority_pool = PriorityPoolManager(mode="paper", data_dir=str(tmp_path))

    tok_ok = make_token_metadata("OkToken111111111111111111111111111111111111", "OK", "Ok Token", tier="CONSOLIDATED")
    tok_rug = make_token_metadata("RugToken11111111111111111111111111111111111", "RUG", "Rugged Token")
    orch.priority_pool.register_executed_token(tok_ok, entry_price=Decimal("1.50"))
    orch.priority_pool.register_executed_token(tok_rug, entry_price=Decimal("1.0"))
    assert orch.priority_pool.count() == 2

    # Mock no price_feed
    orch.price_feed = MagicMock()
    orch.price_feed.fetch_prices = AsyncMock(return_value={
        "OkToken111111111111111111111111111111111111": Decimal("1.50"),
    })
    orch.price_feed.get_pair_data = MagicMock(side_effect=lambda addr: {
        "OkToken111111111111111111111111111111111111": {
            "liquidity": {"usd": 40000.0},
            "volume": {"h24": 15000.0},
            "priceChange": {"h24": 2.0},
        }
    }.get(addr))

    res = await orch.audit_and_purge_priority_pool("PAPER")
    assert res["mode"] == "PAPER"
    assert res["purged_count"] == 1
    assert res["remaining_count"] == 1
    assert orch.priority_pool.count() == 1
    assert orch.priority_pool.get_token("OkToken111111111111111111111111111111111111") is not None
    assert orch.priority_pool.get_token("RugToken11111111111111111111111111111111111") is None
    await db.close()


@pytest.mark.asyncio
async def test_dashboard_server_purge_endpoint(tmp_path: Path) -> None:
    """Testa o endpoint POST /api/tokens/priority/purge do dashboard."""
    from src.dashboard.server import DashboardServer

    db_file = tmp_path / "test_dash_purge.db"
    db = DatabaseManager(str(db_file))
    await db.initialize()

    orch = MagicMock()
    orch.audit_and_purge_priority_pool = AsyncMock(return_value={
        "mode": "PAPER",
        "purged_count": 1,
        "remaining_count": 2,
        "purged": [{"token_address": "AddrPurged", "reason": "Liquidez drenada"}],
    })

    server = DashboardServer(db=db, orchestrator=orch)
    req = MagicMock(spec=web.Request)
    req.query = {"mode": "paper"}
    req.can_read_body = False

    resp = await server.handle_purge_priority_tokens(req)
    assert resp.status == 200
    import json
    data = json.loads(resp.text)
    assert data["status"] == "success"
    assert data["data"]["purged_count"] == 1
    assert data["data"]["remaining_count"] == 2
    orch.audit_and_purge_priority_pool.assert_awaited_once_with("paper")
    await db.close()
