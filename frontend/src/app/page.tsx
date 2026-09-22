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
  Play,
  Pause,
  RotateCcw,
  DollarSign,
  Square,
  FlaskConical,
  X,
  Star,
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
import { PriorityPoolTab } from "@/components/tabs/PriorityPoolTab";
import { RealDashboardView } from "@/components/views/RealDashboardView";
import { StrategiesView } from "@/components/views/StrategiesView";
import { AnalyticsView } from "@/components/views/AnalyticsView";
import { ConfigModal } from "@/components/modals/ConfigModal";
import { RestartModal } from "@/components/modals/RestartModal";
import { depositCash, stopBot, startBot, pauseBot, resumeBot } from "@/lib/api";

type SimSubTab = "OPEN_POSITIONS" | "CLOSED_POSITIONS" | "PRIORITY_POOL" | "WAITING_QUEUE" | "ORDERS";
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
  // Modals & Simulation Controls
  const [isConfigOpen, setIsConfigOpen] = useState(false);
  const [isRestartOpen, setIsRestartOpen] = useState(false);
  const [isDepositOpen, setIsDepositOpen] = useState(false);
  const [depositAmount, setDepositAmount] = useState(10);
  const [isDepositLoading, setIsDepositLoading] = useState(false);
  const [isSimActionLoading, setIsSimActionLoading] = useState(false);

  const isPaperRunning = status?.modes?.paper?.running ?? (status?.is_running && status?.settings?.execution_mode !== "LIVE");
  const isPaperPaused = status?.modes?.paper?.paused ?? status?.is_paused ?? false;

  const handleStartPaper = async () => {
    try {
      setIsSimActionLoading(true);
      await startBot("paper");
      await refreshData();
    } catch (err) {
      console.error("Erro ao iniciar simulação:", err);
    } finally {
      setIsSimActionLoading(false);
    }
  };

  const handlePausePaper = async () => {
    try {
      setIsSimActionLoading(true);
      await pauseBot("paper");
      await refreshData();
    } catch (err) {
      console.error("Erro ao pausar simulação:", err);
    } finally {
      setIsSimActionLoading(false);
    }
  };

  const handleResumePaper = async () => {
    try {
      setIsSimActionLoading(true);
      await resumeBot("paper");
      await refreshData();
    } catch (err) {
      console.error("Erro ao retomar simulação:", err);
    } finally {
      setIsSimActionLoading(false);
    }
  };

  const handleStopPaper = async () => {
    if (!confirm("Tem certeza que deseja encerrar a simulação? Isto irá zerar a carteira simulada e limpar as posições de teste, mantendo o bot ativo.")) {
      return;
    }
    try {
      setIsSimActionLoading(true);
      await stopBot("paper");
      await refreshData();
    } catch (err) {
      console.error("Erro ao encerrar simulação:", err);
    } finally {
      setIsSimActionLoading(false);
    }
  };

  const handleConfirmDeposit = async () => {
    if (depositAmount <= 0) return;
    try {
      setIsDepositLoading(true);
      await depositCash(depositAmount);
      await refreshData();
      setIsDepositOpen(false);
    } catch (err) {
      console.error("Erro ao depositar saldo simulado:", err);
    } finally {
      setIsDepositLoading(false);
    }
  };

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
              {/* Barra de Controle Operacional Exclusiva da Simulação */}
              <div className="bg-surface-card border border-border rounded-2xl p-4 shadow-sm flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-scalp/10 border border-scalp/30 flex items-center justify-center text-scalp">
                    <FlaskConical className="w-5 h-5" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-base font-bold text-white">Ambiente de Simulação (Paper Trading)</h2>
                      <span
                        className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium border ${
                          !isPaperRunning
                            ? "bg-gray-800 text-gray-400 border-gray-700"
                            : isPaperPaused
                            ? "bg-amber-500/10 text-amber-400 border-amber-500/30"
                            : "bg-scalp/10 text-scalp border-scalp/30"
                        }`}
                      >
                        <span
                          className={`w-1.5 h-1.5 rounded-full ${
                            !isPaperRunning
                              ? "bg-gray-500"
                              : isPaperPaused
                              ? "bg-amber-400 animate-ping"
                              : "bg-scalp animate-pulse"
                          }`}
                        />
                        {!isPaperRunning ? "STANDBY / PARADO" : isPaperPaused ? "PAUSADO" : "SIMULAÇÃO ATIVA"}
                      </span>
                    </div>
                    <p className="text-xs text-gray-400 mt-0.5">
                      Executa estratégias e ordens simuladas de forma 100% isolada sem riscos financeiros.
                    </p>
                  </div>
                </div>

                {/* Botões de Ação da Simulação */}
                <div className="flex flex-wrap items-center gap-2 w-full md:w-auto">
                  {!isPaperRunning ? (
                    <button
                      onClick={handleStartPaper}
                      disabled={isSimActionLoading}
                      className="flex-1 md:flex-none flex items-center justify-center gap-2 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-xs shadow-md shadow-emerald-600/20 transition-all disabled:opacity-50"
                    >
                      <Play className="w-3.5 h-3.5 fill-current" />
                      <span>Iniciar Simulação</span>
                    </button>
                  ) : (
                    <>
                      {isPaperPaused ? (
                        <button
                          onClick={handleResumePaper}
                          disabled={isSimActionLoading}
                          className="flex-1 md:flex-none flex items-center justify-center gap-2 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-xs transition-all disabled:opacity-50"
                        >
                          <Play className="w-3.5 h-3.5 fill-current" />
                          <span>Retomar</span>
                        </button>
                      ) : (
                        <button
                          onClick={handlePausePaper}
                          disabled={isSimActionLoading}
                          className="flex-1 md:flex-none flex items-center justify-center gap-2 px-4 py-2 rounded-xl bg-amber-600 hover:bg-amber-500 text-white font-medium text-xs transition-all disabled:opacity-50"
                        >
                          <Pause className="w-3.5 h-3.5 fill-current" />
                          <span>Pausar</span>
                        </button>
                      )}

                      <button
                        onClick={handleStopPaper}
                        disabled={isSimActionLoading}
                        title="Zera a carteira simulada e limpa operações de teste, sem encerrar o bot"
                        className="flex-1 md:flex-none flex items-center justify-center gap-2 px-3 py-2 rounded-xl bg-loss/10 hover:bg-loss/20 border border-loss/30 text-loss font-medium text-xs transition-all disabled:opacity-50"
                      >
                        <Square className="w-3.5 h-3.5 fill-current" />
                        <span>Encerrar Simulação</span>
                      </button>
                    </>
                  )}

                  {/* Reiniciar Trades */}
                  <button
                    onClick={() => setIsRestartOpen(true)}
                    className="flex-1 md:flex-none flex items-center justify-center gap-2 px-3 py-2 rounded-xl bg-surface-card hover:bg-surface-hover border border-border text-gray-300 hover:text-white font-medium text-xs transition-all"
                  >
                    <RotateCcw className="w-3.5 h-3.5" />
                    <span>Reiniciar Trades</span>
                  </button>

                  {/* Depositar Saldo */}
                  <button
                    onClick={() => setIsDepositOpen(true)}
                    className="flex-1 md:flex-none flex items-center justify-center gap-2 px-3 py-2 rounded-xl bg-brand-500/10 hover:bg-brand-500/20 border border-brand-500/30 text-brand-300 hover:text-white font-medium text-xs transition-all"
                  >
                    <DollarSign className="w-3.5 h-3.5" />
                    <span>Depositar Saldo</span>
                  </button>
                </div>
              </div>

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

                  {/* Lista de Prioridades (Real + Simulação Integrados) */}
                  <button
                    onClick={() => setSimSubTab("PRIORITY_POOL")}
                    className={`flex items-center gap-2 px-4 py-2.5 rounded-xl font-medium transition-all ${
                      simSubTab === "PRIORITY_POOL"
                        ? "bg-brand-500 text-white shadow-md shadow-brand-500/20"
                        : "text-gray-400 hover:text-white hover:bg-surface-card"
                    }`}
                  >
                    <Star className="w-4 h-4 text-amber-400" />
                    <span>Lista de Prioridades (Real + Sim)</span>
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
                {simSubTab === "PRIORITY_POOL" && <PriorityPoolTab mode="paper" />}
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

      {/* Modal de Depósito Fictício para Simulação */}
      {isDepositOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="bg-surface-card border border-border rounded-2xl w-full max-w-md p-6 shadow-2xl relative animate-in fade-in zoom-in-95 duration-150">
            <button
              onClick={() => setIsDepositOpen(false)}
              className="absolute top-4 right-4 p-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-surface-hover transition-colors"
            >
              <X className="w-4 h-4" />
            </button>

            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-xl bg-brand-500/20 border border-brand-500/40 flex items-center justify-center text-brand-400">
                <DollarSign className="w-5 h-5" />
              </div>
              <div>
                <h3 className="text-base font-bold text-white">Depositar Saldo Simulado</h3>
                <p className="text-xs text-gray-400">Ambiente Paper Trading</p>
              </div>
            </div>

            <p className="text-xs text-gray-300 mb-4 leading-relaxed">
              Adicione capital simulado em USD à carteira de testes. Esses fundos são estritamente fictícios e não envolvem transações reais na blockchain.
            </p>

            <div className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-gray-300 mb-1.5">
                  Valor a Depositar (USD)
                </label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm font-mono">$</span>
                  <input
                    type="number"
                    min="1"
                    step="1"
                    value={depositAmount}
                    onChange={(e) => setDepositAmount(Math.max(1, Number(e.target.value)))}
                    className="w-full bg-surface border border-border rounded-xl pl-8 pr-4 py-2 text-white font-mono text-sm focus:outline-none focus:border-brand-500"
                  />
                </div>
              </div>

              {/* Botões Rápidos */}
              <div className="grid grid-cols-4 gap-2">
                {[10, 50, 100, 500].map((val) => (
                  <button
                    key={val}
                    type="button"
                    onClick={() => setDepositAmount(val)}
                    className={`py-1.5 px-2 rounded-lg text-xs font-mono border transition-all ${
                      depositAmount === val
                        ? "bg-brand-500 text-white border-brand-400 font-bold"
                        : "bg-surface border-border text-gray-300 hover:bg-surface-hover"
                    }`}
                  >
                    +${val}
                  </button>
                ))}
              </div>

              <div className="flex items-center justify-end gap-3 pt-3 border-t border-border">
                <button
                  type="button"
                  onClick={() => setIsDepositOpen(false)}
                  className="px-4 py-2 rounded-xl text-xs font-medium text-gray-400 hover:text-white transition-colors"
                >
                  Cancelar
                </button>
                <button
                  type="button"
                  onClick={handleConfirmDeposit}
                  disabled={isDepositLoading || depositAmount <= 0}
                  className="px-5 py-2 rounded-xl bg-brand-500 hover:bg-brand-600 text-white text-xs font-semibold shadow-md shadow-brand-500/20 transition-all disabled:opacity-50"
                >
                  {isDepositLoading ? "Depositando..." : "Confirmar Depósito"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
