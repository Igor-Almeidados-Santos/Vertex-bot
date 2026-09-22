"use client";

import { useState } from "react";
import {
  Search,
  ExternalLink,
  CheckCircle2,
  XCircle,
  AlertOctagon,
  Clock,
  History,
} from "lucide-react";
import { PositionItem } from "@/types/bot";
import { formatUSD, formatPct, shortenAddress, formatTimeAgo } from "@/lib/formatters";

interface TradeHistoryTabProps {
  positions: PositionItem[];
}

export function TradeHistoryTab({ positions }: TradeHistoryTabProps) {
  const closedPositions = positions.filter((p) => p.status === "CLOSED" || p.status === "STOPPED");
  const [search, setSearch] = useState("");
  const [filterReason, setFilterReason] = useState<string>("ALL");

  const filtered = closedPositions.filter((p) => {
    const matchesSearch =
      !search ||
      p.token_address.toLowerCase().includes(search.toLowerCase()) ||
      (p.token_symbol && p.token_symbol.toLowerCase().includes(search.toLowerCase()));

    if (!matchesSearch) return false;

    if (filterReason === "ALL") return true;
    if (filterReason === "WIN") return (p.realized_pnl_usd || 0) > 0;
    if (filterReason === "LOSS") return (p.realized_pnl_usd || 0) <= 0;
    if (p.close_reason && p.close_reason.includes(filterReason)) return true;

    return true;
  });

  if (closedPositions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-12 rounded-2xl border border-dashed border-border bg-surface/50 text-center">
        <div className="w-12 h-12 rounded-full bg-surface-card border border-border flex items-center justify-center text-gray-400 mb-3">
          <History className="w-6 h-6" />
        </div>
        <h3 className="text-base font-semibold text-white">Nenhum Trade Encerrado</h3>
        <p className="text-sm text-gray-400 max-w-sm mt-1">
          O histórico registrará automaticamente todas as saídas executadas pelo bot com o resultado de PnL e o motivo do fechamento.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Controles de Busca e Filtro */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3">
        {/* Search */}
        <div className="relative w-full sm:w-72">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar por símbolo ou endereço..."
            className="w-full pl-9 pr-3 py-1.5 text-xs bg-surface-card border border-border rounded-xl text-white placeholder-gray-500 focus:outline-none focus:border-brand-400"
          />
        </div>

        {/* Reason / Outcome Filters */}
        <div className="flex items-center gap-1.5 bg-surface-card p-1 rounded-xl border border-border w-full sm:w-auto overflow-x-auto">
          <button
            onClick={() => setFilterReason("ALL")}
            className={`px-3 py-1 text-xs font-medium rounded-lg transition-all ${
              filterReason === "ALL" ? "bg-brand-500 text-white" : "text-gray-400 hover:text-white"
            }`}
          >
            Todos ({closedPositions.length})
          </button>
          <button
            onClick={() => setFilterReason("WIN")}
            className={`px-3 py-1 text-xs font-medium rounded-lg transition-all ${
              filterReason === "WIN"
                ? "bg-profit/20 text-profit border border-profit/40"
                : "text-gray-400 hover:text-white"
            }`}
          >
            Lucros ({closedPositions.filter((p) => (p.realized_pnl_usd || 0) > 0).length})
          </button>
          <button
            onClick={() => setFilterReason("LOSS")}
            className={`px-3 py-1 text-xs font-medium rounded-lg transition-all ${
              filterReason === "LOSS"
                ? "bg-loss/20 text-loss border border-loss/40"
                : "text-gray-400 hover:text-white"
            }`}
          >
            Prejuízos ({closedPositions.filter((p) => (p.realized_pnl_usd || 0) <= 0).length})
          </button>
        </div>
      </div>

      {/* Tabela de Histórico */}
      <div className="rounded-xl border border-border bg-surface-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface border-b border-border text-gray-400 font-medium">
              <tr>
                <th className="py-3 px-4">Token / Par</th>
                <th className="py-3 px-4">Estratégia</th>
                <th className="py-3 px-4">PnL Realizado</th>
                <th className="py-3 px-4">Entrada / Saída</th>
                <th className="py-3 px-4">Motivo de Encerramento</th>
                <th className="py-3 px-4">Data / Hora</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {filtered.map((pos) => {
                const pnlUsd = pos.realized_pnl_usd ?? pos.unrealized_pnl_usd ?? 0;
                const isWin = pnlUsd > 0;
                const exitPrice = pos.exit_price || pos.current_price;
                const returnPct =
                  pos.entry_price > 0 ? ((exitPrice - pos.entry_price) / pos.entry_price) * 100 : 0;

                const address = pos.token_address || pos.address || "";
                const symbol = pos.symbol || pos.token_symbol || shortenAddress(address);
                const name = pos.name && pos.name !== "N/A" ? pos.name : "";
                const dexUrl = `https://dexscreener.com/solana/${address}`;

                return (
                  <tr key={pos.id} className="hover:bg-surface-hover/50 transition-colors">
                    <td className="py-3 px-4">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="font-bold text-white">
                          {symbol}
                        </span>
                        {name && (
                          <span className="text-gray-400 text-xs font-normal">
                            ({name})
                          </span>
                        )}
                        {address && (
                          <a
                            href={dexUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-gray-400 hover:text-brand-300 font-mono inline-flex items-center"
                            title="Abrir Gráfico no DexScreener"
                          >
                            <ExternalLink className="w-3.5 h-3.5 inline" />
                          </a>
                        )}
                      </div>
                      {address && (
                        <span className="text-[10px] text-gray-400 font-mono block">
                          {shortenAddress(address, 8)}
                        </span>
                      )}
                    </td>

                    <td className="py-3 px-4">
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                          pos.strategy_type === "SWING"
                            ? "bg-swing/15 text-swing border border-swing/30"
                            : "bg-scalp/15 text-scalp border border-scalp/30"
                        }`}
                      >
                        {pos.strategy_type}
                      </span>
                    </td>

                    <td className="py-3 px-4">
                      <div className="flex items-center gap-1.5">
                        {isWin ? (
                          <CheckCircle2 className="w-3.5 h-3.5 text-profit shrink-0" />
                        ) : (
                          <XCircle className="w-3.5 h-3.5 text-loss shrink-0" />
                        )}
                        <div>
                          <span
                            className={`font-mono font-bold block ${
                              isWin ? "text-profit" : "text-loss"
                            }`}
                          >
                            {formatUSD(pnlUsd)}
                          </span>
                          <span
                            className={`text-[10px] font-mono ${
                              returnPct >= 0 ? "text-profit" : "text-loss"
                            }`}
                          >
                            {formatPct(returnPct)}
                          </span>
                        </div>
                      </div>
                    </td>

                    <td className="py-3 px-4 font-mono text-gray-300">
                      <div>E: {formatUSD(pos.entry_price, 6)}</div>
                      <div className="text-gray-400 text-[10px]">S: {formatUSD(exitPrice, 6)}</div>
                    </td>

                    <td className="py-3 px-4">
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-mono bg-surface border border-border text-gray-300">
                        {pos.close_reason || "Saída a Mercado"}
                      </span>
                    </td>

                    <td className="py-3 px-4 text-gray-400 font-mono text-[11px]">
                      {formatTimeAgo(pos.closed_at || pos.opened_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

