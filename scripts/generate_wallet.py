"""Script utilitário para gerar carteiras temporárias em memória RAM para o Vertex-bot.

Gera carteiras para:
- Solana Mainnet (Ed25519)
- Arbitrum One / Base / EVM (secp256k1)
- Modo Unificado (1 Frase de 12 Palavras que comanda ambas as redes simultaneamente)

Uso:
    ./venv/bin/python scripts/generate_wallet.py --unified
    ./venv/bin/python scripts/generate_wallet.py --chain both
    ./venv/bin/python scripts/generate_wallet.py --chain solana
    ./venv/bin/python scripts/generate_wallet.py --chain evm
"""

import argparse
import sys
from pathlib import Path

# Garante que o diretório raiz esteja no sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eth_account import Account
from solders.keypair import Keypair


def generate_solana_wallet() -> tuple[str, str]:
    """Gera um par de chaves avulso para a rede Solana."""
    kp = Keypair()
    address = str(kp.pubkey())
    private_key = str(kp)
    return address, private_key


def generate_evm_wallet() -> tuple[str, str]:
    """Gera um par de chaves avulso para redes EVM (Arbitrum / Base / Polygon / BSC)."""
    acct = Account.create()
    address = str(acct.address)
    private_key = str(acct.key.hex())
    return address, private_key


def generate_unified_multichain_wallet() -> tuple[str, str, str, str, str]:
    """
    Gera uma única Frase de Recuperação (12 palavras) que comanda
    simultaneamente a conta Solana e a conta Arbitrum/Base.
    Retorna: (mnemonic, sol_address, sol_priv, evm_address, evm_priv).
    """
    Account.enable_unaudited_hdwallet_features()  # type: ignore[no-untyped-call]
    evm_acct, mnemonic = Account.create_with_mnemonic()
    evm_address = str(evm_acct.address)
    evm_private_key = str(evm_acct.key.hex())

    # Deriva a carteira Solana a partir da mesma semente
    sol_kp = Keypair.from_seed_phrase_and_passphrase(mnemonic, "")
    sol_address = str(sol_kp.pubkey())
    sol_private_key = str(sol_kp)

    return mnemonic, sol_address, sol_private_key, evm_address, evm_private_key


def main() -> None:
    parser = argparse.ArgumentParser(description="Gerador de Carteiras Temporárias para o Vertex-bot")
    parser.add_argument(
        "--chain",
        choices=["solana", "evm", "both"],
        default="both",
        help="Rede para geração avulsa: 'solana', 'evm' (Arbitrum/Base) ou 'both' (ambas)",
    )
    parser.add_argument(
        "--unified",
        action="store_true",
        help="Gera 1 única frase de 12 palavras que controla as 2 redes simultaneamente na Phantom e no bot",
    )
    args = parser.parse_args()

    print("=" * 76)
    print("🔐 GERADOR DE CARTEIRAS TEMPORÁRIAS EM RAM - VERTEX-BOT")
    print("=" * 76)

    if args.unified:
        mnemonic, sol_addr, sol_priv, evm_addr, evm_priv = generate_unified_multichain_wallet()
        print("🌟 [CARTEIRA UNIFICADA - 1 FRASE PARA AS DUAS REDES]")
        print("   Esta frase de 12 palavras comanda a Solana E a Arbitrum/Base juntas:")
        print(f"\n   📝 Frase Mnemônica (12 Palavras):")
        print(f"   👉 \033[1;33m{mnemonic}\033[0m")
        print("\n   Dica: Se importar essas 12 palavras na Phantom (Adicionar Carteira >")
        print("   Importar Frase Secreta), a Phantom abrirá ambas as redes sob o mesmo perfil!")

        print("\n🟣 [CONTA SOLANA]")
        print(f"   Endereço Público (Envie SOL da Phantom para cá):")
        print(f"   👉 {sol_addr}")
        print(f"   Chave Privada (Cole no Dashboard em 'Conectar Carteira'):")
        print(f"   👉 {sol_priv}")

        print("\n🔵 [CONTA ARBITRUM / BASE (EVM)]")
        print(f"   Endereço Público (Envie ETH/USDC via Arbitrum ou Base para cá):")
        print(f"   👉 {evm_addr}")
        print(f"   Chave Privada (Cole no Dashboard em 'Conectar Carteira'):")
        print(f"   👉 {evm_priv}")

    else:
        print("⚠️  IMPORTANTE:")
        print("   1. Estas chaves são geradas exclusivamente na sua memória local.")
        print("   2. Copie o ENDEREÇO PÚBLICO para enviar fundos a partir da sua Phantom.")
        print("   3. Copie a CHAVE PRIVADA para conectar no Dashboard do bot.")
        print("=" * 76)

        if args.chain in ("solana", "both"):
            sol_addr, sol_priv = generate_solana_wallet()
            print("\n🟣 [SOLANA MAINNET]")
            print(f"   Endereço Público (Envie SOL da Phantom para cá):")
            print(f"   👉 {sol_addr}")
            print(f"   Chave Privada (Cole no Dashboard em 'Conectar Carteira'):")
            print(f"   👉 {sol_priv}")

        if args.chain in ("evm", "both"):
            evm_addr, evm_priv = generate_evm_wallet()
            print("\n🔵 [ARBITRUM ONE / BASE (EVM)]")
            print(f"   Endereço Público (Envie ETH/USDC da Phantom para cá):")
            print(f"   👉 {evm_addr}")
            print(f"   Chave Privada (Cole no Dashboard em 'Conectar Carteira'):")
            print(f"   👉 {evm_priv}")

    print("\n" + "=" * 76)
    print("✅ Concluído! Após abastecer com o valor que deseja operar, conecte")
    print("   as chaves no Dashboard web (http://localhost:8080) na aba 'Operações Reais'.")
    print("=" * 76)


if __name__ == "__main__":
    main()
