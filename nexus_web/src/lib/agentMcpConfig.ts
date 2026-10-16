import { stringify as stringifyToml } from "smol-toml";
import type { AgentMcpExport } from "./types";

export const mcpClients = {
  claude: { label: "Claude Code", path: ".mcp.json", format: "JSON",
    docs: "https://code.claude.com/docs/en/mcp" },
  codex: { label: "Codex", path: "~/.codex/config.toml", format: "TOML",
    docs: "https://developers.openai.com/codex/mcp" },
  cursor: { label: "Cursor", path: ".cursor/mcp.json", format: "JSON",
    docs: "https://cursor.com/docs/mcp" },
  vscode: { label: "VS Code / GitHub Copilot", path: ".vscode/mcp.json", format: "JSON",
    docs: "https://code.visualstudio.com/docs/agents/reference/mcp-configuration" },
} as const;
export type McpClient = keyof typeof mcpClients;

/** The export API is canonical connection data, not a client configuration.
 * Whitelist client fields; never forward Nexus metadata as client settings.
 * Caller scope headers remain unchanged, including for Marketplace Agents.
 */
export function serializeClientConfig(
  config: AgentMcpExport, plaintextKey?: string, client: McpClient = "claude",
) {
  if (config.transport !== "streamable-http") throw new Error("Unsupported MCP transport.");
  if (!Object.keys(config.mcpServers).length) throw new Error("No MCP server is available.");
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(config.credential.environment_variable)) {
    throw new Error("Invalid MCP credential variable.");
  }
  if (plaintextKey && config.template) throw new Error("Sign in to generate a caller-scoped configuration.");
  const variable = config.credential.environment_variable;
  const inputId = `nexus-key-${config.agent_id}`;
  const servers = Object.fromEntries(Object.entries(config.mcpServers).map(([name, server]) => {
    const headers = Object.fromEntries(Object.entries(server.headers)
      .filter(([key]) => key.toLowerCase() !== "authorization"));
    if (client === "codex") {
      if (plaintextKey) headers.Authorization = `Bearer ${plaintextKey}`;
      return [name, { url: server.url,
        ...(!plaintextKey ? { bearer_token_env_var: variable } : {}), http_headers: headers }];
    }
    const reference = client === "vscode" ? `\${input:${inputId}}`
      : client === "cursor" ? `\${env:${variable}}` : `\${${variable}}`;
    headers.Authorization = `Bearer ${plaintextKey || reference}`;
    return [name, { ...(client === "cursor" ? {} : { type: "http" }), url: server.url, headers }];
  }));
  if (client === "codex") return stringifyToml({ mcp_servers: servers });
  if (client === "vscode") return JSON.stringify({
    ...(!plaintextKey ? { inputs: [{ type: "promptString", id: inputId,
      description: "Nexus Agent API key", password: true }] } : {}), servers,
  }, null, 2);
  return JSON.stringify({ mcpServers: servers }, null, 2);
}
