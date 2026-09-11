"""
Testes Unitários para Parsers de Logs On-Chain (Raydium e Pump.fun).
"""

from decimal import Decimal
import pytest

from src.scanner.parser import (
    OnChainLogParser,
    PUMPFUN_PROGRAM_ID,
    RAYDIUM_AMM_V4_PROGRAM_ID,
)


def test_raydium_log_parser_valid():
    payload = {
        "params": {
            "result": {
                "value": {
                    "signature": "sig_raydium_success_123",
                    "logs": [
                        f"Program {RAYDIUM_AMM_V4_PROGRAM_ID} invoke [1]",
                        "Program log: initialize2: InitializeInstruction2",
                        f"Program {RAYDIUM_AMM_V4_PROGRAM_ID} success",
                    ],
                }
            }
        }
    }

    token = OnChainLogParser.parse_log_notification(payload)
    assert token is not None
    assert token.dex == "raydium"
    assert token.chain == "solana"
    assert token.address == "sig_raydium_success_123"
    assert token.initial_liquidity_usd >= Decimal("5000.0")


def test_pumpfun_log_parser_valid():
    payload = {
        "params": {
            "result": {
                "value": {
                    "signature": "sig_pumpfun_success_456",
                    "logs": [
                        f"Program {PUMPFUN_PROGRAM_ID} invoke [1]",
                        "Program log: Instruction: Create",
                        f"Program {PUMPFUN_PROGRAM_ID} success",
                    ],
                }
            }
        }
    }

    token = OnChainLogParser.parse_log_notification(payload)
    assert token is not None
    assert token.dex == "pumpfun"
    assert token.chain == "solana"
    assert token.address == "sig_pumpfun_success_456"


def test_unrelated_logs_ignored():
    payload = {
        "params": {
            "result": {
                "value": {
                    "signature": "sig_transfer_789",
                    "logs": [
                        "Program 11111111111111111111111111111111 invoke [1]",
                        "Program log: Instruction: Transfer",
                        "Program 11111111111111111111111111111111 success",
                    ],
                }
            }
        }
    }

    token = OnChainLogParser.parse_log_notification(payload)
    assert token is None
