"use client";

import {
  LayoutDashboard,
  FlaskConical,
  Coins,
  Clock,
  BarChart3,
  Terminal,
  History,
  Briefcase,
  Settings,
  Shield,
  Zap,
  ChevronRight,
  TrendingUp,
} from "lucide-react";
import { BotStatusData } from "@/types/bot";

export type SidebarSection =
  | "DASHBOARD_REAL"
  | "SIMULATION"
  | "STRATEGIES"
  | "TOKENS"
  | "WAITING"
  | "ANALYTICS"
  | "LOGS"
  | "HISTORY"
  | "SETTINGS";

interface SidebarProps {
  currentSection: SidebarSection;
  onSelectSection: (section: SidebarSection) => void;
  status: BotStatusData | null;
  waitingCount: number;
  openPositionsCount: number;
  isOpenMobile: boolean;
  onCloseMobile: () => void;
}

export function Sidebar({
  currentSection,
  onSelectSection,
  status,
  waitingCount,
  openPositionsCount,
  isOpenMobile,
  onCloseMobile,
}: SidebarProps) {
  const isRunning = status?.is_running ?? false;
  const isPaused = status?.is_paused ?? false;
  const stratMode = status?.settings?.trading_strategy_mode || "DUAL";

  const handleNavClick = (sec: SidebarSection) => {
    onSelectSection(sec);
    onCloseMobile();
  };

  return (
    <>
      {/* Mobile Backdrop */}
      {isOpenMobile && (
        <div
          onClick={onCloseMobile}
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm md:hidden"
        />
      )}

      {/* Sidebar Container */}
      <aside
        className={`fixed md:sticky top-0 inset-y-0 left-0 z-50 w-64 bg-surface border-r border-border flex flex-col h-screen transform transition-transform duration-200 ease-in-out shrink-0 ${
          isOpenMobile ? "translate-x-0" : "-translate-x-full md:translate-x-0"
        }`}
      >
        {/* Brand Header */}
        <div className="h-16 px-5 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-brand-500/10 border border-brand-500/30 flex items-center justify-center text-brand-400">
              <Zap className="w-5 h-5 animate-pulse" />
            </div>
            <div>
              <div className="flex items-center gap-1">
                <span className="font-bold text-white tracking-wider text-sm">VERTEX</span>
                <span className="text-brand-400 font-mono text-xs font-semibold">.BOT</span>
              </div>
              <span className="text-[9px] text-gray-500 font-mono tracking-widest uppercase block">
                Trading Suite v2.0
              </span>
            </div>
          </div>
        </div>

        {/* Navigation Groups */}
        <nav className="flex-1 px-3 py-4 space-y-4 overflow-y-auto text-xs font-medium">
          {/* Grupo 1: Trading & Execução */}
          <div>
            <div className="px-3 pb-1.5 text-[10px] font-bold uppercase tracking-wider text-gray-500 font-mono">
              Trading & Execução
            </div>
            <div className="space-y-1">
              {/* 1. Operações Reais (Live) */}
              <button
                onClick={() => handleNavClick("DASHBOARD_REAL")}
                className={`w-full px-3 py-2.5 rounded-xl text-left flex items-center justify-between transition-all group ${
                  currentSection === "DASHBOARD_REAL"
                    ? "bg-emerald-600 text-white shadow-lg shadow-emerald-600/20 font-semibold"
                    : "text-gray-300 hover:text-white hover:bg-surface-card"
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <LayoutDashboard className="w-4 h-4" />
                  <span>Operações Reais (LIVE)</span>
                </div>
                <span
                  className={`text-[9px] px-1.5 py-0.5 rounded font-mono font-bold ${
                    currentSection === "DASHBOARD_REAL"
                      ? "bg-white/20 text-white"
                      : "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"
                  }`}
                >
                  LIVE
                </span>
              </button>

              {/* 2. Simulação (Paper) */}
              <button
                onClick={() => handleNavClick("SIMULATION")}
                className={`w-full px-3 py-2.5 rounded-xl text-left flex items-center justify-between transition-all group ${
                  currentSection === "SIMULATION"
                    ? "bg-brand-500 text-white shadow-lg shadow-brand-500/20 font-semibold"
                    : "text-gray-300 hover:text-white hover:bg-surface-card"
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <FlaskConical className="w-4 h-4" />
                  <span>Simulação (PAPER)</span>
                </div>

                <div className="flex items-center gap-1.5">
                  {openPositionsCount > 0 && (
                    <span
                      className={`text-[10px] font-mono px-1.5 py-0.2 rounded-full ${
                        currentSection === "SIMULATION" ? "bg-white/20 text-white" : "bg-brand-500/20 text-brand-300"
                      }`}
                    >
                      {openPositionsCount}
                    </span>
                  )}
                  <span
                    className={`text-[9px] px-1.5 py-0.5 rounded font-mono font-bold ${
                      currentSection === "SIMULATION"
                        ? "bg-white/20 text-white"
                        : "bg-profit/15 text-profit border border-profit/30"
                    }`}
                  >
                    Ativo
                  </span>
                </div>
              </button>

              {/* 3. Estratégias */}
              <button
                onClick={() => handleNavClick("STRATEGIES")}
                className={`w-full px-3 py-2.5 rounded-xl text-left flex items-center justify-between transition-all group ${
                  currentSection === "STRATEGIES"
                    ? "bg-brand-500/15 text-brand-300 border border-brand-500/40"
                    : "text-gray-400 hover:text-gray-200 hover:bg-surface-card"
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <TrendingUp className="w-4 h-4 text-swing" />
                  <span>Estratégias</span>
                </div>
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-surface-card border border-border text-brand-300 font-mono">
                  {stratMode}
                </span>
              </button>
            </div>
          </div>

          {/* Grupo 2: Mercado & Triagem */}
          <div>
            <div className="px-3 pb-1.5 text-[10px] font-bold uppercase tracking-wider text-gray-500 font-mono">
              Mercado & Triagem
            </div>
            <div className="space-y-1">
              {/* 4. Tokens */}
              <button
                onClick={() => handleNavClick("TOKENS")}
                className={`w-full px-3 py-2.5 rounded-xl text-left flex items-center justify-between transition-all group ${
                  currentSection === "TOKENS"
                    ? "bg-brand-500/15 text-brand-300 border border-brand-500/40"
                    : "text-gray-400 hover:text-gray-200 hover:bg-surface-card"
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <Coins className="w-4 h-4" />
                  <span>Tokens & Triagem</span>
                </div>
              </button>

              {/* 5. Fila de espera */}
              <button
                onClick={() => handleNavClick("WAITING")}
                className={`w-full px-3 py-2.5 rounded-xl text-left flex items-center justify-between transition-all group ${
                  currentSection === "WAITING"
                    ? "bg-amber-500/15 text-amber-300 border border-amber-500/40"
                    : "text-gray-400 hover:text-gray-200 hover:bg-surface-card"
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <Clock className="w-4 h-4" />
                  <span>Fila de Espera</span>
                </div>
                {waitingCount > 0 && (
                  <span className="text-[10px] px-2 py-0.2 rounded-full font-mono bg-amber-500/20 text-amber-300 font-bold border border-amber-500/40 animate-pulse">
                    {waitingCount}
                  </span>
                )}
              </button>
            </div>
          </div>

          {/* Grupo 3: Inteligência & Auditoria */}
          <div>
            <div className="px-3 pb-1.5 text-[10px] font-bold uppercase tracking-wider text-gray-500 font-mono">
              Inteligência & Auditoria
            </div>
            <div className="space-y-1">
              {/* 6. Analytics */}
              <button
                onClick={() => handleNavClick("ANALYTICS")}
                className={`w-full px-3 py-2.5 rounded-xl text-left flex items-center justify-between transition-all group ${
                  currentSection === "ANALYTICS"
                    ? "bg-brand-500/15 text-brand-300 border border-brand-500/40"
                    : "text-gray-400 hover:text-gray-200 hover:bg-surface-card"
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <BarChart3 className="w-4 h-4" />
                  <span>Analytics</span>
                </div>
              </button>

              {/* 7. Logs */}
              <button
                onClick={() => handleNavClick("LOGS")}
                className={`w-full px-3 py-2.5 rounded-xl text-left flex items-center justify-between transition-all group ${
                  currentSection === "LOGS"
                    ? "bg-brand-500/15 text-brand-300 border border-brand-500/40"
                    : "text-gray-400 hover:text-gray-200 hover:bg-surface-card"
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <Terminal className="w-4 h-4" />
                  <span>Logs do Sistema</span>
                </div>
              </button>

              {/* 8. Histórico */}
              <button
                onClick={() => handleNavClick("HISTORY")}
                className={`w-full px-3 py-2.5 rounded-xl text-left flex items-center justify-between transition-all group ${
                  currentSection === "HISTORY"
                    ? "bg-brand-500/15 text-brand-300 border border-brand-500/40"
                    : "text-gray-400 hover:text-gray-200 hover:bg-surface-card"
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <History className="w-4 h-4" />
                  <span>Histórico de Trades</span>
                </div>
              </button>
            </div>
          </div>

          {/* Grupo 4: Gestão & Sistema */}
          <div>
            <div className="px-3 pb-1.5 text-[10px] font-bold uppercase tracking-wider text-gray-500 font-mono">
              Gestão & Sistema
            </div>
            <div className="space-y-1">
              {/* 9. Settings */}
              <button
                onClick={() => handleNavClick("SETTINGS")}
                className={`w-full px-3 py-2.5 rounded-xl text-left flex items-center justify-between transition-all group ${
                  currentSection === "SETTINGS"
                    ? "bg-brand-500/15 text-brand-300 border border-brand-500/40"
                    : "text-gray-400 hover:text-gray-200 hover:bg-surface-card"
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <Settings className="w-4 h-4" />
                  <span>Settings / Ajustes</span>
                </div>
              </button>
            </div>
          </div>
        </nav>

        {/* Footer do Menu Lateral */}
        <div className="p-3 border-t border-border bg-surface-card/60 text-[11px] font-mono text-gray-400 space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-gray-500">Rede:</span>
            <span className="text-emerald-400 font-bold flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
              Solana Mainnet
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-gray-500">Ambiente:</span>
            <span className="px-1.5 py-0.5 rounded bg-brand-500/10 text-brand-300 border border-brand-500/20 text-[10px] font-bold">
              Paper Sandbox
            </span>
          </div>
        </div>
      </aside>
    </>
  );
}

