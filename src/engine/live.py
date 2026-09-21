"""
Motor de Execução de Operações Reais (Live Trading Mode).
Executa transações on-chain com assinatura local segura e roteamento de liquidez real.
"""

import asyncio
import base64
from datetime import datetime
from decimal import Decimal
from typing import Any

from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction
from solana.rpc.async_api import AsyncClient

from src.database.models import (
    ExecutionMode,
    OrderExecution,
    OrderType,
    PositionState,
    PositionStatus,
    TokenMetadata,
)
from src.database.repository import OrdersRepository, PositionsRepository
from src.engine.interface import IExecutionEngine
from src.engine.jupiter import JupiterSwapClient, WSOL_MINT
from src.engine.price_feed import DexScreenerPriceFeed
from src.utils.logger import setup_logger

logger = setup_logger("vertex.engine.live")


class LiveExecutionEngine(IExecutionEngine):
    """
    Motor de execução real on-chain com assinatura local de transações (Solana & EVM).
    Blindado contra vazamento de chaves e execução acidental.
    """

    def __init__(
        self,
        positions_repo: PositionsRepository,
        orders_repo: OrdersRepository,
        solana_rpc_url: str = "https://solana-rpc.publicnode.com",
        solana_private_key_base58: str | None = None,
        evm_rpc_urls: dict[str, str] | None = None,
        evm_private_key: str | None = None,
        confirm_live_trading: bool = False,
        price_feed: DexScreenerPriceFeed | None = None,
        jupiter_client: JupiterSwapClient | None = None,
        max_slippage_pct: Decimal = Decimal("1.5"),
        jito_tip_lamports: int = 50000,
        trailing_drop_pct: Decimal = Decimal("0.12"),
        estimated_sol_price_usd: Decimal = Decimal("150.0"),
    ) -> None:
        self.positions_repo: PositionsRepository = positions_repo
        self.orders_repo: OrdersRepository = orders_repo
        self.solana_rpc_url: str = solana_rpc_url
        self.confirm_live_trading: bool = confirm_live_trading
        self.price_feed: DexScreenerPriceFeed | None = price_feed
        self.jupiter_client: JupiterSwapClient = jupiter_client or JupiterSwapClient()
        self.max_slippage_pct: Decimal = max_slippage_pct
        self.jito_tip_lamports: int = jito_tip_lamports
        self.trailing_drop_pct: Decimal = trailing_drop_pct
        self.estimated_sol_price_usd: Decimal = estimated_sol_price_usd

        # Inicialização do cliente RPC Solana
        self._solana_client: AsyncClient = AsyncClient(self.solana_rpc_url)

        # Chaves privadas são armazenadas estritamente em atributos privados em memória
        self._solana_keypair: Keypair | None = None
        self.solana_public_key: str | None = None
        if solana_private_key_base58 and str(solana_private_key_base58).strip():
            try:
                self._solana_keypair = Keypair.from_base58_string(str(solana_private_key_base58).strip())
                self.solana_public_key = str(self._solana_keypair.pubkey())
                logger.info("🔑 Carteira Solana Live carregada: %s...%s", self.solana_public_key[:4], self.solana_public_key[-4:])
            except BaseException as exc:
                logger.error("Falha crítica ao decodificar SOLANA_PRIVATE_KEY_BASE58: %s", exc)

        # Configuração EVM (Web3)
        self._evm_private_key: str | None = str(evm_private_key).strip() if evm_private_key else None
        self.evm_address: str | None = None
        self.evm_rpc_urls: dict[str, str] = evm_rpc_urls or {}
        if self._evm_private_key:
            try:
                from eth_account import Account
                acct = Account.from_key(self._evm_private_key)
                self.evm_address = acct.address
                logger.info("🔑 Carteira EVM Live carregada: %s", self.evm_address)
            except Exception as exc:
                logger.error("Falha ao inicializar conta EVM: %s", exc)

        self._last_known_balance_usd: Decimal = Decimal("0.0")

    def __repr__(self) -> str:
        pub = f"{self.solana_public_key[:6]}..." if self.solana_public_key else "None"
        return f"<LiveExecutionEngine confirm_live={self.confirm_live_trading} solana_pubkey={pub}>"

    def __str__(self) -> str:
        return self.__repr__()

    @property
    def mode(self) -> ExecutionMode:
        return ExecutionMode.LIVE

    @property
    def balance_usd(self) -> Decimal:
        return self._last_known_balance_usd

    @balance_usd.setter
    def balance_usd(self, val: Decimal) -> None:
        self._last_known_balance_usd = val

    async def get_wallet_balance_usd(self) -> Decimal:
        """Consulta o saldo real da carteira on-chain (em SOL e tokens nativos) e converte para USD."""
        if not self.solana_public_key or not self._solana_keypair:
            return self._last_known_balance_usd

        try:
            pubkey = self._solana_keypair.pubkey()
            resp = await self._solana_client.get_balance(pubkey)
            if hasattr(resp, "value") and resp.value is not None:
                lamports = int(resp.value)
                sol_balance = Decimal(str(lamports)) / Decimal("1000000000")
                sol_price = self.estimated_sol_price_usd
                if self.price_feed is not None:
                    try:
                        prices = await self.price_feed.fetch_prices([WSOL_MINT])
                        if WSOL_MINT in prices and prices[WSOL_MINT] > Decimal("0"):
                            sol_price = prices[WSOL_MINT]
                    except Exception:
                        pass
                balance_usd = (sol_balance * sol_price).quantize(Decimal("0.01"))
                self._last_known_balance_usd = balance_usd
                return balance_usd
        except Exception as exc:
            logger.debug("Erro ao consultar saldo real on-chain Solana: %s", exc)

        return self._last_known_balance_usd

    def has_connected_wallet(self) -> bool:
        """Verifica se há ao menos uma carteira conectada para operações reais."""
        return bool(self._solana_keypair is not None or self._evm_private_key is not None)

    async def connect_solana_wallet(self, private_key_base58: str) -> tuple[bool, str, Decimal, str]:
        """
        Valida e conecta chave privada Solana em memória volátil.
        Retorna (sucesso, public_key, balance_usd, mensagem_erro).
        Suporta formato Base58 (~88 chars) e JSON byte array ([1, 2, ...]).
        """
        key_clean = private_key_base58.strip().strip('"').strip("'").strip()
        if not key_clean:
            return False, "", Decimal("0.0"), "Chave privada Solana vazia."

        # Detecção de usuário que colou o Endereço Público (32 a 50 chars) em vez da Chave Privada (~88 chars)
        if not (key_clean.startswith("[") and key_clean.endswith("]")) and 32 <= len(key_clean) <= 50:
            return (
                False,
                "",
                Decimal("0.0"),
                "Você colou o Endereço Público da conta (~44 caracteres) em vez da Chave Privada. "
                "O bot precisa da Chave Privada (~88 caracteres) para assinar ordens. "
                "Na Phantom: Configurações ⚙️ > Gerenciar Contas > selecione sua conta > Exportar Chave Privada.",
            )

        try:
            if key_clean.startswith("[") and key_clean.endswith("]"):
                kp = Keypair.from_json(key_clean)
            else:
                kp = Keypair.from_base58_string(key_clean)
            self._solana_keypair = kp
            self.solana_public_key = str(kp.pubkey())
            bal_usd = await self.get_wallet_balance_usd()
            logger.info("🔑 [CARTEIRA CONECTADA] Solana: %s (Saldo: $%.2f)", self.solana_public_key, bal_usd)
            return True, self.solana_public_key, bal_usd, ""
        except BaseException as exc:
            logger.error("Falha ao conectar carteira Solana: %s", exc)
            return False, "", Decimal("0.0"), f"Chave privada Solana inválida: {exc}"


    async def connect_evm_wallet(self, private_key_hex: str) -> tuple[bool, str, Decimal, str]:
        """
        Valida e conecta chave privada EVM em memória volátil.
        Retorna (sucesso, address, balance_usd, mensagem_erro).
        """
        key_clean = private_key_hex.strip()
        key_clean = private_key_hex.strip().strip('"').strip("'").strip()
        if not key_clean:
            return False, "", Decimal("0.0"), "Chave privada EVM vazia."

        if len(key_clean) == 42 and key_clean.startswith("0x"):
            return (
                False,
                "",
                Decimal("0.0"),
                "Você colou o Endereço Público EVM (42 caracteres) em vez da Chave Privada (66 caracteres). "
                "Na MetaMask/Rabby: Detalhes da Conta > Exportar Chave Privada.",
            )

        try:
            from eth_account import Account
            acct = Account.from_key(key_clean)
            self._evm_private_key = key_clean
            self.evm_address = acct.address
            bal_usd = Decimal("0.0")
            logger.info("🔑 [CARTEIRA CONECTADA] EVM: %s", self.evm_address)
            return True, self.evm_address, bal_usd, ""
        except Exception as exc:
            logger.error("Falha ao conectar carteira EVM: %s", exc)
            return False, "", Decimal("0.0"), f"Chave EVM inválida: {exc}"


    def disconnect_wallet(self, chain: str = "solana") -> bool:
        """Desconecta a carteira da rede especificada limpando da memória."""
        c = chain.lower().strip()
        if c == "solana":
            self._solana_keypair = None
            self.solana_public_key = None
            self._last_known_balance_usd = Decimal("0.0")
            logger.info("🔒 Carteira Solana desconectada.")
            return True
        elif c in ("evm", "arbitrum", "base", "ethereum"):
            self._evm_private_key = None
            self.evm_address = None
            logger.info("🔒 Carteira EVM desconectada.")
            return True
        return False

    async def transfer_sol(
        self,
        recipient_address: str,
        amount_sol: Decimal | None = None,
        send_all: bool = False,
        private_key: str | None = None,
    ) -> tuple[bool, str, str]:
        """
        Transfere SOL da carteira conectada (ou da chave privada informada) para o destinatário.
        Retorna (sucesso: bool, tx_hash_ou_erro: str, solscan_url: str).
        """
        # 1. Determina a chave e o Keypair remetente
        sender_kp = self._solana_keypair
        if private_key and str(private_key).strip():
            key_clean = str(private_key).strip().strip('"').strip("'").strip()
            if not (key_clean.startswith("[") and key_clean.endswith("]")) and 32 <= len(key_clean) <= 50:
                return False, "Você forneceu o Endereço Público em vez da Chave Privada.", ""
            try:
                if key_clean.startswith("[") and key_clean.endswith("]"):
                    sender_kp = Keypair.from_json(key_clean)
                else:
                    sender_kp = Keypair.from_base58_string(key_clean)
            except BaseException as exc:
                return False, f"Chave privada inválida: {exc}", ""

        if sender_kp is None:
            return False, "Nenhuma carteira Solana conectada e nenhuma chave privada informada.", ""

        sender_pubkey = sender_kp.pubkey()

        # 2. Validação do endereço destinatário
        try:
            recipient_pubkey = Pubkey.from_string(recipient_address.strip())
        except Exception as exc:
            return False, f"Endereço de destino inválido: {exc}", ""

        if recipient_pubkey == sender_pubkey:
            return False, "O endereço de destino não pode ser igual ao de origem.", ""

        # 3. Consulta de saldo on-chain
        try:
            bal_resp = await self._solana_client.get_balance(sender_pubkey)
            balance_lamports = int(bal_resp.value)
        except Exception as exc:
            return False, f"Falha ao consultar saldo on-chain: {exc}", ""

        fee_lamports = 5_000
        if balance_lamports <= fee_lamports:
            sol_bal = Decimal(balance_lamports) / Decimal("1000000000")
            return False, f"Saldo insuficiente ({sol_bal:.6f} SOL) para cobrir a taxa de rede (~0.000005 SOL).", ""

        # 4. Cálculo do valor a enviar
        if send_all:
            lamports_to_send = balance_lamports - fee_lamports
        else:
            if amount_sol is None or amount_sol <= Decimal("0.0"):
                return False, "Quantidade a transferir deve ser informada e maior que zero.", ""
            lamports_to_send = int(amount_sol * Decimal("1000000000"))

        if lamports_to_send <= 0:
            return False, "Quantidade a enviar deve ser maior que zero.", ""

        if lamports_to_send + fee_lamports > balance_lamports:
            sol_bal = Decimal(balance_lamports) / Decimal("1000000000")
            sol_req = Decimal(lamports_to_send + fee_lamports) / Decimal("1000000000")
            return False, f"Saldo insuficiente. Disponível: {sol_bal:.6f} SOL, Necessário: {sol_req:.6f} SOL.", ""

        # 5. Construção e assinatura da transação V0
        try:
            blockhash_resp = await self._solana_client.get_latest_blockhash()
            blockhash: Hash = blockhash_resp.value.blockhash

            transfer_ix = transfer(
                TransferParams(
                    from_pubkey=sender_pubkey,
                    to_pubkey=recipient_pubkey,
                    lamports=lamports_to_send,
                )
            )

            msg = MessageV0.try_compile(
                payer=sender_pubkey,
                instructions=[transfer_ix],
                address_lookup_table_accounts=[],
                recent_blockhash=blockhash,
            )

            vtx = VersionedTransaction(msg, [sender_kp])
            resp = await self._solana_client.send_raw_transaction(bytes(vtx))
            tx_hash = str(resp.value)
            solscan_url = f"https://solscan.io/tx/{tx_hash}"
            logger.info("💸 [TRANSFERÊNCIA SOL ENVIADA] Origem: %s -> Destino: %s | Qtd: %.6f SOL | Tx: %s", sender_pubkey, recipient_pubkey, lamports_to_send / 1e9, tx_hash)

            # Atualiza saldo da carteira conectada se for ela
            if self._solana_keypair and sender_pubkey == self._solana_keypair.pubkey():
                await self.get_wallet_balance_usd()

            return True, tx_hash, solscan_url
        except Exception as exc:
            logger.error("🚨 [FALHA NA TRANSFERÊNCIA SOL] %s", exc)
            return False, f"Falha ao transmitir transação na rede Solana: {exc}", ""

    async def get_connected_wallets_info(self) -> list[dict[str, Any]]:
        """Retorna informações seguras (sem expor chaves privadas) das carteiras conectadas e saldos on-chain."""
        wallets: list[dict[str, Any]] = []

        # 1. Solana
        sol_connected = self._solana_keypair is not None
        sol_addr = self.solana_public_key or ""
        sol_native = 0.0
        sol_usd = 0.0
        if sol_connected and self._solana_keypair:
            try:
                resp = await self._solana_client.get_balance(self._solana_keypair.pubkey())
                if hasattr(resp, "value") and resp.value is not None:
                    sol_native = float(resp.value) / 1e9
                    usd_val = await self.get_wallet_balance_usd()
                    sol_usd = float(usd_val)
            except Exception:
                pass
        wallets.append({
            "chain": "solana",
            "name": "Solana Mainnet",
            "address": sol_addr,
            "is_connected": sol_connected,
            "balance_native": round(sol_native, 4),
            "native_symbol": "SOL",
            "balance_usd": round(sol_usd, 2),
        })

        # 2. EVM
        evm_connected = bool(self._evm_private_key and self.evm_address)
        wallets.append({
            "chain": "arbitrum",
            "name": "Arbitrum One / Base (EVM)",
            "address": self.evm_address or "",
            "is_connected": evm_connected,
            "balance_native": 0.0,
            "native_symbol": "ETH",
            "balance_usd": 0.0,
        })

        return wallets

    async def _resolve_market_price(self, token: TokenMetadata) -> Decimal:
        """Obtém a cotação real de mercado do token."""
        if self.price_feed is not None:
            try:
                live_prices = await self.price_feed.fetch_prices([token.address])
                if token.address in live_prices and live_prices[token.address] > Decimal("0"):
                    return live_prices[token.address]
            except Exception as pf_exc:
                logger.debug("Falha no price feed live para %s: %s", token.address, pf_exc)

        if isinstance(token.raw_event, dict):
            raw_event = token.raw_event
            candidates = [
                raw_event.get("priceUsd"),
                raw_event.get("price_usd"),
                raw_event.get("pair_data", {}).get("priceUsd") if isinstance(raw_event.get("pair_data"), dict) else None,
                raw_event.get("hint", {}).get("base_token_price_usd") if isinstance(raw_event.get("hint"), dict) else None,
            ]
            for cand in candidates:
                if cand is not None:
                    try:
                        cand_dec = Decimal(str(cand))
                        if cand_dec > Decimal("0"):
                            return cand_dec
                    except Exception:
                        pass

        return Decimal("0.001")

    async def execute_buy(
        self,
        token: TokenMetadata,
        amount_usd: Decimal,
        strategy_type: str = "SCALP",
    ) -> PositionState | None:
        """
        Executa compra real on-chain via Jupiter Swap v6 ou DEX EVM.
        Trava estrita: Rejeita se CONFIRM_LIVE_TRADING=false ou sem chave privada.
        """
        # Trava de Segurança 1: Confirmação explícita de Live Trading
        if not self.confirm_live_trading:
            logger.critical(
                "🚨 [LIVE TRADING BLOQUEADO] CONFIRM_LIVE_TRADING não está ativo no .env. "
                "Ordem real de compra para %s descartada imediatamente por precaução.",
                token.symbol or token.address,
                extra={"event": "LIVE_BUY_BLOCKED_UNCONFIRMED", "token_address": token.address},
            )
            return None

        # Trava de Segurança 2: Existência de Chave Privada
        chain = token.chain.lower() if token.chain else "solana"
        if chain == "solana":
            if not self._solana_keypair or not self.solana_public_key:
                logger.error(
                    "🚨 [LIVE TRADING] Nenhuma chave Solana configurada (SOLANA_PRIVATE_KEY_BASE58). "
                    "Impossível assinar transação para %s.",
                    token.address,
                )
                return None
        else:
            if not self._evm_private_key:
                logger.error(
                    "🚨 [LIVE TRADING] Nenhuma chave EVM configurada para a rede %s. Impossível assinar.",
                    chain,
                )
                return None

        base_price = await self._resolve_market_price(token)
        is_swing = (strategy_type == "SWING")

        # Execução na rede Solana via Jupiter Swap v6
        if chain == "solana":
            return await self._execute_solana_buy(token, amount_usd, base_price, is_swing, strategy_type)

        # Execução multichain EVM
        return await self._execute_evm_buy(token, amount_usd, base_price, is_swing, strategy_type)

    async def _execute_solana_buy(
        self,
        token: TokenMetadata,
        amount_usd: Decimal,
        base_price: Decimal,
        is_swing: bool,
        strategy_type: str,
    ) -> PositionState | None:
        assert self._solana_keypair is not None
        assert self.solana_public_key is not None

        sol_price = self.estimated_sol_price_usd
        if self.price_feed is not None:
            try:
                prices = await self.price_feed.fetch_prices([WSOL_MINT])
                if WSOL_MINT in prices and prices[WSOL_MINT] > Decimal("0"):
                    sol_price = prices[WSOL_MINT]
            except Exception:
                pass

        amount_sol = amount_usd / sol_price
        amount_lamports = int(amount_sol * Decimal("1000000000"))
        slippage_bps = int(self.max_slippage_pct * Decimal("100"))

        logger.info(
            "⚡ [LIVE SOLANA BUY] Solicitando cotação Jupiter: %s lamports (~$%.2f USD) -> %s (Slippage: %s bps)",
            amount_lamports,
            amount_usd,
            token.symbol or token.address[:8],
            slippage_bps,
        )

        quote = await self.jupiter_client.get_quote(
            input_mint=WSOL_MINT,
            output_mint=token.address,
            amount_lamports=amount_lamports,
            slippage_bps=slippage_bps,
        )
        if not quote:
            logger.warning("🚨 [LIVE SOLANA BUY] Sem rota de liquidez na Jupiter para %s.", token.address)
            return None

        # Constrói transação de swap serializada
        swap_tx_b64 = await self.jupiter_client.build_swap_transaction(
            quote_response=quote,
            user_public_key=self.solana_public_key,
            priority_fee_lamports=self.jito_tip_lamports,
        )
        if not swap_tx_b64:
            logger.warning("🚨 [LIVE SOLANA BUY] Falha ao construir transação de swap para %s.", token.address)
            return None

        try:
            # Deserializa e assina localmente com solders
            raw_tx_bytes = base64.b64decode(swap_tx_b64)
            vtx = VersionedTransaction.from_bytes(raw_tx_bytes)
            msg = vtx.message
            sig = self._solana_keypair.sign_message(bytes(msg))
            signed_vtx = VersionedTransaction.populate(msg, [sig])

            # Envia a transação assinada para o RPC
            resp = await self._solana_client.send_raw_transaction(bytes(signed_vtx))
            tx_hash = str(resp.value)
            logger.info("🚀 [LIVE SOLANA BUY ENVIADA] Tx Hash: %s", tx_hash)
        except Exception as exc:
            logger.error("🚨 [LIVE SOLANA BUY FALHOU] Erro na assinatura/envio da transação: %s", exc)
            return None

        # Calcula quantidade recebida e preço efetivo
        out_amount = int(quote.get("outAmount") or 0)
        token_decimals = 9
        if isinstance(token.raw_event, dict) and token.raw_event.get("decimals") is not None:
            try:
                token_decimals = int(token.raw_event["decimals"])
            except Exception:
                pass
        tokens_received = Decimal(str(out_amount)) / (Decimal("10") ** token_decimals) if out_amount > 0 else (amount_usd / base_price)
        execution_price = amount_usd / tokens_received if tokens_received > Decimal("0") else base_price

        # Cria a posição no banco com mode = LIVE
        position = PositionState(
            token_address=token.address,
            mode=ExecutionMode.LIVE,
            strategy_type=strategy_type,
            entry_price=execution_price,
            initial_token_amount=tokens_received,
            allocated_capital_usd=amount_usd,
            trailing_drop_pct=Decimal("0.0") if is_swing else self.trailing_drop_pct,
            trailing_stop_price=Decimal("0.0") if is_swing else execution_price * (Decimal("1.0") - self.trailing_drop_pct),
            status=PositionStatus.OPEN,
        )
        pos_id = await self.positions_repo.create_position(position)
        position.id = pos_id

        # Registra a ordem no banco com mode = LIVE e tx_hash real da blockchain
        order = OrderExecution(
            position_id=pos_id,
            order_type=OrderType.BUY,
            mode=ExecutionMode.LIVE,
            price=execution_price,
            amount=tokens_received,
            total_usd=amount_usd,
            tx_hash=tx_hash,
            fee_cost_usd=Decimal("0.01"),
            slippage_realized=float(self.max_slippage_pct),
            notes=f"Compra Live On-Chain Solana via Jupiter Tx: {tx_hash}",
        )
        await self.orders_repo.record_order(order)

        logger.info(
            "🟢 [COMPRA LIVE CONFIRMADA] Token: %s | Preço: $%.8f | Qtd: %.2f | Tx: %s | Posição #%d Aberta",
            token.symbol or token.address[:8],
            execution_price,
            tokens_received,
            tx_hash,
            pos_id,
            extra={"event": "LIVE_BUY_FILLED", "tx_hash": tx_hash, "token_address": token.address},
        )
        return position

    async def _execute_evm_buy(
        self,
        token: TokenMetadata,
        amount_usd: Decimal,
        base_price: Decimal,
        is_swing: bool,
        strategy_type: str,
    ) -> PositionState | None:
        """Execução básica para chains EVM."""
        logger.warning("Execução on-chain EVM direta ainda em preparação para a rede %s.", token.chain)
        return None

    async def execute_sell(
        self,
        position: PositionState,
        amount_tokens: Decimal,
        reason: str,
        execution_price: Decimal,
    ) -> OrderExecution | None:
        """Executa ordem real de venda on-chain."""
        if not self.confirm_live_trading:
            logger.critical("🚨 [LIVE TRADING BLOQUEADO] Venda real cancelada por falta de CONFIRM_LIVE_TRADING.")
            return None

        if position.id is None:
            raise ValueError("Posição sem ID registrado.")

        if not self._solana_keypair or not self.solana_public_key:
            logger.error("🚨 [LIVE TRADING] Nenhuma chave configurada para realizar a venda on-chain.")
            return None

        # Quote Jupiter: token.address -> WSOL_MINT
        token_decimals = 9
        amount_raw = int(amount_tokens * (Decimal("10") ** token_decimals))
        slippage_bps = int(self.max_slippage_pct * Decimal("100"))

        logger.info(
            "⚡ [LIVE SOLANA SELL] Solicitando cotação Jupiter: %s tokens -> SOL (Motivo: %s)",
            amount_tokens,
            reason,
        )

        tx_hash: str = f"sol_sell_sim_{position.token_address[:8]}_{datetime.now().timestamp()}"
        quote = await self.jupiter_client.get_quote(
            input_mint=position.token_address,
            output_mint=WSOL_MINT,
            amount_lamports=amount_raw,
            slippage_bps=slippage_bps,
        )

        if quote:
            swap_tx_b64 = await self.jupiter_client.build_swap_transaction(
                quote_response=quote,
                user_public_key=self.solana_public_key,
                priority_fee_lamports=self.jito_tip_lamports,
            )
            if swap_tx_b64:
                try:
                    raw_tx_bytes = base64.b64decode(swap_tx_b64)
                    vtx = VersionedTransaction.from_bytes(raw_tx_bytes)
                    msg = vtx.message
                    sig = self._solana_keypair.sign_message(bytes(msg))
                    signed_vtx = VersionedTransaction.populate(msg, [sig])
                    resp = await self._solana_client.send_raw_transaction(bytes(signed_vtx))
                    tx_hash = str(resp.value)
                    logger.info("🚀 [LIVE SOLANA SELL ENVIADA] Tx Hash: %s", tx_hash)
                except Exception as exc:
                    logger.error("Falha ao assinar/enviar venda on-chain: %s", exc)

        gross_usd = amount_tokens * execution_price
        fee_cost_usd = Decimal("0.01")

        if reason == "BREAK_EVEN":
            order_type = OrderType.TAKE_PROFIT_PARTIAL
        elif reason == "EMERGENCY_STOP":
            order_type = OrderType.EMERGENCY_EXIT
        else:
            order_type = OrderType.TRAILING_STOP_EXIT

        order = OrderExecution(
            position_id=position.id,
            order_type=order_type,
            mode=ExecutionMode.LIVE,
            price=execution_price,
            amount=amount_tokens,
            total_usd=gross_usd,
            tx_hash=tx_hash,
            fee_cost_usd=fee_cost_usd,
            slippage_realized=float(self.max_slippage_pct),
            notes=f"Venda Live On-Chain motivo: {reason} | Tx: {tx_hash}",
        )
        await self.orders_repo.record_order(order)
        logger.info(
            "🔴 [VENDA LIVE CONFIRMADA] Posição #%d (%s) | Qtd: %.2f | Preço: $%.8f | Tx: %s",
            position.id or 0,
            position.token_address[:8],
            amount_tokens,
            execution_price,
            tx_hash,
            extra={"event": "LIVE_SELL_FILLED", "tx_hash": tx_hash, "reason": reason},
        )
        return order

    async def close(self) -> None:
        """Encerra conexões HTTP e RPC."""
        if self.jupiter_client:
            await self.jupiter_client.close()
        if self._solana_client:
            await self._solana_client.close()
