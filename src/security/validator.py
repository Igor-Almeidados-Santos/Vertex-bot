"""
Validador Central de Segurança do Vertex-bot (Camada de Triagem).
Executa os 6 Hard Gates de proteção de capital e emite laudo de auditoria.
"""

from decimal import Decimal

from src.database.models import SecurityAuditResult, SecurityStatus, TokenMetadata
from src.database.repository import TokensRepository
from src.scanner.client import ResilientRPCClient
from src.security.checks import SecurityChecks
from src.security.simulator import TransactionSimulator
from src.utils.logger import setup_logger

logger = setup_logger("vertex.security.validator")


class SecurityValidator:
    """Orquestrador da suíte de testes de segurança anti-golpe."""

    def __init__(
        self,
        tokens_repo: TokensRepository,
        rpc_client: ResilientRPCClient | None = None,
        min_liquidity_usd: Decimal = Decimal("5000.0"),
        max_top10_pct: float = 15.0,
        max_tax_pct: float = 3.0,
    ) -> None:
        self.tokens_repo: TokensRepository = tokens_repo
        self.rpc_client: ResilientRPCClient | None = rpc_client
        self.min_liquidity_usd: Decimal = min_liquidity_usd
        self.max_top10_pct: float = max_top10_pct
        self.max_tax_pct: float = max_tax_pct

    async def audit_token(
        self,
        token: TokenMetadata,
        mock_overrides: dict[str, object] | None = None,
    ) -> SecurityAuditResult:
        """
        Executa os Hard Gates sequencialmente. Ao primeiro sinal de perigo, reprova o token.
        """
        mo = mock_overrides or {}

        # 1. Checagem de Liquidez Mínima Inicial
        if token.initial_liquidity_usd < self.min_liquidity_usd:
            return await self._build_rejection(
                token.address,
                f"Liquidez inicial (${token.initial_liquidity_usd:.2f}) abaixo do mínimo (${self.min_liquidity_usd:.2f})",
            )

        # 2. Checagem de Mint Authority
        is_mint_revoked = await SecurityChecks.check_mint_authority(
            token.address,
            self.rpc_client,
            mock_override=mo.get("is_mint_revoked") if "is_mint_revoked" in mo else None,  # type: ignore
        )
        if not is_mint_revoked:
            return await self._build_rejection(
                token.address,
                "Mint Authority ATIVA (risco de emissão infinita)",
                is_mint_revoked=False,
            )

        # 3. Checagem de Freeze Authority
        is_freeze_revoked = await SecurityChecks.check_freeze_authority(
            token.address,
            self.rpc_client,
            mock_override=mo.get("is_freeze_revoked") if "is_freeze_revoked" in mo else None,  # type: ignore
        )
        if not is_freeze_revoked:
            return await self._build_rejection(
                token.address,
                "Freeze Authority ATIVA (risco de congelamento de contas)",
                is_mint_revoked=True,
                is_freeze_revoked=False,
            )

        # 4. Checagem de LP Queimada / Bloqueada
        is_lp_safe, burn_pct = await SecurityChecks.check_lp_status(
            pool_address=token.pool_address,
            token_address=token.address,
            dex=token.dex,
            rpc_client=self.rpc_client,
            mock_burn_pct=mo.get("lp_burn_pct") if "lp_burn_pct" in mo else None,  # type: ignore
        )
        if not is_lp_safe:
            return await self._build_rejection(
                token.address,
                f"LP não bloqueada ou queimada insuficientemente ({burn_pct:.1f}% < 98%)",
                is_mint_revoked=True,
                is_freeze_revoked=True,
                is_lp_safe=False,
                burn_pct=burn_pct,
            )

        # 5. Checagem de Concentração de Top 10 Holders
        top10_pct = await SecurityChecks.check_top10_concentration(
            token.address,
            self.rpc_client,
            mock_pct=mo.get("top10_pct") if "top10_pct" in mo else None,  # type: ignore
        )
        if top10_pct > self.max_top10_pct:
            return await self._build_rejection(
                token.address,
                f"Concentração de Top 10 Holders excessiva ({top10_pct:.1f}% > {self.max_top10_pct:.1f}%)",
                is_mint_revoked=True,
                is_freeze_revoked=True,
                is_lp_safe=True,
                burn_pct=burn_pct,
                top10_pct=top10_pct,
            )

        # 6. Simulação de Swap (Honeypot & Taxas)
        is_honeypot, buy_tax, sell_tax = await TransactionSimulator.simulate_swap(
            token.address,
            self.rpc_client,
            mock_taxes=mo.get("taxes") if "taxes" in mo else None,  # type: ignore
        )
        if is_honeypot:
            return await self._build_rejection(
                token.address,
                "Honeypot detectado: Simulação de venda revertida",
                is_mint_revoked=True,
                is_freeze_revoked=True,
                is_lp_safe=True,
                burn_pct=burn_pct,
                top10_pct=top10_pct,
                is_honeypot=True,
            )
        if buy_tax > self.max_tax_pct or sell_tax > self.max_tax_pct:
            return await self._build_rejection(
                token.address,
                f"Taxas abusivas detectadas (Buy: {buy_tax:.1f}%, Sell: {sell_tax:.1f}% > {self.max_tax_pct:.1f}%)",
                is_mint_revoked=True,
                is_freeze_revoked=True,
                is_lp_safe=True,
                burn_pct=burn_pct,
                top10_pct=top10_pct,
                buy_tax=buy_tax,
                sell_tax=sell_tax,
            )

        # Aprovado em todos os 6 Hard Gates!
        logger.info(
            "Token %s APROVADO em todos os testes de segurança! Score: 100/100",
            token.address,
            extra={"event": "TOKEN_APPROVED", "token_address": token.address},
        )
        audit = SecurityAuditResult(
            token_address=token.address,
            status=SecurityStatus.APPROVED,
            security_score=100.0,
            is_mint_revoked=True,
            is_freeze_revoked=True,
            is_lp_burned_or_locked=True,
            lp_burn_percentage=burn_pct,
            top10_holder_percentage=top10_pct,
            is_honeypot=False,
            buy_tax_percentage=buy_tax,
            sell_tax_percentage=sell_tax,
            rejection_reason=None,
        )
        await self.tokens_repo.update_audit_result(audit)
        return audit

    async def _build_rejection(
        self,
        token_address: str,
        reason: str,
        is_mint_revoked: bool = False,
        is_freeze_revoked: bool = False,
        is_lp_safe: bool = False,
        burn_pct: float = 0.0,
        top10_pct: float = 100.0,
        is_honeypot: bool = False,
        buy_tax: float = 0.0,
        sell_tax: float = 0.0,
    ) -> SecurityAuditResult:
        logger.warning(
            "Token %s REPROVADO na triagem: %s",
            token_address,
            reason,
            extra={"event": "TOKEN_REJECTED", "token_address": token_address, "reason": reason},
        )
        audit = SecurityAuditResult(
            token_address=token_address,
            status=SecurityStatus.REJECTED,
            security_score=0.0,
            is_mint_revoked=is_mint_revoked,
            is_freeze_revoked=is_freeze_revoked,
            is_lp_burned_or_locked=is_lp_safe,
            lp_burn_percentage=burn_pct,
            top10_holder_percentage=top10_pct,
            is_honeypot=is_honeypot,
            buy_tax_percentage=buy_tax,
            sell_tax_percentage=sell_tax,
            rejection_reason=reason,
        )
        await self.tokens_repo.update_audit_result(audit)
        return audit
