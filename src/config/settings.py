"""
Configurações Globais do Vertex-bot com suporte a Pydantic v2 e fallback nativo.
"""

import os
from decimal import Decimal
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    # === MODO DE OPERAÇÃO ===
    EXECUTION_MODE: Literal["PAPER", "LIVE"] = "PAPER"
    CONFIRM_LIVE_TRADING: bool = False
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # === PROVEDOR DE INGESTÃO (SCANNER) ===
    SCANNER_PROVIDER: Literal["INDEXED", "RAW_RPC"] = "INDEXED"
    DEXSCREENER_API_BASE_URL: str = "https://api.dexscreener.com"
    DEXSCREENER_POLL_INTERVAL_SEC: float = 2.0

    # === AGREGADORES E ROTEAMENTO (JUPITER / JITO) ===
    JUPITER_QUOTE_API_URL: str = "https://quote-api.jup.ag/v6/quote"
    JUPITER_SWAP_API_URL: str = "https://quote-api.jup.ag/v6/swap"
    HELIUS_API_KEY: str | None = None

    # === NÓS RPC & WEBSOCKETS (SOLANA FALLBACK) ===
    PRIMARY_RPC_HTTP_URL: str = "https://api.mainnet-beta.solana.com"
    SECONDARY_RPC_HTTP_URL: str | None = None
    PRIMARY_RPC_WS_URL: str = "wss://api.mainnet-beta.solana.com"

    # === BANCO DE DADOS ===
    SQLITE_DB_PATH: str = "data/vertex_bot.db"

    # === PAPER TRADING DEFAULTS ===
    PAPER_INITIAL_WALLET_USD: Decimal = Decimal("5.0")
    MAX_CONCURRENT_POSITIONS: int = 2
    MIN_TRADE_AMOUNT_USD: Decimal = Decimal("1.0")
    PAPER_INITIAL_BALANCE_SOL: Decimal = Decimal("10.0")
    PAPER_SIMULATED_LATENCY_MS: int = 250
    PAPER_DEFAULT_BUY_AMOUNT_SOL: Decimal = Decimal("0.1")
    PAPER_BUY_AMOUNT_USD: Decimal = Decimal("2.5")
    PRICE_POLL_INTERVAL_SEC: float = 3.0

    # === PARÂMETROS DE RISCO E MITIGAÇÃO ===
    MAX_SLIPPAGE_PCT: Decimal = Decimal("1.5")
    MIN_LIQUIDITY_USD: Decimal = Decimal("5000.0")
    MAX_LIQUIDITY_USD: Decimal = Decimal("250000.0")
    MAX_TOP10_HOLDERS_PCT: Decimal = Decimal("15.0")
    MAX_BUY_TAX_PCT: Decimal = Decimal("3.0")
    MAX_SELL_TAX_PCT: Decimal = Decimal("3.0")
    BREAK_EVEN_GAIN_PCT: Decimal = Decimal("100.0")
    TRAILING_STOP_DROP_PCT: Decimal = Decimal("12.0")
    EMERGENCY_STOP_LOSS_PCT: Decimal = Decimal("20.0")
    MAX_DAILY_DRAWDOWN_PCT: Decimal = Decimal("8.0")

    # === LIVE TRADING ===
    WALLET_PRIVATE_KEY_BASE58: str | None = None


def load_env_file(filepath: str = ".env") -> dict[str, str]:
    """Lê um arquivo .env simples linha a linha."""
    values: dict[str, str] = {}
    if not os.path.exists(filepath):
        return values

    with open(filepath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                values[key.strip()] = val.strip().strip("'\"")
    return values


def _apply_env_var(settings: Settings, k: str, v: str) -> None:
    """Aplica uma única variável convertendo para o tipo de destino."""
    if not hasattr(settings, k):
        return
    current_val = getattr(settings, k)
    if isinstance(current_val, bool):
        setattr(settings, k, v.lower() in ("true", "1", "yes"))
    elif isinstance(current_val, int):
        setattr(settings, k, int(v))
    elif isinstance(current_val, float):
        setattr(settings, k, float(v))
    elif isinstance(current_val, Decimal):
        setattr(settings, k, Decimal(v))
    else:
        setattr(settings, k, v)


def get_settings(env_path: str = ".env") -> Settings:
    """Retorna uma instância validada das configurações."""
    env_vars = load_env_file(env_path)
    settings = Settings()

    # Aplica variáveis encontradas no .env
    for k, v in env_vars.items():
        _apply_env_var(settings, k, v)

    # Se HELIUS_API_KEY foi definida e os endpoints ainda apontam para o RPC público default,
    # monta automaticamente as URLs de alta velocidade da Helius:
    if settings.HELIUS_API_KEY:
        key = str(settings.HELIUS_API_KEY).strip()
        if key:
            if "api.mainnet-beta.solana.com" in settings.PRIMARY_RPC_HTTP_URL:
                settings.PRIMARY_RPC_HTTP_URL = f"https://mainnet.helius-rpc.com/?api-key={key}"
            if "api.mainnet-beta.solana.com" in settings.PRIMARY_RPC_WS_URL:
                settings.PRIMARY_RPC_WS_URL = f"wss://mainnet.helius-rpc.com/?api-key={key}"

    return settings
