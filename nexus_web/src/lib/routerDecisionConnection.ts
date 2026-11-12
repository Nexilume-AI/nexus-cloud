import { api, type ApiContext } from "./api";
import type { Router } from "./types";

export type DecisionConnectionDraft = { name: string; url: string; key: string };
export type RouterDecisionSettings = {
  provider_connection_id: string; model: string; timeout_ms: number;
  model_source: "catalog" | "explicit"; model_order?: string[];
  // Form-only state. Never include credentials in Router JSON or browser storage.
  new_connection?: DecisionConnectionDraft;
  needs_start?: boolean;
};

export function decisionConnectionError(value: RouterDecisionSettings): string | null {
  if (!value.model.trim() || value.model.length > 128 || /[\x00-\x1f\x7f]/.test(value.model)) return "Enter a decision model ID.";
  if (!value.new_connection) return value.provider_connection_id ? null : "Choose a System One Provider.";
  const { name, url, key } = value.new_connection;
  if (!name.trim() || name.length > 255) return "Enter a connection name.";
  try {
    const endpoint = new URL(url.trim());
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(endpoint.hostname);
    if (url.length > 1024 || endpoint.username || endpoint.password || endpoint.search || endpoint.hash ||
      (endpoint.protocol !== "https:" && !(endpoint.protocol === "http:" && local)) ||
      /\/(chat\/completions|responses)\/?$/.test(endpoint.pathname)) throw new Error();
  } catch { return "Enter a System One HTTPS base URL or /systemone endpoint."; }
  if (!key.trim() || key.length > 4096 || /[\x00-\x1f\x7f]/.test(key)) return "Enter a valid API key.";
  return null;
}

/** Explicit allowlist: temporary connection fields must never reach Router config. */
export function decisionSettingsPayload(value: RouterDecisionSettings) {
  return {
    provider_connection_id: value.provider_connection_id, model: value.model.trim(),
    timeout_ms: value.timeout_ms, model_source: value.model_source,
    ...(value.model_order ? { model_order: [...value.model_order] } : {}),
  };
}

export async function prepareDecisionConnection(
  context: ApiContext, value: RouterDecisionSettings, router: Pick<Router, "project_id">,
  remember: (value: RouterDecisionSettings) => void,
): Promise<ReturnType<typeof decisionSettingsPayload>> {
  const error = decisionConnectionError(value);
  if (error) throw new Error(error);
  let prepared = decisionSettingsPayload(value);
  if (value.new_connection) {
    const draft = value.new_connection;
    const provider = await api.createProviderConnection(context, {
      name: draft.name.trim(), engine: "direct_api", upstream_provider: "system_one",
      ownership: { scope: router.project_id ? "project" : "organization", project_id: router.project_id },
      url: draft.url.trim().replace(/\/systemone\/?$/, "").replace(/\/$/, ""), key: draft.key.trim(),
    });
    prepared = { ...prepared, provider_connection_id: provider.id, model_source: "explicit" };
    // Forget the key immediately. A failed Start/Router save retries this same
    // connection, rather than creating another Provider or sending the key again.
    remember({ ...prepared, needs_start: true });
  }
  if (value.new_connection || value.needs_start) {
    try { await api.providerConnectionAction(context, prepared.provider_connection_id, "start"); }
    catch { throw new Error("Connection saved, but it could not be started. Retry saving to start the same connection, or manage it in Providers."); }
    remember(prepared);
  }
  return prepared;
}
