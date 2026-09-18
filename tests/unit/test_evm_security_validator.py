"""
Testes Unitários para o Validador de Segurança EVM (Base, Arbitrum, BSC).
"""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.database.models import SecurityStatus, TokenMetadata
from src.security.evm_validator import EVMSecurityValidator
from src.security.validator import SecurityValidator


@pytest.mark.asyncio
async def test_evm_validator_unsupported_chain() -> None:
    """Valida rejeição imediata se a rede não for suportada no validador EVM."""
    validator = EVMSecurityValidator()
    is_safe, reason, details = await validator.check_token_security(
        chain="unknown_chain",
        token_address="0x1234567890abcdef",
    )
    assert is_safe is False
    assert reason is not None
    assert "não suportada" in reason


@pytest.mark.asyncio
async def test_evm_validator_honeypot_detected() -> None:
    """Valida reprovação caso o contrato seja marcado como honeypot pelo GoPlus."""
    validator = EVMSecurityValidator()

    mock_resp = {
        "result": {
            "0xdeadbeef": {
                "is_honeypot": "1",
                "cannot_sell_all": "0",
                "is_open_source": "1",
                "buy_tax": "0.0",
                "sell_tax": "0.0",
            }
        }
    }

    mock_session = MagicMock()
    mock_get = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.status = 200
    mock_ctx.json = AsyncMock(return_value=mock_resp)
    mock_get.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
    mock_get.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_session.get = mock_get
    mock_session.closed = False
    validator._session = mock_session

    is_safe, reason, _ = await validator.check_token_security("base", "0xdeadbeef")
    assert is_safe is False
    assert reason is not None
    assert "Honeypot" in reason


@pytest.mark.asyncio
async def test_evm_validator_cannot_sell_all() -> None:
    """Valida reprovação se o contrato impedir venda total dos tokens."""
    validator = EVMSecurityValidator()

    mock_resp = {
        "result": {
            "0xdeadbeef": {
                "is_honeypot": "0",
                "cannot_sell_all": "1",
                "is_open_source": "1",
                "buy_tax": "0.0",
                "sell_tax": "0.0",
            }
        }
    }

    mock_session = MagicMock()
    mock_get = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.status = 200
    mock_ctx.json = AsyncMock(return_value=mock_resp)
    mock_get.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
    mock_get.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_session.get = mock_get
    mock_session.closed = False
    validator._session = mock_session

    is_safe, reason, _ = await validator.check_token_security("base", "0xdeadbeef")
    assert is_safe is False
    assert reason is not None
    assert "Venda restrita" in reason


@pytest.mark.asyncio
async def test_evm_validator_unverified_contract() -> None:
    """Valida reprovação se o código não for aberto/verificado no explorador."""
    validator = EVMSecurityValidator()

    mock_resp = {
        "result": {
            "0xdeadbeef": {
                "is_honeypot": "0",
                "cannot_sell_all": "0",
                "is_open_source": "0",
                "buy_tax": "0.0",
                "sell_tax": "0.0",
            }
        }
    }

    mock_session = MagicMock()
    mock_get = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.status = 200
    mock_ctx.json = AsyncMock(return_value=mock_resp)
    mock_get.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
    mock_get.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_session.get = mock_get
    mock_session.closed = False
    validator._session = mock_session

    is_safe, reason, _ = await validator.check_token_security("base", "0xdeadbeef")
    assert is_safe is False
    assert reason is not None
    assert "não verificado" in reason


@pytest.mark.asyncio
async def test_evm_validator_excessive_taxes() -> None:
    """Valida reprovação se as taxas de compra ou venda excederem o limite (3.0%)."""
    validator = EVMSecurityValidator(max_tax_pct=Decimal("3.0"))

    mock_resp = {
        "result": {
            "0xdeadbeef": {
                "is_honeypot": "0",
                "cannot_sell_all": "0",
                "is_open_source": "1",
                "buy_tax": "0.01",  # 1%
                "sell_tax": "0.05",  # 5% (> 3%)
            }
        }
    }

    mock_session = MagicMock()
    mock_get = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.status = 200
    mock_ctx.json = AsyncMock(return_value=mock_resp)
    mock_get.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
    mock_get.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_session.get = mock_get
    mock_session.closed = False
    validator._session = mock_session

    is_safe, reason, _ = await validator.check_token_security("base", "0xdeadbeef")
    assert is_safe is False
    assert reason is not None
    assert "Taxa de venda excessiva" in reason


@pytest.mark.asyncio
async def test_evm_validator_approved_clean_token() -> None:
    """Valida aprovação de token limpo na Base."""
    validator = EVMSecurityValidator(max_tax_pct=Decimal("3.0"))

    mock_resp = {
        "result": {
            "0x0000000000000000000000000000000000001234": {
                "is_honeypot": "0",
                "cannot_sell_all": "0",
                "is_open_source": "1",
                "buy_tax": "0.01",
                "sell_tax": "0.01",
                "is_blacklisted": "0",
                "is_proxy": "0",
            }
        }
    }

    mock_session = MagicMock()
    mock_get = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.status = 200
    mock_ctx.json = AsyncMock(return_value=mock_resp)
    mock_get.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
    mock_get.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_session.get = mock_get
    mock_session.closed = False
    validator._session = mock_session

    is_safe, reason, details = await validator.check_token_security(
        "base", "0x0000000000000000000000000000000000001234"
    )
    assert is_safe is True
    assert reason is None
    assert details.get("is_honeypot") == "0"


@pytest.mark.asyncio
async def test_security_validator_evm_pipeline_approval() -> None:
    """Valida pipeline integrado SecurityValidator roteando token da rede Base."""
    mock_repo = MagicMock()
    mock_repo.get_by_address = AsyncMock(return_value=None)
    mock_repo.save_token = AsyncMock()
    mock_repo.update_audit_result = AsyncMock()

    mock_rpc = MagicMock()

    sec_validator = SecurityValidator(
        rpc_client=mock_rpc,
        tokens_repo=mock_repo,
    )

    evm_token = TokenMetadata(
        address="0x4200000000000000000000000000000000000006",
        chain="base",
        dex="aerodrome",
        symbol="WETH",
        name="Wrapped Ether",
        initial_liquidity_usd=Decimal("50000.0"),
    )

    audit = await sec_validator.audit_token(
        token=evm_token,
        mock_overrides={
            "is_evm_safe": True,
            "market_dynamics_approved": True,
        },
    )

    assert audit.status == SecurityStatus.APPROVED
    assert audit.is_honeypot is False
    assert audit.security_score == 100.0
    mock_repo.update_audit_result.assert_awaited_once()
