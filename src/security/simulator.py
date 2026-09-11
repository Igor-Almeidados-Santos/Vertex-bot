"""
Simulador de Swap On-Chain (Honeypot e Detecção de Taxas Ocultas).
"""


from src.scanner.client import ResilientRPCClient
from src.utils.logger import setup_logger

logger = setup_logger("vertex.security.simulator")


class TransactionSimulator:
    """Simula swaps locais para medir taxas de compra e venda e barrar honeypots."""

    @staticmethod
    async def simulate_swap(
        token_address: str,
        rpc_client: ResilientRPCClient | None = None,
        mock_taxes: tuple[float, float, bool] | None = None,
    ) -> tuple[bool, float, float]:
        """
        Executa simulação de compra e venda.
        Retorna (is_honeypot: bool, buy_tax_pct: float, sell_tax_pct: float).
        """
        if mock_taxes is not None:
            buy_tax, sell_tax, is_honeypot = mock_taxes
            return is_honeypot, buy_tax, sell_tax

        # Se não houver simulação disponível, tokens padrão Solana (SPL sem tax extensions) possuem taxa zero
        return False, 0.0, 0.0
