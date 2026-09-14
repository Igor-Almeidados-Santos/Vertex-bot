"use client";

import { useEffect, useState } from "react";
import { Search, ExternalLink, RefreshCw, Eye } from "lucide-react";
import { CatalogTokenItem } from "@/types/bot";
import { getTokens } from "@/lib/api";
import { formatUSD, shortenAddress, formatTimeAgo } from "@/lib/formatters";

export function TokenCatalogTab() {
  const [tokens, setTokens] = useState<CatalogTokenItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [search, setSearch] = useState("");

  const loadTokens = async () => {
    try {
      setLoading(true);
      const data = await getTokens(statusFilter, search);
      setTokens(data);
    } catch (err) {
      console.error("Erro ao carregar catálogo:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTokens();
  }, [statusFilter]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    loadTokens();
  };

  return (
    <div className="space-y-4">
      {/* Controles */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3">
        <form onSubmit={handleSearchSubmit} className="relative w-full sm:w-72">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar por símbolo ou endereço..."
            className="w-full pl-9 pr-3 py-1.5 text-xs bg-surface-card border border-border rounded-xl text-white placeholder-gray-500 focus:outline-none focus:border-brand-400"
          />
        </form>

        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5 bg-surface-card p-1 rounded-xl border border-border">
            <button
              onClick={() => setStatusFilter("")}
              className={`px-3 py-1 text-xs font-medium rounded-lg transition-all ${
                statusFilter === "" ? "bg-brand-500 text-white" : "text-gray-400 hover:text-white"
              }`}
            >
              Todos
            </button>
            <button
              onClick={() => setStatusFilter("APROVADO")}
              className={`px-3 py-1 text-xs font-medium rounded-lg transition-all ${
                statusFilter === "APROVADO"
                  ? "bg-profit/20 text-profit border border-profit/40"
                  : "text-gray-400 hover:text-white"
              }`}
            >
              Aprovados
            </button>
            <button
              onClick={() => setStatusFilter("REJEITADO")}
              className={`px-3 py-1 text-xs font-medium rounded-lg transition-all ${
                statusFilter === "REJEITADO"
                  ? "bg-loss/20 text-loss border border-loss/40"
                  : "text-gray-400 hover:text-white"
              }`}
            >
              Rejeitados
            </button>
          </div>

          <button
            onClick={loadTokens}
            title="Recarregar Catálogo"
            className="p-2 rounded-xl border border-border bg-surface-card text-gray-400 hover:text-white transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {/* Tabela do Catálogo */}
      <div className="rounded-xl border border-border bg-surface-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface border-b border-border text-gray-400 font-medium">
              <tr>
                <th className="py-3 px-4">Token</th>
                <th className="py-3 px-4">DEX / Pool</th>
                <th className="py-3 px-4">Status</th>
                <th className="py-3 px-4">Liquidez Inicial</th>
                <th className="py-3 px-4">Score de Segurança</th>
                <th className="py-3 px-4">Catalogado</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {tokens.map((tok) => {
                const isApproved =
                  tok.status === "APROVADO" ||
                  tok.status === "APPROVED" ||
                  tok.security_status === "APPROVED";
                const symbol = tok.symbol || shortenAddress(tok.address);
                const name = tok.name && tok.name !== "N/A" ? tok.name : "";
                const score = tok.security_score ?? tok.score;
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

                    <td className="py-3 px-4 uppercase font-mono text-gray-300">
                      {tok.dex || "Raydium"}
                    </td>

                    <td className="py-3 px-4">
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-[10px] font-bold ${
                          isApproved
                            ? "bg-profit/15 text-profit border border-profit/30"
                            : "bg-loss/15 text-loss border border-loss/30"
                        }`}
                      >
                        {isApproved ? "APROVADO" : tok.status || tok.security_status || "REJEITADO"}
                      </span>
                    </td>

                    <td className="py-3 px-4 font-mono text-white">
                      {formatUSD(tok.initial_liquidity_usd)}
                    </td>

                    <td className="py-3 px-4 font-mono">
                      {score !== undefined && score !== null ? (
                        <span
                          className={`font-semibold ${
                            score >= 80
                              ? "text-profit"
                              : score >= 50
                              ? "text-amber-400"
                              : "text-loss"
                          }`}
                        >
                          {score}/100
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>

                    <td className="py-3 px-4 text-gray-400 font-mono text-[11px]">
                      {formatTimeAgo(catalogedAt)}
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

