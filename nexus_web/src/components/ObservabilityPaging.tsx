import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ApiContext } from "../lib/api";
import { api } from "../lib/api";

// Keep one page in the DOM/cache per navigation position, not an ever-growing feed.
export function useObservabilityPage<T>(ctx: ApiContext, agent: string, path: string, filters: Record<string, string> = {}, enabled = true, live = false) {
  const scope = JSON.stringify([ctx.tenantId, ctx.projectId, agent, path, filters]);
  const [navigation, setNavigation] = useState({ scope, cursors: [""], index: 0 });
  const current = navigation.scope === scope ? navigation : { scope, cursors: [""], index: 0 };
  const cursor = current.cursors[current.index];
  const query = useQuery({
    queryKey: ["agent-observability", ctx.token, scope, cursor],
    queryFn: () => api.agentObservabilityPage<T>(ctx, agent, path, { ...filters, cursor, limit: "50" }),
    enabled, gcTime: 60_000,
    refetchInterval: live && current.index === 0 ? 5000 : false,
    refetchIntervalInBackground: false,
  });
  return { ...query, items: query.data?.items ?? [],
    paging: {
      index: current.index, count: query.data?.items.length ?? 0,
      busy: query.isFetching, hasNext: !!query.data?.next_cursor,
      next: () => { if (query.data?.next_cursor) setNavigation({ scope, index: current.index + 1, cursors: [...current.cursors.slice(0, current.index + 1), query.data.next_cursor] }); },
      previous: () => setNavigation({ ...current, index: Math.max(0, current.index - 1) }),
    },
  };
}

export function ObservabilityPaging({ paging, label }: { paging: { index: number; count: number; busy: boolean; hasNext: boolean; next: () => void; previous: () => void }; label?: string }) {
  return <nav aria-label={label || "Observability pages"} className="flex min-w-0 flex-wrap items-center gap-3 py-3 text-sm">
    <button className="nex-button nex-button--secondary min-h-11" aria-label={label ? `${label} previous page` : "Previous page"} disabled={paging.busy || paging.index === 0} onClick={paging.previous}>Previous</button>
    <span>Page {paging.index + 1} · {paging.count} loaded</span>
    <button className="nex-button nex-button--secondary min-h-11" aria-label={label ? `${label} next page` : "Next page"} disabled={paging.busy || !paging.hasNext} onClick={paging.next}>Next</button>
  </nav>;
}
