"""Script utilitário para transferir SOL de uma carteira local para outro endereço Solana.

Uso interativo:
    ./venv/bin/python scripts/transfer_sol.py

Uso via argumentos:
    ./venv/bin/python scripts/transfer_sol.py --key <CHAVE_PRIVADA> --to <ENDERECO_DESTINO> --amount all
"""

import argparse
import asyncio
import getpass
import sys
from decimal import Decimal
from pathlib import Path

# Garante que o diretório raiz do projeto esteja no sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from solana.rpc.async_api import AsyncClient
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from src.config.settings import get_settings

DEFAULT_FEE_LAMPORTS = 5_000  # Taxa padrão da rede Solana (0.000005 SOL)
LAMPORTS_PER_SOL = 1_000_000_000


async def get_balance_lamports(client: AsyncClient, pubkey: Pubkey) -> int:
    resp = await client.get_balance(pubkey)
    return int(resp.value)


async def send_sol_transfer(
    client: AsyncClient,
    sender_keypair: Keypair,
    recipient_pubkey: Pubkey,
    lamports_to_send: int,
) -> str:
    # 1. Obter recent blockhash
    blockhash_resp = await client.get_latest_blockhash()
    blockhash: Hash = blockhash_resp.value.blockhash

    # 2. Criar instrução de transferência
    transfer_ix = transfer(
        TransferParams(
            from_pubkey=sender_keypair.pubkey(),
            to_pubkey=recipient_pubkey,
            lamports=lamports_to_send,
        )
    )

    # 3. Compilar mensagem da transação V0
    msg = MessageV0.try_compile(
        payer=sender_keypair.pubkey(),
        instructions=[transfer_ix],
        address_lookup_table_accounts=[],
        recent_blockhash=blockhash,
    )

    # 4. Assinar com o Keypair do remetente
    vtx = VersionedTransaction(msg, [sender_keypair])

    # 5. Enviar para a rede
    resp = await client.send_raw_transaction(bytes(vtx))
    return str(resp.value)


def parse_sender_keypair(key_str: str) -> Keypair:
    clean_key = key_str.strip().strip("\"'")
    if not clean_key:
        raise ValueError("Chave privada vazia.")

    # Alerta amigável se colar chave pública (~44 caracteres)
    if 32 <= len(clean_key) <= 50 and not clean_key.startswith("["):
        raise ValueError(
            "Você forneceu o Endereço Público (~44 caracteres) em vez da Chave Privada. "
            "Para transferir fundos, é necessária a Chave Privada (~88 caracteres em Base58)."
        )

    if clean_key.startswith("[") and clean_key.endswith("]"):
        import json
        bytes_list = [int(b) for b in json.loads(clean_key)]
        return Keypair.from_bytes(bytes_list)

    try:
        return Keypair.from_base58_string(clean_key)
    except BaseException as exc:
        raise ValueError(f"Chave privada inválida ou corrompida: {exc}") from exc


async def main_async(args: argparse.Namespace) -> None:
    print("=" * 60)
    print("  💸 Vertex-Bot: Utilitário de Transferência de SOL")
    print("=" * 60)

    app_settings = get_settings()
    rpc_url = args.rpc or getattr(app_settings, "PRIMARY_RPC_HTTP_URL", None) or getattr(app_settings, "SOLANA_RPC_URL", None) or "https://api.mainnet-beta.solana.com"
    client = AsyncClient(rpc_url)

    try:
        # 1. Obter chave privada do remetente
        key_str = args.key
        if not key_str:
            print("\nCole a Chave Privada da sua carteira de origem (a entrada não será exibida na tela):")
            key_str = getpass.getpass("Chave Privada: ").strip()

        if not key_str:
            print("❌ Nenhuma chave privada informada. Operação cancelada.")
            return

        try:
            sender_kp = parse_sender_keypair(key_str)
        except Exception as e:
            print(f"❌ Erro ao decodificar chave privada: {e}")
            return

        sender_pubkey = sender_kp.pubkey()
        print(f"\n📍 Carteira de Origem: {sender_pubkey}")

        # 2. Consultar saldo da carteira
        balance_lamports = await get_balance_lamports(client, sender_pubkey)
        balance_sol = Decimal(balance_lamports) / Decimal(LAMPORTS_PER_SOL)
        print(f"💰 Saldo Disponível: {balance_sol:.6f} SOL ({balance_lamports:,} lamports)")

        if balance_lamports <= DEFAULT_FEE_LAMPORTS:
            print("❌ Saldo insuficiente para pagar a taxa de rede (mínimo ~0.000005 SOL).")
            return

        # 3. Obter carteira de destino
        to_str = args.to
        if not to_str:
            to_str = input("\nDigite o Endereço da Carteira de Destino: ").strip()

        try:
            recipient_pubkey = Pubkey.from_string(to_str)
        except Exception as e:
            print(f"❌ Endereço de destino inválido: {e}")
            return

        if recipient_pubkey == sender_pubkey:
            print("❌ O endereço de destino não pode ser igual ao de origem.")
            return

        # 4. Obter valor a transferir
        amount_str = args.amount
        if not amount_str:
            max_sendable_sol = Decimal(balance_lamports - DEFAULT_FEE_LAMPORTS) / Decimal(LAMPORTS_PER_SOL)
            print(f"\nQuanto deseja enviar? (Digite o valor em SOL ou 'tudo' para enviar {max_sendable_sol:.6f} SOL)")
            amount_str = input("Quantidade: ").strip()

        if amount_str.lower() in ("tudo", "all", "max"):
            lamports_to_send = balance_lamports - DEFAULT_FEE_LAMPORTS
        else:
            try:
                sol_decimal = Decimal(amount_str.replace(",", "."))
                lamports_to_send = int(sol_decimal * Decimal(LAMPORTS_PER_SOL))
            except Exception as e:
                print(f"❌ Valor inválido: {e}")
                return

        if lamports_to_send <= 0:
            print("❌ Valor deve ser maior que zero.")
            return

        if lamports_to_send + DEFAULT_FEE_LAMPORTS > balance_lamports:
            print(f"❌ Saldo insuficiente para enviar essa quantia + taxa de rede.")
            print(f"   Necessário: {(lamports_to_send + DEFAULT_FEE_LAMPORTS) / LAMPORTS_PER_SOL:.6f} SOL")
            print(f"   Disponível: {balance_sol:.6f} SOL")
            return

        sol_to_send = Decimal(lamports_to_send) / Decimal(LAMPORTS_PER_SOL)

        # 5. Confirmação
        print("\n" + "-" * 50)
        print("📋 RESUMO DA TRANSFERÊNCIA:")
        print(f"   Origem:     {sender_pubkey}")
        print(f"   Destino:    {recipient_pubkey}")
        print(f"   Envio:      {sol_to_send:.6f} SOL")
        print(f"   Taxa Rede:  {Decimal(DEFAULT_FEE_LAMPORTS) / Decimal(LAMPORTS_PER_SOL):.6f} SOL (~$0.001)")
        print(f"   Total Deb.: {Decimal(lamports_to_send + DEFAULT_FEE_LAMPORTS) / Decimal(LAMPORTS_PER_SOL):.6f} SOL")
        print("-" * 50)

        if not args.yes:
            confirm = input("\nConfirmar envio desta transação na rede Solana? [S/n]: ").strip().lower()
            if confirm not in ("s", "sim", "y", "yes", ""):
                print("🚫 Operação cancelada pelo usuário.")
                return

        print("\n🚀 Enviando transação para a rede Solana...")
        tx_sig = await send_sol_transfer(client, sender_kp, recipient_pubkey, lamports_to_send)

        print("\n" + "=" * 60)
        print("✅ TRANSFERÊNCIA ENVIADA COM SUCESSO!")
        print(f"🔗 Assinatura: {tx_sig}")
        print(f"🌐 Ver no Solscan: https://solscan.io/tx/{tx_sig}")
        print("=" * 60)

    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Transferir SOL de uma carteira local para outro endereço")
    parser.add_argument("--key", help="Chave privada do remetente (Base58 ou array JSON)", default=None)
    parser.add_argument("--to", help="Endereço público de destino", default=None)
    parser.add_argument("--amount", help="Quantidade em SOL ou 'tudo'/'all'", default=None)
    parser.add_argument("--rpc", help="URL do RPC Solana (opcional)", default=None)
    parser.add_argument("-y", "--yes", action="store_true", help="Pular confirmação interativa")
    args = parser.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n\nOperação interrompida.")
        sys.exit(1)


if __name__ == "__main__":
    main()
