"""Testes unitários para PriorityPoolManager e varredura por idade decrescente (oldest-to-newest)."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.database.models import TokenMetadata
from src.engine.priority_pool import PriorityPoolManager
from src.scanner.mature_scanner import MatureTokenScanner


def test_priority_pool_lifecycle(tmp_path: Path) -> None:
    pool = PriorityPoolManager(mode="paper", data_dir=tmp_path)
    assert pool.count() == 0

    token = TokenMetadata(
        address="So11111111111111111111111111111111111111112",
        chain="solana",
        dex="raydium",
        initial_liquidity_usd=Decimal("50000.0"),
        symbol="WSOL",
        name="Wrapped SOL",
        detection_timestamp=datetime.now(UTC),
        raw_event={"age_hours": 26000.0, "token_tier": "CONSOLIDATED"},
    )

    # 1. Registrar token executado
    rec = pool.register_executed_token(token, entry_price=Decimal("150.0"), strategy_type="SWING")
    assert rec.address == token.address
    assert rec.tier == "CONSOLIDATED"
    assert rec.total_trades_count == 1
    assert pool.is_priority_token(token.address)

    # 2. Re-execução incrementa contador
    rec2 = pool.register_executed_token(token, entry_price=Decimal("155.0"), strategy_type="SCALP")
    assert rec2.total_trades_count == 2

    # 3. Registrar resultado de trade lucrativo
    pool.record_trade_result(
        token_address=token.address,
        realized_pnl_usd=Decimal("25.50"),
        exit_price=Decimal("175.50"),
        exit_reason="TRAILING_STOP",
    )
    rec_updated = pool.get_candidate_record(token.address)
    assert rec_updated is not None
    assert rec_updated.successful_trades_count == 1
    assert rec_updated.total_realized_pnl_usd == 25.50

    # 4. Avaliar saúde do token
    # Continua saudável
    is_alive = pool.update_token_health(token.address, current_price=Decimal("170.0"), current_liquidity_usd=Decimal("45000.0"))
    assert is_alive
    assert pool.is_priority_token(token.address)

    # Drenagem de liquidez -> deixa de ser prioritário
    is_alive2 = pool.update_token_health(token.address, current_price=Decimal("170.0"), current_liquidity_usd=Decimal("2000.0"))
    assert not is_alive2
    assert not pool.is_priority_token(token.address)

    # 5. Teste de persistência e recarga
    pool2 = PriorityPoolManager(mode="paper", data_dir=tmp_path)
    assert pool2.count() == 1
    rec_reloaded = pool2.get_candidate_record(token.address)
    assert rec_reloaded is not None
    assert rec_reloaded.total_trades_count == 2
    assert rec_reloaded.symbol == "WSOL"


def test_candidate_age_extraction_and_sorting() -> None:
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    scanner = MatureTokenScanner(
        detection_queue=queue,
        min_age_hours=3.0,
        max_age_hours=720.0,
        enable_established_pools=True,
        min_age_hours_consolidated=720.0,
        max_age_hours_consolidated=87600.0,
    )

    now_utc = datetime(2026, 9, 21, 16, 0, 0, tzinfo=UTC)

    # Token 1: Raydium (criado há ~3 anos = ~26.280 horas atrás)
    hint_raydium = {
        "pair_data": {"pairCreatedAt": (now_utc.timestamp() - 26280 * 3600) * 1000},
    }
    age_ray = scanner._extract_approx_candidate_age_hours(hint_raydium, now_utc)
    assert age_ray > 25000.0

    # Token 2: Novo (criado há 5 horas)
    hint_new = {
        "pair_data": {"pairCreatedAt": (now_utc.timestamp() - 5 * 3600) * 1000},
    }
    age_new = scanner._extract_approx_candidate_age_hours(hint_new, now_utc)
    assert 4.9 <= age_new <= 5.1

    # Token 3: Médio (criado há 100 horas)
    hint_med = {
        "pool_created_at": "2026-09-17T12:00:00Z",
    }
    age_med = scanner._extract_approx_candidate_age_hours(hint_med, now_utc)
    assert age_med > 90.0

    # Teste de ordenação dos candidatos
    candidates = [
        ("addr_new", hint_new),
        ("addr_ray", hint_raydium),
        ("addr_med", hint_med),
    ]
    candidates.sort(
        key=lambda item: scanner._extract_approx_candidate_age_hours(item[1], now_utc),
        reverse=True,
    )

    # Raydium (mais antigo) deve vir em PRIMEIRO lugar
    assert candidates[0][0] == "addr_ray"
    assert candidates[1][0] == "addr_med"
    assert candidates[2][0] == "addr_new"


def test_priority_pool_cross_mode_integration(tmp_path: Path) -> None:
    """Verifica que a simulação (PAPER) integra tokens aprovados e saudáveis do modo LIVE."""
    # 1. Cria pool LIVE com 1 token ativo/saudável e 1 token morto/drenado
    pool_live = PriorityPoolManager(mode="live", data_dir=tmp_path)

    tok_live_healthy = TokenMetadata(
        address="LiveHealthyToken11111111111111111111111111",
        chain="solana",
        dex="raydium",
        initial_liquidity_usd=Decimal("60000.0"),
        symbol="LIVEOK",
        name="Live Healthy Token",
        detection_timestamp=datetime.now(UTC),
        raw_event={"token_tier": "CONSOLIDATED"},
    )
    tok_live_dead = TokenMetadata(
        address="LiveDeadToken222222222222222222222222222222",
        chain="solana",
        dex="raydium",
        initial_liquidity_usd=Decimal("10000.0"),
        symbol="LIVEDEAD",
        name="Live Dead Token",
        detection_timestamp=datetime.now(UTC),
        raw_event={"token_tier": "EMERGING"},
    )

    rec_healthy = pool_live.register_executed_token(tok_live_healthy, entry_price=Decimal("10.0"))
    assert rec_healthy.origin_mode == "LIVE"

    pool_live.register_executed_token(tok_live_dead, entry_price=Decimal("1.0"))
    # Mata o token por drenagem de liquidez
    pool_live.update_token_health(tok_live_dead.address, current_price=Decimal("0.05"), current_liquidity_usd=Decimal("500.0"))

    # 2. Inicializa pool PAPER
    pool_paper = PriorityPoolManager(mode="paper", data_dir=tmp_path)

    tok_paper_native = TokenMetadata(
        address="PaperNativeToken33333333333333333333333333",
        chain="solana",
        dex="raydium",
        initial_liquidity_usd=Decimal("20000.0"),
        symbol="PAPERNAT",
        name="Paper Native Token",
        detection_timestamp=datetime.now(UTC),
        raw_event={"token_tier": "EMERGING"},
    )
    rec_paper = pool_paper.register_executed_token(tok_paper_native, entry_price=Decimal("2.5"))
    assert rec_paper.origin_mode == "PAPER"

    # 3. Consulta tokens no modo simulação (PAPER)
    all_paper_tokens = pool_paper.get_all_priority_tokens()
    addrs = [t["address"] for t in all_paper_tokens]

    # Deve constar o token nativo da simulação E o token saudável do modo real
    assert tok_paper_native.address in addrs
    assert tok_live_healthy.address in addrs
    # NÃO deve constar o token morto do live
    assert tok_live_dead.address not in addrs

    # Verifica origem dos tokens
    tok_map = {t["address"]: t for t in all_paper_tokens}
    assert tok_map[tok_paper_native.address]["origin_mode"] == "PAPER"
    assert tok_map[tok_live_healthy.address]["origin_mode"] == "LIVE"

    # 4. Se o token do LIVE também for negociado na simulação, sua origem passa a ser BOTH
    pool_paper.register_executed_token(tok_live_healthy, entry_price=Decimal("10.5"))
    all_paper_tokens_after = pool_paper.get_all_priority_tokens()
    tok_map_after = {t["address"]: t for t in all_paper_tokens_after}
    assert tok_map_after[tok_live_healthy.address]["origin_mode"] == "BOTH"


