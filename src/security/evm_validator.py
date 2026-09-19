"""
Validador de Segurança Especializado para Smart Contracts EVM (Base, Arbitrum, BSC, Ethereum).
Verifica Honeypots, taxas abusivas, funções de blacklist e renúncia de propriedade via GoPlus Security API.
"""

import asyncio
from decimal import Decimal
from typing import Any

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

from src.utils.logger import setup_logger

logger = setup_logger("vertex.security.evm")


class EVMSecurityValidator:
    """Valida a segurança estrutural de tokens em redes EVM (Base, Arbitrum, BSC, etc.)."""

    CHAIN_ID_MAP: dict[str, int] = {
        "base": 8453,
        "arbitrum": 42161,
        "arb": 42161,
        "bsc": 56,
        "binance": 56,
        "ethereum": 1,
        "eth": 1,
        "polygon": 137,
        "matic": 137,
        "polygon_pos": 137,
        "avalanche": 43114,
        "avax": 43114,
        "optimism": 10,
        "op": 10,
        "blast": 81457,
    }

    def __init__(
        self,
        goplus_base_url: str = "https://api.gopluslabs.io/api/v1",
        max_tax_pct: Decimal = Decimal("3.0"),
        timeout_seconds: float = 6.0,
    ) -> None:
        self.goplus_base_url: str = goplus_base_url.rstrip("/")
        self.max_tax_pct: Decimal = max_tax_pct
        self.timeout_seconds: float = timeout_seconds
        self._session: Any | None = None

    async def _get_session(self) -> Any:
        if HAS_AIOHTTP:
            if self._session is None or getattr(self._session, "closed", True):
                timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
                self._session = aiohttp.ClientSession(timeout=timeout, trust_env=True)
            return self._session
        return None

    async def check_token_security(
        self,
        chain: str,
        token_address: str,
        mock_override: bool | None = None,
    ) -> tuple[bool, str | None, dict[str, Any]]:
        """
        Executa auditoria de segurança do token na rede EVM especificada.
        Retorna (is_approved: bool, rejection_reason: str | None, details: dict[str, Any]).
        """
        if mock_override is not None:
            return (
                mock_override,
                None if mock_override else "Reprovado por mock de segurança EVM",
                {"mock": True},
            )

        chain_key = chain.lower().strip()
        chain_id = self.CHAIN_ID_MAP.get(chain_key)
        if chain_id is None:
            return (
                False,
                f"Rede EVM '{chain}' não suportada para auditoria de segurança (Chain IDs conhecidos: {list(self.CHAIN_ID_MAP.keys())})",
                {},
            )

        # Consulta GoPlus Security API
        url = f"{self.goplus_base_url}/token_security/{chain_id}?contract_addresses={token_address}"
        data: dict[str, Any] = {}
        try:
            session = await self._get_session()
            if session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        payload = await resp.json()
                        result = payload.get("result", {})
                        # O resultado do GoPlus chaveia pelo endereço em minúsculas
                        token_info = result.get(token_address.lower()) or result.get(token_address)
                        if isinstance(token_info, dict):
                            data = token_info
        except Exception as exc:
            logger.warning("Falha ao consultar GoPlus Security para token EVM %s (%s): %s", token_address, chain, exc)

        if not data:
            # Se não obtiver resposta da API de segurança, adota abordagem conservadora
            logger.warning("🚨 [EVM SECURITY] Laudo indisponível para %s na rede %s. Rejeitando por precaução.", token_address, chain)
            return False, f"Laudo de segurança EVM indisponível no provedor para a rede {chain}", {}

        # 1. HARD GATE: Honeypot (impossibilidade de vender)
        is_honeypot = str(data.get("is_honeypot", "0")) == "1"
        if is_honeypot:
            reason = "Honeypot detectado no contrato: venda bloqueada para compradores"
            logger.warning("🚨 [EVM HONEYPOT] Token %s (%s): %s", token_address, chain, reason)
            return False, reason, data

        cannot_sell_all = str(data.get("cannot_sell_all", "0")) == "1"
        if cannot_sell_all:
            reason = "Venda restrita: contrato impede a liquidação total dos tokens"
            logger.warning("🚨 [EVM HONEYPOT] Token %s (%s): %s", token_address, chain, reason)
            return False, reason, data

        # 2. HARD GATE: Código-fonte verificado no explorador
        is_open_source = str(data.get("is_open_source", "1")) == "1"
        if not is_open_source:
            reason = "Código do contrato não verificado no explorador oficial (risco crítico de bytecode malicioso)"
            logger.warning("🚨 [EVM NÃO-VERIFICADO] Token %s (%s): %s", token_address, chain, reason)
            return False, reason, data

        # 3. HARD GATE: Taxas de compra e venda (teto de 3%)
        try:
            buy_tax = Decimal(str(data.get("buy_tax") or "0.0")) * Decimal("100.0")
        except Exception:
            buy_tax = Decimal("0.0")

        try:
            sell_tax = Decimal(str(data.get("sell_tax") or "0.0")) * Decimal("100.0")
        except Exception:
            sell_tax = Decimal("0.0")

        if buy_tax > self.max_tax_pct:
            reason = f"Taxa de compra excessiva ({buy_tax:.1f}% > {self.max_tax_pct:.1f}%)"
            return False, reason, data

        if sell_tax > self.max_tax_pct:
            reason = f"Taxa de venda excessiva ({sell_tax:.1f}% > {self.max_tax_pct:.1f}%)"
            return False, reason, data

        # 4. HARD GATE: Blacklist
        is_blacklisted = str(data.get("is_blacklisted", "0")) == "1"
        if is_blacklisted:
            reason = "Contrato possui função ativa de Blacklist de endereços"
            return False, reason, data

        # 5. HARD GATE: Proxy não verificado / manipulável
        is_proxy = str(data.get("is_proxy", "0")) == "1"
        is_hidden_owner = str(data.get("hidden_owner", "0")) == "1"
        if is_proxy and is_hidden_owner:
            reason = "Contrato proxy atualizável com proprietário oculto (risco de alteração súbita de lógica)"
            return False, reason, data

        # 6. HARD GATE: Emissão ilimitada de tokens (is_mintable)
        is_mintable = str(data.get("is_mintable", "0")) == "1"
        if is_mintable:
            reason = "Emissão ilimitada habilitada (is_mintable=1): risco crítico de diluição/rug pull pelo criador"
            logger.warning("🚨 [EVM MINTABLE] Token %s (%s): %s", token_address, chain, reason)
            return False, reason, data

        # 7. HARD GATE: Renúncia reversível (can_take_back_ownership)
        can_reclaim = str(data.get("can_take_back_ownership", "0")) == "1"
        if can_reclaim:
            reason = "Propriedade reversível: criador pode reaver o controle total do contrato a qualquer momento"
            logger.warning("🚨 [EVM OWNERSHIP RECLAIM] Token %s (%s): %s", token_address, chain, reason)
            return False, reason, data

        # 8. HARD GATE: Manipulação arbitrária de saldos (owner_change_balance)
        owner_manipulate = str(data.get("owner_change_balance", "0")) == "1"
        if owner_manipulate:
            reason = "Manipulação de saldo: criador tem permissão para alterar saldos de carteiras de terceiros"
            logger.warning("🚨 [EVM BALANCE MANIPULATION] Token %s (%s): %s", token_address, chain, reason)
            return False, reason, data

        # 9. HARD GATE: Token listado em DEX reconhecida
        is_in_dex = str(data.get("is_in_dex", "1")) == "1"
        if not is_in_dex:
            reason = "Token não identificado em exchanges descentralizadas verificadas (is_in_dex=0)"
            return False, reason, data

        # 10. HARD GATE: Verificação de queima / bloqueio de liquidez (LP)
        lp_holders = data.get("lp_holders")
        if isinstance(lp_holders, list) and lp_holders:
            burn_addrs = {
                "0x0000000000000000000000000000000000000000",
                "0x000000000000000000000000000000000000dead",
                "0x0000000000000000000000000000000000000001",
            }
            locked_or_burned_pct = Decimal("0.0")
            for h in lp_holders:
                if isinstance(h, dict):
                    h_addr = str(h.get("address", "")).lower().strip()
                    h_pct = Decimal(str(h.get("percent") or 0.0)) * Decimal("100.0")
                    is_locked = str(h.get("is_locked", "0")) == "1"
                    if h_addr in burn_addrs or is_locked:
                        locked_or_burned_pct += h_pct

            data["lp_locked_or_burned_pct"] = float(locked_or_burned_pct)
            min_lp_protect = Decimal("70.0")
            if locked_or_burned_pct < min_lp_protect:
                reason = (
                    f"Liquidez (LP) desprotegida: apenas {locked_or_burned_pct:.1f}% bloqueada/queimada "
                    f"(mínimo exigido: {min_lp_protect:.1f}%) - Risco de remoção de liquidez (LP pull)"
                )
                logger.warning("🚨 [EVM LP RISK] Token %s (%s): %s", token_address, chain, reason)
                return False, reason, data

        logger.info(
            "✅ [EVM SECURITY APROVADO] Token %s (%s) aprovado nos Hard Gates EVM (Taxas: Compra %.1f%% / Venda %.1f%%).",
            token_address,
            chain,
            float(buy_tax),
            float(sell_tax),
        )
        return True, None, data

    async def close(self) -> None:
        """Encerra a sessão HTTP assíncrona."""
        if self._session and not self._session.closed:
            await self._session.close()

