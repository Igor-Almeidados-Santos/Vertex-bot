"use client";

import { BarChart3, TrendingUp, Target, Award, ArrowUpRight, ArrowDownRight, Layers } from "lucide-react";
import { PositionItem, SummaryData } from "@/types/bot";
import { formatUSD, formatPct } from "@/lib/formatters";

interface AnalyticsViewProps {
  summary: SummaryData | null;
  positions: PositionItem[];
}

export function AnalyticsView({ summary, positions }: AnalyticsViewProps) {
  const pnl = summary?.pnl;
  const closedPositions = positions.filter((p) => p.status === "CLOSED");

  const totalTrades = pnl?.total_trades ?? closedPositions.length;
  const winRate = pnl?.win_rate_pct ?? 0;
  const winningTrades = pnl?.winning_trades ?? closedPositions.filter((p) => (p.realized_pnl_usd || 0) > 0).length;
  const losingTrades = pnl?.losing_trades ?? closedPositions.filter((p) => (p.realized_pnl_usd || 0) <= 0).length;

  // Calculo de trades por estratégia
  const scalpTrades = closedPositions.filter((p) => p.strategy_type === "SCALP");
  const swingTrades = closedPositions.filter((p) => p.strategy_type === "SWING");

  const scalpWins = scalpTrades.filter((p) => (p.realized_pnl_usd || 0) > 0).length;
  const swingWins = swingTrades.filter((p) => (p.realized_pnl_usd || 0) > 0).length;

  const scalpPnl = scalpTrades.reduce((acc, p) => acc + (p.realized_pnl_usd || 0), 0);
  const swingPnl = swingTrades.reduce((acc, p) => acc + (p.realized_pnl_usd || 0), 0);

  return (
    <div className="space-y-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="p-6 rounded-2xl border border-border bg-surface-card">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-brand-500/10 border border-brand-500/30 text-brand-400">
            <BarChart3 className="w-6 h-6" />
          </div>
          <div>
            <h2 className="text-xl font-bold text-white tracking-tight">
              Inteligência Quantitativa & Analytics
            </h2>
            <p className="text-xs text-gray-400 mt-0.5">
              Análise de rendimento, distribuição de retornos e performance comparativa entre estratégias
            </p>
          </div>
        </div>
      </div>

      {/* Grid de Performance Geral */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Win Rate */}
        <div className="p-4 rounded-xl border border-border bg-surface-card flex flex-col justify-between">
          <div className="flex items-center justify-between text-gray-400 text-xs">
            <span>Taxa de Acerto Global</span>
            <Target className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="mt-3">
            <div className="text-2xl font-bold font-mono text-white">
              {winRate.toFixed(1)}%
            </div>
            <div className="text-[11px] text-gray-400 font-mono mt-1">
              {winningTrades} vitórias / {losingTrades} derrotas
            </div>
          </div>
        </div>

        {/* PnL Realizado */}
        <div className="p-4 rounded-xl border border-border bg-surface-card flex flex-col justify-between">
          <div className="flex items-center justify-between text-gray-400 text-xs">
            <span>PnL Realizado Total</span>
            <TrendingUp className={`w-4 h-4 ${(pnl?.realized_pnl_usd || 0) >= 0 ? "text-profit" : "text-loss"}`} />
          </div>
          <div className="mt-3">
            <div
              className={`text-2xl font-bold font-mono ${
                (pnl?.realized_pnl_usd || 0) >= 0 ? "text-profit" : "text-loss"
              }`}
            >
              {formatUSD(pnl?.realized_pnl_usd)}
            </div>
            <div className="text-[11px] text-gray-400 font-mono mt-1">
              Retorno: {formatPct(pnl?.pnl_pct)}
            </div>
          </div>
        </div>

        {/* Scalp PnL */}
        <div className="p-4 rounded-xl border border-scalp/20 bg-surface-card flex flex-col justify-between">
          <div className="flex items-center justify-between text-scalp text-xs">
            <span>Performance SCALP</span>
            <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-scalp/15 border border-scalp/30">
              {scalpTrades.length} trades
            </span>
          </div>
          <div className="mt-3">
            <div className={`text-2xl font-bold font-mono ${scalpPnl >= 0 ? "text-profit" : "text-loss"}`}>
              {formatUSD(scalpPnl)}
            </div>
            <div className="text-[11px] text-gray-400 font-mono mt-1">
              Acerto: {scalpTrades.length > 0 ? `${((scalpWins / scalpTrades.length) * 100).toFixed(1)}%` : "0.0%"}
            </div>
          </div>
        </div>

        {/* Swing PnL */}
        <div className="p-4 rounded-xl border border-swing/20 bg-surface-card flex flex-col justify-between">
          <div className="flex items-center justify-between text-swing text-xs">
            <span>Performance SWING</span>
            <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-swing/15 border border-swing/30">
              {swingTrades.length} trades
            </span>
          </div>
          <div className="mt-3">
            <div className={`text-2xl font-bold font-mono ${swingPnl >= 0 ? "text-profit" : "text-loss"}`}>
              {formatUSD(swingPnl)}
            </div>
            <div className="text-[11px] text-gray-400 font-mono mt-1">
              Acerto: {swingTrades.length > 0 ? `${((swingWins / swingTrades.length) * 100).toFixed(1)}%` : "0.0%"}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

