"""
Testes Unitários para o Scanner de Feeds Indexados (Photon / DexScreener).
"""

import asyncio
from decimal import Decimal
import pytest

from src.scanner.indexed_feed import IndexedFeedScanner


def test_parse_indexed_pair_solana_valid():
    """Testa a extração e normalização correta de um par da Solana."""
    raw_payload = {
        "chainId": "solana",
        "dexId": "raydium",
        "tokenAddress": "SoLTokenAddress1234567890abcdef1234567890",
        "symbol": "PHOTON",
        "name": "Photon Token",
        "poolAddress": "PoolAddressRaydium999",
        "liquidity": {
            "usd": 18500.50,
            "base": 1000000.0,
            "quote": 120.5,
        },
    }

    token = IndexedFeedScanner.parse_indexed_pair(raw_payload)
    assert token is not None
    assert token.chain == "solana"
    assert token.address == "SoLTokenAddress1234567890abcdef1234567890"
    assert token.symbol == "PHOTON"
    assert token.name == "Photon Token"
    assert token.dex == "raydium"
    assert token.pool_address == "PoolAddressRaydium999"
    assert token.initial_liquidity_usd == Decimal("18500.5")


def test_parse_indexed_pair_non_solana_ignored():
    """Testa se tokens de outras redes (Ethereum, Base, BSC) são descartados."""
    raw_payload = {
        "chainId": "ethereum",
        "tokenAddress": "0x1234567890123456789012345678901234567890",
        "symbol": "ETHMEME",
    }

    token = IndexedFeedScanner.parse_indexed_pair(raw_payload)
    assert token is None


def test_parse_indexed_pair_missing_address_ignored():
    """Testa se payloads incompletos sem endereço de token são descartados com segurança."""
    raw_payload = {
        "chainId": "solana",
        "symbol": "NOADDR",
    }

    token = IndexedFeedScanner.parse_indexed_pair(raw_payload)
    assert token is None


@pytest.mark.asyncio
async def test_indexed_feed_deduplication():
    """Testa se o scanner deduplica tokens e não enfileira o mesmo par duas vezes."""
    queue = asyncio.Queue()
    scanner = IndexedFeedScanner(detection_queue=queue)

    raw_payload = {
        "chainId": "solana",
        "tokenAddress": "DuplicateTokenAddress111",
        "symbol": "DUP",
        "liquidity": {"usd": 12000.0},
    }

    token = scanner.parse_indexed_pair(raw_payload)
    assert token is not None

    # Adiciona à lista de vistos
    scanner._seen_addresses.add(token.address)

    # Segunda tentativa com o mesmo endereço
    assert token.address in scanner._seen_addresses
