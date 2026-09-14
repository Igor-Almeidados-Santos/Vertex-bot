export function formatUSD(val: number | undefined | null, digits = 2): string {
  if (val === undefined || val === null || isNaN(val)) return "$0.00";
  if (Math.abs(val) < 0.0001 && val !== 0) {
    return `$${val.toExponential(2)}`;
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(val);
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

