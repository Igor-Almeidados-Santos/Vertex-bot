"use client";

import { useState } from "react";
import {
  Activity,
  Play,
  Pause,
  RotateCcw,
  Settings,
  RefreshCw,
  Zap,
  ShieldCheck,
  Wifi,
  WifiOff,
} from "lucide-react";
import { BotStatusData, SummaryData } from "@/types/bot";
import { formatUSD, formatPct } from "@/lib/formatters";

interface HeaderProps {
  status: BotStatusData | null;
  summary: SummaryData | null;
  isWsConnected: boolean;
  onTogglePause: () => Promise<void>;
  onOpenConfig: () => void;
  onOpenRestart: () => void;
  onRefresh: () => void;
}

export function Header({
  status,
  summary,
  isWsConnected,
  onTogglePause,
  onOpenConfig,
  onOpenRestart,
  onRefresh,
}: HeaderProps) {
  const [isToggling, setIsToggling] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const isRunning = status?.is_running ?? false;
  const isPaused = status?.is_paused ?? false;
  const executionMode = status?.settings?.execution_mode || "PAPER";
  const strategyMode = status?.settings?.trading_strategy_mode || "DUAL";

  const walletCash = summary?.pnl?.current_cash_usd ?? status?.wallet_balance_usd ?? 0;
  const totalPnlUsd = summary?.pnl?.total_pnl_usd ?? 0;
  const totalPnlPct = summary?.pnl?.pnl_pct ?? 0;

  const handlePauseClick = async () => {
    try {
      setIsToggling(true);
      await onTogglePause();
    } finally {
      setIsToggling(false);
    }
  };

  const handleRefreshClick = async () => {
    setIsRefreshing(true);
    await onRefresh();
    setTimeout(() => setIsRefreshing(false), 500);
  };

  return (
    <header className="sticky top-0 z-40 w-full border-b border-border bg-surface/90 backdrop-blur-md px-4 py-3 sm:px-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        {/* Brand & Bot State */}
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-brand-500/10 border border-brand-500/30 text-brand-400">
            <Zap className="w-5 h-5 animate-pulse" />
          </div>

          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-bold tracking-tight text-white flex items-center gap-1.5">
                VERTEX<span className="text-brand-400 font-mono text-sm font-light">BOT</span>
              </h1>

              {/* Status Badge */}
              <div
                className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium border ${
                  !isRunning
                    ? "bg-loss/10 border-loss/30 text-loss"
                    : isPaused
                    ? "bg-amber-500/10 border-amber-500/30 text-amber-400"
                    : "bg-profit/10 border-profit/30 text-profit"
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    !isRunning
                      ? "bg-loss"
                      : isPaused
                      ? "bg-amber-400 animate-ping"
                      : "bg-profit animate-pulse"
                  }`}
                />
                {!isRunning ? "Offline" : isPaused ? "Pausado" : "Online"}
              </div>

              {/* WS Badge */}
              <span
                title={isWsConnected ? "WebSocket Conectado (Live Stream)" : "WebSocket Desconectado"}
                className={`flex items-center text-xs px-1.5 py-0.5 rounded border ${
                  isWsConnected
                    ? "border-profit/30 bg-profit/10 text-profit"
                    : "border-border bg-surface-card text-gray-400"
                }`}
              >
                {isWsConnected ? <Wifi className="w-3 h-3" /> : <WifiOff className="w-3 h-3" />}
              </span>
            </div>

            {/* Sub-badges */}
            <div className="flex items-center gap-2 mt-0.5 text-xs">
              <span className="px-1.5 py-0.2 rounded bg-surface-card border border-border text-gray-300 font-mono">
                {executionMode === "LIVE" ? (
                  <span className="text-loss font-semibold">LIVE TRADING</span>
                ) : (
                  <span className="text-scalp">PAPER TRADING</span>
                )}
              </span>

              <span
                className={`px-1.5 py-0.2 rounded border font-mono ${
                  strategyMode === "DUAL"
                    ? "bg-gradient-to-r from-scalp/10 to-swing/10 border-brand-500/40 text-brand-300"
                    : strategyMode === "SWING_ONLY"
                    ? "bg-swing/10 border-swing/30 text-swing"
                    : "bg-scalp/10 border-scalp/30 text-scalp"
                }`}
              >
                MODO: {strategyMode}
              </span>
            </div>
          </div>
        </div>

        {/* Financial Highlights & Actions */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Caixa Saldo */}
          <div className="flex flex-col items-end px-3 py-1.5 rounded-lg bg-surface-card/60 border border-border">
            <span className="text-[10px] text-gray-400 uppercase tracking-wider">Caixa Disponível</span>
            <span className="font-mono font-bold text-sm sm:text-base text-white">
              {formatUSD(walletCash)}
            </span>
          </div>

          {/* PnL Total */}
          <div className="flex flex-col items-end px-3 py-1.5 rounded-lg bg-surface-card/60 border border-border">
            <span className="text-[10px] text-gray-400 uppercase tracking-wider">PnL Acumulado</span>
            <div className="flex items-center gap-1.5 font-mono font-bold text-sm sm:text-base">
              <span className={totalPnlUsd >= 0 ? "text-profit" : "text-loss"}>
                {formatUSD(totalPnlUsd)}
              </span>
              <span
                className={`text-xs px-1 rounded ${
                  totalPnlPct >= 0
                    ? "bg-profit/10 text-profit"
                    : "bg-loss/10 text-loss"
                }`}
              >
                {formatPct(totalPnlPct)}
              </span>
            </div>
          </div>

          {/* Botões de Ação */}
          <div className="flex items-center gap-1.5 border-l border-border pl-2">
            {/* Pause / Resume */}
            <button
              onClick={handlePauseClick}
              disabled={isToggling || !isRunning}
              title={isPaused ? "Retomar Operações" : "Pausar Operações"}
              className={`p-2 rounded-lg border transition-all text-xs flex items-center gap-1.5 font-medium ${
                isPaused
                  ? "bg-profit/10 border-profit/40 text-profit hover:bg-profit/20"
                  : "bg-amber-500/10 border-amber-500/40 text-amber-400 hover:bg-amber-500/20"
              } disabled:opacity-40 disabled:cursor-not-allowed`}
            >
              {isPaused ? <Play className="w-4 h-4" /> : <Pause className="w-4 h-4" />}
              <span className="hidden sm:inline">{isPaused ? "Retomar" : "Pausar"}</span>
            </button>

            {/* Reiniciar */}
            <button
              onClick={onOpenRestart}
              title="Reiniciar Simulação & Banca"
              className="p-2 rounded-lg border border-border bg-surface-card hover:bg-surface-hover text-gray-300 hover:text-white transition-all text-xs flex items-center gap-1.5"
            >
              <RotateCcw className="w-4 h-4" />
              <span className="hidden sm:inline">Reiniciar</span>
            </button>

            {/* Configurações */}
            <button
              onClick={onOpenConfig}
              title="Configurações Estratégicas"
              className="p-2 rounded-lg border border-brand-500/40 bg-brand-500/10 hover:bg-brand-500/20 text-brand-300 hover:text-white transition-all text-xs flex items-center gap-1.5"
            >
              <Settings className="w-4 h-4" />
              <span className="hidden sm:inline">Config</span>
            </button>

            {/* Refresh */}
            <button
              onClick={handleRefreshClick}
              title="Atualizar Dados Agora"
              className="p-2 rounded-lg border border-border bg-surface-card hover:bg-surface-hover text-gray-400 hover:text-white transition-all"
            >
              <RefreshCw className={`w-4 h-4 ${isRefreshing ? "animate-spin" : ""}`} />
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}

