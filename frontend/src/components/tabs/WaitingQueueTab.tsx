"use client";

import { Clock, ExternalLink, Info, ShieldCheck } from "lucide-react";
import { WaitingTokenItem } from "@/types/bot";
import { formatUSD, shortenAddress, formatTimeAgo } from "@/lib/formatters";

interface WaitingQueueTabProps {
  waitingTokens: WaitingTokenItem[];
}

export function WaitingQueueTab({ waitingTokens }: WaitingQueueTabProps) {
  if (waitingTokens.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-12 rounded-2xl border border-dashed border-border bg-surface/50 text-center">
        <div className="w-12 h-12 rounded-full bg-surface-card border border-border flex items-center justify-center text-gray-400 mb-3">
          <Clock className="w-6 h-6" />
        </div>
        <h3 className="text-base font-semibold text-white">Fila de Espera Vazia</h3>
        <p className="text-sm text-gray-400 max-w-sm mt-1">
          Todos os tokens aprovados que encontraram vagas e saldo foram executados imediatamente. Quando os slots de operação estiverem cheios, novos tokens aprovados aguardarão aqui.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Banner Explicativo */}
      <div className="p-3.5 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-start gap-3">
        <Info className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
        <div className="text-xs text-gray-300 space-y-0.5">
          <span className="font-semibold text-amber-300 block">
            Fila de Espera Ativa ({waitingTokens.length} tokens aguardando)
          </span>
          <p>
            Estes tokens passaram por 100% dos Hard Gates de segurança e validação quantitativa. Assim que posições existentes forem fechadas e liberarem slots ou caixa, o bot executará as ordens automaticamente.
          </p>
        </div>
      </div>

      {/* Tabela de Tokens em Espera */}
      <div className="rounded-xl border border-border bg-surface-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface border-b border-border text-gray-400 font-medium">
              <tr>
                <th className="py-3 px-4">Token / Par</th>
                <th className="py-3 px-4">Estratégia Pretendida</th>
                <th className="py-3 px-4">Motivo da Espera</th>
                <th className="py-3 px-4">Liquidez</th>
                <th className="py-3 px-4">Idade do Token</th>
                <th className="py-3 px-4">Enfileirado Há</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {waitingTokens.map((item, idx) => {
                const address = item.address || item.token_address || "";
                const symbol = item.symbol || item.token_symbol || shortenAddress(address);
                const name = item.name && item.name !== "N/A" ? item.name : "";
                const dexUrl = `https://dexscreener.com/solana/${address}`;
                const liquidity = item.initial_liquidity_usd ?? item.liquidity_usd ?? 0;
                const reason = item.waiting_reason || item.reason_pending || "AGUARDANDO_SLOT";
                const enqueuedAt = item.enqueued_at || item.added_at || "";
                const strategy = item.eligible_strategy || "DUAL";
                const isDual = strategy === "DUAL";
                const isSwing = strategy === "SWING";
                const isSlot = reason.includes("SLOT") || reason.includes("Vaga") || reason === "AGUARDANDO_SLOT";

                return (
                  <tr key={idx} className="hover:bg-surface-hover/50 transition-colors">
                    <td className="py-3 px-4">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="font-bold text-white">
                          {symbol}
                        </span>
                        {name && (
                          <span className="text-gray-400 text-xs font-normal">
                            ({name})
                          </span>
                        )}
                        {address && (
                          <a
                            href={dexUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-gray-400 hover:text-brand-300 font-mono inline-flex items-center"
                            title="Abrir Gráfico no DexScreener"
                          >
                            <ExternalLink className="w-3.5 h-3.5 inline" />
                          </a>
                        )}
                      </div>
                      {address && (
                        <span className="text-[10px] text-gray-400 font-mono block">
                          {shortenAddress(address, 8)}
                        </span>
                      )}
                    </td>

                    <td className="py-3 px-4">
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                          isDual
                            ? "bg-brand-500/15 text-brand-300 border border-brand-500/30"
                            : isSwing
                            ? "bg-swing/15 text-swing border border-swing/30"
                            : "bg-scalp/15 text-scalp border border-scalp/30"
                        }`}
                      >
                        {strategy}
                      </span>
                    </td>

                    <td className="py-3 px-4">
                      <span
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium border ${
                          isSlot
                            ? "bg-amber-500/10 border-amber-500/30 text-amber-300"
                            : "bg-brand-500/10 border-brand-500/30 text-brand-300"
                        }`}
                      >
                        <Clock className="w-3 h-3" />
                        {isSlot
                          ? "Aguardando Vaga (Slots Cheios)"
                          : reason === "AGUARDANDO_SALDO"
                          ? "Aguardando Saldo em Caixa"
                          : reason}
                      </span>
                    </td>

                    <td className="py-3 px-4 font-mono text-white">
                      {formatUSD(liquidity)}
                    </td>

                    <td className="py-3 px-4 font-mono text-gray-300">
                      {item.age_hours ? `${item.age_hours.toFixed(1)}h` : "—"}
                    </td>

                    <td className="py-3 px-4 text-gray-400 font-mono">
                      {formatTimeAgo(enqueuedAt)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

