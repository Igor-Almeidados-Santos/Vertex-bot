"""
Hierarquia de Exceções de Domínio do Vertex-bot.
Todos os erros específicos do sistema herdam de VertexError.
"""

from typing import Any, Optional


class VertexError(Exception):
    """Exceção base para todas as falhas de domínio do Vertex-bot."""

    def __init__(self, message: str, context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message: str = message
        self.context: dict[str, Any] = context or {}

    def __str__(self) -> str:
        if self.context:
            return f"{self.message} | Contexto: {self.context}"
        return self.message


class ConfigurationError(VertexError):
    """Lançada quando há erro de configuração de ambiente ou inicialização."""

    pass


class RPCConnectionError(VertexError):
    """Lançada em caso de timeout, HTTP 429 ou falha de conectividade com nós RPC."""

    pass


class WebSocketStreamError(VertexError):
    """Lançada quando a conexão WebSocket cai ou sofre erro irrecuperável."""

    pass


class ParseError(VertexError):
    """Lançada quando o parser falha ao decodificar logs ou transações on-chain."""

    pass


class SecurityValidationError(VertexError):
    """Lançada quando um token é reprovado nos critérios de segurança (Hard Gates)."""

    pass


class DatabaseError(VertexError):
    """Lançada em falhas de persistência relacional com o SQLite."""

    pass


class OrderExecutionError(VertexError):
    """Lançada quando uma ordem falha na fase de simulação ou transmissão."""

    pass


class CircuitBreakerTriggeredError(VertexError):
    """Lançada quando os limites globais de risco do sistema são atingidos."""

    pass
