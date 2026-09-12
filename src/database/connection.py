"""
Gerenciamento de Conexão e Inicialização do Banco de Dados SQLite.
Garante execução assíncrona com PRAGMAs de alta concorrência (WAL mode).
"""

import asyncio
import os
import sqlite3
from typing import Any, cast

from src.utils.exceptions import DatabaseError
from src.utils.logger import setup_logger

logger = setup_logger("vertex.database")

# Schema DDL oficial conforme context/DATA_SCHEMA.md
INITIAL_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tokens_catalogados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT UNIQUE NOT NULL,
    symbol TEXT,
    name TEXT,
    chain TEXT NOT NULL DEFAULT 'solana',
    dex TEXT NOT NULL,
    pool_address TEXT,
    initial_liquidity_usd REAL NOT NULL,
    detection_timestamp TIMESTAMP NOT NULL,
    security_status TEXT NOT NULL,
    security_score REAL,
    rejection_reason TEXT,
    audit_details_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tokens_address ON tokens_catalogados(address);
CREATE INDEX IF NOT EXISTS idx_tokens_security_status ON tokens_catalogados(security_status);
CREATE INDEX IF NOT EXISTS idx_tokens_detection ON tokens_catalogados(detection_timestamp DESC);

CREATE TABLE IF NOT EXISTS posicoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    strategy_type TEXT NOT NULL DEFAULT 'SCALP',
    entry_price REAL NOT NULL,
    initial_token_amount REAL NOT NULL,
    remaining_token_amount REAL NOT NULL,
    allocated_capital_usd REAL NOT NULL,
    realized_pnl_usd REAL DEFAULT 0.0,
    highest_price_seen REAL NOT NULL,
    break_even_triggered INTEGER DEFAULT 0,
    trailing_stop_price REAL NOT NULL,
    ratchet_tier INTEGER DEFAULT 0,
    ratchet_floor_price REAL DEFAULT 0.0,
    opened_at TIMESTAMP NOT NULL,
    closed_at TIMESTAMP,
    FOREIGN KEY (token_address) REFERENCES tokens_catalogados(address) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_posicoes_status ON posicoes(status);
CREATE INDEX IF NOT EXISTS idx_posicoes_token ON posicoes(token_address);

CREATE TABLE IF NOT EXISTS ordens_executadas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    order_type TEXT NOT NULL,
    mode TEXT NOT NULL,
    price REAL NOT NULL,
    amount REAL NOT NULL,
    total_usd REAL NOT NULL,
    tx_hash TEXT,
    fee_cost_usd REAL DEFAULT 0.0,
    slippage_realized REAL DEFAULT 0.0,
    notes TEXT,
    executed_at TIMESTAMP NOT NULL,
    FOREIGN KEY (position_id) REFERENCES posicoes(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_ordens_posicao ON ordens_executadas(position_id);
CREATE INDEX IF NOT EXISTS idx_ordens_executed_at ON ordens_executadas(executed_at DESC);
"""


try:
    import aiosqlite
    HAS_AIOSQLITE = True
except ImportError:
    HAS_AIOSQLITE = False


class DatabaseManager:
    """Gerenciador assíncrono de banco de dados SQLite com suporte a aiosqlite e WAL mode."""

    def __init__(self, db_path: str = "data/vertex_bot.db") -> None:
        self.db_path: str = db_path
        self._initialized: bool = False
        self._aiosqlite_conn: Any | None = None
        self._sync_conn: sqlite3.Connection | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Inicializa as tabelas, índices e PRAGMAs obrigatórios sem bloquear o event loop."""
        async with self._lock:
            if self._initialized:
                return

            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

            migrations = [
                "ALTER TABLE posicoes ADD COLUMN strategy_type TEXT DEFAULT 'SCALP'",
                "ALTER TABLE posicoes ADD COLUMN ratchet_tier INTEGER DEFAULT 0",
                "ALTER TABLE posicoes ADD COLUMN ratchet_floor_price REAL DEFAULT 0.0",
            ]

            try:
                if HAS_AIOSQLITE:
                    self._aiosqlite_conn = await aiosqlite.connect(self.db_path, timeout=5.0)
                    self._aiosqlite_conn.row_factory = aiosqlite.Row
                    await self._aiosqlite_conn.executescript(INITIAL_DDL)
                    for mig in migrations:
                        try:
                            await self._aiosqlite_conn.execute(mig)
                        except Exception:
                            pass
                    await self._aiosqlite_conn.commit()
                else:
                    def _init_sync() -> sqlite3.Connection:
                        conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
                        conn.row_factory = sqlite3.Row
                        conn.executescript(INITIAL_DDL)
                        for mig in migrations:
                            try:
                                conn.execute(mig)
                            except Exception:
                                pass
                        conn.commit()
                        return conn

                    self._sync_conn = await asyncio.to_thread(_init_sync)

                self._initialized = True
                logger.info(
                    "Banco de dados SQLite inicializado com sucesso em: %s (Driver: %s)",
                    self.db_path,
                    "aiosqlite" if HAS_AIOSQLITE else "sqlite3-async-thread",
                )
            except Exception as exc:
                raise DatabaseError(f"Falha na inicialização do SQLite em {self.db_path}: {exc}") from exc

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> int:
        """Executa instrução de escrita assincronamente e retorna o ID inserido ou contagem."""
        if not self._initialized:
            await self.initialize()

        async with self._lock:
            try:
                if HAS_AIOSQLITE and self._aiosqlite_conn:
                    cursor = await self._aiosqlite_conn.execute(query, params)
                    await self._aiosqlite_conn.commit()
                    last_id = cursor.lastrowid
                    return int(last_id) if last_id is not None else cursor.rowcount
                elif self._sync_conn:
                    def _exec() -> int:
                        assert self._sync_conn is not None
                        cursor = self._sync_conn.cursor()
                        cursor.execute(query, params)
                        self._sync_conn.commit()
                        last_id = cursor.lastrowid
                        return int(last_id) if last_id is not None else cursor.rowcount

                    return await asyncio.to_thread(_exec)
                else:
                    raise DatabaseError("Banco de dados não conectado.")
            except Exception as exc:
                raise DatabaseError(f"Erro ao executar query '{query[:60]}...': {exc}") from exc

    async def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        """Executa consulta assincronamente retornando lista de registros."""
        if not self._initialized:
            await self.initialize()

        async with self._lock:
            try:
                if HAS_AIOSQLITE and self._aiosqlite_conn:
                    cursor = await self._aiosqlite_conn.execute(query, params)
                    rows = await cursor.fetchall()
                    return cast(list[tuple[Any, ...]], rows)
                elif self._sync_conn:
                    def _fetch() -> list[tuple[Any, ...]]:
                        assert self._sync_conn is not None
                        cursor = self._sync_conn.cursor()
                        cursor.execute(query, params)
                        return cursor.fetchall()

                    return await asyncio.to_thread(_fetch)
                else:
                    raise DatabaseError("Banco de dados não conectado.")
            except Exception as exc:
                raise DatabaseError(f"Erro ao buscar registros: {exc}") from exc

    async def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
        """Executa consulta assincronamente retornando um único registro."""
        if not self._initialized:
            await self.initialize()

        async with self._lock:
            try:
                if HAS_AIOSQLITE and self._aiosqlite_conn:
                    cursor = await self._aiosqlite_conn.execute(query, params)
                    row = await cursor.fetchone()
                    return cast(tuple[Any, ...] | None, row)
                elif self._sync_conn:
                    def _fetch() -> tuple[Any, ...] | None:
                        assert self._sync_conn is not None
                        cursor = self._sync_conn.cursor()
                        cursor.execute(query, params)
                        row = cursor.fetchone()
                        return cast(tuple[Any, ...] | None, row)

                    return await asyncio.to_thread(_fetch)
                else:
                    raise DatabaseError("Banco de dados não conectado.")
            except Exception as exc:
                raise DatabaseError(f"Erro ao buscar registro: {exc}") from exc

    async def close(self) -> None:
        """Fecha com segurança a conexão ativa com o banco."""
        async with self._lock:
            if HAS_AIOSQLITE and self._aiosqlite_conn:
                await self._aiosqlite_conn.close()
                self._aiosqlite_conn = None
            elif self._sync_conn:
                await asyncio.to_thread(self._sync_conn.close)
                self._sync_conn = None
            self._initialized = False
