"""
Validador de Dinâmica de Mercado e Proteção Anti-Dump (Market Dynamics Gates).
Avalia Volume 1h, Pressão Compradora (Buy/Sell Ratio), Anti-Falling-Knife e
Elegibilidade Estrutural por Estratégia (SCALP vs SWING).
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from src.database.models import TokenMetadata
from src.utils.logger import setup_logger

logger = setup_logger("vertex.security.market_dynamics")


class MarketDynamicsValidator:
    """Valida métricas quantitativas de liquidez, volume, fluxo comprador e momentum."""

    def __init__(
        self,
        min_volume_1h_usd: Decimal = Decimal("15000.0"),
        min_buy_ratio_5m_pct: Decimal = Decimal("50.0"),
        min_price_change_5m_pct: Decimal = Decimal("-2.0"),
        min_age_hours_scalp: float = 0.5,
        max_age_hours_scalp: float = 4.0,
        min_age_hours_swing: float = 2.0,
        max_age_hours_swing: float = 48.0,
        min_liquidity_scalp_usd: Decimal = Decimal("5000.0"),
        min_liquidity_swing_usd: Decimal = Decimal("20000.0"),
    ) -> None:
        self.min_volume_1h_usd: Decimal = min_volume_1h_usd
        self.min_buy_ratio_5m_pct: Decimal = min_buy_ratio_5m_pct
        self.min_price_change_5m_pct: Decimal = min_price_change_5m_pct
        self.min_age_hours_scalp: float = min_age_hours_scalp
        self.max_age_hours_scalp: float = max_age_hours_scalp
        self.min_age_hours_swing: float = min_age_hours_swing
        self.max_age_hours_swing: float = max_age_hours_swing
        self.min_liquidity_scalp_usd: Decimal = min_liquidity_scalp_usd
        self.min_liquidity_swing_usd: Decimal = min_liquidity_swing_usd

    def extract_metrics(self, token: TokenMetadata) -> dict[str, Any]:
        """Extrai e normaliza as métricas de mercado a partir do token e de seus metadados brutos."""
        raw_event: dict[str, Any] = token.raw_event if isinstance(token.raw_event, dict) else {}
        pair_data: dict[str, Any] = (
            raw_event.get("pair_data")
            or (raw_event.get("hint", {}).get("pair_data") if isinstance(raw_event.get("hint"), dict) else {})
            or {}
        )

        now_utc = datetime.now(UTC)

        # 1. Idade do par em horas
        age_hours: float | None = None
        if "age_hours" in raw_event and raw_event["age_hours"] is not None:
            try:
                age_hours = float(raw_event["age_hours"])
            except (ValueError, TypeError):
                age_hours = None

        if age_hours is None and pair_data.get("pairCreatedAt"):
            try:
                created_ms = float(pair_data["pairCreatedAt"])
                if created_ms > 0:
                    age_hours = max(0.0, (now_utc.timestamp() * 1000.0 - created_ms) / (1000.0 * 3600.0))
            except (ValueError, TypeError):
                age_hours = None

        if age_hours is None:
            raw_hint = raw_event.get("hint")
            hint: dict[str, Any] = raw_hint if isinstance(raw_hint, dict) else {}
            pool_iso = hint.get("pool_created_at") or raw_event.get("pool_created_at")
            if pool_iso:
                try:
                    clean_ts = str(pool_iso).replace("Z", "+00:00")
                    dt = datetime.fromisoformat(clean_ts)
                    age_hours = max(0.0, (now_utc - dt).total_seconds() / 3600.0)
                except Exception:
                    age_hours = None

        # 2. Volume em 1 hora
        vol_dict = pair_data.get("volume", {}) if isinstance(pair_data.get("volume"), dict) else {}
        volume_1h = Decimal(str(vol_dict.get("h1") or 0.0))

        # 3. Variação de preço em 5 minutos
        pc_dict = pair_data.get("priceChange", {}) if isinstance(pair_data.get("priceChange"), dict) else {}
        price_change_5m = Decimal(str(pc_dict.get("m5") or 0.0))

        # 4. Transações de compra e venda em 5 minutos
        tx_dict = pair_data.get("txns", {}) if isinstance(pair_data.get("txns"), dict) else {}
        m5_dict = tx_dict.get("m5", {}) if isinstance(tx_dict.get("m5"), dict) else {}
        buys = int(m5_dict.get("buys") or 0)
        sells = int(m5_dict.get("sells") or 0)
        total_txns = buys + sells
        buy_ratio_5m = (Decimal(str(buys)) / Decimal(str(total_txns)) * Decimal("100.0")) if total_txns > 0 else Decimal("0.0")

        # 5. Liquidez USD
        liq_dict = pair_data.get("liquidity", {}) if isinstance(pair_data.get("liquidity"), dict) else {}
        pair_liq = Decimal(str(liq_dict.get("usd") or 0.0))
        liquidity_usd = max(token.initial_liquidity_usd, pair_liq)

        return {
            "has_pair_data": bool(pair_data),
            "age_hours": age_hours,
            "volume_1h_usd": volume_1h,
            "price_change_5m_pct": price_change_5m,
            "buys_5m": buys,
            "sells_5m": sells,
            "total_txns_5m": total_txns,
            "buy_ratio_5m_pct": buy_ratio_5m,
            "liquidity_usd": liquidity_usd,
        }

    def evaluate(
        self,
        token: TokenMetadata,
        strategy_mode: str = "DUAL",
        mock_override: bool | None = None,
    ) -> tuple[bool, str | None, dict[str, Any]]:
        """
        Executa os Hard Gates de dinâmica de mercado.
        Retorna (is_approved: bool, rejection_reason: str | None, details: dict[str, Any]).
        """
        if mock_override is not None:
            return (
                mock_override,
                None if mock_override else "Reprovado por mock de mercado",
                {"eligible_scalp": mock_override, "eligible_swing": mock_override},
            )

        metrics = self.extract_metrics(token)
        has_pair = metrics["has_pair_data"]
        age_hours = metrics["age_hours"]
        volume_1h = metrics["volume_1h_usd"]
        price_change_5m = metrics["price_change_5m_pct"]
        total_txns = metrics["total_txns_5m"]
        buy_ratio_5m = metrics["buy_ratio_5m_pct"]
        liquidity_usd = metrics["liquidity_usd"]

        # Se temos dados detalhados de mercado (DexScreener pair_data), validamos os gates de momentum e fluxo:
        if has_pair:
            # Gate 1: Anti-Falling Knife (Queda abrupta recente em 5m)
            if price_change_5m < self.min_price_change_5m_pct:
                reason = (
                    f"Faca caindo detectada: variação de 5m ({price_change_5m:.1f}%) "
                    f"abaixo do limite de proteção ({self.min_price_change_5m_pct:.1f}%)"
                )
                return False, reason, metrics

            # Gate 2: Volume Mínimo 1h
            if volume_1h < self.min_volume_1h_usd:
                reason = (
                    f"Volume 1h (${volume_1h:,.2f}) insuficiente "
                    f"(mínimo exigido: ${self.min_volume_1h_usd:,.2f})"
                )
                return False, reason, metrics

            # Gate 3: Pressão Compradora (Buy Ratio em 5m)
            if total_txns >= 5 and buy_ratio_5m < self.min_buy_ratio_5m_pct:
                reason = (
                    f"Pressão vendedora dominante: compras representam apenas {buy_ratio_5m:.1f}% "
                    f"das transações em 5m (< {self.min_buy_ratio_5m_pct:.1f}%)"
                )
                return False, reason, metrics

        # Gate 4: Elegibilidade por Estratégia (Idade e Liquidez)
        eligible_scalp = False
        if (
            age_hours is not None
            and self.min_age_hours_scalp <= age_hours <= self.max_age_hours_scalp
            and liquidity_usd >= self.min_liquidity_scalp_usd
        ):
            eligible_scalp = True

        eligible_swing = False
        if (
            age_hours is not None
            and self.min_age_hours_swing <= age_hours <= self.max_age_hours_swing
            and liquidity_usd >= self.min_liquidity_swing_usd
        ):
            eligible_swing = True

        metrics["eligible_scalp"] = eligible_scalp
        metrics["eligible_swing"] = eligible_swing

        mode_upper = strategy_mode.upper()

        if mode_upper == "SCALP_ONLY":
            if not eligible_scalp:
                if age_hours is not None and age_hours < self.min_age_hours_scalp:
                    reason = (
                        f"Idade do token ({age_hours * 60.0:.0f}m) abaixo do mínimo para Scalp "
                        f"({self.min_age_hours_scalp * 60.0:.0f}m) - Risco de sniper dump"
                    )
                elif age_hours is not None and age_hours > self.max_age_hours_scalp:
                    reason = (
                        f"Idade do token ({age_hours:.1f}h) acima do teto para Scalp "
                        f"({self.max_age_hours_scalp:.1f}h)"
                    )
                else:
                    reason = (
                        f"Token inelegível para Scalp: liquidez (${liquidity_usd:,.2f}) "
                        f"abaixo do mínimo (${self.min_liquidity_scalp_usd:,.2f})"
                    )
                return False, reason, metrics
            return True, None, metrics

        elif mode_upper == "SWING_ONLY":
            if not eligible_swing:
                if age_hours is not None and age_hours < self.min_age_hours_swing:
                    reason = (
                        f"Idade do token ({age_hours:.1f}h) abaixo do mínimo para Swing "
                        f"({self.min_age_hours_swing:.1f}h) - Falta de consolidação"
                    )
                elif age_hours is not None and age_hours > self.max_age_hours_swing:
                    reason = (
                        f"Idade do token ({age_hours:.1f}h) acima do teto para Swing "
                        f"({self.max_age_hours_swing:.1f}h)"
                    )
                else:
                    reason = (
                        f"Token inelegível para Swing: liquidez (${liquidity_usd:,.2f}) "
                        f"abaixo do piso (${self.min_liquidity_swing_usd:,.2f})"
                    )
                return False, reason, metrics
            return True, None, metrics

        else:  # "DUAL"
            if not eligible_scalp and not eligible_swing:
                if age_hours is not None and age_hours < self.min_age_hours_scalp:
                    reason = (
                        f"Idade do token ({age_hours * 60.0:.0f}m) abaixo do mínimo de segurança "
                        f"({self.min_age_hours_scalp * 60.0:.0f}m) - Risco de sniper dump"
                    )
                else:
                    age_str = f"{age_hours:.1f}h" if age_hours is not None else "N/D"
                    reason = (
                        f"Token inelegível para Dual-Track (Idade: {age_str}, "
                        f"Liquidez: ${liquidity_usd:,.2f})"
                    )
                return False, reason, metrics
            return True, None, metrics
