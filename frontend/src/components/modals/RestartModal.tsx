"use client";

import { useState } from "react";
import { AlertTriangle, RotateCcw, X } from "lucide-react";
import { restartSimulation } from "@/lib/api";

interface RestartModalProps {
  isOpen: boolean;
  onClose: () => void;
  currentInitialWallet: number;
  onRestarted: () => void;
}

export function RestartModal({
  isOpen,
  onClose,
  currentInitialWallet,
  onRestarted,
}: RestartModalProps) {
  const [walletAmount, setWalletAmount] = useState<number>(currentInitialWallet || 10.0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleRestart = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await restartSimulation(walletAmount);
      onRestarted();
      onClose();
    } catch (err: any) {
      setError(err.message || "Falha ao reiniciar simulação.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="relative w-full max-w-md rounded-2xl border border-border bg-surface-card shadow-2xl p-6 space-y-4">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-amber-500/15 border border-amber-500/30 text-amber-400">
              <AlertTriangle className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-bold text-white">Reiniciar Simulação</h3>
              <p className="text-xs text-gray-400">Zerar sessão de Paper Trading</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-gray-400 hover:text-white transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-3 rounded-xl bg-surface border border-border text-xs text-gray-300 space-y-1">
          <p>Esta ação irá:</p>
          <ul className="list-disc list-inside text-gray-400 space-y-0.5 font-mono text-[11px]">
            <li>Encerrar todas as posições simuladas abertas.</li>
            <li>Zerar métricas de PnL e histórico da sessão ativa.</li>
            <li>Reiniciar a sequência de IDs a partir do #1.</li>
          </ul>
        </div>

        {error && (
          <div className="p-2.5 rounded-lg bg-loss/10 border border-loss/30 text-xs text-loss">
            {error}
          </div>
        )}

        <form onSubmit={handleRestart} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-gray-300 mb-1">
              Banca Inicial da Nova Sessão ($ USD)
            </label>
            <input
              type="number"
              step="0.5"
              min="1.0"
              required
              value={walletAmount}
              onChange={(e) => setWalletAmount(parseFloat(e.target.value))}
              className="w-full bg-surface border border-border rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-brand-400 font-mono"
            />
          </div>

          <div className="flex items-center justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="px-4 py-2 text-xs font-medium rounded-xl border border-border bg-surface hover:bg-surface-hover text-gray-300 transition-colors"
            >
              Cancelar
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-2 text-xs font-semibold rounded-xl bg-amber-500 hover:bg-amber-600 text-black flex items-center gap-1.5 transition-colors disabled:opacity-50"
            >
              <RotateCcw className="w-4 h-4" />
              <span>{submitting ? "Reiniciando..." : "Confirmar e Zerar"}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

