export function formatUSD(val: number | undefined | null, digits = 2): string {
  if (val === undefined || val === null || isNaN(val)) return "$0.00";
  const abs = Math.abs(val);
  const sign = val < 0 ? "-" : "";
  if (abs === 0) return "$0.00";
  if (abs < 0.01) {
    // Para valores minúsculos (ex: frações de centavo), formata sem notação científica
    const fixed = abs.toFixed(6).replace(/(\.\d*?[1-9])0+$/, "$1");
    return `${sign}$${fixed}`;
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(val);
}

export function formatCryptoPrice(val: number | undefined | null): string {
  if (val === undefined || val === null || isNaN(val) || val === 0) return "$0.00";
  const abs = Math.abs(val);
  const sign = val < 0 ? "-" : "";
  if (abs >= 1000) {
    return `${sign}$${abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
  if (abs >= 1) {
    return `${sign}$${abs.toFixed(4)}`;
  }
  if (abs >= 0.01) {
    return `${sign}$${abs.toFixed(6)}`;
  }
  // Para micro-preços de tokens (ex: $0.00000278), garante precisão total sem notação científica (e-6)
  const str = abs.toFixed(10);
  const cleaned = str.replace(/0+$/, "");
  const parts = cleaned.split(".");
  if (parts.length === 2 && parts[1].length < 4) {
    return `${sign}$${parts[0]}.${parts[1].padEnd(4, "0")}`;
  }
  return `${sign}$${cleaned}`;
}


export function formatPct(val: number | undefined | null, digits = 2): string {
  if (val === undefined || val === null || isNaN(val)) return "0.00%";
  const sign = val > 0 ? "+" : "";
  return `${sign}${val.toFixed(digits)}%`;
}

export function formatNumber(val: number | undefined | null, digits = 2): string {
  if (val === undefined || val === null || isNaN(val)) return "0";
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits,
  }).format(val);
}

export function shortenAddress(address: string | undefined | null, chars = 4): string {
  if (!address) return "—";
  if (address.length <= chars * 2 + 2) return address;
  return `${address.slice(0, chars)}...${address.slice(-chars)}`;
}

export function formatTimeAgo(dateStr: string | undefined | null): string {
  if (!dateStr) return "—";
  try {
    const diff = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
    if (diff < 60) return `${diff}s atrás`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m atrás`;
    if (diff < 86400) return `${(diff / 3600).toFixed(1)}h atrás`;
    return `${Math.floor(diff / 86400)}d atrás`;
  } catch {
    return dateStr;
  }
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

