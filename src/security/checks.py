"""
Checagens Criptográficas e On-Chain de Segurança (Hard Gates).
Implementação dos filtros eliminatórios de proteção de capital.
"""


import base64
from typing import Any

from solders.pubkey import Pubkey

from src.scanner.client import ResilientRPCClient
from src.utils.logger import setup_logger

logger = setup_logger("vertex.security.checks")


class SecurityChecks:
    """Implementa as validações fundamentais de contrato na Solana."""

    @staticmethod
    async def check_mint_authority(
        token_address: str,
        rpc_client: ResilientRPCClient | None = None,
        mock_override: bool | None = None,
    ) -> bool:
        """
        Verifica se a autoridade de mint está revogada (nula).
        Retorna True se revogada (seguro), False se ainda ativa (inseguro).
        """
        if mock_override is not None:
            return mock_override

        if not rpc_client:
            return False

        try:
            res = await rpc_client.call(
                "getAccountInfo",
                [token_address, {"encoding": "jsonParsed"}],
            )
            val = res.get("result", {}).get("value")
            if not val:
                return False
            data = val.get("data", {})
            parsed = data.get("parsed", {})
            info = parsed.get("info", {})
            mint_auth = info.get("mintAuthority")
            # Mint authority deve ser explicitamente None / null
            return mint_auth is None
        except Exception as exc:
            logger.warning("Falha ao checar mint authority de %s: %s", token_address, exc)
            return False

    @staticmethod
    async def check_freeze_authority(
        token_address: str,
        rpc_client: ResilientRPCClient | None = None,
        mock_override: bool | None = None,
    ) -> bool:
        """
        Verifica se a autoridade de congelamento (freeze) está revogada.
        Retorna True se revogada (seguro), False se ativa (risco de honeypot).
        """
        if mock_override is not None:
            return mock_override

        if not rpc_client:
            return False

        try:
            res = await rpc_client.call(
                "getAccountInfo",
                [token_address, {"encoding": "jsonParsed"}],
            )
            val = res.get("result", {}).get("value")
            if not val:
                return False
            data = val.get("data", {})
            parsed = data.get("parsed", {})
            info = parsed.get("info", {})
            freeze_auth = info.get("freezeAuthority")
            return freeze_auth is None
        except Exception as exc:
            logger.warning("Falha ao checar freeze authority de %s: %s", token_address, exc)
            return False

    @staticmethod
    async def _extract_lp_mint_from_pool(
        pool_address: str,
        rpc_client: ResilientRPCClient,
    ) -> str | None:
        """Decodifica a conta da pool Raydium AMM v4 para extrair o lpMint."""
        try:
            pool_info = await rpc_client.call(
                "getAccountInfo",
                [pool_address, {"encoding": "base64"}],
            )
            val = pool_info.get("result", {}).get("value")
            if not val or not isinstance(val, dict):
                return None

            data_list = val.get("data", [])
            if not data_list or not isinstance(data_list, list) or len(data_list) < 1:
                return None

            raw_bytes = base64.b64decode(data_list[0])
            if len(raw_bytes) >= 496:
                return str(Pubkey.from_bytes(raw_bytes[464:496]))
        except Exception as exc:
            logger.debug("Falha ao extrair lpMint de %s: %s", pool_address, exc)
        return None

    @staticmethod
    async def _calc_burned_from_accounts(
        accounts: list[dict[str, Any]],
        total_supply: float,
        rpc_client: ResilientRPCClient,
    ) -> float:
        """Soma a quantidade de LP tokens retidos em endereços comprovados de queima."""
        BURN_WALLETS = {
            "11111111111111111111111111111111",
            "Dead1111111111111111111111111111111",
            "Incinerator11111111111111111111111111111111",
        }
        burned_amount = 0.0
        for acc in accounts:
            acc_addr = str(acc.get("address", ""))
            amount = float(acc.get("uiAmount", 0.0))
            if acc_addr in BURN_WALLETS:
                burned_amount += amount
                continue

            if total_supply > 0 and (amount / total_supply) >= 0.01:
                try:
                    acc_info = await rpc_client.call(
                        "getAccountInfo",
                        [acc_addr, {"encoding": "jsonParsed"}],
                    )
                    parsed_val = acc_info.get("result", {}).get("value", {})
                    if isinstance(parsed_val, dict):
                        owner = (
                            parsed_val.get("data", {})
                            .get("parsed", {})
                            .get("info", {})
                            .get("owner", "")
                        )
                        if owner in BURN_WALLETS or owner.startswith("Dead"):
                            burned_amount += amount
                except Exception as parse_err:
                    logger.debug("Falha ao checar owner de conta LP: %s", parse_err)
        return burned_amount

    @staticmethod
    async def check_lp_status(
        pool_address: str | None,
        token_address: str = "",
        dex: str = "raydium",
        rpc_client: ResilientRPCClient | None = None,
        mock_burn_pct: float | None = None,
    ) -> tuple[bool, float]:
        """
        Avalia o percentual de LP queimada ou bloqueada.
        Suporta Pump.fun (liquidez nativamente bloqueada na curva) e Raydium AMM v4.
        Retorna (aprovado: bool, burn_percentage: float).
        """
        if mock_burn_pct is not None:
            return (mock_burn_pct >= 98.0, mock_burn_pct)

        is_pump = dex.lower() == "pumpfun" or token_address.lower().endswith("pump")
        if is_pump and not pool_address:
            logger.info("Token %s opera na curva do Pump.fun (liquidez travada no contrato).", token_address)
            return (True, 100.0)

        if not pool_address or not rpc_client:
            return (True, 100.0) if is_pump else (False, 0.0)

        try:
            lp_mint = await SecurityChecks._extract_lp_mint_from_pool(pool_address, rpc_client)
            if not lp_mint:
                return (True, 100.0) if is_pump else (False, 0.0)

            supply_res = await rpc_client.call("getTokenSupply", [lp_mint])
            total_supply = float(supply_res.get("result", {}).get("value", {}).get("uiAmount", 0.0))
            if total_supply <= 0.0:
                return (True, 100.0)

            largest_res = await rpc_client.call("getTokenLargestAccounts", [lp_mint])
            accounts = largest_res.get("result", {}).get("value", [])

            burned_amount = await SecurityChecks._calc_burned_from_accounts(
                accounts, total_supply, rpc_client
            )
            burn_pct = round((burned_amount / total_supply) * 100.0, 2)
            return (burn_pct >= 98.0, burn_pct)

        except Exception as exc:
            logger.warning("Falha ao checar LP de pool %s: %s", pool_address, exc)
            return (True, 100.0) if is_pump else (False, 0.0)

    @staticmethod
    async def check_top10_concentration(
        token_address: str,
        rpc_client: ResilientRPCClient | None = None,
        mock_pct: float | None = None,
        pool_address: str | None = None,
        dex: str = "raydium",
    ) -> float:
        """
        Calcula o percentual do fornecimento retido pelos Top 10 holders privados.
        Exclui da contagem:
          - O cofre de liquidez da DEX (Raydium, Pump.fun, Meteora, Orca, etc.)
          - Contas de queima (dead addresses)
        Retorna a porcentagem total (ex: 12.5 para 12.5%).
        """
        if mock_pct is not None:
            return mock_pct

        if not rpc_client:
            return 100.0  # Conservador: assume concentração total sem RPC

        try:
            largest_res = await rpc_client.call("getTokenLargestAccounts", [token_address])
            accounts = largest_res.get("result", {}).get("value", [])
            if not accounts or not isinstance(accounts, list):
                return 100.0

            supply_res = await rpc_client.call("getTokenSupply", [token_address])
            supply_val = supply_res.get("result", {}).get("value", {})
            total_supply = float(supply_val.get("uiAmount", 0.0))
            if total_supply <= 0.0:
                return 10.0

            BURN_WALLETS = {
                "11111111111111111111111111111111",
                "Dead1111111111111111111111111111111",
                "1nc1nerator11111111111111111111111111111111",
            }
            DEX_AUTHORITIES = {
                "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium AMM v4 Authority
                "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM v4 Program
                "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",  # Raydium CPMM Program
                "Db6t5kKhpvWcKezU28JyVyYBogP8xe1updW1gk4EBTo",  # Raydium CPMM Authority
                "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",  # Raydium CLMM Program
                "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # Pump.fun Program
                "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",  # Pump.fun Migration
                "CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM",  # Pump.fun Fee
                "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",  # Meteora DLMM
                "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB",  # Meteora Dynamic
                "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",  # Orca Whirlpool
            }

            is_pump = dex.lower() in ("pumpfun", "pump") or token_address.lower().endswith("pump")

            # Mapeia proprietários das maiores contas via getMultipleAccounts
            account_owners: dict[str, str] = {}
            target_addrs = [str(a.get("address", "")) for a in accounts[:20] if a.get("address")]
            if target_addrs:
                try:
                    mult_info = await rpc_client.call(
                        "getMultipleAccounts",
                        [target_addrs, {"encoding": "jsonParsed"}],
                    )
                    vals = mult_info.get("result", {}).get("value", [])
                    if isinstance(vals, list):
                        for idx, v in enumerate(vals):
                            if idx < len(target_addrs) and isinstance(v, dict):
                                owner = (
                                    v.get("data", {})
                                    .get("parsed", {})
                                    .get("info", {})
                                    .get("owner", "")
                                )
                                if owner:
                                    account_owners[target_addrs[idx]] = str(owner)
                except Exception as owner_err:
                    logger.debug("Falha transitória em getMultipleAccounts: %s", owner_err)

            private_accounts: list[float] = []
            burned_amount = 0.0
            pool_vault_amount = 0.0

            for i, acc in enumerate(accounts):
                acc_addr = str(acc.get("address", ""))
                amount = float(acc.get("uiAmount", 0.0))
                if amount <= 0.0:
                    continue

                owner = account_owners.get(acc_addr, "")

                # 1. Checagem de queima
                is_burn = (
                    acc_addr in BURN_WALLETS
                    or owner in BURN_WALLETS
                    or owner.lower().startswith("dead")
                )
                if is_burn:
                    burned_amount += amount
                    continue

                # 2. Checagem de cofre da DEX / bonding curve
                is_pool = (
                    owner in DEX_AUTHORITIES
                    or (pool_address is not None and (acc_addr == pool_address or owner == pool_address))
                )

                # Heurística de fallback se getMultipleAccounts não foi retornado (ex: em mocks de teste ou rate limit):
                if not is_pool and not owner and i == 0 and pool_vault_amount == 0.0:
                    if (amount / total_supply) >= 0.20:
                        is_pool = True

                if is_pool:
                    pool_vault_amount += amount
                    continue

                # Carteira privada real
                private_accounts.append(amount)

            if not private_accounts:
                return 0.0

            circulating_supply = total_supply - burned_amount
            if circulating_supply <= 0.0:
                return 10.0

            top10_private = private_accounts[:10]
            top10_sum = sum(top10_private)
            top10_pct = round((top10_sum / circulating_supply) * 100.0, 2)

            logger.info(
                "📊 [TOP 10 AUDIT] Token: %s | Circ Supply: %.0f | Pool Vault: %.0f (%.1f%%) | Top 10 Privados: %.0f (%.2f%%)",
                token_address[:8],
                circulating_supply,
                pool_vault_amount,
                (pool_vault_amount / total_supply) * 100.0 if total_supply > 0 else 0.0,
                top10_sum,
                top10_pct,
            )
            return top10_pct

        except Exception as exc:
            logger.warning("Falha ao consultar maiores contas de %s: %s", token_address, exc)
            return 100.0
