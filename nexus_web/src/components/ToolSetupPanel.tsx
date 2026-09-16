import { t, useLocale, getLocale } from "../localization";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, ChevronRight, KeyRound, Loader2, Plus, RefreshCw, Save, Search, Terminal, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import type { ApiContext } from "../lib/api";
import type { ToolSetupClient } from "../app/toolSetup";
import type { WorkspaceConnection, WorkspaceTerminalSession, WorkspaceToolConfigChange, WorkspaceToolConfigPreview } from "../lib/types";
import { ToolSetupPendingChange, type ToolRecoveryAction } from "./ToolSetupPendingChange";

const credentialActionLabels: Record<string, string> = {
  rotate: "Rotate credential",
  reuse: "Reuse credential",
  repair: "Repair credential",
  create: "Create credential",
  revoke: "Revoke credential"
};

export type ToolSetupForm = {
  selectedTool: "codex" | "claude_code";
  selectedRuntimeId: string;
  selectedAgentId: string;
};

export type TerminalToolStatus = {
  checked_at?: string;
  runner?: string;
  error?: string;
  tools?: Record<string, { installed?: boolean; command?: string; path?: string; version?: string }>;
};

export function ToolSetupPanel({
  client,
  apiContext,
  isContextReady,
  form,
  selectedConnection,
  activeSession,
  isDetecting,
  isOpeningSession,
  onChange,
  onClose,
  onDetectTools,
  onOpenSession
}: {
  client: ToolSetupClient;
  apiContext: ApiContext;
  isContextReady: boolean;
  form: ToolSetupForm;
  selectedConnection: WorkspaceConnection | undefined;
  activeSession: WorkspaceTerminalSession | null;
  isDetecting: boolean;
  isOpeningSession: boolean;
  onChange: (form: ToolSetupForm) => void;
  onClose: () => void;
  onDetectTools: () => void;
  onOpenSession: () => void;
}) {
  useLocale();
  const queryClient = useQueryClient();
  const toolStatus = getToolStatus(activeSession);
  const [tab, setTab] = useState<"overview" | "api" | "agents">("overview");
  const [routerSearch, setRouterSearch] = useState("");
  const [selectedRouterId, setSelectedRouterId] = useState("");
  const [agentSearch, setAgentSearch] = useState("");
  const [preview, setPreview] = useState<WorkspaceToolConfigPreview | null>(null);
  const [pendingChange, setPendingChange] = useState<WorkspaceToolConfigChange | null>(null);
  const [recoveryError, setRecoveryError] = useState("");

  const toolConfig = useQuery({
    queryKey: ["workspace-tool-config", apiContext, activeSession?.id, "codex", "v2"],
    queryFn: () => client.workspaceToolConfig(apiContext, activeSession?.id || "", "codex"),
    enabled: isContextReady && !!activeSession
  });
  const setupOptions = useQuery({
    queryKey: ["workspace-tool-config-options", apiContext, activeSession?.id, "codex"],
    queryFn: () => client.workspaceToolConfigOptions(apiContext, activeSession?.id || "", "codex"),
    enabled: isContextReady && !!activeSession
  });
  const remoteConfig = toolConfig.data;
  const routerOptions = setupOptions.data?.routers ?? [];
  const agentOptions = setupOptions.data?.agents ?? [];
  const writesAllowed = remoteConfig?.write_availability?.available !== false;

  useEffect(() => {
    const preferred = remoteConfig?.api.router_id || routerOptions.find((item) => item.available)?.id || "";
    if (!selectedRouterId && preferred) setSelectedRouterId(preferred);
  }, [remoteConfig?.api.router_id, routerOptions, selectedRouterId]);

  function clearPreview() {
    setPreview(null);
    setPendingChange(null);
  }

  const previewChange = useMutation({
    mutationFn: (change: WorkspaceToolConfigChange) => {
      if (!activeSession || !remoteConfig) throw new Error(t("Reload the remote configuration before reviewing changes."));
      if (!writesAllowed) throw new Error(t("Resolve the pending configuration or Computer status before reviewing changes."));
      return client.previewWorkspaceToolConfig(apiContext, activeSession.id, {
        ...change,
        tool: "codex",
        expected_revision: remoteConfig.revision
      });
    },
    onSuccess: (result, variables) => {
      setPreview(result);
      setPendingChange({ ...variables, expected_revision: result.revision });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("Could not preview this change"))
  });
  const applyChange = useMutation({
    mutationFn: () => {
      if (!activeSession || !pendingChange) throw new Error(t("Review a change before applying it."));
      if (!writesAllowed || pendingChange.expected_revision !== remoteConfig?.revision) throw new Error(t("The configuration changed. Reload and review it again."));
      return client.applyWorkspaceToolConfigV2(apiContext, activeSession.id, {
        ...pendingChange,
        confirm_destructive: preview?.destructive || pendingChange.confirm_destructive
      });
    },
    onSuccess: async (result) => {
      queryClient.setQueryData(
        ["workspace-tool-config", apiContext, activeSession?.id, "codex", "v2"],
        result
      );
      clearPreview();
      toast.success(t("Tool setup updated"));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["workspace-tool-config"] }),
        queryClient.invalidateQueries({ queryKey: ["workspace-tool-config-options"] })
      ]);
    },
    onError: async (error) => {
      clearPreview();
      toast.error(error instanceof Error ? error.message : t("Failed to apply Tool setup"));
      await queryClient.invalidateQueries({ queryKey: ["workspace-tool-config"] });
    }
  });
  const recoverChange = useMutation({
    mutationFn: (action: ToolRecoveryAction) => {
      const recovery = remoteConfig?.recovery;
      if (!activeSession || !remoteConfig || !recovery?.available || !recovery.operation_id || !recovery.actions?.includes(action)) {
        throw new Error(t("Reload this Computer's recovery status first."));
      }
      return client.recoverWorkspaceToolConfig(apiContext, activeSession.id, {
        action, operation_id: recovery.operation_id, expected_revision: remoteConfig.revision
      });
    },
    onMutate: () => { clearPreview(); setRecoveryError(""); },
    onSuccess: async (result) => {
      queryClient.setQueryData(["workspace-tool-config", apiContext, activeSession?.id, "codex", "v2"], result);
      toast.success(t("Computer configuration checked"));
      await queryClient.invalidateQueries({ queryKey: ["workspace-tool-config-options"] });
    },
    onError: (error) => setRecoveryError(error instanceof Error ? error.message : t("Could not confirm recovery. Reload status and try again."))
  });
  useEffect(() => {
    if (pendingChange && (!writesAllowed || pendingChange.expected_revision !== remoteConfig?.revision)) clearPreview();
  }, [pendingChange, writesAllowed, remoteConfig?.revision]);
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !applyChange.isPending && !recoverChange.isPending && !previewChange.isPending) onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [applyChange.isPending, recoverChange.isPending, previewChange.isPending, onClose]);

  const filteredRouters = routerOptions.filter((item) =>
    `${item.name} ${item.model} ${item.strategy}`.toLowerCase().includes(routerSearch.trim().toLowerCase())
  );
  const connectedIds = new Set(remoteConfig?.agents.managed.map((item) => item.agent_id) ?? []);
  const filteredAgents = agentOptions.filter((item) =>
    !connectedIds.has(item.id) && `${item.name} ${item.version} ${item.visibility}`.toLowerCase().includes(agentSearch.trim().toLowerCase())
  );
  const selectedRouter = routerOptions.find((item) => item.id === selectedRouterId);
  const busy = previewChange.isPending || applyChange.isPending || recoverChange.isPending;
  const canApplyPreview = Boolean(writesAllowed && preview?.can_apply && pendingChange);

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-slate-950/40"
      role="dialog"
      aria-modal="true"
      aria-labelledby="tool-setup-title"
      onMouseDown={() => { if (!busy) onClose(); }}
    >
      <section className="flex h-full w-full max-w-xl flex-col overflow-hidden border-l border-line bg-white shadow-overlay" onMouseDown={(event) => event.stopPropagation()}>
        <header className="shrink-0 border-b border-line bg-white px-4 pt-4 sm:px-6">
          <div className="flex items-start justify-between gap-4 pb-4">
            <div className="min-w-0">
              <h2 id="tool-setup-title" className="text-lg font-semibold tracking-[-0.02em] text-ink">{t("Tool setup")}</h2>
              <p className="mt-1 truncate text-sm text-muted">
                {selectedConnection ? `${selectedConnection.name} · ${formatComputerConnection(selectedConnection)}` : t("No Computer selected")}
              </p>
            </div>
            <button className="btn h-10 w-10 shrink-0 p-0" type="button" aria-label={t("Close tool setup")} disabled={busy} onClick={onClose}><X size={17} /></button>
          </div>
          <nav className="grid grid-cols-3 gap-1 rounded-xl bg-slate-100 p-1" aria-label={t("Tool setup sections")}>
            {(["overview", "api", "agents"] as const).map((item) => (
              <button
                key={item}
                className={`min-h-11 rounded-lg px-2 text-sm font-semibold transition ${tab === item ? "bg-white text-ink shadow-sm" : "text-muted hover:text-ink"}`}
                type="button"
                aria-current={tab === item ? "page" : undefined}
                disabled={busy}
                onClick={() => { setTab(item); clearPreview(); }}
              >
                {item === "overview" ? t("Overview") : item === "api" ? t("API setup") : t("Agent setup")}
              </button>
            ))}
          </nav>
          <div className="h-3" />
        </header>

        <div className="flex-1 overflow-y-auto">
          {remoteConfig && <ToolSetupPendingChange
            key={`${remoteConfig.recovery.operation_id || "none"}:${remoteConfig.revision}`}
            config={remoteConfig} busy={busy} error={recoveryError}
            onRecover={(action) => recoverChange.mutate(action)}
            onReload={() => { clearPreview(); setRecoveryError(""); void toolConfig.refetch(); void setupOptions.refetch(); }}
          />}
          {!activeSession ? (
            <section className="px-4 py-6 sm:px-6">
              <div className="rounded-xl border border-dashed border-line bg-slate-50 p-5">
                <Terminal size={24} className="text-slate-600" />
                <h3 className="mt-3 text-sm font-semibold text-ink">{t("Open a terminal session first")}</h3>
                <p className="mt-1 text-sm leading-6 text-muted">{t("You can inspect known tool status without a session, but Nexus needs an active Computer Runtime terminal to read or change Codex settings.")}</p>
                <button className="btn btn-primary mt-4 min-h-11" type="button" disabled={!selectedConnection || isOpeningSession} onClick={onOpenSession}>
                  {isOpeningSession ? <Loader2 size={16} className="animate-spin" /> : <Terminal size={16} />}{" "}{t("Open terminal")}</button>
              </div>
            </section>
          ) : toolConfig.isLoading || setupOptions.isLoading ? (
            <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-muted"><Loader2 size={18} className="animate-spin" />{t("Reading Tool setup…")}</div>
          ) : toolConfig.isError || setupOptions.isError || !remoteConfig ? (
            <section className="px-4 py-6 sm:px-6">
              <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900" role="alert">
                <div className="font-semibold">{t("Tool setup could not be loaded")}</div>
                <p className="mt-1 leading-6">{toolConfig.error instanceof Error ? toolConfig.error.message : setupOptions.error instanceof Error ? setupOptions.error.message : t("Reload the active session and try again.")}</p>
                <button className="btn mt-3" type="button" onClick={() => { void toolConfig.refetch(); void setupOptions.refetch(); }}><RefreshCw size={15} />{t("Retry")}</button>
              </div>
            </section>
          ) : tab === "overview" ? (
            <div className="divide-y divide-line">
              <section className="px-4 py-5 sm:px-6">
                <div className="flex items-start justify-between gap-3">
                  <div><h3 className="text-sm font-semibold text-ink">{t("Tools on this Computer")}</h3><p className="mt-1 text-xs leading-5 text-muted">{toolStatus?.checked_at ? t("Checked {{0}}", { 0: new Date(toolStatus.checked_at).toLocaleString(getLocale()) }) : t("Status has not been checked in this session.")}</p></div>
                  <button className="btn h-9 shrink-0 px-3 text-xs" type="button" disabled={isDetecting} onClick={onDetectTools}>{isDetecting ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}{t("Refresh")}</button>
                </div>
                <div className="mt-4 overflow-hidden rounded-xl border border-line">
                  <SetupStatusRow name="Codex" status={toolStatus?.tools?.codex?.installed ? t("Detected") : t("Not detected")} detail={toolStatus?.tools?.codex?.version || t("Managed setup available")} ready={Boolean(toolStatus?.tools?.codex?.installed)} />
                  <SetupStatusRow name="Claude Code" status={toolStatus?.tools?.claude_code?.installed ? t("Detected") : t("Not detected")} detail={t("Status only · managed setup is not available")} ready={Boolean(toolStatus?.tools?.claude_code?.installed)} />
                </div>
              </section>
              <section className="px-4 py-5 sm:px-6">
                <h3 className="text-sm font-semibold text-ink">{t("Configuration")}</h3>
                <div className="mt-3 overflow-hidden rounded-xl border border-line">
                  <SetupActionRow
                    title={t("Router API exit")}
                    status={remoteConfig.api.status === "ready" ? t("Ready") : remoteConfig.api.status === "needs_repair" ? t("Needs repair") : t("Setup required")}
                    detail={remoteConfig.api.router_name ? t("{{0}} · {{1}} model{{2}}", { 0: remoteConfig.api.router_name, 1: remoteConfig.api.models?.length || 1, 2: (remoteConfig.api.models?.length || 1) === 1 ? "" : "s" }) : t(remoteConfig.api.message)}
                    ready={remoteConfig.api.status === "ready"}
                    action={t("Open API setup")}
                    onClick={() => setTab("api")}
                  />
                  <SetupActionRow
                    title={t("Agent MCP")}
                    status={t("{{0}} connected", { 0: remoteConfig.agents.managed.length })}
                    detail={remoteConfig.agents.external.length ? t("{{0}} external server{{1}} preserved", { 0: remoteConfig.agents.external.length, 1: remoteConfig.agents.external.length === 1 ? "" : "s" }) : t("External MCP servers are never changed by normal Agent setup")}
                    ready={remoteConfig.agents.credential_status !== "needs_repair"}
                    action={t("Open Agent setup")}
                    onClick={() => setTab("agents")}
                  />
                </div>
              </section>
            </div>
          ) : tab === "api" ? (
            <div className="divide-y divide-line">
              <section className="px-4 py-5 sm:px-6">
                <SetupNotice status={remoteConfig.api.status} title={remoteConfig.api.status === "ready" ? t("Codex API is routed") : remoteConfig.api.status === "needs_repair" ? t("Credential needs repair") : t("Choose a Router")} detail={t(remoteConfig.api.message)} />
                <p className="mt-4 text-sm leading-6 text-muted">{t("Codex sends requests through the selected Router. Its Pool and Source policy decides which Provider serves each request.")}</p>
                <div className="mt-4 flex items-center gap-2 rounded-lg border border-line bg-white px-3"><Search size={16} className="text-muted" /><input className="h-11 min-w-0 flex-1 bg-transparent text-sm outline-none" value={routerSearch} onChange={(event) => setRouterSearch(event.target.value)} placeholder={t("Search Routers")} aria-label={t("Search Routers")} /></div>
                <div className="mt-3 max-h-72 divide-y divide-line overflow-y-auto rounded-xl border border-line">
                  {filteredRouters.length ? filteredRouters.map((router) => (
                    <label key={router.id} className={`flex min-h-14 items-start gap-3 px-3 py-3 ${router.available ? "cursor-pointer hover:bg-slate-50" : "cursor-not-allowed bg-slate-50 opacity-70"}`} title={router.message ? t(router.message) : undefined}>
                      <input className="mt-1" type="radio" name="api-router" disabled={!router.available} checked={selectedRouterId === router.id} onChange={() => { setSelectedRouterId(router.id); clearPreview(); }} />
                      <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-ink">{router.name}</span><span className="mt-0.5 block text-xs text-muted">{router.router_type === "aggregation" ? t("Aggregation Router") : t("Execution Router")} · {router.models?.length || (router.model ? 1 : 0)}{" "}{t("model")}{getLocale() === "zh-CN" ? "" : (router.models?.length || (router.model ? 1 : 0)) === 1 ? "" : "s"} · {router.available ? (router.models?.join(", ") || router.model) : t(router.message)}</span></span>
                    </label>
                  )) : <div className="p-4 text-sm text-muted">{t("No Router matches this search. Create a Router, add a Model Pool, and deploy it first.")}</div>}
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <button className="btn btn-primary min-h-11" type="button" disabled={!selectedRouter?.available || busy || !writesAllowed} onClick={() => previewChange.mutate({ tool: "codex", section: "api", action: "set_router", router_id: selectedRouterId })}>{previewChange.isPending ? <Loader2 size={16} className="animate-spin" /> : <ChevronRight size={16} />}{t("Review API route")}</button>
                  <button className="btn min-h-11" type="button" disabled={!remoteConfig.api.credential_managed || !selectedRouter?.available || busy || !writesAllowed} onClick={() => previewChange.mutate({ tool: "codex", section: "api", action: "rotate_api_credential", router_id: selectedRouterId })}><KeyRound size={16} />{t("Rotate credential")}</button>
                </div>
              </section>
            </div>
          ) : (
            <div className="divide-y divide-line">
              <section className="px-4 py-5 sm:px-6">
                <div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-semibold text-ink">{t("Connected Agents")}</h3><p className="mt-1 text-xs text-muted">{t("Each change preserves Provider API and every other MCP server.")}</p></div>{remoteConfig.agents.credential_status === "needs_repair" && <span className="rounded-full bg-amber-100 px-2 py-1 text-xs font-semibold text-amber-800">{t("Needs repair")}</span>}</div>
                {remoteConfig.agents.managed.length ? <div className="mt-3 divide-y divide-line overflow-hidden rounded-xl border border-line">{remoteConfig.agents.managed.map((agent) => (
                  <div key={agent.server_name} className="flex min-h-14 items-center justify-between gap-3 px-3 py-3"><div className="min-w-0"><div className="truncate text-sm font-medium text-ink">{agent.name}</div><div className="mt-0.5 truncate text-xs text-muted">{agent.server_name} · {agent.available ? t("Available") : t(agent.message)}</div></div><button className="btn h-9 shrink-0 px-3 text-xs" type="button" disabled={busy || !writesAllowed} onClick={() => previewChange.mutate({ tool: "codex", section: "agents", action: "remove_agent", mcp_server_name: agent.server_name })}><Trash2 size={14} />{t("Remove")}</button></div>
                ))}</div> : <div className="mt-3 rounded-xl border border-dashed border-line bg-slate-50 p-4 text-sm text-muted">{t("No Nexus Agent is connected yet.")}</div>}
                {remoteConfig.agents.managed.length > 0 && <button className="btn mt-3 min-h-11" type="button" disabled={busy || !writesAllowed} onClick={() => previewChange.mutate({ tool: "codex", section: "agents", action: "rotate_agent_credential" })}><KeyRound size={16} />{t("Rotate Agent credential")}</button>}
              </section>
              <section className="px-4 py-5 sm:px-6">
                <h3 className="text-sm font-semibold text-ink">{t("Available Agents")}</h3>
                <div className="mt-3 flex items-center gap-2 rounded-lg border border-line px-3"><Search size={16} className="text-muted" /><input className="h-11 min-w-0 flex-1 bg-transparent text-sm outline-none" value={agentSearch} onChange={(event) => setAgentSearch(event.target.value)} placeholder={t("Search Agents you can call")} aria-label={t("Search available Agents")} /></div>
                <div className="mt-3 max-h-72 divide-y divide-line overflow-y-auto rounded-xl border border-line">{filteredAgents.length ? filteredAgents.map((agent) => (
                  <div key={agent.id} className={`flex min-h-14 items-center justify-between gap-3 px-3 py-3 ${agent.available ? "" : "bg-slate-50 opacity-70"}`} title={agent.message ? t(agent.message) : undefined}><button className="min-w-0 flex-1 text-left" type="button" disabled={!agent.available} onClick={() => { onChange({ ...form, selectedAgentId: agent.id }); clearPreview(); }}><span className="block truncate text-sm font-medium text-ink">{agent.name}</span><span className="mt-0.5 block text-xs text-muted">{agent.version || t("No version")} · {agent.available ? t(agent.visibility) : t(agent.message)}</span></button><button className="btn h-9 shrink-0 px-3 text-xs" type="button" disabled={!agent.available || busy || !writesAllowed} onClick={() => { onChange({ ...form, selectedAgentId: agent.id }); previewChange.mutate({ tool: "codex", section: "agents", action: "add_agent", agent_id: agent.id }); }}><Plus size={14} />{t("Add")}</button></div>
                )) : <div className="p-4 text-sm text-muted">{t("No additional callable Agent matches this search.")}</div>}</div>
              </section>
              <section className="px-4 py-5 sm:px-6"><h3 className="text-sm font-semibold text-ink">{t("External MCP servers")}</h3><p className="mt-1 text-xs leading-5 text-muted">{t("These entries were not created by Nexus and remain read-only during normal Agent setup.")}</p>{remoteConfig.agents.external.length ? <div className="mt-3 divide-y divide-line overflow-hidden rounded-xl border border-line">{remoteConfig.agents.external.map((server) => <div key={server.server_name} className="px-3 py-3"><div className="truncate text-sm font-medium text-ink">{server.server_name}</div><div className="mt-0.5 truncate text-xs text-muted">{server.url || server.type || t("External configuration")}</div></div>)}</div> : <div className="mt-3 text-sm text-muted">{t("No external MCP servers.")}</div>}</section>
            </div>
          )}
        </div>

        {preview && pendingChange && (
          <footer className="shrink-0 border-t border-line bg-white px-4 py-3 shadow-[0_-8px_24px_rgba(15,23,42,0.06)] sm:px-6">
            <div className="mb-3 max-h-36 overflow-y-auto rounded-lg border border-line bg-slate-50 p-3" aria-live="polite">
              <div className="flex items-center justify-between gap-3"><span className="text-xs font-semibold uppercase tracking-[0.12em] text-muted">{t("Review changes")}</span><span className="text-xs font-medium text-slate-700">{t("Credential:")}{" "}{t(credentialActionLabels[preview.credential_action] || preview.credential_action)}</span></div>
              {preview.changes.map((change, index) => <div key={`${change.label}-${index}`} className="mt-2 text-xs text-slate-700"><span className="font-semibold">{t(change.label)}:</span> {t(change.before)} → {t(change.after)}</div>)}
              {preview.warnings.map((warning) => <div key={warning.code} className="mt-2 text-xs font-medium text-amber-800">{t(warning.message)}</div>)}
            </div>
            <div className="flex justify-end gap-2"><button className="btn min-h-11" type="button" disabled={applyChange.isPending} onClick={clearPreview}>{t("Cancel")}</button><button className="btn btn-primary min-h-11 min-w-32 justify-center" type="button" disabled={!canApplyPreview || applyChange.isPending} onClick={() => applyChange.mutate()}>{applyChange.isPending ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}{applyChange.isPending ? t("Applying…") : preview.can_apply ? t("Apply change") : t("Up to date")}</button></div>
          </footer>
        )}
      </section>
    </div>
  );
}

function SetupStatusRow({ name, status, detail, ready }: { name: string; status: string; detail: string; ready: boolean }) {
  useLocale();
  return <div className="flex min-h-14 items-center justify-between gap-3 border-b border-line px-3 py-3 last:border-b-0"><div className="min-w-0"><div className="text-sm font-medium text-ink">{name}</div><div className="mt-0.5 truncate text-xs text-muted">{detail}</div></div><span className={`rounded-full px-2 py-1 text-xs font-semibold ${ready ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-700"}`}>{status}</span></div>;
}

function SetupActionRow({ title, status, detail, ready, action, onClick }: { title: string; status: string; detail: string; ready: boolean; action: string; onClick: () => void }) {
  useLocale();
  return <div className="flex min-h-16 items-center justify-between gap-3 border-b border-line px-3 py-3 last:border-b-0"><div className="min-w-0"><div className="flex items-center gap-2 text-sm font-medium text-ink">{ready ? <CheckCircle2 size={15} className="text-emerald-700" /> : <AlertTriangle size={15} className="text-amber-700" />}{title} · {status}</div><div className="mt-1 truncate text-xs text-muted">{detail}</div></div><button className="btn h-9 shrink-0 px-3 text-xs" type="button" onClick={onClick}>{action}<ChevronRight size={14} /></button></div>;
}

function SetupNotice({ status, title, detail }: { status: string; title: string; detail: string }) {
  useLocale();
  const ready = status === "ready";
  return <div className={`rounded-xl border p-4 ${ready ? "border-emerald-200 bg-emerald-50 text-emerald-900" : status === "needs_repair" ? "border-amber-200 bg-amber-50 text-amber-900" : "border-line bg-slate-50 text-slate-800"}`}><div className="flex items-start gap-3">{ready ? <CheckCircle2 size={19} className="mt-0.5 shrink-0" /> : <AlertTriangle size={19} className="mt-0.5 shrink-0" />}<div><div className="text-sm font-semibold">{title}</div><div className="mt-1 text-xs leading-5 opacity-80">{detail}</div></div></div></div>;
}

export function getToolStatus(session: WorkspaceTerminalSession | null): TerminalToolStatus | null {
  const value = session?.metadata?.tool_status;
  if (!value || typeof value !== "object") return null;
  return value as TerminalToolStatus;
}

export function formatComputerConnection(connection: WorkspaceConnection) {
  if (connection.connection_type === "runtime") {
    const platform = connection.runtime?.platform
      ? connection.runtime.platform.replace(/^./, (letter) => letter.toUpperCase())
      : t("Runtime");
    const state = connection.runtime?.online ? t("Online") : connection.runtime ? t("Offline") : t("Pairing");
    const browser = connection.runtime?.browser_name ? ` · ${connection.runtime.browser_name}` : "";
    return `${platform} · ${state}${browser}`;
  }
  return t("Legacy SSH · Disabled");
}
