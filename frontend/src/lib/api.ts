import {
  BotStatusData,
  CatalogTokenItem,
  OrderItem,
  PositionItem,
  SummaryData,
  WaitingTokenItem,
} from "@/types/bot";

const API_BASE = "";

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers || {}),
    },
  });

  if (!res.ok) {
    let errorMsg = `HTTP Error ${res.status}`;
    try {
      const errData = await res.json();
      if (errData.message) errorMsg = errData.message;
    } catch {
      // fallback to status text
    }
    throw new Error(errorMsg);
  }

  const data = await res.json();
  if (data.status === "error") {
    throw new Error(data.message || "Erro retornado pela API");
  }

  return data.data !== undefined ? data.data : data;
}

export async function getBotStatus(): Promise<BotStatusData> {
  return fetchJson<BotStatusData>("/api/bot/status");
}

export async function getSummary(): Promise<SummaryData> {
  return fetchJson<SummaryData>("/api/summary");
}

export async function getPositions(status?: "OPEN" | "CLOSED"): Promise<PositionItem[]> {
  const q = status ? `?status=${status}` : "";
  return fetchJson<PositionItem[]>(`/api/positions${q}`);
}

export async function getWaitingTokens(): Promise<WaitingTokenItem[]> {
  return fetchJson<WaitingTokenItem[]>("/api/waiting_tokens");
}

export async function getOrders(limit = 100): Promise<OrderItem[]> {
  return fetchJson<OrderItem[]>(`/api/orders?limit=${limit}`);
}

export async function getTokens(status?: string, search?: string, allTime = true): Promise<CatalogTokenItem[]> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (search) params.set("search", search);
  if (allTime) params.set("all_time", "true");
  const q = params.toString() ? `?${params.toString()}` : "";
  return fetchJson<CatalogTokenItem[]>(`/api/tokens${q}`);
}

export async function pauseBot(): Promise<{ is_paused: boolean }> {
  return fetchJson<{ is_paused: boolean }>("/api/bot/pause", { method: "POST" });
}

export async function resumeBot(): Promise<{ is_paused: boolean }> {
  return fetchJson<{ is_paused: boolean }>("/api/bot/resume", { method: "POST" });
}

export async function startBot(): Promise<{ is_running: boolean }> {
  return fetchJson<{ is_running: boolean }>("/api/bot/start", { method: "POST" });
}

export async function restartSimulation(initialWalletUsd: number): Promise<void> {
  return fetchJson<void>("/api/bot/restart", {
    method: "POST",
    body: JSON.stringify({
      initial_wallet_usd: initialWalletUsd,
      wallet_balance_usd: initialWalletUsd,
      paper_initial_wallet_usd: initialWalletUsd,
    }),
  });
}

export async function updateConfig(payload: Record<string, unknown>): Promise<void> {
  return fetchJson<void>("/api/bot/config", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function depositCash(amountUsd: number): Promise<{ wallet_balance_usd: number }> {
  return fetchJson<{ wallet_balance_usd: number }>("/api/wallet/deposit", {
    method: "POST",
    body: JSON.stringify({ amount_usd: amountUsd }),
  });
}

