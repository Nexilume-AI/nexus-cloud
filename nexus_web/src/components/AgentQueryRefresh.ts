import { useCallback } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { useApplicationDistribution } from "../app/distribution";
import { refreshAgentQueries, type AgentRefreshTarget } from "../lib/agentQueryRefresh";

export function useAgentQueryRefresh(surface: "control"): (client: QueryClient, agentId: string) => Promise<void>;
export function useAgentQueryRefresh(surface: "runtime" | "settings"): (client: QueryClient) => Promise<void>;
export function useAgentQueryRefresh(surface: AgentRefreshTarget["surface"]) {
  const extraKeys = useApplicationDistribution().resourcePublishing?.agentRefreshKeys;
  return useCallback(async (client: QueryClient, agentId?: string) => {
    if (surface === "control" && !agentId) throw new Error("Agent refresh requires an Agent ID.");
    const target: AgentRefreshTarget = surface === "control" ? { surface, agentId: agentId! } : { surface };
    await refreshAgentQueries(client, target, extraKeys?.(target));
  }, [surface, extraKeys]);
}
