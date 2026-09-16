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
    SCANNER_PROVIDER: Literal["MATURE_POOLS", "INDEXED", "RAW_RPC", "PUMPPORTAL", "HYBRID", "GRADUATIONS"] = "HYBRID"
    MIN_TOKEN_AGE_HOURS: float = 2.0   # Mínimo 2 horas (elimina zona de morte e cascatas de snipers/devs)
    MAX_TOKEN_AGE_HOURS: float = 720.0 # Até 720 horas (1 mês) para Scalp consolidado
    ENABLE_ESTABLISHED_POOLS: bool = True  # Ativa busca de pools consolidadas e trending
    MATURE_POOLS_POLL_INTERVAL_SEC: float = 5.0
    WATCHLIST_POLL_INTERVAL_SEC: float = 25.0  # Intervalo de verificação de reentrada na watchlist
    WATCHLIST_RETRY_COOLDOWN_SEC: float = 60.0  # Cooldown por token na watchlist
    GECKOTERMINAL_API_BASE_URL: str = "https://api.geckoterminal.com"
    PUMPPORTAL_WS_URL: str = "wss://pumpportal.fun/api/data"
    GRADUATION_WS_URL: str = "wss://pumpportal.fun/api/data"
    ESTIMATED_SOL_PRICE_USD: Decimal = Decimal("150.0")
    DEXSCREENER_API_BASE_URL: str = "https://api.dexscreener.com"
    DEXSCREENER_POLL_INTERVAL_SEC: float = 2.0

    # === AGREGADORES E ROTEAMENTO (JUPITER / JITO) ===
    JUPITER_QUOTE_API_URL: str = "https://quote-api.jup.ag/v6/quote"
    JUPITER_SWAP_API_URL: str = "https://quote-api.jup.ag/v6/swap"
    HELIUS_API_KEY: str | None = "b58666c5-72ea-46a6-b496-aae641ddd71a"

    # === NÓS RPC & WEBSOCKETS (SOLANA FALLBACK) ===
    PRIMARY_RPC_HTTP_URL: str = "https://mainnet.helius-rpc.com/?api-key=b58666c5-72ea-46a6-b496-aae641ddd71a"
    SECONDARY_RPC_HTTP_URL: str | None = None
    PRIMARY_RPC_WS_URL: str = "wss://mainnet.helius-rpc.com/?api-key=b58666c5-72ea-46a6-b496-aae641ddd71a"

    # === BANCO DE DADOS ===
    SQLITE_DB_PATH: str = "data/vertex_bot.db"

    # === PAPER TRADING DEFAULTS ===
    PAPER_INITIAL_WALLET_USD: Decimal = Decimal("5.0")
    MAX_CONCURRENT_POSITIONS: int = 50
    MIN_TRADE_AMOUNT_USD: Decimal = Decimal("1.0")
    PAPER_INITIAL_BALANCE_SOL: Decimal = Decimal("10.0")
    PAPER_SIMULATED_LATENCY_MS: int = 250
    PAPER_DEFAULT_BUY_AMOUNT_SOL: Decimal = Decimal("0.1")
    PAPER_BUY_AMOUNT_USD: Decimal = Decimal("1.0")
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

    # === PARÂMETROS DE REENTRADA INTELIGENTE (ANTI-FALLING-KNIFE) ===
    REENTRY_TRAILING_COOLOFF_SEC: float = 300.0  # 5 minutos após trailing stop
    REENTRY_STOPLOSS_COOLOFF_SEC: float = 1800.0  # 30 minutos após stop loss
    REENTRY_MIN_BOUNCE_PCT: Decimal = Decimal("3.0")  # Repique de 3% a partir do fundo
    REENTRY_MAX_DROP_PCT: Decimal = Decimal("25.0")  # Queda máxima pós-saída sem repique

    # === ESTRATÉGIAS DUAL-TRACK (SCALP + SWING RATCHET) ===
    TRADING_STRATEGY_MODE: Literal["DUAL", "SCALP_ONLY", "SWING_ONLY"] = "DUAL"
    SCALP_MAX_HOLD_MINUTES: float = 60.0             # Duração máxima da perna Scalp: até 1 hora
    SCALP_TARGET_GAIN_PCT: Decimal = Decimal("100.0") # Alvo de saída Scalp: +100% (2x) com 100% de venda
    SWING_MAX_HOLD_HOURS: float = 24.0               # Duração máxima da perna Swing: até 24 horas
    SWING_TARGET_GAIN_PCT: Decimal = Decimal("2000.0")# Alvo mestre de lucro: até +2.000% (21x)
    SWING_MAX_HOURLY_DROP_PCT: Decimal = Decimal("15.0") # Queda máxima na hora para decidir encerramento
    SWING_INITIAL_STOP_LOSS_PCT: Decimal = Decimal("0.0")
    SWING_TIER1_TARGET_MULT: Decimal = Decimal("2.0")  # +100% (2x): Piso sobe para Entrada ($x / BE)
    SWING_TIER2_TARGET_MULT: Decimal = Decimal("4.0")  # +300% (4x): Piso sobe para 2x (+100% travado)
    SWING_TIER3_TARGET_MULT: Decimal = Decimal("6.0")  # +500% (6x): Piso sobe para 4x (+300% travado)
    SWING_TIER4_TARGET_MULT: Decimal = Decimal("11.0") # +1.000% (11x): Piso sobe para 6x (+500% travado)
    SWING_TIER5_TARGET_MULT: Decimal = Decimal("21.0") # +2.000% (21x): Alvo mestre atingido (100% Take Profit)
    SWING_TRAILING_DROP_PCT: Decimal = Decimal("25.0") # Trailing stop elástico (-25%)
    MAX_SCALP_POSITIONS: int = 3
    MAX_SWING_POSITIONS: int = 3

    # === PARÂMETROS DE DINÂMICA DE MERCADO (ANTI-DUMP & SELEÇÃO) ===
    MIN_TOKEN_AGE_HOURS_SCALP: float = 2.0   # Mínimo 2 horas para Scalp (supera a zona de cascata de snipers)
    MAX_TOKEN_AGE_HOURS_SCALP: float = 720.0 # Até 720 horas (1 mês) para Scalp consolidado
    MIN_TOKEN_AGE_HOURS_SWING: float = 3.0   # Mínimo 3 horas de consolidação para Swing
    MAX_TOKEN_AGE_HOURS_SWING: float = 6.0   # Máximo 6 horas na entrada de Swing
    SWING_INCUBATOR_MIN_LIQUIDITY_USD: Decimal = Decimal("15000.0") # Piso de liquidez para manter token em incubação para Swing
    MIN_VOLUME_1H_USD: Decimal = Decimal("15000.0")  # Giro mínimo em 1h
    MIN_BUY_RATIO_5M_PCT: Decimal = Decimal("50.0")  # Ao menos 50% de compras em 5m
    MIN_PRICE_CHANGE_5M_PCT: Decimal = Decimal("-2.0")  # Não comprar em queda livre
    MIN_LIQUIDITY_SWING_USD: Decimal = Decimal("20000.0")  # Liquidez mais densa para swing
    MAX_SELLER_TO_BUYER_RATIO: Decimal = Decimal("1.5")  # Rejeita se vendedores superarem compradores em mais de 50%
    MIN_LIQUIDITY_TO_VOLUME_RATIO: Decimal = Decimal("0.05")  # Liquidez deve ser >= 5% do volume de 24h (anti-drenagem)
    MIN_UNIQUE_TRADERS_24H: int = 15  # Mínimo de traders únicos para tokens com volume expressivo
    MAX_PARABOLIC_1H_GAIN_PCT: Decimal = Decimal("250.0")  # Teto de rali parabólico em 1h para exaustão pós-pump

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

    # Limpa URLs ou chaves com placeholder default (ex: YOUR-KEY)
    placeholders = ("YOUR-KEY", "YOUR_KEY", "YOUR-API-KEY", "YOUR_API_KEY", "CHANGEME", "<KEY>", "<API_KEY>")
    if settings.SECONDARY_RPC_HTTP_URL and any(ph in settings.SECONDARY_RPC_HTTP_URL for ph in placeholders):
        settings.SECONDARY_RPC_HTTP_URL = None

    # Se HELIUS_API_KEY foi definida e válida, monta automaticamente URLs da Helius
    if settings.HELIUS_API_KEY:
        key = str(settings.HELIUS_API_KEY).strip()
        if key and not any(ph in key for ph in placeholders):
            if any(default_node in settings.PRIMARY_RPC_HTTP_URL for default_node in ("api.mainnet-beta.solana.com", "publicnode.com", "helius-rpc.com")):
                settings.PRIMARY_RPC_HTTP_URL = f"https://mainnet.helius-rpc.com/?api-key={key}"
            if any(default_node in settings.PRIMARY_RPC_WS_URL for default_node in ("api.mainnet-beta.solana.com", "publicnode.com", "helius-rpc.com")):
                settings.PRIMARY_RPC_WS_URL = f"wss://mainnet.helius-rpc.com/?api-key={key}"
    elif settings.PRIMARY_RPC_HTTP_URL == "https://api.mainnet-beta.solana.com":
        # Se não há chave privada e a URL caiu no nó padrão que bloqueia cloud/VPS, desvia para nó público mais estável
        settings.PRIMARY_RPC_HTTP_URL = "https://solana-rpc.publicnode.com"

    return settings
