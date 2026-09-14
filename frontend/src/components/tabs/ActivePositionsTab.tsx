"use client";

import { useState } from "react";
import {
  ExternalLink,
  Shield,
  TrendingUp,
  Clock,
  Layers,
  AlertTriangle,
} from "lucide-react";
import { PositionItem } from "@/types/bot";
import { formatUSD, formatPct, shortenAddress, formatTimeAgo } from "@/lib/formatters";

interface ActivePositionsTabProps {
  positions: PositionItem[];
}

export function ActivePositionsTab({ positions }: ActivePositionsTabProps) {
  const activePositions = positions.filter((p) => p.status === "OPEN");
  const [filterStrategy, setFilterStrategy] = useState<"ALL" | "SCALP" | "SWING">("ALL");

  const filtered = activePositions.filter((p) => {
    if (filterStrategy === "ALL") return true;
    return p.strategy_type === filterStrategy;
  });

  if (activePositions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-12 rounded-2xl border border-dashed border-border bg-surface/50 text-center">
        <div className="w-12 h-12 rounded-full bg-surface-card border border-border flex items-center justify-center text-gray-400 mb-3">
          <Layers className="w-6 h-6" />
        </div>
        <h3 className="text-base font-semibold text-white">Nenhuma Posição Aberta</h3>
        <p className="text-sm text-gray-400 max-w-sm mt-1">
          O bot está monitorando os scanners. Assim que um token atender aos critérios de segurança e liquidez, a posição será exibida aqui.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Filtros por Estratégia */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-1.5 bg-surface-card p-1 rounded-xl border border-border">
          <button
            onClick={() => setFilterStrategy("ALL")}
            className={`px-3 py-1 text-xs font-medium rounded-lg transition-all ${
              filterStrategy === "ALL"
                ? "bg-brand-500 text-white shadow-sm"
                : "text-gray-400 hover:text-white"
            }`}
          >
            Todas ({activePositions.length})
          </button>
          <button
            onClick={() => setFilterStrategy("SCALP")}
            className={`px-3 py-1 text-xs font-medium rounded-lg transition-all ${
              filterStrategy === "SCALP"
                ? "bg-scalp text-black font-semibold shadow-sm"
                : "text-gray-400 hover:text-white"
            }`}
          >
            Scalp ({activePositions.filter((p) => p.strategy_type === "SCALP").length})
          </button>
          <button
            onClick={() => setFilterStrategy("SWING")}
            className={`px-3 py-1 text-xs font-medium rounded-lg transition-all ${
              filterStrategy === "SWING"
                ? "bg-swing text-white shadow-sm"
                : "text-gray-400 hover:text-white"
            }`}
          >
            Swing ({activePositions.filter((p) => p.strategy_type === "SWING").length})
          </button>
        </div>

        <span className="text-xs text-gray-400 font-mono">
          Exibindo {filtered.length} de {activePositions.length} posições abertas
        </span>
      </div>

      {/* Grid de Posições */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {filtered.map((pos) => {
          const isScalp = pos.strategy_type === "SCALP";
          const pnlUsd = pos.unrealized_pnl_usd || 0;
          const pnlPct = pos.unrealized_pnl_pct || 0;
          const isPositive = pnlPct >= 0;

          const address = pos.token_address || pos.address || "";
          const symbol = pos.symbol || pos.token_symbol || shortenAddress(address);
          const name = pos.name && pos.name !== "N/A" ? pos.name : "";
          const dexUrl = `https://dexscreener.com/solana/${address}`;

          return (
            <div
              key={pos.id}
              className={`p-4 rounded-xl border transition-all flex flex-col justify-between ${
                isScalp
                  ? "bg-surface-card/90 border-scalp/20 hover:border-scalp/40"
                  : "bg-surface-card/90 border-swing/20 hover:border-swing/40"
              }`}
            >
              {/* Header do Card */}
              <div>
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="font-bold text-white tracking-wide text-base">
                        {symbol}
                      </span>
                      {name && (
                        <span className="text-gray-400 text-xs font-normal">
                          ({name})
                        </span>
                      )}
                      <span
                        className={`text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider ${
                          isScalp
                            ? "bg-scalp/15 text-scalp border border-scalp/30"
                            : "bg-swing/15 text-swing border border-swing/30"
                        }`}
                      >
                        {pos.strategy_type}
                      </span>
                    </div>

                    {address && (
                      <a
                        href={dexUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-xs text-gray-400 hover:text-brand-300 font-mono mt-0.5"
                        title="Abrir Gráfico no DexScreener"
                      >
                        {shortenAddress(address, 6)}
                        <ExternalLink className="w-3 h-3 inline" />
                      </a>
                    )}
                  </div>

                  {/* PnL Flutuante */}
                  <div className="text-right">
                    <div
                      className={`text-lg font-bold font-mono ${
                        isPositive ? "text-profit" : "text-loss"
                      }`}
                    >
                      {formatPct(pnlPct)}
                    </div>
                    <div className="text-xs text-gray-400 font-mono">
                      {formatUSD(pnlUsd)}
                    </div>
                  </div>
                </div>

                {/* Barra de Progresso Visual de PnL */}
                <div className="w-full bg-surface h-1.5 rounded-full overflow-hidden my-3">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${
                      isPositive ? "bg-profit" : "bg-loss"
                    }`}
                    style={{
                      width: `${Math.min(Math.max(Math.abs(pnlPct), 5), 100)}%`,
                    }}
                  />
                </div>

                {/* Métricas de Preço e Catraca */}
                <div className="grid grid-cols-2 gap-2 text-xs py-2 border-y border-border/60">
                  <div>
                    <span className="text-gray-400 block text-[10px]">Entrada</span>
                    <span className="font-mono text-white font-medium">
                      {formatUSD(pos.entry_price, 6)}
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-400 block text-[10px]">Preço Atual</span>
                    <span className="font-mono text-white font-medium">
                      {formatUSD(pos.current_price, 6)}
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-400 block text-[10px]">Capital Alocado</span>
                    <span className="font-mono text-white font-medium">
                      {formatUSD(pos.allocated_capital_usd)}
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-400 block text-[10px]">Máx Atingido</span>
                    <span className="font-mono text-white font-medium">
                      {formatUSD(pos.highest_price_seen, 6)}
                    </span>
                  </div>
                </div>

                {/* Travas de Risco Ativas */}
                <div className="mt-2 text-[11px] space-y-1 text-gray-300 font-mono">
                  {pos.trailing_stop_price > 0 && (
                    <div className="flex items-center justify-between">
                      <span className="text-gray-400 flex items-center gap-1">
                        <Shield className="w-3 h-3 text-amber-400" /> Trailing Stop:
                      </span>
                      <span>{formatUSD(pos.trailing_stop_price, 6)}</span>
                    </div>
                  )}

                  {!isScalp && pos.ratchet_floor_price && pos.ratchet_floor_price > 0 && (
                    <div className="flex items-center justify-between text-swing">
                      <span className="flex items-center gap-1">
                        <TrendingUp className="w-3 h-3" /> Piso Catraca (T{pos.active_tier || 1}):
                      </span>
                      <span>{formatUSD(pos.ratchet_floor_price, 6)}</span>
                    </div>
                  )}
                </div>
              </div>

              {/* Footer do Card */}
              <div className="flex items-center justify-between pt-3 mt-2 border-t border-border/60 text-[11px] text-gray-400">
                <span className="flex items-center gap-1">
                  <Clock className="w-3 h-3" /> Aberta {formatTimeAgo(pos.opened_at)}
                </span>
                <span className="font-mono text-[10px] bg-surface px-1.5 py-0.5 rounded border border-border">
                  ID #{pos.id}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

