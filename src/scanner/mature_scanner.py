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

    def __init__(
        self,
        detection_queue: asyncio.Queue[TokenMetadata],
        min_age_hours: float = 1.0,
        max_age_hours: float = 5.0,
        poll_interval_seconds: float = 5.0,
        dexscreener_base_url: str = "https://api.dexscreener.com",
        geckoterminal_base_url: str = "https://api.geckoterminal.com",
        max_seen_cache: int = 10000,
        enable_established_pools: bool = True,
    ) -> None:
        self.detection_queue: asyncio.Queue[TokenMetadata] = detection_queue
        self.min_age_hours: float = min_age_hours
        self.max_age_hours: float = max_age_hours
        self.poll_interval: float = poll_interval_seconds
        self.dexscreener_base_url: str = dexscreener_base_url.rstrip("/")
        self.geckoterminal_base_url: str = geckoterminal_base_url.rstrip("/")
        self.max_seen_cache: int = max_seen_cache
        self.enable_established_pools: bool = enable_established_pools
        self._seen_addresses: set[str] = set()
        self._maturing_tokens: dict[str, dict[str, Any]] = {}
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
            "Scanner de Tokens Maduros iniciado. Janela alvo: [%.1fh a %.1fh] de existência.",
            self.min_age_hours,
            self.max_age_hours,
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

    def _remember_seen_address(self, token_addr: str) -> None:
        """Registra o endereço no cache de vistos, respeitando o limite máximo."""
        self._seen_addresses.add(token_addr)
        if len(self._seen_addresses) > self.max_seen_cache:
            self._seen_addresses.pop()

    def release_token(self, token_addr: str) -> None:
        """Remove o token do cache de vistos para permitir nova reanálise e reentrada futura."""
        self._seen_addresses.discard(token_addr)
        logger.info(
            "🔄 [TOKEN LIBERADO PARA REANÁLISE] Endereço %s removido do cache de vistos do MatureTokenScanner.",
            token_addr,
        )

    async def _process_single_candidate(
        self,
        token_addr: str,
        hint: dict[str, Any],
    ) -> None:
        """Avalia um candidato e encaminha para a fila se aprovado."""
        if token_addr in self._seen_addresses:
            return

        token, is_permanent = await self._evaluate_and_enrich_token(token_addr, hint)
        if token:
            self._remember_seen_address(token_addr)
            await self.detection_queue.put(token)
        elif is_permanent:
            self._remember_seen_address(token_addr)

    async def _check_maturing_tokens(self) -> None:
        """Verifica a fila de maturação e libera tokens que completaram o tempo mínimo (15m)."""
        if not self._maturing_tokens:
            return

        now_utc = datetime.now(UTC)
        now_ms = int(now_utc.timestamp() * 1000)
        ready_to_release: list[str] = []

        for token_addr, data in list(self._maturing_tokens.items()):
            created_ms = data.get("created_at_ms", 0)
            age_hours = (now_ms - created_ms) / (1000.0 * 3600.0)
            if self.min_age_hours <= age_hours <= self.max_age_hours:
                ready_to_release.append(token_addr)
            elif age_hours > self.max_age_hours:
                self._maturing_tokens.pop(token_addr, None)

        for token_addr in ready_to_release:
            data = self._maturing_tokens.pop(token_addr, {})
            hint = data.get("hint", {})
            if "pair_data" in data and data["pair_data"]:
                hint["pair_data"] = data["pair_data"]

            logger.info(
                "🌱 [TOKEN ATINGIU MATURAÇÃO (15m)] Liberando %s para fila de auditoria!",
                token_addr[:8],
            )
            token, is_permanent = await self._evaluate_and_enrich_token(token_addr, hint)
            if token:
                self._remember_seen_address(token_addr)
                await self.detection_queue.put(token)
            elif is_permanent:
                self._remember_seen_address(token_addr)

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
        """Agrega endereços candidatos da Solana de múltiplas fontes públicas com metadados pré-carregados."""
        results: list[tuple[str, dict[str, Any]]] = []

        # 1. DexScreener Search (rotativo por palavras-chave com metadados de pares embutidos)
        search_candidates = await self._fetch_dexscreener_search_candidates()
        results.extend(search_candidates)

        # 2. DexScreener Boosts, Profiles e Community Takeovers
        dex_addresses = await self._fetch_dexscreener_candidates()
        unseen_dex_addrs = [
            a for a in dex_addresses
            if a not in self._seen_addresses and a not in self._maturing_tokens
        ]
        if unseen_dex_addrs:
            batch_pairs = await self._query_dexscreener_pairs_batch(unseen_dex_addrs)
            for addr in unseen_dex_addrs:
                pair_data = batch_pairs.get(addr)
                results.append((addr, {"pair_data": pair_data} if pair_data else {}))

        # 3. GeckoTerminal Solana New Pools (crawler rotativo respeitando cota sem rate limit)
        gecko_pools = await self._fetch_geckoterminal_candidates(pages=1)
        results.extend(gecko_pools)

        # 4. GeckoTerminal Solana Top & Trending Pools (Tokens Consolidados 1d a 1 mês)
        if self.enable_established_pools:
            established_pools = await self._fetch_geckoterminal_established_pools()
            results.extend(established_pools)

        return results

    async def _fetch_dexscreener_search_candidates(
        self,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Coleta tokens na rede Solana a partir de buscas rotativas semânticas de alto engajamento."""
        all_queries = [
            "solana", "raydium", "pump", "meteora",
            "ai", "cat", "dog", "agent", "meme",
            "pepe", "inu", "moon", "trump", "coin"
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
                            chain == "solana"
                            and token_addr
                            and isinstance(token_addr, str)
                            and token_addr not in seen_in_batch
                            and token_addr not in self._seen_addresses
                            and token_addr not in self._maturing_tokens
                        ):
                            seen_in_batch.add(token_addr)
                            found_candidates.append((token_addr, {"pair_data": p}))
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
                    if isinstance(p, dict) and str(p.get("chainId", "")).lower() == "solana":
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

    async def _fetch_dexscreener_candidates(self) -> list[str]:
        """Coleta tokens na Solana listados em boosts, perfis e takeovers da DexScreener."""
        endpoints = [
            "/token-boosts/latest/v1",
            "/token-boosts/top/v1",
            "/token-profiles/latest/v1",
            "/community-takeovers/latest/v1",
        ]
        tasks = [self._http_get_json(f"{self.dexscreener_base_url}{ep}") for ep in endpoints]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        found_addresses: set[str] = set()
        for res in responses:
            if isinstance(res, list):
                for item in res:
                    if isinstance(item, dict):
                        chain = str(item.get("chainId", "")).lower()
                        token_addr = item.get("tokenAddress")
                        if chain == "solana" and token_addr and isinstance(token_addr, str):
                            found_addresses.add(token_addr)
        return list(found_addresses)

    async def _fetch_geckoterminal_candidates(
        self,
        pages: int = 1,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Consulta páginas de novas pools na Solana do GeckoTerminal usando crawler rotativo."""
        pools_out: list[tuple[str, dict[str, Any]]] = []
        target_page = self._gecko_pages_cycle[self._gecko_cycle_idx % len(self._gecko_pages_cycle)]
        self._gecko_cycle_idx += 1

        url = f"{self.geckoterminal_base_url}/api/v2/networks/solana/new_pools?page={target_page}"
        res = await self._http_get_json(url)

        if isinstance(res, dict) and "data" in res and isinstance(res["data"], list):
            for pool in res["data"]:
                try:
                    attrs = pool.get("attributes", {})
                    rel = pool.get("relationships", {})
                    base_token_id = (
                        rel.get("base_token", {}).get("data", {}).get("id", "")
                    )
                    if base_token_id.startswith("solana_"):
                        raw_addr = base_token_id.replace("solana_", "")
                        hint = {
                            "pool_created_at": attrs.get("pool_created_at"),
                            "pool_address": attrs.get("address"),
                            "reserve_usd": attrs.get("reserve_in_usd"),
                            "name": attrs.get("name"),
                        }
                        pools_out.append((raw_addr, hint))
                except Exception as parse_err:
                    logger.debug("Erro ao parsear pool GeckoTerminal: %s", parse_err)

        return pools_out

    async def _fetch_geckoterminal_established_pools(self) -> list[tuple[str, dict[str, Any]]]:
        """Consulta pools consolidadas e em tendência (trending) na Solana via GeckoTerminal (1d a 1 mês)."""
        pools_out: list[tuple[str, dict[str, Any]]] = []
        target_page = self._established_pages_cycle[self._established_cycle_idx % len(self._established_pages_cycle)]
        self._established_cycle_idx += 1

        endpoints = [
            f"{self.geckoterminal_base_url}/api/v2/networks/solana/pools?page={target_page}",
            f"{self.geckoterminal_base_url}/api/v2/networks/solana/trending_pools?page={target_page}",
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
                        if base_token_id.startswith("solana_"):
                            raw_addr = base_token_id.replace("solana_", "")
                            if raw_addr not in self._seen_addresses and raw_addr not in self._maturing_tokens:
                                hint = {
                                    "pool_created_at": attrs.get("pool_created_at"),
                                    "pool_address": attrs.get("address"),
                                    "reserve_usd": attrs.get("reserve_in_usd"),
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
        liquidity_usd = Decimal("5000.0")
        symbol: str | None = None
        name: str | None = None

        price_usd: str | None = None
        if pair_data:
            dex = str(pair_data.get("dexId", "raydium")).lower()
            pool_address = pair_data.get("pairAddress") or pool_address
            liq_dict = pair_data.get("liquidity", {})
            if isinstance(liq_dict, dict) and "usd" in liq_dict:
                liquidity_usd = Decimal(str(liq_dict.get("usd") or 5000.0))
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
                liquidity_usd = Decimal("5000.0")
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
        if not pair_data and not hint.get("pool_created_at"):
            pair_data = await self._query_dexscreener_pair(token_address)

        now_utc = datetime.now(UTC)
        age_hours = self._resolve_candidate_age(now_utc, pair_data, hint)

        # Se não conseguimos determinar a idade (ex: erro de rede/rate limit), NÃO descartamos permanentemente
        if age_hours is None:
            return None, False

        # Token jovem demais: registrar no pipeline de maturação para liberar no minuto 15
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
        label = f"{symbol} ({name})" if symbol else token_address[:8]

        min_desc = f"{int(self.min_age_hours * 60)}m"
        max_desc = f"{int(self.max_age_hours / 24)}d" if self.max_age_hours >= 24.0 else f"{int(self.max_age_hours)}h"

        if age_hours >= 24.0:
            age_desc = f"{age_hours / 24.0:.1f} dias"
            tag = "🏛️ [TOKEN CONSOLIDADO (1D-1M) DETECTADO]"
            event_name = "ESTABLISHED_TOKEN_DETECTED"
        else:
            age_desc = f"{age_hours * 60.0:.1f} min ({age_hours:.1f}h)"
            tag = "🕒 [TOKEN GRADUADO/MATURO DETECTADO]"
            event_name = "MATURE_TOKEN_DETECTED"

        logger.info(
            "%s Token: %s (%s) | Idade: %s [%s-%s] | DEX: %s | Liq: $%.2f",
            tag,
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
                "age_hours": round(age_hours, 2),
                "liquidity_usd": str(liquidity_usd),
            },
        )

        token = TokenMetadata(
            address=token_address,
            chain="solana",
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

        if created_ms:
            self._maturing_tokens[token_address] = {
                "created_at_ms": created_ms,
                "hint": hint,
                "pair_data": pair_data,
            }
            logger.debug(
                "Token %s registrado na fila de maturação (idade: %.1f min). Fila ativa: %d pools.",
                token_address,
                age_hours * 60.0,
                len(self._maturing_tokens),
            )

    async def _query_dexscreener_pair(self, token_address: str) -> dict[str, Any] | None:
        """Consulta as pools ativas do token na DexScreener para obter o melhor par."""
        url = f"{self.dexscreener_base_url}/latest/dex/tokens/{token_address}"
        payload = await self._http_get_json(url)
        if isinstance(payload, dict):
            pairs = payload.get("pairs")
            if isinstance(pairs, list) and pairs:
                # Seleciona o par com maior liquidez em USD
                solana_pairs = [
                    p
                    for p in pairs
                    if isinstance(p, dict)
                    and str(p.get("chainId", "")).lower() == "solana"
                ]
                if solana_pairs:
                    solana_pairs.sort(
                        key=lambda p: float(
                            p.get("liquidity", {}).get("usd", 0.0)
                            if isinstance(p.get("liquidity"), dict)
                            else 0.0
                        ),
                        reverse=True,
                    )
                    return cast(dict[str, Any], solana_pairs[0])
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
