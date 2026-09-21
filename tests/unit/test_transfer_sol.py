"""Testes unitários para o utilitário de transferência de SOL (scripts/transfer_sol.py)."""

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from scripts.transfer_sol import (
    DEFAULT_FEE_LAMPORTS,
    LAMPORTS_PER_SOL,
    parse_sender_keypair,
)


def test_parse_sender_keypair_valid_base58() -> None:
    kp = Keypair()
    b58_str = str(kp)
    parsed = parse_sender_keypair(b58_str)
    assert parsed.pubkey() == kp.pubkey()


def test_parse_sender_keypair_json_array() -> None:
    kp = Keypair()
    raw_bytes = list(bytes(kp))
    json_str = str(raw_bytes)
    parsed = parse_sender_keypair(json_str)
    assert parsed.pubkey() == kp.pubkey()


def test_parse_sender_keypair_rejects_public_key() -> None:
    pubkey_str = "11111111111111111111111111111111"
    with pytest.raises(ValueError, match="Endereço Público"):
        parse_sender_keypair(pubkey_str)


def test_parse_sender_keypair_rejects_empty() -> None:
    with pytest.raises(ValueError, match="vazia"):
        parse_sender_keypair("")


def test_constants() -> None:
    assert DEFAULT_FEE_LAMPORTS == 5_000
    assert LAMPORTS_PER_SOL == 1_000_000_000

