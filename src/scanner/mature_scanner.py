"""
Scanner de Tokens Maduros em Janela de Existência (1 a 5 Horas).
Filtra tokens que já superaram a volatilidade e os rug pulls dos minutos iniciais,
com liquidez consolidada e autoridades de Mint/Freeze revogadas.
"""

import asyncio
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

try:
    import aiohttp

    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

from src.database.models import TokenMetadata
from src.utils.logger import setup_logger

logger = setup_logger("vertex.scanner.mature")


class MatureTokenScanner:
    """
    Consome múltiplos feeds (DexScreener e GeckoTerminal) e filtra
    estritamente tokens que possuem entre 1 e 5 horas de existência.
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

    GECKO_PREFIX_MAP: dict[str, str] = {
        "solana_": "solana",
        "base_": "base",
        "arbitrum_": "arbitrum",
        "bsc_": "bsc",
        "polygon_pos_": "polygon",
        "polygon_": "polygon",
        "eth_": "ethereum",
        "avax_": "avalanche",
        "optimism_": "optimism",
        "blast_": "blast",
    }

    def __init__(
        self,
        detection_queue: asyncio.Queue[TokenMetadata],
        min_age_hours: float = 2.0,
        max_age_hours: float = 720.0,
        min_age_hours_swing: float = 3.0,
        max_age_hours_swing: float = 6.0,
        min_liquidity_usd: Decimal = Decimal("5000.0"),
        swing_incubator_min_liquidity_usd: Decimal = Decimal("15000.0"),
        min_liquidity_swing_usd: Decimal = Decimal("20000.0"),
        max_liquidity_usd: Decimal = Decimal("250000.0"),
        poll_interval_seconds: float = 5.0,
        dexscreener_base_url: str = "https://api.dexscreener.com",
        geckoterminal_base_url: str = "https://api.geckoterminal.com",
        max_seen_cache: int = 10000,
        enable_established_pools: bool = False,
        target_chains: tuple[str, ...] | list[str] = (
            "solana",
            "base",
            "arbitrum",
            "bsc",
            "polygon",
            "ethereum",
            "avalanche",
            "optimism",
            "blast",
        ),
    ) -> None:
        self.detection_queue: asyncio.Queue[TokenMetadata] = detection_queue
        self.min_age_hours: float = min_age_hours
        self.max_age_hours: float = max_age_hours
        self.min_age_hours_swing: float = min_age_hours_swing
        self.max_age_hours_swing: float = max_age_hours_swing
        self.min_liquidity_usd: Decimal = min_liquidity_usd
        self.swing_incubator_min_liquidity_usd: Decimal = swing_incubator_min_liquidity_usd
        self.min_liquidity_swing_usd: Decimal = min_liquidity_swing_usd
        self.max_liquidity_usd: Decimal = max_liquidity_usd
        self.poll_interval: float = poll_interval_seconds
        self.dexscreener_base_url: str = dexscreener_base_url.rstrip("/")
        self.geckoterminal_base_url: str = geckoterminal_base_url.rstrip("/")
        self.max_seen_cache: int = max_seen_cache
        self.enable_established_pools: bool = enable_established_pools
        self.target_chains: tuple[str, ...] = tuple(c.lower().strip() for c in target_chains)

        self._seen_addresses: set[str] = set()
        self._seen_scalp: set[str] = set()
        self._seen_swing: set[str] = set()
        self._permanently_rejected: set[str] = set()
        self._last_age_check: dict[str, tuple[float, float]] = {}

        self._maturing_tokens: dict[str, dict[str, Any]] = {}
        self._maturing_swing_tokens: dict[str, dict[str, Any]] = {}
        self._gecko_pages_cycle: list[int] = [1, 2, 3, 5, 7, 10]
        self._gecko_cycle_idx: int = 0
        self._established_pages_cycle: list[int] = [1, 2, 3]
        self._established_cycle_idx: int = 0
        self._search_cycle_idx: int = 0
        self.is_running: bool = False
        self._task: asyncio.Task[None] | None = None
        self._session: Any | None = None

    async def _get_session(self) -> Any:
        if HAS_AIOHTTP:
            if self._session is None or getattr(self._session, "closed", True):
                timeout = aiohttp.ClientTimeout(total=8.0)
                self._session = aiohttp.ClientSession(timeout=timeout, trust_env=True)
            return self._session
        return None

    async def start(self) -> None:
        """Inicia o loop assíncrono de varredura de tokens maduros."""
        self.is_running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "Scanner de Tokens Maduros iniciado. Janela Scalp: [%.1fh a %.1fh] | Swing: [%.1fh a %.1fh].",
            self.min_age_hours,
            self.max_age_hours,
            self.min_age_hours_swing,
            self.max_age_hours_swing,
        )

    async def stop(self) -> None:
        """Encerra o loop e libera conexões de rede."""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._session and not getattr(self._session, "closed", True):
            await self._session.close()
            self._session = None
        logger.info("Scanner de Tokens Maduros finalizado.")

    def _remember_seen_address(self, token_addr: str, stage: str | None = None) -> None:
        """Registra o endereço no cache de vistos de acordo com o estágio avaliado."""
        self._seen_addresses.add(token_addr)
        if stage == "SCALP":
            self._seen_scalp.add(token_addr)
        elif stage == "SWING":
            self._seen_swing.add(token_addr)
            self._seen_scalp.add(token_addr)
        elif stage == "PERMANENT":
            self._permanently_rejected.add(token_addr)
            self._seen_scalp.add(token_addr)
            self._seen_swing.add(token_addr)

        if len(self._seen_addresses) > self.max_seen_cache:
            discarded = self._seen_addresses.pop()
            self._seen_scalp.discard(discarded)
            self._seen_swing.discard(discarded)
            self._permanently_rejected.discard(discarded)
            self._last_age_check.pop(discarded, None)

    def release_token(self, token_addr: str) -> None:
        """Remove o token do cache de vistos e incubadoras para permitir nova reanálise e reentrada futura."""
        self._seen_addresses.discard(token_addr)
        self._seen_scalp.discard(token_addr)
        self._seen_swing.discard(token_addr)
        self._permanently_rejected.discard(token_addr)
        self._maturing_tokens.pop(token_addr, None)
        self._maturing_swing_tokens.pop(token_addr, None)
        self._last_age_check.pop(token_addr, None)
        logger.info(
            "🔄 [TOKEN LIBERADO PARA REANÁLISE] Endereço %s removido do cache de vistos do MatureTokenScanner.",
            token_addr,
        )

    def _is_candidate_needed(self, token_addr: str, now_sec: float | None = None) -> bool:
        """
        Determina se um candidato deve ser consultado e processado:
        - Rejeitado permanentemente: NÃO.
        - Já avaliado para Swing: NÃO.
        - Em incubação ativa: NÃO (será processado pelo próprio loop de incubação).
        - Nunca visto: SIM.
        - Visto para Scalp, mas pendente para Swing: SIM se já tiver decorrido tempo para atingir 2h.
        """
        if token_addr in self._permanently_rejected:
            return False
        if token_addr in self._seen_swing:
            return False
        if token_addr in self._maturing_tokens or token_addr in self._maturing_swing_tokens:
            return False
        if token_addr not in self._seen_addresses:
            return True

        # Foi visto anteriormente para Scalp e ainda não para Swing:
        if token_addr in self._seen_scalp and token_addr not in self._seen_swing:
            if now_sec is None:
                now_sec = datetime.now(UTC).timestamp()
            last_check = self._last_age_check.get(token_addr)
            if last_check:
                last_age, last_ts = last_check
                elapsed_hours = max(0.0, (now_sec - last_ts) / 3600.0)
                if (last_age + elapsed_hours) < self.min_age_hours_swing:
                    return False
            return True

        return token_addr not in self._seen_addresses

    async def _process_single_candidate(
        self,
        token_addr: str,
        hint: dict[str, Any],
    ) -> None:
        """Avalia um candidato e encaminha para a fila se aprovado."""
        if not self._is_candidate_needed(token_addr):
            return

        token, is_permanent = await self._evaluate_and_enrich_token(token_addr, hint)
        if token:
            age = float(token.raw_event.get("age_hours") or 0.0) if isinstance(token.raw_event, dict) else 0.0
            if age >= self.min_age_hours_swing:
                self._remember_seen_address(token_addr, stage="SWING")
            else:
                self._remember_seen_address(token_addr, stage="SCALP")
                if token.initial_liquidity_usd >= self.swing_incubator_min_liquidity_usd:
                    created_ms = 0
                    if isinstance(token.raw_event, dict) and isinstance(token.raw_event.get("pair_data"), dict):
                        pair_dict = token.raw_event["pair_data"]
                        if isinstance(pair_dict, dict):
                            created_ms = int(pair_dict.get("pairCreatedAt") or 0)
                    if not created_ms:
                        now_ms = int(datetime.now(UTC).timestamp() * 1000)
                        created_ms = int(now_ms - age * 3600.0 * 1000.0)
                    self._maturing_swing_tokens[token_addr] = {
                        "created_at_ms": created_ms,
                        "hint": hint,
                        "pair_data": token.raw_event.get("pair_data") if isinstance(token.raw_event, dict) else None,
                    }
                    logger.info(
                        "🔭 [INCUBADORA SWING] Token %s (Liq $%.0f, Idade %.1fh) monitorado para promoção aos %.1fh.",
                        token.symbol or token_addr[:8],
                        token.initial_liquidity_usd,
                        age,
                        self.min_age_hours_swing,
                    )
            await self.detection_queue.put(token)
        elif is_permanent:
            self._remember_seen_address(token_addr, stage="PERMANENT")

    async def _check_maturing_tokens(self) -> None:
        """Verifica as filas de maturação (Scalp e Swing) e libera tokens quando completam a idade mínima."""
        if not self._maturing_tokens and not self._maturing_swing_tokens:
            return

        now_utc = datetime.now(UTC)
        now_ms = int(now_utc.timestamp() * 1000)

        # Log de status periódico da incubadora (a cada 60s)
        if not hasattr(self, "_last_incubator_log_ts"):
            self._last_incubator_log_ts = 0.0
        now_ts = now_utc.timestamp()
        if now_ts - self._last_incubator_log_ts >= 60.0 and (self._maturing_tokens or self._maturing_swing_tokens):
            self._last_incubator_log_ts = now_ts
            logger.info(
                "🍼 [INCUBADORA ATIVA] %d tokens aguardando %.0f min para liberação (Scalp) | %d monitorados para Swing (%.1fh).",
                len(self._maturing_tokens),
                self.min_age_hours * 60.0,
                len(self._maturing_swing_tokens),
                self.min_age_hours_swing,
            )

        # 1. Maturação Scalp (< min_age_hours -> min_age_hours, ex: 15m a 30m)
        ready_scalp: list[str] = []
        for token_addr, data in list(self._maturing_tokens.items()):
            created_ms = data.get("created_at_ms", 0)
            age_hours = (now_ms - created_ms) / (1000.0 * 3600.0)
            if self.min_age_hours <= age_hours <= self.max_age_hours:
                ready_scalp.append(token_addr)
            elif age_hours > self.max_age_hours:
                self._maturing_tokens.pop(token_addr, None)

        for token_addr in ready_scalp:
            data = self._maturing_tokens.pop(token_addr, {})
            hint = data.get("hint", {})
            if "pair_data" in data and data["pair_data"]:
                hint["pair_data"] = data["pair_data"]

            logger.info(
                "🌱 [TOKEN ATINGIU MATURAÇÃO (%.0fm)] Liberando %s para fila de auditoria!",
                self.min_age_hours * 60.0,
                token_addr[:8],
            )
            token, is_permanent = await self._evaluate_and_enrich_token(token_addr, hint)
            if token:
                if token.initial_liquidity_usd >= self.swing_incubator_min_liquidity_usd:
                    self._maturing_swing_tokens[token_addr] = {
                        "created_at_ms": data.get("created_at_ms", now_ms - int(self.min_age_hours * 3600 * 1000)),
                        "hint": hint,
                        "pair_data": token.raw_event.get("pair_data") if isinstance(token.raw_event, dict) else None,
                    }
                    logger.info(
                        "🔭 [INCUBADORA SWING] Token %s (Liq $%.0f) adicionado para promoção aos %.1fh.",
                        token.symbol or token_addr[:8],
                        token.initial_liquidity_usd,
                        self.min_age_hours_swing,
                    )
                self._remember_seen_address(token_addr, stage="SCALP")
                await self.detection_queue.put(token)
            elif is_permanent:
                self._remember_seen_address(token_addr, stage="PERMANENT")

        # 2. Maturação Swing (min_age_hours_swing, ex: 2.0h a 6.0h)
        ready_swing: list[str] = []
        for token_addr, data in list(self._maturing_swing_tokens.items()):
            created_ms = data.get("created_at_ms", 0)
            age_hours = (now_ms - created_ms) / (1000.0 * 3600.0)
            if self.min_age_hours_swing <= age_hours <= self.max_age_hours_swing:
                ready_swing.append(token_addr)
            elif age_hours > self.max_age_hours_swing:
                self._maturing_swing_tokens.pop(token_addr, None)

        for token_addr in ready_swing:
            data = self._maturing_swing_tokens.pop(token_addr, {})
            hint = data.get("hint", {})
            pair_data = await self._query_dexscreener_pair(token_addr)
            if pair_data:
                hint["pair_data"] = pair_data
            elif "pair_data" in data and data["pair_data"]:
                hint["pair_data"] = data["pair_data"]

            token, is_permanent = await self._evaluate_and_enrich_token(token_addr, hint)
            if token:
                logger.info(
                    "🎯 [PROMOÇÃO PARA SWING (%.1fh+)] Token %s promovido para fila de auditoria de Swing! (Liq: $%.0f)",
                    self.min_age_hours_swing,
                    token.symbol or token_addr[:8],
                    token.initial_liquidity_usd,
                )
                self._remember_seen_address(token_addr, stage="SWING")
                await self.detection_queue.put(token)
            elif is_permanent:
                self._remember_seen_address(token_addr, stage="PERMANENT")

    async def _poll_loop(self) -> None:
        """Loop contínuo consultando feeds da DexScreener e GeckoTerminal com pipeline de maturação."""
        while self.is_running:
            try:
                # 1. Libera tokens em maturação que completaram a idade mínima
                await self._check_maturing_tokens()

                # 2. Coleta novos candidatos de múltiplas fontes
                candidates = await self._gather_candidates()
                for token_addr, hint in candidates:
                    if not self.is_running:
                        break
                    await self._process_single_candidate(token_addr, hint)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Falha transitória na varredura de tokens maduros: %s", exc)

            await asyncio.sleep(self.poll_interval)

    async def _gather_candidates(self) -> list[tuple[str, dict[str, Any]]]:
        """Agrega endereços candidatos de múltiplas fontes públicas com metadados pré-carregados nas chains ativas."""
        results: list[tuple[str, dict[str, Any]]] = []

        # 1. DexScreener Search (rotativo por palavras-chave com metadados de pares embutidos)
        search_candidates = await self._fetch_dexscreener_search_candidates()
        results.extend(search_candidates)

        # 2. DexScreener Boosts, Profiles e Community Takeovers
        dex_tokens = await self._fetch_dexscreener_candidates()
        unseen_dex_tokens = [
            (addr, ch) for addr, ch in dex_tokens
            if self._is_candidate_needed(addr)
        ]
        if unseen_dex_tokens:
            unseen_addrs = [addr for addr, _ in unseen_dex_tokens]
            batch_pairs = await self._query_dexscreener_pairs_batch(unseen_addrs)
            for addr, ch in unseen_dex_tokens:
                pair_data = batch_pairs.get(addr)
                hint_dict: dict[str, Any] = {"chain": ch}
                if pair_data:
                    hint_dict["pair_data"] = pair_data
                results.append((addr, hint_dict))

        # 3. GeckoTerminal New Pools (crawler rotativo respeitando cota sem rate limit)
        gecko_pools = await self._fetch_geckoterminal_candidates(pages=1)
        results.extend(gecko_pools)

        # 4. GeckoTerminal Top & Trending Pools (Tokens Consolidados 1d a 1 mês e Trending 1h/6h)
        if self.enable_established_pools:
            established_pools = await self._fetch_geckoterminal_established_pools()
            results.extend(established_pools)

        return results

    async def _fetch_dexscreener_search_candidates(
        self,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Coleta tokens a partir de buscas rotativas semânticas de alto engajamento nas redes ativas."""
        all_queries = [
            "solana", "base", "raydium", "aerodrome", "pump", "meteora",
            "ai", "cat", "dog", "agent", "meme",
            "pepe", "inu", "moon", "trump", "coin"
            "solana", "base", "arbitrum", "bsc", "polygon", "ethereum", "avalanche", "optimism", "blast",
            "raydium", "aerodrome", "pancake", "camelot", "uniswap", "quickswap", "traderjoe",
            "ai", "cat", "dog", "agent", "meme", "pepe", "inu", "moon", "trump", "coin"
        ]
        idx = (self._search_cycle_idx * 3) % len(all_queries)
        self._search_cycle_idx += 1
        queries = [all_queries[(idx + i) % len(all_queries)] for i in range(3)]

        tasks = [
            self._http_get_json(f"{self.dexscreener_base_url}/latest/dex/search?q={q}")
            for q in queries
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        found_candidates: list[tuple[str, dict[str, Any]]] = []
        seen_in_batch: set[str] = set()
        for res in responses:
            if isinstance(res, dict) and "pairs" in res and isinstance(res["pairs"], list):
                for p in res["pairs"]:
                    if isinstance(p, dict):
                        chain = str(p.get("chainId", "")).lower()
                        base_token = p.get("baseToken", {})
                        token_addr = (
                            base_token.get("address")
                            if isinstance(base_token, dict)
                            else None
                        )
                        if (
                            chain in self.target_chains
                            and token_addr
                            and isinstance(token_addr, str)
                            and token_addr not in seen_in_batch
                            and self._is_candidate_needed(token_addr)
                        ):
                            raw_dex = str(p.get("dexId", "")).lower()
                            if raw_dex in ("pumpfun", "pump"):
                                continue
                            raw_liq = p.get("liquidity")
                            liq_dict = raw_liq if isinstance(raw_liq, dict) else {}
                            liq_usd = float(liq_dict.get("usd") or 0.0)
                            if liq_usd < float(self.min_liquidity_usd) or liq_usd > float(self.max_liquidity_usd):
                                continue
                            seen_in_batch.add(token_addr)
                            found_candidates.append((token_addr, {"pair_data": p, "chain": chain}))
        return found_candidates

    async def _query_dexscreener_pairs_batch(
        self,
        token_addresses: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Consulta pools ativas de múltiplos tokens na DexScreener em lote (até 30 por requisição)."""
        if not token_addresses:
            return {}

        results: dict[str, dict[str, Any]] = {}
        chunk_size = 30
        for i in range(0, len(token_addresses), chunk_size):
            chunk = token_addresses[i : i + chunk_size]
            addrs_str = ",".join(chunk)
            url = f"{self.dexscreener_base_url}/latest/dex/tokens/{addrs_str}"
            payload = await self._http_get_json(url)
            if isinstance(payload, dict) and "pairs" in payload and isinstance(payload["pairs"], list):
                for p in payload["pairs"]:
                    if isinstance(p, dict) and str(p.get("chainId", "")).lower() in self.target_chains:
                        base_token = p.get("baseToken", {})
                        addr = base_token.get("address") if isinstance(base_token, dict) else None
                        if addr and isinstance(addr, str):
                            curr_best = results.get(addr)
                            new_liq = float(
                                p.get("liquidity", {}).get("usd", 0.0)
                                if isinstance(p.get("liquidity"), dict)
                                else 0.0
                            )
                            curr_liq = (
                                float(
                                    curr_best.get("liquidity", {}).get("usd", 0.0)
                                    if isinstance(curr_best.get("liquidity"), dict)
                                    else 0.0
                                )
                                if curr_best
                                else -1.0
                            )
                            if new_liq > curr_liq:
                                results[addr] = p
            if i + chunk_size < len(token_addresses):
                await asyncio.sleep(0.3)
        return results

    async def _fetch_dexscreener_candidates(self) -> list[tuple[str, str]]:
        """Coleta tokens listados em boosts, perfis e takeovers da DexScreener para as redes ativas."""
        endpoints = [
            "/token-boosts/latest/v1",
            "/token-boosts/top/v1",
            "/token-profiles/latest/v1",
            "/community-takeovers/latest/v1",
        ]
        tasks = [self._http_get_json(f"{self.dexscreener_base_url}{ep}") for ep in endpoints]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        found_tokens: list[tuple[str, str]] = []
        seen_in_call: set[str] = set()
        for res in responses:
            if isinstance(res, list):
                for item in res:
                    if isinstance(item, dict):
                        chain = str(item.get("chainId", "")).lower()
                        token_addr = item.get("tokenAddress")
                        if (
                            chain in self.target_chains
                            and token_addr
                            and isinstance(token_addr, str)
                            and token_addr not in seen_in_call
                        ):
                            seen_in_call.add(token_addr)
                            found_tokens.append((token_addr, chain))
        return found_tokens

    async def _fetch_geckoterminal_candidates(
        self,
        pages: int = 1,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Consulta páginas de novas pools no GeckoTerminal usando crawler rotativo entre redes ativas."""
        pools_out: list[tuple[str, dict[str, Any]]] = []
        gecko_nets = [c for c in self.target_chains if c in ("solana", "base", "arbitrum", "bsc")]
        gecko_nets = [
            self.GECKO_NETWORK_MAP.get(c, c)
            for c in self.target_chains
            if c in self.GECKO_NETWORK_MAP or c in self.GECKO_NETWORK_MAP.values()
        ]
        if not gecko_nets:
            gecko_nets = ["solana"]
        target_network = gecko_nets[self._gecko_cycle_idx % len(gecko_nets)]
        target_page = self._gecko_pages_cycle[(self._gecko_cycle_idx // len(gecko_nets)) % len(self._gecko_pages_cycle)]
        self._gecko_cycle_idx += 1

        url = f"{self.geckoterminal_base_url}/api/v2/networks/{target_network}/new_pools?page={target_page}"
        res = await self._http_get_json(url)

        if isinstance(res, dict) and "data" in res and isinstance(res["data"], list):
            for pool in res["data"]:
                try:
                    attrs = pool.get("attributes", {})
                    rel = pool.get("relationships", {})
                    base_token_id = (
                        rel.get("base_token", {}).get("data", {}).get("id", "")
                    )
                    detected_net: str | None = None
                    raw_addr: str = ""
                    for prefix, net_name in self.GECKO_PREFIX_MAP.items():
                        if base_token_id.startswith(prefix):
                            raw_addr = base_token_id[len(prefix):]
                            detected_net = net_name
                            break

                    if not detected_net or not raw_addr:
                        prefix = f"{target_network}_"
                        if base_token_id.startswith(prefix):
                            raw_addr = base_token_id[len(prefix):]
                            rev_map = {v: k for k, v in self.GECKO_NETWORK_MAP.items()}
                            detected_net = rev_map.get(target_network, target_network)
                        else:
                            continue

                    if self._is_candidate_needed(raw_addr):
                        reserve_usd = attrs.get("reserve_in_usd")
                        if reserve_usd is not None:
                            try:
                                if float(reserve_usd) < float(self.min_liquidity_usd):
                                    continue
                            except (ValueError, TypeError):
                                pass
                        hint = {
                            "chain": detected_net,
                            "pool_created_at": attrs.get("pool_created_at"),
                            "pool_address": attrs.get("address"),
                            "reserve_usd": reserve_usd,
                            "name": attrs.get("name"),
                        }
                        pools_out.append((raw_addr, hint))
                except Exception as parse_err:
                    logger.debug("Erro ao parsear pool GeckoTerminal: %s", parse_err)

        return pools_out

    async def _fetch_geckoterminal_established_pools(self) -> list[tuple[str, dict[str, Any]]]:
        """Consulta pools consolidadas e em tendência via GeckoTerminal rotacionando durações e redes."""
        pools_out: list[tuple[str, dict[str, Any]]] = []
        gecko_nets = [c for c in self.target_chains if c in ("solana", "base", "arbitrum", "bsc")]
        gecko_nets = [
            self.GECKO_NETWORK_MAP.get(c, c)
            for c in self.target_chains
            if c in self.GECKO_NETWORK_MAP or c in self.GECKO_NETWORK_MAP.values()
        ]
        if not gecko_nets:
            gecko_nets = ["solana"]
        target_network = gecko_nets[self._established_cycle_idx % len(gecko_nets)]
        target_page = self._established_pages_cycle[(self._established_cycle_idx // len(gecko_nets)) % len(self._established_pages_cycle)]
        self._established_cycle_idx += 1

        durations = ["1h", "6h", "24h"]
        duration = durations[self._established_cycle_idx % len(durations)]

        endpoints = [
            f"{self.geckoterminal_base_url}/api/v2/networks/{target_network}/trending_pools?duration={duration}&page={target_page}",
            f"{self.geckoterminal_base_url}/api/v2/networks/{target_network}/pools?page={target_page}",
        ]
        tasks = [self._http_get_json(url) for url in endpoints]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        for res in responses:
            if isinstance(res, dict) and "data" in res and isinstance(res["data"], list):
                for pool in res["data"]:
                    try:
                        attrs = pool.get("attributes", {})
                        rel = pool.get("relationships", {})
                        base_token_id = (
                            rel.get("base_token", {}).get("data", {}).get("id", "")
                        )
                        detected_net: str | None = None
                        raw_addr: str = ""
                        for prefix, net_name in self.GECKO_PREFIX_MAP.items():
                            if base_token_id.startswith(prefix):
                                raw_addr = base_token_id[len(prefix):]
                                detected_net = net_name
                                break

                        if not detected_net or not raw_addr:
                            prefix = f"{target_network}_"
                            if base_token_id.startswith(prefix):
                                raw_addr = base_token_id[len(prefix):]
                                rev_map = {v: k for k, v in self.GECKO_NETWORK_MAP.items()}
                                detected_net = rev_map.get(target_network, target_network)
                            else:
                                continue

                        if self._is_candidate_needed(raw_addr):
                            reserve_usd = attrs.get("reserve_in_usd")
                            if reserve_usd is not None:
                                try:
                                    if float(reserve_usd) < float(self.min_liquidity_usd):
                                        continue
                                except (ValueError, TypeError):
                                    pass
                            hint = {
                                "chain": detected_net,
                                "pool_created_at": attrs.get("pool_created_at"),
                                "pool_address": attrs.get("address"),
                                "reserve_usd": reserve_usd,
                                "name": attrs.get("name"),
                            }
                            pools_out.append((raw_addr, hint))
                    except Exception as parse_err:
                        logger.debug("Erro ao parsear pool consolidada GeckoTerminal: %s", parse_err)

        return pools_out


    def _resolve_candidate_age(
        self,
        now_utc: datetime,
        pair_data: dict[str, Any] | None,
        hint: dict[str, Any],
    ) -> float | None:
        """Calcula a idade do par a partir dos metadados disponíveis da DexScreener ou GeckoTerminal."""
        pair_created_at_ms: int | None = None
        if pair_data:
            pair_created_at_ms = pair_data.get("pairCreatedAt")

        if pair_created_at_ms is not None:
            return (int(now_utc.timestamp() * 1000) - pair_created_at_ms) / (1000.0 * 3600.0)

        if hint.get("pool_created_at"):
            return self.calculate_age_from_iso(str(hint["pool_created_at"]), now_utc)

        return None

    def _extract_pool_data(
        self,
        pair_data: dict[str, Any] | None,
        hint: dict[str, Any],
    ) -> tuple[str, str | None, Decimal, str | None, str | None, str | None]:
        """Extrai dex, pool_address, liquidez em USD, symbol, name e price_usd dos metadados."""
        dex = "raydium"
        pool_address = hint.get("pool_address")
        liquidity_usd = Decimal("0.0")
        symbol: str | None = None
        name: str | None = None

        price_usd: str | None = None
        if pair_data:
            dex = str(pair_data.get("dexId", "raydium")).lower()
            pool_address = pair_data.get("pairAddress") or pool_address
            liq_dict = pair_data.get("liquidity", {})
            if isinstance(liq_dict, dict) and "usd" in liq_dict and liq_dict.get("usd") is not None:
                liquidity_usd = Decimal(str(liq_dict.get("usd") or 0.0))
            symbol = pair_data.get("baseToken", {}).get("symbol")
            name = pair_data.get("baseToken", {}).get("name")
            if pair_data.get("priceUsd"):
                price_usd = str(pair_data["priceUsd"])
            elif pair_data.get("priceNative"):
                price_usd = str(pair_data["priceNative"])
        elif hint.get("reserve_usd"):
            try:
                liquidity_usd = Decimal(str(hint["reserve_usd"]))
            except Exception:
                liquidity_usd = Decimal("0.0")
            if hint.get("base_token_price_usd"):
                price_usd = str(hint["base_token_price_usd"])
            elif hint.get("price_usd"):
                price_usd = str(hint["price_usd"])

        return dex, pool_address, liquidity_usd, symbol, name, price_usd

    async def _evaluate_and_enrich_token(
        self,
        token_address: str,
        hint: dict[str, Any],
    ) -> tuple[TokenMetadata | None, bool]:
        """
        Calcula a idade exata e aplica a validação estrita da janela [min_age_hours, max_age_hours].
        Reutiliza pair_data pré-carregado no lote para evitar sobrecarga de rede.
        Retorna (TokenMetadata | None, is_permanent_rejection: bool).
        """
        pair_data = hint.get("pair_data")
        if not pair_data:
            pair_data = await self._query_dexscreener_pair(token_address)

        now_utc = datetime.now(UTC)
        age_hours = self._resolve_candidate_age(now_utc, pair_data, hint)

        if age_hours is not None:
            self._last_age_check[token_address] = (age_hours, now_utc.timestamp())

        # Se não conseguimos determinar a idade (ex: erro de rede/rate limit), NÃO descartamos permanentemente
        if age_hours is None:
            return None, False

        # Token jovem demais: registrar no pipeline de maturação para liberar no minuto 15/30
        if age_hours < self.min_age_hours:
            self._register_maturing_candidate(token_address, age_hours, pair_data, hint)
            return None, False

        # Token muito antigo: ultrapassou o teto da janela, descartar permanentemente
        if age_hours > self.max_age_hours:
            logger.debug(
                "Token %s expirado: %.2fh > %.1fh. Descartado permanentemente.",
                token_address,
                age_hours,
                self.max_age_hours,
            )
            return None, True

        dex, pool_address, liquidity_usd, symbol, name, price_usd = self._extract_pool_data(pair_data, hint)

        # Descarta bonding curves do Pump.fun que nunca graduaram para pool AMM
        if dex in ("pumpfun", "pump"):
            logger.debug(
                "Token %s descartado: bonding curve Pump.fun sem graduação para DEX AMM.",
                token_address,
            )
            return None, True

        # Identificação de Chain
        detected_chain = "solana"
        if pair_data and pair_data.get("chainId"):
            detected_chain = str(pair_data["chainId"]).lower()
        elif hint.get("chain"):
            detected_chain = str(hint["chain"]).lower()

        # Pré-filtro anti-impersonation: descarta clones falsos de ativos nativos
        OFFICIAL_NATIVE: dict[str, dict[str, str | tuple[str, ...]]] = {
            "SOL": {"solana": "So11111111111111111111111111111111111111112"},
            "WSOL": {"solana": "So11111111111111111111111111111111111111112"},
            "ETH": {
                "ethereum": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
                "base": "0x4200000000000000000000000000000000000006",
                "arbitrum": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
                "optimism": "0x4200000000000000000000000000000000000006",
                "blast": "0x4300000000000000000000000000000000000004",
                "polygon": "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619",
                "bsc": "0x2170ed0880ac9a755fd29b2688956bd959f933f8",
            },
            "WETH": {
                "ethereum": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
                "base": "0x4200000000000000000000000000000000000006",
                "arbitrum": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
                "optimism": "0x4200000000000000000000000000000000000006",
                "blast": "0x4300000000000000000000000000000000000004",
                "polygon": "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619",
                "bsc": "0x2170ed0880ac9a755fd29b2688956bd959f933f8",
            },
            "BNB": {"bsc": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"},
            "WBNB": {"bsc": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"},
            "MATIC": {
                "polygon": (
                    "0x0000000000000000000000000000000000001010",
                    "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270",
                )
            },
            "WMATIC": {"polygon": "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270"},
            "POL": {"polygon": "0x455e53cbb86018ac2b8092fdcd39d8444affc3f6"},
            "AVAX": {"avalanche": "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7"},
            "WAVAX": {"avalanche": "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7"},
            "USDC": {
                "solana": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "base": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
                "ethereum": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                "arbitrum": (
                    "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
                    "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8",
                ),
                "bsc": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
                "polygon": (
                    "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
                    "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",
                ),
                "avalanche": (
                    "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e",
                    "0xa7d7079b0fead91f3e65f86e8915cb59c1a4c664",
                ),
                "optimism": "0x0b2c639c533813f4aa9d7837caf62653d097ff85",
            },
            "USDT": {
                "solana": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
                "base": "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2",
                "ethereum": "0xdac17f958d2ee523a2206206994597c13d831ec7",
                "arbitrum": "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
                "bsc": "0x55d398326f99059ff775485246999027b3197955",
                "polygon": "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
                "avalanche": "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7",
                "optimism": "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58",
            },
        }
        sym_clean = str(symbol).upper().strip()
        if sym_clean in OFFICIAL_NATIVE:
            official_map = OFFICIAL_NATIVE[sym_clean]
            if detected_chain in official_map:
                expected_raw = official_map[detected_chain]
                expected_addrs = (
                    [expected_raw.lower()]
                    if isinstance(expected_raw, str)
                    else [a.lower() for a in expected_raw]
                )
                if token_address.lower() not in expected_addrs:
                    logger.warning(
                        "🚨 [ANTI-IMPERSONATION] Token %s descartado na chain %s: Impersonação fraudulenta de %s.",
                        token_address,
                        detected_chain,
                        sym_clean,
                    )
                    return None, True

        # Pré-filtro de liquidez mínima e máxima (anti-fake CLMM e pools sem liquidez):
        if liquidity_usd < self.min_liquidity_usd or liquidity_usd > self.max_liquidity_usd:
            logger.debug(
                "Token %s descartado no pré-filtro: liquidez ($%.2f) fora da faixa segura [$%.2f, $%.2f].",
                token_address,
                liquidity_usd,
                self.min_liquidity_usd,
                self.max_liquidity_usd,
            )
            return None, True

        label = f"{symbol} ({name})" if symbol else token_address[:8]

        min_desc = f"{int(self.min_age_hours * 60)}m"
        max_desc = f"{int(self.max_age_hours / 24)}d" if self.max_age_hours >= 24.0 else f"{int(self.max_age_hours)}h"

        if self.min_age_hours_swing <= age_hours <= self.max_age_hours_swing:
            age_desc = f"{age_hours:.2f}h"
            tag = "🎯 [TOKEN SWING (2H-6H) DETECTADO]"
            event_name = "SWING_TOKEN_DETECTED"
        elif age_hours >= 24.0:
            age_desc = f"{age_hours / 24.0:.1f} dias"
            tag = "🏛️ [TOKEN CONSOLIDADO (1D-1M) DETECTADO]"
            event_name = "ESTABLISHED_TOKEN_DETECTED"
        else:
            age_desc = f"{age_hours * 60.0:.1f} min ({age_hours:.1f}h)"
            tag = "🕒 [TOKEN GRADUADO/MATURO DETECTADO]"
            event_name = "MATURE_TOKEN_DETECTED"

        logger.info(
            "%s Token [%s]: %s (%s) | Idade: %s [%s-%s] | DEX: %s | Liq: $%.2f",
            tag,
            detected_chain.upper(),
            label,
            token_address,
            age_desc,
            min_desc,
            max_desc,
            dex,
            liquidity_usd,
            extra={
                "event": event_name,
                "token_address": token_address,
                "chain": detected_chain,
                "age_hours": round(age_hours, 2),
                "liquidity_usd": str(liquidity_usd),
            },
        )

        token = TokenMetadata(
            address=token_address,
            chain=detected_chain,
            dex=dex,
            pool_address=pool_address,
            initial_liquidity_usd=liquidity_usd,
            symbol=symbol,
            name=name,
            detection_timestamp=now_utc,
            raw_event={
                "priceUsd": price_usd,
                "age_hours": age_hours,
                "pair_data": pair_data,
                "hint": hint,
            },
        )
        return token, True

    def _register_maturing_candidate(
        self,
        token_address: str,
        age_hours: float,
        pair_data: dict[str, Any] | None,
        hint: dict[str, Any],
    ) -> None:
        """Registra token na fila de maturação se criado recentemente (< min_age_hours)."""
        created_ms: int | None = None
        if pair_data and pair_data.get("pairCreatedAt"):
            try:
                created_ms = int(pair_data["pairCreatedAt"])
            except Exception:
                pass
        elif hint.get("pool_created_at"):
            try:
                clean_ts = str(hint["pool_created_at"]).replace("Z", "+00:00")
                dt = datetime.fromisoformat(clean_ts)
                created_ms = int(dt.timestamp() * 1000)
            except Exception:
                pass

        if created_ms is None:
            created_ms = int(datetime.now(UTC).timestamp() * 1000)

        is_new = token_address not in self._maturing_tokens
        self._maturing_tokens[token_address] = {
            "created_at_ms": created_ms,
            "hint": hint,
            "pair_data": pair_data,
        }

        if is_new:
            sym: str | None = None
            if pair_data and isinstance(pair_data, dict):
                sym = pair_data.get("baseToken", {}).get("symbol")
            elif hint and isinstance(hint, dict):
                sym = hint.get("symbol") or hint.get("name")
            label = f"{sym} ({token_address[:8]}...)" if sym else f"{token_address[:8]}..."
            logger.debug(
                "⏳ [INCUBADORA ANTI-DUMP] Token %s detectado com %.1f min de vida. Armazenado na incubadora (liberação aos %.0f min). Total incubados: %d",
                label,
                age_hours * 60.0,
                self.min_age_hours * 60.0,
                len(self._maturing_tokens),
            )
        else:
            logger.debug(
                "Token %s atualizado na fila de maturação (idade: %.1f min). Fila ativa: %d pools.",
                token_address,
                age_hours * 60.0,
                len(self._maturing_tokens),
            )

    async def _query_dexscreener_pair(
        self,
        token_address: str,
        chain: str | None = None,
    ) -> dict[str, Any] | None:
        """Consulta as pools ativas do token na DexScreener para obter o melhor par."""
        url = f"{self.dexscreener_base_url}/latest/dex/tokens/{token_address}"
        payload = await self._http_get_json(url)
        if isinstance(payload, dict):
            pairs = payload.get("pairs")
            if isinstance(pairs, list) and pairs:
                # Seleciona o par com maior liquidez em USD respeitando a(s) chain(s) alvo
                matched_pairs = [
                    p
                    for p in pairs
                    if isinstance(p, dict)
                    and (
                        str(p.get("chainId", "")).lower() == chain.lower()
                        if chain
                        else str(p.get("chainId", "")).lower() in self.target_chains
                    )
                ]
                if matched_pairs:
                    matched_pairs.sort(
                        key=lambda p: float(
                            p.get("liquidity", {}).get("usd", 0.0)
                            if isinstance(p.get("liquidity"), dict)
                            else 0.0
                        ),
                        reverse=True,
                    )
                    return cast(dict[str, Any], matched_pairs[0])
        return None

    def _sync_http_get_json(self, url: str, headers: dict[str, str]) -> Any:
        """Executa requisição GET síncrona segura em worker thread."""
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            data = resp.read()
            return json.loads(data.decode("utf-8"))

    async def _http_get_json(self, url: str) -> Any:
        """Realiza requisição GET HTTP com headers e timeout adequados."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Vertex-bot Quantitative Engine/1.0)",
            "Accept": "application/json",
        }
        try:
            if HAS_AIOHTTP:
                session = await self._get_session()
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429:
                        logger.debug("Rate limit recebido em %s. Aguardando recuo.", url)
                    return None
            else:
                return await asyncio.to_thread(self._sync_http_get_json, url, headers)
        except Exception as exc:
            logger.debug("Falha na requisição HTTP a %s: %s", url, exc)
            return None

    @staticmethod
    def calculate_age_from_ms(pair_created_at_ms: int, now_ms: int) -> float:
        """Calcula a idade em horas a partir de timestamps em milissegundos."""
        diff_ms = max(0, now_ms - pair_created_at_ms)
        return float(diff_ms) / (1000.0 * 3600.0)

    @staticmethod
    def calculate_age_from_iso(iso_timestamp: str, now: datetime) -> float | None:
        """Calcula a idade em horas a partir de string ISO 8601 UTC."""
        try:
            clean_ts = iso_timestamp.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_ts)
            diff_sec = max(0.0, (now - dt).total_seconds())
            return diff_sec / 3600.0
        except Exception:
            return None
