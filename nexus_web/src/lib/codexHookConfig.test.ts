import { describe, expect, it } from "vitest";
import { parse } from "smol-toml";
import type { AgentMcpExport, CodexHookDeclaration } from "./types";
import { serializeCodexHook, codexHookEvents } from "./codexHookConfig";

const config: AgentMcpExport = {
  agent_id: "test", server_name: "nexus-test", version: "1", transport: "streamable-http",
  scope: { kind: "project", tenant_id: "caller-org", project_id: "caller-project" },
  template: false, credential: { mode: "bearer_env", environment_variable: "NEXUS_API_KEY", placeholder: "${NEXUS_API_KEY}", one_time_inline_supported: true },
  mcpServers: { "nexus-test": { type: "streamable-http", name: "test", agent_id: "test", version: "1",
    url: "https://cloud.example/mcp", headers: { "X-Nexus-Tenant": "caller-org", "X-Nexus-Project": "caller-project" } } },
  tools: [{ name: "review_patch", description: "test", input_schema: { type: "object", required: ["patch"] },
    codex_hooks: [{ event: "PostToolUse", matcher: "^apply_patch$", input: { patch: "${tool_input.command}" }, timeout: 30 }] }],
};
function withHook(hook: Partial<CodexHookDeclaration>) {
  const c = structuredClone(config);
  Object.assign(c.tools![0].codex_hooks![0], hook);
  return c;
}
describe("SDK-declared Codex MCP hooks", () => {
  it("copies declarations verbatim, binding the trusted server and tool", () => {
    const original = structuredClone(config);
    const result = parse(serializeCodexHook(config)) as any;
    expect(result.mcp_servers["nexus-test"].bearer_token_env_var).toBe("NEXUS_API_KEY");
    expect(result.mcp_servers["nexus-test"].http_headers["X-Nexus-Tenant"]).toBe("caller-org");
    expect(result.hooks.PostToolUse[0]).toEqual({ matcher: "^apply_patch$", hooks: [{ type: "mcp_tool", server: "nexus-test", tool: "review_patch", input: { patch: "${tool_input.command}" }, timeout: 30, statusMessage: "Running Nexus Hook" }] });
    expect(config).toEqual(original);
  });
  it.each(codexHookEvents)("supports SDK declaration of %s", event => {
    const c = withHook({ event, matcher: undefined, timeout: undefined, input: { patch: "${hook_event_name}", flag: false } });
    const result = parse(serializeCodexHook(c)) as any;
    expect(result.hooks[event][0].hooks[0].input.flag).toBe(false);
    expect(result.hooks[event][0].hooks[0].timeout).toBe(event === "Interrupt" ? 1 : 30);
  });
  it.each([0, 601, 1.5, NaN])("rejects invalid timeout %s", timeout =>
    expect(() => serializeCodexHook(withHook({ timeout }))).toThrow("timeout"));
  it.each(["SessionEnd", "Fake"])("rejects unsupported event %s", event =>
    expect(() => serializeCodexHook(withHook({ event }))).toThrow());
  it("rejects unknown executable fields, required argument omissions and Interrupt timeouts", () => {
    expect(() => serializeCodexHook(withHook({ command: "evil" } as any))).toThrow();
    expect(() => serializeCodexHook(withHook({ input: {} }))).toThrow("required");
    expect(() => serializeCodexHook(withHook({ event: "Interrupt", matcher: undefined, timeout: 4 }))).toThrow();
    expect(() => serializeCodexHook(withHook({ event: "Stop" }))).toThrow("matcher");
  });
  it("does not guess hooks for undeclared or invalid tools", () => {
    expect(() => serializeCodexHook({ ...config, tools: [] })).toThrow("not published");
    expect(() => serializeCodexHook({ ...config, tools: [{ ...config.tools![0], codex_hooks_invalid: true }] })).toThrow("invalid");
    expect(() => serializeCodexHook({ ...config, server_name: "fake" })).toThrow("server name");
    expect(() => serializeCodexHook({ ...config, template: true })).toThrow("Sign in");
  });
  it("rejects duplicates rather than installing twice", () => {
    const c = structuredClone(config);
    c.tools![0].codex_hooks!.push(c.tools![0].codex_hooks![0]);
    expect(() => serializeCodexHook(c)).toThrow("Duplicate");
  });
});
