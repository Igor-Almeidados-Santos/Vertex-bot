"use client";

import {
  Wallet,
  TrendingUp,
  Target,
  Layers,
  Clock,
  SearchCheck,
} from "lucide-react";
import { BotStatusData, PositionItem, SummaryData, WaitingTokenItem } from "@/types/bot";
import { formatUSD, formatPct, formatNumber } from "@/lib/formatters";

interface KpiGridProps {
  status: BotStatusData | null;
  summary: SummaryData | null;
  positions: PositionItem[];
  waitingTokens: WaitingTokenItem[];
}

export function KpiGrid({ status, summary, positions, waitingTokens }: KpiGridProps) {
  const pnl = summary?.pnl;
  const scanner = summary?.scanner;

  const currentCash = pnl?.current_cash_usd ?? status?.wallet_balance_usd ?? 0;
  const initialWallet = pnl?.initial_wallet_usd ?? status?.initial_wallet_usd ?? 10;
  const equity = pnl?.equity_usd ?? currentCash;
  const totalPnlUsd = pnl?.total_pnl_usd ?? 0;
  const totalPnlPct = pnl?.pnl_pct ?? 0;
  const realizedPnl = pnl?.realized_pnl_usd ?? 0;
  const unrealizedPnl = pnl?.unrealized_pnl_usd ?? 0;

  const totalTrades = pnl?.total_trades ?? 0;
  const winRate = pnl?.win_rate_pct ?? 0;
  const winningTrades = pnl?.winning_trades ?? 0;
  const losingTrades = pnl?.losing_trades ?? 0;

  const maxSlots = status?.settings?.max_concurrent_positions ?? 50;
  const activeCount = positions.filter((p) => p.status === "OPEN").length;
  const scalpPositions = positions.filter((p) => p.status === "OPEN" && p.strategy_type === "SCALP").length;
  const swingPositions = positions.filter((p) => p.status === "OPEN" && p.strategy_type === "SWING").length;

  const waitingCount = waitingTokens.length;

  // Scanner KPIs: se houver dados da sessão ativa, prioriza a sessão; senão, exibe o acumulado histórico do banco
  const hasSessionTokens = (scanner?.total_scanned ?? 0) > 0;
  const totalScanned = hasSessionTokens
    ? (scanner?.total_scanned ?? 0)
    : (scanner?.all_time_cataloged ?? scanner?.total_scanned ?? 0);
  const approvedCount = hasSessionTokens
    ? (scanner?.approved ?? scanner?.total_approved ?? 0)
    : (scanner?.all_time_approved ?? scanner?.approved ?? scanner?.total_approved ?? 0);
  const rejectedCount = hasSessionTokens
    ? (scanner?.rejected ?? scanner?.total_rejected ?? 0)
    : (scanner?.all_time_rejected ?? scanner?.rejected ?? scanner?.total_rejected ?? 0);

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3">
      {/* 1. Saldo / Equity */}
      <div className="p-4 rounded-xl bg-surface-card border border-border flex flex-col justify-between hover:border-border-light transition-colors">
        <div className="flex items-center justify-between text-gray-400">
          <span className="text-xs font-medium">Patrimônio / Caixa</span>
          <Wallet className="w-4 h-4 text-brand-400" />
        </div>
        <div className="mt-2">
          <div className="text-xl font-bold font-mono text-white tracking-tight">
            {formatUSD(equity)}
          </div>
          <div className="flex items-center justify-between text-[11px] text-gray-400 mt-1 font-mono">
            <span>Caixa: {formatUSD(currentCash)}</span>
            <span>Banca: {formatUSD(initialWallet)}</span>
          </div>
        </div>
      </div>

      {/* 2. PnL Total */}
      <div className="p-4 rounded-xl bg-surface-card border border-border flex flex-col justify-between hover:border-border-light transition-colors">
        <div className="flex items-center justify-between text-gray-400">
          <span className="text-xs font-medium">PnL Total da Sessão</span>
          <TrendingUp className={`w-4 h-4 ${totalPnlUsd >= 0 ? "text-profit" : "text-loss"}`} />
        </div>
        <div className="mt-2">
          <div className="flex items-baseline gap-2">
            <span
              className={`text-xl font-bold font-mono tracking-tight ${
                totalPnlUsd >= 0 ? "text-profit" : "text-loss"
              }`}
            >
              {formatUSD(totalPnlUsd)}
            </span>
            <span
              className={`text-xs font-mono font-semibold px-1.5 py-0.5 rounded ${
                totalPnlPct >= 0 ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"
              }`}
            >
              {formatPct(totalPnlPct)}
            </span>
          </div>
          <div className="flex items-center justify-between text-[11px] text-gray-400 mt-1 font-mono">
            <span>Realiz: {formatUSD(realizedPnl)}</span>
            <span>Flutuante: {formatUSD(unrealizedPnl)}</span>
          </div>
        </div>
      </div>

      {/* 3. Taxa de Acerto / Win Rate */}
      <div className="p-4 rounded-xl bg-surface-card border border-border flex flex-col justify-between hover:border-border-light transition-colors">
        <div className="flex items-center justify-between text-gray-400">
          <span className="text-xs font-medium">Taxa de Acerto</span>
          <Target className="w-4 h-4 text-emerald-400" />
        </div>
        <div className="mt-2">
          <div className="text-xl font-bold font-mono text-white tracking-tight">
            {totalTrades > 0 ? `${winRate.toFixed(1)}%` : "0.0%"}
          </div>
          <div className="flex items-center justify-between text-[11px] text-gray-400 mt-1 font-mono">
            <span>Trades: {totalTrades}</span>
            <span className="text-profit">W: {winningTrades}</span>
            <span className="text-loss">L: {losingTrades}</span>
          </div>
        </div>
      </div>

      {/* 4. Posições Abertas */}
      <div className="p-4 rounded-xl bg-surface-card border border-border flex flex-col justify-between hover:border-border-light transition-colors">
        <div className="flex items-center justify-between text-gray-400">
          <span className="text-xs font-medium">Posições Abertas</span>
          <Layers className="w-4 h-4 text-brand-400" />
        </div>
        <div className="mt-2">
          <div className="flex items-baseline gap-1.5">
            <span className="text-xl font-bold font-mono text-white tracking-tight">
              {activeCount}
            </span>
            <span className="text-xs text-gray-400 font-mono">/ {maxSlots} slots</span>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-gray-400 mt-1 font-mono">
            <span className="text-scalp">Scalp: {scalpPositions}</span>
            <span>•</span>
            <span className="text-swing">Swing: {swingPositions}</span>
          </div>
        </div>
      </div>

      {/* 5. Fila de Espera */}
      <div
        className={`p-4 rounded-xl border flex flex-col justify-between transition-colors ${
          waitingCount > 0
            ? "bg-amber-500/5 border-amber-500/30 hover:border-amber-500/50"
            : "bg-surface-card border border-border hover:border-border-light"
        }`}
      >
        <div className="flex items-center justify-between text-gray-400">
          <span className="text-xs font-medium">Fila de Espera</span>
          <Clock className={`w-4 h-4 ${waitingCount > 0 ? "text-amber-400 animate-spin" : "text-gray-400"}`} />
        </div>
        <div className="mt-2">
          <div className="flex items-baseline gap-2">
            <span
              className={`text-xl font-bold font-mono tracking-tight ${
                waitingCount > 0 ? "text-amber-400" : "text-white"
              }`}
            >
              {waitingCount}
            </span>
            <span className="text-xs text-gray-400">tokens</span>
          </div>
          <div className="text-[11px] text-gray-400 mt-1">
            {waitingCount > 0 ? (
              <span className="text-amber-300">Aguardando slots/saldo</span>
            ) : (
              "Sem espera pendente"
            )}
          </div>
        </div>
      </div>

      {/* 6. Scanner & Filtragem */}
      <div className="p-4 rounded-xl bg-surface-card border border-border flex flex-col justify-between hover:border-border-light transition-colors">
        <div className="flex items-center justify-between text-gray-400">
          <span className="text-xs font-medium">Scanner de Mercado</span>
          <SearchCheck className="w-4 h-4 text-brand-400" />
        </div>
        <div className="mt-2">
          <div className="flex items-baseline gap-2">
            <span className="text-xl font-bold font-mono text-white tracking-tight">
              {formatNumber(totalScanned)}
            </span>
            <span className="text-[10px] text-gray-400 font-mono">
              {hasSessionTokens ? "sessão" : "catálogo"}
            </span>
          </div>
          <div className="flex items-center justify-between text-[11px] text-gray-400 mt-1 font-mono">
            <span className="text-profit font-semibold">Aprov: {approvedCount}</span>
            <span className="text-loss font-semibold">Rejeit: {rejectedCount}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

