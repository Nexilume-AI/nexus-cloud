import { t, useLocale } from "../localization";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Monitor,
  RefreshCw,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { api } from "../lib/api";
import type { WorkspaceConnection } from "../lib/types";
import { NexilumeDialog } from "./NexilumeControls";

type ApiContext = Parameters<typeof api.workspaceConnections>[0];

export function AgentComputerAttachDialog({
  open,
  agentId,
  agentName,
  declaredScopes,
  apiContext,
  returnTo,
  onClose,
  onAttached,
  targetRun,
}: {
  open: boolean;
  agentId: string;
  agentName: string;
  declaredScopes: string[];
  apiContext: ApiContext;
  returnTo: string;
  onClose: () => void;
  onAttached: () => void | Promise<void>;
  targetRun?: { id: string; displayToken: string; revision: number; name: string };
}) {
  useLocale();
  const queryClient = useQueryClient();
  const connections = useQuery({
    queryKey: [
      "workspace-connections",
      apiContext.token,
      apiContext.tenantId,
      apiContext.projectId,
    ],
    queryFn: () => api.workspaceConnections(apiContext),
    enabled: open,
  });
  const bindings = useQuery({
    queryKey: [
      "agent-computer-bindings",
      agentId,
      apiContext.token,
      apiContext.tenantId,
      apiContext.projectId,
    ],
    queryFn: () => api.agentComputerBindings(apiContext, agentId),
    enabled: open && Boolean(agentId),
  });
  const grant = useQuery({
    queryKey: [
      "agent-workspace-grant",
      agentId,
      apiContext.token,
      apiContext.tenantId,
      apiContext.projectId,
    ],
    queryFn: () => api.agentWorkspaceGrant(apiContext, agentId),
    enabled: open && Boolean(agentId),
  });
  const effectiveDeclaredScopes = grant.isSuccess
    ? grant.data.declared_scopes
    : declaredScopes;
  const policyReady = grant.isSuccess && !grant.isFetching;
  const authorized =
    policyReady &&
    (effectiveDeclaredScopes.length === 0 ||
      (grant.data.status === "active" &&
        effectiveDeclaredScopes.every((scope) =>
          grant.data.scopes.includes(scope),
        )));
  const runtimeConnections = useMemo(
    () =>
      (connections.data ?? []).filter(
        (connection) => connection.connection_type === "runtime",
      ),
    [connections.data],
  );
  const defaultConnectionId = useMemo(
    () =>
      bindings.data?.find(
        (binding) => binding.is_default && binding.status === "active",
      )?.connection_id ||
      bindings.data?.find((binding) => binding.status === "active")
        ?.connection_id ||
      "",
    [bindings.data],
  );
  const [selectedConnectionId, setSelectedConnectionId] = useState("");
  const [complete, setComplete] = useState(false);

  useEffect(() => {
    if (!open) {
      setComplete(false);
      setSelectedConnectionId("");
      return;
    }
    if (selectedConnectionId) return;
    const preferred = runtimeConnections.find(
      (connection) =>
        connection.id === defaultConnectionId &&
        connection.availability?.available,
    );
    const firstAvailable = runtimeConnections.find(
      (connection) => connection.availability?.available,
    );
    setSelectedConnectionId(preferred?.id || firstAvailable?.id || "");
  }, [defaultConnectionId, open, runtimeConnections, selectedConnectionId]);

  const attach = useMutation({
    mutationFn: async () => {
      if (!runtimeConnections.some((connection) => connection.id === selectedConnectionId && connection.availability?.available))
        throw new Error(t("Select an online Nexus Computer Runtime."));
      if (!policyReady)
        throw new Error(
          t("Wait for the latest Computer policy before attaching."),
        );
      if (!authorized)
        await api.setAgentWorkspaceGrant(
          apiContext,
          agentId,
          effectiveDeclaredScopes,
        );
      if (targetRun) return api.switchPrivateAgentRunComputer(apiContext, targetRun.id,
        targetRun.displayToken, selectedConnectionId, targetRun.revision);
      return api.createAgentComputerBinding(
        apiContext,
        agentId,
        selectedConnectionId,
        true,
      );
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["private-agent-run", apiContext, targetRun?.id] }),
        queryClient.invalidateQueries({ queryKey: ["private-agent-run-computer"] }),
        queryClient.invalidateQueries({ queryKey: ["private-agent-run-outputs"] }),
        queryClient.invalidateQueries({
          queryKey: ["agent-computer-bindings", agentId],
        }),
        queryClient.invalidateQueries({
          queryKey: ["agent-workspace-grant", agentId],
        }),
        queryClient.invalidateQueries({ queryKey: ["workspace-connections"] }),
        queryClient.invalidateQueries({
          queryKey: ["agent-interactor", apiContext, agentId],
        }),
      ]);
      setComplete(true);
      toast.success(targetRun ? t("Computer changed for the next turn in this Run") : t("Default Computer changed for future Runs"));
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Unable to attach Computer"),
      ),
  });
  const selected = runtimeConnections.find(
    (connection) => connection.id === selectedConnectionId,
  );
  const busy = attach.isPending;

  return (
    <NexilumeDialog
      open={open}
      onClose={onClose}
      title={targetRun ? t("Change Computer for this Run") : t("{{0}} default Computer", { 0: defaultConnectionId ? "Change" : "Attach" })}
      eyebrow={t("PRIVATE RUN SETUP")}
      description={targetRun ? t("Replace {{0}} for the next turn. This conversation and historical outputs stay; files and browser sessions are not migrated. The default for new Runs is unchanged.", { 0: targetRun.name || "the current Computer" }) : t("Choose the default Computer for new Runs of {{0}}. Existing Runs are unchanged.", { 0: agentName })}
      busy={busy}
      size="large"
      footer={
        complete ? (
          <button
            type="button"
            className="btn btn-primary min-h-11"
            onClick={() => void onAttached()}
          >{t("Done")}</button>
        ) : (
          <div className="flex w-full justify-between gap-3">
            <button
              type="button"
              className="btn min-h-11"
              onClick={() =>
                void Promise.all([connections.refetch(), grant.refetch(), bindings.refetch()])
              }
              disabled={busy}
            >
              <RefreshCw size={15} />{t("Refresh status")}</button>
            <div className="flex gap-2">
              <button
                type="button"
                className="btn min-h-11"
                onClick={onClose}
                disabled={busy}
              >{t("Cancel")}</button>
              <button
                type="button"
                className="btn btn-primary min-h-11"
                disabled={busy || !selected?.availability?.available || !policyReady || connections.isFetching || connections.isError || bindings.isLoading || bindings.isError}
                onClick={() => attach.mutate()}
              >
                {busy ? (
                  <Loader2 size={15} className="animate-spin" />
                ) : (
                  <Monitor size={15} />
                )}
                {targetRun ? t("Use for next turn") : authorized
                  ? defaultConnectionId
                    ? t("Change Computer")
                    : t("Attach Computer")
                  : t("Allow and attach")}
              </button>
            </div>
          </div>
        )
      }
    >
      {complete ? (
        <section
          className="border border-emerald-200 bg-emerald-50 p-5 text-emerald-950"
          aria-live="polite"
        >
          <div className="flex gap-3">
            <CheckCircle2 size={20} />
            <div>
              <h3 className="font-semibold">{t("Computer ready")}</h3>
              <p className="mt-1 text-sm">
                {selected?.name || t("The selected Computer")}{targetRun ? t("will be used for the next turn of this Run. Your conversation and previous output snapshots remain available.") : t("is the default for future Runs. Existing Runs keep their original binding.")}
              </p>
            </div>
          </div>
        </section>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <section
            aria-labelledby="computer-permission-title"
            className="border border-line bg-paper p-4"
          >
            <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">{t("01 · Permissions")}</div>
            <h3
              id="computer-permission-title"
              className="mt-2 font-semibold text-ink"
            >{t("Allow requested capabilities")}</h3>
            <p className="mt-1 text-sm leading-6 text-muted">{t("The grant belongs to the caller and applies only to this Agent.")}</p>
            <ul className="mt-4 grid gap-2 text-sm text-ink">
              {effectiveDeclaredScopes.map((scope) => (
                <li
                  key={scope}
                  className="flex items-center gap-2 border-t border-line pt-2"
                >
                  <span className="h-2 w-2 rounded-full bg-lume ring-1 ring-ink" />
                  {scope.replaceAll(".", " · ").replaceAll("_", " ")}
                </li>
              ))}
              {!effectiveDeclaredScopes.length && policyReady ? (
                <li>{t("No Computer actions requested.")}</li>
              ) : null}
            </ul>
            {grant.isError ? (
              <div
                role="alert"
                className="mt-4 border border-danger/30 bg-red-50 p-3 text-sm text-danger"
              >{t("The latest Agent Computer policy could not be loaded. Retry before attaching.")}</div>
            ) : null}
            <div className="mt-4 font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
              {grant.isLoading
                ? t("Checking latest policy")
                : grant.isError
                  ? t("Policy unavailable")
                  : authorized
                    ? t("Permission granted")
                    : t("Approval required")}
            </div>
          </section>
          <section aria-labelledby="computer-device-title">
            <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">{t("02 · Device")}</div>
            <h3
              id="computer-device-title"
              className="mt-2 font-semibold text-ink"
            >{t("Choose your Computer")}</h3>
            {connections.isLoading || bindings.isLoading ? (
              <div className="mt-4 flex min-h-28 items-center justify-center border border-line text-sm text-muted">
                <Loader2 className="mr-2 animate-spin" size={16} />{" "}{t("Loading caller Computers")}</div>
            ) : null}
            {connections.isError ? (
              <div
                role="alert"
                className="mt-4 border border-danger/30 bg-red-50 p-3 text-sm text-danger"
              >{t("Computer Runtime connections could not be loaded.")}</div>
            ) : null}
            {bindings.isError ? <p role="alert" className="mt-4 text-danger">{t("The current Computer binding could not be loaded. Refresh status before attaching.")}</p> : null}
            {!connections.isLoading &&
            !connections.isError &&
            !runtimeConnections.length ? (
              <div className="mt-4 border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
                <div className="flex gap-2 font-semibold">
                  <AlertTriangle size={17} />{" "}{t("No paired Computer")}</div>
                <p className="mt-2 leading-6">{t("Pair a caller-owned Computer Runtime in Operate &gt; Computer, then return here.")}</p>
                <Link
                  className="btn mt-3 min-h-11"
                  to={`/remote-workspaces?return=${encodeURIComponent(returnTo)}`}
                  onClick={onClose}
                >{t("Pair Computer")}</Link>
              </div>
            ) : (
              <div
                className="mt-4 grid gap-2"
                role="radiogroup"
                aria-label={t("Caller Computers")}
              >
                {runtimeConnections.map((connection) => (
                  <ComputerChoice
                    key={connection.id}
                    connection={connection}
                    selected={connection.id === selectedConnectionId}
                    onSelect={() => setSelectedConnectionId(connection.id)}
                  />
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </NexilumeDialog>
  );
}

function ComputerChoice({
  connection,
  selected,
  onSelect,
}: {
  connection: WorkspaceConnection;
  selected: boolean;
  onSelect: () => void;
}) {
  useLocale();
  const available = Boolean(connection.availability?.available);
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      disabled={!available}
      onClick={onSelect}
      className={`flex min-h-16 items-center justify-between border px-3 py-3 text-left disabled:cursor-not-allowed disabled:opacity-60 ${selected ? "border-ink bg-lume/10" : "border-line bg-white"}`}
    >
      <span className="min-w-0">
        <span className="flex items-center gap-2 text-sm font-semibold text-ink">
          <span
            className={`h-2.5 w-2.5 rounded-full border ${selected ? "border-ink bg-lume" : "border-muted"}`}
          />
          <span className="truncate">{connection.name}</span>
        </span>
        <span
          className={`mt-1 block pl-[18px] text-xs ${available ? "text-muted" : "text-amber-700"}`}
        >
          {connection.runtime?.platform || t("Computer Runtime")} ·{" "}
          {available ? t("Online") : connection.availability?.message || t("Offline")}
        </span>
      </span>
      <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
        {available ? t("ready") : t("unavailable")}
      </span>
    </button>
  );
}
