"""
Cliente de Execução Multichain EVM (Base, Arbitrum, BSC, Ethereum, Polygon).
Roteamento descentralizado via KyberSwap Aggregator API com assinatura local segura Web3.
"""

import asyncio
from decimal import Decimal
from typing import Any, cast

import aiohttp
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexStr
from pydantic import BaseModel, ConfigDict
from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.types import TxParams, Wei

from src.utils.logger import setup_logger

logger = setup_logger("vertex.engine.evm_executor")

# Constantes de Agregação
KYBER_API_BASE_URL: str = "https://aggregator-api.kyberswap.com"
KYBER_NATIVE_TOKEN: str = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

# Configurações de Redes EVM Suportadas
CHAIN_CONFIGS: dict[str, dict[str, Any]] = {
    "base": {
        "chain_id": 8453,
        "name": "Base",
        "native_symbol": "ETH",
        "wrapped_native": "0x4200000000000000000000000000000000000006",
        "default_rpc": "https://mainnet.base.org",
        "explorer_tx": "https://basescan.org/tx/{tx_hash}",
        "explorer_address": "https://basescan.org/address/{address}",
        "is_eip1559": True,
        "kyber_chain": "base",
    },
    "arbitrum": {
        "chain_id": 42161,
        "name": "Arbitrum One",
        "native_symbol": "ETH",
        "wrapped_native": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "default_rpc": "https://arb1.arbitrum.io/rpc",
        "explorer_tx": "https://arbiscan.io/tx/{tx_hash}",
        "explorer_address": "https://arbiscan.io/address/{address}",
        "is_eip1559": True,
        "kyber_chain": "arbitrum",
    },
    "bsc": {
        "chain_id": 56,
        "name": "BNB Smart Chain",
        "native_symbol": "BNB",
        "wrapped_native": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
        "default_rpc": "https://bsc-dataseed.binance.org",
        "explorer_tx": "https://bscscan.com/tx/{tx_hash}",
        "explorer_address": "https://bscscan.com/address/{address}",
        "is_eip1559": False,
        "kyber_chain": "bsc",
    },
    "polygon": {
        "chain_id": 137,
        "name": "Polygon",
        "native_symbol": "POL",
        "wrapped_native": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
        "default_rpc": "https://polygon-rpc.com",
        "explorer_tx": "https://polygonscan.com/tx/{tx_hash}",
        "explorer_address": "https://polygonscan.com/address/{address}",
        "is_eip1559": True,
        "kyber_chain": "polygon",
    },
    "ethereum": {
        "chain_id": 1,
        "name": "Ethereum",
        "native_symbol": "ETH",
        "wrapped_native": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "default_rpc": "https://eth.llamarpc.com",
        "explorer_tx": "https://etherscan.io/tx/{tx_hash}",
        "explorer_address": "https://etherscan.io/address/{address}",
        "is_eip1559": True,
        "kyber_chain": "ethereum",
    },
    "avalanche": {
        "chain_id": 43114,
        "name": "Avalanche C-Chain",
        "native_symbol": "AVAX",
        "wrapped_native": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",
        "default_rpc": "https://api.avax.network/ext/bc/C/rpc",
        "explorer_tx": "https://snowtrace.io/tx/{tx_hash}",
        "explorer_address": "https://snowtrace.io/address/{address}",
        "is_eip1559": True,
        "kyber_chain": "avalanche",
    },
    "optimism": {
        "chain_id": 10,
        "name": "Optimism",
        "native_symbol": "ETH",
        "wrapped_native": "0x4200000000000000000000000000000000000006",
        "default_rpc": "https://mainnet.optimism.io",
        "explorer_tx": "https://optimistic.etherscan.io/tx/{tx_hash}",
        "explorer_address": "https://optimistic.etherscan.io/address/{address}",
        "is_eip1559": True,
        "kyber_chain": "optimism",
    },
    "blast": {
        "chain_id": 81457,
        "name": "Blast",
        "native_symbol": "ETH",
        "wrapped_native": "0x4300000000000000000000000000000000000004",
        "default_rpc": "https://rpc.blast.io",
        "explorer_tx": "https://blastscan.io/tx/{tx_hash}",
        "explorer_address": "https://blastscan.io/address/{address}",
        "is_eip1559": True,
        "kyber_chain": "blast",
    },
}

# ABI Padrão ERC-20 para checagens de saldo, casas decimais e aprovação
ERC20_ABI: list[dict[str, Any]] = [
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]


class EVMSwapResult(BaseModel):
    """Resultado da execução de um Swap on-chain em redes EVM."""

    model_config = ConfigDict(frozen=True)

    success: bool
    tx_hash: str
    amount_in: Decimal
    amount_out: Decimal
    effective_price: Decimal
    fee_cost_usd: Decimal
    explorer_url: str
    chain: str
    error_message: str | None = None


class EVMExecutionClient:
    """
    Cliente de execução e roteamento de ordens para ecossistemas EVM.
    Gerencia nós RPC assíncronos, cálculo de taxas EIP-1559, allowance ERC-20 e KyberSwap API.
    """

    def __init__(
        self,
        private_key: str | None = None,
        rpc_urls: dict[str, str] | None = None,
        session: aiohttp.ClientSession | None = None,
        min_gas_reserve_usd: Decimal = Decimal("5.0"),
    ) -> None:
        self.rpc_urls: dict[str, str] = rpc_urls or {}
        self.min_gas_reserve_usd: Decimal = min_gas_reserve_usd
        self._custom_session: aiohttp.ClientSession | None = session
        self._owned_session: aiohttp.ClientSession | None = None

        self._w3_instances: dict[str, AsyncWeb3] = {}
        self._account: LocalAccount | None = None
        self._address: str | None = None

        if private_key and str(private_key).strip():
            self.set_private_key(str(private_key).strip())

    @property
    def has_wallet(self) -> bool:
        """Verifica se há conta carregada para assinatura de transações."""
        return self._account is not None and self._address is not None

    @property
    def address(self) -> str | None:
        """Retorna o endereço público formatado (checksum) da carteira EVM."""
        return self._address

    def set_private_key(self, private_key_hex: str) -> tuple[bool, str, str]:
        """
        Carrega chave privada EVM em memória volátil de forma segura.
        Retorna (sucesso: bool, address: str, erro: str).
        """
        key_clean = private_key_hex.strip().strip('"').strip("'").strip()
        if not key_clean:
            return False, "", "Chave privada EVM vazia."

        if len(key_clean) == 42 and key_clean.startswith("0x"):
            return (
                False,
                "",
                "Você colou o Endereço Público EVM (42 caracteres) em vez da Chave Privada (66 caracteres). "
                "Na MetaMask/Rabby: Detalhes da Conta > Exportar Chave Privada.",
            )

        try:
            if not key_clean.startswith("0x") and len(key_clean) == 64:
                key_clean = f"0x{key_clean}"
            acct = Account.from_key(key_clean)
            self._account = acct
            self._address = acct.address
            logger.info("🔑 [EVM] Carteira conectada: %s", self._address)
            return True, self._address, ""
        except Exception as exc:
            logger.error("Falha ao inicializar chave privada EVM: %s", exc)
            return False, "", f"Chave privada EVM inválida: {exc}"

    def clear_private_key(self) -> None:
        """Remove a chave privada da memória."""
        self._account = None
        self._address = None
        logger.info("🔒 [EVM] Carteira desconectada da memória.")

    def get_web3(self, chain: str) -> AsyncWeb3:
        """Obtém ou instancia o cliente AsyncWeb3 com timeout adequado para a rede alvo."""
        c = chain.lower().strip()
        if c not in CHAIN_CONFIGS:
            c = "base"

        if c not in self._w3_instances:
            rpc_url = self.rpc_urls.get(c) or CHAIN_CONFIGS[c]["default_rpc"]
            provider = AsyncHTTPProvider(rpc_url, request_kwargs={"timeout": 15})
            self._w3_instances[c] = AsyncWeb3(provider)

        return self._w3_instances[c]

    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self._custom_session and not self._custom_session.closed:
            return self._custom_session
        if self._owned_session is None or self._owned_session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self._owned_session = aiohttp.ClientSession(timeout=timeout)
        return self._owned_session

    def get_explorer_tx_url(self, chain: str, tx_hash: str) -> str:
        """Retorna o link público do explorador de blocos para a transação."""
        c = chain.lower().strip()
        cfg = CHAIN_CONFIGS.get(c, CHAIN_CONFIGS["base"])
        return str(cfg["explorer_tx"]).format(tx_hash=tx_hash)

    def get_explorer_address_url(self, chain: str, address: str) -> str:
        """Retorna o link público do explorador de blocos para o endereço."""
        c = chain.lower().strip()
        cfg = CHAIN_CONFIGS.get(c, CHAIN_CONFIGS["base"])
        return str(cfg["explorer_address"]).format(address=address)

    async def get_native_balance(self, chain: str, address: str | None = None) -> tuple[int, Decimal]:
        """
        Consulta o saldo on-chain na moeda nativa (ETH / BNB / POL / AVAX).
        Retorna (balance_wei: int, balance_native: Decimal).
        """
        target_addr = address or self._address
        if not target_addr:
            return 0, Decimal("0.0")

        w3 = self.get_web3(chain)
        try:
            checksum_addr = AsyncWeb3.to_checksum_address(target_addr)
            wei = await w3.eth.get_balance(checksum_addr)
            native = Decimal(str(wei)) / Decimal("1000000000000000000")
            return int(wei), native
        except Exception as exc:
            logger.debug("Falha ao consultar saldo nativo na rede %s: %s", chain, exc)
            return 0, Decimal("0.0")

    async def get_token_decimals(self, chain: str, token_address: str) -> int:
        """Consulta as casas decimais de um token ERC-20 on-chain."""
        w3 = self.get_web3(chain)
        try:
            checksum_token = AsyncWeb3.to_checksum_address(token_address)
            contract = w3.eth.contract(address=checksum_token, abi=ERC20_ABI)
            decimals: int = await contract.functions.decimals().call()
            return int(decimals)
        except Exception as exc:
            logger.debug("Não foi possível ler decimais de %s on-chain (%s). Assumindo 18.", token_address, exc)
            return 18

    async def get_token_balance(
        self, chain: str, token_address: str, address: str | None = None
    ) -> tuple[int, Decimal, int]:
        """
        Consulta o saldo de um token ERC-20 específico na carteira.
        Retorna (balance_raw: int, balance_token: Decimal, decimals: int).
        """
        target_addr = address or self._address
        if not target_addr:
            return 0, Decimal("0.0"), 18

        w3 = self.get_web3(chain)
        try:
            checksum_token = AsyncWeb3.to_checksum_address(token_address)
            checksum_addr = AsyncWeb3.to_checksum_address(target_addr)
            contract = w3.eth.contract(address=checksum_token, abi=ERC20_ABI)
            raw_bal: int = await contract.functions.balanceOf(checksum_addr).call()
            decimals = await self.get_token_decimals(chain, token_address)
            bal_dec = Decimal(str(raw_bal)) / (Decimal("10") ** decimals)
            return int(raw_bal), bal_dec, decimals
        except Exception as exc:
            logger.debug("Falha ao consultar saldo ERC-20 de %s: %s", token_address, exc)
            return 0, Decimal("0.0"), 18

    async def get_native_price_usd(self, chain: str) -> Decimal:
        """
        Consulta o preço da moeda nativa (ETH/BNB) em USD em tempo real usando rota KyberSwap.
        Retorna cotação em Decimal.
        """
        c = chain.lower().strip()
        kyber_chain = CHAIN_CONFIGS.get(c, {}).get("kyber_chain", "base")
        # Solicita cotação de 0.01 unidades de moeda nativa (10^16 wei)
        test_amount = 10_000_000_000_000_000
        wrapped_addr = CHAIN_CONFIGS.get(c, {}).get("wrapped_native", "0x4200000000000000000000000000000000000006")

        route = await self.get_kyber_route(
            chain=c,
            token_in=KYBER_NATIVE_TOKEN,
            token_out=wrapped_addr,
            amount_in_raw=test_amount,
        )
        if route and route.get("amountInUsd"):
            try:
                usd_val = Decimal(str(route["amountInUsd"]))
                price = usd_val / Decimal("0.01")
                if price > Decimal("0"):
                    return price
            except Exception:
                pass

        # Fallbacks seguros por ativo caso a rota de teste falhe momentaneamente
        symbol = CHAIN_CONFIGS.get(c, {}).get("native_symbol", "ETH")
        if symbol == "ETH":
            return Decimal("2700.0")
        elif symbol == "BNB":
            return Decimal("580.0")
        elif symbol == "POL":
            return Decimal("0.40")
        elif symbol == "AVAX":
            return Decimal("28.0")
        return Decimal("1.0")

    async def get_kyber_route(
        self, chain: str, token_in: str, token_out: str, amount_in_raw: int
    ) -> dict[str, Any] | None:
        """
        Consulta as melhores rotas de swap no KyberSwap Aggregator API.
        """
        c = chain.lower().strip()
        kyber_chain = CHAIN_CONFIGS.get(c, {}).get("kyber_chain", "base")
        url = f"{KYBER_API_BASE_URL}/{kyber_chain}/api/v1/routes"
        params = {
            "tokenIn": token_in,
            "tokenOut": token_out,
            "amountIn": str(amount_in_raw),
        }
        headers = {"x-client-id": "vertex-bot"}
        session = await self._get_http_session()

        try:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    logger.debug("KyberSwap routes retornou HTTP %s para %s", resp.status, kyber_chain)
                    return None
                data = await resp.json()
                if data.get("code") == 0 and "data" in data:
                    summary: dict[str, Any] = data["data"].get("routeSummary", {})
                    return summary
                logger.debug("KyberSwap routes sem rota válida: %s", data.get("message"))
                return None
        except Exception as exc:
            logger.debug("Exceção ao consultar rotas KyberSwap (%s): %s", kyber_chain, exc)
            return None

    async def build_kyber_swap(
        self,
        chain: str,
        route_summary: dict[str, Any],
        sender: str,
        recipient: str,
        slippage_bps: int = 150,
    ) -> dict[str, Any] | None:
        """
        Constrói o calldata exato da transação de swap via KyberSwap API.
        """
        c = chain.lower().strip()
        kyber_chain = CHAIN_CONFIGS.get(c, {}).get("kyber_chain", "base")
        url = f"{KYBER_API_BASE_URL}/{kyber_chain}/api/v1/route/build"
        payload = {
            "routeSummary": route_summary,
            "sender": sender,
            "recipient": recipient,
            "slippageTolerance": slippage_bps,
        }
        headers = {"x-client-id": "vertex-bot"}
        session = await self._get_http_session()

        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    logger.debug("KyberSwap build retornou HTTP %s", resp.status)
                    return None
                data = await resp.json()
                if data.get("code") == 0 and "data" in data:
                    build_data: dict[str, Any] = data["data"]
                    return build_data
                logger.warning("Falha ao construir transação KyberSwap: %s", data.get("message"))
                return None
        except Exception as exc:
            logger.error("Exceção ao chamar build KyberSwap (%s): %s", kyber_chain, exc)
            return None

    async def check_and_approve_allowance(
        self, chain: str, token_address: str, spender_address: str, required_amount_raw: int
    ) -> tuple[bool, str]:
        """
        Verifica se o router tem permissão suficiente para gastar o token ERC-20.
        Se a permissão for insuficiente, assina e envia a transação de aprovação on-chain.
        Retorna (sucesso: bool, tx_hash_ou_erro: str).
        """
        if not self._account or not self._address:
            return False, "Nenhuma carteira EVM carregada."

        w3 = self.get_web3(chain)
        checksum_token = AsyncWeb3.to_checksum_address(token_address)
        checksum_spender = AsyncWeb3.to_checksum_address(spender_address)
        checksum_owner = AsyncWeb3.to_checksum_address(self._address)

        contract = w3.eth.contract(address=checksum_token, abi=ERC20_ABI)
        try:
            current_allowance: int = await contract.functions.allowance(checksum_owner, checksum_spender).call()
            if current_allowance >= required_amount_raw:
                return True, "allowance_already_sufficient"

            logger.info(
                "🔓 [EVM APPROVE] Solicitando aprovação para token %s no router %s (atual: %s, necessário: %s)",
                checksum_token[:8],
                checksum_spender[:8],
                current_allowance,
                required_amount_raw,
            )

            # Max uint256 para evitar custos recorrentes de aprovação
            max_uint256 = (1 << 256) - 1
            approve_data = contract.encode_abi("approve", [checksum_spender, max_uint256])

            nonce = await w3.eth.get_transaction_count(checksum_owner, "pending")
            chain_id = CHAIN_CONFIGS.get(chain.lower(), {}).get("chain_id", 8453)

            tx: dict[str, Any] = {
                "from": checksum_owner,
                "to": checksum_token,
                "value": 0,
                "data": approve_data,
                "nonce": nonce,
                "chainId": chain_id,
            }

            tx = await self.estimate_gas_and_fees(chain, tx, default_gas=75000)
            success, tx_hash, _ = await self.send_signed_transaction(chain, tx, timeout_seconds=45)
            if success:
                logger.info("✅ [EVM APPROVE CONFIRMADO] Tx: %s", tx_hash)
                return True, tx_hash
            return False, f"Falha ao enviar approve: {tx_hash}"
        except Exception as exc:
            logger.error("Erro no ciclo de aprovação ERC-20: %s", exc)
            return False, str(exc)

    async def estimate_gas_and_fees(
        self, chain: str, tx_dict: dict[str, Any], default_gas: int = 350000
    ) -> dict[str, Any]:
        """
        Calcula taxas de gás dinamicamente respeitando EIP-1559 ou redes legadas (Legacy).
        """
        w3 = self.get_web3(chain)
        c = chain.lower().strip()
        is_eip1559_config = CHAIN_CONFIGS.get(c, {}).get("is_eip1559", True)

        # 1. Estimativa de Limite de Gás (com margem de segurança de +20%)
        try:
            est_gas = await w3.eth.estimate_gas(cast(TxParams, tx_dict))
            tx_dict["gas"] = int(est_gas * 1.2)
        except Exception as est_exc:
            logger.debug("Não foi possível estimar gás exato (%s). Usando padrão %s.", est_exc, default_gas)
            tx_dict["gas"] = default_gas

        # 2. Definição das Taxas (EIP-1559 vs Legacy)
        try:
            latest_block = await w3.eth.get_block("latest")
            base_fee = latest_block.get("baseFeePerGas")

            if is_eip1559_config and base_fee is not None and base_fee > 0:
                try:
                    priority_fee = await w3.eth.max_priority_fee
                except Exception:
                    priority_fee = Wei(1_000_000_000)  # 1 gwei fallback

                # maxFeePerGas = 1.5 * baseFee + maxPriorityFee
                max_fee = int(base_fee * 1.5) + priority_fee
                tx_dict["maxFeePerGas"] = max_fee
                tx_dict["maxPriorityFeePerGas"] = priority_fee
                tx_dict.pop("gasPrice", None)
            else:
                gas_price = await w3.eth.gas_price
                tx_dict["gasPrice"] = Wei(int(gas_price))
                tx_dict.pop("maxFeePerGas", None)
                tx_dict.pop("maxPriorityFeePerGas", None)
        except Exception as fee_exc:
            logger.debug("Falha ao consultar taxas dinâmicas de gás (%s). Usando fallback.", fee_exc)
            tx_dict["gasPrice"] = Wei(10_000_000_000)

        return tx_dict

    async def send_signed_transaction(
        self, chain: str, tx_dict: dict[str, Any], timeout_seconds: int = 60
    ) -> tuple[bool, str, Decimal]:
        """
        Assina a transação localmente e aguarda confirmação de mineração (recibo).
        Retorna (sucesso: bool, tx_hash_ou_erro: str, fee_cost_usd: Decimal).
        """
        if not self._account:
            return False, "Nenhuma conta carregada para assinar.", Decimal("0.0")

        w3 = self.get_web3(chain)
        try:
            signed = self._account.sign_transaction(tx_dict)  # type: ignore[no-untyped-call]
            raw_tx = getattr(signed, "rawTransaction", getattr(signed, "raw_transaction", None))
            if raw_tx is None:
                return False, "Falha interna ao gerar rawTransaction.", Decimal("0.0")

            tx_hash_bytes = await w3.eth.send_raw_transaction(raw_tx)
            tx_hash = tx_hash_bytes.hex()
            logger.info("🚀 [EVM TX ENVIADA] Tx Hash: %s", tx_hash)

            # Aguarda o recibo de mineração da transação
            receipt = await asyncio.wait_for(
                w3.eth.wait_for_transaction_receipt(HexStr(tx_hash)),
                timeout=timeout_seconds,
            )

            status = receipt.get("status")
            if status != 1:
                logger.error("🚨 [EVM TX REVERTIDA] Transação falhou on-chain. Status: %s", status)
                return False, f"Transação revertida on-chain (Tx: {tx_hash})", Decimal("0.0")

            gas_used = int(receipt.get("gasUsed", 0))
            effective_gas_price = int(receipt.get("effectiveGasPrice", tx_dict.get("gasPrice", 0)))
            total_fee_wei = gas_used * effective_gas_price
            fee_native = Decimal(str(total_fee_wei)) / Decimal("1000000000000000000")
            native_price = await self.get_native_price_usd(chain)
            fee_cost_usd = (fee_native * native_price).quantize(Decimal("0.0001"))

            logger.info(
                "✅ [EVM TX CONFIRMADA] Bloco: %s | Gás Gasto: %s | Custo: $%.4f USD",
                receipt.get("blockNumber"),
                gas_used,
                fee_cost_usd,
            )
            return True, tx_hash, fee_cost_usd
        except asyncio.TimeoutError:
            return False, f"Timeout aguardando confirmação da transação ({timeout_seconds}s).", Decimal("0.0")
        except Exception as exc:
            logger.error("Falha ao assinar e transmitir transação EVM: %s", exc)
            return False, str(exc), Decimal("0.0")

    async def buy_token(
        self,
        chain: str,
        token_address: str,
        amount_usd: Decimal,
        slippage_pct: Decimal = Decimal("1.5"),
        native_price_usd: Decimal | None = None,
    ) -> EVMSwapResult | None:
        """
        Executa compra on-chain de token ERC-20 usando a moeda nativa da rede alvo.
        Verifica rigorosamente a Reserva Mínima de Gás antes de transmitir.
        """
        if not self._account or not self._address:
            logger.error("🚨 [EVM BUY] Nenhuma carteira conectada para realizar compra.")
            return None

        c = chain.lower().strip()
        checksum_token = AsyncWeb3.to_checksum_address(token_address)
        checksum_wallet = AsyncWeb3.to_checksum_address(self._address)
        w3 = self.get_web3(c)

        # 1. Resolução de preço nativo e reserva de gás
        n_price = native_price_usd if (native_price_usd and native_price_usd > Decimal("0")) else await self.get_native_price_usd(c)
        _, current_native_bal = await self.get_native_balance(c)
        current_usd_bal = current_native_bal * n_price

        # Verificação da trava de segurança de reserva de gás blindada
        if current_usd_bal - amount_usd < self.min_gas_reserve_usd:
            logger.critical(
                "🚨 [EVM BUY BLOQUEADO] Saldo insuficiente para manter a reserva mínima de gás blindada. "
                "Saldo Total: $%.2f | Compra: $%.2f | Reserva Obrigatória: $%.2f | Rede: %s",
                current_usd_bal,
                amount_usd,
                self.min_gas_reserve_usd,
                c.upper(),
            )
            return None

        # 2. Conversão de USD para wei da moeda nativa
        amount_native = amount_usd / n_price
        amount_in_wei = int(amount_native * Decimal("1000000000000000000"))
        if amount_in_wei <= 0:
            logger.error("🚨 [EVM BUY] Quantidade de entrada em wei inválida: %s", amount_in_wei)
            return None

        slippage_bps = int(slippage_pct * Decimal("100"))

        # 3. Consulta da melhor rota no agregador KyberSwap
        route = await self.get_kyber_route(
            chain=c,
            token_in=KYBER_NATIVE_TOKEN,
            token_out=checksum_token,
            amount_in_raw=amount_in_wei,
        )
        if not route:
            logger.warning("🚨 [EVM BUY] Nenhuma rota de liquidez encontrada para %s na rede %s.", checksum_token, c)
            return None

        # 4. Construção da transação de swap
        swap_payload = await self.build_kyber_swap(
            chain=c,
            route_summary=route,
            sender=checksum_wallet,
            recipient=checksum_wallet,
            slippage_bps=slippage_bps,
        )
        if not swap_payload:
            logger.warning("🚨 [EVM BUY] Falha ao construir calldata de swap no KyberSwap.")
            return None

        router_address = AsyncWeb3.to_checksum_address(swap_payload["routerAddress"])
        calldata_hex = swap_payload["data"]
        nonce = await w3.eth.get_transaction_count(checksum_wallet, "pending")
        chain_id = CHAIN_CONFIGS.get(c, {}).get("chain_id", 8453)

        tx: dict[str, Any] = {
            "from": checksum_wallet,
            "to": router_address,
            "value": amount_in_wei,
            "data": calldata_hex,
            "nonce": nonce,
            "chainId": chain_id,
        }

        kyber_gas = int(swap_payload.get("gas") or 350000)
        tx = await self.estimate_gas_and_fees(c, tx, default_gas=int(kyber_gas * 1.3))

        # 5. Assinatura e Transmissão On-Chain
        success, tx_hash_or_err, fee_cost = await self.send_signed_transaction(c, tx, timeout_seconds=60)
        if not success:
            logger.error("🚨 [EVM BUY FALHOU] Erro na transmissão on-chain: %s", tx_hash_or_err)
            return None

        # 6. Cálculo de tokens recebidos e preço de execução
        token_decimals = await self.get_token_decimals(c, checksum_token)
        amount_out_raw = int(route.get("amountOut") or 0)
        tokens_received = (
            Decimal(str(amount_out_raw)) / (Decimal("10") ** token_decimals)
            if amount_out_raw > 0
            else Decimal("0.0")
        )
        execution_price = (amount_usd / tokens_received) if tokens_received > Decimal("0") else Decimal("0.0")

        explorer_url = self.get_explorer_tx_url(c, tx_hash_or_err)
        return EVMSwapResult(
            success=True,
            tx_hash=tx_hash_or_err,
            amount_in=amount_usd,
            amount_out=tokens_received,
            effective_price=execution_price,
            fee_cost_usd=fee_cost,
            explorer_url=explorer_url,
            chain=c,
        )

    async def sell_token(
        self,
        chain: str,
        token_address: str,
        amount_tokens: Decimal,
        slippage_pct: Decimal = Decimal("1.5"),
        native_price_usd: Decimal | None = None,
    ) -> EVMSwapResult | None:
        """
        Executa venda parcial ou total on-chain de token ERC-20 convertendo para a moeda nativa.
        Gerencia o ciclo de aprovação (allowance + approve) antes de submeter o swap.
        """
        if not self._account or not self._address:
            logger.error("🚨 [EVM SELL] Nenhuma carteira conectada para realizar venda.")
            return None

        c = chain.lower().strip()
        checksum_token = AsyncWeb3.to_checksum_address(token_address)
        checksum_wallet = AsyncWeb3.to_checksum_address(self._address)
        w3 = self.get_web3(c)

        token_decimals = await self.get_token_decimals(c, checksum_token)
        amount_in_raw = int(amount_tokens * (Decimal("10") ** token_decimals))

        # 1. Validação do saldo on-chain real do token na carteira
        on_chain_raw, on_chain_bal, _ = await self.get_token_balance(c, checksum_token)
        if on_chain_raw <= 0:
            logger.error("🚨 [EVM SELL] Saldo on-chain do token %s é zero.", checksum_token)
            return None

        # Ajusta para não tentar vender mais do que a carteira possui (evita falhas por arredondamento)
        if amount_in_raw > on_chain_raw:
            amount_in_raw = on_chain_raw
            amount_tokens = on_chain_bal

        slippage_bps = int(slippage_pct * Decimal("100"))

        # 2. Consulta de rota no KyberSwap (Token -> Moeda Nativa)
        route = await self.get_kyber_route(
            chain=c,
            token_in=checksum_token,
            token_out=KYBER_NATIVE_TOKEN,
            amount_in_raw=amount_in_raw,
        )
        if not route:
            logger.warning("🚨 [EVM SELL] Sem rota de liquidez para venda de %s na rede %s.", checksum_token, c)
            return None

        # 3. Construção do calldata de swap
        swap_payload = await self.build_kyber_swap(
            chain=c,
            route_summary=route,
            sender=checksum_wallet,
            recipient=checksum_wallet,
            slippage_bps=slippage_bps,
        )
        if not swap_payload:
            logger.warning("🚨 [EVM SELL] Falha ao construir transação de venda no KyberSwap.")
            return None

        router_address = AsyncWeb3.to_checksum_address(swap_payload["routerAddress"])

        # 4. Ciclo de Aprovação de Allowance ERC-20
        app_success, app_res = await self.check_and_approve_allowance(
            chain=c,
            token_address=checksum_token,
            spender_address=router_address,
            required_amount_raw=amount_in_raw,
        )
        if not app_success:
            logger.error("🚨 [EVM SELL] Aprovação do token falhou: %s", app_res)
            return None

        # 5. Construção e Envio da Transação de Swap de Venda
        calldata_hex = swap_payload["data"]
        nonce = await w3.eth.get_transaction_count(checksum_wallet, "pending")
        chain_id = CHAIN_CONFIGS.get(c, {}).get("chain_id", 8453)

        tx: dict[str, Any] = {
            "from": checksum_wallet,
            "to": router_address,
            "value": 0,
            "data": calldata_hex,
            "nonce": nonce,
            "chainId": chain_id,
        }

        kyber_gas = int(swap_payload.get("gas") or 350000)
        tx = await self.estimate_gas_and_fees(c, tx, default_gas=int(kyber_gas * 1.3))

        success, tx_hash_or_err, fee_cost = await self.send_signed_transaction(c, tx, timeout_seconds=60)
        if not success:
            logger.error("🚨 [EVM SELL FALHOU] Erro na transmissão da ordem de venda: %s", tx_hash_or_err)
            return None

        # 6. Cálculo dos valores financeiros recebidos
        out_native_wei = int(route.get("amountOut") or 0)
        native_received = Decimal(str(out_native_wei)) / Decimal("1000000000000000000")
        n_price = native_price_usd if (native_price_usd and native_price_usd > Decimal("0")) else await self.get_native_price_usd(c)
        total_usd_received = (native_received * n_price).quantize(Decimal("0.01"))
        effective_price = (total_usd_received / amount_tokens) if amount_tokens > Decimal("0") else Decimal("0.0")

        explorer_url = self.get_explorer_tx_url(c, tx_hash_or_err)
        return EVMSwapResult(
            success=True,
            tx_hash=tx_hash_or_err,
            amount_in=amount_tokens,
            amount_out=total_usd_received,
            effective_price=effective_price,
            fee_cost_usd=fee_cost,
            explorer_url=explorer_url,
            chain=c,
        )

    async def transfer_native(
        self,
        chain: str,
        recipient_address: str,
        amount_native: Decimal | None = None,
        send_all: bool = False,
        private_key: str | None = None,
    ) -> tuple[bool, str, str]:
        """
        Transfere moedas nativas (ETH, BNB, POL, AVAX) para o destinatário informado.
        Retorna (sucesso: bool, tx_hash_ou_erro: str, explorer_url: str).
        """
        c = chain.lower().strip()
        w3 = self.get_web3(c)

        sender_acct = self._account
        if private_key and str(private_key).strip():
            try:
                pk_clean = str(private_key).strip()
                if not pk_clean.startswith("0x") and len(pk_clean) == 64:
                    pk_clean = f"0x{pk_clean}"
                sender_acct = Account.from_key(pk_clean)
            except Exception as exc:
                return False, f"Chave privada informada inválida: {exc}", ""

        if not sender_acct:
            return False, "Nenhuma carteira carregada para envio.", ""

        try:
            checksum_recipient = AsyncWeb3.to_checksum_address(recipient_address.strip())
            checksum_sender = AsyncWeb3.to_checksum_address(sender_acct.address)
        except Exception as exc:
            return False, f"Endereço de destino inválido: {exc}", ""

        if checksum_recipient == checksum_sender:
            return False, "O endereço de destino não pode ser igual ao de origem.", ""

        wei_bal, native_bal = await self.get_native_balance(c, checksum_sender)
        gas_limit = 21000

        # Estima taxas de rede para transferências simples
        latest_block = await w3.eth.get_block("latest")
        base_fee = latest_block.get("baseFeePerGas") or 1_000_000_000
        gas_fee_wei = gas_limit * int(base_fee * 1.5)

        if wei_bal <= gas_fee_wei:
            return False, f"Saldo insuficiente ({native_bal:.6f}) para cobrir o gás da transferência.", ""

        if send_all:
            wei_to_send = wei_bal - gas_fee_wei
        else:
            if amount_native is None or amount_native <= Decimal("0.0"):
                return False, "Quantidade a transferir deve ser informada e maior que zero.", ""
            wei_to_send = int(amount_native * Decimal("1000000000000000000"))

        if wei_to_send <= 0 or (wei_to_send + gas_fee_wei) > wei_bal:
            return False, "Saldo insuficiente para cobrir o valor desejado mais o gás.", ""

        nonce = await w3.eth.get_transaction_count(checksum_sender, "pending")
        chain_id = CHAIN_CONFIGS.get(c, {}).get("chain_id", 8453)

        tx: dict[str, Any] = {
            "from": checksum_sender,
            "to": checksum_recipient,
            "value": wei_to_send,
            "data": b"",
            "gas": gas_limit,
            "nonce": nonce,
            "chainId": chain_id,
        }
        tx = await self.estimate_gas_and_fees(c, tx, default_gas=gas_limit)

        signed = sender_acct.sign_transaction(tx)  # type: ignore[no-untyped-call]
        raw_tx = getattr(signed, "rawTransaction", getattr(signed, "raw_transaction", None))
        if raw_tx is None:
            return False, "Falha interna ao gerar rawTransaction.", ""

        try:
            tx_hash_bytes = await w3.eth.send_raw_transaction(raw_tx)
            tx_hash = tx_hash_bytes.hex()
            explorer_url = self.get_explorer_tx_url(c, tx_hash)
            logger.info("💸 [EVM TRANSFERÊNCIA ENVIADA] De: %s -> Para: %s | Tx: %s", checksum_sender, checksum_recipient, tx_hash)
            return True, tx_hash, explorer_url
        except Exception as exc:
            logger.error("Erro na transferência EVM: %s", exc)
            return False, f"Falha na transmissão: {exc}", ""

    async def close(self) -> None:
        """Fecha sessões HTTP e conexões de provedor Web3."""
        if self._owned_session and not self._owned_session.closed:
            await self._owned_session.close()
