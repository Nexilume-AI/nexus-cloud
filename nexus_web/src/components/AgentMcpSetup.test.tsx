import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentMcpExport } from "../lib/types";
import { AgentMcpSetup } from "./AgentMcpSetup";
import { CodexHookSetup } from "./CodexHookSetup";

const config: AgentMcpExport = {
  agent_id: "test", server_name: "test", version: "1", transport: "streamable-http",
  scope: { kind: "project", tenant_id: "org", project_id: "project" }, template: false,
  credential: { mode: "bearer_env", environment_variable: "NEXUS_API_KEY", placeholder: "${NEXUS_API_KEY}", one_time_inline_supported: true },
  mcpServers: { test: { type: "streamable-http", name: "test", agent_id: "test", version: "1",
    url: "https://cloud.example/mcp", headers: {} } },
};
const tool = { name: "tool", description: "Ordinary MCP tool", input_schema: {} };
beforeEach(() => vi.stubGlobal("localStorage", { getItem: () => "codex" }));
afterEach(() => vi.unstubAllGlobals());

describe("Hook entry follows SDK declarations", () => {
  it.each([undefined, [], [tool], [{ ...tool, codex_hooks: [] }]])(
    "keeps undeclared Agents on ordinary MCP without warning or retry (%j)", tools => {
      const value = { ...config, tools };
      const html = renderToStaticMarkup(<AgentMcpSetup config={value} onLoad={() => {}} />);
      expect(html).toContain("Generated MCP configuration");
      expect(html).not.toContain("Codex Hook");
      expect(html).not.toContain("SDK-declared Hooks");
      expect(html).not.toContain("Connection usage");
      expect(html).not.toContain("Retry configuration");
      expect(html).not.toContain("Ask its author");
      expect(renderToStaticMarkup(<CodexHookSetup config={value} onReload={() => {}} />)).toBe("");
    });
  it("shows the entry only for published declarations", () => {
    const value = { ...config, tools: [{ ...tool, codex_hooks: [{ event: "Stop", input: {} }] }] };
    const html = renderToStaticMarkup(<AgentMcpSetup config={value} onLoad={() => {}} />);
    expect(html).toContain("Codex Hook");
    expect(html).toContain("Connection usage");
    expect(html).toContain('aria-label="SDK-declared Hooks"');
    expect(html).toContain("This Agent declares Hooks in its SDK configuration.");
    const template = renderToStaticMarkup(<AgentMcpSetup config={{ ...value, template: true }} onLoad={() => {}} />);
    expect(template).not.toContain("Codex Hook");
    expect(template).not.toContain("SDK-declared Hooks");
  });
  it("shows the capability badge regardless of the selected MCP client", () => {
    vi.stubGlobal("localStorage", { getItem: () => "claude" });
    const value = { ...config, tools: [{ ...tool, codex_hooks: [{ event: "Stop", input: {} }] }] };
    const html = renderToStaticMarkup(<AgentMcpSetup config={value} onLoad={() => {}} />);
    expect(html).toContain('aria-label="SDK-declared Hooks"');
    expect(html).not.toContain("Connection usage");
  });
  it("keeps errors for invalid declarations distinct from no declaration", () => {
    const value = { ...config, tools: [{ ...tool, codex_hooks: [], codex_hooks_invalid: true }] };
    expect(renderToStaticMarkup(<AgentMcpSetup config={value} onLoad={() => {}} />)).toContain("Codex Hook");
    const html = renderToStaticMarkup(<CodexHookSetup config={value} onReload={() => {}} />);
    expect(html).toContain("invalid Hook declaration");
    expect(html).toContain("Retry configuration");
  });
});
