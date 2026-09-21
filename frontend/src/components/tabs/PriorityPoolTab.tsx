"use client";

import { useEffect, useState, useMemo } from "react";
import {
  Star,
  RefreshCw,
  Search,
  ExternalLink,
  Copy,
  Check,
  TrendingUp,
  ShieldCheck,
  Building2,
  Rocket,
  Filter,
} from "lucide-react";
import { ChainBadge } from "@/components/ChainBadge";
import { getPriorityTokens } from "@/lib/api";
import { PriorityToken } from "@/types/bot";
import { formatUSD, formatCryptoPrice, shortenAddress } from "@/lib/formatters";

interface PriorityPoolTabProps {
  mode: "paper" | "live";
}

export function PriorityPoolTab({ mode }: PriorityPoolTabProps) {
  const [tokens, setTokens] = useState<PriorityToken[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedOrigin, setSelectedOrigin] = useState<"ALL" | "LIVE" | "PAPER" | "BOTH">("ALL");
  const [selectedTier, setSelectedTier] = useState<"ALL" | "CONSOLIDATED" | "EMERGING">("ALL");
  const [copiedAddress, setCopiedAddress] = useState<string | null>(null);

  const fetchTokens = async (showLoading = false) => {
    if (showLoading) setIsLoading(true);
    setIsRefreshing(true);
    try {
      const data = await getPriorityTokens(mode);
      setTokens(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error("Erro ao carregar tokens prioritários:", err);
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    fetchTokens(true);
    const interval = setInterval(() => {
      fetchTokens(false);
    }, 10000);
    return () => clearInterval(interval);
  }, [mode]);

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedAddress(text);
    setTimeout(() => setCopiedAddress(null), 2000);
  };

  // Filtros aplicados
  const filteredTokens = useMemo(() => {
    return tokens.filter((tok) => {
      const term = searchTerm.toLowerCase().trim();
      const matchesSearch =
        !term ||
        tok.symbol.toLowerCase().includes(term) ||
        tok.name.toLowerCase().includes(term) ||
        tok.address.toLowerCase().includes(term);

      const origin = (tok.origin_mode || "PAPER").toUpperCase();
      const matchesOrigin =
        selectedOrigin === "ALL" ||
        (selectedOrigin === "LIVE" && (origin === "LIVE" || origin === "BOTH")) ||
        (selectedOrigin === "PAPER" && (origin === "PAPER" || origin === "BOTH")) ||
        (selectedOrigin === "BOTH" && origin === "BOTH");

      const matchesTier =
        selectedTier === "ALL" ||
        tok.tier?.toUpperCase() === selectedTier;

      return matchesSearch && matchesOrigin && matchesTier;
    });
  }, [tokens, searchTerm, selectedOrigin, selectedTier]);

  // Contadores analíticos
  const stats = useMemo(() => {
    const total = tokens.length;
    const consolidated = tokens.filter((t) => t.tier === "CONSOLIDATED").length;
    const emerging = tokens.filter((t) => t.tier === "EMERGING").length;
    const fromReal = tokens.filter((t) => (t.origin_mode || "").toUpperCase() === "LIVE").length;
    const fromPaper = tokens.filter((t) => (t.origin_mode || "PAPER").toUpperCase() === "PAPER").length;
    const fromBoth = tokens.filter((t) => (t.origin_mode || "").toUpperCase() === "BOTH").length;
    const totalPnl = tokens.reduce((acc, t) => acc + (t.total_realized_pnl_usd || 0), 0);
    return { total, consolidated, emerging, fromReal, fromPaper, fromBoth, totalPnl };
  }, [tokens]);

  return (
    <div className="space-y-4">
      {/* Banner Informativo */}
      <div className="p-4 rounded-xl border border-brand-500/30 bg-brand-500/10 text-xs text-brand-300 flex items-start gap-3">
        <Star className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="font-bold text-white text-sm">
              {mode === "paper"
                ? "Lista de Prioridades da Simulação (Real + Simulação Integrados)"
                : "Pool de Prioridades de Negociação (Ativos Reais Consolidados)"}
            </span>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-mono bg-brand-500/20 text-brand-300 border border-brand-500/30">
              {mode === "paper" ? "PAPER TRADING" : "LIVE TRADING"}
            </span>
          </div>
          <p className="text-gray-300 leading-relaxed">
            {mode === "paper"
              ? "Na simulação, constam todos os tokens aprovados pelos gates de segurança e validados no mercado, originados tanto de operações em modo Real quanto Simulado que continuam ativos e saudáveis (de acordo). O bot tem precedência de execução contínua nestes ativos."
              : "Tokens aprovados e operados na blockchain com capital real. Enquanto mantiverem liquidez ativa (≥ $5.000) e estrutura saudável sem quedas bruscas, o bot prioriza alocações e reentradas nestes ativos."}
          </p>
        </div>
      </div>

      {/* Grid de Métricas do Pool */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <div className="p-3 rounded-xl border border-border bg-surface-card">
          <span className="text-[11px] text-gray-400 block">Total de Ativos</span>
          <span className="text-lg font-bold font-mono text-white mt-0.5 block">{stats.total}</span>
        </div>
        <div className="p-3 rounded-xl border border-border bg-surface-card">
          <span className="text-[11px] text-amber-300 flex items-center gap-1">
            <Building2 className="w-3 h-3" /> Consolidados
          </span>
          <span className="text-lg font-bold font-mono text-amber-400 mt-0.5 block">{stats.consolidated}</span>
        </div>
        <div className="p-3 rounded-xl border border-border bg-surface-card">
          <span className="text-[11px] text-cyan-300 flex items-center gap-1">
            <Rocket className="w-3 h-3" /> Em Ascensão
          </span>
          <span className="text-lg font-bold font-mono text-cyan-400 mt-0.5 block">{stats.emerging}</span>
        </div>
        <div className="p-3 rounded-xl border border-border bg-surface-card">
          <span className="text-[11px] text-emerald-400 flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-emerald-400" /> Origem Real
          </span>
          <span className="text-lg font-bold font-mono text-emerald-400 mt-0.5 block">{stats.fromReal + stats.fromBoth}</span>
        </div>
        <div className="p-3 rounded-xl border border-border bg-surface-card">
          <span className="text-[11px] text-blue-400 flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-blue-400" /> Origem Simulação
          </span>
          <span className="text-lg font-bold font-mono text-blue-400 mt-0.5 block">{stats.fromPaper + stats.fromBoth}</span>
        </div>
        <div className="p-3 rounded-xl border border-border bg-surface-card">
          <span className="text-[11px] text-gray-400 flex items-center gap-1">
            <TrendingUp className="w-3 h-3" /> PnL Acumulado
          </span>
          <span
            className={`text-lg font-bold font-mono mt-0.5 block ${
              stats.totalPnl >= 0 ? "text-profit" : "text-loss"
            }`}
          >
            {formatUSD(stats.totalPnl)}
          </span>
        </div>
      </div>

      {/* Barra de Controles e Filtros */}
      <div className="p-3.5 rounded-xl border border-border bg-surface-card flex flex-col md:flex-row items-center justify-between gap-3">
        {/* Campo de Busca */}
        <div className="relative w-full md:w-72">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Buscar por símbolo ou endereço..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full bg-surface border border-border rounded-xl pl-9 pr-3 py-1.5 text-xs text-white placeholder-gray-500 focus:outline-none focus:border-brand-500"
          />
        </div>

        {/* Filtros Rápidos */}
        <div className="flex flex-wrap items-center gap-2 w-full md:w-auto">
          {/* Filtro de Origem */}
          {mode === "paper" && (
            <div className="flex items-center gap-1 bg-surface p-1 rounded-xl border border-border text-[11px]">
              <span className="text-gray-400 px-1.5 flex items-center gap-1">
                <Filter className="w-3 h-3" /> Origem:
              </span>
              <button
                onClick={() => setSelectedOrigin("ALL")}
                className={`px-2 py-0.5 rounded-lg transition-colors ${
                  selectedOrigin === "ALL" ? "bg-brand-500 text-white font-semibold" : "text-gray-400 hover:text-white"
                }`}
              >
                Todas
              </button>
              <button
                onClick={() => setSelectedOrigin("LIVE")}
                className={`px-2 py-0.5 rounded-lg transition-colors ${
                  selectedOrigin === "LIVE" ? "bg-emerald-500/20 text-emerald-400 font-semibold border border-emerald-500/30" : "text-gray-400 hover:text-white"
                }`}
              >
                🟢 Real
              </button>
              <button
                onClick={() => setSelectedOrigin("PAPER")}
                className={`px-2 py-0.5 rounded-lg transition-colors ${
                  selectedOrigin === "PAPER" ? "bg-blue-500/20 text-blue-400 font-semibold border border-blue-500/30" : "text-gray-400 hover:text-white"
                }`}
              >
                🔵 Simulação
              </button>
              <button
                onClick={() => setSelectedOrigin("BOTH")}
                className={`px-2 py-0.5 rounded-lg transition-colors ${
                  selectedOrigin === "BOTH" ? "bg-purple-500/20 text-purple-300 font-semibold border border-purple-500/30" : "text-gray-400 hover:text-white"
                }`}
              >
                🟣 Ambos
              </button>
            </div>
          )}

          {/* Filtro de Tier */}
          <div className="flex items-center gap-1 bg-surface p-1 rounded-xl border border-border text-[11px]">
            <button
              onClick={() => setSelectedTier("ALL")}
              className={`px-2 py-0.5 rounded-lg transition-colors ${
                selectedTier === "ALL" ? "bg-brand-500 text-white font-semibold" : "text-gray-400 hover:text-white"
              }`}
            >
              Todos Tiers
            </button>
            <button
              onClick={() => setSelectedTier("CONSOLIDATED")}
              className={`px-2 py-0.5 rounded-lg transition-colors ${
                selectedTier === "CONSOLIDATED" ? "bg-amber-500/20 text-amber-300 font-semibold border border-amber-500/30" : "text-gray-400 hover:text-white"
              }`}
            >
              🏛️ Consolidados
            </button>
            <button
              onClick={() => setSelectedTier("EMERGING")}
              className={`px-2 py-0.5 rounded-lg transition-colors ${
                selectedTier === "EMERGING" ? "bg-cyan-500/20 text-cyan-300 font-semibold border border-cyan-500/30" : "text-gray-400 hover:text-white"
              }`}
            >
              🚀 Em Ascensão
            </button>
          </div>

          {/* Botão Atualizar */}
          <button
            onClick={() => fetchTokens(false)}
            disabled={isRefreshing}
            className="p-1.5 rounded-xl bg-surface hover:bg-surface-hover border border-border text-gray-400 hover:text-white transition-colors"
            title="Atualizar lista"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? "animate-spin text-brand-400" : ""}`} />
          </button>
        </div>
      </div>

      {/* Tabela de Tokens Prioritários */}
      {filteredTokens.length === 0 ? (
        <div className="flex flex-col items-center justify-center p-12 rounded-2xl border border-dashed border-border bg-surface/50 text-center">
          <div className="w-12 h-12 rounded-full bg-surface-card border border-border flex items-center justify-center text-amber-400 mb-3">
            <Star className="w-6 h-6" />
          </div>
          <h3 className="text-base font-semibold text-white">Nenhum Ativo Encontrado</h3>
          <p className="text-sm text-gray-400 max-w-sm mt-1">
            {searchTerm || selectedOrigin !== "ALL" || selectedTier !== "ALL"
              ? "Nenhum ativo corresponde aos filtros selecionados."
              : mode === "paper"
              ? "Tokens aprovados e negociados no modo Real ou Simulação que continuam saudáveis no mercado serão catalogados aqui com prioridade contínua de negociação."
              : "Assim que o bot executar operações e confirmar ativos com liquidez ativa on-chain, eles serão registrados aqui."}
          </p>
        </div>
      ) : (
        <div className="rounded-2xl border border-border bg-surface-card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-border bg-surface/60 text-gray-400 font-medium">
                  <th className="py-3 px-4">Ativo Prioritário</th>
                  <th className="py-3 px-4">Origem</th>
                  <th className="py-3 px-4">Categoria / Tier</th>
                  <th className="py-3 px-4">Status de Saúde</th>
                  <th className="py-3 px-4">Preço Atual</th>
                  <th className="py-3 px-4">Liquidez On-Chain</th>
                  <th className="py-3 px-4">Trades Realizados</th>
                  <th className="py-3 px-4">PnL Total Acumulado</th>
                  <th className="py-3 px-4">Última Operação</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {filteredTokens.map((tok) => {
                  const isConsolidated = tok.tier === "CONSOLIDATED";
                  const isProfitable = tok.total_realized_pnl_usd >= 0;
                  const origin = (tok.origin_mode || "PAPER").toUpperCase();
                  const solscanUrl = `https://solscan.io/account/${tok.address}`;

                  return (
                    <tr key={tok.address} className="hover:bg-surface-hover/50 transition-colors">
                      {/* Ativo */}
                      <td className="py-3 px-4">
                        <div className="flex items-center gap-2">
                          <span className="font-bold font-mono text-white text-sm">
                            {tok.symbol}
                          </span>
                          <ChainBadge chain={tok.chain} />
                        </div>
                        <div className="flex items-center gap-1.5 mt-0.5">
                          <span className="text-[10px] text-gray-400 font-mono">
                            {shortenAddress(tok.address, 6)}
                          </span>
                          <button
                            onClick={() => copyToClipboard(tok.address)}
                            className="p-0.5 rounded hover:bg-surface-hover text-gray-400 hover:text-white"
                            title="Copiar Endereço"
                          >
                            {copiedAddress === tok.address ? (
                              <Check className="w-3 h-3 text-profit" />
                            ) : (
                              <Copy className="w-3 h-3" />
                            )}
                          </button>
                          <a
                            href={solscanUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="p-0.5 rounded hover:bg-surface-hover text-gray-400 hover:text-white"
                            title="Ver no Solscan"
                          >
                            <ExternalLink className="w-3 h-3" />
                          </a>
                        </div>
                      </td>

                      {/* Origem */}
                      <td className="py-3 px-4">
                        {origin === "LIVE" ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold font-mono bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                            🟢 REAL
                          </span>
                        ) : origin === "BOTH" ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold font-mono bg-purple-500/15 text-purple-300 border border-purple-500/30">
                            🟣 REAL + SIM
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold font-mono bg-blue-500/15 text-blue-400 border border-blue-500/30">
                            🔵 SIMULAÇÃO
                          </span>
                        )}
                      </td>

                      {/* Tier */}
                      <td className="py-3 px-4">
                        <span
                          className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-bold font-mono ${
                            isConsolidated
                              ? "bg-amber-500/15 text-amber-300 border border-amber-500/30"
                              : "bg-cyan-500/15 text-cyan-300 border border-cyan-500/30"
                          }`}
                        >
                          {isConsolidated ? "🏛️ CONSOLIDADO" : "🚀 EM ASCENSÃO"}
                        </span>
                      </td>

                      {/* Status de Saúde */}
                      <td className="py-3 px-4">
                        <span
                          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold font-mono ${
                            tok.is_active_priority && tok.is_alive
                              ? "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"
                              : "bg-gray-500/15 text-gray-400 border border-gray-500/30"
                          }`}
                        >
                          {tok.is_active_priority && tok.is_alive
                            ? "⭐ DE ACORDO"
                            : "⏸️ OBSERVAÇÃO"}
                        </span>
                      </td>

                      {/* Preço Atual */}
                      <td className="py-3 px-4 font-mono text-white font-semibold">
                        {formatCryptoPrice(tok.last_price)}
                      </td>

                      {/* Liquidez */}
                      <td className="py-3 px-4 font-mono text-gray-300">
                        {formatUSD(tok.current_liquidity_usd || tok.initial_liquidity_usd)}
                      </td>

                      {/* Trades Realizados */}
                      <td className="py-3 px-4 font-mono text-gray-300">
                        <span className="font-bold text-white">{tok.total_trades_count}</span> trades
                        {tok.successful_trades_count > 0 && (
                          <span className="text-profit ml-1 text-[11px]">
                            ({tok.successful_trades_count} no lucro)
                          </span>
                        )}
                      </td>

                      {/* PnL Total */}
                      <td className="py-3 px-4 font-mono">
                        <span
                          className={`font-semibold ${
                            isProfitable ? "text-profit" : "text-loss"
                          }`}
                        >
                          {isProfitable ? "+" : ""}
                          {formatUSD(tok.total_realized_pnl_usd)}
                        </span>
                      </td>

                      {/* Última Operação */}
                      <td className="py-3 px-4 text-gray-400 text-[11px]">
                        {tok.last_traded_at
                          ? new Date(tok.last_traded_at).toLocaleTimeString([], {
                              hour: "2-digit",
                              minute: "2-digit",
                              second: "2-digit",
                            })
                          : "Recente"}
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

