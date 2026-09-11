#!/usr/bin/env python3
"""
Script de Reset do Banco de Dados do Vertex-bot.
Zera completamente o histórico de tokens catalogados, posições financeiras,
ordens executadas e lucros (PnL), recriando as tabelas e PRAGMAs do zero.
"""

import asyncio
import os
import sys

from src.database.connection import DatabaseManager


def _cleanup_files(db_path: str) -> None:
    """Remove fisicamente os arquivos do SQLite de forma segura."""
    for ext in ["", "-wal", "-shm"]:
        target = f"{db_path}{ext}"
        if os.path.exists(target):
            try:
                os.remove(target)
                print(f"✓ Removido: {target}")
            except Exception as exc:
                print(f"✗ Erro ao remover {target}: {exc}")


async def reset_database(db_path: str = "data/vertex_bot.db") -> None:
    print(f"Iniciando limpeza total em: {db_path} ...")
    _cleanup_files(db_path)

    # Reinicializa as tabelas, índices e PRAGMAs limpos
    db = DatabaseManager(db_path)
    await db.initialize()
    await db.close()

    print("==================================================")
    print(" BANCO DE DADOS ZERADO COM SUCESSO!")
    print(" 0 Tokens | 0 Posições | 0 Ordens | $0.00 PnL")
    print(" Pronto para iniciar novas operações do zero.")
    print("==================================================")


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/vertex_bot.db"
    asyncio.run(reset_database(db_path))


if __name__ == "__main__":
    main()
