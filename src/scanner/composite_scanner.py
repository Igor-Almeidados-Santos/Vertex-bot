"""
Orquestrador Composto de Múltiplos Scanners Concorrentes (CompositeScanner).
Permite executar simultaneamente diferentes provedores de detecção (ex: Tokens Maduros + Graduações Raydium)
alimentando concorrentemente a mesma fila assíncrona de triagem de segurança com isolamento de falhas.
"""

import asyncio
from typing import Any

from src.utils.logger import setup_logger

logger = setup_logger("vertex.scanner.composite")


class CompositeScanner:
    """Supervisiona e executa múltiplos scanners em paralelo de forma transparente."""

    def __init__(self, scanners: list[Any]) -> None:
        self.scanners: list[Any] = scanners
        self.is_running: bool = False

    async def start(self) -> None:
        """Inicia todos os sub-scanners concorrentemente."""
        self.is_running = True
        logger.info(
            "Iniciando CompositeScanner com %d motores de varredura ativos em paralelo...",
            len(self.scanners),
        )
        tasks = [s.start() for s in self.scanners if hasattr(s, "start")]
        if tasks:
            await asyncio.gather(*tasks)
        logger.info("CompositeScanner totalmente operacional.")

    async def stop(self) -> None:
        """Finaliza todos os sub-scanners de maneira coordenada."""
        self.is_running = False
        logger.info("Encerrando CompositeScanner e seus sub-scanners...")
        tasks = [s.stop() for s in self.scanners if hasattr(s, "stop")]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("CompositeScanner finalizado.")

    def release_token(self, token_addr: str) -> None:
        """Propaga a liberação do token para todos os sub-scanners."""
        for s in self.scanners:
            if hasattr(s, "release_token"):
                try:
                    s.release_token(token_addr)
                except Exception as exc:
                    logger.debug("Erro ao liberar token no sub-scanner %s: %s", type(s).__name__, exc)

    def incubate_token(
        self,
        token: Any,
        reason: str = "AGUARDANDO_3_VELAS_1H",
        wait_minutes: float = 30.0,
    ) -> None:
        """Propaga a inclusão do token em incubação/quarentena para sub-scanners compatíveis."""
        for s in self.scanners:
            if hasattr(s, "incubate_token"):
                try:
                    s.incubate_token(token, reason=reason, wait_minutes=wait_minutes)
                except Exception as exc:
                    logger.debug("Erro ao incubar token no sub-scanner %s: %s", type(s).__name__, exc)

    def get_incubator_tokens(self) -> list[dict[str, Any]]:
        """Consolida os tokens em incubação de todos os sub-scanners que possuem incubadora."""
        results: list[dict[str, Any]] = []
        for s in self.scanners:
            if hasattr(s, "get_incubator_tokens"):
                try:
                    results.extend(s.get_incubator_tokens())
                except Exception as exc:
                    logger.debug("Erro ao coletar tokens da incubadora do sub-scanner %s: %s", type(s).__name__, exc)
        return results

