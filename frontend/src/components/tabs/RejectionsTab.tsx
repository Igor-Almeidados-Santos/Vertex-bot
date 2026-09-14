"use client";

import { useEffect, useState } from "react";
import { ShieldAlert, Search, ExternalLink, RefreshCw } from "lucide-react";
import { CatalogTokenItem } from "@/types/bot";
import { getTokens } from "@/lib/api";
import { formatUSD, shortenAddress, formatTimeAgo } from "@/lib/formatters";

export function RejectionsTab() {
  const [tokens, setTokens] = useState<CatalogTokenItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  const loadRejections = async () => {
    try {
      setLoading(true);
      const data = await getTokens("REJEITADO", search);
      setTokens(data);
    } catch (err) {
      console.error("Erro ao carregar rejeições:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadRejections();
  }, []);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    loadRejections();
  };

  return (
    <div className="space-y-4">
      {/* Controles de Busca */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3">
        <form onSubmit={handleSearch} className="relative w-full sm:w-80">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar rejeição por símbolo ou mint..."
            className="w-full pl-9 pr-3 py-1.5 text-xs bg-surface-card border border-border rounded-xl text-white placeholder-gray-500 focus:outline-none focus:border-brand-400"
          />
        </form>

        <button
          onClick={loadRejections}
          title="Recarregar Rejeições"
          className="p-2 rounded-xl border border-border bg-surface-card text-gray-400 hover:text-white transition-colors"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {/* Tabela de Rejeições */}
      <div className="rounded-xl border border-border bg-surface-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface border-b border-border text-gray-400 font-medium">
              <tr>
                <th className="py-3 px-4">Token / Par</th>
                <th className="py-3 px-4">Motivo Reprovação (Hard Gate)</th>
                <th className="py-3 px-4">Liquidez</th>
                <th className="py-3 px-4">DEX</th>
                <th className="py-3 px-4">Data Análise</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {tokens.map((tok) => {
                const symbol = tok.symbol || shortenAddress(tok.address);
                const name = tok.name && tok.name !== "N/A" ? tok.name : "";
                const catalogedAt = tok.cataloged_at || tok.detection_timestamp || "";

                return (
                  <tr key={tok.address} className="hover:bg-surface-hover/50 transition-colors">
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
                        <a
                          href={`https://dexscreener.com/solana/${tok.address}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-gray-400 hover:text-brand-300 font-mono inline-flex items-center"
                          title="Abrir Gráfico no DexScreener"
                        >
                          <ExternalLink className="w-3.5 h-3.5 inline" />
                        </a>
                      </div>
                      <span className="text-[10px] text-gray-400 font-mono block">
                        {shortenAddress(tok.address, 8)}
                      </span>
                    </td>

                    <td className="py-3 px-4">
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-mono bg-loss/10 border border-loss/30 text-loss font-medium">
                        <ShieldAlert className="w-3.5 h-3.5 shrink-0" />
                        {tok.rejection_reason || "Reprovado em Hard Gate de Risco"}
                      </span>
                    </td>

                    <td className="py-3 px-4 font-mono text-white">
                      {formatUSD(tok.initial_liquidity_usd)}
                    </td>

                    <td className="py-3 px-4 uppercase font-mono text-gray-300">
                      {tok.dex || "Raydium"}
                    </td>

                    <td className="py-3 px-4 text-gray-400 font-mono text-[11px]">
                      {formatTimeAgo(catalogedAt)}
                    </td>
                  </tr>
                );
              })}
              {tokens.length === 0 && !loading && (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-gray-400 text-xs">
                    Nenhuma reprovação recente registrada no período.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

