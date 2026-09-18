interface ChainBadgeProps {
  chain?: string;
  className?: string;
}

export function ChainBadge({ chain, className = "" }: ChainBadgeProps) {
  const c = (chain || "solana").toLowerCase().trim();

  if (c === "base") {
    return (
      <span
        className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-blue-500/15 text-blue-400 border border-blue-500/30 ${className}`}
        title="Rede Base L2 (EVM)"
      >
        Base
      </span>
    );
  }
  if (c === "arbitrum" || c === "arb") {
    return (
      <span
        className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-cyan-500/15 text-cyan-400 border border-cyan-500/30 ${className}`}
        title="Rede Arbitrum One (EVM)"
      >
        Arbitrum
      </span>
    );
  }
  if (c === "bsc" || c === "binance") {
    return (
      <span
        className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-yellow-500/15 text-yellow-400 border border-yellow-500/30 ${className}`}
        title="BNB Smart Chain (EVM)"
      >
        BSC
      </span>
    );
  }
  if (c === "polygon" || c === "matic" || c === "polygon_pos") {
    return (
      <span
        className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-violet-500/15 text-violet-400 border border-violet-500/30 ${className}`}
        title="Polygon PoS (EVM)"
      >
        Polygon
      </span>
    );
  }
  if (c === "ethereum" || c === "eth") {
    return (
      <span
        className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-indigo-500/15 text-indigo-400 border border-indigo-500/30 ${className}`}
        title="Ethereum Mainnet (EVM)"
      >
        Ethereum
      </span>
    );
  }
  if (c === "avalanche" || c === "avax") {
    return (
      <span
        className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-red-500/15 text-red-400 border border-red-500/30 ${className}`}
        title="Avalanche C-Chain (EVM)"
      >
        Avalanche
      </span>
    );
  }
  if (c === "optimism" || c === "op") {
    return (
      <span
        className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-rose-500/15 text-rose-400 border border-rose-500/30 ${className}`}
        title="Optimism Mainnet (EVM)"
      >
        Optimism
      </span>
    );
  }
  if (c === "blast") {
    return (
      <span
        className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 ${className}`}
        title="Blast L2 (EVM)"
      >
        Blast
      </span>
    );
  }
  if (c === "solana" || c === "sol") {
    return (
      <span
        className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-purple-500/15 text-purple-400 border border-purple-500/30 ${className}`}
        title="Rede Solana (SVM)"
      >
        Solana
      </span>
    );
  }
  return (
    <span
      className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-purple-500/15 text-purple-400 border border-purple-500/30 ${className}`}
      title="Rede Solana (SVM)"
      className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-slate-500/15 text-slate-400 border border-slate-500/30 ${className}`}
      title={`Rede ${chain}`}
    >
      Solana
      {chain}
    </span>
  );
}

