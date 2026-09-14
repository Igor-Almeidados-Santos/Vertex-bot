"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import {
  getBotStatus,
  getSummary,
  getPositions,
  getWaitingTokens,
  getOrders,
  pauseBot,
  resumeBot,
  startBot,
} from "@/lib/api";
import {
  BotStatusData,
  OrderItem,
  PositionItem,
  SummaryData,
  SystemLogItem,
  WaitingTokenItem,
} from "@/types/bot";

export function useBotData() {
  const [status, setStatus] = useState<BotStatusData | null>(null);
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [positions, setPositions] = useState<PositionItem[]>([]);
  const [waitingTokens, setWaitingTokens] = useState<WaitingTokenItem[]>([]);
  const [recentOrders, setRecentOrders] = useState<OrderItem[]>([]);
  const [logs, setLogs] = useState<SystemLogItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isWsConnected, setIsWsConnected] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());

  const wsRef = useRef<WebSocket | null>(null);
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

  const refreshData = useCallback(async () => {
    try {
      const [st, sum, pos, wait, ord] = await Promise.all([
        getBotStatus().catch(() => null),
        getSummary().catch(() => null),
        getPositions().catch(() => []),
        getWaitingTokens().catch(() => []),
        getOrders(50).catch(() => []),
      ]);

      if (st) setStatus(st);
      if (sum) setSummary(sum);
      setPositions(pos);
      setWaitingTokens(wait);
      setRecentOrders(ord);
      setLastUpdated(new Date());
    } catch (err) {
      console.error("Erro ao atualizar dados do bot:", err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Polling fallback
  useEffect(() => {
    refreshData();
    pollIntervalRef.current = setInterval(refreshData, 2000);
    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    };
  }, [refreshData]);

  // WebSocket Connection
  useEffect(() => {
    let reconnectTimeout: NodeJS.Timeout;
    const protocol = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = typeof window !== "undefined" ? window.location.host : "localhost:8080";
    // In dev mode (port 3000), point to 8080
    const wsHost = host.includes(":3000") ? "localhost:8080" : host;
    const wsUrl = `${protocol}//${wsHost}/ws`;

    function connectWs() {
      try {
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onopen = () => {
          setIsWsConnected(true);
        };

        ws.onclose = () => {
          setIsWsConnected(false);
          reconnectTimeout = setTimeout(connectWs, 3000);
        };

        ws.onerror = () => {
          setIsWsConnected(false);
          ws.close();
        };

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            if (data.type === "log" || data.message) {
              setLogs((prev) => [
                {
                  id: `${Date.now()}-${Math.random()}`,
                  timestamp: data.timestamp || new Date().toISOString(),
                  level: data.level || "INFO",
                  module: data.module || "core",
                  message: data.message || JSON.stringify(data),
                },
                ...prev.slice(0, 250),
              ]);
            }
            if (data.type === "trade" || data.type === "status_change") {
              refreshData();
            }
          } catch {
            // Raw text log fallback
            if (typeof event.data === "string" && event.data.trim()) {
              setLogs((prev) => [
                {
                  id: `${Date.now()}-${Math.random()}`,
                  timestamp: new Date().toISOString(),
                  level: "INFO",
                  module: "runtime",
                  message: event.data,
                },
                ...prev.slice(0, 250),
              ]);
            }
          }
        };
      } catch (e) {
        console.warn("Falha ao abrir WebSocket:", e);
        reconnectTimeout = setTimeout(connectWs, 5000);
      }
    }

    connectWs();

    return () => {
      clearTimeout(reconnectTimeout);
      if (wsRef.current) wsRef.current.close();
    };
  }, [refreshData]);

  const handleTogglePause = async () => {
    if (!status) return;
    try {
      if (status.is_paused) {
        await resumeBot();
      } else {
        await pauseBot();
      }
      await refreshData();
    } catch (err) {
      console.error("Erro ao alternar pausa do bot:", err);
      throw err;
    }
  };

  const handleStartBot = async () => {
    try {
      await startBot();
      await refreshData();
    } catch (err) {
      console.error("Erro ao iniciar bot:", err);
      throw err;
    }
  };

  const clearLogs = () => setLogs([]);

  return {
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
  };
}

