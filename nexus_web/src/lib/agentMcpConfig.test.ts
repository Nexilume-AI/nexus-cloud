import { describe, expect, it } from "vitest";
import { parse } from "smol-toml";
import { mcpClients, serializeClientConfig, type McpClient } from "./agentMcpConfig";
import type { AgentMcpExport } from "./types";

function fixture(): AgentMcpExport {
  return {
    agent_id: "agent-1", server_name: "nexus-agent", version: "1", transport: "streamable-http",
    scope: { kind: "project", tenant_id: "consumer-org", project_id: "consumer-project" },
    credential: { mode: "bearer_env", environment_variable: "NEXUS_API_KEY",
      placeholder: "${NEXUS_API_KEY}", one_time_inline_supported: true },
    template: false,
    mcpServers: { "nexus-agent": { type: "streamable-http", agent_id: "agent-1",
      name: "Agent", version: "1", tenant_id: "consumer-org", url: "https://cloud.example/api/v1/agents/agent-1/mcp/",
      headers: { authorization: "Bearer ${NEXUS_API_KEY}", "X-Nexus-Tenant": "consumer-org", "X-Nexus-Project": "consumer-project" } } },
  };
}

describe("native MCP client configuration", () => {
  it("emits Claude Code HTTP config with its native variable syntax", () => {
    const result = JSON.parse(serializeClientConfig(fixture()));
    expect(Object.keys(result)).toEqual(["mcpServers"]);
    expect(result.mcpServers["nexus-agent"]).toEqual({ type: "http", url: fixture().mcpServers["nexus-agent"].url,
      headers: { Authorization: "Bearer ${NEXUS_API_KEY}", "X-Nexus-Tenant": "consumer-org", "X-Nexus-Project": "consumer-project" } });
  });
  it("emits Cursor's env interpolation, without Nexus metadata", () => {
    const server = JSON.parse(serializeClientConfig(fixture(), undefined, "cursor")).mcpServers["nexus-agent"];
    expect(Object.keys(server).sort()).toEqual(["headers", "url"]);
    expect(server.headers.Authorization).toBe("Bearer ${env:NEXUS_API_KEY}");
  });
  it("emits VS Code servers and a masked credential input", () => {
    const result = JSON.parse(serializeClientConfig(fixture(), undefined, "vscode"));
    expect(result.mcpServers).toBeUndefined();
    expect(result.servers["nexus-agent"].type).toBe("http");
    expect(result.servers["nexus-agent"].headers.Authorization).toBe("Bearer ${input:nexus-key-agent-1}");
    expect(result.inputs).toEqual([{ type: "promptString", id: "nexus-key-agent-1", description: "Nexus Agent API key", password: true }]);
  });
  it("uses valid Codex TOML and a bearer environment variable", () => {
    const result = parse(serializeClientConfig(fixture(), undefined, "codex")) as any;
    expect(result.mcp_servers["nexus-agent"]).toEqual({ url: fixture().mcpServers["nexus-agent"].url,
      bearer_token_env_var: "NEXUS_API_KEY", http_headers: { "X-Nexus-Tenant": "consumer-org", "X-Nexus-Project": "consumer-project" } });
  });
  it.each(Object.keys(mcpClients) as McpClient[])("reuses the provided key and consumer headers for %s without mutation", (client) => {
    const config = fixture();
    const original = structuredClone(config);
    const key = 'test-key-"quoted"\\value';
    const output = serializeClientConfig(config, key, client);
    const result = (client === "codex" ? parse(output) : JSON.parse(output)) as any;
    const server = (result.mcp_servers || result.mcpServers || result.servers)["nexus-agent"];
    const headers = server.http_headers || server.headers;
    expect(headers.Authorization).toBe(`Bearer ${key}`);
    expect(headers["X-Nexus-Tenant"]).toBe("consumer-org");
    expect(headers["X-Nexus-Project"]).toBe("consumer-project");
    expect(headers.authorization).toBeUndefined();
    expect(server.bearer_token_env_var).toBeUndefined();
    expect(result.inputs).toBeUndefined();
    expect(output).not.toContain("streamable-http");
    expect(config).toEqual(original);
  });
  it("round-trips TOML keys and values with quotes, dots and newlines", () => {
    const config = fixture();
    const name = 'Agent. "one"';
    config.mcpServers = { [name]: { ...config.mcpServers["nexus-agent"], headers: { "X-Example": 'line 1\n"line 2"' } } };
    const result = parse(serializeClientConfig(config, undefined, "codex")) as any;
    expect(result.mcp_servers[name].http_headers["X-Example"]).toBe('line 1\n"line 2"');
  });
  it("fails closed on unsupported transport, empty servers, invalid variables and inline template credentials", () => {
    expect(() => serializeClientConfig({ ...fixture(), transport: "stdio" })).toThrow("Unsupported");
    expect(() => serializeClientConfig({ ...fixture(), mcpServers: {} })).toThrow("No MCP server");
    const config = fixture(); config.credential.environment_variable = "INVALID VAR";
    expect(() => serializeClientConfig(config)).toThrow("Invalid MCP credential");
    expect(() => serializeClientConfig({ ...fixture(), template: true }, "test-key")).toThrow("Sign in");
  });
});
