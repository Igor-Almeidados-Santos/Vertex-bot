"use client";

import { TrendingUp, Zap, Layers, CheckCircle2, Shield, Settings } from "lucide-react";
import { BotSettings } from "@/types/bot";

interface StrategiesViewProps {
  settings: BotSettings | undefined;
  onOpenConfig: () => void;
}

export function StrategiesView({ settings, onOpenConfig }: StrategiesViewProps) {
  const currentMode = settings?.trading_strategy_mode || "DUAL";

  return (
    <div className="space-y-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 p-6 rounded-2xl border border-border bg-surface-card">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-xl font-bold text-white tracking-tight">
              Estratégias Quantitativas
            </h2>
            <span className="px-2.5 py-0.5 rounded text-xs font-mono font-bold bg-brand-500/20 text-brand-300 border border-brand-500/40">
              Modo Atual: {currentMode}
            </span>
          </div>
          <p className="text-xs text-gray-400 mt-1 max-w-2xl">
            O Vertex-bot opera com duas estratégias especializadas e complementares, projetadas para capturar tanto movimentos rápidos de momentum quanto expansões parabólicas.
          </p>
        </div>

        <button
          onClick={onOpenConfig}
          className="px-4 py-2 text-xs font-semibold rounded-xl bg-brand-500 hover:bg-brand-600 text-white shadow-lg shadow-brand-500/20 flex items-center gap-1.5 transition-all shrink-0"
        >
          <Settings className="w-4 h-4" />
          <span>Configurar Estratégias</span>
        </button>
      </div>

      {/* Grid de Estratégias */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* CARD 1: SCALP STRATEGY */}
        <div className="p-6 rounded-2xl border border-scalp/30 bg-surface-card/90 space-y-4 flex flex-col justify-between">
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <div className="p-2 rounded-xl bg-scalp/15 text-scalp border border-scalp/30">
                  <Zap className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-white">Estratégia SCALP</h3>
                  <span className="text-xs text-gray-400">Captura de Variação Rápida & Maturação</span>
                </div>
              </div>
              <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-scalp/15 text-scalp border border-scalp/30 uppercase">
                Operações de até 1h
              </span>
            </div>

            {/* Regras Fixas */}
            <div className="p-3.5 rounded-xl bg-surface border border-border/80 text-xs space-y-2 font-mono">
              <div className="flex items-center justify-between text-gray-300">
                <span className="text-gray-400">Janela de Idade na Entrada:</span>
                <span className="font-bold text-scalp">30 min a 720h (1 mês) [FIXO]</span>
              </div>
              <div className="flex items-center justify-between text-gray-300">
                <span className="text-gray-400">Tempo Máximo de Posição:</span>
                <span className="font-bold text-scalp">Até 1h (Timeout com saída 100%)</span>
              </div>
              <div className="flex items-center justify-between text-gray-300">
                <span className="text-gray-400">Meta de Lucro (Take Profit):</span>
                <span className="font-bold text-profit">+{settings?.scalp_target_gain_pct || 100}% (Venda 100%)</span>
              </div>
              <div className="flex items-center justify-between text-gray-300">
                <span className="text-gray-400">Trailing Stop Drop:</span>
                <span className="text-white">-{settings?.trailing_stop_drop_pct || 12}% da máxima</span>
              </div>
              <div className="flex items-center justify-between text-gray-300">
                <span className="text-gray-400">Stop-Loss Emergencial:</span>
                <span className="text-loss">-{settings?.emergency_stop_loss_pct || 20}% do preço</span>
              </div>
            </div>

            <p className="text-xs text-gray-400 leading-relaxed">
              Focada em tokens consolidados ou maduros. Acompanha o volume e a pressão compradora nos primeiros 60 minutos de operação. Se atingir o alvo de +100% ou o trailing stop, encerra 100% da posição sem deixar resíduos.
            </p>
          </div>
        </div>

        {/* CARD 2: SWING RATCHET STRATEGY */}
        <div className="p-6 rounded-2xl border border-swing/30 bg-surface-card/90 space-y-4 flex flex-col justify-between">
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <div className="p-2 rounded-xl bg-swing/15 text-swing border border-swing/30">
                  <TrendingUp className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-white">Estratégia SWING</h3>
                  <span className="text-xs text-gray-400">Condução de Degraus (Catraca Ratchet)</span>
                </div>
              </div>
              <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-swing/15 text-swing border border-swing/30 uppercase">
                Operações de até 24h
              </span>
            </div>

            {/* Regras Fixas */}
            <div className="p-3.5 rounded-xl bg-surface border border-border/80 text-xs space-y-2 font-mono">
              <div className="flex items-center justify-between text-gray-300">
                <span className="text-gray-400">Janela de Idade na Entrada:</span>
                <span className="font-bold text-swing">2.0h a 4.0h de vida [FIXO]</span>
              </div>
              <div className="flex items-center justify-between text-gray-300">
                <span className="text-gray-400">Tempo Máximo de Posição:</span>
                <span className="font-bold text-swing">Até {settings?.swing_max_hold_hours || 24}h</span>
              </div>
              <div className="flex items-center justify-between text-gray-300">
                <span className="text-gray-400">Alvo Master de Saída:</span>
                <span className="font-bold text-profit">+{settings?.swing_target_gain_pct || 2000}% (21x)</span>
              </div>
              <div className="flex items-center justify-between text-gray-300">
                <span className="text-gray-400">Trailing Stop Elástico:</span>
                <span className="text-white">-{settings?.swing_trailing_drop_pct || 25}%</span>
              </div>
              <div className="flex items-center justify-between text-gray-300">
                <span className="text-gray-400">Queda Máxima na Hora:</span>
                <span className="text-loss">-{settings?.swing_max_hourly_drop_pct || 15}% / hora</span>
              </div>
            </div>

            {/* Degraus de Catraca */}
            <div className="space-y-1">
              <span className="text-[11px] font-mono text-gray-400 block font-semibold">
                Degraus de Catraca (Piso Travado):
              </span>
              <div className="grid grid-cols-5 gap-1.5 text-center font-mono text-[10px]">
                <div className="p-1.5 rounded bg-surface border border-border">
                  <span className="text-gray-500 block">T1</span>
                  <span className="text-swing font-bold">{settings?.swing_tier1_mult || 2}x</span>
                </div>
                <div className="p-1.5 rounded bg-surface border border-border">
                  <span className="text-gray-500 block">T2</span>
                  <span className="text-swing font-bold">{settings?.swing_tier2_mult || 4}x</span>
                </div>
                <div className="p-1.5 rounded bg-surface border border-border">
                  <span className="text-gray-500 block">T3</span>
                  <span className="text-swing font-bold">{settings?.swing_tier3_mult || 6}x</span>
                </div>
                <div className="p-1.5 rounded bg-surface border border-border">
                  <span className="text-gray-500 block">T4</span>
                  <span className="text-swing font-bold">{settings?.swing_tier4_mult || 11}x</span>
                </div>
                <div className="p-1.5 rounded bg-surface border border-border">
                  <span className="text-gray-500 block">T5</span>
                  <span className="text-swing font-bold">{settings?.swing_tier5_mult || 21}x</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* CARD 3: MODO DUAL SIMULTÂNEO */}
      <div className="p-6 rounded-2xl border border-brand-500/40 bg-gradient-to-r from-scalp/10 via-surface-card to-swing/10 space-y-3">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-brand-500/20 text-brand-300 border border-brand-500/30">
            <Layers className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-base font-bold text-white">
              Modo DUAL SIMULTÂNEO — Alocação Integral
            </h3>
            <span className="text-xs text-gray-300">
              Execução paralela das duas estratégias sem divisão de 50%
            </span>
          </div>
        </div>

        <p className="text-xs text-gray-300 leading-relaxed">
          Quando um token é elegível simultaneamente para ambas as estratégias (idade entre 2h e 4h e liquidez &ge; $20.000), o orquestrador abre <strong>100% do valor definido em Compra por Posição para o SCALP</strong> E <strong>100% do valor definido para o SWING</strong>, consumindo 2 slots independentes. Se não houver vagas ou saldo suficiente, o token é direcionado para a <strong>Fila de Espera</strong> e executado automaticamente assim que posições anteriores forem liquidadas.
        </p>
      </div>
    </div>
  );
}

