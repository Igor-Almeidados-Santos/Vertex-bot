"""
Testes Unitários para o Scanner de Streaming PumpPortal em Tempo Real.
"""

import asyncio
from decimal import Decimal
from unittest.mock import MagicMock

from src.database.models import TokenMetadata
from src.scanner.listener import create_scanner
from src.scanner.pumpportal import PumpPortalScanner


def test_parse_pumpportal_event_valid() -> None:
    """Testa a extração e normalização de um evento válido de criação no PumpPortal."""
    raw_payload = {
        "txType": "create",
        "mint": "6p6xgHyF7Ae3NiNd6UBoGazTZBoPtVM4UKuvuvBpump",
        "name": "Vertex Pump Coin",
        "symbol": "VERTEX",
        "bondingCurveKey": "BCurveKey1111111111111111111111111111111111",
        "vTokensInBondingCurve": 1073000000,
        "vSolInBondingCurve": 30.0,
        "marketCapSol": 30.0,
    }

    token = PumpPortalScanner.parse_pumpportal_event(raw_payload)
    assert token is not None
    assert token.chain == "solana"
    assert token.address == "6p6xgHyF7Ae3NiNd6UBoGazTZBoPtVM4UKuvuvBpump"
    assert token.symbol == "VERTEX"
    assert token.name == "Vertex Pump Coin"
    assert token.dex == "pumpfun"
    assert token.pool_address == "BCurveKey1111111111111111111111111111111111"
    # 30.0 SOL * $150 = $4500.00
    assert token.initial_liquidity_usd == Decimal("4500.0")


def test_parse_pumpportal_event_missing_mint() -> None:
    """Testa se eventos sem o campo mint são ignorados com segurança."""
    raw_payload = {
        "txType": "create",
        "name": "Invalid Token",
    }
    token = PumpPortalScanner.parse_pumpportal_event(raw_payload)
    assert token is None


def test_parse_pumpportal_event_default_liquidity() -> None:
    """Testa se o valor default de liquidez ($5,000) é atribuído caso vSolInBondingCurve esteja ausente."""
    raw_payload = {
        "txType": "create",
        "mint": "TokenWithNoVSol111111111111111111111111111111",
        "symbol": "NODATA",
    }
    token = PumpPortalScanner.parse_pumpportal_event(raw_payload)
    assert token is not None
    assert token.initial_liquidity_usd == Decimal("5000.0")


def test_create_scanner_factory_pumpportal() -> None:
    """Valida se a factory create_scanner instancia o PumpPortalScanner quando configurado."""
    queue: asyncio.Queue[TokenMetadata] = asyncio.Queue()
    mock_settings = MagicMock()
    mock_settings.SCANNER_PROVIDER = "PUMPPORTAL"
    mock_settings.PUMPPORTAL_WS_URL = "wss://pumpportal.fun/api/data"

    scanner = create_scanner(mock_settings, queue)
    assert isinstance(scanner, PumpPortalScanner)
    assert scanner.ws_url == "wss://pumpportal.fun/api/data"

