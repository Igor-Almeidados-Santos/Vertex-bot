"use client";

import { useState, useEffect, useCallback, useRef } from "react";

import {
  ShieldCheck,
  Zap,
  Lock,
  Cpu,
  Play,
  Pause,
  Square,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  ExternalLink,
  Wallet,
  Layers,
  Activity,
  Sliders,
  AlertCircle,
  CheckCircle2,
  XCircle,
  X,
  Loader2,
  ArrowDownLeft,
  ArrowUpRight,
  Key,
  Copy,
  Check,
  Eye,
  EyeOff,
  Star,
  Send,
  Coins,
} from "lucide-react";
import {
  BotStatusData,
  SummaryData,
  PositionItem,
  OrderItem,
  WalletInfo,
  PriorityToken,
} from "@/types/bot";
import {
  getBotStatus,
  getSummary,
  getPositions,
  getOrders,
  getWallets,
  connectWallet,
  disconnectWallet,
  pauseBot,
  resumeBot,
  startBot,
  stopBot,
  closePosition,
  updateConfig,
  transferSol,
  getPriorityTokens,
} from "@/lib/api";
import {
  formatUSD,
  formatCryptoPrice,
  formatPct,
  formatNumber,
  shortenAddress,
  formatTimeAgo,
} from "@/lib/formatters";
import { ChainBadge } from "@/components/ChainBadge";
import { PriorityPoolTab } from "@/components/tabs/PriorityPoolTab";

function getExplorerTxUrl(chain: string | undefined, txHash: string): string {
  const c = (chain || "solana").toLowerCase().trim();
  if (c === "arbitrum" || c === "arb") return `https://arbiscan.io/tx/${txHash}`;
  if (c === "base") return `https://basescan.org/tx/${txHash}`;
  if (c === "polygon" || c === "matic") return `https://polygonscan.com/tx/${txHash}`;
  if (c === "bsc" || c === "binance") return `https://bscscan.com/tx/${txHash}`;
  return `https://solscan.io/tx/${txHash}`;
}

export function RealDashboardView() {
  const [status, setStatus] = useState<BotStatusData | null>(null);
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [positions, setPositions] = useState<PositionItem[]>([]);
  const [orders, setOrders] = useState<OrderItem[]>([]);
  const [wallets, setWallets] = useState<WalletInfo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ message: string; isError?: boolean } | null>(null);
  const [isConfigOpen, setIsConfigOpen] = useState(false);
  const [isConnectWalletOpen, setIsConnectWalletOpen] = useState(false);
  const [walletChainToConnect, setWalletChainToConnect] = useState<"solana" | "evm">("solana");
  const [privateKeyInput, setPrivateKeyInput] = useState("");
  const [showPrivateKey, setShowPrivateKey] = useState(false);
  const [copiedAddress, setCopiedAddress] = useState<string | null>(null);
  const [priorityTokens, setPriorityTokens] = useState<PriorityToken[]>([]);
  const [activeTab, setActiveTab] = useState<"POSITIONS" | "ORDERS" | "PRIORITY">("POSITIONS");

  // Transfer On-Chain Modal State
  const [isTransferOpen, setIsTransferOpen] = useState(false);
  const [transferRecipient, setTransferRecipient] = useState("");
  const [transferAmount, setTransferAmount] = useState("");
  const [transferSendAll, setTransferSendAll] = useState(false);
  const [transferPrivateKey, setTransferPrivateKey] = useState("");
  const [showTransferKey, setShowTransferKey] = useState(false);
  const [transferLoading, setTransferLoading] = useState(false);
  const [transferTxResult, setTransferTxResult] = useState<{ hash: string; url: string; recipient: string } | null>(null);
  const [transferError, setTransferError] = useState<string | null>(null);

  // Config Form State (strings para permitir digitação livre sem coerção ou reset prematuro)
  const [cfgBuyAmount, setCfgBuyAmount] = useState<string>("5.0");
  const [cfgMaxPositions, setCfgMaxPositions] = useState<string>("3");
  const [cfgMaxSlippage, setCfgMaxSlippage] = useState<string>("1.5");
  const [cfgJitoTip, setCfgJitoTip] = useState<string>("100000");
  const isConfigOpenRef = useRef<boolean>(false);

  useEffect(() => {
    isConfigOpenRef.current = isConfigOpen;
  }, [isConfigOpen]);

  const fetchData = useCallback(async (quiet = false) => {
    if (!quiet) setIsRefreshing(true);
    try {
      const [st, sm, pos, ord, wls, pts] = await Promise.all([
        getBotStatus("live").catch(() => null),
        getSummary("live").catch(() => null),
        getPositions(undefined, "live").catch(() => []),
        getOrders(100, "live").catch(() => []),
        getWallets().catch(() => []),
        getPriorityTokens("live").catch(() => []),
      ]);
      if (st) {
        setStatus(st);
        // Só sincroniza os inputs locais se o modal NÃO estiver aberto pelo usuário,
        // impedindo que o polling periódico de 4 segundos apague o que está sendo digitado
        if (!isConfigOpenRef.current && st.settings) {
          setCfgBuyAmount(String(st.settings.live_buy_amount_usd ?? 5.0));
          setCfgMaxPositions(String(st.settings.live_max_concurrent_positions ?? 3));
          setCfgMaxSlippage(String(st.settings.live_max_slippage_pct ?? 1.5));
          setCfgJitoTip(String(st.settings.live_jito_tip_lamports ?? 100000));
        }
      }
      if (sm) setSummary(sm);
      setPositions(pos);
      setOrders(ord);
      if (wls && wls.length > 0) setWallets(wls);
      if (pts) setPriorityTokens(pts);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error("Erro ao carregar dados do Live Trading:", msg);
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(() => {
      fetchData(true);
    }, 4000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleOpenConfig = () => {
    if (status?.settings) {
      setCfgBuyAmount(String(status.settings.live_buy_amount_usd ?? 5.0));
      setCfgMaxPositions(String(status.settings.live_max_concurrent_positions ?? 3));
      setCfgMaxSlippage(String(status.settings.live_max_slippage_pct ?? 1.5));
      setCfgJitoTip(String(status.settings.live_jito_tip_lamports ?? 100000));
    }
    isConfigOpenRef.current = true;
    setIsConfigOpen(true);
  };

  const handleCloseConfig = () => {
    isConfigOpenRef.current = false;
    setIsConfigOpen(false);
  };


  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedAddress(text);
    setTimeout(() => setCopiedAddress(null), 2000);
  };

  const handleTogglePause = async () => {
    if (!status) return;
    setActionLoading("pause");
    setFeedback(null);
    try {
      if (status.is_paused) {
        await resumeBot("live");
        setFeedback({ message: "Operações reais retomadas com sucesso!" });
      } else {
        await pauseBot("live");
        setFeedback({ message: "Operações reais pausadas. Nenhuma nova compra será feita." });
      }
      await fetchData(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Falha ao alterar estado do bot";
      setFeedback({ message: msg, isError: true });
    } finally {
      setActionLoading(null);
      setTimeout(() => setFeedback(null), 5000);
    }
  };

  const handleStart = async () => {
    setActionLoading("start");
    setFeedback(null);
    try {
      await startBot("live");
      setFeedback({ message: "Bot de operações reais iniciado!" });
      setFeedback({ message: "Bot de operações reais iniciado com sucesso!" });
      await fetchData(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Falha ao iniciar o bot";
      setFeedback({ message: msg, isError: true });
    } finally {
      setActionLoading(null);
      setTimeout(() => setFeedback(null), 5000);
    }
  };

  const handleStop = async () => {
    if (!window.confirm("Deseja encerrar o modo real? Novas compras reais serão desativadas. O histórico on-chain permanece intacto.")) {
      return;
    }
    setActionLoading("stop");
    setFeedback(null);
    try {
      await stopBot("live");
      setFeedback({ message: "Operações reais encerradas. O bot está em modo standby para ordens reais." });
      await fetchData(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Falha ao encerrar modo real";
      setFeedback({ message: msg, isError: true });
    } finally {
      setActionLoading(null);
      setTimeout(() => setFeedback(null), 5000);
    }
  };

  const handleConnectWallet = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!privateKeyInput.trim()) return;
    setActionLoading("connect_wallet");
    setFeedback(null);
    try {
      const res = await connectWallet(walletChainToConnect, privateKeyInput.trim());
      setFeedback({ message: `Carteira ${walletChainToConnect.toUpperCase()} conectada com sucesso (${res.address.slice(0, 6)}...${res.address.slice(-4)})!` });
      setIsConnectWalletOpen(false);
      setPrivateKeyInput("");
      setShowPrivateKey(false);
      await fetchData(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Erro ao conectar carteira";
      setFeedback({ message: msg, isError: true });
    } finally {
      setActionLoading(null);
      setTimeout(() => setFeedback(null), 6000);
    }
  };

  const handleDisconnectWallet = async (chain: string) => {
    if (!window.confirm(`Deseja desconectar a carteira ${chain.toUpperCase()}? O bot não poderá abrir novas posições reais nesta rede.`)) {
      return;
    }
    setActionLoading(`disconnect_${chain}`);
    setFeedback(null);
    try {
      await disconnectWallet(chain);
      setFeedback({ message: `Carteira ${chain.toUpperCase()} desconectada.` });
      await fetchData(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Erro ao desconectar carteira";
      setFeedback({ message: msg, isError: true });
    } finally {
      setActionLoading(null);
      setTimeout(() => setFeedback(null), 5000);
    }
  };

  const handleOpenTransfer = () => {
    setTransferRecipient("");
    setTransferAmount("");
    setTransferSendAll(false);
    setTransferPrivateKey("");
    setShowTransferKey(false);
    setTransferTxResult(null);
    setTransferError(null);
    setIsTransferOpen(true);
  };

  const handleTransferSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!transferRecipient.trim()) {
      setTransferError("Por favor, informe o endereço de destino.");
      return;
    }
    setTransferLoading(true);
    setTransferError(null);
    setTransferTxResult(null);
    try {
      const parsedAmount = transferSendAll ? undefined : (parseFloat(transferAmount.replace(",", ".")) || undefined);
      const res = await transferSol({
        recipient_address: transferRecipient.trim(),
        amount_sol: parsedAmount,
        send_all: transferSendAll,
        private_key: transferPrivateKey.trim() || undefined,
      });
      setTransferTxResult({
        hash: res.tx_hash,
        url: res.solscan_url,
        recipient: res.recipient,
      });
      setFeedback({
        message: `Transferência on-chain enviada com sucesso! Tx: ${shortenAddress(res.tx_hash, 6)}`,
      });
      await fetchData(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setTransferError(msg);
    } finally {
      setTransferLoading(false);
    }
  };

  const handleClosePosition = async (posId: number) => {
    if (!window.confirm(`ATENÇÃO: Deseja liquidar a mercado a posição REAL #${posId}? A ordem de venda on-chain será transmitida imediatamente.`)) {
      return;
    }
    setActionLoading(`close-${posId}`);
    setFeedback(null);
    try {
      const res = await closePosition(posId, "live");
      setFeedback({ message: res.message || `Posição #${posId} fechada com sucesso!` });
      await fetchData(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Falha ao liquidar posição real";
      setFeedback({ message: msg, isError: true });
    } finally {
      setActionLoading(null);
      setTimeout(() => setFeedback(null), 6000);
    }
  };

  const handleSaveConfig = async (e: React.FormEvent) => {
    e.preventDefault();
    setActionLoading("save_config");
    setFeedback(null);
    try {
      const buyAmountNum = parseFloat(cfgBuyAmount) || 5.0;
      const maxPositionsNum = parseInt(cfgMaxPositions, 10) || 3;
      const maxSlippageNum = parseFloat(cfgMaxSlippage) || 1.5;
      const jitoTipNum = parseInt(cfgJitoTip, 10) || 100000;

      await updateConfig(
        {
          live_buy_amount_usd: buyAmountNum,
          live_max_concurrent_positions: maxPositionsNum,
          live_max_slippage_pct: maxSlippageNum,
          live_jito_tip_lamports: jitoTipNum,
        },
        "live"
      );
      setFeedback({ message: "Parâmetros do modo LIVE atualizados com sucesso!" });
      handleCloseConfig();
      await fetchData(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Erro ao salvar configurações reais";
      setFeedback({ message: msg, isError: true });
    } finally {
      setActionLoading(null);
      setTimeout(() => setFeedback(null), 5000);
    }
  };

  const activePositions = positions.filter((p) => p.status === "OPEN" || p.status === "PARTIALLY_CLOSED");
  const isRunning = status?.is_running ?? false;
  const isPaused = status?.is_paused ?? false;
  const hasWallet = Boolean(status?.has_wallet || status?.has_connected_wallet || wallets.some((w) => w.is_connected));

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Toast Feedback */}
      {feedback && (
        <div
          className={`flex items-center justify-between p-4 rounded-xl border text-sm font-medium transition-all ${
            feedback.isError
              ? "bg-loss/15 border-loss/40 text-loss"
              : "bg-profit/15 border-profit/40 text-profit"
          }`}
        >
          <div className="flex items-center gap-2">
            {feedback.isError ? <AlertCircle className="w-5 h-5" /> : <CheckCircle2 className="w-5 h-5" />}
            <span>{feedback.message}</span>
          </div>
          <button onClick={() => setFeedback(null)} className="text-gray-400 hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Top Banner & Operational Controls */}
      <div className="p-6 rounded-2xl border border-brand-500/30 bg-gradient-to-r from-brand-500/10 via-surface-card to-surface-card flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
        <div className="space-y-2">
          <div className="flex items-center gap-2.5 flex-wrap">
            <span className="text-xs font-mono font-bold px-3 py-1 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 uppercase tracking-wider flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              Operações Reais (Live On-Chain)
            </span>
            {/* Status Badge */}
            {!isRunning ? (
              <span className="text-xs font-mono px-2.5 py-0.5 rounded bg-gray-500/20 text-gray-400 border border-gray-500/30">
                OFFLINE
                OFFLINE / STANDBY
              </span>
            ) : isPaused ? (
              <span className="text-xs font-mono px-2.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40 flex items-center gap-1">
                <Pause className="w-3 h-3" /> PAUSADO
              </span>
            ) : (
              <span className="text-xs font-mono px-2.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 flex items-center gap-1">
                <Play className="w-3 h-3" /> ATIVO
              </span>
            )}
          </div>
          <h2 className="text-2xl font-bold text-white tracking-tight">
            Terminal de Execução Real
          </h2>
          <p className="text-xs text-gray-400 max-w-xl">
            Ambiente on-chain 100% isolado da simulação. As ordens utilizam assinatura local de chaves em memória restrita e rotas Jupiter/Uniswap com proteção MEV.
          </p>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-2 flex-wrap">
          {isRunning ? (
            <>
              <button
                onClick={handleTogglePause}
                disabled={actionLoading === "pause"}
                className={`flex items-center gap-2 px-4 py-2.5 rounded-xl font-semibold text-xs transition-all shadow-md ${
                  isPaused
                    ? "bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-600/20"
                    : "bg-amber-600 hover:bg-amber-500 text-white shadow-amber-600/20"
                }`}
              >
                {actionLoading === "pause" ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : isPaused ? (
                  <Play className="w-4 h-4 fill-current" />
                ) : (
                  <Pause className="w-4 h-4 fill-current" />
                )}
                <span>{isPaused ? "Retomar Operações" : "Pausar Operações"}</span>
              </button>

              <button
                onClick={handleStop}
                disabled={actionLoading === "stop"}
                className="flex items-center gap-2 px-4 py-2.5 rounded-xl font-semibold text-xs bg-loss/15 hover:bg-loss/25 text-loss border border-loss/30 transition-all"
                title="Encerrar Modo Real (interrompe novas compras sem apagar histórico on-chain)"
              >
                {actionLoading === "stop" ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Square className="w-4 h-4 fill-current" />
                )}
                <span>Encerrar Modo Real</span>
              </button>
            </>
          ) : (
            <div className="relative group">
              <button
                onClick={handleStart}
                disabled={!hasWallet || actionLoading === "start"}
                className={`flex items-center gap-2 px-4 py-2.5 rounded-xl font-semibold text-xs transition-all shadow-md ${
                  !hasWallet
                    ? "bg-gray-700/50 text-gray-400 border border-gray-600/30 cursor-not-allowed"
                    : "bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-600/20"
                }`}
              >
                {actionLoading === "start" ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : !hasWallet ? (
                  <Lock className="w-4 h-4" />
                ) : (
                  <Play className="w-4 h-4 fill-current" />
                )}
                <span>Iniciar Bot Live</span>
              </button>
              {!hasWallet && (
                <div className="absolute top-full right-0 mt-1 hidden group-hover:block z-20 w-60 p-2 rounded-lg bg-surface-card border border-border shadow-xl text-[11px] text-amber-300">
                  Conecte ao menos uma carteira Solana ou EVM com saldo antes de iniciar as operações reais.
                </div>
              )}
            </div>
          )}

          <button
            onClick={handleOpenConfig}
            className="flex items-center gap-2 px-3.5 py-2.5 rounded-xl text-xs font-semibold bg-surface-card hover:bg-surface-hover border border-border text-gray-200 transition-colors"
          >
            <Sliders className="w-4 h-4" />
            <span>Configurações Live</span>
          </button>


          <button
            onClick={() => fetchData(false)}
            disabled={isRefreshing}
            className="p-2.5 rounded-xl bg-surface-card hover:bg-surface-hover border border-border text-gray-400 hover:text-white transition-colors"
            title="Atualizar Dados"
          >
            <RefreshCw className={`w-4 h-4 ${isRefreshing ? "animate-spin text-brand-400" : ""}`} />
          </button>
        </div>
      </div>

      {/* Painel de Carteiras Conectadas On-Chain */}
      <div className="p-5 rounded-2xl border border-border bg-surface-card space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <Wallet className="w-5 h-5 text-emerald-400" />
              <h3 className="text-base font-bold text-white">Carteiras On-Chain Conectadas</h3>
            </div>
            <p className="text-xs text-gray-400 mt-0.5">
              O bot requer ao menos uma carteira ativa com saldo para abrir posições reais. As chaves são mantidas estritamente na memória volátil.
            </p>
          </div>
          <button
            onClick={() => {
              setWalletChainToConnect("solana");
              setIsConnectWalletOpen(true);
            }}
            className="flex items-center gap-2 px-3.5 py-2 rounded-xl text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white shadow-md shadow-emerald-600/20 transition-all self-start sm:self-auto"
          >
            <Key className="w-4 h-4" />
            <span>Conectar Carteira</span>
          </button>
          <div className="flex items-center gap-2 self-start sm:self-auto">
            <button
              onClick={handleOpenTransfer}
              className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-semibold bg-surface-card hover:bg-surface-hover border border-emerald-500/40 text-emerald-400 hover:text-emerald-300 transition-all shadow-sm"
              title="Sacar ou Transferir SOL On-Chain"
            >
              <ArrowUpRight className="w-4 h-4 text-emerald-400" />
              <span>Transferir / Sacar SOL</span>
            </button>
            <button
              onClick={() => {
                setWalletChainToConnect("solana");
                setIsConnectWalletOpen(true);
              }}
              className="flex items-center gap-2 px-3.5 py-2 rounded-xl text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white shadow-md shadow-emerald-600/20 transition-all"
            >
              <Key className="w-4 h-4" />
              <span>Conectar Carteira</span>
            </button>
          </div>
        </div>

        {/* Alerta de Trava de Segurança */}
        {!hasWallet && (
          <div className="p-3.5 rounded-xl border border-amber-500/40 bg-amber-500/10 text-amber-300 text-xs flex items-start gap-2.5">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
            <div>
              <span className="font-bold">Trava de Segurança Ativa:</span> Nenhuma carteira real conectada.
              Conecte sua carteira Solana ou EVM para liberar a abertura de operações reais on-chain.
            </div>
          </div>
        )}

        {/* Grid de Carteiras */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Solana Card */}
          {(() => {
            const solWallet = wallets.find((w) => w.chain.toLowerCase() === "solana");
            const isConn = Boolean(solWallet?.is_connected);
            const addr = solWallet?.address || "";
            return (
              <div className={`p-4 rounded-xl border transition-all ${isConn ? "border-emerald-500/30 bg-surface/80" : "border-border bg-surface/40"}`}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full bg-[#14F195]" />
                    <span className="font-bold text-sm text-white">Solana Mainnet</span>
                  </div>
                  <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full font-semibold ${isConn ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30" : "bg-gray-500/20 text-gray-400 border border-gray-500/30"}`}>
                    {isConn ? "CONECTADA" : "DESCONECTADA"}
                  </span>
                </div>

                {isConn && addr ? (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-gray-400 font-mono">{shortenAddress(addr, 6)}</span>
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={() => copyToClipboard(addr)}
                          className="p-1 rounded hover:bg-surface-hover text-gray-400 hover:text-white"
                          title="Copiar Endereço"
                        >
                          {copiedAddress === addr ? <Check className="w-3.5 h-3.5 text-profit" /> : <Copy className="w-3.5 h-3.5" />}
                        </button>
                        <a
                          href={`https://solscan.io/account/${addr}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="p-1 rounded hover:bg-surface-hover text-gray-400 hover:text-white"
                          title="Ver no Solscan"
                        >
                          <ExternalLink className="w-3.5 h-3.5" />
                        </a>
                      </div>
                    </div>
                    <div className="flex items-baseline justify-between pt-1 border-t border-border/50">
                      <span className="text-[11px] text-gray-400">Saldo On-Chain</span>
                      <div className="text-right font-mono">
                        <span className="text-sm font-bold text-white">{solWallet?.balance_native ?? 0} SOL</span>
                        <span className="text-xs text-gray-400 block">{formatUSD(solWallet?.balance_usd ?? 0)}</span>
                      </div>
                    </div>
                    <button
                      onClick={() => handleDisconnectWallet("solana")}
                      disabled={actionLoading === "disconnect_solana"}
                      className="w-full mt-2 py-1.5 rounded-lg text-xs font-semibold bg-loss/10 hover:bg-loss/20 text-loss border border-loss/20 transition-colors flex items-center justify-center gap-1.5"
                    >
                      {actionLoading === "disconnect_solana" ? <Loader2 className="w-3 h-3 animate-spin" /> : <XCircle className="w-3 h-3" />}
                      <span>Desconectar Solana</span>
                    </button>
                    <div className="grid grid-cols-2 gap-2 mt-2">
                      <button
                        onClick={handleOpenTransfer}
                        className="py-1.5 rounded-lg text-xs font-semibold bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 transition-colors flex items-center justify-center gap-1.5"
                      >
                        <ArrowUpRight className="w-3.5 h-3.5" />
                        <span>Sacar / Transferir</span>
                      </button>
                      <button
                        onClick={() => handleDisconnectWallet("solana")}
                        disabled={actionLoading === "disconnect_solana"}
                        className="py-1.5 rounded-lg text-xs font-semibold bg-loss/10 hover:bg-loss/20 text-loss border border-loss/20 transition-colors flex items-center justify-center gap-1.5"
                      >
                        {actionLoading === "disconnect_solana" ? <Loader2 className="w-3 h-3 animate-spin" /> : <XCircle className="w-3 h-3" />}
                        <span>Desconectar</span>
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-3 pt-1">
                    <p className="text-xs text-gray-400">
                      Conecte sua carteira Solana via chave privada Base58 para assinar swaps na Raydium / Pumpfun / Jupiter.
                    </p>
                    <button
                      onClick={() => {
                        setWalletChainToConnect("solana");
                        setIsConnectWalletOpen(true);
                      }}
                      className="w-full py-2 rounded-xl text-xs font-semibold bg-surface-card hover:bg-surface-hover border border-border text-emerald-400 hover:text-emerald-300 transition-colors flex items-center justify-center gap-1.5"
                    >
                      <Key className="w-3.5 h-3.5" />
                      <span>Conectar Carteira Solana</span>
                    </button>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      <button
                        onClick={() => {
                          setWalletChainToConnect("solana");
                          setIsConnectWalletOpen(true);
                        }}
                        className="w-full py-2 rounded-xl text-xs font-semibold bg-surface-card hover:bg-surface-hover border border-border text-emerald-400 hover:text-emerald-300 transition-colors flex items-center justify-center gap-1.5"
                      >
                        <Key className="w-3.5 h-3.5" />
                        <span>Conectar Solana</span>
                      </button>
                      <button
                        onClick={handleOpenTransfer}
                        className="w-full py-2 rounded-xl text-xs font-semibold bg-surface-card hover:bg-surface-hover border border-border text-gray-300 hover:text-white transition-colors flex items-center justify-center gap-1.5"
                      >
                        <ArrowUpRight className="w-3.5 h-3.5 text-emerald-400" />
                        <span>Transferir Avulsa</span>
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })()}

          {/* EVM Card */}
          {(() => {
            const evmWallet = wallets.find((w) => w.chain.toLowerCase() === "evm" || w.chain.toLowerCase() === "arbitrum");
            const isConn = Boolean(evmWallet?.is_connected);
            const addr = evmWallet?.address || "";
            return (
              <div className={`p-4 rounded-xl border transition-all ${isConn ? "border-emerald-500/30 bg-surface/80" : "border-border bg-surface/40"}`}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full bg-[#28A0F0]" />
                    <span className="font-bold text-sm text-white">Arbitrum One / Base (EVM)</span>
                  </div>
                  <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full font-semibold ${isConn ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30" : "bg-gray-500/20 text-gray-400 border border-gray-500/30"}`}>
                    {isConn ? "CONECTADA" : "DESCONECTADA"}
                  </span>
                </div>

                {isConn && addr ? (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-gray-400 font-mono">{shortenAddress(addr, 6)}</span>
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={() => copyToClipboard(addr)}
                          className="p-1 rounded hover:bg-surface-hover text-gray-400 hover:text-white"
                          title="Copiar Endereço"
                        >
                          {copiedAddress === addr ? <Check className="w-3.5 h-3.5 text-profit" /> : <Copy className="w-3.5 h-3.5" />}
                        </button>
                        <a
                          href={`https://arbiscan.io/address/${addr}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="p-1 rounded hover:bg-surface-hover text-gray-400 hover:text-white"
                          title="Ver no Arbiscan"
                        >
                          <ExternalLink className="w-3.5 h-3.5" />
                        </a>
                      </div>
                    </div>
                    <div className="flex items-baseline justify-between pt-1 border-t border-border/50">
                      <span className="text-[11px] text-gray-400">Saldo On-Chain</span>
                      <div className="text-right font-mono">
                        <span className="text-sm font-bold text-white">{evmWallet?.balance_native ?? 0} ETH</span>
                        <span className="text-xs text-gray-400 block">{formatUSD(evmWallet?.balance_usd ?? 0)}</span>
                      </div>
                    </div>
                    <button
                      onClick={() => handleDisconnectWallet("evm")}
                      disabled={actionLoading === "disconnect_evm"}
                      className="w-full mt-2 py-1.5 rounded-lg text-xs font-semibold bg-loss/10 hover:bg-loss/20 text-loss border border-loss/20 transition-colors flex items-center justify-center gap-1.5"
                    >
                      {actionLoading === "disconnect_evm" ? <Loader2 className="w-3 h-3 animate-spin" /> : <XCircle className="w-3 h-3" />}
                      <span>Desconectar EVM</span>
                    </button>
                  </div>
                ) : (
                  <div className="space-y-3 pt-1">
                    <p className="text-xs text-gray-400">
                      Conecte sua carteira EVM via chave privada hexadecimal (0x...) para assinar swaps na Uniswap v3 / Camelot.
                    </p>
                    <button
                      onClick={() => {
                        setWalletChainToConnect("evm");
                        setIsConnectWalletOpen(true);
                      }}
                      className="w-full py-2 rounded-xl text-xs font-semibold bg-surface-card hover:bg-surface-hover border border-border text-brand-400 hover:text-brand-300 transition-colors flex items-center justify-center gap-1.5"
                    >
                      <Key className="w-3.5 h-3.5" />
                      <span>Conectar Carteira EVM</span>
                    </button>
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      </div>

      {/* KPI Cards Grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Card 1: Saldo On-Chain */}
        <div className="p-4 rounded-xl border border-border bg-surface-card space-y-1">
          <div className="flex items-center justify-between text-gray-400 text-xs">
            <span className="font-medium">Saldo em Carteira</span>
            <Wallet className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="text-xl sm:text-2xl font-bold font-mono text-white">
            {formatUSD(status?.wallet_balance_usd ?? 0.0)}
          </div>
          <div className="text-[11px] text-gray-400">
            Montante por Trade: <span className="text-gray-200 font-mono font-semibold">{formatUSD(status?.settings.live_buy_amount_usd ?? (parseFloat(cfgBuyAmount) || 5.0))}</span>
          </div>
        </div>

        {/* Card 2: PnL Realizado */}
        <div className="p-4 rounded-xl border border-border bg-surface-card space-y-1">
          <div className="flex items-center justify-between text-gray-400 text-xs">
            <span className="font-medium">PnL Realizado</span>
            {(summary?.pnl.realized_pnl_usd ?? 0) >= 0 ? (
              <TrendingUp className="w-4 h-4 text-profit" />
            ) : (
              <TrendingDown className="w-4 h-4 text-loss" />
            )}
          </div>
          <div
            className={`text-xl sm:text-2xl font-bold font-mono ${
              (summary?.pnl.realized_pnl_usd ?? 0) >= 0 ? "text-profit" : "text-loss"
            }`}
          >
            {formatUSD(summary?.pnl.realized_pnl_usd ?? 0.0)}
          </div>
          <div className="text-[11px] text-gray-400">
            Win-Rate: <span className="text-gray-200 font-mono font-semibold">{formatPct(summary?.pnl.win_rate_pct ?? 0.0)}</span> ({summary?.pnl.total_trades ?? 0} trades)
          </div>
        </div>

        {/* Card 3: PnL Não-Realizado */}
        <div className="p-4 rounded-xl border border-border bg-surface-card space-y-1">
          <div className="flex items-center justify-between text-gray-400 text-xs">
            <span className="font-medium">PnL Não-Realizado</span>
            <Activity className="w-4 h-4 text-cyan-400" />
          </div>
          <div
            className={`text-xl sm:text-2xl font-bold font-mono ${
              (summary?.pnl.unrealized_pnl_usd ?? 0) >= 0 ? "text-profit" : "text-loss"
            }`}
          >
            {formatUSD(summary?.pnl.unrealized_pnl_usd ?? 0.0)}
          </div>
          <div className="text-[11px] text-gray-400">
            Capital em Posição: <span className="text-gray-200 font-mono font-semibold">{formatUSD(activePositions.reduce((acc, p) => acc + (p.allocated_capital_usd || 0), 0))}</span>
          </div>
        </div>

        {/* Card 4: Posições Abertas */}
        <div className="p-4 rounded-xl border border-border bg-surface-card space-y-1">
          <div className="flex items-center justify-between text-gray-400 text-xs">
            <span className="font-medium">Posições Abertas</span>
            <Layers className="w-4 h-4 text-brand-400" />
          </div>
          <div className="text-xl sm:text-2xl font-bold font-mono text-white">
            {activePositions.length} <span className="text-sm font-normal text-gray-400">/ {status?.settings.live_max_concurrent_positions ?? cfgMaxPositions}</span>
          </div>
          <div className="text-[11px] text-gray-400">
            Slippage Máximo: <span className="text-gray-200 font-mono font-semibold">{formatPct(status?.settings.live_max_slippage_pct ?? (parseFloat(cfgMaxSlippage) || 1.5))}</span>
          </div>
        </div>

      </div>

      {/* Security Architecture Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
        <div className="p-4 rounded-xl border border-border bg-surface-card space-y-1.5">
          <div className="flex items-center gap-2 text-emerald-400 font-semibold">
            <Lock className="w-4 h-4" />
            <span>Assinatura Segura Local</span>
          </div>
          <p className="text-gray-400">
            Chaves privadas isoladas em memória volátil via <code className="text-emerald-300">solders.Keypair</code> e <code className="text-emerald-300">eth_account</code>. Jamais são expostas em logs, arquivos JSON ou APIs.
          </p>
        </div>

        <div className="p-4 rounded-xl border border-border bg-surface-card space-y-1.5">
          <div className="flex items-center gap-2 text-brand-400 font-semibold">
            <Cpu className="w-4 h-4" />
            <span>Jito MEV Protection</span>
          </div>
          <p className="text-gray-400">
            Ordens na Solana são roteadas preferencialmente via bundles Jito com gorjeta configurável, neutralizando ataques sanduíche e front-running.
          </p>
        </div>

        <div className="p-4 rounded-xl border border-border bg-surface-card space-y-1.5">
          <div className="flex items-center gap-2 text-amber-400 font-semibold">
            <ShieldCheck className="w-4 h-4" />
            <span>Hard Gates Inegociáveis</span>
          </div>
          <p className="text-gray-400">
            Mint revogada, Freeze revogada, LP &ge; 98% queimada/trancada e Top 10 holders &le; 15% validados estritamente antes de qualquer transação.
          </p>
        </div>
      </div>

      {/* Sub-Tabs: Posições Abertas Reais vs Histórico de Ordens Reais */}
      <div className="space-y-4">
        <div className="border-b border-border flex items-center justify-between">
          <nav className="flex items-center gap-2 pb-1 text-xs">
            <button
              onClick={() => setActiveTab("POSITIONS")}
              className={`flex items-center gap-2 px-4 py-2.5 rounded-xl font-medium transition-all ${
                activeTab === "POSITIONS"
                  ? "bg-brand-500 text-white shadow-md shadow-brand-500/20"
                  : "text-gray-400 hover:text-white hover:bg-surface-card"
              }`}
            >
              <Layers className="w-4 h-4" />
              <span>Posições Abertas Reais</span>
              <span className="px-1.5 py-0.2 rounded-full text-[10px] font-mono bg-white/20 text-white">
                {activePositions.length}
              </span>
            </button>

            <button
              onClick={() => setActiveTab("ORDERS")}
              className={`flex items-center gap-2 px-4 py-2.5 rounded-xl font-medium transition-all ${
                activeTab === "ORDERS"
                  ? "bg-brand-500 text-white shadow-md shadow-brand-500/20"
                  : "text-gray-400 hover:text-white hover:bg-surface-card"
              }`}
            >
              <Activity className="w-4 h-4" />
              <span>Ordens On-Chain Executadas</span>
              <span className="px-1.5 py-0.2 rounded-full text-[10px] font-mono bg-white/20 text-white">
                {orders.length}
              </span>
            </button>

            <button
              onClick={() => setActiveTab("PRIORITY")}
              className={`flex items-center gap-2 px-4 py-2.5 rounded-xl font-medium transition-all ${
                activeTab === "PRIORITY"
                  ? "bg-brand-500 text-white shadow-md shadow-brand-500/20"
                  : "text-gray-400 hover:text-white hover:bg-surface-card"
              }`}
            >
              <Star className="w-4 h-4 text-amber-400" />
              <span>Lista de Prioridades</span>
              <span className="px-1.5 py-0.2 rounded-full text-[10px] font-mono bg-white/20 text-white">
                {priorityTokens.length}
              </span>
            </button>
          </nav>
        </div>

        {/* Tab 1: Posições Abertas Reais */}
        {activeTab === "POSITIONS" && (
          <div>
            {activePositions.length === 0 ? (
              <div className="flex flex-col items-center justify-center p-12 rounded-2xl border border-dashed border-border bg-surface/50 text-center">
                <div className="w-12 h-12 rounded-full bg-surface-card border border-border flex items-center justify-center text-gray-400 mb-3">
                  <Layers className="w-6 h-6" />
                </div>
                <h3 className="text-base font-semibold text-white">Nenhuma Posição Real Aberta</h3>
                <p className="text-sm text-gray-400 max-w-sm mt-1">
                  O bot está analisando o mercado em busca de oportunidades com liquidez segura. Assim que uma entrada real for executada, os detalhes aparecerão aqui.
                </p>
              </div>
            ) : (
              <div className="rounded-xl border border-border bg-surface-card overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-surface border-b border-border text-gray-400 font-medium">
                      <tr>
                        <th className="py-3 px-4">Token / Par</th>
                        <th className="py-3 px-4">Estratégia</th>
                        <th className="py-3 px-4">Preço Entrada</th>
                        <th className="py-3 px-4">Preço Atual</th>
                        <th className="py-3 px-4">PnL Não-Realizado</th>
                        <th className="py-3 px-4">Stop / Saída</th>
                        <th className="py-3 px-4">Abertura</th>
                        <th className="py-3 px-4 text-right">Ações</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/60">
                      {activePositions.map((p) => {
                        const pnlPct = p.unrealized_pnl_pct ?? 0;
                        const pnlUsd = p.unrealized_pnl_usd ?? 0;
                        const isProfitable = pnlPct >= 0;
                        const addr = p.token_address || p.address || "";
                        const sym = p.token_symbol || p.symbol || shortenAddress(addr);
                        const chain = (p.chain || "solana").toLowerCase();
                        const dexUrl = `https://dexscreener.com/${chain}/${addr}`;

                        return (
                          <tr key={p.id} className="hover:bg-surface-hover/50 transition-colors">
                            <td className="py-3 px-4">
                              <div className="flex items-center gap-1.5 flex-wrap">
                                <span className="font-bold font-mono text-white text-sm">
                                  {sym}
                                </span>
                                <ChainBadge chain={p.chain} />
                                {addr && (
                                  <a
                                    href={dexUrl}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-gray-400 hover:text-brand-300"
                                    title="Abrir no DexScreener"
                                  >
                                    <ExternalLink className="w-3.5 h-3.5" />
                                  </a>
                                )}
                              </div>
                              <span className="text-[10px] text-gray-400 font-mono block">
                                {shortenAddress(addr, 6)}
                              </span>
                            </td>

                            <td className="py-3 px-4">
                              <span className="px-2 py-0.5 rounded text-[10px] font-bold font-mono uppercase bg-surface border border-border text-gray-300">
                                {p.strategy_type || "SCALP"}
                              </span>
                            </td>

                            <td className="py-3 px-4 font-mono text-gray-300">
                              {formatCryptoPrice(p.entry_price)}
                            </td>

                            <td className="py-3 px-4 font-mono font-bold text-white">
                              {formatCryptoPrice(p.current_price)}
                            </td>

                            <td className="py-3 px-4">
                              <div className={`font-mono font-bold ${isProfitable ? "text-profit" : "text-loss"}`}>
                                {formatPct(pnlPct)}
                              </div>
                              <div className={`text-[11px] font-mono ${isProfitable ? "text-profit/80" : "text-loss/80"}`}>
                                {formatUSD(pnlUsd)}
                              </div>
                            </td>

                            <td className="py-3 px-4 font-mono text-gray-400 text-[11px]">
                              <div>Stop: {formatCryptoPrice(p.trailing_stop_price || p.stop_loss_price)}</div>
                              {p.break_even_triggered && (
                                <span className="text-[10px] text-brand-300 font-semibold">Break-Even Ativo</span>
                              )}
                            </td>

                            <td className="py-3 px-4 font-mono text-gray-400 text-[11px]">
                              {formatTimeAgo(p.opened_at)}
                            </td>

                            <td className="py-3 px-4 text-right">
                              <button
                                onClick={() => handleClosePosition(p.id)}
                                disabled={actionLoading === `close-${p.id}`}
                                className="px-2.5 py-1 rounded-lg text-xs font-semibold bg-loss/15 hover:bg-loss/25 text-loss border border-loss/30 transition-colors inline-flex items-center gap-1"
                                title="Liquidar Posição Real a Mercado"
                              >
                                {actionLoading === `close-${p.id}` ? (
                                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                ) : (
                                  <XCircle className="w-3.5 h-3.5" />
                                )}
                                <span>Vender a Mercado</span>
                              </button>
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
        )}

        {/* Tab 2: Histórico de Ordens On-Chain */}
        {activeTab === "ORDERS" && (
          <div>
            {orders.length === 0 ? (
              <div className="flex flex-col items-center justify-center p-12 rounded-2xl border border-dashed border-border bg-surface/50 text-center">
                <h3 className="text-base font-semibold text-white">Nenhuma Ordem On-Chain</h3>
                <p className="text-sm text-gray-400 max-w-sm mt-1">
                  As ordens de compra e venda on-chain transmitidas pela carteira real serão catalogadas aqui com o hash da transação.
                </p>
              </div>
            ) : (
              <div className="rounded-xl border border-border bg-surface-card overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-surface border-b border-border text-gray-400 font-medium">
                      <tr>
                        <th className="py-3 px-4">Tipo</th>
                        <th className="py-3 px-4">Token</th>
                        <th className="py-3 px-4">Preço Execução</th>
                        <th className="py-3 px-4">Montante ($ USD)</th>
                        <th className="py-3 px-4">Tokens</th>
                        <th className="py-3 px-4">Tx Hash (Explorer)</th>
                        <th className="py-3 px-4">Data / Hora</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/60">
                      {orders.map((ord) => {
                        const isBuy = ord.order_type === "BUY";
                        const addr = ord.token_address || "";
                        const sym = ord.token_symbol || ord.symbol || shortenAddress(addr);
                        const amountUsd = ord.amount_usd ?? ord.total_usd ?? 0;
                        const tokenAmount = ord.tokens_amount ?? ord.amount ?? 0;
                        const txHash = ord.tx_hash || "";
                        const explorerUrl = txHash ? getExplorerTxUrl(ord.chain, txHash) : null;

                        return (
                          <tr key={ord.id} className="hover:bg-surface-hover/50 transition-colors">
                            <td className="py-3 px-4">
                              <span
                                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold font-mono uppercase ${
                                  isBuy
                                    ? "bg-profit/15 text-profit border border-profit/30"
                                    : "bg-loss/15 text-loss border border-loss/30"
                                }`}
                              >
                                {isBuy ? <ArrowDownLeft className="w-3 h-3" /> : <ArrowUpRight className="w-3 h-3" />}
                                {ord.order_type}
                              </span>
                            </td>

                            <td className="py-3 px-4">
                              <div className="flex items-center gap-1.5 flex-wrap">
                                <span className="font-bold font-mono text-white">
                                  {sym}
                                </span>
                                <ChainBadge chain={ord.chain} />
                              </div>
                              {addr && (
                                <span className="text-[10px] text-gray-400 font-mono block">
                                  {shortenAddress(addr, 6)}
                                </span>
                              )}
                            </td>

                            <td className="py-3 px-4 font-mono text-gray-300">
                              {formatCryptoPrice(ord.price)}
                            </td>

                            <td className="py-3 px-4 font-mono font-bold text-white">
                              {formatUSD(amountUsd)}
                            </td>

                            <td className="py-3 px-4 font-mono text-gray-300">
                              {formatNumber(tokenAmount, 2)}
                            </td>

                            <td className="py-3 px-4 font-mono">
                              {explorerUrl ? (
                                <a
                                  href={explorerUrl}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="inline-flex items-center gap-1 text-brand-400 hover:text-brand-300 hover:underline text-[11px]"
                                  title="Ver no Explorer On-Chain"
                                >
                                  <span>{shortenAddress(txHash, 6)}</span>
                                  <ExternalLink className="w-3 h-3" />
                                </a>
                              ) : (
                                <span className="text-gray-500 text-[11px]">—</span>
                              )}
                            </td>

                            <td className="py-3 px-4 font-mono text-gray-400 text-[11px]">
                              {formatTimeAgo(ord.timestamp || ord.executed_at || "")}
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
        )}

        {/* Tab 3: Lista de Prioridades (Tokens Aprovados e Negociados) */}
        {activeTab === "PRIORITY" && (
          <PriorityPoolTab mode="live" />
        )}
      </div>

      {/* Modal de Configurações Live */}
      {isConfigOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
          <div className="w-full max-w-md bg-surface-card border border-border rounded-2xl p-6 space-y-5 shadow-2xl animate-in fade-in zoom-in duration-200">
            <div className="flex items-center justify-between border-b border-border pb-3">
              <div className="flex items-center gap-2">
                <Sliders className="w-5 h-5 text-brand-400" />
                <h3 className="text-base font-bold text-white">Parâmetros de Operações Reais</h3>
              </div>
              <button
                onClick={handleCloseConfig}
                className="text-gray-400 hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSaveConfig} className="space-y-4 text-xs">
              <div>
                <label className="block text-gray-300 font-semibold mb-1">
                  Montante por Trade ($ USD)
                </label>
                <input
                  type="number"
                  step="0.5"
                  min="0.5"
                  max="1000"
                  value={cfgBuyAmount}
                  onChange={(e) => setCfgBuyAmount(e.target.value)}
                  className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-white font-mono focus:border-brand-500 focus:outline-none"
                  required
                />
                <span className="text-[10px] text-gray-500">Valor alocado em cada nova ordem de compra real.</span>
              </div>

              <div>
                <label className="block text-gray-300 font-semibold mb-1">
                  Máximo de Posições Simultâneas
                </label>
                <input
                  type="number"
                  step="1"
                  min="1"
                  max="20"
                  value={cfgMaxPositions}
                  onChange={(e) => setCfgMaxPositions(e.target.value)}
                  className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-white font-mono focus:border-brand-500 focus:outline-none"
                  required
                />
                <span className="text-[10px] text-gray-500">Limite prudencial de posições abertas ao mesmo tempo em modo real.</span>
              </div>

              <div>
                <label className="block text-gray-300 font-semibold mb-1">
                  Slippage Máximo Tolerado (%)
                </label>
                <input
                  type="number"
                  step="0.1"
                  min="0.5"
                  max="5.0"
                  value={cfgMaxSlippage}
                  onChange={(e) => setCfgMaxSlippage(e.target.value)}
                  className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-white font-mono focus:border-brand-500 focus:outline-none"
                  required
                />
                <span className="text-[10px] text-gray-500">Tolerância a oscilação de preço durante a execução (1.0% a 2.0% recomendado).</span>
              </div>

              <div>
                <label className="block text-gray-300 font-semibold mb-1">
                  Gorjeta Jito MEV (Lamports)
                </label>
                <input
                  type="number"
                  step="10000"
                  min="10000"
                  max="5000000"
                  value={cfgJitoTip}
                  onChange={(e) => setCfgJitoTip(e.target.value)}
                  className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-white font-mono focus:border-brand-500 focus:outline-none"
                  required
                />
                <span className="text-[10px] text-gray-500">Tip para os validadores Jito na Solana (100.000 lamports = 0.0001 SOL).</span>
              </div>

              <div className="pt-2 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={handleCloseConfig}
                  className="px-4 py-2 rounded-xl bg-surface hover:bg-surface-hover text-gray-300 font-semibold transition-colors"
                >
                  Cancelar
                </button>

                <button
                  type="submit"
                  disabled={actionLoading === "save_config"}
                  className="px-4 py-2 rounded-xl bg-brand-500 hover:bg-brand-600 text-white font-semibold transition-all shadow-md shadow-brand-500/20 inline-flex items-center gap-1.5"
                >
                  {actionLoading === "save_config" && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  <span>Salvar Parâmetros</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal Conectar Carteira On-Chain */}
      {isConnectWalletOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
          <div className="w-full max-w-md bg-surface-card border border-border rounded-2xl p-6 space-y-5 shadow-2xl animate-in fade-in zoom-in duration-200">
            <div className="flex items-center justify-between border-b border-border pb-3">
              <div className="flex items-center gap-2">
                <Key className="w-5 h-5 text-emerald-400" />
                <h3 className="text-base font-bold text-white">Conectar Carteira On-Chain</h3>
              </div>
              <button
                onClick={() => {
                  setIsConnectWalletOpen(false);
                  setPrivateKeyInput("");
                  setShowPrivateKey(false);
                }}
                className="text-gray-400 hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleConnectWallet} className="space-y-4 text-xs">
              <div>
                <label className="block text-gray-300 font-semibold mb-1.5">
                  Rede Blockchain
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setWalletChainToConnect("solana")}
                    className={`py-2 px-3 rounded-xl border text-xs font-semibold flex items-center justify-center gap-2 transition-all ${
                      walletChainToConnect === "solana"
                        ? "bg-emerald-500/20 border-emerald-500 text-emerald-300 shadow-md shadow-emerald-500/10"
                        : "bg-surface border-border text-gray-400 hover:text-white"
                    }`}
                  >
                    <span className="w-2 h-2 rounded-full bg-[#14F195]" />
                    <span>Solana (SOL)</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setWalletChainToConnect("evm")}
                    className={`py-2 px-3 rounded-xl border text-xs font-semibold flex items-center justify-center gap-2 transition-all ${
                      walletChainToConnect === "evm"
                        ? "bg-brand-500/20 border-brand-500 text-brand-300 shadow-md shadow-brand-500/10"
                        : "bg-surface border-border text-gray-400 hover:text-white"
                    }`}
                  >
                    <span className="w-2 h-2 rounded-full bg-[#28A0F0]" />
                    <span>Arbitrum / Base (EVM)</span>
                  </button>
                </div>
              </div>

              <div>
                <label className="block text-gray-300 font-semibold mb-1">
                  Chave Privada {walletChainToConnect === "solana" ? "(Base58)" : "(Hexadecimal 0x...)"}
                </label>
                <div className="relative">
                  <input
                    type={showPrivateKey ? "text" : "password"}
                    value={privateKeyInput}
                    onChange={(e) => setPrivateKeyInput(e.target.value)}
                    placeholder={walletChainToConnect === "solana" ? "Ex: 2uD... ou [1, 2, ...]" : "Ex: 0x..."}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2.5 pr-10 text-white font-mono focus:border-brand-500 focus:outline-none"
                    required
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPrivateKey(!showPrivateKey)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-white"
                    title={showPrivateKey ? "Ocultar Chave" : "Revelar Chave"}
                  >
                    {showPrivateKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              <div className="p-3 rounded-xl bg-amber-500/10 border border-amber-500/30 text-[11px] text-amber-300 space-y-1">
                <span className="font-semibold block">⚠️ Onde obter a Chave Privada:</span>
                <p className="text-gray-300 leading-relaxed">
                  {walletChainToConnect === "solana" ? (
                    <>
                      Na <strong>Phantom</strong>: Configurações ⚙️ &gt; <em>Gerenciar Contas</em> &gt; selecione sua conta &gt; <em>Exportar Chave Privada</em> (digite sua senha). <br />
                      <span className="text-amber-400 font-medium">Atenção:</span> Não cole o <em>Endereço da Conta</em> (~44 chars) nem a frase de 12 palavras. A chave tem ~88 caracteres.
                    </>
                  ) : (
                    <>
                      Na <strong>MetaMask / Rabby</strong>: Menu da conta &gt; <em>Detalhes da conta</em> &gt; <em>Exportar chave privada</em> (66 caracteres iniciando com 0x).
                    </>
                  )}
                </p>
              </div>

              <div className="p-3 rounded-xl bg-surface border border-border/70 text-[11px] text-gray-400 space-y-1">
                <div className="flex items-center gap-1.5 text-emerald-400 font-semibold">
                  <Lock className="w-3.5 h-3.5" />
                  <span>Segurança em Memória Volátil</span>
                </div>
                <p>
                  Sua chave privada será carregada e mantida estritamente na memória volátil do processo de execução local. Ela nunca é salva no banco de dados SQLite, arquivos JSON ou logs.
                </p>
              </div>


              <div className="pt-2 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setIsConnectWalletOpen(false);
                    setPrivateKeyInput("");
                    setShowPrivateKey(false);
                  }}
                  className="px-4 py-2 rounded-xl bg-surface hover:bg-surface-hover text-gray-300 font-semibold transition-colors"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={actionLoading === "connect_wallet" || !privateKeyInput.trim()}
                  className="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold transition-all shadow-md shadow-emerald-600/20 inline-flex items-center gap-1.5 disabled:opacity-50"
                >
                  {actionLoading === "connect_wallet" && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  <span>Validar e Conectar</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal Transferir / Sacar SOL On-Chain */}
      {isTransferOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
          <div className="w-full max-w-md bg-surface-card border border-border rounded-2xl p-6 space-y-4 shadow-2xl animate-in fade-in zoom-in duration-200">
            <div className="flex items-center justify-between border-b border-border pb-3">
              <div className="flex items-center gap-2">
                <ArrowUpRight className="w-5 h-5 text-emerald-400" />
                <h3 className="text-base font-bold text-white">Transferir / Sacar SOL On-Chain</h3>
              </div>
              <button
                onClick={() => {
                  setIsTransferOpen(false);
                  setTransferTxResult(null);
                  setTransferError(null);
                }}
                className="text-gray-400 hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {transferTxResult ? (
              <div className="space-y-4 text-center py-4">
                <div className="w-12 h-12 rounded-full bg-emerald-500/20 border border-emerald-500/40 text-emerald-400 flex items-center justify-center mx-auto">
                  <CheckCircle2 className="w-6 h-6" />
                </div>
                <div className="space-y-1">
                  <h4 className="text-base font-bold text-white">Transferência Enviada com Sucesso!</h4>
                  <p className="text-xs text-gray-400">
                    A transação foi confirmada e propagada na blockchain Solana.
                  </p>
                </div>
                <div className="p-3 rounded-xl bg-surface border border-border text-xs text-left space-y-1 font-mono">
                  <div className="flex justify-between text-gray-400">
                    <span>Destino:</span>
                    <span className="text-white">{shortenAddress(transferTxResult.recipient, 6)}</span>
                  </div>
                  <div className="flex justify-between text-gray-400">
                    <span>Assinatura Tx:</span>
                    <span className="text-brand-400">{shortenAddress(transferTxResult.hash, 6)}</span>
                  </div>
                </div>
                <div className="flex items-center justify-center gap-3 pt-2">
                  <a
                    href={transferTxResult.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white shadow-md shadow-brand-600/20 transition-all"
                  >
                    <span>Ver no Solscan</span>
                    <ExternalLink className="w-3.5 h-3.5" />
                  </a>
                  <button
                    onClick={() => {
                      setIsTransferOpen(false);
                      setTransferTxResult(null);
                    }}
                    className="px-4 py-2 rounded-xl text-xs font-semibold bg-surface hover:bg-surface-hover text-gray-300 transition-colors"
                  >
                    Concluir
                  </button>
                </div>
              </div>
            ) : (
              <form onSubmit={handleTransferSubmit} className="space-y-4">
                {/* Carteira de Origem */}
                {(() => {
                  const solWallet = wallets.find((w) => w.chain.toLowerCase() === "solana");
                  const isConn = Boolean(solWallet?.is_connected);
                  const solBal = solWallet?.balance_native ?? 0;
                  const solAddr = solWallet?.address || "";

                  return (
                    <div className="p-3 rounded-xl bg-surface border border-border space-y-1.5 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="text-gray-400">Carteira de Origem:</span>
                        <span className="font-mono font-semibold text-white">
                          {isConn ? shortenAddress(solAddr, 6) : "Chave avulsa informada abaixo"}
                        </span>
                      </div>
                      <div className="flex items-center justify-between pt-1 border-t border-border/50">
                        <span className="text-gray-400">Saldo Disponível:</span>
                        <span className="font-mono font-bold text-emerald-400">
                          {solBal} SOL {solWallet?.balance_usd ? `(${formatUSD(solWallet.balance_usd)})` : ""}
                        </span>
                      </div>
                    </div>
                  );
                })()}

                {/* Destino */}
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-gray-300 block">
                    Endereço de Destino (Solana) *
                  </label>
                  <input
                    type="text"
                    value={transferRecipient}
                    onChange={(e) => {
                      setTransferRecipient(e.target.value);
                      if (transferError) setTransferError(null);
                    }}
                    placeholder="Cole o endereço da sua conta na Phantom (ex: 7xKX...)"
                    className="w-full px-3 py-2 text-xs bg-surface border border-border rounded-xl text-white placeholder-gray-500 focus:outline-none focus:border-brand-500 font-mono"
                    required
                  />
                </div>

                {/* Quantidade */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-medium text-gray-300">
                      Quantidade a Enviar (SOL) *
                    </label>
                    {(() => {
                      const solWallet = wallets.find((w) => w.chain.toLowerCase() === "solana");
                      const maxSendable = Math.max(0, (solWallet?.balance_native ?? 0) - 0.000005);
                      return (
                        <button
                          type="button"
                          onClick={() => {
                            setTransferAmount(maxSendable > 0 ? maxSendable.toFixed(6) : "0");
                            setTransferSendAll(true);
                            if (transferError) setTransferError(null);
                          }}
                          className="text-[11px] font-semibold text-brand-400 hover:text-brand-300 hover:underline inline-flex items-center gap-1"
                        >
                          <Coins className="w-3 h-3" />
                          <span>Máximo (Enviar Tudo)</span>
                        </button>
                      );
                    })()}
                  </div>

                  <div className="relative">
                    <input
                      type="text"
                      value={transferAmount}
                      onChange={(e) => {
                        setTransferAmount(e.target.value);
                        setTransferSendAll(false);
                        if (transferError) setTransferError(null);
                      }}
                      placeholder="Ex: 0.05 ou clique em Máximo"
                      className="w-full px-3 py-2 text-xs bg-surface border border-border rounded-xl text-white placeholder-gray-500 focus:outline-none focus:border-brand-500 font-mono"
                      required={!transferSendAll}
                    />
                  </div>
                  {transferSendAll && (
                    <p className="text-[11px] text-emerald-400 font-medium">
                      ✓ Enviando saldo total com dedução automática da taxa de rede (~0.000005 SOL).
                    </p>
                  )}
                </div>

                {/* Chave Privada Opcional se já conectada, obrigatória se desconectada */}
                {(() => {
                  const solWallet = wallets.find((w) => w.chain.toLowerCase() === "solana");
                  const isConn = Boolean(solWallet?.is_connected);
                  if (isConn) return null;

                  return (
                    <div className="space-y-1.5 pt-1">
                      <label className="text-xs font-medium text-amber-300 flex items-center gap-1">
                        <Key className="w-3.5 h-3.5" />
                        <span>Chave Privada de Origem (Base58) *</span>
                      </label>
                      <div className="relative">
                        <input
                          type={showTransferKey ? "text" : "password"}
                          value={transferPrivateKey}
                          onChange={(e) => {
                            setTransferPrivateKey(e.target.value);
                            if (transferError) setTransferError(null);
                          }}
                          placeholder="Cole a chave privada da carteira local que contém o SOL"
                          className="w-full px-3 py-2 pr-10 text-xs bg-surface border border-border rounded-xl text-white placeholder-gray-500 focus:outline-none focus:border-brand-500 font-mono"
                          required
                        />
                        <button
                          type="button"
                          onClick={() => setShowTransferKey(!showTransferKey)}
                          className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-white"
                        >
                          {showTransferKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                        </button>
                      </div>
                    </div>
                  );
                })()}

                {/* Taxa estimada */}
                <div className="p-2.5 rounded-xl bg-surface/60 border border-border/70 text-[11px] text-gray-400 flex items-center justify-between">
                  <span>Taxa de rede estimada:</span>
                  <span className="font-mono text-gray-200">~0.000005 SOL (&lt; $0.001)</span>
                </div>

                {/* Erro */}
                {transferError && (
                  <div className="p-3 rounded-xl bg-loss/10 border border-loss/30 text-loss text-xs flex items-start gap-2">
                    <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                    <span>{transferError}</span>
                  </div>
                )}

                {/* Ações */}
                <div className="pt-2 flex items-center justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setIsTransferOpen(false);
                      setTransferError(null);
                    }}
                    className="px-4 py-2 rounded-xl text-xs bg-surface hover:bg-surface-hover text-gray-300 font-semibold transition-colors"
                  >
                    Cancelar
                  </button>
                  <button
                    type="submit"
                    disabled={transferLoading || !transferRecipient.trim()}
                    className="px-4 py-2 rounded-xl text-xs bg-emerald-600 hover:bg-emerald-500 text-white font-semibold transition-all shadow-md shadow-emerald-600/20 inline-flex items-center gap-1.5 disabled:opacity-50"
                  >
                    {transferLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
                    <span>Confirmar e Transmitir</span>
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
