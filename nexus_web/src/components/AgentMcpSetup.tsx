import { t, useLocale } from "../localization";
import { useId } from "react";
import { Copy, FileOutput, Loader2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import type { AgentMcpExport } from "../lib/types";

type AgentMcpSetupProps = {
  config: AgentMcpExport | null;
  plaintextKey?: string;
  loading?: boolean;
  onLoad: () => void;
  onForgetPlaintext?: () => void;
  title?: string;
  description?: string;
  workspaceName?: string;
  projectName?: string;
};

export function AgentMcpSetup({
  config,
  plaintextKey = "",
  loading = false,
  onLoad,
  onForgetPlaintext,
  title = t("MCP setup"),
  description = t("Use the same caller-scoped configuration in any Streamable HTTP MCP client."),
  workspaceName = "",
  projectName = "",
}: AgentMcpSetupProps) {
  useLocale();
  const titleId = useId();
  const server = config ? Object.values(config.mcpServers)[0] : null;
  const secureConfig = config ? serializeClientConfig(config) : "";
  const readyConfig =
    config && plaintextKey
      ? serializeClientConfig(config, plaintextKey)
      : "";
  const scopeLabel =
    config?.scope.kind === "project"
      ? projectName || t("Current project")
      : config?.scope.kind === "workspace"
        ? workspaceName
          ? t("{{0}} · Organization-wide", { 0: workspaceName })
          : t("Organization-wide")
        : t("Authentication required");

  return (
    <section className="agent-control-section" aria-labelledby={titleId}>
      <div className="agent-control-section__header">
        <div>
          <p className="agent-control-eyebrow">{t("Client configuration")}</p>
          <h2 id={titleId}>{title}</h2>
          <p>{description}</p>
        </div>
        <button className="btn" onClick={onLoad} disabled={loading}>
          {loading ? (
            <Loader2 size={15} className="animate-spin" />
          ) : (
            <FileOutput size={15} />
          )}
          {config ? t("Refresh setup") : t("Load setup")}
        </button>
      </div>

      {config && server ? (
        <div className="grid gap-4">
          <div className="grid gap-px border border-line bg-line sm:grid-cols-2 xl:grid-cols-4">
            <McpFact label={t("Transport")} value={config.transport} />
            <McpFact label={t("Scope")} value={scopeLabel} />
            <McpFact label={t("Server")} value={config.server_name} mono />
            <McpFact
              label={t("Authentication")}
              value={plaintextKey ? "One-time key ready" : "Environment variable"}
            />
          </div>
          <div className="grid gap-2">
            <span className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">{t("Endpoint")}</span>
            <code className="agent-code-block break-all">{server.url}</code>
          </div>
          <div className="grid gap-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm font-medium text-ink">{t("Secure configuration")}</div>
                <div className="text-xs text-muted">{t("Set")}{" "}{config.credential.environment_variable}{" "}{t("before starting the client.")}</div>
              </div>
              <button className="btn" onClick={() => copyValue(secureConfig)}>
                <Copy size={14} />{t("Copy secure config")}</button>
            </div>
            <pre className="agent-json-block">{secureConfig}</pre>
          </div>
          {readyConfig && (
            <div className="grid gap-3 border-l-2 border-lume bg-[#f5f8dc] p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-start gap-2">
                  <ShieldCheck size={17} className="mt-0.5 text-ink" />
                  <div>
                    <div className="text-sm font-medium text-ink">{t("One-time ready configuration")}</div>
                    <div className="text-xs leading-5 text-muted">{t("Contains the plaintext key. It exists only in this browser session.")}</div>
                  </div>
                </div>
                <button
                  className="btn btn-primary"
                  onClick={() => copyValue(readyConfig)}
                >
                  <Copy size={14} />{t("Copy ready config")}</button>
                {onForgetPlaintext && (
                  <button className="btn" onClick={onForgetPlaintext}>{t("Forget plaintext key")}</button>
                )}
              </div>
            </div>
          )}
        </div>
      ) : (
        <p className="agent-inline-help">{t("Load setup to generate a caller-scoped configuration. Organization and Project values are filled by Nexus.")}</p>
      )}
    </section>
  );
}

function McpFact({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  useLocale();
  return (
    <div className="min-w-0 bg-paper p-3">
      <div className="text-[0.65rem] font-semibold uppercase tracking-[0.08em] text-muted">
        {label}
      </div>
      <div
        className={`mt-1 truncate text-sm font-medium text-ink ${mono ? "font-mono" : ""}`}
        title={value}
      >
        {value}
      </div>
    </div>
  );
}

export function serializeClientConfig(
  config: AgentMcpExport,
  plaintextKey?: string,
) {
  const mcpServers = structuredClone(config.mcpServers);
  if (plaintextKey) {
    for (const server of Object.values(mcpServers)) {
      if (
        server.headers.Authorization ===
        `Bearer ${config.credential.placeholder}`
      ) {
        server.headers.Authorization = `Bearer ${plaintextKey}`;
      }
    }
  }
  return JSON.stringify({ mcpServers }, null, 2);
}

async function copyValue(value: string) {
  if (!value) return;
  await navigator.clipboard.writeText(value);
  toast.success(t("Copied"));
}
