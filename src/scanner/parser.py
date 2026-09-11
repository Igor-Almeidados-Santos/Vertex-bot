"""
Parser de Logs On-Chain para Detecção de Novos Pares em DEXes (Solana).
Suporte a Raydium AMM v4 e Pump.fun Bonding Curves.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from src.database.models import TokenMetadata
from src.utils.exceptions import ParseError
from src.utils.logger import setup_logger

logger = setup_logger("vertex.scanner.parser")

# Endereços conhecidos de programas na Solana
RAYDIUM_AMM_V4_PROGRAM_ID = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class OnChainLogParser:
    """Decodifica notificações de logs WebSocket da Solana para identificar novos pares."""

    @staticmethod
    def parse_log_notification(payload: dict[str, Any]) -> Optional[TokenMetadata]:
        """
        Avalia o payload de log retornado pelo WebSocket RPC e extrai dados do par detectado.
        Retorna TokenMetadata se for um novo par válido; caso contrário, None.
        """
        try:
            params = payload.get("params", {})
            result = params.get("result", {})
            value = result.get("value", {})
            logs = value.get("logs", [])
            signature = value.get("signature", "")

            if not logs:
                return None

            # 1. Checagem de Raydium AMM v4 (initialize2)
            raydium_token = OnChainLogParser._parse_raydium(logs, signature)
            if raydium_token:
                return raydium_token

            # 2. Checagem de Pump.fun (create)
            pump_token = OnChainLogParser._parse_pumpfun(logs, signature)
            if pump_token:
                return pump_token

            return None

        except Exception as exc:
            logger.debug("Falha transitória no parsing de log: %s", exc)
            return None

    @staticmethod
    def _parse_raydium(logs: list[str], signature: str) -> Optional[TokenMetadata]:
        """Identifica a instrução 'initialize2' do Raydium AMM v4."""
        is_raydium = False
        is_initialize = False

        for log in logs:
            if RAYDIUM_AMM_V4_PROGRAM_ID in log:
                is_raydium = True
            if "initialize2" in log or "Instruction: Initialize2" in log:
                is_initialize = True

        if is_raydium and is_initialize:
            # Em evento real de WebSocket, o endereço do token é extraído das contas envolvidas na tx
            # Aqui geramos o registro preliminar vinculado à assinatura para resolução detalhada no RPC
            logger.info(
                "Novo par Raydium detectado! Signature: %s",
                signature,
                extra={"event": "NEW_POOL_DETECTED", "signature": signature},
            )
            return TokenMetadata(
                address=signature,  # Chave temporária até resolução no RPC
                chain="solana",
                dex="raydium",
                pool_address=None,
                initial_liquidity_usd=Decimal("5000.0"),  # Baseline para auditoria
                detection_timestamp=datetime.now(timezone.utc),
                raw_event={"signature": signature, "dex": "raydium"},
            )
        return None

    @staticmethod
    def _parse_pumpfun(logs: list[str], signature: str) -> Optional[TokenMetadata]:
        """Identifica a criação de nova bonding curve no Pump.fun."""
        is_pump = False
        is_create = False

        for log in logs:
            if PUMPFUN_PROGRAM_ID in log:
                is_pump = True
            if "Instruction: Create" in log or "Program log: initialize" in log:
                is_create = True

        if is_pump and is_create:
            logger.info(
                "Novo par Pump.fun detectado! Signature: %s",
                signature,
                extra={"event": "NEW_PUMP_DETECTED", "signature": signature},
            )
            return TokenMetadata(
                address=signature,
                chain="solana",
                dex="pumpfun",
                pool_address=None,
                initial_liquidity_usd=Decimal("6000.0"),
                detection_timestamp=datetime.now(timezone.utc),
                raw_event={"signature": signature, "dex": "pumpfun"},
            )
        return None
