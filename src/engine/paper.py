"""
Motor de Simulação e Homologação (Paper Trading Mode).
Executa ordens virtuais contra cotações e liquidez reais sem exposição de capital.
"""

import asyncio
from datetime import datetime
from decimal import Decimal

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
from src.engine.price_feed import DexScreenerPriceFeed
from src.utils.logger import setup_logger

logger = setup_logger("vertex.engine.paper")


class PaperExecutionEngine(IExecutionEngine):
    """Motor de execução simulada com cálculo dinâmico de slippage e latência virtual."""

    def __init__(
        self,
        positions_repo: PositionsRepository,
        orders_repo: OrdersRepository,
        initial_balance_usd: Decimal = Decimal("5.0"),
        simulated_latency_ms: int = 250,
        trailing_drop_pct: Decimal = Decimal("0.12"),
        price_feed: DexScreenerPriceFeed | None = None,
    ) -> None:
        self.positions_repo: PositionsRepository = positions_repo
        self.orders_repo: OrdersRepository = orders_repo
        self.balance_usd: Decimal = initial_balance_usd
        self.simulated_latency_ms: int = simulated_latency_ms
        self.trailing_drop_pct: Decimal = trailing_drop_pct
        self.max_slippage_pct: Decimal = Decimal("0.015")
        self.price_feed: DexScreenerPriceFeed | None = price_feed

    @property
    def mode(self) -> ExecutionMode:
        return ExecutionMode.PAPER

    async def _resolve_market_price(self, token: TokenMetadata) -> Decimal:
        """Resolve a cotação real do token consultando o PriceFeed em tempo real ou metadados."""
        # 1. Consulta cotação em tempo real via PriceFeed se disponível
        if self.price_feed is not None:
            try:
                live_prices = await self.price_feed.fetch_prices([token.address])
                if token.address in live_prices and live_prices[token.address] > Decimal("0"):
                    logger.info(
                        "💵 [COTAÇÃO REAL OBTIDA] Token %s | Preço de mercado: $%.8f",
                        token.symbol or token.address[:8],
                        live_prices[token.address],
                    )
                    return live_prices[token.address]
            except Exception as pf_exc:
                logger.debug("Falha ao consultar cotação em tempo real para %s: %s", token.address, pf_exc)

        # 2. Se o PriceFeed não retornou, busca nos metadados do token (raw_event, pair_data, hint)
        if isinstance(token.raw_event, dict):
            raw_event = token.raw_event
            candidates = [
                raw_event.get("priceUsd"),
                raw_event.get("price_usd"),
                raw_event.get("pair_data", {}).get("priceUsd") if isinstance(raw_event.get("pair_data"), dict) else None,
                raw_event.get("hint", {}).get("base_token_price_usd") if isinstance(raw_event.get("hint"), dict) else None,
                raw_event.get("hint", {}).get("price_usd") if isinstance(raw_event.get("hint"), dict) else None,
            ]
            for cand in candidates:
                if cand is not None:
                    try:
                        cand_dec = Decimal(str(cand))
                        if cand_dec > Decimal("0"):
                            return cand_dec
                    except Exception:
                        pass

        # 3. Fallback defensivo caso nenhuma cotação seja encontrada
        return Decimal("0.001")

    async def execute_buy(
        self,
        token: TokenMetadata,
        amount_usd: Decimal,
        strategy_type: str = "SCALP",
    ) -> PositionState | None:
        """Simula compra a mercado com cálculo de slippage proporcional à liquidez."""
        if self.balance_usd < Decimal("0.05"):
            logger.warning(
                "Saldo virtual insuficiente para compra: Saldo=$%.2f (Mínimo: $0.05)",
                self.balance_usd,
            )
            return None

        # Limita o valor investido ao saldo restante em caixa
        if amount_usd > self.balance_usd:
            amount_usd = self.balance_usd

        # Simulação de latência de confirmação de rede
        await asyncio.sleep(self.simulated_latency_ms / 1000.0)

        # Preço base obtido em tempo real da cotação real de mercado
        base_price = await self._resolve_market_price(token)

        # Modelo de slippage: slippage = (ordem / liquidez_inicial) * 0.5
        liquidity = token.initial_liquidity_usd if token.initial_liquidity_usd > Decimal("0") else Decimal("5000.0")
        slippage_ratio = (amount_usd / liquidity) * Decimal("0.5")
        if slippage_ratio > Decimal("0.05"):
            slippage_ratio = Decimal("0.05")
        max_allowed_slippage = getattr(self, "max_slippage_pct", Decimal("0.015"))
        if slippage_ratio > max_allowed_slippage:
            slippage_ratio = max_allowed_slippage

        execution_price = base_price * (Decimal("1.0") + slippage_ratio)
        tokens_received = amount_usd / execution_price

        # Debita saldo virtual
        self.balance_usd -= amount_usd

        # Inicializa estado da posição com o tipo de estratégia (SCALP ou SWING)
        position = PositionState(
            token_address=token.address,
            mode=ExecutionMode.PAPER,
            strategy_type=strategy_type,
            entry_price=execution_price,
            initial_token_amount=tokens_received,
            allocated_capital_usd=amount_usd,
            trailing_drop_pct=self.trailing_drop_pct,
            status=PositionStatus.OPEN,
        )

        pos_id = await self.positions_repo.create_position(position)
        position.id = pos_id

        # Registra ordem imutável
        order = OrderExecution(
            position_id=pos_id,
            order_type=OrderType.BUY,
            mode=ExecutionMode.PAPER,
            price=execution_price,
            amount=tokens_received,
            total_usd=amount_usd,
            tx_hash=f"paper_buy_{token.address[:8]}_{datetime.now().timestamp()}",
            fee_cost_usd=Decimal("0.005"),  # Taxa estimada de rede
            slippage_realized=float(slippage_ratio * Decimal("100")),
            notes="Execução simulada Paper Trading",
        )
        await self.orders_repo.record_order(order)

        label = f"{token.symbol} ({token.name})" if token.symbol else token.address[:8]
        logger.info(
            "🟢 [COMPRA PAPER EXECUTADA] Token: %s (%s) | Preço: $%.8f | Qtd: %.2f | Capital Alocado: $%.2f | Posição #%d Aberta",
            label,
            token.address,
            execution_price,
            tokens_received,
            amount_usd,
            pos_id,
            extra={
                "event": "PAPER_BUY_FILLED",
                "token_address": token.address,
                "price": str(execution_price),
            },
        )
        logger.info(
            "💵 [CAIXA ATUALIZADO] Saldo Livre: $%.2f | Alocado na Posição #%d: $%.2f",
            self.balance_usd,
            pos_id,
            amount_usd,
            extra={
                "event": "CASH_UPDATED_BUY",
                "balance_usd": str(self.balance_usd),
                "amount_usd": str(amount_usd),
            },
        )
        return position

    async def execute_sell(
        self,
        position: PositionState,
        amount_tokens: Decimal,
        reason: str,
        execution_price: Decimal,
    ) -> OrderExecution | None:
        """Simula venda a mercado e atualiza saldo virtual e banco de dados."""
        if position.id is None:
            raise ValueError("Posição sem ID registrado.")

        await asyncio.sleep(self.simulated_latency_ms / 1000.0)

        gross_usd = amount_tokens * execution_price
        fee_cost_usd = Decimal("0.005")
        net_usd = max(Decimal("0.0"), gross_usd - fee_cost_usd)

        # Credita saldo virtual líquido retornado ao caixa
        self.balance_usd += net_usd

        if reason == "BREAK_EVEN":
            order_type = OrderType.TAKE_PROFIT_PARTIAL
        elif reason == "EMERGENCY_STOP":
            order_type = OrderType.EMERGENCY_EXIT
        else:
            order_type = OrderType.TRAILING_STOP_EXIT

        order = OrderExecution(
            position_id=position.id,
            order_type=order_type,
            mode=ExecutionMode.PAPER,
            price=execution_price,
            amount=amount_tokens,
            total_usd=gross_usd,
            tx_hash=f"paper_sell_{position.token_address[:8]}_{datetime.now().timestamp()}",
            fee_cost_usd=fee_cost_usd,
            slippage_realized=0.5,
            notes=f"Venda simulada Paper Trading motivo: {reason}",
        )
        await self.orders_repo.record_order(order)

        tag = "💰 [VENDA PARCIAL BREAK-EVEN]" if reason == "BREAK_EVEN" else (
            "🔴 [VENDA TOTAL TRAILING STOP]" if reason == "TRAILING_STOP" else "🛑 [VENDA STOP LOSS]"
        )
        logger.info(
            "%s! Posição #%d (%s) | Qtd: %.2f | Preço: $%.8f | Bruto: $%.2f | Líquido: $%.2f | Saldo: $%.2f",
            tag,
            position.id or 0,
            position.token_address[:8],
            amount_tokens,
            execution_price,
            gross_usd,
            net_usd,
            self.balance_usd,
            extra={
                "event": "PAPER_SELL_FILLED",
                "token_address": position.token_address,
                "reason": reason,
            },
        )
        logger.info(
            "💵 [CAIXA ATUALIZADO] Saldo Livre: $%.2f | Retorno Líquido: $%.2f (Bruto: $%.2f, Taxa: $%.3f)",
            self.balance_usd,
            net_usd,
            gross_usd,
            fee_cost_usd,
            extra={
                "event": "CASH_UPDATED_SELL",
                "balance_usd": str(self.balance_usd),
                "net_usd": str(net_usd),
                "reason": reason,
            },
        )
        return order
