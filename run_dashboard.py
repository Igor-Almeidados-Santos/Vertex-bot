#!/usr/bin/env python3
"""
Ponto de Entrada Avulso para o Dashboard Web do Vertex-bot.
Permite monitorar o banco SQLite (WAL mode) sem interferir no bot em execução.
"""

import argparse
import asyncio
import sys

from src.dashboard.server import run_dashboard_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Vertex-bot Web Dashboard")
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Porta HTTP para o servidor do dashboard (padrão: 8080)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host HTTP para o servidor do dashboard (padrão: 0.0.0.0)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="data/vertex_bot.db",
        help="Caminho para o banco de dados SQLite (padrão: data/vertex_bot.db)",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            run_dashboard_server(
                db_path=args.db_path,
                host=args.host,
                port=args.port,
            )
        )
    except KeyboardInterrupt:
        print("\nDashboard encerrado pelo usuário.")
        sys.exit(0)


if __name__ == "__main__":
    main()

