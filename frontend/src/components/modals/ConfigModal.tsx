"use client";

import { useState, useEffect } from "react";
import { X, Save, Shield, Settings2, Sliders, Zap, TrendingUp, RefreshCw, CheckCircle2, AlertCircle } from "lucide-react";
import { BotSettings } from "@/types/bot";
import { updateConfig } from "@/lib/api";

interface ConfigModalProps {
  isOpen: boolean;
  onClose: () => void;
  currentSettings: BotSettings | undefined;
  onSaved: () => void;
}

export function ConfigModal({ isOpen, onClose, currentSettings, onSaved }: ConfigModalProps) {
  const [activeTab, setActiveTab] = useState<"ENTRY" | "SCALP" | "SWING" | "REENTRY" | "MOMENTUM" | "WALLET">("ENTRY");
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);

  // Form State
  const [formData, setFormData] = useState<Record<string, any>>({});

  useEffect(() => {
    if (currentSettings) {
      setFormData({
        trading_strategy_mode: currentSettings.trading_strategy_mode || "DUAL",
        paper_buy_amount_usd: currentSettings.paper_buy_amount_usd ?? 1.0,
        max_concurrent_positions: currentSettings.max_concurrent_positions ?? 50,
        max_slippage_pct: currentSettings.max_slippage_pct ?? 1.5,
        max_top10_holders_pct: currentSettings.max_top10_holders_pct ?? 15.0,
        min_liquidity_usd: currentSettings.min_liquidity_usd ?? 5000.0,

        // Scalp
        scalp_max_hold_minutes: currentSettings.scalp_max_hold_minutes ?? 60.0,
        scalp_target_gain_pct: currentSettings.scalp_target_gain_pct ?? 100.0,
        trailing_stop_drop_pct: currentSettings.trailing_stop_drop_pct ?? 12.0,
        emergency_stop_loss_pct: currentSettings.emergency_stop_loss_pct ?? 20.0,
        break_even_gain_pct: currentSettings.break_even_gain_pct ?? 100.0,

        // Swing
        swing_max_hold_hours: currentSettings.swing_max_hold_hours ?? 24.0,
        swing_target_gain_pct: currentSettings.swing_target_gain_pct ?? 2000.0,
        swing_max_hourly_drop_pct: currentSettings.swing_max_hourly_drop_pct ?? 15.0,
        swing_initial_stop_loss_pct: currentSettings.swing_initial_stop_loss_pct ?? 0.0,
        swing_trailing_drop_pct: currentSettings.swing_trailing_drop_pct ?? 25.0,
        swing_tier1_mult: currentSettings.swing_tier1_mult ?? 2.0,
        swing_tier2_mult: currentSettings.swing_tier2_mult ?? 4.0,
        swing_tier3_mult: currentSettings.swing_tier3_mult ?? 6.0,
        swing_tier4_mult: currentSettings.swing_tier4_mult ?? 11.0,
        swing_tier5_mult: currentSettings.swing_tier5_mult ?? 21.0,

        // Reentry
        reentry_trailing_cooloff_min: currentSettings.reentry_trailing_cooloff_min ?? 5.0,
        reentry_stoploss_cooloff_min: currentSettings.reentry_stoploss_cooloff_min ?? 30.0,
        reentry_min_bounce_pct: currentSettings.reentry_min_bounce_pct ?? 3.0,

        // Momentum
        min_volume_1h_usd: currentSettings.min_volume_1h_usd ?? 15000.0,
        min_buy_ratio_5m_pct: currentSettings.min_buy_ratio_5m_pct ?? 50.0,
        min_price_change_5m_pct: currentSettings.min_price_change_5m_pct ?? -2.0,
        min_liquidity_swing_usd: currentSettings.min_liquidity_swing_usd ?? 20000.0,

        // Wallet
        paper_initial_wallet_usd:
          currentSettings.wallet_balance_usd ?? currentSettings.paper_initial_wallet_usd ?? 10.0,
      });
    }
  }, [currentSettings, isOpen]);

  if (!isOpen) return null;

  const handleChange = (field: string, value: any) => {
    setFormData((prev) => ({ ...prev, [field]: value }));
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setToast(null);
    try {
      await updateConfig(formData);
      setToast({ type: "success", message: "Configurações salvas e ativas com sucesso!" });
      onSaved();
      setTimeout(() => {
        onClose();
        setToast(null);
      }, 1200);
    } catch (err: any) {
      setToast({ type: "error", message: err.message || "Erro ao salvar configurações" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4 overflow-y-auto">
      <div className="relative w-full max-w-3xl rounded-2xl border border-border bg-surface-card shadow-2xl flex flex-col max-h-[90vh] overflow-hidden">
        {/* Header do Modal */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border bg-surface">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-xl bg-brand-500/10 border border-brand-500/30 text-brand-400">
              <Settings2 className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base font-bold text-white">Configurações Estratégicas</h2>
              <p className="text-xs text-gray-400">
                Ajuste parâmetros operacionais, gestão de risco e carteira
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-surface-hover transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Notificação Toast interna */}
        {toast && (
          <div
            className={`px-4 py-2.5 text-xs font-medium flex items-center gap-2 ${
              toast.type === "success"
                ? "bg-profit/15 text-profit border-b border-profit/30"
                : "bg-loss/15 text-loss border-b border-loss/30"
            }`}
          >
            {toast.type === "success" ? (
              <CheckCircle2 className="w-4 h-4 shrink-0" />
            ) : (
              <AlertCircle className="w-4 h-4 shrink-0" />
            )}
            <span>{toast.message}</span>
          </div>
        )}

        {/* Abas do Modal */}
        <div className="flex items-center gap-1 px-6 py-2 border-b border-border bg-surface/50 overflow-x-auto text-xs">
          <button
            type="button"
            onClick={() => setActiveTab("ENTRY")}
            className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
              activeTab === "ENTRY" ? "bg-brand-500 text-white shadow-sm" : "text-gray-400 hover:text-white"
            }`}
          >
            1. Entrada & Alocação
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("SCALP")}
            className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
              activeTab === "SCALP" ? "bg-scalp text-black font-semibold shadow-sm" : "text-gray-400 hover:text-white"
            }`}
          >
            2. Gestão SCALP
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("SWING")}
            className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
              activeTab === "SWING" ? "bg-swing text-white shadow-sm" : "text-gray-400 hover:text-white"
            }`}
          >
            3. Gestão SWING
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("REENTRY")}
            className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
              activeTab === "REENTRY" ? "bg-brand-500 text-white shadow-sm" : "text-gray-400 hover:text-white"
            }`}
          >
            4. Reentrada
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("MOMENTUM")}
            className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
              activeTab === "MOMENTUM" ? "bg-brand-500 text-white shadow-sm" : "text-gray-400 hover:text-white"
            }`}
          >
            5. Filtros Momento
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("WALLET")}
            className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
              activeTab === "WALLET" ? "bg-brand-500 text-white shadow-sm" : "text-gray-400 hover:text-white"
            }`}
          >
            6. Carteira
          </button>
        </div>

        {/* Corpo do Formulário */}
        <form onSubmit={handleSave} className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* TAB 1: ENTRADA & ALOCAÇÃO */}
          {activeTab === "ENTRY" && (
            <div className="space-y-4">
              <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                <Sliders className="w-4 h-4 text-brand-400" /> Parâmetros de Entrada & Alocação
              </h3>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Modo Operacional da Estratégia
                  </label>
                  <select
                    value={formData.trading_strategy_mode}
                    onChange={(e) => handleChange("trading_strategy_mode", e.target.value)}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-medium"
                  >
                    <option value="DUAL">DUAL SIMULTÂNEO (Scalp + Swing com valor integral)</option>
                    <option value="SCALP_ONLY">SCALP ONLY (Apenas operações rápidas de até 1h)</option>
                    <option value="SWING_ONLY">SWING ONLY (Apenas posições longas de 2h a 24h)</option>
                  </select>
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Compra por Posição ($ USD)
                  </label>
                  <input
                    type="number"
                    step="0.1"
                    min="0.1"
                    value={formData.paper_buy_amount_usd}
                    onChange={(e) => handleChange("paper_buy_amount_usd", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                  <span className="text-[10px] text-gray-500">
                    No modo DUAL, aloca este valor cheio tanto no Scalp quanto no Swing.
                  </span>
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Máximo de Slots Concomitantes
                  </label>
                  <input
                    type="number"
                    min="1"
                    max="100"
                    value={formData.max_concurrent_positions}
                    onChange={(e) => handleChange("max_concurrent_positions", parseInt(e.target.value, 10))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Slippage Máximo Permitido (%)
                  </label>
                  <input
                    type="number"
                    step="0.1"
                    min="0.1"
                    max="10.0"
                    value={formData.max_slippage_pct}
                    onChange={(e) => handleChange("max_slippage_pct", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Concentração Máx. Top 10 Holders (%)
                  </label>
                  <input
                    type="number"
                    step="0.5"
                    min="1.0"
                    max="50.0"
                    value={formData.max_top10_holders_pct}
                    onChange={(e) => handleChange("max_top10_holders_pct", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Liquidez Mínima Inicial ($ USD)
                  </label>
                  <input
                    type="number"
                    step="500"
                    min="1000"
                    value={formData.min_liquidity_usd}
                    onChange={(e) => handleChange("min_liquidity_usd", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: GESTÃO SCALP */}
          {activeTab === "SCALP" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                  <Zap className="w-4 h-4 text-scalp" /> Parâmetros de Gestão SCALP
                </h3>
                <div className="flex gap-1.5 text-[11px] font-mono">
                  <span className="px-2 py-0.5 rounded bg-scalp/15 text-scalp border border-scalp/30">
                    Entrada: 30m a 720h (Fixo)
                  </span>
                  <span className="px-2 py-0.5 rounded bg-scalp/15 text-scalp border border-scalp/30">
                    Tempo Máx: 1h (Fixo)
                  </span>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Alvo de Lucro / Take Profit (%)
                  </label>
                  <input
                    type="number"
                    step="5"
                    min="10"
                    value={formData.scalp_target_gain_pct}
                    onChange={(e) => handleChange("scalp_target_gain_pct", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                  <span className="text-[10px] text-gray-500">Padrão: +100% (2x) com saída de 100%.</span>
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Trailing Stop Drop (%)
                  </label>
                  <input
                    type="number"
                    step="1"
                    min="1"
                    max="50"
                    value={formData.trailing_stop_drop_pct}
                    onChange={(e) => handleChange("trailing_stop_drop_pct", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Stop-Loss Emergencial (%)
                  </label>
                  <input
                    type="number"
                    step="1"
                    min="5"
                    max="95"
                    value={formData.emergency_stop_loss_pct}
                    onChange={(e) => handleChange("emergency_stop_loss_pct", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Break-Even Gain (%)
                  </label>
                  <input
                    type="number"
                    step="5"
                    min="10"
                    value={formData.break_even_gain_pct}
                    onChange={(e) => handleChange("break_even_gain_pct", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>
              </div>
            </div>
          )}

          {/* TAB 3: GESTÃO SWING */}
          {activeTab === "SWING" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                  <TrendingUp className="w-4 h-4 text-swing" /> Parâmetros de Gestão SWING (Catraca Ratchet)
                </h3>
                <span className="px-2 py-0.5 rounded bg-swing/15 text-swing border border-swing/30 text-[11px] font-mono">
                  Entrada: 2h a 4h de vida (Fixo)
                </span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Tempo Máximo de Posição (Horas)
                  </label>
                  <input
                    type="number"
                    min="1"
                    max="72"
                    value={formData.swing_max_hold_hours}
                    onChange={(e) => handleChange("swing_max_hold_hours", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                  <span className="text-[10px] text-gray-500">Padrão da estratégia: 24h.</span>
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Alvo Master de Saída (%)
                  </label>
                  <input
                    type="number"
                    step="100"
                    min="500"
                    value={formData.swing_target_gain_pct}
                    onChange={(e) => handleChange("swing_target_gain_pct", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                  <span className="text-[10px] text-gray-500">Padrão: +2.000% (21x).</span>
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Queda Máxima Permitida em 1h (% anti-dump)
                  </label>
                  <input
                    type="number"
                    step="1"
                    min="5"
                    max="50"
                    value={formData.swing_max_hourly_drop_pct}
                    onChange={(e) => handleChange("swing_max_hourly_drop_pct", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Trailing Stop Elástico (%)
                  </label>
                  <input
                    type="number"
                    step="1"
                    min="5"
                    max="40"
                    value={formData.swing_trailing_drop_pct}
                    onChange={(e) => handleChange("swing_trailing_drop_pct", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>
              </div>

              {/* Degraus de Multiplicadores da Catraca */}
              <div className="pt-2 border-t border-border">
                <span className="block text-xs font-semibold text-gray-200 mb-2">
                  Multiplicadores de Alvo dos Degraus (Ratchet Tiers)
                </span>
                <div className="grid grid-cols-5 gap-2 font-mono text-xs">
                  <div>
                    <label className="block text-[10px] text-gray-400 mb-1">Degrau 1</label>
                    <input
                      type="number"
                      step="0.5"
                      value={formData.swing_tier1_mult}
                      onChange={(e) => handleChange("swing_tier1_mult", parseFloat(e.target.value))}
                      className="w-full bg-surface border border-border rounded-lg px-2 py-1.5 text-white"
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] text-gray-400 mb-1">Degrau 2</label>
                    <input
                      type="number"
                      step="0.5"
                      value={formData.swing_tier2_mult}
                      onChange={(e) => handleChange("swing_tier2_mult", parseFloat(e.target.value))}
                      className="w-full bg-surface border border-border rounded-lg px-2 py-1.5 text-white"
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] text-gray-400 mb-1">Degrau 3</label>
                    <input
                      type="number"
                      step="0.5"
                      value={formData.swing_tier3_mult}
                      onChange={(e) => handleChange("swing_tier3_mult", parseFloat(e.target.value))}
                      className="w-full bg-surface border border-border rounded-lg px-2 py-1.5 text-white"
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] text-gray-400 mb-1">Degrau 4</label>
                    <input
                      type="number"
                      step="0.5"
                      value={formData.swing_tier4_mult}
                      onChange={(e) => handleChange("swing_tier4_mult", parseFloat(e.target.value))}
                      className="w-full bg-surface border border-border rounded-lg px-2 py-1.5 text-white"
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] text-gray-400 mb-1">Degrau 5</label>
                    <input
                      type="number"
                      step="0.5"
                      value={formData.swing_tier5_mult}
                      onChange={(e) => handleChange("swing_tier5_mult", parseFloat(e.target.value))}
                      className="w-full bg-surface border border-border rounded-lg px-2 py-1.5 text-white"
                    />
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 4: REENTRADA */}
          {activeTab === "REENTRY" && (
            <div className="space-y-4">
              <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                <RefreshCw className="w-4 h-4 text-brand-400" /> Parâmetros de Reentrada Inteligente
              </h3>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Cool-off Pós-Trailing (Minutos)
                  </label>
                  <input
                    type="number"
                    min="1"
                    max="60"
                    value={formData.reentry_trailing_cooloff_min}
                    onChange={(e) => handleChange("reentry_trailing_cooloff_min", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Cool-off Pós-Stop Loss (Minutos)
                  </label>
                  <input
                    type="number"
                    min="5"
                    max="180"
                    value={formData.reentry_stoploss_cooloff_min}
                    onChange={(e) => handleChange("reentry_stoploss_cooloff_min", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Repique Mínimo de Suporte (%)
                  </label>
                  <input
                    type="number"
                    step="0.5"
                    min="1"
                    max="20"
                    value={formData.reentry_min_bounce_pct}
                    onChange={(e) => handleChange("reentry_min_bounce_pct", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>
              </div>
            </div>
          )}

          {/* TAB 5: FILTROS DE MOMENTO */}
          {activeTab === "MOMENTUM" && (
            <div className="space-y-4">
              <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                <Shield className="w-4 h-4 text-emerald-400" /> Filtros Quantitativos de Momento
              </h3>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Volume Mínimo em 1h ($ USD)
                  </label>
                  <input
                    type="number"
                    step="1000"
                    min="0"
                    value={formData.min_volume_1h_usd}
                    onChange={(e) => handleChange("min_volume_1h_usd", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Pressão Compradora Mínima em 5m (%)
                  </label>
                  <input
                    type="number"
                    step="1"
                    min="0"
                    max="100"
                    value={formData.min_buy_ratio_5m_pct}
                    onChange={(e) => handleChange("min_buy_ratio_5m_pct", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Variação Mínima em 5m (% anti-queda)
                  </label>
                  <input
                    type="number"
                    step="0.5"
                    value={formData.min_price_change_5m_pct}
                    onChange={(e) => handleChange("min_price_change_5m_pct", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-xs text-gray-300 font-medium mb-1">
                    Liquidez Mínima para Elegibilidade Swing ($ USD)
                  </label>
                  <input
                    type="number"
                    step="1000"
                    min="5000"
                    value={formData.min_liquidity_swing_usd}
                    onChange={(e) => handleChange("min_liquidity_swing_usd", parseFloat(e.target.value))}
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                  />
                </div>
              </div>
            </div>
          )}

          {/* TAB 6: CARTEIRA */}
          {activeTab === "WALLET" && (
            <div className="space-y-4">
              <h3 className="text-sm font-semibold text-white">Gestão de Carteira & Banca Inicial</h3>

              <div>
                <label className="block text-xs text-gray-300 font-medium mb-1">
                  Saldo Inicial da Carteira Simulada ($ USD)
                </label>
                <input
                  type="number"
                  step="1.0"
                  min="1.0"
                  value={formData.paper_initial_wallet_usd}
                  onChange={(e) => handleChange("paper_initial_wallet_usd", parseFloat(e.target.value))}
                  className="w-full max-w-sm bg-surface border border-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-brand-400 font-mono"
                />
                <span className="text-[10px] text-gray-500 block mt-1">
                  Ajusta a banca base utilizada para os cálculos de rendimento, alocação e proteção de capital.
                </span>
              </div>
            </div>
          )}

          {/* Footer com Botões */}
          <div className="flex items-center justify-end gap-3 pt-4 border-t border-border">
            <button
              type="button"
              onClick={onClose}
              disabled={saving}
              className="px-4 py-2 text-xs font-medium rounded-xl border border-border bg-surface hover:bg-surface-hover text-gray-300 transition-colors"
            >
              Cancelar
            </button>

            <button
              type="submit"
              disabled={saving}
              className="px-5 py-2 text-xs font-semibold rounded-xl bg-brand-500 hover:bg-brand-600 text-white shadow-lg shadow-brand-500/20 flex items-center gap-1.5 transition-all disabled:opacity-50"
            >
              <Save className="w-4 h-4" />
              <span>{saving ? "Salvando..." : "Salvar e Aplicar"}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

