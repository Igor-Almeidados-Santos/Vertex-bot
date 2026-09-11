# DATA_SCHEMA.md — Modelagem de Dados e Estado

> **Sistema**: Vertex-bot  
> **Camada**: Persistência (SQLite) e Objetos de Domínio em Memória (Pydantic v2)  
> **Objetivo**: Definir o schema relacional imutável e os contratos de dados em memória para garantir consistência operacional, prevenindo divergências de nomes de campos e tipos.

---

## 1. Configurações Globais do Banco de Dados (SQLite)

O banco de dados relacional local opera sob driver assíncrono (`aiosqlite`). Na inicialização de qualquer conexão, os seguintes `PRAGMAS` são obrigatórios para garantir concorrência de leitura e escrita sem travamentos:

```sql
PRAGMA journal_mode = WAL;          -- Write-Ahead Logging para concorrência de leitura/escrita
PRAGMA busy_timeout = 5000;         -- 5 segundos de espera antes de falhar por lock
PRAGMA synchronous = NORMAL;        -- Otimização de performance preservando integridade WAL
PRAGMA foreign_keys = ON;           -- Integridade referencial ativa
```

---

## 2. Schema Relacional (DDL)

```sql
-- ---------------------------------------------------------------------------
-- Tabela: tokens_catalogados
-- Armazena todos os tokens identificados pelo scanner e o laudo de segurança.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tokens_catalogados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT UNIQUE NOT NULL,
    symbol TEXT,
    name TEXT,
    chain TEXT NOT NULL DEFAULT 'solana',
    dex TEXT NOT NULL,                          -- Ex: raydium, pumpfun, meteora
    pool_address TEXT,
    initial_liquidity_usd REAL NOT NULL,
    detection_timestamp TIMESTAMP NOT NULL,
    security_status TEXT NOT NULL,              -- 'PENDING', 'APPROVED', 'REJECTED'
    security_score REAL,                        -- 0.0 a 100.0
    rejection_reason TEXT,                      -- Motivo detalhado se REJECTED
    audit_details_json TEXT,                    -- Dump JSON das checagens realizadas
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tokens_address ON tokens_catalogados(address);
CREATE INDEX IF NOT EXISTS idx_tokens_security_status ON tokens_catalogados(security_status);
CREATE INDEX IF NOT EXISTS idx_tokens_detection ON tokens_catalogados(detection_timestamp DESC);

-- ---------------------------------------------------------------------------
-- Tabela: posicoes
-- Rastreia o ciclo de vida e a contabilidade de posições abertas ou finalizadas.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS posicoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT NOT NULL,
    status TEXT NOT NULL,                       -- 'OPEN', 'PARTIALLY_CLOSED', 'CLOSED', 'STOPPED'
    mode TEXT NOT NULL,                         -- 'PAPER', 'LIVE'
    entry_price REAL NOT NULL,                  -- Preço unitário na entrada (USD)
    initial_token_amount REAL NOT NULL,         -- Quantidade total comprada
    remaining_token_amount REAL NOT NULL,       -- Quantidade remanescente pós-parciais
    allocated_capital_usd REAL NOT NULL,        -- Capital investido na entrada
    realized_pnl_usd REAL DEFAULT 0.0,          -- Lucro/prejuízo já liquidado
    highest_price_seen REAL NOT NULL,           -- Máxima cotação para o Trailing Stop
    break_even_triggered INTEGER DEFAULT 0,     -- 0 = False, 1 = True (venda de 50% feita)
    trailing_stop_price REAL NOT NULL,          -- Preço de gatilho de saída do stop
    opened_at TIMESTAMP NOT NULL,
    closed_at TIMESTAMP,
    FOREIGN KEY (token_address) REFERENCES tokens_catalogados(address) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_posicoes_status ON posicoes(status);
CREATE INDEX IF NOT EXISTS idx_posicoes_token ON posicoes(token_address);

-- ---------------------------------------------------------------------------
-- Tabela: ordens_executadas
-- Histórico imutável de transações de compra e venda (auditoria contábil).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ordens_executadas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    order_type TEXT NOT NULL,                   -- 'BUY', 'TAKE_PROFIT_PARTIAL', 'TRAILING_STOP_EXIT', 'EMERGENCY_EXIT'
    mode TEXT NOT NULL,                         -- 'PAPER', 'LIVE'
    price REAL NOT NULL,                        -- Preço unitário da execução (USD)
    amount REAL NOT NULL,                       -- Quantidade de tokens negociados
    total_usd REAL NOT NULL,                    -- Valor financeiro total
    tx_hash TEXT,                               -- Hash da transação na blockchain (nulo em paper)
    fee_cost_usd REAL DEFAULT 0.0,              -- Taxas de rede estimadas ou reais
    slippage_realized REAL DEFAULT 0.0,         -- Slippage observado em %
    notes TEXT,                                 -- Contexto operacional ou erro
    executed_at TIMESTAMP NOT NULL,
    FOREIGN KEY (position_id) REFERENCES posicoes(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_ordens_posicao ON ordens_executadas(position_id);
CREATE INDEX IF NOT EXISTS idx_ordens_executed_at ON ordens_executadas(executed_at DESC);
```

---

## 3. Modelos de Domínio em Memória (Pydantic v2)

Para garantir segurança de tipos e contratos determinísticos entre as tarefas assíncronas, o código deve utilizar os seguintes modelos Pydantic:

```python
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

class SecurityStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class PositionStatus(str, Enum):
    OPEN = "OPEN"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"
    STOPPED = "STOPPED"

class OrderType(str, Enum):
    BUY = "BUY"
    TAKE_PROFIT_PARTIAL = "TAKE_PROFIT_PARTIAL"
    TRAILING_STOP_EXIT = "TRAILING_STOP_EXIT"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"

class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"

# --- Modelo de Detecção do Token ---
class TokenMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    address: str
    symbol: Optional[str] = None
    name: Optional[str] = None
    chain: str = "solana"
    dex: str
    pool_address: Optional[str] = None
    initial_liquidity_usd: Decimal
    detection_timestamp: datetime

# --- Laudo de Auditoria de Segurança ---
class SecurityAuditResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    token_address: str
    status: SecurityStatus
    security_score: float = Field(ge=0.0, le=100.0)
    is_mint_revoked: bool
    is_freeze_revoked: bool
    is_lp_burned_or_locked: bool
    lp_burn_percentage: float
    top10_holder_percentage: float
    is_honeypot: bool
    buy_tax_percentage: float
    sell_tax_percentage: float
    rejection_reason: Optional[str] = None
    details: dict = Field(default_factory=dict)

# --- Estado da Posição Aberta em Memória ---
class PositionState(BaseModel):
    id: Optional[int] = None
    token_address: str
    status: PositionStatus = PositionStatus.OPEN
    mode: ExecutionMode
    entry_price: Decimal
    initial_token_amount: Decimal
    remaining_token_amount: Decimal
    allocated_capital_usd: Decimal
    realized_pnl_usd: Decimal = Decimal("0.0")
    highest_price_seen: Decimal
    break_even_triggered: bool = False
    trailing_stop_price: Decimal
    opened_at: datetime
    closed_at: Optional[datetime] = None

    def update_high_and_stop(self, current_price: Decimal, trailing_drop_pct: Decimal) -> None:
        """Atualiza a máxima histórica da posição e ajusta o trailing stop dinâmico."""
        if current_price > self.highest_price_seen:
            self.highest_price_seen = current_price
            self.trailing_stop_price = current_price * (Decimal("1.0") - trailing_drop_pct)

# --- Ordem de Execução ---
class OrderExecution(BaseModel):
    model_config = ConfigDict(frozen=True)

    position_id: int
    order_type: OrderType
    mode: ExecutionMode
    price: Decimal
    amount: Decimal
    total_usd: Decimal
    tx_hash: Optional[str] = None
    fee_cost_usd: Decimal = Decimal("0.0")
    slippage_realized: float = 0.0
    executed_at: datetime
    notes: Optional[str] = None
```

---

## 4. Diretrizes de Consistência e Migrações

1. **Campos Obrigatórios**: Nunca omita `chain`, `dex` ou `mode`.
2. **Imutabilidade de Histórico**: Registros na tabela `ordens_executadas` são imutáveis (`INSERT` apenas). Nenhuma operação de `UPDATE` ou `DELETE` é permitida nesta tabela.
3. **Serialização JSON**: Dicionários e dados aninhados armazenados na coluna `audit_details_json` devem ser serializados estritamente via `model.model_dump_json()` do Pydantic para evitar erros de tipos não-serializáveis (ex: `Decimal` ou `datetime`).
