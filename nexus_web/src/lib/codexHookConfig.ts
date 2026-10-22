import { parse, stringify } from "smol-toml";
import type { AgentMcpExport } from "./types";
import { serializeClientConfig } from "./agentMcpConfig";

export const codexHookEvents = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
  "PostToolUse", "PreCompact", "PostCompact", "SubagentStart", "SubagentStop", "Stop", "Interrupt"] as const;
const withoutMatcher = new Set(["UserPromptSubmit", "Stop", "Interrupt"]);

/** An absent declaration is an ordinary MCP Agent, not a setup failure.
 * Invalid published declarations remain visible for diagnosis. */
export function hasDeclaredCodexHooks(config: AgentMcpExport | null | undefined): boolean {
  return Boolean(config && !config.template && config.tools?.some(tool =>
    tool.codex_hooks_invalid === true || (Array.isArray(tool.codex_hooks) && tool.codex_hooks.length > 0)));
}

/** Only deployed SDK mappings are executable. Server/tool/type are bound here,
 * never accepted from publisher-supplied Hook metadata. */
export function serializeCodexHook(config: AgentMcpExport, key?: string) {
  if (config.template) throw new Error("Sign in before configuring a Hook.");
  const hooks: Record<string, unknown[]> = {};
  for (const tool of config.tools ?? []) {
    if (tool.codex_hooks_invalid) throw new Error("The Agent has an invalid Hook declaration. Ask its author to redeploy.");
    const declarations = tool.codex_hooks ?? [];
    if (!Array.isArray(declarations) || declarations.length > 16) throw new Error("Invalid SDK Hook declaration.");
    const seen = new Set<string>();
    for (const declaration of declarations) {
      if (!declaration || Object.keys(declaration).some(key => !["event", "input", "matcher", "timeout"].includes(key))) throw new Error("Invalid SDK Hook declaration.");
      const { event, input, matcher } = declaration;
      if (!(codexHookEvents as readonly string[]).includes(event)) throw new Error("Unsupported Hook event.");
      const timeout = declaration.timeout ?? (event === "Interrupt" ? 1 : 30);
      if (!Number.isInteger(timeout) || timeout < 1 || timeout > (event === "Interrupt" ? 3 : 600)) throw new Error("Invalid Hook timeout.");
      if (matcher !== undefined && (withoutMatcher.has(event) || typeof matcher !== "string" || matcher.length > 256)) throw new Error("Invalid SDK Hook matcher.");
      if (!input || typeof input !== "object" || Array.isArray(input) || new TextEncoder().encode(JSON.stringify(input)).length > 8192) throw new Error("Invalid SDK Hook arguments.");
      const required = tool.input_schema.required;
      if (Array.isArray(required) && required.some(name => typeof name === "string" && !Object.hasOwn(input, name))) throw new Error("SDK Hook arguments are missing required fields.");
      const identity = JSON.stringify([event, matcher || "*"]);
      if (seen.has(identity)) throw new Error("Duplicate SDK Hook declaration.");
      seen.add(identity);
      (hooks[event] ??= []).push({
        ...(matcher !== undefined ? { matcher } : {}),
        hooks: [{ type: "mcp_tool", server: config.server_name, tool: tool.name, input, timeout, statusMessage: "Running Nexus Hook" }],
      });
    }
  }
  if (!Object.keys(hooks).length) throw new Error("This Agent has not published any Codex Hooks. Ask its author to declare hooks in the SDK and redeploy.");
  const connection = parse(serializeClientConfig(config, key, "codex"));
  if (!Object.hasOwn(config.mcpServers, config.server_name)) throw new Error("MCP server name does not match the connection.");
  return stringify({ ...connection, hooks });
}
