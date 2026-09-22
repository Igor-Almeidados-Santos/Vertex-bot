import {
  BotStatusData,
  CatalogTokenItem,
  OrderItem,
  PositionItem,
  SummaryData,
  WaitingTokenItem,
  WalletInfo,
  PriorityToken,
  PurgePriorityResult,
} from "@/types/bot";

const API_BASE = "";

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${url}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options?.headers || {}),
      },
    });
  } catch {
    throw new Error(
      "Falha de conexão com o servidor do bot. Verifique se o Vertex-bot está em execução no terminal (./venv/bin/python main.py --provider hybrid --dashboard)."
    );
  }

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


export async function getBotStatus(mode: string = "paper"): Promise<BotStatusData> {
  return fetchJson<BotStatusData>(`/api/bot/status?mode=${encodeURIComponent(mode)}`);
}

export async function getSummary(mode: string = "paper"): Promise<SummaryData> {
  return fetchJson<SummaryData>(`/api/summary?mode=${encodeURIComponent(mode)}`);
}

export async function getPositions(status?: "OPEN" | "CLOSED", mode: string = "paper"): Promise<PositionItem[]> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (mode) params.set("mode", mode);
  const q = params.toString() ? `?${params.toString()}` : "";
  return fetchJson<PositionItem[]>(`/api/positions${q}`);
}

export async function getWaitingTokens(mode: string = "paper"): Promise<WaitingTokenItem[]> {
  return fetchJson<WaitingTokenItem[]>(`/api/waiting_tokens?mode=${encodeURIComponent(mode)}`);
}

export async function getOrders(limit = 100, mode: string = "paper"): Promise<OrderItem[]> {
  return fetchJson<OrderItem[]>(`/api/orders?limit=${limit}&mode=${encodeURIComponent(mode)}`);
}

export async function getTokens(status?: string, search?: string, allTime = true, mode: string = "paper"): Promise<CatalogTokenItem[]> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (search) params.set("search", search);
  if (allTime) params.set("all_time", "true");
  if (mode) params.set("mode", mode);
  const q = params.toString() ? `?${params.toString()}` : "";
  return fetchJson<CatalogTokenItem[]>(`/api/tokens${q}`);
}

export async function pauseBot(mode: string = "paper"): Promise<{ is_paused: boolean }> {
  return fetchJson<{ is_paused: boolean }>(`/api/bot/pause?mode=${encodeURIComponent(mode)}`, { method: "POST" });
}

export async function resumeBot(mode: string = "paper"): Promise<{ is_paused: boolean }> {
  return fetchJson<{ is_paused: boolean }>(`/api/bot/resume?mode=${encodeURIComponent(mode)}`, { method: "POST" });
}

export async function startBot(mode: string = "paper"): Promise<{ is_running: boolean }> {
  return fetchJson<{ is_running: boolean }>(`/api/bot/start?mode=${encodeURIComponent(mode)}`, { method: "POST" });
}

export async function restartSimulation(initialWalletUsd: number): Promise<void> {
  return fetchJson<void>("/api/bot/restart?mode=paper", {
    method: "POST",
    body: JSON.stringify({
      initial_wallet_usd: initialWalletUsd,
      wallet_balance_usd: initialWalletUsd,
      paper_initial_wallet_usd: initialWalletUsd,
    }),
  });
}

export async function updateConfig(payload: Record<string, unknown>, mode: string = "paper"): Promise<void> {
  return fetchJson<void>(`/api/bot/config?mode=${encodeURIComponent(mode)}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function depositCash(amountUsd: number): Promise<{ wallet_balance_usd: number }> {
  return fetchJson<{ wallet_balance_usd: number }>("/api/wallet/deposit?mode=paper", {
    method: "POST",
    body: JSON.stringify({ amount_usd: amountUsd }),
  });
}

export async function stopBot(mode: string = "paper"): Promise<{ is_running: boolean; mode: string }> {
  return fetchJson<{ is_running: boolean; mode: string }>(`/api/bot/stop?mode=${encodeURIComponent(mode)}`, { method: "POST" });
}

export async function getWallets(): Promise<WalletInfo[]> {
  return fetchJson<WalletInfo[]>("/api/wallets");
}

export async function connectWallet(chain: string, privateKey: string): Promise<{ chain: string; address: string; balance_usd: number }> {
  return fetchJson<{ chain: string; address: string; balance_usd: number }>("/api/wallets/connect", {
    method: "POST",
    body: JSON.stringify({ chain, private_key: privateKey }),
  });
}

export async function disconnectWallet(chain: string): Promise<{ message: string }> {
  return fetchJson<{ message: string }>("/api/wallets/disconnect", {
    method: "POST",
    body: JSON.stringify({ chain }),
  });
}

export async function closePosition(positionId: number, mode: string = "paper"): Promise<{ status: string; message: string; position_id: number }> {
  return fetchJson<{ status: string; message: string; position_id: number }>(`/api/positions/${positionId}/close?mode=${encodeURIComponent(mode)}`, {
    method: "POST",
  });
}

export async function buyMorePosition(positionId: number, mode: string = "paper"): Promise<{ status: string; message: string; position_id: number }> {
  return fetchJson<{ status: string; message: string; position_id: number }>(`/api/positions/${positionId}/buy_more?mode=${encodeURIComponent(mode)}`, {
    method: "POST",
  });
}

export async function transferSol(params: {
  recipient_address: string;
  amount_sol?: number;
  send_all?: boolean;
  private_key?: string;
}): Promise<{ tx_hash: string; solscan_url: string; recipient: string }> {
  return fetchJson<{ tx_hash: string; solscan_url: string; recipient: string }>("/api/wallets/transfer", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export async function getPriorityTokens(mode: string = "live"): Promise<PriorityToken[]> {
  return fetchJson<PriorityToken[]>(`/api/tokens/priority?mode=${encodeURIComponent(mode)}`);
}

export async function purgePriorityTokens(mode: string = "live"): Promise<PurgePriorityResult> {
  return fetchJson<PurgeResultPayload>("/api/tokens/priority/purge", {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
}

type PurgeResultPayload = PurgePriorityResult;





