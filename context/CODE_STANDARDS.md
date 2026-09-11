# CODE_STANDARDS.md — Padrões de Código e Qualidade

> **Sistema**: Vertex-bot  
> **Nível de Engenharia**: Sênior / Sistemas Críticos de Alta Confiabilidade  
> **Objetivo**: Garantir que todo código desenvolvido seja fortemente tipado, resiliente a falhas de terceiros, auditável por logs estruturados e livre de bloqueios no event loop assíncrono.

---

## 1. Tipagem Estrita (Type Annotations)

Todo o código Python deve ser **100% tipado estaticamente**. O uso de verificadores como `mypy --strict` é obrigatório.

### 1.1. Regras de Tipagem
- **Parâmetros e Retornos**: Toda função, método e gerador deve declarar explicitamente os tipos de todos os argumentos e do retorno (`-> ReturnType` ou `-> None`).
- **Proibição de Tipos Ambíguos**: O uso de `Any` ou `object` como tipo coringa é expressamente proibido, exceto ao lidar com deserialização de payloads brutos de RPC onde `dict[str, Any]` é transitório e imediatamente convertido em um modelo Pydantic.
- **Coleções**: Use generics modernos (`list[str]`, `dict[str, Decimal]`, `set[int]`, `tuple[str, ...]`) em vez dos equivalentes legados de `typing.List`.
- **Nulabilidade**: Use `Optional[T]` ou a sintaxe moderna de união `T | None`. Valores opcionais devem sempre ter valor default explícito (`= None`) quando cabível.

```python
# Correto:
from decimal import Decimal
from typing import Optional

async def calculate_slippage(
    expected_amount: Decimal, 
    actual_amount: Decimal
) -> Decimal:
    if expected_amount <= Decimal("0.0"):
        raise ValueError("O montante esperado deve ser estritamente positivo.")
    return ((expected_amount - actual_amount) / expected_amount) * Decimal("100.0")

# Incorreto:
def calculate_slippage(expected_amount, actual_amount):
    return (expected_amount - actual_amount) / expected_amount
```

---

## 2. Tratamento Rigoroso de Exceções

Em sistemas de trading e varredura on-chain, falhas de rede de RPCs públicos e privados são a regra, não a exceção. O código deve ser defensivo e granular.

### 2.1. Hierarquia de Exceções Customizadas
Todas as exceções do projeto devem herdar de `VertexError`:

```python
class VertexError(Exception):
    """Exceção base para todas as falhas de domínio do Vertex-bot."""
    def __init__(self, message: str, context: Optional[dict[str, object]] = None) -> None:
        super().__init__(message)
        self.context: dict[str, object] = context or {}

class SecurityValidationError(VertexError):
    """Lançada quando um token é reprovado nos critérios de segurança."""
    pass

class RPCConnectionError(VertexError):
    """Lançada em caso de timeout, HTTP 429 ou falha de conectividade com nós RPC."""
    pass

class OrderExecutionError(VertexError):
    """Lançada quando uma ordem falha na fase de simulação ou transmissão."""
    pass

class CircuitBreakerTriggeredError(VertexError):
    """Lançada quando os limites globais de risco do sistema são atingidos."""
    pass
```

### 2.2. Regras de Blocos `try/except`
1. **Proibição Absoluta de Captura Silenciosa**:

```python
# NUNCA FAÇA ISSO:
try:
    await process_order()
except Exception:
    pass
```
2. **Granularidade e Especificidade**: Capture exceções específicas (`asyncio.TimeoutError`, `aiohttp.ClientResponseError`, `aiosqlite.Error`) em vez de capturar genericamente `Exception`.
3. **Padrão Retry com Backoff Exponencial**:
   Chamadas de rede a nós RPC devem implementar tentativas com jitter aleatório para evitar sobrecarga de requisições:

```python
import asyncio
import random
from vertex.core.exceptions import RPCConnectionError

async def rpc_call_with_retry(func, *args, max_retries: int = 3, base_delay: float = 0.5, **kwargs):
    for attempt in range(1, max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except (asyncio.TimeoutError, RPCConnectionError) as exc:
            if attempt == max_retries:
                raise RPCConnectionError(f"Falha definitiva após {max_retries} tentativas: {exc}") from exc
            jitter = random.uniform(0.05, 0.2)
            delay = (base_delay * (2 ** (attempt - 1))) + jitter
            await asyncio.sleep(delay)
```

---

## 3. Logging Estruturado e Telemetria

O sistema deve produzir logs rastreáveis em formato limpo para console e estruturado (JSON) para persistência em arquivo de log.

### 3.1. Níveis de Log e Convenções
- **`DEBUG`**: Dados detalhados de payload RPC, tempos intermediários de micro-operações e passos de parsing.
- **`INFO`**: Eventos operacionais normais (Novo token detectado, Token aprovado em segurança, Posição aberta, Break-even acionado).
- **`WARNING`**: Falhas recuperáveis (RPC retornou 429 com comutação para fallback, slippage acima do normal mas dentro da tolerância, reconexão de WebSocket).
- **`ERROR`**: Erros de execução de ordens, rejeições inesperadas de transação, falha de integridade em banco.
- **`CRITICAL`**: Acionamento de Circuit Breaker de drawdown diário ou impossibilidade total de conexão com todos os nós RPC.

### 3.2. Formato de Log Estruturado
Os logs devem sempre carregar metadados essenciais de rastreabilidade:

```json
{
  "timestamp": "2026-09-11T12:00:00.123456Z",
  "level": "INFO",
  "module": "vertex.risk.manager",
  "event": "BREAK_EVEN_TRIGGERED",
  "token_address": "So11111111111111111111111111111111111111112",
  "position_id": 42,
  "entry_price": 1.25,
  "current_price": 2.50,
  "gain_pct": 100.0,
  "action": "PARTIAL_SELL_50_PERCENT"
}
```

### 3.3. Segurança de Dados Sensíveis
- **Chaves Privadas e Seeds**: Nunca logar chaves privadas, mnemonics, URLs completas contendo API keys em query string ou headers de autorização.
- Use mascaramento explícito: `f"Wallet: {public_key[:4]}...{public_key[-4:]}"`.

---

## 4. Diretrizes de Concorrência e Clean Code Assíncrono

1. **Nunca Bloquear o Event Loop**:
   - É expressamente proibido usar `time.sleep()`, `requests.get()`, `urllib` ou operações síncronas de I/O em funções `async def`.
   - Utilize sempre os equivalentes assíncronos: `asyncio.sleep()`, `aiohttp.ClientSession()`, `aiosqlite`.
   - Se for indispensável executar computação pesada em CPU (ex: hashing intensivo ou decodificação binária complexa), utilize `await asyncio.to_thread(sync_func, *args)`.
2. **Gerenciadores de Contexto Assíncronos**:
   - Todas as conexões de rede, sessões HTTP e conexões com banco devem ser gerenciadas por `async with` para garantir fechamento de sockets mesmo sob falhas:

```python
async with aiohttp.ClientSession() as session:
    async with session.get(url) as response:
        ...
```
3. **Imutabilidade e Funções Puras**:
   - Prefira classes com `frozen=True` no Pydantic ou `@dataclass(frozen=True)` para objetos de trânsito em filas. Dados imutáveis eliminam condições de corrida (*race conditions*) entre corrotinas.

---

## 5. Ferramental de Qualidade e CI

- **Linter & Formatter**: `ruff check .` e `ruff format .`
- **Type Checker**: `mypy --strict src/`
- **Suíte de Testes**: `pytest tests/ -v`
