"use client";

import { ShieldCheck, AlertCircle, Zap, Lock, Cpu } from "lucide-react";

export function RealDashboardView() {
  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      {/* Banner Principal */}
      <div className="p-8 rounded-2xl border border-brand-500/30 bg-gradient-to-b from-brand-500/10 to-surface-card text-center space-y-4">
        <div className="w-16 h-16 rounded-2xl bg-brand-500/10 border border-brand-500/40 flex items-center justify-center text-brand-400 mx-auto">
          <Zap className="w-8 h-8 animate-pulse" />
        </div>

        <div className="space-y-2">
          <span className="text-xs font-mono font-bold px-3 py-1 rounded-full bg-brand-500/20 text-brand-300 border border-brand-500/40 uppercase tracking-wider">
            Operações Reais (Live Trading)
          </span>
          <h2 className="text-2xl font-bold text-white tracking-tight">
            Módulo de Operação Real em Breve
          </h2>
          <p className="text-sm text-gray-400 max-w-lg mx-auto">
            O ambiente de operações reais é mantido estritamente isolado do sandbox de simulação. Todos os testes, ajustes de estratégia e métricas de PnL atuais ocorrem na aba <strong className="text-brand-300">Simulação</strong>.
          </p>
        </div>
      </div>

      {/* Grid de Requisitos e Arquitetura de Segurança */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
        <div className="p-5 rounded-xl border border-border bg-surface-card space-y-2">
          <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-400 w-fit">
            <Lock className="w-5 h-5" />
          </div>
          <h4 className="font-semibold text-white">Assinatura Local de Chaves</h4>
          <p className="text-gray-400">
            Nenhuma chave privada é exposta ao dashboard ou logada. As transações reais são assinadas via `solders.Keypair` em memória restrita.
          </p>
        </div>

        <div className="p-5 rounded-xl border border-border bg-surface-card space-y-2">
          <div className="p-2 rounded-lg bg-brand-500/10 text-brand-400 w-fit">
            <Cpu className="w-5 h-5" />
          </div>
          <h4 className="font-semibold text-white">Jito Block Engine & MEV</h4>
          <p className="text-gray-400">
            Envio direto via bundles Jito protegendo ordens contra sanduíche, front-running e falhas por congestionamento RPC.
          </p>
        </div>

        <div className="p-5 rounded-xl border border-border bg-surface-card space-y-2">
          <div className="p-2 rounded-lg bg-amber-500/10 text-amber-400 w-fit">
            <ShieldCheck className="w-5 h-5" />
          </div>
          <h4 className="font-semibold text-white">Hard Gates Eliminatórios</h4>
          <p className="text-gray-400">
            Mint revogada, Freeze revogada, LP &ge; 98% bloqueada e Top 10 &le; 15% aplicados com tolerância zero.
          </p>
        </div>
      </div>
    </div>
  );
}

