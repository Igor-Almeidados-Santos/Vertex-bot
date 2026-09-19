"""
Módulo de Telemetria e Coleta de Métricas Locais (Fase de Homologação).
"""

from collections import Counter
from decimal import Decimal
from typing import Any

from src.utils.logger import setup_logger

logger = setup_logger("vertex.telemetry")


class TelemetryCollector:
    """Acumula estatísticas e métricas de desempenho durante as execuções locais."""

    def __init__(self) -> None:
        self.tokens_detected: int = 0
        self.tokens_approved: int = 0
        self.tokens_rejected: int = 0
        self.rejection_reasons: Counter[str] = Counter()
        self.paper_trades_opened: int = 0
        self.paper_trades_closed: int = 0
        self.break_evens_hit: int = 0
        self.trailing_stops_hit: int = 0
        self.emergency_stops_hit: int = 0
        self.total_pnl_usd: Decimal = Decimal("0.0")
        self.winning_trades: int = 0
        self.losing_trades: int = 0

    def record_detection(self) -> None:
        self.tokens_detected += 1

    def record_approval(self) -> None:
        self.tokens_approved += 1

    def record_rejection(self, reason: str) -> None:
        self.tokens_rejected += 1
        self.rejection_reasons[reason] += 1

    def record_trade_opened(self) -> None:
        self.paper_trades_opened += 1

    def record_break_even(self) -> None:
        self.break_evens_hit += 1

    def record_trade_closed(self, pnl: Decimal, reason: str) -> None:
        self.paper_trades_closed += 1
        self.total_pnl_usd += pnl
        if pnl > Decimal("0.0"):
            self.winning_trades += 1
        else:
            self.losing_trades += 1

        if reason == "TRAILING_STOP":
            self.trailing_stops_hit += 1
        elif reason == "EMERGENCY_STOP":
            self.emergency_stops_hit += 1

    def generate_report(self) -> dict[str, Any]:
        """Gera dicionário analítico com as principais métricas quantitativas."""
        total_closed = self.winning_trades + self.losing_trades
        win_rate = (
            (float(self.winning_trades) / float(total_closed)) * 100.0
            if total_closed > 0
            else 0.0
        )

        approval_rate = (
            (float(self.tokens_approved) / float(self.tokens_detected)) * 100.0
            if self.tokens_detected > 0
            else 0.0
        )

        return {
            "tokens_detected": self.tokens_detected,
            "tokens_approved": self.tokens_approved,
            "tokens_rejected": self.tokens_rejected,
            "approval_rate_pct": round(approval_rate, 2),
            "top_rejection_reasons": dict(self.rejection_reasons.most_common(5)),
            "trades_opened": self.paper_trades_opened,
            "trades_closed": self.paper_trades_closed,
            "break_evens_hit": self.break_evens_hit,
            "win_rate_pct": round(win_rate, 2),
            "total_realized_pnl_usd": float(self.total_pnl_usd),
        }

    def print_summary(self) -> None:
        """Exibe resumo visual no log."""
        rep = self.generate_report()
        logger.info(
            "=== RELATÓRIO DE TELEMETRIA LOCAL ===\n"
            "Tokens Detectados: %d | Aprovados (Segurança): %d (%.1f%%) | Rejeitados (Hard Gates): %d\n"
            "Trades Abertos: %d | Fechados: %d | Win-Rate: %.1f%%\n"
            "Break-Evens Conquistados: %d\n"
            "PnL Total Realizado: $%.2f USD\n"
            "Principais Motivos de Reprovação: %s",
            rep["tokens_detected"],
            rep["tokens_approved"],
            rep["approval_rate_pct"],
            rep["tokens_rejected"],
            rep["trades_opened"],
            rep["trades_closed"],
            rep["win_rate_pct"],
            rep["break_evens_hit"],
            rep["total_realized_pnl_usd"],
            rep["top_rejection_reasons"],
        )
