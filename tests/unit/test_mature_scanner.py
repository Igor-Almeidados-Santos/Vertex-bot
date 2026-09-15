"""
Testes Unitários para o Scanner de Tokens Maduros (Janela de 1h a 5h).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

from src.database.models import TokenMetadata
from src.scanner.listener import create_scanner
from src.scanner.mature_scanner import MatureTokenScanner


def test_calculate_age_from_ms() -> None:
    """Valida cálculo de idade em horas a partir de timestamps em milissegundos."""
    now_ms = 1789160000000
    # 2 horas atrás: 2 * 3600 * 1000 = 7,200,000 ms
    two_hours_ago_ms = now_ms - 7200000
    age = MatureTokenScanner.calculate_age_from_ms(two_hours_ago_ms, now_ms)
    assert round(age, 2) == 2.00

    # 30 minutos atrás: 0.5 horas
    half_hour_ago_ms = now_ms - 1800000
    age_half = MatureTokenScanner.calculate_age_from_ms(half_hour_ago_ms, now_ms)
    assert round(age_half, 2) == 0.50


def test_calculate_age_from_iso() -> None:
    """Valida cálculo de idade em horas a partir de strings ISO 8601 UTC."""
    now = datetime(2026, 9, 11, 20, 0, 0, tzinfo=UTC)
    iso_2h_ago = (now - timedelta(hours=2.5)).isoformat().replace("+00:00", "Z")

    age = MatureTokenScanner.calculate_age_from_iso(iso_2h_ago, now)
    assert age is not None
    assert round(age, 2) == 2.50


def test_age_gate_filtering() -> None:
    """Valida se o filtro de maturidade aceita apenas tokens na janela de 15m a 3h [0.25h, 3.0h]."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    scanner = MatureTokenScanner(
        detection_queue=queue,
        min_age_hours=0.25,  # 15 min
        max_age_hours=3.0,   # 3 horas
    )

    now_ms = 1789160000000

    # Token 1: 5 minutos (0.08h) -> Deve ser rejeitado por ser jovem demais (< 15m)
    age_5m = MatureTokenScanner.calculate_age_from_ms(now_ms - 300000, now_ms)
    assert not (scanner.min_age_hours <= age_5m <= scanner.max_age_hours)

    # Token 2: 25 minutos (0.41h) -> Deve ser aceito (em janela de graduação)
    age_25m = MatureTokenScanner.calculate_age_from_ms(now_ms - 1500000, now_ms)
    assert scanner.min_age_hours <= age_25m <= scanner.max_age_hours

    # Token 3: 1.5 horas -> Deve ser aceito
    age_1_5h = MatureTokenScanner.calculate_age_from_ms(now_ms - 5400000, now_ms)
    assert scanner.min_age_hours <= age_1_5h <= scanner.max_age_hours

    # Token 4: 4 horas -> Deve ser rejeitado por ser muito antigo (> 3h)
    age_4h = MatureTokenScanner.calculate_age_from_ms(now_ms - 14400000, now_ms)
    assert not (scanner.min_age_hours <= age_4h <= scanner.max_age_hours)


def test_create_scanner_factory_mature_pools() -> None:
    """Valida se a factory create_scanner instancia o MatureTokenScanner com defaults de 15m a 3h."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    mock_settings = MagicMock()
    mock_settings.SCANNER_PROVIDER = "MATURE_POOLS"
    mock_settings.MIN_TOKEN_AGE_HOURS = 0.25
    mock_settings.MAX_TOKEN_AGE_HOURS = 3.0
    mock_settings.MATURE_POOLS_POLL_INTERVAL_SEC = 5.0
    mock_settings.DEXSCREENER_API_BASE_URL = "https://api.dexscreener.com"
    mock_settings.GECKOTERMINAL_API_BASE_URL = "https://api.geckoterminal.com"

    scanner = create_scanner(mock_settings, queue)
    assert isinstance(scanner, MatureTokenScanner)
    assert scanner.min_age_hours == 0.25
    assert scanner.max_age_hours == 3.0


async def test_batch_dexscreener_lookup() -> None:
    """Valida consulta em lote na DexScreener agrupando até 30 tokens e selecionando maior liquidez."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    scanner = MatureTokenScanner(detection_queue=queue)

    mock_pairs_payload = {
        "pairs": [
            {
                "chainId": "solana",
                "dexId": "raydium",
                "pairAddress": "pool_low_liq",
                "baseToken": {"address": "tokenA", "symbol": "TKNA"},
                "liquidity": {"usd": 5000.0},
            },
            {
                "chainId": "solana",
                "dexId": "raydium",
                "pairAddress": "pool_high_liq",
                "baseToken": {"address": "tokenA", "symbol": "TKNA"},
                "liquidity": {"usd": 25000.0},
            },
            {
                "chainId": "ethereum",
                "baseToken": {"address": "tokenETH"},
                "liquidity": {"usd": 100000.0},
            },
        ]
    }

    async def mock_http_get(url: str) -> dict[str, Any]:
        return mock_pairs_payload

    scanner._http_get_json = mock_http_get  # type: ignore[method-assign]

    result = await scanner._query_dexscreener_pairs_batch(["tokenA", "tokenB"])
    assert "tokenA" in result
    assert result["tokenA"]["pairAddress"] == "pool_high_liq"
    assert "tokenETH" not in result


async def test_evaluate_and_enrich_token_with_preloaded_hint() -> None:
    """Valida que tokens com hint contendo pair_data são avaliados sem fazer novas requisições de rede."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    scanner = MatureTokenScanner(detection_queue=queue, min_age_hours=0.25, max_age_hours=3.0)

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    one_hour_ago_ms = now_ms - 3600 * 1000

    preloaded_pair = {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "pool_xyz",
        "baseToken": {"address": "token123", "symbol": "ABC", "name": "Abc Token"},
        "liquidity": {"usd": 15000.0},
        "pairCreatedAt": one_hour_ago_ms,
    }

    # Se tentar chamar _query_dexscreener_pair, falha o teste propositalmente
    async def failing_query(addr: str) -> None:
        raise AssertionError("Não deveria chamar _query_dexscreener_pair quando hint já tem dados!")

    scanner._query_dexscreener_pair = failing_query  # type: ignore[method-assign]

    token, is_perm = await scanner._evaluate_and_enrich_token(
        "token123",
        {"pair_data": preloaded_pair},
    )

    assert token is not None
    assert token.address == "token123"
    assert token.symbol == "ABC"
    assert token.initial_liquidity_usd == Decimal("15000.0")
    assert is_perm is True


async def test_transient_error_handling() -> None:
    """Valida que se houver falha de rede/rate limit na idade, o token não é marcado como permanente."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    scanner = MatureTokenScanner(detection_queue=queue)

    # Simula retorno None por 429 na DexScreener
    async def empty_query(addr: str) -> None:
        return None

    scanner._query_dexscreener_pair = empty_query  # type: ignore[method-assign]

    token, is_perm = await scanner._evaluate_and_enrich_token("token_transient", {})
    assert token is None
    assert is_perm is False  # Não descarta permanentemente!


async def test_maturing_pipeline_release() -> None:
    """Valida que tokens jovens são registrados em _maturing_tokens e liberados quando atingem 15m."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    scanner = MatureTokenScanner(
        detection_queue=queue,
        min_age_hours=0.25,  # 15 min
        max_age_hours=3.0,
    )

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    # 5 minutos atrás (jovem demais)
    five_min_ago_ms = now_ms - 300 * 1000

    young_pair = {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "pool_young",
        "baseToken": {"address": "token_young", "symbol": "YOUNG"},
        "liquidity": {"usd": 20000.0},
        "pairCreatedAt": five_min_ago_ms,
    }

    # Avaliação inicial: deve registrar em _maturing_tokens e não enfileirar
    token, is_perm = await scanner._evaluate_and_enrich_token("token_young", {"pair_data": young_pair})
    assert token is None
    assert is_perm is False
    assert "token_young" in scanner._maturing_tokens

    # Executa verificação de maturação: ainda tem apenas 5 minutos -> não deve liberar
    await scanner._check_maturing_tokens()
    assert queue.empty()
    assert "token_young" in scanner._maturing_tokens

    # Simula passagem de tempo: token agora tem 16 minutos (0.26h)
    sixteen_min_ago_ms = now_ms - 960 * 1000
    scanner._maturing_tokens["token_young"]["created_at_ms"] = sixteen_min_ago_ms
    young_pair["pairCreatedAt"] = sixteen_min_ago_ms

    # Executa verificação de maturação novamente: deve liberar e enfileirar!
    await scanner._check_maturing_tokens()
    assert not queue.empty()
    matured_token = await queue.get()
    assert matured_token.address == "token_young"
    assert matured_token.symbol == "YOUNG"
    assert "token_young" not in scanner._maturing_tokens
    assert "token_young" in scanner._seen_addresses


async def test_re_evaluation_for_swing_after_scalp() -> None:
    """Valida que um token visto no estágio de Scalp (0.5h) é reavaliado e liberado quando atinge Swing (2.5h)."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    scanner = MatureTokenScanner(
        detection_queue=queue,
        min_age_hours=0.5,
        max_age_hours=720.0,
        min_age_hours_swing=2.0,
        max_age_hours_swing=6.0,
        min_liquidity_usd=Decimal("5000.0"),
        min_liquidity_swing_usd=Decimal("20000.0"),
    )

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    thirty_min_ago_ms = now_ms - int(0.5 * 3600 * 1000)

    scalp_pair = {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "pool_scalp",
        "baseToken": {"address": "token_dual", "symbol": "DUAL"},
        "liquidity": {"usd": 8000.0},  # Válido para Scalp ($8k >= $5k), mas < $15k para incubadora direta de Swing
        "pairCreatedAt": thirty_min_ago_ms,
    }

    # 1. Primeira avaliação aos 30 minutos (Scalp)
    await scanner._process_single_candidate("token_dual", {"pair_data": scalp_pair})
    assert not queue.empty()
    token_scalp = await queue.get()
    assert token_scalp.address == "token_dual"
    assert "token_dual" in scanner._seen_addresses
    assert "token_dual" in scanner._seen_scalp
    assert "token_dual" not in scanner._seen_swing

    # 2. Re-tentativa antes de 2.0h -> _is_candidate_needed deve retornar False para evitar spam
    assert scanner._is_candidate_needed("token_dual") is False

    # 3. Token atinge 2.5 horas de vida
    two_and_half_hours_ago_ms = now_ms - int(2.5 * 3600 * 1000)
    swing_pair = {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "pool_scalp",
        "baseToken": {"address": "token_dual", "symbol": "DUAL"},
        "liquidity": {"usd": 35000.0},
        "pairCreatedAt": two_and_half_hours_ago_ms,
    }

    # Avança o relógio no tracker de idade
    scanner._last_age_check["token_dual"] = (2.5, datetime.now(UTC).timestamp())
    assert scanner._is_candidate_needed("token_dual") is True

    # Processa novamente como candidato de Swing
    await scanner._process_single_candidate("token_dual", {"pair_data": swing_pair})
    assert not queue.empty()
    token_swing = await queue.get()
    assert token_swing.address == "token_dual"
    assert "token_dual" in scanner._seen_swing
    # Agora que foi avaliado para Swing, _is_candidate_needed retorna False
    assert scanner._is_candidate_needed("token_dual") is False


async def test_liquidity_pre_filter() -> None:
    """Valida que tokens com liquidez conhecida inferior a $5.000 são descartados no pré-filtro."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    scanner = MatureTokenScanner(
        detection_queue=queue,
        min_liquidity_usd=Decimal("5000.0"),
    )

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    one_hour_ago_ms = now_ms - 3600 * 1000

    low_liq_pair = {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "pool_low",
        "baseToken": {"address": "token_low", "symbol": "LOW"},
        "liquidity": {"usd": 1200.0},  # Abaixo de $5.000!
        "pairCreatedAt": one_hour_ago_ms,
    }

    token, is_perm = await scanner._evaluate_and_enrich_token("token_low", {"pair_data": low_liq_pair})
    assert token is None
    assert is_perm is True  # Descarte permanente por liquidez insuficiente
    assert queue.empty()


async def test_swing_maturation_incubator_progression() -> None:
    """Valida que tokens na incubadora com alta liquidez são promovidos para a perna de Swing aos 120 min."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    scanner = MatureTokenScanner(
        detection_queue=queue,
        min_age_hours=0.5,
        max_age_hours=720.0,
        min_age_hours_swing=2.0,
        max_age_hours_swing=6.0,
        min_liquidity_usd=Decimal("5000.0"),
        swing_incubator_min_liquidity_usd=Decimal("15000.0"),
        min_liquidity_swing_usd=Decimal("20000.0"),
    )

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    # Inicialmente com 10 minutos (jovem)
    ten_min_ago_ms = now_ms - 600 * 1000

    young_pair = {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "pool_progression",
        "baseToken": {"address": "token_prog", "symbol": "PROG"},
        "liquidity": {"usd": 25000.0},
        "pairCreatedAt": ten_min_ago_ms,
    }

    # Registra na incubadora inicial de maturação (<30m)
    await scanner._process_single_candidate("token_prog", {"pair_data": young_pair})
    assert "token_prog" in scanner._maturing_tokens
    assert queue.empty()

    # Avança tempo para 31 minutos (liberação de Scalp)
    thirty_one_min_ago_ms = now_ms - 31 * 60 * 1000
    scanner._maturing_tokens["token_prog"]["created_at_ms"] = thirty_one_min_ago_ms
    young_pair["pairCreatedAt"] = thirty_one_min_ago_ms

    await scanner._check_maturing_tokens()
    # Deve liberar para Scalp
    assert not queue.empty()
    scalp_token = await queue.get()
    assert scalp_token.address == "token_prog"
    # E como tem liq >= $15k, deve ingressar na incubadora de Swing!
    assert "token_prog" in scanner._maturing_swing_tokens

    # Simula avanço de tempo para 2.1 horas (liberação de Swing)
    two_hours_ago_ms = now_ms - int(2.1 * 3600 * 1000)
    scanner._maturing_swing_tokens["token_prog"]["created_at_ms"] = two_hours_ago_ms
    young_pair["pairCreatedAt"] = two_hours_ago_ms

    # Mock de consulta atualizada na DexScreener
    async def mock_fresh_query(addr: str) -> dict[str, Any]:
        return young_pair

    scanner._query_dexscreener_pair = mock_fresh_query  # type: ignore[method-assign]

    await scanner._check_maturing_tokens()
    # Deve liberar para Swing!
    assert not queue.empty()
    swing_token = await queue.get()
    assert swing_token.address == "token_prog"
    assert "token_prog" not in scanner._maturing_swing_tokens
    assert "token_prog" in scanner._seen_swing

