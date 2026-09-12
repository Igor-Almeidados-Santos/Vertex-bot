"""
Testes Unitários para o RaydiumGraduationScanner e CompositeScanner (Modo Híbrido).
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from src.database.models import TokenMetadata
from src.scanner.composite_scanner import CompositeScanner
from src.scanner.graduation_scanner import RaydiumGraduationScanner
from src.scanner.listener import create_scanner


def test_parse_graduation_event_success() -> None:
    """Valida o parsing e cálculo de liquidez em USD de uma graduação para Raydium."""
    sample_event = {
        "txType": "createPool",
        "mint": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
        "pool": "BntF8z6YqNq4pE9vJkZp2qL6M5vJ8xZ1y9uW2qR3t4y5",
        "marketId": "Mkt1234567890",
        "solAmount": 85.0,
        "tokenAmount": 206000000.0,
        "symbol": "GRAD",
        "name": "Graduated Coin",
    }

    token = RaydiumGraduationScanner.parse_graduation_event(
        sample_event,
        sol_price_usd=Decimal("150.0"),
    )

    assert token is not None
    assert token.address == "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
    assert token.dex == "raydium"
    assert token.chain == "solana"
    assert token.pool_address == "BntF8z6YqNq4pE9vJkZp2qL6M5vJ8xZ1y9uW2qR3t4y5"
    assert token.symbol == "GRAD"
    # Liquidez = 85 SOL * $150 * 2 = $25,500 USD
    assert token.initial_liquidity_usd == Decimal("25500.0")


def test_parse_graduation_event_fallback_liquidity() -> None:
    """Valida fallback de liquidez quando solAmount não é fornecido."""
    sample_event = {
        "mint": "MintXYZ999",
        "pool": "PoolXYZ999",
    }

    token = RaydiumGraduationScanner.parse_graduation_event(sample_event)
    assert token is not None
    assert token.address == "MintXYZ999"
    assert token.initial_liquidity_usd == Decimal("65000.0")


def test_parse_graduation_event_invalid_missing_mint() -> None:
    """Valida que eventos sem mint são descartados."""
    sample_event = {"pool": "PoolXYZ"}
    token = RaydiumGraduationScanner.parse_graduation_event(sample_event)
    assert token is None


async def test_composite_scanner_lifecycle() -> None:
    """Valida que o CompositeScanner inicializa e encerra todos os sub-scanners coordenadamente."""
    sub_scanner1 = MagicMock()
    sub_scanner1.start = AsyncMock()
    sub_scanner1.stop = AsyncMock()

    sub_scanner2 = MagicMock()
    sub_scanner2.start = AsyncMock()
    sub_scanner2.stop = AsyncMock()

    composite = CompositeScanner([sub_scanner1, sub_scanner2])

    await composite.start()
    assert composite.is_running is True
    sub_scanner1.start.assert_awaited_once()
    sub_scanner2.start.assert_awaited_once()

    await composite.stop()
    assert composite.is_running is False
    sub_scanner1.stop.assert_awaited_once()
    sub_scanner2.stop.assert_awaited_once()


def test_create_scanner_factory_hybrid() -> None:
    """Valida que a factory create_scanner instancia o CompositeScanner com ambos os motores no modo HYBRID."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    mock_settings = MagicMock()
    mock_settings.SCANNER_PROVIDER = "HYBRID"
    mock_settings.MIN_TOKEN_AGE_HOURS = 0.25
    mock_settings.MAX_TOKEN_AGE_HOURS = 3.0
    mock_settings.MATURE_POOLS_POLL_INTERVAL_SEC = 5.0
    mock_settings.DEXSCREENER_API_BASE_URL = "https://api.dexscreener.com"
    mock_settings.GECKOTERMINAL_API_BASE_URL = "https://api.geckoterminal.com"
    mock_settings.PUMPPORTAL_WS_URL = "wss://pumpportal.fun/api/data"
    mock_settings.GRADUATION_WS_URL = "wss://pumpportal.fun/api/data"
    mock_settings.ESTIMATED_SOL_PRICE_USD = Decimal("150.0")

    scanner = create_scanner(mock_settings, queue)
    assert isinstance(scanner, CompositeScanner)
    assert len(scanner.scanners) == 2


def test_create_scanner_factory_graduations() -> None:
    """Valida que a factory create_scanner instancia o RaydiumGraduationScanner isolado no modo GRADUATIONS."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    mock_settings = MagicMock()
    mock_settings.SCANNER_PROVIDER = "GRADUATIONS"
    mock_settings.GRADUATION_WS_URL = "wss://pumpportal.fun/api/data"
    mock_settings.ESTIMATED_SOL_PRICE_USD = Decimal("150.0")

    scanner = create_scanner(mock_settings, queue)
    assert isinstance(scanner, RaydiumGraduationScanner)
