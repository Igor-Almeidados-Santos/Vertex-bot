"use client";

import { OrderItem } from "@/types/bot";
import { formatUSD, formatNumber, shortenAddress, formatTimeAgo } from "@/lib/formatters";
import { ArrowDownLeft, ArrowUpRight, ExternalLink } from "lucide-react";

interface OrdersTabProps {
  orders: OrderItem[];
}

export function OrdersTab({ orders }: OrdersTabProps) {
  if (orders.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-12 rounded-2xl border border-dashed border-border bg-surface/50 text-center">
        <h3 className="text-base font-semibold text-white">Nenhuma Ordem Executada</h3>
        <p className="text-sm text-gray-400 max-w-sm mt-1">
          As ordens individuais de compra e venda emitidas pelo ExecutionEngine serão exibidas aqui.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border bg-surface-card overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="bg-surface border-b border-border text-gray-400 font-medium">
            <tr>
              <th className="py-3 px-4">Tipo</th>
              <th className="py-3 px-4">Token</th>
              <th className="py-3 px-4">Preço Execução</th>
              <th className="py-3 px-4">Montante ($ USD)</th>
              <th className="py-3 px-4">Quantidade Tokens</th>
              <th className="py-3 px-4">Motivo / Execução</th>
              <th className="py-3 px-4">Data / Hora</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {orders.map((ord) => {
              const isBuy = ord.order_type === "BUY";
              const address = ord.token_address || "";
              const symbol = ord.token_symbol || ord.symbol || shortenAddress(address);
              const name = ord.name && ord.name !== "N/A" ? ord.name : "";
              const dexUrl = `https://dexscreener.com/solana/${address}`;
              const amountUsd = ord.amount_usd ?? ord.total_usd ?? 0;
              const tokenAmount = ord.tokens_amount ?? ord.amount ?? 0;
              const reason = ord.reason || ord.notes || (isBuy ? "Entrada na Posição" : "Encerramento da Posição");
              const timestamp = ord.timestamp || ord.executed_at || "";

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

                  <td className="py-3 px-4 font-mono text-gray-300">
                    {formatUSD(ord.price, 6)}
                  </td>

                  <td className="py-3 px-4 font-mono text-white font-bold">
                    {formatUSD(amountUsd)}
                  </td>

                  <td className="py-3 px-4 font-mono text-gray-300">
                    {formatNumber(tokenAmount, 2)}
                  </td>

                  <td className="py-3 px-4">
                    <span className="text-[11px] font-mono text-gray-400">
                      {reason}
                    </span>
                  </td>

                  <td className="py-3 px-4 font-mono text-gray-400 text-[11px]">
                    {formatTimeAgo(timestamp)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

