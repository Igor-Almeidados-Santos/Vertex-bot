"""
Testes Unitários para Persistência de Auditoria e Rejeições no SQLite.
Garante que todo token reprovado é gravado com status REJECTED e justificativa.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from src.database.connection import DatabaseManager
from src.database.models import SecurityStatus, TokenMetadata
from src.database.repository import TokensRepository
from src.security.validator import SecurityValidator


@pytest.mark.asyncio
async def test_rejection_persistence_low_liquidity(tmp_path: Path):
    db_path = str(tmp_path / "test_rejection.db")

    db = DatabaseManager(db_path)
    await db.initialize()
    tokens_repo = TokensRepository(db)
    validator = SecurityValidator(tokens_repo=tokens_repo, min_liquidity_usd=Decimal("5000.0"))

    token = TokenMetadata(
        address="LOW_LIQ_TOKEN_111",
        dex="pumpfun",
        initial_liquidity_usd=Decimal("1200.0"),  # Abaixo de $5000
    )
    await tokens_repo.save_detected_token(token)

    # Verifica status inicial
    saved_initial = await tokens_repo.get_by_address(token.address)
    assert saved_initial is not None
    assert saved_initial["security_status"] == SecurityStatus.PENDING.value

    # Executa auditoria
    audit = await validator.audit_token(token)
    assert audit.is_approved is False
    assert audit.status == SecurityStatus.REJECTED
    assert "abaixo do mínimo" in (audit.rejection_reason or "")

    # Verifica persistência imediata no SQLite
    saved_after = await tokens_repo.get_by_address(token.address)
    assert saved_after is not None
    assert saved_after["security_status"] == SecurityStatus.REJECTED.value
    assert saved_after["rejection_reason"] is not None
    assert "abaixo do mínimo" in saved_after["rejection_reason"]
    assert float(saved_after["security_score"]) == 0.0

    await db.close()


@pytest.mark.asyncio
async def test_rejection_persistence_active_mint(tmp_path: Path):
    db_path = str(tmp_path / "test_rejection_mint.db")

    db = DatabaseManager(db_path)
    await db.initialize()
    tokens_repo = TokensRepository(db)
    validator = SecurityValidator(tokens_repo=tokens_repo)

    token = TokenMetadata(
        address="ACTIVE_MINT_TOKEN_222",
        dex="raydium",
        initial_liquidity_usd=Decimal("10000.0"),
    )
    await tokens_repo.save_detected_token(token)

    # Simula falha na Mint Authority (is_mint_revoked = False)
    audit = await validator.audit_token(token, mock_overrides={"is_mint_revoked": False})
    assert audit.is_approved is False
    assert audit.status == SecurityStatus.REJECTED

    # Confirma no SQLite
    saved = await tokens_repo.get_by_address(token.address)
    assert saved is not None
    assert saved["security_status"] == SecurityStatus.REJECTED.value
    assert "Mint Authority ATIVA" in (saved["rejection_reason"] or "")

    await db.close()

