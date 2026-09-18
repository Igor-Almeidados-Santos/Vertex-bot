"use client";

import { useState } from "react";
import {
  Clock,
  ExternalLink,
  Info,
  ShieldCheck,
  Zap,
  Search,
  CheckCircle2,
  Hourglass,
  Wallet,
  Layers,
  Copy,
  Check,
} from "lucide-react";
import { WaitingTokenItem } from "@/types/bot";
import { formatUSD, shortenAddress, formatTimeAgo } from "@/lib/formatters";

interface IncubatorTabProps {
  waitingTokens: WaitingTokenItem[];
}

export function IncubatorTab({ waitingTokens }: IncubatorTabProps) {
  const [filter, setFilter] = useState<string>("ALL");
  const [search, setSearch] = useState<string>("");
  const [copiedAddr, setCopiedAddr] = useState<string | null>(null);

  const copyAddress = (addr: string) => {
    navigator.clipboard.writeText(addr);
    setCopiedAddr(addr);
    setTimeout(() => setCopiedAddr(null), 2000);
  };

  const filteredTokens = waitingTokens.filter((item) => {
    const addr = (item.address || item.token_address || "").toLowerCase();
    const sym = (item.symbol || item.token_symbol || "").toLowerCase();
    const name = (item.name || "").toLowerCase();
    const query = search.toLowerCase().trim();

    const matchesSearch = !query || sym.includes(query) || name.includes(query) || addr.includes(query);
    if (!matchesSearch) return false;

    const reason = item.waiting_reason || item.reason_pending || "";
    if (filter === "SLOT") return reason.includes("SLOT") || reason === "AGUARDANDO_SLOT";
    if (filter === "BALANCE") return reason.includes("SALDO") || reason.includes("CASH") || reason === "AGUARDANDO_SALDO";
    if (filter === "MATURING") return reason.includes("MATURA") || reason.includes("IDADE");
    return true;
  });

  const countSlots = waitingTokens.filter((t) => (t.waiting_reason || t.reason_pending || "").includes("SLOT")).length;
  const countBalance = waitingTokens.filter((t) => (t.waiting_reason || t.reason_pending || "").includes("SALDO")).length;
  const countMaturing = waitingTokens.filter((t) => (t.waiting_reason || t.reason_pending || "").includes("MATURA")).length;

  return (
    <div className="space-y-4">
      {/* Header com Resumo e Métricas */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <div className="p-3 rounded-xl bg-surface-card border border-border flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-brand-500/10 border border-brand-500/20 flex items-center justify-center text-brand-400">
            <Layers className="w-5 h-5" />
          </div>
          <div>
            <div className="text-[11px] text-gray-400 font-medium">Total na Incubadora</div>
            <div className="text-lg font-bold text-white">{waitingTokens.length}</div>
          </div>
        </div>

        <div className="p-3 rounded-xl bg-surface-card border border-border flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-sky-500/10 border border-sky-500/20 flex items-center justify-center text-sky-400">
            <Hourglass className="w-5 h-5" />
          </div>
          <div>
            <div className="text-[11px] text-gray-400 font-medium">Aguardando Slots</div>
            <div className="text-lg font-bold text-sky-300">{countSlots}</div>
          </div>
        </div>

        <div className="p-3 rounded-xl bg-surface-card border border-border flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-amber-500/10 border border-amber-500/20 flex items-center justify-center text-amber-400">
            <Wallet className="w-5 h-5" />
          </div>
          <div>
            <div className="text-[11px] text-gray-400 font-medium">Aguardando Saldo</div>
            <div className="text-lg font-bold text-amber-300">{countBalance}</div>
          </div>
        </div>

        <div className="p-3 rounded-xl bg-surface-card border border-border flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-purple-500/10 border border-purple-500/20 flex items-center justify-center text-purple-400">
            <Clock className="w-5 h-5" />
          </div>
          <div>
            <div className="text-[11px] text-gray-400 font-medium">Em Maturação</div>
            <div className="text-lg font-bold text-purple-300">{countMaturing}</div>
          </div>
        </div>
      </div>

      {/* Banner Informativo */}
      <div className="p-3.5 rounded-xl bg-brand-500/10 border border-brand-500/20 flex items-start gap-3">
        <Info className="w-5 h-5 text-brand-400 shrink-0 mt-0.5" />
        <div className="text-xs text-gray-300 space-y-1">
          <span className="font-semibold text-brand-300 block">
            Incubadora e Triagem Pré-Execução ({waitingTokens.length} tokens catalogados)
          </span>
          <p>
            Tokens aprovados nos Hard Gates que aguardam vagas de execução ou consolidação de tempo. O bot avalia
            continuamente a liberação de slots e saldo para promover a abertura de posições instantaneamente.
          </p>
        </div>
      </div>

      {/* Barra de Filtros e Busca */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3 text-xs">
        <div className="flex items-center gap-1.5 w-full sm:w-auto overflow-x-auto pb-1 sm:pb-0">
          <button
            onClick={() => setFilter("ALL")}
            className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
              filter === "ALL"
                ? "bg-brand-500 text-white"
                : "bg-surface-card border border-border text-gray-400 hover:text-white"
            }`}
          >
            Todos ({waitingTokens.length})
          </button>
          <button
            onClick={() => setFilter("SLOT")}
            className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
              filter === "SLOT"
                ? "bg-sky-600 text-white"
                : "bg-surface-card border border-border text-gray-400 hover:text-white"
            }`}
          >
            Aguardando Slot ({countSlots})
          </button>
          <button
            onClick={() => setFilter("BALANCE")}
            className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
              filter === "BALANCE"
                ? "bg-amber-600 text-white"
                : "bg-surface-card border border-border text-gray-400 hover:text-white"
            }`}
          >
            Aguardando Saldo ({countBalance})
          </button>
          <button
            onClick={() => setFilter("MATURING")}
            className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
              filter === "MATURING"
                ? "bg-purple-600 text-white"
                : "bg-surface-card border border-border text-gray-400 hover:text-white"
            }`}
          >
            Em Maturação ({countMaturing})
          </button>
        </div>

        <div className="relative w-full sm:w-64">
          <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            placeholder="Buscar por símbolo ou endereço..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-8 pr-3 py-1.5 rounded-lg bg-surface border border-border text-white placeholder-gray-500 text-xs focus:outline-none focus:border-brand-500"
          />
        </div>
      </div>

      {/* Lista de Tokens da Incubadora */}
      {filteredTokens.length === 0 ? (
        <div className="flex flex-col items-center justify-center p-12 rounded-xl border border-dashed border-border bg-surface/50 text-center">
          <div className="w-12 h-12 rounded-full bg-surface-card border border-border flex items-center justify-center text-gray-400 mb-3">
            <Clock className="w-6 h-6" />
          </div>
          <h3 className="text-sm font-semibold text-white">Nenhum token encontrado nesta categoria</h3>
          <p className="text-xs text-gray-400 max-w-sm mt-1">
            Novos tokens detectados que atenderem aos critérios quantitativos serão automaticamente catalogados aqui.
          </p>
        </div>
      ) : (
        <div className="rounded-xl border border-border bg-surface-card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-surface border-b border-border text-gray-400 font-medium">
                <tr>
                  <th className="py-3 px-4">Token</th>
                  <th className="py-3 px-4">Estratégia</th>
                  <th className="py-3 px-4">Status / Motivo</th>
                  <th className="py-3 px-4">Liquidez Atual</th>
                  <th className="py-3 px-4">Idade de Mercado</th>
                  <th className="py-3 px-4">Enfileirado Há</th>
                  <th className="py-3 px-4 text-right">Ações</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {filteredTokens.map((item, idx) => {
                  const address = item.address || item.token_address || "";
                  const symbol = item.symbol || item.token_symbol || shortenAddress(address);
                  const name = item.name && item.name !== "N/A" ? item.name : "";
                  const dexUrl = `https://dexscreener.com/solana/${address}`;
                  const solscanUrl = `https://solscan.io/token/${address}`;
                  const liquidity = item.initial_liquidity_usd ?? item.liquidity_usd ?? 0;
                  const reason = item.waiting_reason || item.reason_pending || "AGUARDANDO_SLOT";
                  const enqueuedAt = item.enqueued_at || item.added_at || "";
                  const strategy = item.eligible_strategy || "DUAL";
                  const isSlot = reason.includes("SLOT") || reason === "AGUARDANDO_SLOT";
                  const isCash = reason.includes("SALDO") || reason === "AGUARDANDO_SALDO";

                  return (
                    <tr key={idx} className="hover:bg-surface-hover/50 transition-colors">
                      <td className="py-3 px-4">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="font-bold text-white text-sm">{symbol}</span>
                          {name && <span className="text-gray-400 text-xs font-normal">({name})</span>}
                          <span className="inline-flex items-center gap-1 text-[10px] text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-1.5 py-0.5 rounded">
                            <ShieldCheck className="w-3 h-3" /> Auditado
                          </span>
                        </div>
                        <div className="flex items-center gap-2 mt-1">
                          <span className="text-[11px] text-gray-400 font-mono">
                            {shortenAddress(address, 6)}
                          </span>
                          <button
                            onClick={() => copyAddress(address)}
                            className="text-gray-500 hover:text-white transition-colors"
                            title="Copiar endereço do token"
                          >
                            {copiedAddr === address ? (
                              <Check className="w-3 h-3 text-emerald-400" />
                            ) : (
                              <Copy className="w-3 h-3" />
                            )}
                          </button>
                          <a
                            href={dexUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-gray-500 hover:text-brand-400 transition-colors"
                            title="Ver no DexScreener"
                          >
                            <ExternalLink className="w-3 h-3" />
                          </a>
                        </div>
                      </td>

                      <td className="py-3 px-4">
                        <span
                          className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-semibold border ${
                            strategy === "DUAL"
                              ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                              : strategy === "SWING"
                              ? "bg-purple-500/10 text-purple-400 border-purple-500/30"
                              : "bg-sky-500/10 text-sky-400 border-sky-500/30"
                          }`}
                        >
                          <Zap className="w-3 h-3" />
                          {strategy}
                        </span>
                      </td>

                      <td className="py-3 px-4">
                        {isSlot ? (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium bg-sky-500/10 text-sky-400 border border-sky-500/30">
                            <Hourglass className="w-3 h-3 animate-pulse" />
                            Aguardando Slot Livre
                          </span>
                        ) : isCash ? (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium bg-amber-500/10 text-amber-400 border border-amber-500/30">
                            <Wallet className="w-3 h-3" />
                            Aguardando Saldo
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium bg-purple-500/10 text-purple-400 border border-purple-500/30">
                            <Clock className="w-3 h-3" />
                            {reason}
                          </span>
                        )}
                      </td>

                      <td className="py-3 px-4 font-mono font-medium text-white">
                        {formatUSD(liquidity)}
                      </td>

                      <td className="py-3 px-4 text-gray-300">
                        {item.age_hours !== undefined && item.age_hours !== null ? (
                          <span className="inline-flex items-center gap-1 font-mono">
                            <Clock className="w-3 h-3 text-gray-400" />
                            {item.age_hours >= 24
                              ? `${(item.age_hours / 24).toFixed(1)} dias`
                              : `${item.age_hours.toFixed(1)} horas`}
                          </span>
                        ) : (
                          <span className="text-gray-500">Recente</span>
                        )}
                      </td>

                      <td className="py-3 px-4 text-gray-400">
                        {enqueuedAt ? formatTimeAgo(enqueuedAt) : "Agora"}
                      </td>

                      <td className="py-3 px-4 text-right">
                        <a
                          href={solscanUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="px-2.5 py-1.5 rounded-lg bg-surface border border-border text-gray-300 hover:text-white hover:border-brand-500/40 text-[11px] inline-flex items-center gap-1 transition-all"
                        >
                          Solscan <ExternalLink className="w-3 h-3" />
                        </a>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

