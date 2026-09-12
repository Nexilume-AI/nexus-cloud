import type { QueryClient, QueryKey } from "@tanstack/react-query";

export type AgentRefreshTarget =
  | { surface: "control"; agentId: string }
  | { surface: "runtime" | "settings" };
export type AgentRefreshKeys = (target: AgentRefreshTarget) => readonly QueryKey[];

/** Preserve each operational surface's refresh scope and completion semantics. */
export async function refreshAgentQueries(
  client: QueryClient,
  target: AgentRefreshTarget,
  extraKeys: readonly QueryKey[] = [],
) {
  const keys: QueryKey[] = target.surface === "control"
    ? [["agent"], ["agents"], ["agent-runtime-status"]]
    : target.surface === "runtime" ? [["agents"], ["agent-runtime-status"]] : [["agents"]];
  keys.push(...extraKeys);
  if (target.surface === "control") keys.push(["agent-mobile-grant", target.agentId], ["agent-interactor"]);
  await Promise.all(keys.map(queryKey => client.invalidateQueries({ queryKey })));
  if (target.surface === "control") await client.refetchQueries({ queryKey: ["agent"], type: "active" });
}
