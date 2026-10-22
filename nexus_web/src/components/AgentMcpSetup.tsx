import { t, useLocale } from "../localization";
import { useEffect, useId, useState, type ReactNode } from "react";
import { Check, Copy, Loader2 } from "lucide-react";
import type { AgentMcpExport } from "../lib/types";
import { mcpClients, serializeClientConfig, type McpClient } from "../lib/agentMcpConfig";
import { CodexHookSetup } from "./CodexHookSetup";
import { Badge } from "./Badge";
import { hasDeclaredCodexHooks } from "../lib/codexHookConfig";
export { serializeClientConfig } from "../lib/agentMcpConfig";

const CLIENT_PREFERENCE = "nexus.mcp.client";
function preferredClient(): McpClient {
  try {
    const value = localStorage.getItem(CLIENT_PREFERENCE);
    if (value && Object.hasOwn(mcpClients, value)) return value as McpClient;
  } catch { /* Preference storage is optional. */ }
  return "claude";
}

type Props = {
  config: AgentMcpExport | null;
  plaintextKey?: string;
  loading?: boolean;
  error?: string;
  onLoad: () => void;
  credentials?: ReactNode;
  workspaceName?: string;
  projectName?: string;
};

export function AgentMcpSetup({ config, plaintextKey = "", loading = false, error = "", onLoad,
  credentials, workspaceName = "", projectName = "" }: Props) {
  useLocale();
  const id = useId();
  const [client, setClient] = useState<McpClient>(preferredClient);
  const [usage, setUsage] = useState("tool");
  const hasHooks = hasDeclaredCodexHooks(config);
  useEffect(() => { setUsage("tool"); }, [config?.agent_id, hasHooks]);
  const [credentialMode, setCredentialMode] = useState("reference");
  const [copyError, setCopyError] = useState(false);
  const [copied, setCopied] = useState("");
  const target = mcpClients[client];
  const inline = credentialMode === "inline" && Boolean(plaintextKey) && !config?.template;
  let content = "", configError = "";
  try {
    if (config) content = serializeClientConfig(config, inline ? plaintextKey : undefined, client);
  } catch { configError = t("MCP configuration is unavailable. Reload setup and try again."); }
  const unavailable = error || configError;
  const scope = config?.scope.kind === "project"
    ? [workspaceName, projectName || t("Current project")].filter(Boolean).join(" / ")
    : config?.scope.kind === "workspace" ? workspaceName || t("Organization-wide") : t("Authentication required");
  const copiedCurrent = Boolean(content) && copied === content;
  useEffect(() => { setCopied(""); setCopyError(false); }, [plaintextKey, config]);
  async function copyConfiguration() {
    try { await navigator.clipboard.writeText(content); setCopyError(false); setCopied(content); }
    catch { setCopyError(true); setCopied(""); }
  }
  function selectClient(value: McpClient) {
    setClient(value); setCopied(""); setCopyError(false);
    try { localStorage.setItem(CLIENT_PREFERENCE, value); } catch { /* Preference only. */ }
  }
  return <section className="min-w-0 rounded-xl border border-line bg-white" aria-labelledby={id + "-title"}>
    <header className="border-b border-line px-4 py-4 sm:px-6">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id={id + "-title"} className="text-lg font-semibold text-ink">{t("Connect Agent")}</h2>
        {hasHooks && <span title={t("This Agent declares Hooks in its SDK configuration.")} aria-label={t("SDK-declared Hooks")}>
          <Badge tone="info">{t("Hooks")}</Badge>
        </span>}
      </div>
      <p className="mt-1 text-sm text-muted">{t("Use this Agent in your development tools.")}</p>
    </header>
    <div className="grid min-w-0 gap-4 p-4 sm:p-6">
      <label className="grid gap-2 text-sm font-medium" htmlFor={id + "-client"}>{t("MCP client")}
        <select id={id + "-client"} className="input min-h-11 w-full sm:max-w-sm" value={client}
          onChange={event => selectClient(event.target.value as McpClient)}>
          {Object.entries(mcpClients).map(([value, item]) => <option key={value} value={value}>{item.label}</option>)}
        </select>
      </label>
      {client === "codex" && hasHooks && <label className="grid gap-2 text-sm font-medium" htmlFor={id + "-usage"}>{t("Connection usage")}
        <select id={id + "-usage"} className="input min-h-11 w-full sm:max-w-sm" value={usage} onChange={event => setUsage(event.target.value)}>
          <option value="tool">{t("Tool calls")}</option><option value="hook">{t("Codex Hook")}</option>
        </select>
      </label>}
      {loading ? <div className="flex min-h-48 items-center justify-center gap-2 text-sm text-muted" role="status">
        <Loader2 size={16} className="animate-spin" />{t("Preparing configuration…")}</div>
        : unavailable || !content ? <div className="grid min-h-48 content-center justify-items-start gap-3">
          <p role="alert" className="text-sm text-danger">{unavailable || t("Could not prepare the connection. Try again.")}</p>
          <button className="btn min-h-11" onClick={onLoad}>{t("Retry configuration")}</button>
        </div> : client === "codex" && hasHooks && usage === "hook" ? <CodexHookSetup key={config!.agent_id} config={config!} onReload={onLoad} /> : <>
          <div className="min-w-0 overflow-hidden rounded-lg border border-line">
            <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-b border-line bg-paper px-4 py-3 text-sm">
              <code className="min-w-0 break-all">{target.path}</code><span className="text-xs text-muted">{target.format}</span>
            </div>
            <pre className="m-0 max-h-56 min-h-40 max-w-full overflow-auto bg-white p-4 font-mono text-xs leading-6 text-ink sm:text-sm"
              aria-label={t("Generated MCP configuration")} tabIndex={0}>{content}</pre>
          </div>
          <div className="sticky bottom-0 z-10 grid gap-3 border-t border-line bg-white py-3 sm:static sm:flex sm:items-end sm:justify-between">
            <label className="grid gap-1 text-sm" htmlFor={id + "-credential"}>{t("Credential method")}
              <select id={id + "-credential"} className="input min-h-11" value={inline ? "inline" : "reference"}
                onChange={event => { setCredentialMode(event.target.value); setCopied(""); setCopyError(false); }}>
                <option value="reference">{client === "vscode" ? t("Secure input prompt") : t("Environment variable")}</option>
                <option value="inline" disabled={!plaintextKey || config?.template}>{t("Include API key")}</option>
              </select>
            </label>
            <button className="btn btn-primary min-h-11 min-w-40 justify-center" onClick={() => void copyConfiguration()}>
              {copiedCurrent ? <Check size={15} /> : <Copy size={15} />}{t(copiedCurrent ? "Configuration copied" : "Copy configuration")}
            </button>
          </div>
          {copyError && <p role="alert" className="text-sm text-danger">{t("Copy failed. Select the configuration above and copy it manually.")}</p>}
          <div className="grid gap-2 text-sm leading-6 text-muted">
            {inline ? <p>{t("Contains your API key. Keep the copied configuration private.")}</p>
              : <p>{client === "vscode" ? t("VS Code will ask for your API key when connecting.")
                : t("Set {{0}} in the environment that launches your client, then restart it. A project .env file is not automatically loaded.", { 0: config!.credential.environment_variable })}</p>}
            <p>{t("Merge into {{0}}, keep your other servers, then reload {{1}}.", { 0: target.path, 1: target.label })}{" "}
              <a href={target.docs} target="_blank" rel="noreferrer" className="underline underline-offset-4">{t("Client documentation")}</a></p>
            <p role="status">{config?.template ? t("Sign in and load setup again to include your Organization and Project.")
              : t("Configuration prepared · Client connection not verified")}</p>
          </div>
        </>}
      <div className="grid min-w-0 gap-3 border-t border-line pt-4">
        {credentials}
        {config && <p className="break-words text-sm text-muted" title={projectName || workspaceName}>{t("Access scope")}: {scope}</p>}
        {config && <details className="min-w-0 text-sm text-muted">
          <summary className="min-h-11 cursor-pointer py-3">{t("Connection details")}</summary>
          <dl className="grid min-w-0 gap-2 py-2">
            <div><dt>{t("Transport")}</dt><dd className="break-all font-mono">{config.transport}</dd></div>
            <div><dt>{t("Server")}</dt><dd className="break-all font-mono">{config.server_name}</dd></div>
            <div><dt>{t("Endpoint")}</dt><dd className="break-all font-mono">{Object.values(config.mcpServers)[0]?.url}</dd></div>
          </dl>
        </details>}
      </div>
    </div>
  </section>;
}
