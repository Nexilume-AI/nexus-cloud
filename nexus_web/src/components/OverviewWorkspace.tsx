import { useMemo, type ReactNode } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  Bot,
  Inbox,
  MessageSquareText,
  Play,
  RefreshCw,
} from "lucide-react";
import { Link } from "react-router-dom";
import { useAuth } from "../app/AuthContext";
import { useApplicationDistribution } from "../app/distribution";
import { api, ApiError } from "../lib/api";
import { formatDate } from "../lib/format";
import { Badge } from "./Badge";
import { statusTone } from "../lib/status";
import type { Agent, AgentPrivateRun } from "../lib/types";

function restricted(error: unknown) {
  return error instanceof ApiError && [401, 403].includes(error.status);
}

function nextAgentRoute(agent: Agent) {
  const configuring =
    !["archived", "disabled"].includes(
      agent.lifecycle_status || agent.status,
    ) &&
    agent.allowed_actions?.includes("configure_runtime") &&
    (agent.configuration_drift ||
      ["not_deployed", "pending", "unknown", ""].includes(
        agent.runtime_status ?? "",
      ));
  return {
    to: `/agents/${encodeURIComponent(agent.id)}/${configuring ? "runtime" : "overview"}`,
    label: configuring ? "Continue setup" : "Open Agent",
  };
}

function agentIsConfiguring(agent: Agent) {
  return nextAgentRoute(agent).label === "Continue setup";
}

function runAttention(run: AgentPrivateRun) {
  const state = String(run.attention_state || run.status || "").toLowerCase();
  if (state === "input_required" || state === "waiting_for_input") return 0;
  if (["running", "pending", "queued", "resuming"].includes(state)) return 1;
  if (["failed", "error"].includes(state)) return 2;
  if (run.completion_unread || state === "completed_unread") return 3;
  return 99;
}

function runStatusLabel(run: AgentPrivateRun) {
  const priority = runAttention(run);
  if (priority === 0) return "Waiting for your reply";
  if (priority === 1) return "Run in progress";
  if (priority === 2) return "Run needs attention";
  if (priority === 3) return "Result ready";
  return String(run.status || "Run available").replace(/_/g, " ");
}

function runActionLabel(run: AgentPrivateRun) {
  const priority = runAttention(run);
  if (priority === 0) return "Reply and continue";
  if (priority === 1) return "Open live Run";
  if (priority === 2) return "Review and retry";
  return "View result";
}

function privateDisplayRoute(agentId: string, runId?: string) {
  const base = `/agents/${encodeURIComponent(agentId)}/private-display`;
  return runId ? `${base}?run=${encodeURIComponent(runId)}` : base;
}

export function OverviewWorkspace() {
  const discovery = useApplicationDistribution().agentDiscovery;
  const { apiContext, isContextReady } = useAuth();
  const organizationContext = { ...apiContext, projectId: null };
  const inboxSummary = useQuery({
    queryKey: ["private", "work-inbox", apiContext.tenantId, "summary"],
    queryFn: ({ signal }) => api.inboxSummary(organizationContext, signal),
    enabled: isContextReady,
    retry: false,
    refetchInterval: 30000,
  });
  const inboxAttention = useQuery({
    queryKey: ["private", "work-inbox", apiContext.tenantId, "overview-attention"],
    queryFn: ({ signal }) => api.inboxItems(organizationContext, { state: "needs_action" }, signal),
    enabled: isContextReady,
    retry: false,
    refetchInterval: 30000,
  });
  const priorityWork = inboxAttention.data?.results[0];

  const capabilities = useQuery({
    queryKey: [
      "agent-capabilities",
      apiContext.token,
      apiContext.tenantId,
      apiContext.projectId,
    ],
    queryFn: () => api.agentCapabilities(apiContext),
    enabled: isContextReady,
    retry: false,
  });
  const canView = !capabilities.isError && capabilities.data?.view === true;
  const canCreate = !capabilities.isError && capabilities.data?.create === true;
  const agents = useQuery({
    queryKey: ["agents", "overview", apiContext],
    queryFn: ({ signal }) =>
      api.agentsPage(apiContext, { sort: "updated", limit: "3" }, signal),
    enabled: isContextReady && canView,
    retry: false,
    refetchInterval: 30000,
  });
  const recent = agents.data?.items?.slice(0, 3) ?? [];
  const runQueries = useQueries({
    queries: recent.map((agent) => ({
      queryKey: ["overview-private-runs", apiContext, agent.id],
      queryFn: () =>
        api.privateAgentRuns(apiContext, agent.id, "", "", "all", 4),
      enabled: isContextReady && canView,
      retry: false,
      refetchInterval: 30000,
    })),
  });
  const continuation = useMemo(() => {
    const candidates = runQueries.flatMap((query, index) => {
      const agent = recent[index];
      if (!agent) return [];
      return (query.data?.results ?? [])
        .filter((run) => runAttention(run) < 99)
        .map((run) => ({ agent, run }));
    });
    return candidates.sort((left, right) => {
      const priority = runAttention(left.run) - runAttention(right.run);
      if (priority) return priority;
      return String(right.run.updated_at || right.run.started_at).localeCompare(
        String(left.run.updated_at || left.run.started_at),
      );
    })[0];
  }, [recent, runQueries]);

  const checkingRuns =
    recent.length > 0 && runQueries.some((query) => query.isPending);
  const runHistoryUnavailable = runQueries.some((query) => query.isError);
  const empty =
    canView && agents.isSuccess && !agents.isError && recent.length === 0;
  const first = canView && !agents.isError ? recent[0] : undefined;
  const focusStatus = !isContextReady || capabilities.isPending
    ? "Checking workspace"
    : inboxSummary.isError
      ? "Status incomplete"
      : priorityWork
        ? "Needs attention"
    : capabilities.isError
      ? "Access unavailable"
      : !canView
        ? discovery?.restricted.status ?? "Access unavailable"
        : agents.isPending
          ? "Checking workspace"
          : agents.isError
            ? "Agent data unavailable"
            : empty
              ? "First launch"
              : runHistoryUnavailable
                ? "Run data unavailable"
                : checkingRuns
                  ? "Checking private Runs"
                  : continuation
                    ? runStatusLabel(continuation.run)
                    : first && agentIsConfiguring(first)
                      ? "Setup required"
                      : first
                        ? "Ready for a Run"
                        : "Checking workspace";

  return (
    <div className="overview-page overview-home">
      <header className="overview-home__header">
        <div>
          <p className="tech-label">NEXILUME AI / OVERVIEW</p>
          <h1>Your Agent workspace</h1>
          <p>Continue a private Run or bring a new Agent online.</p>
        </div>
        {canCreate && (
          <Link className="btn overview-home__upload" to="/agents?create=1">
            New Agent
            <ArrowRight size={17} />
          </Link>
        )}
      </header>

      <section
        className="overview-focus"
        aria-labelledby="overview-focus-title"
      >
        <div className="overview-focus__rail" aria-hidden="true">
          <span />
        </div>
        <div className="overview-focus__heading">
          <p className="tech-label">CONTINUE YOUR WORK</p>
          <span className="overview-focus__status"><i aria-hidden="true" />{focusStatus}</span>
        </div>

        <div className="overview-focus__body">
          {!isContextReady ? (
            <FocusMessage
              icon={<Bot size={25} />}
              title="Choose an Organization"
              description="Select a workspace scope to see your private Agent work."
            />
          ) : priorityWork ? (
            <FocusMessage
              icon={<Inbox size={25} />}
              eyebrow={priorityWork.ownership === "role" ? "Shared role queue" : "Work Inbox"}
              title={priorityWork.title}
              description={priorityWork.message}
              note={<span>{priorityWork.project_name ?? "Organization-wide"} · {priorityWork.resource_name || priorityWork.category}</span>}
              action={<Link className="btn btn-primary" to={`/inbox?item=${encodeURIComponent(priorityWork.id)}`}>Review first issue <ArrowRight size={17} /></Link>}
            />
          ) : capabilities.isError ? (
            <FocusError
              label={
                restricted(capabilities.error)
                  ? "Agent access is restricted."
                  : "Agent access could not be checked."
              }
              retry={() => void capabilities.refetch()}
            />
          ) : capabilities.isPending ? (
            <FocusMessage
              icon={<Bot size={25} />}
              title="Opening your workspace…"
              description="Nexus is checking the Agent work available to you."
            />
          ) : !canView ? (
            <FocusMessage
              icon={<Bot size={25} />}
              title={discovery?.restricted.title ?? "Agent access unavailable"}
              description={discovery?.restricted.description ?? "Sign in as the configured owner to access this personal instance's Agents."}
              action={
                discovery && <Link className="btn btn-primary" to={discovery.href}>
                  {discovery.primaryLabel}
                  <ArrowRight size={17} />
                </Link>
              }
            />
          ) : agents.isError ? (
            <FocusError
              label={
                restricted(agents.error)
                  ? "Agent management is restricted."
                  : "Your Agents could not be loaded."
              }
              retry={() => void agents.refetch()}
            />
          ) : agents.isPending ? (
            <FocusMessage
              icon={<Bot size={25} />}
              title="Finding your latest work…"
              description="Loading the Agents available in this scope."
            />
          ) : empty ? (
            <FocusMessage
              icon={<Bot size={25} />}
              title={
                canCreate
                  ? "Bring your first Agent online"
                  : "No Agents are visible in this scope"
              }
              description={
                canCreate
                  ? "Upload a Python Agent, keep it private, and open its first Run when it is ready."
                  : discovery?.emptyDescription ?? "Bring an Agent online using the configured owner account."
              }
              note={
                canCreate ? <FirstAgentPath /> : discovery &&
                  <Link className="overview-home__text-link" to={discovery.href}>
                    {discovery.secondaryLabel} <ArrowRight size={15} />
                  </Link>
              }
              action={
                (canCreate || discovery) && <Link
                  className="btn btn-primary"
                  to={canCreate ? "/agents?create=1" : discovery!.href}
                >
                  {canCreate ? "Upload your first Agent" : discovery!.primaryLabel}
                  <ArrowRight size={17} />
                </Link>
              }
              afterAction={canCreate && discovery && <Link className="overview-home__text-link" to={discovery.href}>{discovery.secondaryLabel} <ArrowRight size={15} /></Link>}
            />
          ) : runHistoryUnavailable ? (
            <FocusMessage
              icon={<MessageSquareText size={25} />}
              title="Recent Run status is unavailable"
              description="Nexus will not guess which private task needs your attention while part of your Run history is unavailable."
              action={
                <button
                  className="btn btn-primary"
                  onClick={() =>
                    runQueries.forEach((query) => void query.refetch())
                  }
                >
                  Retry recent Runs
                  <RefreshCw size={17} />
                </button>
              }
            />
          ) : checkingRuns ? (
            <FocusMessage
              icon={<MessageSquareText size={25} />}
              title="Finding your latest work…"
              description="Checking private Runs that belong to you in this scope."
            />
          ) : continuation ? (
            <FocusMessage
              icon={<MessageSquareText size={25} />}
              eyebrow={continuation.agent.name}
              title={
                continuation.run.title ||
                continuation.run.tool_name ||
                "Private Run"
              }
              description={
                continuation.run.preview ||
                "Open the private Run to continue from its current state."
              }
              note={
                <span>
                  {runStatusLabel(continuation.run)} · Updated{" "}
                  {formatDate(
                    continuation.run.updated_at || continuation.run.started_at,
                  )}
                </span>
              }
              action={
                <Link
                  className="btn btn-primary"
                  to={privateDisplayRoute(
                    continuation.agent.id,
                    continuation.run.id,
                  )}
                >
                  {runActionLabel(continuation.run)}
                  <ArrowRight size={17} />
                </Link>
              }
            />
          ) : first ? (
            <FocusMessage
              icon={
                agentIsConfiguring(first) ? (
                  <Bot size={25} />
                ) : (
                  <Play size={25} />
                )
              }
              eyebrow={first.name}
              title={
                agentIsConfiguring(first)
                  ? "Finish preparing this Agent"
                  : "Start a new private Run"
              }
              description={
                agentIsConfiguring(first)
                  ? "Complete Runtime setup and verify the Agent before its first call."
                  : "Nothing currently needs your attention. Start fresh when you are ready."
              }
              action={
                <Link
                  className="btn btn-primary"
                  to={
                    agentIsConfiguring(first)
                      ? nextAgentRoute(first).to
                      : privateDisplayRoute(first.id)
                  }
                >
                  {agentIsConfiguring(first) ? "Continue setup" : "Start a Run"}
                  <ArrowRight size={17} />
                </Link>
              }
            />
          ) : null}
        </div>
      </section>

      {canView && !agents.isError && recent.length > 0 && (
        <section className="overview-home-agents" aria-labelledby="agents-title">
          <div className="overview-home-agents__heading">
            <div>
              <p className="tech-label">YOUR AGENTS</p>
              <h2 id="agents-title">Ready when you are</h2>
            </div>
            <Link to="/agents">
              View all <ArrowRight size={15} />
            </Link>
          </div>
          <ul className="overview-home-agents__list">
            {recent.map((agent) => {
              const target = nextAgentRoute(agent);
              const state = (agent.runtime_status || "unknown")
                .replace(/_/g, " ")
                .replace(/^./, (letter) => letter.toUpperCase());
              return (
                <li key={agent.id}>
                  <span className="overview-agent-mark" aria-hidden="true">
                    <Bot size={20} />
                  </span>
                  <div className="overview-home-agents__identity">
                    <Link to={target.to}>{agent.name}</Link>
                    <span>Updated {formatDate(agent.updated_at)}</span>
                  </div>
                  <Badge tone={statusTone(agent.runtime_status)}>{state}</Badge>
                  <Link
                    className="overview-home-agents__action"
                    to={target.to}
                    aria-label={`${target.label}: ${agent.name}`}
                  >
                    <span>{target.label}</span>
                    <ArrowRight size={16} />
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <footer className="overview-legal">
        <span>© 2026 Nexilume AI LLC. All rights reserved.</span>
        <nav aria-label="Legal">
          <a href="https://nexilume.com/privacy">Privacy Policy</a>
          <a href="https://nexilume.com/terms">Terms of Service</a>
          <a href="mailto:feedback@nexilume.com">Contact us</a>
        </nav>
      </footer>
    </div>
  );
}

function FocusMessage({
  icon,
  eyebrow,
  title,
  description,
  note,
  action,
  afterAction,
}: {
  icon: ReactNode;
  eyebrow?: string;
  title: string;
  description: string;
  note?: ReactNode;
  action?: ReactNode;
  afterAction?: ReactNode;
}) {
  return (
    <div className="overview-focus__message">
      <span className="overview-agent-mark" aria-hidden="true">
        {icon}
      </span>
      <div className="overview-focus__copy">
        {eyebrow && <p className="overview-focus__eyebrow">{eyebrow}</p>}
        <h2 id="overview-focus-title">{title}</h2>
        <p>{description}</p>
        {note && <div className="overview-focus__note">{note}</div>}
      </div>
      {action && <SignalConnector />}
      {action && <div className="overview-focus__action">{action}{afterAction && <div className="overview-focus__secondary">{afterAction}</div>}</div>}
    </div>
  );
}

function SignalConnector() {
  return <svg className="overview-focus__signal" viewBox="0 0 180 58" preserveAspectRatio="none" aria-hidden="true">
    <path d="M4 52H72L114 8H176" />
    <circle cx="4" cy="52" r="3" /><circle className="is-hit" cx="176" cy="8" r="5" />
  </svg>;
}

function FirstAgentPath() {
  return <ol className="overview-first-path" aria-label="First Agent launch path">
    <li className="is-current"><span>01</span><div><strong>Upload Python</strong><small>Choose a tool file and requirements.</small></div></li>
    <li><span>02</span><div><strong>Validate build</strong><small>Available after the source is uploaded.</small></div></li>
    <li><span>03</span><div><strong>Start private Run</strong><small>Available after the Runtime is ready.</small></div></li>
  </ol>;
}

function FocusError({ label, retry }: { label: string; retry: () => void }) {
  return (
    <FocusMessage
      icon={<Bot size={25} />}
      title={label}
      description="The workspace summary has been left unchanged. Retry when the service is available."
      action={
        <button className="btn btn-primary" onClick={retry}>
          Retry
          <RefreshCw size={17} />
        </button>
      }
    />
  );
}
