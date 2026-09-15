import type { Agent } from "./types";

export function agentNeedsConfiguration(agent: Agent): boolean {
  return Boolean(
    !["archived", "disabled"].includes(agent.lifecycle_status || agent.status) &&
    agent.allowed_actions?.includes("configure_runtime") &&
    (agent.configuration_drift ||
      ["not_deployed", "pending", "unknown", ""].includes(agent.runtime_status ?? "")),
  );
}
