import { useEffect, useState } from "react";

export function useHashRoute(): string[] {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash.replace(/^#\/?/, "").split("/").filter(Boolean);
}

export function navigate(path: string): void {
  window.location.hash = path.startsWith("#") ? path : `#${path}`;
}

export const fmt = {
  int: (v: number | null | undefined) => (v == null ? "–" : Math.round(v).toLocaleString()),
  sec: (ms: number | null | undefined) => (ms == null ? "–" : `${(ms / 1000).toFixed(1)}s`),
  usd: (v: number | null | undefined) => (v == null ? "–" : `$${v.toFixed(4)}`),
  pct: (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v * 100)}%`),
  num: (v: number | null | undefined, d = 2) => (v == null ? "–" : v.toFixed(d)),
  time: (epoch: number | null | undefined) => (epoch == null ? "–" : new Date(epoch * 1000).toLocaleString()),
};

export const SYSTEM_COLORS: Record<string, string> = {
  fa_mas: "var(--s1)",
  full_replan: "var(--s2)",
  naive_incremental: "var(--s3)",
  fixed_workflow: "var(--s4)",
  single_agent: "var(--s5)",
  fa_mas_verify: "var(--s6)",
};

export const SYSTEM_ORDER = ["single_agent", "fixed_workflow", "full_replan", "naive_incremental", "fa_mas", "fa_mas_verify"];

export const TAG_LABEL: Record<string, string> = {
  reused: "reused",
  retry: "retry",
  reassigned: "reassigned",
  recompute: "recompute",
  repair: "repair",
  new: "new",
  decomposed: "merge",
  salvaged: "salvaged",
  skipped: "skipped",
  restart: "restart",
  cache_hit: "reused",
};
