"""
Módulo Especializado de Auditoria Gráfica, Estrutura de Velas e Proteção Anti-Dump.
Verifica se o token possui histórico mínimo de 3 velas ativas, ausência de pavio de despejo
anômalo e transações recentes antes da entrada ou liberação da fila de espera.
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

import urllib.request
import json

from src.database.models import TokenMetadata
from src.utils.logger import setup_logger

logger = setup_logger("vertex.security.chart_auditor")


class ChartHealthAuditor:
    """
    Audita o histórico de velas (OHLCV) e a integridade de liquidez e transações em tempo real.
    Garante que nenhum token seja comprado sem pelo menos 3 velas de negociação orgânica
    ou enquanto estiver em queda abrupta / abandono de liquidez.
    """

    GECKO_NETWORK_MAP: dict[str, str] = {
        "solana": "solana",
        "base": "base",
        "arbitrum": "arbitrum",
        "bsc": "bsc",
        "polygon": "polygon_pos",
        "ethereum": "eth",
        "avalanche": "avax",
        "optimism": "optimism",
        "blast": "blast",
    }

    def __init__(
        self,
        geckoterminal_base_url: str = "https://api.geckoterminal.com",
        dexscreener_base_url: str = "https://api.dexscreener.com",
        min_candles_required: int = 3,
        min_volume_1h_usd: Decimal = Decimal("3000.0"),
        min_txns_5m: int = 2,
        min_txns_1h: int = 10,
        min_liquidity_usd: Decimal = Decimal("5000.0"),
        max_5m_drop_pct: Decimal = Decimal("-10.0"),
        max_1h_drop_pct: Decimal = Decimal("-18.0"),
        max_24h_drop_pct: Decimal = Decimal("-45.0"),
        timeout_seconds: float = 4.0,
    ) -> None:
        self.geckoterminal_base_url: str = geckoterminal_base_url.rstrip("/")
        self.dexscreener_base_url: str = dexscreener_base_url.rstrip("/")
        self.min_candles_required: int = min_candles_required
        self.min_volume_1h_usd: Decimal = min_volume_1h_usd
        self.min_txns_5m: int = min_txns_5m
        self.min_txns_1h: int = min_txns_1h
        self.min_liquidity_usd: Decimal = min_liquidity_usd
        self.max_5m_drop_pct: Decimal = max_5m_drop_pct
        self.max_1h_drop_pct: Decimal = max_1h_drop_pct
        self.max_24h_drop_pct: Decimal = max_24h_drop_pct
        self.timeout_seconds: float = timeout_seconds
        self._session: Any | None = None

    async def _get_session(self) -> Any:
        if HAS_AIOHTTP:
            if self._session is None or getattr(self._session, "closed", True):
                timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
                self._session = aiohttp.ClientSession(timeout=timeout)
            return self._session
        return None

    async def close(self) -> None:
        if self._session and not getattr(self._session, "closed", True):
            await self._session.close()
            self._session = None

    async def _http_get_json(self, url: str) -> dict[str, Any] | None:
        """Executa requisição GET HTTP resiliente com fallback síncrono."""
        headers = {"User-Agent": "Vertex-bot/1.0", "Accept": "application/json"}
        try:
            if HAS_AIOHTTP:
                session = await self._get_session()
                if session:
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if isinstance(data, dict):
                                return data
            else:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    raw = resp.read()
                    data = json.loads(raw.decode("utf-8"))
                    if isinstance(data, dict):
                        return data
        except Exception as exc:
            logger.debug("Falha na consulta HTTP para %s: %s", url, exc)
        return None

    async def audit_ohlcv_candles(
        self,
        chain: str,
        pool_address: str,
        timeframe: str = "minute",
        aggregate: int = 15,
        limit: int = 10,
        mock_candles: list[list[float]] | None = None,
    ) -> tuple[bool, str | None, dict[str, Any]]:
        """
        Consulta e audita o histórico de velas no GeckoTerminal.
        Rejeita tokens que não possuem histórico mínimo de negociação ou cuja
        formação represente pico e despejo imediato (Single-Spike & Dump).
        """
        if mock_candles is not None:
            ohlcv_list = mock_candles
        else:
            net_id = self.GECKO_NETWORK_MAP.get(chain.lower().strip(), chain.lower().strip())
            url = f"{self.geckoterminal_base_url}/api/v2/networks/{net_id}/pools/{pool_address}/ohlcv/{timeframe}?aggregate={aggregate}&limit={limit}"
            data = await self._http_get_json(url)
            if not data or not isinstance(data.get("data"), dict):
                return False, f"Histórico gráfico OHLCV indisponível no GeckoTerminal para o par {pool_address}", {}
            attrs = data["data"].get("attributes", {})
            raw_list = attrs.get("ohlcv_list")
            if not isinstance(raw_list, list):
                return False, f"Estrutura de velas inválida retornada para o par {pool_address}", {}
            ohlcv_list = raw_list

        candle_count = len(ohlcv_list)
        details: dict[str, Any] = {
            "candle_count": candle_count,
            "candles_analyzed": candle_count,
        }

        # 1. HARD GATE: Mínimo de 3 velas ativas fechadas
        if candle_count < self.min_candles_required:
            reason = (
                f"Histórico gráfico insuficiente: apenas {candle_count} vela(s) de negociação "
                f"(mínimo exigido: {self.min_candles_required} velas fechadas) - Risco de Fake Pump em vela única"
            )
            return False, reason, details

        # Velas são retornadas ordenadas do mais recente para o mais antigo:
        # [timestamp, open, high, low, close, volume]
        recent_candles = ohlcv_list[: self.min_candles_required]
        try:
            latest = recent_candles[0]
            latest_open = float(latest[1])
            latest_high = float(latest[2])
            latest_low = float(latest[3])
            latest_close = float(latest[4])
            latest_vol = float(latest[5])

            highest_in_window = max(float(c[2]) for c in recent_candles)
            lowest_in_window = min(float(c[3]) for c in recent_candles)
            total_vol_window = sum(float(c[5]) for c in recent_candles)

            details.update(
                {
                    "latest_close": latest_close,
                    "highest_in_window": highest_in_window,
                    "lowest_in_window": lowest_in_window,
                    "total_vol_window": total_vol_window,
                }
            )

            # 2. HARD GATE: Colapso a partir do topo das 3 velas (Queda > 45%)
            if highest_in_window > 0.0:
                drawdown_from_peak = (latest_close - highest_in_window) / highest_in_window * 100.0
                details["drawdown_from_peak_pct"] = round(drawdown_from_peak, 2)
                if drawdown_from_peak < -45.0:
                    reason = (
                        f"Colapso gráfico em andamento: cotação atual recuou {drawdown_from_peak:.1f}% "
                        f"a partir da máxima recente (${highest_in_window:,.6f}) - Faca caindo"
                    )
                    return False, reason, details

            # 3. HARD GATE: Pavio Superior Extremo de Despejo na Vela Anterior (Shooting Star Dump)
            if len(recent_candles) >= 2:
                prev = recent_candles[1]
                prev_open = float(prev[1])
                prev_high = float(prev[2])
                prev_low = float(prev[3])
                prev_close = float(prev[4])
                body = abs(prev_close - prev_open)
                upper_wick = prev_high - max(prev_open, prev_close)
                # Se o pavio superior for 3x maior que o corpo e o preço estiver fechando na mínima
                if upper_wick > 3.0 * max(body, 1e-9) and (prev_high - prev_low) > 0:
                    wick_ratio = upper_wick / (prev_high - prev_low)
                    if wick_ratio > 0.60 and latest_close < prev_open:
                        reason = (
                            f"Vela de despejo anômala detectada no histórico recente "
                            f"(pavio de rejeição de {wick_ratio * 100.0:.0f}% com perda da abertura) - Dev Dump"
                        )
                        return False, reason, details

            # 4. HARD GATE: Concentração Anômala de Volume em 1 Vela (Volume Artificial)
            if total_vol_window > 0.0:
                single_candle_vol_pct = (max(float(c[5]) for c in recent_candles) / total_vol_window) * 100.0
                if single_candle_vol_pct > 92.0 and latest_vol < (total_vol_window * 0.05):
                    reason = (
                        f"Volume artificial esgotado: {single_candle_vol_pct:.0f}% de todo o volume "
                        f"ocorreu em 1 única vela e a atividade cessou na vela atual"
                    )
                    return False, reason, details

        except (IndexError, ValueError, TypeError) as parse_err:
            logger.debug("Erro ao analisar matemática das velas: %s", parse_err)

        return True, None, details

    async def fetch_fresh_dexscreener_pair(
        self,
        chain: str,
        token_address: str,
    ) -> dict[str, Any] | None:
        """Consulta as informações mais recentes da pool na DexScreener."""
        url = f"{self.dexscreener_base_url}/latest/dex/tokens/{token_address}"
        data = await self._http_get_json(url)
        if isinstance(data, dict):
            pairs = data.get("pairs")
            if isinstance(pairs, list) and pairs:
                target_chain = chain.lower().strip()
                matched = [
                    p
                    for p in pairs
                    if isinstance(p, dict)
                    and str(p.get("chainId", "")).lower().strip() == target_chain
                ]
                if matched:
                    matched.sort(
                        key=lambda p: float(
                            p.get("liquidity", {}).get("usd", 0.0)
                            if isinstance(p.get("liquidity"), dict)
                            else 0.0
                        ),
                        reverse=True,
                    )
                    return matched[0]
                elif pairs and isinstance(pairs[0], dict):
                    return pairs[0]
        return None

    async def audit_token_pre_entry(
        self,
        token: TokenMetadata,
        fresh_pair_data: dict[str, Any] | None = None,
        bypass_candles_for_testing: bool = False,
    ) -> tuple[bool, str | None, dict[str, Any]]:
        """
        Realiza auditoria completa de último segundo antes de autorizar a compra do token.
        Revalida liquidez atual, volume, transações recentes (5m/1h) e estrutura de velas.
        """
        chain = str(token.chain or "solana").lower().strip()
        pair_data = fresh_pair_data
        if pair_data is None:
            pair_data = await self.fetch_fresh_dexscreener_pair(chain, token.address)

        if not pair_data or not isinstance(pair_data, dict):
            return False, "Pool não localizada na DexScreener no momento da pré-entrada", {}

        raw_liq = pair_data.get("liquidity")
        liq_dict: dict[str, Any] = raw_liq if isinstance(raw_liq, dict) else {}
        current_liq_usd = Decimal(str(liq_dict.get("usd") or 0.0))

        # 1. Piso de Liquidez em Tempo Real
        if current_liq_usd < self.min_liquidity_usd:
            reason = (
                f"Liquidez atual (${current_liq_usd:,.2f}) abaixo do piso de segurança "
                f"(${self.min_liquidity_usd:,.2f}) - Risco de drenagem pós-anúncio"
            )
            return False, reason, {"current_liquidity_usd": float(current_liq_usd)}

        # 2. Anti-Drenagem Relativa: Se a liquidez caiu mais de 35% em relação ao momento de catalogação
        if token.initial_liquidity_usd > Decimal("0.0"):
            liq_retention = current_liq_usd / token.initial_liquidity_usd
            if liq_retention < Decimal("0.65"):
                reason = (
                    f"Drenagem de liquidez detectada: liquidez caiu {((Decimal('1.0') - liq_retention) * Decimal('100.0')):.1f}% "
                    f"desde a catalogação (${token.initial_liquidity_usd:,.2f} -> ${current_liq_usd:,.2f})"
                )
                return False, reason, {"current_liquidity_usd": float(current_liq_usd)}

        # 3. Transações e Atividade Recente (Liveness Gate)
        raw_tx = pair_data.get("txns")
        tx_dict: dict[str, Any] = raw_tx if isinstance(raw_tx, dict) else {}
        raw_m5 = tx_dict.get("m5")
        m5_dict: dict[str, Any] = raw_m5 if isinstance(raw_m5, dict) else {}
        raw_h1 = tx_dict.get("h1")
        h1_dict: dict[str, Any] = raw_h1 if isinstance(raw_h1, dict) else {}

        buys_5m = int(m5_dict.get("buys") or 0)
        sells_5m = int(m5_dict.get("sells") or 0)
        total_5m = buys_5m + sells_5m

        buys_1h = int(h1_dict.get("buys") or 0)
        sells_1h = int(h1_dict.get("sells") or 0)
        total_1h = buys_1h + sells_1h

        if total_5m < self.min_txns_5m:
            reason = (
                f"Negociação inativa nos últimos 5 minutos: apenas {total_5m} transações "
                f"(mínimo exigido: {self.min_txns_5m}) - Token sem liquidez de negociação contínua"
            )
            return False, reason, {"total_5m": total_5m, "total_1h": total_1h}

        if total_1h < self.min_txns_1h:
            reason = (
                f"Negociação estagnada em 1h: apenas {total_1h} transações "
                f"(mínimo exigido: {self.min_txns_1h}) - Pool abandonada"
            )
            return False, reason, {"total_5m": total_5m, "total_1h": total_1h}

        # 4. Volume Mínimo de 1h
        raw_vol = pair_data.get("volume")
        vol_dict: dict[str, Any] = raw_vol if isinstance(raw_vol, dict) else {}
        volume_1h = Decimal(str(vol_dict.get("h1") or 0.0))
        if volume_1h < self.min_volume_1h_usd:
            reason = (
                f"Volume em 1h (${volume_1h:,.2f}) abaixo do mínimo exigido (${self.min_volume_1h_usd:,.2f})"
            )
            return False, reason, {"volume_1h": float(volume_1h)}

        # 5. Variação de Preço (Anti-Dump Recente)
        raw_pc = pair_data.get("priceChange")
        pc_dict: dict[str, Any] = raw_pc if isinstance(raw_pc, dict) else {}
        pc_5m = Decimal(str(pc_dict.get("m5") or 0.0))
        pc_1h = Decimal(str(pc_dict.get("h1") or 0.0))
        pc_24h = Decimal(str(pc_dict.get("h24") or 0.0))

        if pc_5m < self.max_5m_drop_pct:
            reason = f"Queda abrupta de 5m ({pc_5m:.1f}% < {self.max_5m_drop_pct:.1f}%) - Faca caindo"
            return False, reason, {"pc_5m": float(pc_5m)}

        if pc_1h < self.max_1h_drop_pct:
            reason = f"Despejo severo em 1h ({pc_1h:.1f}% < {self.max_1h_drop_pct:.1f}%)"
            return False, reason, {"pc_1h": float(pc_1h)}

        if pc_24h < self.max_24h_drop_pct:
            reason = f"Colapso estrutural em 24h ({pc_24h:.1f}% < {self.max_24h_drop_pct:.1f}%)"
            return False, reason, {"pc_24h": float(pc_24h)}

        # 6. Auditoria Gráfica de Velas (OHLCV)
        pool_addr = str(pair_data.get("pairAddress") or "").strip()
        if not bypass_candles_for_testing and pool_addr:
            is_candles_ok, candle_reason, candle_details = await self.audit_ohlcv_candles(
                chain=chain,
                pool_address=pool_addr,
            )
            if not is_candles_ok:
                return False, candle_reason, candle_details

        audit_summary: dict[str, Any] = {
            "current_liquidity_usd": float(current_liq_usd),
            "volume_1h_usd": float(volume_1h),
            "total_5m_txns": total_5m,
            "total_1h_txns": total_1h,
            "price_change_5m": float(pc_5m),
            "price_change_1h": float(pc_1h),
            "pair_address": pool_addr,
        }
        return True, None, audit_summary

