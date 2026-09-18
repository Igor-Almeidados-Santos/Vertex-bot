"use client";

import { useState } from "react";
import {
  Layers,
  Clock,
  CheckCircle2,
  ListOrdered,
  BookOpen,
  ShieldAlert,
  Menu,
} from "lucide-react";
import { useBotData } from "@/hooks/useBotData";
import { Sidebar, SidebarSection } from "@/components/Sidebar";
import { Header } from "@/components/Header";
import { KpiGrid } from "@/components/KpiGrid";
import { ActivePositionsTab } from "@/components/tabs/ActivePositionsTab";
import { ClosedPositionsTab } from "@/components/tabs/ClosedPositionsTab";
import { WaitingQueueTab } from "@/components/tabs/WaitingQueueTab";
import { OrdersTab } from "@/components/tabs/OrdersTab";
import { TradeHistoryTab } from "@/components/tabs/TradeHistoryTab";
import { TokenCatalogTab } from "@/components/tabs/TokenCatalogTab";
import { RejectionsTab } from "@/components/tabs/RejectionsTab";
import { IncubatorTab } from "@/components/tabs/IncubatorTab";
import { SystemLogsTab } from "@/components/tabs/SystemLogsTab";
import { RealDashboardView } from "@/components/views/RealDashboardView";
import { StrategiesView } from "@/components/views/StrategiesView";
import { AnalyticsView } from "@/components/views/AnalyticsView";
import { ConfigModal } from "@/components/modals/ConfigModal";
import { RestartModal } from "@/components/modals/RestartModal";

type SimSubTab = "OPEN_POSITIONS" | "CLOSED_POSITIONS" | "WAITING_QUEUE" | "ORDERS";
type TokensSubTab = "CATALOG" | "REJECTIONS" | "INCUBATOR";

export default function DashboardPage() {
  const {
    status,
    summary,
    positions,
    waitingTokens,
    recentOrders,
    logs,
    isLoading,
    isWsConnected,
    lastUpdated,
    refreshData,
    handleTogglePause,
    handleStartBot,
    clearLogs,
  } = useBotData();

  // Navigation State
  const [currentSection, setCurrentSection] = useState<SidebarSection>("SIMULATION");
  const [simSubTab, setSimSubTab] = useState<SimSubTab>("OPEN_POSITIONS");
  const [tokensSubTab, setTokensSubTab] = useState<TokensSubTab>("CATALOG");
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);

  // Modals
  const [isConfigOpen, setIsConfigOpen] = useState(false);
  const [isRestartOpen, setIsRestartOpen] = useState(false);

  const activePositions = positions.filter((p) => p.status === "OPEN");
  const closedPositions = positions.filter((p) => p.status === "CLOSED");

  return (
    <div className="min-h-screen flex bg-background text-gray-100">
      {/* 1. SIDEBAR VERTICAL À ESQUERDA (9 Abas conforme Step 3294) */}
      <Sidebar
        currentSection={currentSection}
        onSelectSection={(sec) => {
          if (sec === "SETTINGS") {
            setIsConfigOpen(true);
          } else {
            setCurrentSection(sec);
          }
        }}
        status={status}
        waitingCount={waitingTokens.length}
        openPositionsCount={activePositions.length}
        isOpenMobile={isMobileSidebarOpen}
        onCloseMobile={() => setIsMobileSidebarOpen(false)}
      />

      {/* 2. WRAPPER PRINCIPAL À DIREITA */}
      <div className="flex-1 flex flex-col min-w-0 min-h-screen">
        {/* Mobile menu trigger bar */}
        <div className="md:hidden flex items-center justify-between p-3 border-b border-border bg-surface">
          <button
            onClick={() => setIsMobileSidebarOpen(true)}
            className="p-2 rounded-lg bg-surface-card border border-border text-gray-300"
          >
            <Menu className="w-5 h-5" />
          </button>
          <span className="font-bold text-sm text-white">VERTEX-BOT</span>
          <div className="w-9" />
        </div>

        {/* Top Header */}
        <Header
          status={status}
          summary={summary}
          isWsConnected={isWsConnected}
          onTogglePause={handleTogglePause}
          onOpenConfig={() => setIsConfigOpen(true)}
          onOpenRestart={() => setIsRestartOpen(true)}
          onRefresh={refreshData}
        />

        {/* Main Content Area */}
        <main className="flex-1 max-w-7xl w-full mx-auto p-4 sm:p-6 space-y-6">
          {/* ==================================================== */}
          {/* SEÇÃO 1: DASHBOARD (REAL / LIVE TRADING)              */}
          {/* ==================================================== */}
          {currentSection === "DASHBOARD_REAL" && <RealDashboardView />}

          {/* ==================================================== */}
          {/* SEÇÃO 2: SIMULAÇÃO (PAPER TRADING) — INTERFACE COMPLETA */}
          {/* ==================================================== */}
          {currentSection === "SIMULATION" && (
            <div className="space-y-6">
              {/* Grade de KPIs */}
              <KpiGrid
                status={status}
                summary={summary}
                positions={positions}
                waitingTokens={waitingTokens}
              />

              {/* Sub-Abas da Simulação (Step 1687 & Step 3294) */}
              <div className="border-b border-border">
                <nav className="flex items-center gap-2 overflow-x-auto pb-1 text-xs">
                  {/* Posições Abertas */}
                  <button
                    onClick={() => setSimSubTab("OPEN_POSITIONS")}
                    className={`flex items-center gap-2 px-4 py-2.5 rounded-xl font-medium transition-all ${
                      simSubTab === "OPEN_POSITIONS"
                        ? "bg-brand-500 text-white shadow-md shadow-brand-500/20"
                        : "text-gray-400 hover:text-white hover:bg-surface-card"
                    }`}
                  >
                    <Layers className="w-4 h-4" />
                    <span>Posições Abertas</span>
                    <span
                      className={`px-1.5 py-0.2 rounded-full text-[10px] font-mono ${
                        simSubTab === "OPEN_POSITIONS"
                          ? "bg-white/20 text-white"
                          : "bg-surface-card text-gray-400"
                      }`}
                    >
                      {activePositions.length}
                    </span>
                  </button>

                  {/* Posições Fechadas (Step 1687) */}
                  <button
                    onClick={() => setSimSubTab("CLOSED_POSITIONS")}
                    className={`flex items-center gap-2 px-4 py-2.5 rounded-xl font-medium transition-all ${
                      simSubTab === "CLOSED_POSITIONS"
                        ? "bg-brand-500 text-white shadow-md shadow-brand-500/20"
                        : "text-gray-400 hover:text-white hover:bg-surface-card"
                    }`}
                  >
                    <CheckCircle2 className="w-4 h-4" />
                    <span>Posições Fechadas (100% Vendidas)</span>
                    <span
                      className={`px-1.5 py-0.2 rounded-full text-[10px] font-mono ${
                        simSubTab === "CLOSED_POSITIONS"
                          ? "bg-white/20 text-white"
                          : "bg-surface-card text-gray-400"
                      }`}
                    >
                      {closedPositions.length}
                    </span>
                  </button>

                  {/* Fila de Espera */}
                  <button
                    onClick={() => setSimSubTab("WAITING_QUEUE")}
                    className={`flex items-center gap-2 px-4 py-2.5 rounded-xl font-medium transition-all ${
                      simSubTab === "WAITING_QUEUE"
                        ? "bg-amber-500 text-black font-semibold shadow-md shadow-amber-500/20"
                        : "text-gray-400 hover:text-white hover:bg-surface-card"
                    }`}
                  >
                    <Clock className="w-4 h-4" />
                    <span>Fila de Espera</span>
                    {waitingTokens.length > 0 && (
                      <span
                        className={`px-1.5 py-0.2 rounded-full text-[10px] font-mono ${
                          simSubTab === "WAITING_QUEUE"
                            ? "bg-black/30 text-black"
                            : "bg-amber-500/20 text-amber-300 font-bold"
                        }`}
                      >
                        {waitingTokens.length}
                      </span>
                    )}
                  </button>

                  {/* Ordens Executadas */}
                  <button
                    onClick={() => setSimSubTab("ORDERS")}
                    className={`flex items-center gap-2 px-4 py-2.5 rounded-xl font-medium transition-all ${
                      simSubTab === "ORDERS"
                        ? "bg-brand-500 text-white shadow-md shadow-brand-500/20"
                        : "text-gray-400 hover:text-white hover:bg-surface-card"
                    }`}
                  >
                    <ListOrdered className="w-4 h-4" />
                    <span>Ordens Executadas</span>
                    <span
                      className={`px-1.5 py-0.2 rounded-full text-[10px] font-mono ${
                        simSubTab === "ORDERS"
                          ? "bg-white/20 text-white"
                          : "bg-surface-card text-gray-400"
                      }`}
                    >
                      {recentOrders.length}
                    </span>
                  </button>
                </nav>
              </div>

              {/* Renderizador das Sub-Abas */}
              <div>
                {simSubTab === "OPEN_POSITIONS" && <ActivePositionsTab positions={positions} onRefresh={refreshData} />}
                {simSubTab === "CLOSED_POSITIONS" && <ClosedPositionsTab positions={positions} />}
                {simSubTab === "WAITING_QUEUE" && <WaitingQueueTab waitingTokens={waitingTokens} />}
                {simSubTab === "ORDERS" && <OrdersTab orders={recentOrders} />}
              </div>
            </div>
          )}

          {/* ==================================================== */}
          {/* SEÇÃO 3: ESTRATÉGIAS (Step 3837)                      */}
          {/* ==================================================== */}
          {currentSection === "STRATEGIES" && (
            <StrategiesView
              settings={status?.settings}
              onOpenConfig={() => setIsConfigOpen(true)}
            />
          )}

          {/* ==================================================== */}
          {/* SEÇÃO 4: TOKENS & TRIAGEM                             */}
          {/* ==================================================== */}
          {currentSection === "TOKENS" && (
            <div className="space-y-4">
              <div className="flex items-center gap-2 border-b border-border pb-2 text-xs">
                <button
                  onClick={() => setTokensSubTab("CATALOG")}
                  className={`px-3 py-1.5 rounded-lg font-medium transition-all flex items-center gap-1.5 ${
                    tokensSubTab === "CATALOG"
                      ? "bg-brand-500 text-white"
                      : "text-gray-400 hover:text-white hover:bg-surface-card"
                  }`}
                >
                  <BookOpen className="w-4 h-4" />
                  <span>Catálogo de Tokens</span>
                </button>
                <button
                  onClick={() => setTokensSubTab("REJECTIONS")}
                  className={`px-3 py-1.5 rounded-lg font-medium transition-all flex items-center gap-1.5 ${
                    tokensSubTab === "REJECTIONS"
                      ? "bg-loss text-white"
                      : "text-gray-400 hover:text-white hover:bg-surface-card"
                  }`}
                >
                  <ShieldAlert className="w-4 h-4" />
                  <span>Auditoria de Rejeições (Hard Gates)</span>
                </button>
                <button
                  onClick={() => setTokensSubTab("INCUBATOR")}
                  className={`px-3 py-1.5 rounded-lg font-medium transition-all flex items-center gap-1.5 ${
                    tokensSubTab === "INCUBATOR"
                      ? "bg-sky-600 text-white"
                      : "text-gray-400 hover:text-white hover:bg-surface-card"
                  }`}
                >
                  <Clock className="w-4 h-4" />
                  <span>Tokens na Incubadora</span>
                  {waitingTokens.length > 0 && (
                    <span className="ml-1 px-1.5 py-0.2 rounded-full text-[10px] bg-sky-500/20 text-sky-300 font-bold border border-sky-500/40">
                      {waitingTokens.length}
                    </span>
                  )}
                </button>
              </div>

              {tokensSubTab === "CATALOG" && <TokenCatalogTab />}
              {tokensSubTab === "REJECTIONS" && <RejectionsTab />}
              {tokensSubTab === "INCUBATOR" && <IncubatorTab waitingTokens={waitingTokens} />}
            </div>
          )}

          {/* ==================================================== */}
          {/* SEÇÃO 5: FILA DE ESPERA (VISÃO COMPLETA DEDICADA)    */}
          {/* ==================================================== */}
          {currentSection === "WAITING" && <WaitingQueueTab waitingTokens={waitingTokens} />}

          {/* ==================================================== */}
          {/* SEÇÃO 6: ANALYTICS                                   */}
          {/* ==================================================== */}
          {currentSection === "ANALYTICS" && (
            <AnalyticsView summary={summary} positions={positions} />
          )}

          {/* ==================================================== */}
          {/* SEÇÃO 7: LOGS DO SISTEMA                              */}
          {/* ==================================================== */}
          {currentSection === "LOGS" && <SystemLogsTab logs={logs} onClearLogs={clearLogs} />}

          {/* ==================================================== */}
          {/* SEÇÃO 8: HISTÓRICO DE TRADES                         */}
          {/* ==================================================== */}
          {currentSection === "HISTORY" && <TradeHistoryTab positions={positions} />}
        </main>

        {/* Footer */}
        <footer className="border-t border-border bg-surface/50 py-3 px-6 text-center text-xs text-gray-500 flex flex-col sm:flex-row items-center justify-between gap-2 mt-auto">
          <span>Vertex-bot Architecture • 4 Layers Async Pipeline • Next.js v15 Suite</span>
          <span className="font-mono text-[11px]">
            Última sincronização: {lastUpdated.toLocaleTimeString("pt-BR")}
          </span>
        </footer>
      </div>

      {/* Modais Globais */}
      <ConfigModal
        isOpen={isConfigOpen}
        onClose={() => setIsConfigOpen(false)}
        currentSettings={status?.settings}
        onSaved={refreshData}
      />

      <RestartModal
        isOpen={isRestartOpen}
        onClose={() => setIsRestartOpen(false)}
        currentInitialWallet={summary?.pnl?.initial_wallet_usd ?? status?.initial_wallet_usd ?? 10.0}
        onRestarted={refreshData}
      />
    </div>
  );
}
