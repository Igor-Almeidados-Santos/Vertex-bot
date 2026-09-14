"use client";

import { useState } from "react";
import { Terminal, Trash2, Search } from "lucide-react";
import { SystemLogItem } from "@/types/bot";

interface SystemLogsTabProps {
  logs: SystemLogItem[];
  onClearLogs: () => void;
}

export function SystemLogsTab({ logs, onClearLogs }: SystemLogsTabProps) {
  const [filterLevel, setFilterLevel] = useState<string>("ALL");
  const [search, setSearch] = useState("");

  const filtered = logs.filter((log) => {
    if (filterLevel !== "ALL" && log.level !== filterLevel) return false;
    if (search && !log.message.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  return (
    <div className="space-y-3">
      {/* Controles do Terminal */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-2">
        <div className="flex items-center gap-2 w-full sm:w-auto">
          <div className="flex items-center gap-1 bg-surface-card p-1 rounded-xl border border-border">
            {["ALL", "INFO", "WARN", "ERROR"].map((lvl) => (
              <button
                key={lvl}
                onClick={() => setFilterLevel(lvl)}
                className={`px-2.5 py-1 text-xs font-mono font-medium rounded-lg transition-all ${
                  filterLevel === lvl
                    ? "bg-brand-500 text-white"
                    : "text-gray-400 hover:text-white"
                }`}
              >
                {lvl}
              </button>
            ))}
          </div>

          <div className="relative flex-1 sm:w-64">
            <Search className="w-3.5 h-3.5 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filtrar mensagens de log..."
              className="w-full pl-8 pr-3 py-1 text-xs bg-surface-card border border-border rounded-xl text-white placeholder-gray-500 focus:outline-none focus:border-brand-400 font-mono"
            />
          </div>
        </div>

        <button
          onClick={onClearLogs}
          className="px-3 py-1 text-xs font-mono rounded-lg border border-border bg-surface-card hover:bg-surface-hover text-gray-400 hover:text-white flex items-center gap-1.5 transition-colors self-end sm:self-auto"
        >
          <Trash2 className="w-3.5 h-3.5" /> Limpar Console
        </button>
      </div>

      {/* Janela do Terminal */}
      <div className="rounded-xl border border-border bg-[#070b12] p-3 font-mono text-xs text-gray-300 min-h-[400px] max-h-[600px] overflow-y-auto space-y-1 select-text">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-gray-600">
            <Terminal className="w-8 h-8 mb-2 opacity-50" />
            <p>Nenhum registro de log para o filtro selecionado.</p>
          </div>
        ) : (
          filtered.map((log) => {
            const isError = log.level === "ERROR";
            const isWarn = log.level === "WARN";

            return (
              <div
                key={log.id}
                className="flex items-start gap-2.5 hover:bg-white/[0.03] py-0.5 px-1 rounded transition-colors"
              >
                <span className="text-gray-600 select-none text-[11px] shrink-0">
                  {new Date(log.timestamp).toLocaleTimeString("pt-BR")}
                </span>

                <span
                  className={`px-1 rounded text-[10px] font-bold shrink-0 ${
                    isError
                      ? "bg-loss/20 text-loss border border-loss/40"
                      : isWarn
                      ? "bg-amber-500/20 text-amber-300 border border-amber-500/40"
                      : "bg-brand-500/10 text-brand-300 border border-brand-500/30"
                  }`}
                >
                  {log.level}
                </span>

                <span className="text-gray-500 text-[11px] shrink-0">[{log.module}]</span>

                <span className="text-gray-200 break-all flex-1">{log.message}</span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

