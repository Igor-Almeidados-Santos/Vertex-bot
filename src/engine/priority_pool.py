"""Gerenciador do Pool de Prioridades (PriorityPoolManager).

Mantém a lista de tokens aprovados e negociados com sucesso pelo Vertex-Bot.
Tokens consolidados e em ascensão que se mantêm saudáveis ("sem morrer")
recebem prioridade máxima na alocação de capital e em reentradas contínuas.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.database.models import TokenMetadata
from src.utils.logger import setup_logger

logger = setup_logger("vertex.engine.priority_pool")


@dataclass
class PriorityTokenRecord:
    """Representa um ativo registrado no Pool de Prioridades."""

    address: str
    symbol: str
    name: str
    chain: str
    dex: str
    tier: str  # 'CONSOLIDATED' ou 'EMERGING'
    initial_liquidity_usd: float
    current_liquidity_usd: float
    last_price: float
    highest_price_seen: float
    total_trades_count: int = 1
    successful_trades_count: int = 0
    total_realized_pnl_usd: float = 0.0
    is_active_priority: bool = True
    is_alive: bool = True
    origin_mode: str = "PAPER"  # 'LIVE', 'PAPER' ou 'BOTH'
    added_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_traded_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_evaluated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    raw_event: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PriorityTokenRecord":
        return cls(
            address=data["address"],
            symbol=data.get("symbol") or data["address"][:8],
            name=data.get("name") or "N/A",
            chain=data.get("chain", "solana"),
            dex=data.get("dex", "raydium"),
            tier=data.get("tier", "EMERGING"),
            initial_liquidity_usd=float(data.get("initial_liquidity_usd", 0.0)),
            current_liquidity_usd=float(data.get("current_liquidity_usd", data.get("initial_liquidity_usd", 0.0))),
            last_price=float(data.get("last_price", 0.0)),
            highest_price_seen=float(data.get("highest_price_seen", data.get("last_price", 0.0))),
            total_trades_count=int(data.get("total_trades_count", 1)),
            successful_trades_count=int(data.get("successful_trades_count", 0)),
            total_realized_pnl_usd=float(data.get("total_realized_pnl_usd", 0.0)),
            is_active_priority=bool(data.get("is_active_priority", True)),
            is_alive=bool(data.get("is_alive", True)),
            origin_mode=str(data.get("origin_mode") or "PAPER").upper(),
            added_at=data.get("added_at") or datetime.now(UTC).isoformat(),
            last_traded_at=data.get("last_traded_at") or datetime.now(UTC).isoformat(),
            last_evaluated_at=data.get("last_evaluated_at") or datetime.now(UTC).isoformat(),
            raw_event=dict(data.get("raw_event") or {}),
        )


class PriorityPoolManager:
    """
    Gerencia a Lista de Prioridades (Priority Pool) de tokens aprovados e negociados.
    Tokens que se mantêm ativos e com liquidez saudável têm precedência absoluta na
    abertura de posições sobre tokens novos não testados.
    """

    def __init__(
        self,
        mode: str = "paper",
        data_dir: Path | str = "data",
        min_alive_liquidity_usd: Decimal = Decimal("5000.0"),
        max_catastrophic_drop_pct: Decimal = Decimal("75.0"),
    ) -> None:
        self.mode = mode.lower().strip()
        self.data_dir = Path(data_dir)
        self.min_alive_liquidity_usd = min_alive_liquidity_usd
        self.max_catastrophic_drop_pct = max_catastrophic_drop_pct
        self._tokens: dict[str, PriorityTokenRecord] = {}

        self.storage_file = self.data_dir / self.mode / "priority_tokens.json"
        self._load_from_disk()

    def sync_cross_mode_tokens(self) -> None:
        """
        Em modo PAPER (simulação), sincroniza tokens aprovados e saudáveis do modo LIVE.
        Assegura que a simulação opere com ativos consolidados e aprovados em ambos os ambientes.
        """
        if self.mode != "paper":
            return

        live_file = self.data_dir / "live" / "priority_tokens.json"
        if not live_file.exists():
            return

        try:
            raw = json.loads(live_file.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                added_count = 0
                for item in raw:
                    if isinstance(item, dict) and "address" in item:
                        live_rec = PriorityTokenRecord.from_dict(item)
                        # Só sincroniza tokens que estiverem de acordo (ativos, vivos e com liquidez saudável >= 5000)
                        if not (live_rec.is_active_priority and live_rec.is_alive and live_rec.current_liquidity_usd >= float(self.min_alive_liquidity_usd)):
                            continue

                        addr = live_rec.address
                        if addr in self._tokens:
                            if self._tokens[addr].origin_mode == "PAPER":
                                self._tokens[addr].origin_mode = "BOTH"
                            if live_rec.current_liquidity_usd > 0:
                                self._tokens[addr].current_liquidity_usd = live_rec.current_liquidity_usd
                            if live_rec.last_price > 0:
                                self._tokens[addr].last_price = live_rec.last_price
                        else:
                            live_rec.origin_mode = "LIVE"
                            self._tokens[addr] = live_rec
                            added_count += 1
                if added_count > 0:
                    logger.info("⭐ [PRIORITY POOL] %d tokens aprovados do modo LIVE integrados à SIMULAÇÃO.", added_count)
        except Exception as exc:
            logger.debug("Falha não-bloqueante ao sincronizar tokens LIVE no PAPER: %s", exc)

    def _load_from_disk(self) -> None:
        """Carrega lista de tokens prioritários persistida do disco."""
        if not self.storage_file.exists():
            # Tenta carregar do caminho legô se existir
            legacy_file = self.data_dir / "priority_tokens.json"
            if legacy_file.exists():
                try:
                    raw = json.loads(legacy_file.read_text(encoding="utf-8"))
                    if isinstance(raw, list):
                        for item in raw:
                            if isinstance(item, dict) and "address" in item:
                                rec = PriorityTokenRecord.from_dict(item)
                                self._tokens[rec.address] = rec
                except Exception as exc:
                    logger.warning("Falha ao carregar arquivo de prioridades legado: %s", exc)
            self.sync_cross_mode_tokens()
            return

        try:
            raw = json.loads(self.storage_file.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict) and "address" in item:
                        rec = PriorityTokenRecord.from_dict(item)
                        self._tokens[rec.address] = rec
            logger.info("⭐ [PRIORITY POOL] %d tokens carregados da lista de prioridades (%s).", len(self._tokens), self.mode.upper())
        except Exception as exc:
            logger.error("Falha ao carregar priority_tokens.json (%s): %s", self.mode, exc)

        self.sync_cross_mode_tokens()

    def _save_to_disk(self) -> None:
        """Salva a lista de prioridades atomicamente no disco."""
        try:
            self.storage_file.parent.mkdir(parents=True, exist_ok=True)
            data_list = [t.to_dict() for t in self._tokens.values()]
            tmp_file = self.storage_file.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(data_list, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp_file.replace(self.storage_file)
        except Exception as exc:
            logger.error("Erro ao salvar priority_tokens.json (%s): %s", self.mode, exc)

    def register_executed_token(
        self,
        token: TokenMetadata,
        entry_price: Decimal,
        strategy_type: str = "SCALP",
    ) -> PriorityTokenRecord:
        """
        Registra um token que teve posição executada com sucesso na Lista de Prioridades.
        Se já estiver registrado, atualiza contador e dados operacionais.
        """
        addr = token.address
        now_iso = datetime.now(UTC).isoformat()

        # Determina tier a partir dos metadados de auditoria ou idade
        tier = "EMERGING"
        if isinstance(token.raw_event, dict):
            tier = str(token.raw_event.get("token_tier", "EMERGING")).upper()
            if tier not in ("CONSOLIDATED", "EMERGING"):
                age_h = token.raw_event.get("age_hours")
                if age_h is not None and float(age_h) >= 720.0:
                    tier = "CONSOLIDATED"
                else:
                    tier = "EMERGING"

        liq_float = float(token.initial_liquidity_usd)
        price_float = float(entry_price)
        current_origin = "LIVE" if self.mode == "live" else "PAPER"

        if addr in self._tokens:
            rec = self._tokens[addr]
            rec.total_trades_count += 1
            rec.last_traded_at = now_iso
            rec.last_price = price_float
            rec.highest_price_seen = max(rec.highest_price_seen, price_float)
            rec.is_active_priority = True
            rec.is_alive = True
            if rec.origin_mode != current_origin:
                rec.origin_mode = "BOTH"
            if liq_float > 0:
                rec.current_liquidity_usd = liq_float
            logger.info("⭐ [PRIORITY POOL] Token reincidente atualizado: %s (%s) | Trades: %d", rec.symbol, addr[:8], rec.total_trades_count)
            logger.info("⭐ [PRIORITY POOL] Token reincidente atualizado: %s (%s) | Trades: %d | Origem: %s", rec.symbol, addr[:8], rec.total_trades_count, rec.origin_mode)
        else:
            rec = PriorityTokenRecord(
                address=addr,
                symbol=token.symbol or addr[:8],
                name=token.name or "N/A",
                chain=token.chain,
                dex=token.dex,
                tier=tier,
                initial_liquidity_usd=liq_float,
                current_liquidity_usd=liq_float,
                last_price=price_float,
                highest_price_seen=price_float,
                total_trades_count=1,
                successful_trades_count=0,
                total_realized_pnl_usd=0.0,
                is_active_priority=True,
                is_alive=True,
                origin_mode=current_origin,
                added_at=now_iso,
                last_traded_at=now_iso,
                last_evaluated_at=now_iso,
                raw_event=token.raw_event if isinstance(token.raw_event, dict) else {},
            )
            self._tokens[addr] = rec
            logger.info("⭐ [PRIORITY POOL] Novo token adicionado à Lista de Prioridades: %s (%s) | Tier: %s", rec.symbol, addr[:8], tier)
            logger.info("⭐ [PRIORITY POOL] Novo token adicionado à Lista de Prioridades: %s (%s) | Tier: %s | Origem: %s", rec.symbol, addr[:8], tier, current_origin)

        self._save_to_disk()
        return rec

    def record_trade_result(
        self,
        token_address: str,
        realized_pnl_usd: Decimal,
        exit_price: Decimal,
        exit_reason: str,
    ) -> None:
        """Atualiza estatísticas de desempenho histórico do token na Lista de Prioridades."""
        rec = self._tokens.get(token_address)
        if not rec:
            return

        pnl_float = float(realized_pnl_usd)
        rec.total_realized_pnl_usd += pnl_float
        rec.last_price = float(exit_price)
        rec.highest_price_seen = max(rec.highest_price_seen, float(exit_price))

        if pnl_float > 0:
            rec.successful_trades_count += 1
            rec.is_active_priority = True

        self._save_to_disk()

    def update_token_health(
        self,
        token_address: str,
        current_price: Decimal,
        current_liquidity_usd: Decimal | None = None,
    ) -> bool:
        """
        Avalia se o token prioritário 'se mantém sem morrer' no mercado.
        Retorna True se continuar vivo e elegível; False se sofreu colapso terminal.
        """
        rec = self._tokens.get(token_address)
        if not rec:
            return False

        rec.last_evaluated_at = datetime.now(UTC).isoformat()
        price_float = float(current_price)
        if price_float > 0:
            rec.last_price = price_float
            rec.highest_price_seen = max(rec.highest_price_seen, price_float)

        if current_liquidity_usd is not None:
            liq_float = float(current_liquidity_usd)
            rec.current_liquidity_usd = liq_float
            if current_liquidity_usd < self.min_alive_liquidity_usd:
                rec.is_alive = False
                rec.is_active_priority = False
                logger.warning(
                    "💀 [PRIORITY POOL] Token %s drenado/morto: liquidez atual $%.2f < $%.2f.",
                    rec.symbol,
                    liq_float,
                    float(self.min_alive_liquidity_usd),
                )
                self._save_to_disk()
                return False

        # Queda catastrófica terminal (> 75% da máxima histórica do registro sem reversão)
        if rec.highest_price_seen > 0 and price_float > 0:
            drop_pct = ((rec.highest_price_seen - price_float) / rec.highest_price_seen) * 100.0
            if drop_pct >= float(self.max_catastrophic_drop_pct):
                # Se for consolidado histórico, toleramos consolidação mais profunda se liquidez estiver alta
                if rec.tier == "CONSOLIDATED" and rec.current_liquidity_usd >= 50000.0:
                    pass
                else:
                    rec.is_active_priority = False
                    logger.debug("⚠️ [PRIORITY POOL] Token %s em queda profunda (-%.1f%%). Prioridade pausada temporariamente.", rec.symbol, drop_pct)
                    self._save_to_disk()
                    return False

        rec.is_alive = True
        rec.is_active_priority = True
        return True

    def get_all_priority_tokens(self) -> list[dict[str, Any]]:
        """Retorna lista de todos os tokens no pool de prioridades ordenados por precedência de execução."""
        if self.mode == "paper":
            self.sync_cross_mode_tokens()

        def sort_key(t: PriorityTokenRecord) -> tuple[int, int, float, float]:
            # 1. Ativo e Vivo primeiro (1 para True)
            is_active = 1 if (t.is_active_priority and t.is_alive) else 0
            # 2. Consolidados têm prioridade sobre emergentes
            tier_score = 2 if t.tier == "CONSOLIDATED" else 1
            # 3. PnL histórico acumulado
            pnl = t.total_realized_pnl_usd
            # 4. Liquidez atual
            liq = t.current_liquidity_usd
            return (is_active, tier_score, pnl, liq)

        sorted_recs = sorted(self._tokens.values(), key=sort_key, reverse=True)
        return [r.to_dict() for r in sorted_recs]

    def get_active_candidates(self) -> list[PriorityTokenRecord]:
        """Retorna os registros de tokens que estão ativos e saudáveis (de acordo)."""
        if self.mode == "paper":
            self.sync_cross_mode_tokens()
        return [r for r in self._tokens.values() if r.is_active_priority and r.is_alive]

    def get_candidate_record(self, address: str) -> PriorityTokenRecord | None:
        """Obtém registro de um token específico no pool."""
        return self._tokens.get(address)

    def is_priority_token(self, address: str) -> bool:
        """Indica se o token está registrado e ativo na Lista de Prioridades."""
        rec = self._tokens.get(address)
        return bool(rec and rec.is_active_priority and rec.is_alive)

    def count(self) -> int:
        return len(self._tokens)

