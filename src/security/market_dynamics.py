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

    # Contratos oficiais imutáveis de ativos de referência na Solana
    OFFICIAL_CONTRACTS: dict[str, str] = {
        "SOL": "So11111111111111111111111111111111111111112",
        "WSOL": "So11111111111111111111111111111111111111112",
        "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    }

    def __init__(
        self,
        min_volume_1h_usd: Decimal = Decimal("15000.0"),
        min_buy_ratio_5m_pct: Decimal = Decimal("50.0"),
        min_price_change_5m_pct: Decimal = Decimal("-2.0"),
        min_age_hours_scalp: float = 2.0,
        max_age_hours_scalp: float = 720.0,
        min_age_hours_swing: float = 3.0,
        max_age_hours_swing: float = 6.0,
        min_liquidity_scalp_usd: Decimal = Decimal("5000.0"),
        min_liquidity_swing_usd: Decimal = Decimal("20000.0"),
        max_liquidity_usd: Decimal = Decimal("250000.0"),
        max_seller_to_buyer_ratio: Decimal = Decimal("1.5"),
        min_liquidity_to_volume_ratio: Decimal = Decimal("0.05"),
        min_unique_traders_24h: int = 15,
        max_parabolic_1h_gain_pct: Decimal = Decimal("250.0"),
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
        self.max_liquidity_usd: Decimal = max_liquidity_usd
        self.max_seller_to_buyer_ratio: Decimal = max_seller_to_buyer_ratio
        self.min_liquidity_to_volume_ratio: Decimal = min_liquidity_to_volume_ratio
        self.min_unique_traders_24h: int = min_unique_traders_24h
        self.max_parabolic_1h_gain_pct: Decimal = max_parabolic_1h_gain_pct

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

        # 2. Volumes
        vol_dict = pair_data.get("volume", {}) if isinstance(pair_data.get("volume"), dict) else {}
        volume_1h = Decimal(str(vol_dict.get("h1") or 0.0))
        volume_24h = Decimal(str(vol_dict.get("h24") or 0.0))

        # 3. Variações de preço
        pc_dict = pair_data.get("priceChange", {}) if isinstance(pair_data.get("priceChange"), dict) else {}
        price_change_5m = Decimal(str(pc_dict.get("m5") or 0.0))
        price_change_1h = Decimal(str(pc_dict.get("h1") or 0.0))
        price_change_24h = Decimal(str(pc_dict.get("h24") or 0.0))

        # 4. Transações de compra e venda (5m e 24h)
        tx_dict = pair_data.get("txns", {}) if isinstance(pair_data.get("txns"), dict) else {}
        m5_dict = tx_dict.get("m5", {}) if isinstance(tx_dict.get("m5"), dict) else {}
        buys_5m = int(m5_dict.get("buys") or 0)
        sells_5m = int(m5_dict.get("sells") or 0)
        total_txns_5m = buys_5m + sells_5m
        buy_ratio_5m = (
            (Decimal(str(buys_5m)) / Decimal(str(total_txns_5m)) * Decimal("100.0"))
            if total_txns_5m > 0
            else Decimal("0.0")
        )

        h24_dict = tx_dict.get("h24", {}) if isinstance(tx_dict.get("h24"), dict) else {}
        buys_24h = int(h24_dict.get("buys") or 0)
        sells_24h = int(h24_dict.get("sells") or 0)
        total_txns_24h = buys_24h + sells_24h

        # 5. Traders (compradores e vendedores únicos)
        traders_dict = pair_data.get("traders", {}) if isinstance(pair_data.get("traders"), dict) else {}
        buyers = int(traders_dict.get("buyers") or 0)
        sellers = int(traders_dict.get("sellers") or 0)
        total_traders = int(traders_dict.get("total") or 0)
        if total_traders <= 0 and (buyers > 0 or sellers > 0):
            total_traders = max(buyers, sellers)

        # 6. Liquidez USD e saldos do par
        liq_dict = pair_data.get("liquidity", {}) if isinstance(pair_data.get("liquidity"), dict) else {}
        pair_liq = Decimal(str(liq_dict.get("usd") or 0.0))
        pooled_quote = Decimal(str(liq_dict.get("quote") or 0.0))
        pooled_base = Decimal(str(liq_dict.get("base") or 0.0))
        liquidity_usd = max(token.initial_liquidity_usd, pair_liq)

        # 7. Identificação dos tokens do par
        base_token = pair_data.get("baseToken", {}) if isinstance(pair_data.get("baseToken"), dict) else {}
        quote_token = pair_data.get("quoteToken", {}) if isinstance(pair_data.get("quoteToken"), dict) else {}
        base_symbol = str(base_token.get("symbol") or token.symbol).upper().strip()
        base_address = str(base_token.get("address") or token.address).strip()
        quote_symbol = str(quote_token.get("symbol") or "").upper().strip()
        quote_address = str(quote_token.get("address") or "").strip()

        # 8. Elegibilidade Estrutural por Estratégia
        eligible_scalp = False
        if (
            (age_hours is None or self.min_age_hours_scalp <= age_hours <= self.max_age_hours_scalp)
            and liquidity_usd >= self.min_liquidity_scalp_usd
            and liquidity_usd <= self.max_liquidity_usd
        ):
            eligible_scalp = True

        eligible_swing = False
        if (
            age_hours is not None
            and self.min_age_hours_swing <= age_hours <= self.max_age_hours_swing
            and liquidity_usd >= self.min_liquidity_swing_usd
            and liquidity_usd <= self.max_liquidity_usd
        ):
            eligible_swing = True

        return {
            "has_pair_data": bool(pair_data),
            "age_hours": age_hours,
            "volume_1h_usd": volume_1h,
            "volume_24h_usd": volume_24h,
            "price_change_5m_pct": price_change_5m,
            "price_change_1h_pct": price_change_1h,
            "price_change_24h_pct": price_change_24h,
            "buys_5m": buys_5m,
            "sells_5m": sells_5m,
            "total_txns_5m": total_txns_5m,
            "buy_ratio_5m_pct": buy_ratio_5m,
            "buys_24h": buys_24h,
            "sells_24h": sells_24h,
            "total_txns_24h": total_txns_24h,
            "buyers": buyers,
            "sellers": sellers,
            "total_traders": total_traders,
            "liquidity_usd": liquidity_usd,
            "pooled_quote": pooled_quote,
            "pooled_base": pooled_base,
            "base_symbol": base_symbol,
            "base_address": base_address,
            "quote_symbol": quote_symbol,
            "quote_address": quote_address,
            "eligible_scalp": eligible_scalp,
            "eligible_swing": eligible_swing,
        }

    def evaluate(
        self,
        token: TokenMetadata,
        strategy_mode: str = "DUAL",
        mock_override: bool | None = None,
    ) -> tuple[bool, str | None, dict[str, Any]]:
        """
        Executa os Hard Gates de dinâmica de mercado e proteção anti-fraude.
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
        volume_24h = metrics["volume_24h_usd"]
        price_change_5m = metrics["price_change_5m_pct"]
        price_change_1h = metrics["price_change_1h_pct"]
        price_change_24h = metrics["price_change_24h_pct"]
        total_txns_5m = metrics["total_txns_5m"]
        buy_ratio_5m = metrics["buy_ratio_5m_pct"]
        buys_24h = metrics["buys_24h"]
        sells_24h = metrics["sells_24h"]
        total_txns_24h = metrics["total_txns_24h"]
        buyers = metrics["buyers"]
        sellers = metrics["sellers"]
        total_traders = metrics["total_traders"]
        liquidity_usd = metrics["liquidity_usd"]
        pooled_quote = metrics["pooled_quote"]
        base_symbol = metrics["base_symbol"]
        base_address = metrics["base_address"]
        quote_symbol = metrics["quote_symbol"]

        # =========================================================================
        # HARD GATE A: ANTI-IMPERSONATION DE ATIVOS NATIVOS (SOL, USDC, USDT)
        # =========================================================================
        if base_symbol in self.OFFICIAL_CONTRACTS:
            expected_addr = self.OFFICIAL_CONTRACTS[base_symbol]
            if base_address != expected_addr and token.address != expected_addr:
                reason = (
                    f"Impersonação de token oficial detectada: símbolo {base_symbol} "
                    f"com contrato fraudulento ({base_address[:8]}...)"
                )
                return False, reason, metrics

        # =========================================================================
        # HARD GATE B: TETO DE LIQUIDEZ E ANTI-FAKE CLMM
        # =========================================================================
        if liquidity_usd > self.max_liquidity_usd:
            reason = (
                f"Liquidez excessiva (${liquidity_usd:,.2f} > ${self.max_liquidity_usd:,.2f}) - "
                f"Risco de piscina CLMM concentrada artificialmente"
            )
            return False, reason, metrics

        # =========================================================================
        # HARD GATE C: PISO ABSOLUTO DE LIQUIDEZ EM TEMPO REAL
        # =========================================================================
        if liquidity_usd < self.min_liquidity_scalp_usd:
            reason = (
                f"Liquidez atualizada insuficiente (${liquidity_usd:,.2f} < "
                f"${self.min_liquidity_scalp_usd:,.2f})"
            )
            return False, reason, metrics

        # =========================================================================
        # GATES AVANÇADOS COM DADOS DE PAR (DEXSCREENER)
        # =========================================================================
        if not has_pair:
            reason = (
                "Dados de mercado e volume não localizados na DEX "
                "(Token sem pool AMM verificada ou não indexado com liquidez no DexScreener)"
            )
            return False, reason, metrics

        if has_pair:
            # GATE D: LIQUIDEZ REAL DA MOEDA DE COTAÇÃO (QUOTE TOKEN)
            if quote_symbol in ("USDC", "USDT") and 0.0 < pooled_quote < self.min_liquidity_scalp_usd:
                reason = (
                    f"Liquidez real de cotação insuficiente: ${pooled_quote:,.2f} {quote_symbol} "
                    f"(mínimo exigido: ${self.min_liquidity_scalp_usd:,.2f})"
                )
                return False, reason, metrics

            if quote_symbol in ("SOL", "WSOL") and 0.0 < pooled_quote < Decimal("15.0"):
                reason = (
                    f"Liquidez real do par em SOL esgotada/drenada: {pooled_quote:.2f} SOL "
                    f"(piso seguro de mercado: 15.0 SOL)"
                )
                return False, reason, metrics

            # GATE E: ANTI-PREÇO CONGELADO / GRÁFICO BARCODE (WASH TRADING)
            if volume_1h >= Decimal("10000.0"):
                if (
                    abs(price_change_5m) < Decimal("0.001")
                    and abs(price_change_1h) < Decimal("0.01")
                    and abs(price_change_24h) < Decimal("0.05")
                ):
                    reason = (
                        f"Preço congelado / Volume artificial detectado: volume 1h de ${volume_1h:,.2f} "
                        f"sem volatilidade real (5m: {price_change_5m}%, 1h: {price_change_1h}%, 24h: {price_change_24h}%)"
                    )
                    return False, reason, metrics

            # GATE F: MÍNIMO DE TRADERS ÚNICOS (ANTI-CARTEIRA ÚNICA)
            if volume_1h >= Decimal("10000.0") and 0 < total_traders < self.min_unique_traders_24h:
                reason = (
                    f"Volume artificial: apenas {total_traders} traders únicos para "
                    f"${volume_1h:,.2f} de volume (mínimo exigido: {self.min_unique_traders_24h})"
                )
                return False, reason, metrics

            # GATE G: ANTI-DEV DUMP / TAXA EXCESSIVA DE VENDEDORES (CASO NIKE)
            if buyers > 0 and sellers > 0 and (buyers + sellers) >= 20:
                seller_ratio = Decimal(str(sellers)) / Decimal(str(buyers))
                if seller_ratio > self.max_seller_to_buyer_ratio:
                    reason = (
                        f"Pressão extrema de despejo: {sellers} vendedores para {buyers} compradores "
                        f"(proporção {seller_ratio:.1f}x > teto {self.max_seller_to_buyer_ratio:.1f}x) - Risco de Dev Dump"
                    )
                    return False, reason, metrics

            if buys_24h > 0 and sells_24h > 0 and total_txns_24h >= 50:
                sell_txn_ratio = Decimal(str(sells_24h)) / Decimal(str(buys_24h))
                if sell_txn_ratio > Decimal("2.0"):
                    reason = (
                        f"Despejo massivo em transações: {sells_24h} vendas para {buys_24h} compras em 24h "
                        f"(proporção {sell_txn_ratio:.1f}x) - Venda em cascata"
                    )
                    return False, reason, metrics

            # GATE H: ANTI-DRENAGEM DE LIQUIDEZ (DESPROPORÇÃO LIQUIDEZ/VOLUME)
            if volume_24h >= Decimal("20000.0"):
                liq_vol_ratio = liquidity_usd / volume_24h
                if liq_vol_ratio < self.min_liquidity_to_volume_ratio:
                    reason = (
                        f"Liquidez drenada: liquidez (${liquidity_usd:,.2f}) representa apenas "
                        f"{liq_vol_ratio * Decimal('100.0'):.1f}% do volume (${volume_24h:,.2f}) - "
                        f"Piso seguro: {self.min_liquidity_to_volume_ratio * Decimal('100.0'):.1f}%"
                    )
                    return False, reason, metrics

            # GATE I: ANTI-EXAUSTÃO PARABÓLICA (BLOW-OFF TOP PROTECTION)
            if price_change_1h >= self.max_parabolic_1h_gain_pct and price_change_5m < Decimal("0.0"):
                reason = (
                    f"Exaustão parabólica pós-pump: rali de +{price_change_1h:.1f}% em 1h com "
                    f"reversão negativa em 5m ({price_change_5m:.1f}%) - Risco crítico de topo"
                )
                return False, reason, metrics

            # GATE J: ANTI-FALLING KNIFE (QUEDA ABRUPTA EM 5M)
            if price_change_5m < self.min_price_change_5m_pct:
                reason = (
                    f"Faca caindo detectada: variação de 5m ({price_change_5m:.1f}%) "
                    f"abaixo do limite de proteção ({self.min_price_change_5m_pct:.1f}%)"
                )
                return False, reason, metrics

            # GATE K: VOLUME MÍNIMO EM 1H
            if volume_1h < self.min_volume_1h_usd:
                reason = (
                    f"Volume 1h (${volume_1h:,.2f}) insuficiente "
                    f"(mínimo exigido: ${self.min_volume_1h_usd:,.2f})"
                )
                return False, reason, metrics

            # GATE L: PRESSÃO COMPRADORA MÍNIMA EM 5M
            if total_txns_5m >= 5 and buy_ratio_5m < self.min_buy_ratio_5m_pct:
                reason = (
                    f"Pressão vendedora dominante: compras representam apenas {buy_ratio_5m:.1f}% "
                    f"das transações em 5m (< {self.min_buy_ratio_5m_pct:.1f}%)"
                )
                return False, reason, metrics

        # =========================================================================
        # ELEGIBILIDADE POR ESTRATÉGIA (SCALP vs SWING)
        # =========================================================================
        eligible_scalp = bool(metrics.get("eligible_scalp", False))
        eligible_swing = bool(metrics.get("eligible_swing", False))

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
                        f"({self.max_age_hours_scalp:.1f}h / 30 dias)"
                    )
                else:
                    reason = (
                        f"Token inelegível para Scalp: liquidez (${liquidity_usd:,.2f}) "
                        f"fora dos parâmetros de segurança"
                    )
                return False, reason, metrics
            return True, None, metrics

        elif mode_upper == "SWING_ONLY":
            if not eligible_swing:
                if age_hours is not None and age_hours < self.min_age_hours_swing:
                    reason = (
                        f"Idade do token ({age_hours:.1f}h) abaixo da janela de entrada para Swing "
                        f"({self.min_age_hours_swing:.1f}h a {self.max_age_hours_swing:.1f}h) - Falta de consolidação"
                    )
                elif age_hours is not None and age_hours > self.max_age_hours_swing:
                    reason = (
                        f"Idade do token ({age_hours:.1f}h) fora da janela de entrada para Swing "
                        f"({self.min_age_hours_swing:.1f}h a {self.max_age_hours_swing:.1f}h)"
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
                elif age_hours is not None and age_hours > self.max_age_hours_scalp:
                    reason = (
                        f"Idade do token ({age_hours:.1f}h) excede o teto máximo de mercado "
                        f"({self.max_age_hours_scalp:.1f}h / 30 dias)"
                    )
                else:
                    age_str = f"{age_hours:.1f}h" if age_hours is not None else "N/D"
                    reason = (
                        f"Token inelegível para Dual-Track (Idade: {age_str}, "
                        f"Liquidez: ${liquidity_usd:,.2f})"
                    )
                return False, reason, metrics
            return True, None, metrics
