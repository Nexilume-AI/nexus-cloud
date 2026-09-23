import { describe, expect, it } from "vitest";

import type { ProviderConnection, ProviderRuntimeAccount, ProviderRuntimeModelOffer } from "../lib/types";
import { getProviderRecommendedAction, getRuntimeRecommendedAction } from "./ProvidersPage";

const baseOffer: ProviderRuntimeModelOffer = {
  id: "offer-1",
  canonical_model_id: "model-1",
  canonical_model_key: "model-1",
  upstream_model_id: "upstream-1",
  status: "confirmed",
  daily_limit: 100,
  monthly_limit: 1000,
  capacity: 4,
  capabilities: ["tools"],
  health_status: "healthy",
  health_reason: "",
  last_discovered_at: null,
  last_health_check_at: null,
  confirmed_at: null,
  source_ids: [],
};

function runtime(overrides: Partial<ProviderRuntimeAccount> = {}): ProviderRuntimeAccount {
  return {
    id: "runtime-1",
    tenant_id: "tenant-1",
    project_id: "project-1",
    provider_account_id: "account-1",
    source_provider_account_id: "account-1",
    name: "Provider Runtime",
    runtime_type: "direct_api",
    share_mode: "private",
    status: "active",
    public_login_path: "",
    model_offers: [baseOffer],
    last_error: "",
    last_health_check_at: null,
    quota_snapshot: {},
    created_at: "2026-08-20T00:00:00Z",
    updated_at: "2026-08-20T00:00:00Z",
    ...overrides,
  };
}

describe("getRuntimeRecommendedAction", () => {
  it("requests model verification after saving Direct API credentials without starting a stopped provider", () => {
    const provider: ProviderConnection = {
      id: "provider-1", name: "Provider", account_identity: "default", upstream_provider: "openai-compatible",
      url: "https://provider.example.test/v1", engine: "direct_api", auth_mode: "api_key",
      status: "active", status_message: "", login_status: "unknown", last_error: "",
      models: [{ ...baseOffer, health_status: "unknown" }],
      quota: { status: "unknown", remaining_tokens: null, remaining_requests: null, reset_at: null },
      available_actions: [], last_checked_at: null, health_history: [], created_at: "", updated_at: "",
    };
    expect(getProviderRecommendedAction(provider)).toBe("refresh");
    expect(getProviderRecommendedAction({ ...provider, status: "stopped" })).toBe("start");
  });
  it("uses the fixed operational priority", () => {
    expect(getRuntimeRecommendedAction(runtime({ status: "starting" }))).toBe("wait");
    expect(getRuntimeRecommendedAction(runtime({ status: "stopping" }))).toBe("wait");
    expect(getRuntimeRecommendedAction(runtime({ runtime_type: "codex_proxy", status: "login_required" }))).toBe("login");
    expect(getRuntimeRecommendedAction(runtime({ status: "stopped" }))).toBe("start");
    expect(getRuntimeRecommendedAction(runtime({ status: "degraded" }))).toBe("health");
    expect(getRuntimeRecommendedAction(runtime({ model_offers: [] }))).toBe("refresh");
    expect(getRuntimeRecommendedAction(runtime({ model_offers: [{ ...baseOffer, status: "detected" }] }))).toBe("use_source");
    expect(getRuntimeRecommendedAction(runtime())).toBe("use_source");
    expect(getRuntimeRecommendedAction(runtime({ model_offers: [{ ...baseOffer, source_ids: ["source-1"] }] }))).toBe("operational");
  });
  it("only offers publication through an explicit policy after operational recovery", () => {
    const policy = { canPublish: () => true, needsPublication: () => true };
    const configured = runtime({ model_offers: [{ ...baseOffer, source_ids: ["source-1"] }] });
    expect(getRuntimeRecommendedAction(configured, policy)).toBe("publish");
    expect(getRuntimeRecommendedAction({ ...configured, status: "stopped" }, policy)).toBe("start");
    expect(getRuntimeRecommendedAction({ ...configured, status: "failed" }, policy)).toBe("health");
    expect(getRuntimeRecommendedAction(runtime(), policy)).toBe("use_source");
    expect(getRuntimeRecommendedAction(configured)).toBe("operational");
  });
});
