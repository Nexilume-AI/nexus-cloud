import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { decisionConnectionError, decisionSettingsPayload, prepareDecisionConnection, type RouterDecisionSettings } from "./routerDecisionConnection";
import type { ProviderConnection } from "./types";

const draft = (): RouterDecisionSettings => ({ provider_connection_id: "old", model: "decision-model-preview", timeout_ms: 3000, model_source: "catalog", model_order: ["strong", "fast"], new_connection: { name: "My System One", url: "https://decision.example/v1/systemone/", key: "test-secret" } });
const context = { tenantId: "tenant" };
const provider = { id: "new", name: "My System One" } as ProviderConnection;
afterEach(() => vi.restoreAllMocks());

describe("inline System One connection", () => {
  it("saves encrypted Provider input in the Router's scope, starts it, and only returns a reference", async () => {
    const create = vi.spyOn(api, "createProviderConnection").mockResolvedValue(provider);
    const start = vi.spyOn(api, "providerConnectionAction").mockResolvedValue(provider);
    const remembered: RouterDecisionSettings[] = [];
    const config = await prepareDecisionConnection(context, draft(), { project_id: "router-project" }, value => remembered.push(value));
    expect(create).toHaveBeenCalledWith(context, { name: "My System One", url: "https://decision.example/v1", key: "test-secret", engine: "direct_api", upstream_provider: "system_one", ownership: { scope: "project", project_id: "router-project" } });
    expect(start).toHaveBeenCalledWith(context, "new", "start");
    expect(remembered[0]).toEqual({ ...config, needs_start: true });
    expect(remembered[1]).toEqual(config);
    expect(config).toEqual({ provider_connection_id: "new", model: "decision-model-preview", timeout_ms: 3000, model_source: "explicit", model_order: ["strong", "fast"] });
    expect(JSON.stringify([config, remembered])).not.toMatch(/test-secret|https:\/\//);
  });
  it("retries Start against the saved Provider without re-sending the key or creating duplicates", async () => {
    const create = vi.spyOn(api, "createProviderConnection").mockResolvedValue(provider);
    const start = vi.spyOn(api, "providerConnectionAction").mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(provider);
    let state = draft();
    await expect(prepareDecisionConnection(context, state, { project_id: null }, value => { state = value; })).rejects.toThrow("Connection saved");
    expect(state.new_connection).toBeUndefined();
    expect(state.needs_start).toBe(true);
    expect(create.mock.calls[0][1].ownership).toEqual({ scope: "organization", project_id: null });
    await prepareDecisionConnection(context, state, { project_id: null }, value => { state = value; });
    expect(create).toHaveBeenCalledTimes(1);
    expect(start).toHaveBeenCalledTimes(2);
    expect(state.needs_start).toBeUndefined();
  });
  it("does not mutate a selected existing Provider or restart it after a Router-save failure", async () => {
    const create = vi.spyOn(api, "createProviderConnection");
    const start = vi.spyOn(api, "providerConnectionAction");
    const config = decisionSettingsPayload(draft());
    expect(await prepareDecisionConnection(context, config, { project_id: null }, vi.fn())).toEqual(config);
    expect(create).not.toHaveBeenCalled(); expect(start).not.toHaveBeenCalled();
  });
  it("leaves the Router untouched and does not attempt Start if creation is denied", async () => {
    vi.spyOn(api, "createProviderConnection").mockRejectedValue(new Error("permission denied"));
    const start = vi.spyOn(api, "providerConnectionAction");
    const remember = vi.fn();
    await expect(prepareDecisionConnection(context, draft(), { project_id: null }, remember)).rejects.toThrow("permission denied");
    expect(remember).not.toHaveBeenCalled(); expect(start).not.toHaveBeenCalled();
  });
  it.each(["http://decision.example/v1", "https://user:pass@decision.example/v1", "https://decision.example/v1?key=secret", "https://decision.example/v1#secret", "https://decision.example/v1/chat/completions", "https://decision.example/v1/responses", "not a URL"])("rejects invalid or credential-bearing endpoint %s", url => {
    const value = draft(); value.new_connection!.url = url;
    expect(decisionConnectionError(value)).toContain("HTTPS base URL");
  });
  it("validates required fields and never serializes transient connection data into Router JSON", () => {
    const value = draft();
    expect(decisionConnectionError(value)).toBeNull();
    expect(decisionSettingsPayload({ ...value, needs_start: true })).not.toHaveProperty("new_connection");
    expect(decisionSettingsPayload({ ...value, needs_start: true })).not.toHaveProperty("needs_start");
    value.new_connection!.key = "";
    expect(decisionConnectionError(value)).toContain("API key");
    expect(decisionConnectionError({ ...value, model: "" })).toContain("model ID");
  });
});
