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
    ) -> float:
        """
        Calcula o percentual do fornecimento retido pelos Top 10 holders privados.
        Retorna a porcentagem total (ex: 12.5 para 12.5%).
        """
        if mock_pct is not None:
            return mock_pct

        if not rpc_client:
            return 100.0  # Conservador: assume concentração total

        try:
            res = await rpc_client.call("getTokenLargestAccounts", [token_address])
            accounts = res.get("result", {}).get("value", [])
            if not accounts:
                return 100.0

            total_top10 = sum(float(acc.get("uiAmount", 0.0)) for acc in accounts[:10])
            supply_res = await rpc_client.call("getTokenSupply", [token_address])
            supply_val = supply_res.get("result", {}).get("value", {})
            total_supply = float(supply_val.get("uiAmount", 0.0))
            if total_supply > 0.0:
                return round((total_top10 / total_supply) * 100.0, 2)
            return 10.0  # Baseline
        except Exception as exc:
            logger.warning("Falha ao consultar maiores contas de %s: %s", token_address, exc)
            return 100.0
