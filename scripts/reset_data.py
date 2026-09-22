"""
Script Utilitário para Limpeza Completa de Dados do Vertex-bot (Reset Geral).
Pode ser executado localmente ou em VPS para zerar o banco de dados e arquivos de estado.

Uso:
    python scripts/reset_data.py
    python scripts/reset_data.py --balance 100.0
    python scripts/reset_data.py --keep-logs
"""

import argparse
import asyncio
import json
import os
import sqlite3
import sys

# Garante que o diretório raiz do projeto esteja no sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.database.connection import DatabaseManager


async def reset_database(db_path: str) -> None:
    """Limpa todas as tabelas do banco SQLite e executa checkpoint e compactação VACUUM."""
    if not os.path.exists(db_path):
        print(f"ℹ️ Arquivo de banco {db_path} não existia. Será inicializado limpo.")
        db = DatabaseManager(db_path)
        await db.initialize()
        await db.close()
        return

    print(f"🧹 Limpando tabelas do banco SQLite: {db_path}...")
    db = DatabaseManager(db_path)
    await db.initialize()
    await db.execute("DELETE FROM ordens_executadas;")
    await db.execute("DELETE FROM posicoes;")
    await db.execute("DELETE FROM tokens_catalogados;")
    try:
        await db.execute("DELETE FROM sqlite_sequence;")
    except Exception:
        pass
    await db.close()

    # Executa checkpoint do WAL e VACUUM de forma síncrona
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.execute("VACUUM;")
        conn.close()
    except Exception as exc:
        print(f"⚠️ Aviso ao compactar arquivo SQLite: {exc}")

    print("✅ Banco de dados zerado e compactado com sucesso.")


def reset_state_files(base_dir: str, initial_wallet: float = 110.0) -> None:
    """Zera todos os arquivos JSON de cache, incubadora, fila de espera e controle."""
    json_targets = {
        os.path.join(base_dir, "data", "incubator_tokens.json"): {},
        os.path.join(base_dir, "data", "waiting_tokens.json"): {},
        os.path.join(base_dir, "data", "paper", "waiting_tokens.json"): {},
        os.path.join(base_dir, "data", "paper", "priority_tokens.json"): {},
        os.path.join(base_dir, "data", "paper", "paper_session.json"): {
            "initial_wallet_usd": initial_wallet,
            "current_wallet_usd": initial_wallet,
            "total_pnl_usd": 0.0,
            "trades_count": 0,
            "win_rate_pct": 0.0,
            "is_paused": False,
        },
        os.path.join(base_dir, "data", "paper_session.json"): {
            "initial_wallet_usd": initial_wallet,
            "current_wallet_usd": initial_wallet,
            "total_pnl_usd": 0.0,
            "trades_count": 0,
            "win_rate_pct": 0.0,
            "is_paused": False,
        },
        os.path.join(base_dir, "data", "paper", "bot_status.json"): {
            "is_running": False,
            "is_paused": False,
            "mode": "PAPER",
            "wallet_balance_usd": initial_wallet,
            "initial_wallet_usd": initial_wallet,
            "active_positions_count": 0,
            "slot_quotas": {
                "max_positions": 50,
                "priority_slots_max": 25,
                "priority_slots_used": 0,
                "new_tokens_slots_max": 25,
                "new_tokens_slots_used": 0,
                "total_active": 0,
            },
            "priority_slots_max": 25,
            "priority_slots_used": 0,
            "new_tokens_slots_max": 25,
            "new_tokens_slots_used": 0,
        },
        os.path.join(base_dir, "data", "bot_status.json"): {
            "is_running": False,
            "is_paused": False,
            "mode": "PAPER",
            "wallet_balance_usd": initial_wallet,
            "initial_wallet_usd": initial_wallet,
            "active_positions_count": 0,
        },
        os.path.join(base_dir, "data", "bot_control.json"): {"action": "none"},
        os.path.join(base_dir, "data", "paper", "bot_control.json"): {"action": "none"},
        os.path.join(base_dir, "data", "live", "bot_control.json"): {"action": "none"},
    }

    for file_path, content in json_targets.items():
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2)
        print(f"✅ Resetado: {os.path.relpath(file_path, base_dir)}")


def git_clean_and_protect_data(base_dir: str, do_pull: bool = False) -> None:
    """
    Descarta alterações locais do Git na pasta data/ para evitar conflito de merge no 'git pull',
    opcionalmente executa o 'git pull origin main', e marca os arquivos como assume-unchanged.
    """
    try:
        import subprocess

        git_dir = os.path.join(base_dir, ".git")
        if not os.path.exists(git_dir):
            return

        print("🧹 Verificando e limpando travas do Git na pasta data/...")
        # 1. Descarta alterações locais em data/ para destravar o git pull
        subprocess.run(
            ["git", "checkout", "--", "data/"],
            cwd=base_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        print("✅ Arquivos de data/ revertidos para o estado limpo do Git.")

        # 2. Se a flag --pull for passada, executa git pull origin main
        if do_pull:
            print("🔄 Executando git pull origin main...")
            pull_res = subprocess.run(
                ["git", "pull", "origin", "main"],
                cwd=base_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if pull_res.returncode == 0:
                print("✅ Git pull concluído com sucesso:\n" + pull_res.stdout.strip())
            else:
                print("⚠️ Aviso no git pull:\n" + (pull_res.stderr or pull_res.stdout).strip())

        # 3. Marca os arquivos rastreados em data/ com assume-unchanged para que o git
        # nunca mais bloqueie pulls por conta de alterações operacionais do bot
        tracked_candidates = [
            "data/bot_status.json",
            "data/bot_control.json",
            "data/bot_config.json",
            "data/incubator_tokens.json",
            "data/waiting_tokens.json",
            "data/paper_session.json",
            "data/paper/bot_status.json",
            "data/paper/bot_control.json",
            "data/paper/bot_config.json",
            "data/paper/priority_tokens.json",
            "data/paper/waiting_tokens.json",
            "data/paper/paper_session.json",
            "data/live/bot_status.json",
            "data/live/bot_control.json",
        ]
        for tf in tracked_candidates:
            full_p = os.path.join(base_dir, tf)
            if os.path.exists(full_p):
                subprocess.run(
                    ["git", "update-index", "--assume-unchanged", tf],
                    cwd=base_dir,
                    capture_output=True,
                    check=False,
                )
        print("🛡️ Arquivos dinâmicos de data/ marcados com assume-unchanged (não bloquearão mais git pull).")
    except Exception as exc:
        print(f"ℹ️ Rotina Git ignorada: {exc}")


def reset_logs(base_dir: str) -> None:
    """Zera o arquivo de logs para um início limpo."""
    log_path = os.path.join(base_dir, "logs", "vertex.log")
    if os.path.exists(log_path):
        with open(log_path, "w", encoding="utf-8"):
            pass
        print("✅ Arquivo logs/vertex.log truncado com sucesso.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Zera todos os registros e dados do Vertex-bot.")
    parser.add_argument(
        "--balance",
        type=float,
        default=110.0,
        help="Saldo simulado inicial em USD para a carteira (padrão: 110.0)",
    )
    parser.add_argument(
        "--keep-logs",
        action="store_true",
        help="Não apagar o histórico de logs/vertex.log",
    )
    parser.add_argument(
        "--pull",
        action="store_true",
        help="Executa git checkout em data/ e git pull origin main antes de resetar",
    )
    args = parser.parse_args()

    print("=" * 65)
    print("🚀 INICIANDO LIMPEZA COMPLETA DE DADOS DO VERTEX-BOT")
    print("=" * 65)

    # 1. Limpa travas do Git e opcionalmente sincroniza código
    git_clean_and_protect_data(BASE_DIR, do_pull=args.pull)

    # 2. Reseta o banco de dados SQLite
    db_path = os.path.join(BASE_DIR, "data", "vertex_bot.db")
    await reset_database(db_path)

    # 3. Zera os arquivos de estado JSON
    reset_state_files(BASE_DIR, initial_wallet=args.balance)

    # 4. Trunca os logs (opcional)
    if not args.keep_logs:
        reset_logs(BASE_DIR)

    # 5. Reaplica proteção assume-unchanged aos arquivos de estado recém-gerados
    git_clean_and_protect_data(BASE_DIR, do_pull=False)

    print("=" * 65)
    print("✨ LIMPEZA CONCLUÍDA! O bot está pronto para iniciar do zero.")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())

