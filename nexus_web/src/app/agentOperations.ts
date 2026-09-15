import { t } from "../localization";
import type { Agent } from "../lib/types";
import type { AgentPublicationPolicy } from "./resourcePublishing";

export type AgentSection = "overview" | "runtime" | "access" | "observability" | "publish" | "settings";

export function hasRuntimeTarget(agent: Agent) {
  return Boolean(
    agent.current_image_id || isOpenWrtRuntime(agent.runtime_kind),
  );
}

export function isAgentRunning(agent: Agent) {
  return (
    agent.runtime_status === "active" ||
    agent.active_runtime_deployment_count > 0
  );
}

export function isOpenWrtRuntime(runtimeKind?: string | null) {
  return runtimeKind === "openwrt_ipv6" || runtimeKind === "openwrt_relay";
}

export function getAgentNextAction(agent: Agent, publication?: Pick<AgentPublicationPolicy, "recommend">): {
  label: string;
  description: string;
  section: AgentSection;
} {
  if (!hasRuntimeTarget(agent))
    return {
      label: t("Configure runtime"),
      description: t("Choose a Nexus Container or bind an OpenWrt Agent."),
      section: "runtime",
    };
  if (agent.configuration_drift)
    return {
      label: t("Deploy update"),
      description: t("The selected runtime image differs from production."),
      section: "runtime",
    };
  if (!isAgentRunning(agent))
    return {
      label: t("Deploy Agent"),
      description: t("Start the production Runtime before connecting callers."),
      section: "runtime",
    };
  if (
    ["degraded", "unhealthy", "failed", "unknown"].includes(
      (agent.runtime_health_status || "").toLowerCase(),
    )
  )
    return {
      label: t("Check runtime health"),
      description: t("Verify the active Runtime before accepting new calls."),
      section: "runtime",
    };
  const recommendation = publication?.recommend(agent);
  if (recommendation) return recommendation;
  return {
    label: t("View Runs"),
    description: t("Review recent Trace, Memory, and Output lineage."),
    section: "observability",
  };
}

export function getAgentSections(publication?: Pick<AgentPublicationPolicy, "sectionLabel">) {
  const sections: Array<{ value: AgentSection; label: string }> = [
    { value: "overview", label: t("Overview") },
    { value: "runtime", label: t("Runtime") },
    { value: "access", label: t("Access") },
    { value: "observability", label: t("Observability") },
    { value: "publish", label: publication?.sectionLabel ?? t("Test") },
    { value: "settings", label: t("Settings") },
  ];
  return sections;
}
