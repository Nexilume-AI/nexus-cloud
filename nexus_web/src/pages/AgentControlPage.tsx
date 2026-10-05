import { t, useLocale } from "../localization";
import { useAgentQueryRefresh } from "../components/AgentQueryRefresh";
import { useApplicationDistribution } from "../app/distribution";
import { useRunObservabilityPresentation } from "../components/RunPresentation";
import { getAgentSections, getAgentNextAction, hasRuntimeTarget, isAgentRunning, isOpenWrtRuntime, type AgentSection } from "../app/agentOperations";
import { OverviewRow, ResponsiveAgentInspector, SectionHeading, InspectorValue, canAgent, agentOwnershipLabel, humanize, mutationError } from "../components/AgentControlPresentation";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import {
  Link,
  Navigate,
  NavLink,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  Activity,
  AlertCircle,
  ArrowLeft,
  Boxes,
  Check,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Copy,
  FileOutput,
  Gauge,
  KeyRound,
  LockKeyhole,
  Loader2,
  MemoryStick,
  MonitorPlay,
  PackagePlus,
  RefreshCw,
  Rocket,
  Save,
  Server,
  Settings,
  ShieldCheck,
  Smartphone,
  Square,
  Terminal,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "../app/AuthContext";
import { StatusBadge } from "../components/Badge";
import { AgentMcpSetup } from "../components/AgentMcpSetup";
import { AgentPythonSupply } from "../components/AgentPythonSupply";
import { AgentRuntimeImages } from "../components/AgentRuntimeImages";
import { ObservabilityPaging, useObservabilityPage } from "../components/ObservabilityPaging";
import { AgentComputerAttachDialog } from "../components/AgentComputerAttachDialog";
import { AgentMobileAttachDialog } from "../components/AgentMobileAttachDialog";
import { agentDisplayEntryUrl } from "../lib/agentDisplayNavigation";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Form";
import { FilePicker } from "../components/FilePicker";
import { NexilumeDialog, NexilumeTabs } from "../components/NexilumeControls";
import { AgentPublicationSummary, AgentPublicationWorkspace } from "../components/ResourcePublishing";
import { api } from "../lib/api";
import { compactId, formatDate, formatNumber } from "../lib/format";
import type {
  Agent,
  AgentDisplayEvent,
  AgentDisplayRun,
  AgentLog,
  AgentMemoryItem,
  AgentMcpExport,
  AgentOutputArtifact,
  AgentRuntimeDeployment,
  AgentVersion,
  Job,
} from "../lib/types";

type ApiContextValue = ReturnType<typeof useAuth>["apiContext"];


const workspaceCapabilityGroups = [
  {
    id: "connections",
    label: "Connection management",
    detail:
      "Discover, create, test, and bind caller-owned Computer connections.",
    scopes: [
      "connection.list",
      "connection.create",
      "connection.update",
      "connection.delete",
      "connection.test",
      "connection.bind",
    ],
  },
  {
    id: "files",
    label: "Workspace files",
    detail:
      "List, read, and write files inside the caller-authorized workspace root.",
    scopes: ["files.list", "files.read", "files.write"],
  },
  {
    id: "terminal",
    label: "Terminal execution",
    detail:
      "Execute commands through the per-Run, caller-owned terminal broker.",
    scopes: ["command.execute"],
  },
  {
    id: "browser",
    label: "Isolated browser",
    detail:
      "Control a clean Chrome or Edge profile on the caller's Attached Computer.",
    scopes: ["browser.control"],
  },
] as const;

const workspaceScopeLabels: Record<string, string> = {
  "connection.list": "List connections",
  "connection.create": "Create SSH connections",
  "connection.update": "Update SSH connections",
  "connection.delete": "Delete SSH connections",
  "connection.test": "Test SSH connections",
  "connection.bind": "Bind a Computer",
  "files.list": "List files",
  "files.read": "Read files",
  "files.write": "Write files",
  "command.execute": "Execute terminal commands",
  "browser.control": "Control an isolated browser",
};

const mobileScopeLabels: Record<string, string> = {
  "mobile.observe": "Observe the accessibility state",
  "mobile.screen.capture": "Capture the current screen",
  "mobile.tap": "Tap text or coordinates",
  "mobile.type_text": "Type text",
  "mobile.swipe": "Swipe the screen",
  "mobile.press_back": "Use Android Back",
  "mobile.open_app": "Open an installed app",
  "mobile.wait_for_state": "Wait for a visible state",
};

export function AgentControlPage() {
  useLocale();
  const agentSections = getAgentSections(useApplicationDistribution().resourcePublishing?.agentPolicy);
  const { agentId = "", section } = useParams();
  const { apiContext, isContextReady, projects } = useAuth();
  const queryClient = useQueryClient();
  const activeSection = section as AgentSection | undefined;
  const validSection =
    activeSection &&
    agentSections.map((item) => item.value).includes(activeSection);

  const agentQuery = useQuery({
    queryKey: [
      "agent",
      apiContext.token,
      apiContext.tenantId,
      apiContext.projectId,
      agentId,
    ],
    queryFn: () => api.agent(apiContext, agentId),
    enabled: isContextReady && Boolean(agentId),
  });

  if (!section) return <Navigate to={`/agents/${agentId}/overview`} replace />;
  if (!validSection)
    return <Navigate to={`/agents/${agentId}/overview`} replace />;

  if (agentQuery.isLoading || !isContextReady) return <AgentControlSkeleton />;
  if (agentQuery.isError || !agentQuery.data) {
    return (
      <section className="agent-control-unavailable">
        <AgentHexNode label={t("Unavailable")} tone="muted" />
        <p className="agent-control-eyebrow">{t("Agent control")}</p>
        <h1>{t("Agent unavailable")}</h1>
        <p>{t("This Agent could not be found in the selected Organization, or you do not have permission to manage it.")}</p>
        <Link className="btn btn-primary" to="/agents">
          <ArrowLeft size={15} />{t("Back to Agents")}</Link>
      </section>
    );
  }

  const agent = agentQuery.data;
  const projectName = agent.project_id
    ? projects.find((project) => project.id === agent.project_id)?.name ||
      "Project"
    : "All projects";
  const activeSectionLabel =
    agentSections.find((item) => item.value === activeSection)?.label ??
    t("Overview");

  return (
    <div className="agent-control-shell">
      <header className="agent-control-header">
        <div className="agent-control-identity">
          <Link
            className="agent-control-back"
            to="/agents"
            aria-label={t("Back to Agents")}
          >
            <ArrowLeft size={16} />
          </Link>
          <AgentHexNode
            label={agent.name}
            tone={isAgentRunning(agent) ? "active" : "muted"}
          />
          <div className="min-w-0">
            <p className="agent-control-eyebrow">{t("Agent command surface")}</p>
            <h1 id="agent-control-title">{agent.name || t("Unnamed agent")}</h1>
            <p className="agent-control-meta">
              <span>{projectName}</span>
              <span>{compactId(agent.id)}</span>
            </p>
          </div>
        </div>
      </header>

      <AgentStatusRail agent={agent} />
      <AgentSectionNav
        agentId={agent.id}
        activeSection={activeSection ?? "overview"}
      />

      <section
        className="agent-control-main"
        aria-labelledby="agent-control-section-title"
      >
        <h2 id="agent-control-section-title" className="sr-only">
          {agent.name || t("Agent")} · {activeSectionLabel}
        </h2>
        {activeSection === "overview" && (
          <AgentOverview agent={agent} apiContext={apiContext} />
        )}
        {activeSection === "runtime" && (
          <AgentRuntime
            agent={agent}
            apiContext={apiContext}
            queryClient={queryClient}
          />
        )}
        {activeSection === "access" && (
          <AgentAccess
            agent={agent}
            apiContext={apiContext}
            queryClient={queryClient}
          />
        )}
        {activeSection === "observability" && (
          <AgentObservability agent={agent} apiContext={apiContext} />
        )}
        {activeSection === "publish" && (
          <AgentPublish
            agent={agent}
            apiContext={apiContext}
            queryClient={queryClient}
          />
        )}
        {activeSection === "settings" && (
          <AgentSettings
            agent={agent}
            apiContext={apiContext}
            queryClient={queryClient}
          />
        )}
      </section>
    </div>
  );
}

function AgentSectionNav({
  agentId,
  activeSection,
}: {
  agentId: string;
  activeSection: AgentSection;
}) {
  useLocale();
  const agentSections = getAgentSections(useApplicationDistribution().resourcePublishing?.agentPolicy);
  const [pickerOpen, setPickerOpen] = useState(false);
  const activeLabel =
    agentSections.find((item) => item.value === activeSection)?.label ??
    t("Overview");
  return (
    <>
      <nav className="agent-section-nav" aria-label={t("Agent sections")}>
        <div className="agent-section-nav__rail">
          {agentSections
            .filter((item) => item.value !== "settings")
            .map((item) => (
              <NavLink
                key={item.value}
                to={`/agents/${agentId}/${item.value}`}
                className={({ isActive }) => (isActive ? "is-active" : "")}
              >
                {t(item.label)}
              </NavLink>
            ))}
        </div>
        <NavLink
          className={({ isActive }) =>
            `agent-section-settings ${isActive ? "is-active" : ""}`
          }
          to={`/agents/${agentId}/settings`}
        >
          <Settings size={15} />{t("Settings")}</NavLink>
      </nav>
      <div className="agent-section-picker">
        <button
          type="button"
          aria-haspopup="dialog"
          aria-expanded={pickerOpen}
          onClick={() => setPickerOpen(true)}
        >
          <span>
            <small>{t("Agent section")}</small>
            <strong>{activeLabel}</strong>
          </span>
          <ChevronDown size={17} aria-hidden="true" />
        </button>
      </div>
      <NexilumeDialog
        open={pickerOpen}
        eyebrow={t("Agent command surface")}
        title={t("Choose a section")}
        description={t("Move between stable Agent management areas without losing this Agent context.")}
        onClose={() => setPickerOpen(false)}
      >
        <nav
          className="agent-section-picker__options"
          aria-label={t("Choose Agent section")}
        >
          {agentSections.map((item, index) => (
            <NavLink
              key={item.value}
              to={`/agents/${agentId}/${item.value}`}
              aria-current={item.value === activeSection ? "page" : undefined}
              onClick={() => setPickerOpen(false)}
            >
              <span>0{index + 1}</span>
              <strong>{t(item.label)}</strong>
              {item.value === activeSection ? (
                <em>{t("Current")}</em>
              ) : (
                <ChevronRight size={16} />
              )}
            </NavLink>
          ))}
        </nav>
      </NexilumeDialog>
    </>
  );
}

function AgentStatusRail({ agent }: { agent: Agent }) {
  useLocale();
  const publication = useApplicationDistribution().resourcePublishing?.agentPolicy.status(agent);
  const lifecycle = agentLifecycle(agent);
  const statuses = [
    { label: t("Lifecycle"), value: lifecycle.label, tone: lifecycle.tone },
    {
      label: t("Runtime"),
      value: runtimeLabel(agent),
      tone: isAgentRunning(agent) ? "success" : "muted",
    },
    {
      label: t("Health"),
      value: humanize(agent.runtime_health_status || "Not checked"),
      tone: healthTone(agent.runtime_health_status),
    },
    {
      label: t("Ownership"),
      value: agentOwnershipLabel(agent),
      tone: "info",
    },
    ...(publication ? [publication] : []),
  ] as const;
  return (
    <>
      <div className="agent-status-rail" aria-label={t("Agent status summary")}>
        {statuses.map((status, index) => (
          <div className="agent-status-rail__item" key={status.label}>
            <span className="agent-status-rail__index">0{index + 1}</span>
            <span>
              <small>{status.label}</small>
              <strong>{status.value}</strong>
            </span>
            <span
              className={`agent-status-dot agent-status-dot--${status.tone}`}
              aria-hidden="true"
            />
          </div>
        ))}
      </div>
      <details className="agent-status-mobile">
        <summary>
          <span>
            <small>{t("Status summary")}</small>
            <strong>
              {lifecycle.label} ·{" "}
              {humanize(agent.runtime_health_status || "Not checked")}
            </strong>
          </span>
          <span>{t("View all")}{" "}<ChevronDown size={15} aria-hidden="true" />
          </span>
        </summary>
        <div>
          {statuses.map((status) => (
            <p key={status.label}>
              <small>{status.label}</small>
              <strong>{status.value}</strong>
              <span
                className={`agent-status-dot agent-status-dot--${status.tone}`}
                aria-hidden="true"
              />
            </p>
          ))}
        </div>
      </details>
    </>
  );
}

function AgentOverview({
  agent,
  apiContext,
}: {
  agent: Agent;
  apiContext: ApiContextValue;
}) {
  useLocale();
  const [contextOpen, setContextOpen] = useState(false);
  const runs = useQuery({
    queryKey: [
      "agent-display-runs",
      apiContext.token,
      apiContext.tenantId,
      agent.id,
    ],
    queryFn: () => api.agentDisplayRuns(apiContext, agent.id),
  });
  const latestRun = runs.data?.[0];
  const nextAction = getAgentNextAction(agent, useApplicationDistribution().resourcePublishing?.agentPolicy);

  return (
    <>
      <div className="agent-workspace-layout">
        <section className="agent-workspace-primary">
          <div className="agent-overview-priority">
            <span>
              <small>{t("Current priority")}</small>
              <strong>{nextAction.label}</strong>
              <p>{nextAction.description}</p>
            </span>
            <div>
              <Link
                className="btn btn-primary"
                to={`/agents/${agent.id}/${nextAction.section}`}
              >{t("Continue")}<ChevronRight size={15} />
              </Link>
              <button
                className="btn"
                type="button"
                onClick={() => setContextOpen(true)}
              >{t("View context")}</button>
            </div>
          </div>
          <SectionHeading
            eyebrow={t("Control overview")}
            title={t("Operational context")}
            description={t("Open the runtime, access, observability, or distribution object that needs work.")}
          />
          <div className="agent-overview-rows">
            <OverviewRow
              icon={<Server size={17} />}
              title={t("Runtime")}
              value={runtimeLabel(agent)}
              detail={
                agent.current_image_ref ||
                agent.edge_router_id ||
                t("No runtime target")
              }
              to={`/agents/${agent.id}/runtime`}
            />
            <OverviewRow
              icon={<KeyRound size={17} />}
              title={t("Ownership")}
              value={agentOwnershipLabel(agent)}
              detail={t("Roles and explicit grants control who can use this Agent")}
              to={`/agents/${agent.id}/access`}
            />
            <OverviewRow
              icon={<Activity size={17} />}
              title={t("Latest invocation")}
              value={latestRun ? humanize(latestRun.status) : "No Runs yet"}
              detail={
                latestRun
                  ? `${latestRun.caller} · ${formatDate(latestRun.started_at)}`
                  : t("Run history will appear after the first tools/call.")
              }
              to={`/agents/${agent.id}/observability`}
            />
            <AgentPublicationSummary surface="control-overview" agent={agent} />
          </div>
        </section>
        <AgentPriorityInspector
          agent={agent}
          nextAction={nextAction}
          callsLoading={runs.isLoading}
          calls={runs.data?.length || 0}
          mode="desktop"
        />
      </div>
      <NexilumeDialog
        open={contextOpen}
        variant="drawer"
        eyebrow={t("Agent command surface")}
        title={t("Agent context")}
        description={t("Current priority and complete operational identity.")}
        onClose={() => setContextOpen(false)}
      >
        <AgentPriorityInspector
          agent={agent}
          nextAction={nextAction}
          callsLoading={runs.isLoading}
          calls={runs.data?.length || 0}
          mode="drawer"
        />
      </NexilumeDialog>
    </>
  );
}

function AgentPriorityInspector({
  agent,
  nextAction,
  callsLoading,
  calls,
  mode,
}: {
  agent: Agent;
  nextAction: ReturnType<typeof getAgentNextAction>;
  callsLoading: boolean;
  calls: number;
  mode: "desktop" | "drawer";
}) {
  useLocale();
  const runtimeReference =
    agent.deployed_image_ref ||
    agent.current_image_ref ||
    agent.edge_router_id ||
    "Not configured";
  return (
    <aside
      className={`agent-inspector agent-inspector--${mode}`}
      aria-label={t("Current Agent priority")}
    >
      <p className="agent-inspector__eyebrow">{t("Inspector · Current priority")}</p>
      <h2>{nextAction.label}</h2>
      <p>{nextAction.description}</p>
      <Link
        className="btn btn-primary"
        to={`/agents/${agent.id}/${nextAction.section}`}
      >
        {nextAction.label}
        <ChevronRight size={15} />
      </Link>
      <dl>
        <InspectorValue
          label={t("Runtime target")}
          value={
            agent.runtime_kind ? humanize(agent.runtime_kind) : "Not selected"
          }
        />
        <InspectorValue
          label={t("Health")}
          value={humanize(agent.runtime_health_status || "Not checked")}
        />
        <InspectorValue
          label={t("Calls recorded")}
          value={callsLoading ? "Loading" : formatNumber(calls)}
        />
        <InspectorValue label={t("Updated")} value={formatDate(agent.updated_at)} />
      </dl>
      <InspectorCopyValue label={t("Agent ID")} value={agent.id} />
      <InspectorCopyValue
        label={t("Runtime reference")}
        value={runtimeReference}
        disabled={runtimeReference === "Not configured"}
      />
    </aside>
  );
}

function InspectorCopyValue({
  label,
  value,
  disabled = false,
}: {
  label: string;
  value: string;
  disabled?: boolean;
}) {
  useLocale();
  return (
    <div className="agent-inspector__copy">
      <span>
        <small>{label}</small>
        <code title={value}>{value}</code>
      </span>
      <button
        type="button"
        aria-label={t("Copy {{0}}", { 0: label })}
        disabled={disabled}
        onClick={() => void copyText(value)}
      >
        <Copy size={14} />
      </button>
    </div>
  );
}


function AgentRuntime({
  agent,
  apiContext,
  queryClient,
}: {
  agent: Agent;
  apiContext: ApiContextValue;
  queryClient: QueryClient;
}) {
  useLocale();
  const refreshAgentData = useAgentQueryRefresh("control");
  const location = useLocation();
  const [target, setTarget] = useState<"docker" | "openwrt">(
    isOpenWrtRuntime(agent.runtime_kind) ? "openwrt" : "docker",
  );
  const [showImageTask, setShowImageTask] = useState(false);
  const [showStopConfirm, setShowStopConfirm] = useState(false);
  const [resources, setResources] = useState({ cpu: "1", memory: "512Mi" });
  const [activeJobId, setActiveJobId] = useState("");

  useEffect(
    () =>
      setTarget(isOpenWrtRuntime(agent.runtime_kind) ? "openwrt" : "docker"),
    [agent.id, agent.runtime_kind],
  );

  const runtime = useQuery({
    queryKey: [
      "agent-runtime-status",
      apiContext.token,
      apiContext.tenantId,
      agent.id,
    ],
    queryFn: () => api.agentRuntimeStatus(apiContext, agent.id),
    refetchInterval: (query) => activeJobId || query.state.data?.deployments?.some(item => item.status === "deploying") ? 1500 : false,
  });
  const job = useQuery<Job>({
    queryKey: ["job", apiContext.token, activeJobId],
    queryFn: () => api.job(apiContext, activeJobId),
    enabled: Boolean(activeJobId),
    refetchInterval: (query) =>
      ["succeeded", "failed", "canceled"].includes(
        query.state.data?.status || "",
      )
        ? false
        : 1500,
  });
  const currentDeployment =
    (runtime.data?.deployments ?? []).find((item) => item.env === "prod") ??
    null;
  const images = runtime.data?.images ?? [];
  const operationBusy = Boolean(
    job.data && ["queued", "running"].includes(job.data.status),
  );
  const currentStrategy = currentDeployment
    ? isOpenWrtRuntime(currentDeployment.runtime_kind)
      ? "openwrt"
      : "docker"
    : null;
  const strategyConflict = Boolean(
    currentStrategy &&
    currentStrategy !== target &&
    ["active", "deploying"].includes(currentDeployment?.status || ""),
  );
  const canConfigure = canAgent(agent, "configure_runtime");

  useEffect(() => {
    if (
      !job.data ||
      !["succeeded", "failed", "canceled"].includes(job.data.status)
    )
      return;
    void refreshAgentData(queryClient, agent.id);
  }, [agent.id, job.data?.status, queryClient]);

  const deploy = useMutation({
    mutationFn: () =>
      api.deployAgentRuntime(apiContext, agent.id, { env: "prod" }),
    onSuccess: async (result) => {
      setActiveJobId(result.job_id || "");
      toast.success(t("Runtime deployment queued"));
      await refreshAgentData(queryClient, agent.id);
    },
    onError: mutationError("Failed to deploy runtime"),
  });
  const health = useMutation({
    mutationFn: () =>
      api.healthCheckAgentRuntime(apiContext, agent.id, { env: "prod" }),
    onSuccess: async (result) => {
      setActiveJobId(result.job_id || "");
      toast.success(t("Health check queued"));
      await refreshAgentData(queryClient, agent.id);
    },
    onError: mutationError("Failed to run health check"),
  });
  const stop = useMutation({
    mutationFn: () =>
      isOpenWrtRuntime(currentDeployment?.runtime_kind)
        ? api.disconnectAgentEdgeRuntime(apiContext, agent.id, "prod")
        : api.stopAgentRuntime(apiContext, agent.id, { env: "prod" }),
    onSuccess: async (result) => {
      setActiveJobId(result.job_id || "");
      setShowStopConfirm(false);
      toast.success(
        isOpenWrtRuntime(result.runtime_kind)
          ? t("OpenWrt runtime disconnected")
          : t("Runtime stop queued"),
      );
      await refreshAgentData(queryClient, agent.id);
    },
    onError: mutationError("Failed to stop runtime"),
  });
  const saveResources = useMutation({
    mutationFn: () => api.setAgentResources(apiContext, agent.id, resources),
    onSuccess: () => toast.success(t("Runtime limits saved")),
    onError: mutationError("Failed to save runtime limits"),
  });

  return (
    <div className="agent-workspace-layout">
      <section className="agent-workspace-primary">
        <SectionHeading
          eyebrow={t("Runtime")}
          title={t("Choose where this Agent runs")}
          description={t("Nexus Container and OpenWrt are separate runtime strategies. Only the selected strategy is shown.")}
        />
        {location.search.includes("setup=1") && (
          <InlineNotice
            tone="lume"
            title={t("Agent created — choose a runtime")}
            detail={t("Configure a runtime target now, or return later while the Agent remains a draft.")}
          />
        )}
        {job.data && <JobBanner job={job.data} />}

        <div
          className="agent-choice-switch"
          role="group"
          aria-label={t("Runtime strategy")}
        >
          <button
            type="button"
            className={target === "docker" ? "is-active" : ""}
            onClick={() => setTarget("docker")}
          >
            <Boxes size={18} />
            <span>
              <strong>{t("Nexus Container")}</strong>
              <small>{t("Python file, registry image or Docker archive")}</small>
            </span>
          </button>
          <button
            type="button"
            className={target === "openwrt" ? "is-active" : ""}
            onClick={() => setTarget("openwrt")}
          >
            <Server size={18} />
            <span>
              <strong>{t("OpenWrt Agent")}</strong>
              <small>{t("Direct IPv6 or outbound Relay")}</small>
            </span>
          </button>
        </div>

        {target === "docker" ? (
          <div className="agent-control-section">
            {canConfigure && <AgentPythonSupply agentId={agent.id} apiContext={apiContext}
              deployedImageId={currentDeployment?.status === "active" ? currentDeployment.image_id ?? undefined : undefined}
              onChanged={() => refreshAgentData(queryClient, agent.id)} onDeployment={setActiveJobId} />}
            <div className="agent-control-section__header">
              <div>
                <p className="agent-control-eyebrow">{t("Container supply")}</p>
                <h2>{t("Runtime images")}</h2>
                <p>{t("Register a tagged image or upload an archive produced by Docker save.")}</p>
              </div>
              {canAgent(agent, "configure_runtime") && (
                <button
                  className="btn"
                  onClick={() => setShowImageTask(true)}
                >
                  <PackagePlus size={15} />{t("Add image")}</button>
              )}
            </div>
            {runtime.isLoading ? (
              <InlineLoading label={t("Loading runtime images")} />
            ) : images.length === 0 ? (
              <EmptyState
                title={t("No runtime image")}
                description={t("Add a registry reference or upload a Docker image archive to continue.")}
                action={
                  canAgent(agent, "configure_runtime") ? (
                    <button
                      className="btn"
                      onClick={() => setShowImageTask(true)}
                    >{t("Add image")}</button>
                  ) : undefined
                }
              />
            ) : (
              <AgentRuntimeImages agentId={agent.id} apiContext={apiContext} images={images}
                currentImageId={agent.current_image_id} canManage={canConfigure}
                onChanged={() => refreshAgentData(queryClient, agent.id)} />
            )}
          </div>
        ) : (
          <div className="agent-control-section">
            <div className="agent-control-section__header">
              <div>
                <p className="agent-control-eyebrow">{t("User-hosted runtime")}</p>
                <h2>{t("OpenWrt connection")}</h2>
                <p>{t("The router owns the process. Nexus verifies the authenticated IPv6 or Relay path.")}</p>
              </div>
              {isOpenWrtRuntime(agent.runtime_kind) && (
                <StatusBadge
                  status={agent.runtime_health_status || agent.runtime_status}
                />
              )}
            </div>
            {isOpenWrtRuntime(agent.runtime_kind) ? (
              <div className="agent-detail-grid">
                <InspectorValue
                  label={t("Router")}
                  value={agent.edge_router_id || "OpenWrt"}
                />
                <InspectorValue
                  label={t("Transport")}
                  value={
                    agent.runtime_kind === "openwrt_relay"
                      ? "Outbound Relay"
                      : "Direct IPv6"
                  }
                />
                <InspectorValue
                  label={t("Health")}
                  value={humanize(agent.runtime_health_status || "Unknown")}
                />
              </div>
            ) : (
              <div className="agent-edge-setup">
                <InlineNotice
                  tone="lume"
                  title={t("OpenWrt managed provisioning")}
                  detail={t("Eligible Agents are created and connected automatically when the current Nexus Connector publishes its managed manifest. Manual binding from an older connector is no longer part of this workflow.")}
                />
                <div className="agent-pairing-code">
                  <div>
                    <strong>{t("OpenWrt Router inventory")}</strong>
                    <small>{t("Connect or upgrade the Router, then refresh Agents after its next sync.")}</small>
                  </div>
                  <Link className="btn" to="/openwrt-routers">{t("Manage OpenWrt Routers")}<ChevronRight size={14} />
                  </Link>
                </div>
              </div>
            )}
          </div>
        )}

        {strategyConflict && (
          <InlineNotice
            tone="warning"
            title={t("{{0}} is currently active", { 0: currentStrategy === "openwrt" ? "OpenWrt Agent" : "Nexus Container" })}
            detail={t("Stop or disconnect the current production Runtime before switching to {{0}}.", { 0: target === "openwrt" ? "OpenWrt Agent" : "Nexus Container" })}
          />
        )}

        <div className="agent-control-section">
          <div className="agent-control-section__header">
            <div>
              <p className="agent-control-eyebrow">{t("Production operation")}</p>
              <h2>{t("Deployment control")}</h2>
              <p>{t("Runtime operations remain visible here while Nexus tracks their job state.")}</p>
            </div>
            <div className="agent-action-cluster">
              {target === "docker" && canAgent(agent, "deploy") && (
                <button
                  className="btn btn-primary"
                  onClick={() => deploy.mutate()}
                  disabled={
                    !agent.current_image_id ||
                    deploy.isPending ||
                    operationBusy ||
                    strategyConflict
                  }
                >
                  <Rocket size={15} />
                  {agent.configuration_drift
                    ? t("Deploy update")
                    : currentDeployment?.status === "active"
                      ? t("Redeploy")
                      : t("Deploy")}
                </button>
              )}
              {canAgent(agent, "health_check") && (
                <button
                  className="btn"
                  onClick={() => health.mutate()}
                  disabled={
                    !currentDeployment || health.isPending || operationBusy
                  }
                >
                  <RefreshCw size={15} />{t("Health check")}</button>
              )}
              {canAgent(agent, "stop") && (
                <button
                  className="btn"
                  onClick={() => setShowStopConfirm(true)}
                  disabled={
                    !currentDeployment ||
                    !["active", "deploying"].includes(
                      currentDeployment.status,
                    ) ||
                    operationBusy
                  }
                >
                  <Square size={14} />
                  {isOpenWrtRuntime(currentDeployment?.runtime_kind)
                    ? t("Disconnect")
                    : t("Stop")}
                </button>
              )}
            </div>
          </div>
          {currentDeployment ? (
            <DeploymentRow deployment={currentDeployment} />
          ) : (
            <InlineNotice
              title={t("No production deployment")}
              detail={t("Configure a runtime target, then deploy it to production.")}
            />
          )}
        </div>

        {target === "docker" && (
          <div className="agent-control-section">
            <div className="agent-control-section__header">
              <div>
                <p className="agent-control-eyebrow">{t("Compute profile")}</p>
                <h2>{t("Runtime limits")}</h2>
                <p>{t("Set the CPU and memory requested by the Nexus container runtime.")}</p>
              </div>
            </div>
            <div className="agent-inline-form">
              <Field label={t("CPU")}>
                <input
                  className="input"
                  value={resources.cpu}
                  disabled={!canConfigure}
                  onChange={(event) =>
                    setResources((current) => ({
                      ...current,
                      cpu: event.target.value,
                    }))
                  }
                />
              </Field>
              <Field label={t("Memory")}>
                <input
                  className="input"
                  value={resources.memory}
                  disabled={!canConfigure}
                  onChange={(event) =>
                    setResources((current) => ({
                      ...current,
                      memory: event.target.value,
                    }))
                  }
                />
              </Field>
              {canConfigure && (
                <button
                  className="btn"
                  onClick={() => saveResources.mutate()}
                  disabled={saveResources.isPending}
                >
                  <Save size={15} />{t("Save limits")}</button>
              )}
            </div>
          </div>
        )}
      </section>

      <ResponsiveAgentInspector
        eyebrow={t("Runtime")}
        title={target === "docker" ? t("Nexus Container") : t("OpenWrt Agent")}
        description={
          target === "docker"
            ? t("Nexus manages deployment without receiving a caller Computer or SSH credentials.")
            : t("The user-hosted router remains the runtime authority.")
        }
        action={
          <Link className="btn" to="/remote-workspaces">
            <Terminal size={15} />{t("My Computers")}</Link>
        }
      >
        <InspectorValue
          label={t("Current target")}
          value={
            target === "docker"
              ? agent.current_image_ref || "Not configured"
              : agent.edge_router_id || "Not bound"
          }
        />
        <InspectorValue
          label={t("Deployment")}
          value={humanize(currentDeployment?.status || "Not deployed")}
        />
        <InspectorValue
          label={t("Health")}
          value={humanize(currentDeployment?.health_status || "Not checked")}
        />
        <InspectorValue label={t("Computer")} value="Caller scoped" />
      </ResponsiveAgentInspector>

      <RuntimeImageDialog
        open={showImageTask}
        agent={agent}
        apiContext={apiContext}
        queryClient={queryClient}
        onClose={() => setShowImageTask(false)}
      />
      <ConfirmDialog
        open={showStopConfirm}
        title={
          isOpenWrtRuntime(currentDeployment?.runtime_kind)
            ? t("Disconnect OpenWrt runtime?")
            : t("Stop production runtime?")
        }
        description={t("This interrupts new Agent calls. Existing Run records and outputs remain available.")}
        confirmLabel={
          isOpenWrtRuntime(currentDeployment?.runtime_kind)
            ? t("Disconnect")
            : t("Stop runtime")
        }
        busy={stop.isPending}
        destructive
        onConfirm={() => stop.mutate()}
        onClose={() => setShowStopConfirm(false)}
      />
    </div>
  );
}

function RuntimeImageDialog({
  open,
  agent,
  apiContext,
  queryClient,
  onClose,
}: {
  open: boolean;
  agent: Agent;
  apiContext: ApiContextValue;
  queryClient: QueryClient;
  onClose: () => void;
}) {
  useLocale();
  const refreshAgentData = useAgentQueryRefresh("control");
  const [mode, setMode] = useState<"reference" | "upload">("reference");
  const [imageRef, setImageRef] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (open) {
      setMode("reference");
      setImageRef("");
      setFile(null);
    }
  }, [open]);
  const submit = useMutation({
    mutationFn: async () => {
      const image =
        mode === "reference"
          ? await api.registerAgentRuntimeImage(apiContext, agent.id, {
              image_ref: imageRef.trim(),
            })
          : await api.uploadAgentRuntimeImage(
              apiContext,
              agent.id,
              file!,
              imageRef.trim(),
            );
      await api.setCurrentAgentRuntimeImage(apiContext, agent.id, image.id);
      return image;
    },
    onSuccess: async () => {
      toast.success(t("Runtime image added"));
      await refreshAgentData(queryClient, agent.id);
      onClose();
    },
    onError: mutationError("Failed to add runtime image"),
  });
  const valid = mode === "reference" ? Boolean(imageRef.trim()) : Boolean(file);
  return (
    <NexilumeDialog
      open={open}
      title={t("Add runtime image")}
      eyebrow={t("Runtime task")}
      description={t("Choose one image source. The result becomes the current runtime image.")}
      busy={submit.isPending}
      onClose={onClose}
      initialFocusRef={inputRef}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={submit.isPending}>{t("Cancel")}</button>
          <button
            className="btn btn-primary"
            onClick={() => submit.mutate()}
            disabled={!valid || submit.isPending}
          >
            {submit.isPending && <Loader2 size={15} className="animate-spin" />}{t("Add image")}</button>
        </>
      }
    >
      <NexilumeTabs
        label={t("Image source")}
        value={mode}
        onChange={setMode}
        variant="compact"
        options={[
          { value: "reference", label: t("Registry reference") },
          { value: "upload", label: t("Docker archive") },
        ]}
      />
      <div className="mt-5 grid gap-4" role="tabpanel">
        <Field
          label={
            mode === "reference"
              ? t("Image reference")
              : t("Optional image reference")
          }
          hint={
            mode === "reference"
              ? t("Use an explicit tag or digest, for example registry.example/agent:1.2.0.")
              : t("Nexus can infer this from the loaded archive.")
          }
        >
          <input
            ref={inputRef}
            className="input"
            value={imageRef}
            onChange={(event) => setImageRef(event.target.value)}
            placeholder={t("registry.example/agent:1.2.0")}
          />
        </Field>
        {mode === "upload" && (
          <FilePicker
            label={t("Docker image tar")}
            hint={t("Select an archive produced by docker save.")}
            accept=".tar,application/x-tar"
            disabled={submit.isPending}
            files={file ? [file] : []}
            onFilesChange={(files) => setFile(files[0] || null)}
          />
        )}
      </div>
    </NexilumeDialog>
  );
}

function AgentAccess({
  agent,
  apiContext,
  queryClient,
}: {
  agent: Agent;
  apiContext: ApiContextValue;
  queryClient: QueryClient;
}) {
  useLocale();
  const [tab, setTab] = useState<"api" | "computer" | "mobile">("api");
  return (
    <div className="agent-workspace-layout">
      <section className="agent-workspace-primary">
        <SectionHeading
          eyebrow={t("Access")}
          title={t("Choose how callers reach this Agent")}
          description={t("API credentials, caller-owned Computer permissions, and Mobile bindings are managed separately.")}
        />
        <NexilumeTabs
          label={t("Agent access channels")}
          value={tab}
          onChange={setTab}
          options={[
            { value: "api", label: t("API & MCP"), eyebrow: "01" },
            { value: "computer", label: t("Caller Computer"), eyebrow: "02" },
            { value: "mobile", label: t("Mobile"), eyebrow: "03" },
          ]}
        />
        <div className="agent-tab-panel" role="tabpanel">
          {tab === "api" && (
            <AgentApiAccess agent={agent} apiContext={apiContext} />
          )}
          {tab === "computer" && (
            <AgentComputerAccess
              agent={agent}
              apiContext={apiContext}
              queryClient={queryClient}
            />
          )}
          {tab === "mobile" && (
            <AgentMobileAccess
              agent={agent}
              apiContext={apiContext}
              queryClient={queryClient}
            />
          )}
        </div>
      </section>
      <ResponsiveAgentInspector
        eyebrow={t("Access")}
        title={
          tab === "api"
            ? t("API & MCP")
            : tab === "computer"
              ? t("Caller Computer")
              : t("Mobile")
        }
        description={
          tab === "api"
            ? t("Create narrowly scoped client credentials without mixing in runtime logs.")
            : tab === "computer"
              ? t("The caller grants a subset of the Agent declaration for each private invocation.")
              : t("Connected devices are included in the Agent MCP export.")
        }
      >
        <InspectorValue
          label={t("Ownership")}
          value={agentOwnershipLabel(agent)}
        />
        <InspectorValue
          label={t("Computer requirement")}
          value={humanize(agent.computer_requirement || "Optional")}
        />
        <InspectorValue
          label={t("Requested capabilities")}
          value={formatNumber(agent.workspace_capabilities?.length || 0)}
        />
        <InspectorValue label={t("Credential storage")} value="Nexus scoped" />
      </ResponsiveAgentInspector>
    </div>
  );
}

function AgentApiAccess({
  agent,
  apiContext,
}: {
  agent: Agent;
  apiContext: ApiContextValue;
}) {
  useLocale();
  const { tenants, projects } = useAuth();
  const [showKeyDialog, setShowKeyDialog] = useState(false);
  const [plaintextKey, setPlaintextKey] = useState("");
  const [mcpConfig, setMcpConfig] = useState<AgentMcpExport | null>(null);
  const createKey = useMutation({
    mutationFn: () => api.createAgentKey(apiContext, agent.id),
    onSuccess: (key) => {
      setPlaintextKey(key.plaintext_key);
      exportMcp.mutate();
      toast.success(t("Scoped API key created"));
    },
    onError: mutationError("Failed to create scoped API key"),
  });
  const exportMcp = useMutation({
    mutationFn: () => api.exportAgentMcp(apiContext, agent.id),
    onSuccess: setMcpConfig,
    onError: mutationError("Failed to export MCP config"),
  });
  function closeKeyDialog() {
    if (!createKey.isPending) {
      setShowKeyDialog(false);
    }
  }
  useEffect(() => {
    setPlaintextKey("");
    setMcpConfig(null);
  }, [agent.id, apiContext.tenantId, apiContext.projectId]);
  return (
    <div className="grid gap-6">
      <div className="agent-control-section">
        <div className="agent-control-section__header">
          <div>
            <p className="agent-control-eyebrow">{t("Credential task")}</p>
            <h2>{t("Scoped API key")}</h2>
            <p>{t("The plaintext value is shown once and is never added to Agent logs or Display events.")}</p>
          </div>
          {canAgent(agent, "manage_access") && (
            <button
              className="btn btn-primary"
              onClick={() => setShowKeyDialog(true)}
            >
              <KeyRound size={15} />{t("Create key")}</button>
          )}
        </div>
      </div>
      <AgentMcpSetup
        config={mcpConfig}
        plaintextKey={plaintextKey}
        loading={exportMcp.isPending}
        onLoad={() => exportMcp.mutate()}
        onForgetPlaintext={() => setPlaintextKey("")}
        workspaceName={
          tenants.find((tenant) => tenant.id === apiContext.tenantId)?.name
        }
        projectName={
          projects.find((project) => project.id === apiContext.projectId)?.name
        }
      />
      <NexilumeDialog
        open={showKeyDialog}
        title={plaintextKey ? t("Store this key now") : t("Create scoped API key")}
        eyebrow={t("Access task")}
        description={
          plaintextKey
            ? t("This is the only time Nexilume AI will show the plaintext key.")
            : t("The new credential will be limited to this Agent.")
        }
        busy={createKey.isPending}
        onClose={closeKeyDialog}
        footer={
          plaintextKey ? (
            <button className="btn btn-primary" onClick={closeKeyDialog}>{t("I have stored the key")}</button>
          ) : (
            <>
              <button className="btn" onClick={closeKeyDialog}>{t("Cancel")}</button>
              <button
                className="btn btn-primary"
                onClick={() => createKey.mutate()}
                disabled={createKey.isPending}
              >
                {createKey.isPending && (
                  <Loader2 size={15} className="animate-spin" />
                )}{t("Create key")}</button>
            </>
          )
        }
      >
        {plaintextKey ? (
          <div className="grid gap-3">
            <code className="agent-code-block break-all">{plaintextKey}</code>
            <button
              className="btn w-fit"
              onClick={() => copyText(plaintextKey)}
            >
              <Copy size={14} />{t("Copy key")}</button>
          </div>
        ) : (
          <InlineNotice
            title={t("One-time secret")}
            detail={t("Creating a new key does not revoke previously issued credentials.")}
          />
        )}
      </NexilumeDialog>
    </div>
  );
}

function AgentComputerAccess({
  agent,
  apiContext,
  queryClient,
}: {
  agent: Agent;
  apiContext: ApiContextValue;
  queryClient: QueryClient;
}) {
  useLocale();
  const refreshAgentData = useAgentQueryRefresh("control");
  const [requirement, setRequirement] = useState(
    agent.computer_requirement || "optional",
  );
  const [scopes, setScopes] = useState<string[]>(
    agent.workspace_capabilities ?? [],
  );
  const declaredBySdk = Boolean(agent.computer_declared_by_sdk);
  const canManage = canAgent(agent, "manage_access") && !declaredBySdk;
  useEffect(() => {
    setRequirement(agent.computer_requirement || "optional");
    setScopes(agent.workspace_capabilities ?? []);
  }, [agent.id, agent.computer_requirement, agent.workspace_capabilities]);
  const save = useMutation({
    mutationFn: () =>
      api.updateAgent(apiContext, agent.id, {
        computer_requirement: requirement as
          "required" | "optional" | "disabled",
        workspace_capabilities: requirement === "disabled" ? [] : scopes,
      }),
    onSuccess: async () => {
      toast.success(t("Caller Computer policy saved"));
      await refreshAgentData(queryClient, agent.id);
    },
    onError: mutationError("Failed to save Caller Computer policy"),
  });
  function toggleScope(scope: string) {
    setScopes((current) =>
      current.includes(scope)
        ? current.filter((item) => item !== scope)
        : [...current, scope],
    );
  }
  function toggleGroup(groupScopes: readonly string[]) {
    const allSelected = groupScopes.every((scope) => scopes.includes(scope));
    setScopes((current) =>
      allSelected
        ? current.filter((scope) => !groupScopes.includes(scope))
        : Array.from(new Set([...current, ...groupScopes])),
    );
  }
  return (
    <div className="grid gap-6">
      <div className="agent-control-section">
        <div className="agent-control-section__header">
          <div>
            <p className="agent-control-eyebrow">{t("Invocation requirement")}</p>
            <h2>{t("Caller-owned Computer")}</h2>
            <p>
              {declaredBySdk
                ? t("Declared by SDK. Update the Python Agent manifest and restart it to change this contract.")
                : t("Nexus represents the Agent on the caller's Computer. Developers never receive SSH details or terminal transcripts.")}
            </p>
          </div>
        </div>
        <div
          className="agent-choice-switch agent-choice-switch--three"
          role="group"
          aria-label={t("Computer requirement")}
        >
          {(["required", "optional", "disabled"] as const).map((value) => (
            <button
              type="button"
              disabled={!canManage}
              className={requirement === value ? "is-active" : ""}
              onClick={() => setRequirement(value)}
              key={value}
            >
              <CircleDot size={16} />
              <span>
                <strong>{humanize(value)}</strong>
                <small>
                  {value === "required"
                    ? t("Block calls without a binding")
                    : value === "optional"
                      ? t("Use a binding when available")
                      : t("Never request Computer access")}
                </small>
              </span>
            </button>
          ))}
        </div>
      </div>
      <div
        className={`agent-control-section ${requirement === "disabled" ? "is-disabled" : ""}`}
      >
        <div className="agent-control-section__header">
          <div>
            <p className="agent-control-eyebrow">{t("Maximum declaration")}</p>
            <h2>{t("Requested capabilities")}</h2>
            <p>{t("Each caller still approves a subset before a short-lived delegate is issued.")}</p>
          </div>
        </div>
        <fieldset
          disabled={!canManage || requirement === "disabled" || save.isPending}
          className="agent-capability-groups"
        >
          {workspaceCapabilityGroups.map((group) => {
            const selectedCount = group.scopes.filter((scope) =>
              scopes.includes(scope),
            ).length;
            return (
              <div className="agent-capability-group" key={group.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={selectedCount === group.scopes.length}
                    onChange={() => toggleGroup(group.scopes)}
                  />
                  <span>
                    <strong>{group.label}</strong>
                    <small>{group.detail}</small>
                  </span>
                  <em>
                    {selectedCount}/{group.scopes.length}
                  </em>
                </label>
                <details>
                  <summary>{t("Advanced permissions")}</summary>
                  <div>
                    {group.scopes.map((scope) => (
                      <label key={scope}>
                        <input
                          type="checkbox"
                          checked={scopes.includes(scope)}
                          onChange={() => toggleScope(scope)}
                        />
                        <span>{t(workspaceScopeLabels[scope])}</span>
                      </label>
                    ))}
                  </div>
                </details>
              </div>
            );
          })}
        </fieldset>
        <div className="agent-section-footer">
          <span>
            {declaredBySdk
              ? t("Declared by SDK; Cloud keeps this read-only and syncs scope reductions immediately.")
              : canManage
              ? t("Changing the declaration never expands an existing caller grant.")
              : t("This policy is read-only for your current role.")}
          </span>
          {canManage && (
            <button
              className="btn btn-primary"
              onClick={() => save.mutate()}
              disabled={save.isPending}
            >
              {save.isPending && <Loader2 size={15} className="animate-spin" />}{t("Save policy")}</button>
          )}
        </div>
      </div>
    </div>
  );
}

function AgentMobileAccess({
  agent,
  apiContext,
  queryClient,
}: {
  agent: Agent;
  apiContext: ApiContextValue;
  queryClient: QueryClient;
}) {
  useLocale();
  const refreshAgentData = useAgentQueryRefresh("control");
  const [requirement, setRequirement] = useState(
    agent.mobile_requirement || "disabled",
  );
  const [scopes, setScopes] = useState<string[]>(
    agent.mobile_capabilities ?? [],
  );
  const canManage = canAgent(agent, "manage_access");
  const sdkRequirement = agent.mobile_sdk_requirement || "disabled";
  const sdkScopes = agent.mobile_sdk_capabilities ?? [];
  const sdkDiffers =
    sdkRequirement !== agent.mobile_requirement ||
    sdkScopes.join("|") !== (agent.mobile_capabilities ?? []).join("|");
  useEffect(() => {
    setRequirement(agent.mobile_requirement || "disabled");
    setScopes(agent.mobile_capabilities ?? []);
  }, [agent.id, agent.mobile_requirement, agent.mobile_capabilities]);
  const save = useMutation({
    mutationFn: () =>
      api.updateAgent(apiContext, agent.id, {
        mobile_requirement: requirement as
          | "required"
          | "optional"
          | "disabled",
        mobile_capabilities: requirement === "disabled" ? [] : scopes,
      }),
    onSuccess: async () => {
      toast.success(t("Caller Mobile contract saved"));
      await refreshAgentData(queryClient, agent.id);
    },
    onError: mutationError("Failed to save Caller Mobile contract"),
  });
  const restoreSdk = useMutation({
    mutationFn: () =>
      api.updateAgent(apiContext, agent.id, { mobile_policy_source: "sdk" }),
    onSuccess: async () => {
      toast.success(t("Latest SDK Mobile defaults restored"));
      await refreshAgentData(queryClient, agent.id);
    },
    onError: mutationError("Failed to restore SDK Mobile defaults"),
  });
  function toggleScope(scope: string) {
    setScopes((current) =>
      current.includes(scope)
        ? current.filter((item) => item !== scope)
        : [...current, scope],
    );
  }
  return (
    <div className="grid gap-6">
      <InlineNotice
        tone={agent.mobile_policy_source === "sdk" ? "lume" : "neutral"}
        title={
          agent.mobile_policy_source === "sdk"
            ? t("Following SDK defaults")
            : t("Cloud override active")
        }
        detail={
          agent.mobile_can_restore_sdk
            ? sdkDiffers
              ? t("Latest SDK default is {{0}} with {{1}} {{2}}; the current Cloud policy remains authoritative.", { 0: humanize(sdkRequirement), 1: sdkScopes.length, 2: sdkScopes.length === 1 ? "capability" : "capabilities" })
              : t("The current policy matches the latest SDK declaration. Saving a change here creates a persistent Cloud override.")
            : t("This Agent's Mobile policy is managed in Nexus Cloud. New Runs snapshot the current effective declaration.")
        }
      />
      {canManage && agent.mobile_can_restore_sdk && agent.mobile_policy_source === "cloud" ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-y border-line py-3">
          <span className="text-sm text-muted">{t("Restore the latest OpenWrt SDK declaration and follow future SDK updates.")}</span>
          <button
            type="button"
            className="btn"
            onClick={() => restoreSdk.mutate()}
            disabled={save.isPending || restoreSdk.isPending}
          >
            {restoreSdk.isPending && <Loader2 size={15} className="animate-spin" />}{t("Restore SDK defaults")}</button>
        </div>
      ) : null}
      <div className="agent-control-section">
        <div className="agent-control-section__header">
          <div>
            <p className="agent-control-eyebrow">{t("Invocation requirement")}</p>
            <h2>{t("Caller-owned Mobile")}</h2>
            <p>{t("Declare what the Agent needs. Every caller pairs and authorizes their own phone; no developer device is attached here.")}</p>
          </div>
        </div>
        <div
          className="agent-choice-switch agent-choice-switch--three"
          role="group"
          aria-label={t("Mobile requirement")}
        >
          {(["required", "optional", "disabled"] as const).map((value) => (
            <button
              type="button"
              disabled={!canManage}
              className={requirement === value ? "is-active" : ""}
              onClick={() => setRequirement(value)}
              key={value}
            >
              <Smartphone size={16} />
              <span>
                <strong>{humanize(value)}</strong>
                <small>
                  {value === "required"
                    ? t("Block calls without an authorized phone")
                    : value === "optional"
                      ? t("Use the caller's phone when available")
                      : t("Never request Mobile access")}
                </small>
              </span>
            </button>
          ))}
        </div>
      </div>
      <div
        className={`agent-control-section ${requirement === "disabled" ? "is-disabled" : ""}`}
      >
        <div className="agent-control-section__header">
          <div>
            <p className="agent-control-eyebrow">{t("Maximum declaration")}</p>
            <h2>{t("Requested Mobile capabilities")}</h2>
            <p>{t("Callers approve a subset before Nexus issues a Run-scoped delegate. High-risk actions always require confirmation.")}</p>
          </div>
        </div>
        <fieldset
          disabled={!canManage || requirement === "disabled" || save.isPending || restoreSdk.isPending}
          className="agent-capability-groups"
        >
          <div className="agent-capability-group">
            {Object.entries(mobileScopeLabels).map(([scope, label]) => (
              <label key={scope}>
                <input
                  type="checkbox"
                  checked={scopes.includes(scope)}
                  onChange={() => toggleScope(scope)}
                />
                <span>
                  <strong>{t(label)}</strong>
                  <small>{scope}</small>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="agent-section-footer">
          <span>
            {requirement === "required" && scopes.length === 0
              ? t("Select at least one capability before requiring Caller Mobile.")
              : t("Device identity, screen content, and commands remain private to each caller and Run.")}
          </span>
          {canManage && (
            <button
              className="btn btn-primary"
              onClick={() => save.mutate()}
              disabled={save.isPending || restoreSdk.isPending || (requirement === "required" && scopes.length === 0)}
            >
              {save.isPending && <Loader2 size={15} className="animate-spin" />}{t("Save Mobile contract")}</button>
          )}
        </div>
      </div>
    </div>
  );
}

function AgentObservability({
  agent,
  apiContext,
}: {
  agent: Agent;
  apiContext: ApiContextValue;
}) {
  useLocale();
  const [params, setParams] = useSearchParams();
  const tab = (["runs", "memory", "events"].includes(params.get("obs_tab") || "") ? params.get("obs_tab") : "runs") as "runs" | "memory" | "events";
  const change = (key: string, value: string) => setParams(previous => {
    const next = new URLSearchParams(previous);
    if (value) next.set(key, value); else next.delete(key);
    if (key !== "obs_run") next.delete("obs_run");
    return next;
  }, { replace: true });
  const selectedRunId = params.get("obs_run") || "";
  const setSelectedRunId = (id: string) => change("obs_run", id);
  const filters = { q: params.get("obs_q") || "", since: params.get("obs_since") || "", until: params.get("obs_until") || "" };
  const runs = useObservabilityPage<AgentDisplayRun>(apiContext, agent.id, "runs", { ...filters, status: params.get("obs_status") || "", tool: params.get("obs_tool") || "", runtime: params.get("obs_runtime") || "" }, tab === "runs");
  const memories = useObservabilityPage<AgentMemoryItem>(apiContext, agent.id, "memory", filters, tab === "memory");
  const runtimeEvents = useObservabilityPage<AgentLog>(apiContext, agent.id, "logs", filters, tab === "events");
  const selectedPage = useObservabilityPage<AgentDisplayRun>(apiContext, agent.id, "runs", { run_id: selectedRunId },
    tab === "runs" && !!selectedRunId && !!runs.data && !runs.items.some(run => run.id === selectedRunId));
  const selectedRun =
    runs.items.find((run) => run.id === selectedRunId) ?? (selectedRunId ? selectedPage.items[0] : null) ?? runs.items[0] ?? null;
  const activePage = (tab === "runs" ? runs : tab === "memory" ? memories : runtimeEvents).paging;
  return (
    <div className="agent-workspace-layout">
      <section className="agent-workspace-primary">
        <SectionHeading
          eyebrow={t("Observability")}
          title={t("Runs, Memory, and runtime events")}
          description={t("Operational data stays attached to its Run and caller lineage. Caller Terminal and SSH details are never exposed here.")}
        />
        <NexilumeTabs
          label={t("Agent observability")}
          value={tab}
          onChange={(value) => change("obs_tab", value)}
          options={[
            { value: "runs", label: t("Runs") },
            { value: "memory", label: t("Memory") },
            { value: "events", label: t("Runtime events") },
          ]}
        />
        <form className="grid min-w-0 grid-cols-1 gap-3 py-4 sm:grid-cols-2" onSubmit={event => event.preventDefault()}>
          <label className="text-sm">{t("Search")}<input type="search" aria-label={t("Search observability")} className="w-full min-w-0 min-h-11 rounded border p-2" placeholder={tab === "runs" ? t("Title or exact Run ID") : t("Search metadata")} value={filters.q} maxLength={160} onChange={event => change("obs_q", event.target.value)} />
          </label>
          {tab === "runs" && <label className="text-sm">{t("Status")}<select aria-label={t("Run status")} className="w-full min-h-11 rounded border p-2" value={params.get("obs_status") || ""} onChange={event => change("obs_status", event.target.value)}>
            <option value="">{t("All statuses")}</option>{["running", "completed", "failed", "cancelled", "expired"].map(value => <option key={value} value={value}>{humanize(value)}</option>)}
          </select></label>}
          <label className="text-sm">{t("From")}<input aria-label={t("From date")} type="date" className="w-full min-w-0 min-h-11 rounded border p-2" value={filters.since} onChange={event => change("obs_since", event.target.value)} /></label>
          <label className="text-sm">{t("Before")}<input aria-label={t("Before date")} type="date" className="w-full min-w-0 min-h-11 rounded border p-2" value={filters.until} onChange={event => change("obs_until", event.target.value)} /></label>
          {tab === "runs" && <>
            <label className="text-sm">{t("Tool")}<input aria-label={t("Tool name")} className="w-full min-h-11 rounded border p-2" value={params.get("obs_tool") || ""} onChange={event => change("obs_tool", event.target.value)} /></label>
            <label className="text-sm">{t("Runtime ID")}<input aria-label={t("Runtime ID")} className="w-full min-w-0 min-h-11 rounded border p-2" value={params.get("obs_runtime") || ""} onChange={event => change("obs_runtime", event.target.value)} /></label>
          </>}
        </form>
        <div className="agent-tab-panel" role="tabpanel">
          {tab === "runs" && (
            <AgentRunsPanel
              agent={agent}
              apiContext={apiContext}
              runs={runs.items}
              loading={runs.isLoading}
              error={runs.isError}
              selectedRun={selectedRun}
              onSelect={setSelectedRunId}
              retry={() => void runs.refetch()}
            />
          )}
          {tab === "memory" && (
            <AgentMemoryPanel
              memories={memories.items}
              loading={memories.isLoading}
              error={memories.isError}
              retry={() => void memories.refetch()}
            />
          )}
          {tab === "events" && (
            <AgentRuntimeEventsPanel
              events={runtimeEvents.items}
              loading={runtimeEvents.isLoading}
              error={runtimeEvents.isError}
              retry={() => void runtimeEvents.refetch()}
            />
          )}
        </div>
        <ObservabilityPaging paging={{ ...activePage,
          next: () => { setSelectedRunId(""); activePage.next(); },
          previous: () => { setSelectedRunId(""); activePage.previous(); },
        }} />
      </section>
      <ResponsiveAgentInspector
        eyebrow={t("Observability")}
        title={
          selectedRun
            ? selectedRun.title || t("Run {{0}}", { 0: compactId(selectedRun.id) })
            : t("No Run selected")
        }
        description={
          selectedRun
            ? t("This view contains sanitized developer observability only.")
            : t("Invocation data will appear after the Agent receives a tools/call.")
        }
      >
        <InspectorValue
          label={t("Runs on this page")}
          value={
            runs.isLoading ? "Loading" : formatNumber(runs.items.length)
          }
        />
        <InspectorValue
          label={t("Selected status")}
          value={selectedRun ? humanize(selectedRun.status) : "—"}
        />
        <InspectorValue label={t("Caller")} value={selectedRun?.caller || "—"} />
        <InspectorValue
          label={t("Computer")}
          value={selectedRun ? humanize(selectedRun.computer_status) : "—"}
        />
        <InspectorValue
          label={t("Mobile")}
          value={selectedRun ? humanize(selectedRun.mobile_status) : "—"}
        />
      </ResponsiveAgentInspector>
    </div>
  );
}

function AgentRunsPanel({
  agent,
  apiContext,
  runs,
  loading,
  error,
  selectedRun,
  onSelect,
  retry,
}: {
  agent: Agent;
  apiContext: ApiContextValue;
  runs: AgentDisplayRun[];
  loading: boolean;
  error: boolean;
  selectedRun: AgentDisplayRun | null;
  onSelect: (id: string) => void;
  retry: () => void;
}) {
  useLocale();
  if (loading) return <InlineLoading label={t("Loading invocation Runs")} />;
  if (error && !runs.length)
    return (
      <InlineError
        title={t("Runs unavailable")}
        detail={t("Nexilume AI could not load invocation history.")}
        retry={retry}
      />
    );
  if (!runs.length)
    return (
      <EmptyState
        title={t("No invocation Runs")}
        description={t("Each tools/call creates an independent Run with its own Trace and Output lineage.")}
      />
    );
  return (
    <div className="agent-runs-workspace">
      {error && <p role="alert">{t("Refresh failed. Showing the last loaded page.")}{" "}<button onClick={retry}>{t("Retry")}</button></p>}
      <div className="agent-run-list" aria-label={t("Invocation Runs")}>
        {runs.map((run) => (
          <button
            className={run.id === selectedRun?.id ? "is-active" : ""}
            onClick={() => onSelect(run.id)}
            key={run.id}
          >
            <span>
              <strong>{run.title || t("Run {{0}}", { 0: compactId(run.id) })}</strong>
              <small>
                {run.caller} · {formatDate(run.started_at)}
              </small>
            </span>
            <StatusBadge status={run.status} />
          </button>
        ))}
      </div>
      {selectedRun && (
        <AgentRunDetail
          key={selectedRun.id}
          agent={agent}
          apiContext={apiContext}
          run={selectedRun}
        />
      )}
    </div>
  );
}

function AgentRunDetail({
  agent,
  apiContext,
  run,
}: {
  agent: Agent;
  apiContext: ApiContextValue;
  run: AgentDisplayRun;
}) {
  useLocale();
  const [live, setLive] = useState(false);
  const events = useObservabilityPage<AgentDisplayEvent>(apiContext, agent.id, `runs/${run.id}/events`, {}, true, live);
  const outputs = useObservabilityPage<AgentOutputArtifact>(apiContext, agent.id, `runs/${run.id}/outputs`);
  const presentation = useRunObservabilityPresentation(run);
  return (
    <div className="agent-run-detail">
      <div className="agent-run-detail__header">
        <div>
          <p className="agent-control-eyebrow">{t("Selected Run")}</p>
          <h2>{run.title || t("Run {{0}}", { 0: compactId(run.id) })}</h2>
          <p>
            {run.caller} · {formatDate(run.started_at)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusBadge status={run.computer_status} />
          <StatusBadge status={run.mobile_status} />
          <StatusBadge status={run.status} />
        </div>
      </div>
      <div className="agent-run-summary">
        <InspectorValue label={t("Run kind")} value={humanize(run.run_kind)} />
        <InspectorValue label={t("Tool")} value={run.tool_name || "Not recorded"} />
        <InspectorValue label={t("Turn")} value={presentation.turn} />
        <InspectorValue label={t("Invocation duration")} value={run.latency_ms == null ? "Not recorded" : `${run.latency_ms} ms`} />
        {presentation.details.map(item => <InspectorValue key={t(item.label)} {...item} />)}
        <InspectorValue label={t("Failure code")} value={run.error_code || "—"} />
        <InspectorValue
          label={t("Trace events loaded")}
          value={
            events.isLoading
              ? "Loading"
              : formatNumber(events.items.length)
          }
        />
        <InspectorValue
          label={t("Outputs loaded")}
          value={
            outputs.isLoading
              ? "Loading"
              : formatNumber(outputs.items.length)
          }
        />
        <InspectorValue
          label={t("Completed")}
          value={
            run.completed_at ? formatDate(run.completed_at) : "In progress"
          }
        />
      </div>
      <section className="agent-run-block">
        <h3>{t("Trace")}</h3>
        <label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" checked={live} onChange={event => setLive(event.target.checked)} />{t("Live latest events (pauses in background)")}</label>
        {events.isLoading ? (
          <InlineLoading label={t("Loading Trace")} />
        ) : events.isError && !events.items.length ? (
          <InlineNotice
            tone="warning"
            title={t("Trace unavailable")}
            detail={t("Historical routing or AG-UI details could not be loaded. Nexilume AI will not infer missing events.")}
          />
        ) : !events.items.length ? (
          <p className="agent-inline-help">{t("Historical route details unavailable.")}</p>
        ) : (
          <div className="agent-trace-list">
            {events.items.map((event) => (
              <TraceEventRow event={event} key={event.id} />
            ))}
          </div>
        )}
        {events.isError && <button onClick={() => void events.refetch()}>{t("Retry Trace")}</button>}
        <ObservabilityPaging paging={events.paging} label={t("Trace")} />
      </section>
      <section className="agent-run-block">
        <h3>{t("Output")}</h3>
        {outputs.isLoading ? (
          <InlineLoading label={t("Loading Output")} />
        ) : outputs.isError && !outputs.items.length ? (
          <InlineNotice
            tone="warning"
            title={t("Output unavailable")}
            detail={t("The immutable Output snapshot could not be loaded.")}
          />
        ) : !outputs.items.length ? (
          <p className="agent-inline-help">{t("No Output metadata was reported for this Run.")}</p>
        ) : (
          <div className="agent-output-list">
            {outputs.items.map((artifact) => (
              <OutputArtifactRow artifact={artifact} key={artifact.id} />
            ))}
          </div>
        )}
        {outputs.isError && <button onClick={() => void outputs.refetch()}>{t("Retry Output")}</button>}
        <ObservabilityPaging paging={outputs.paging} label={t("Output")} />
      </section>
    </div>
  );
}

function TraceEventRow({ event }: { event: AgentDisplayEvent }) {
  useLocale();
  const eventType = String(event.type || event.event_type || "EVENT");
  const label = String(
    event.stepName ||
      event.name ||
      event.activityType ||
      event.message ||
      event.toolCallId ||
      humanize(eventType),
  );
  return (
    <div>
      <span className="agent-trace-seq">{event.seq}</span>
      <span>
        <strong>{humanize(eventType)}</strong>
        <small>{label}</small>
      </span>
      <time>{formatDate(event.created_at)}</time>
    </div>
  );
}

function OutputArtifactRow({ artifact }: { artifact: AgentOutputArtifact }) {
  useLocale();
  return (
    <div>
      <span className="agent-output-node">
        <FileOutput size={15} />
      </span>
      <span className="min-w-0">
        <strong>
          {artifact.original_file_name || artifact.workspace_path}
        </strong>
        <small>
          {artifact.content_type || t("Unknown type")} ·{" "}
          {formatBytes(artifact.size_bytes)} ·{" "}
          {artifact.snapshot_status || t("snapshot unknown")}
        </small>
      </span>
      <StatusBadge status={artifact.scan_status || artifact.status} />
    </div>
  );
}

function AgentMemoryPanel({
  memories,
  loading,
  error,
  retry,
}: {
  memories: AgentMemoryItem[];
  loading: boolean;
  error: boolean;
  retry: () => void;
}) {
  useLocale();
  if (loading) return <InlineLoading label={t("Loading Memory lineage")} />;
  if (error && !memories.length)
    return (
      <InlineError
        title={t("Memory unavailable")}
        detail={t("Nexilume AI could not load Agent Memory.")}
        retry={retry}
      />
    );
  if (!memories.length)
    return (
      <EmptyState
        title={t("No Memory lineage")}
        description={t("Caller, Agent-global, and developer-only Memory will retain their source Run and consent state here.")}
      />
    );
  return (
    <div className="agent-memory-list">
      <p className="agent-inline-help">{t("Lineage metadata only. Caller memory content stays private.")}</p>
      {error && <p role="alert">{t("Refresh failed. Previous data is retained.")}{" "}<button onClick={retry}>{t("Retry")}</button></p>}
      {memories.map((memory) => (
        <article key={memory.id}>
          <span className="agent-memory-node">
            <MemoryStick size={15} />
          </span>
          <div>
            <strong>
              {memory.content_text || humanize(memory.memory_type)}
            </strong>
            <p>
              {memory.scope} · {memory.consent_status} ·{" "}
              {memory.sensitivity_level}
            </p>
            <small>{t("Source Run:")}{" "}
              {memory.source_run_id
                ? compactId(memory.source_run_id)
                : t("Not linked")}
            </small>
          </div>
          <StatusBadge status={memory.status} />
        </article>
      ))}
    </div>
  );
}

function AgentRuntimeEventsPanel({
  events,
  loading,
  error,
  retry,
}: {
  events: AgentLog[];
  loading: boolean;
  error: boolean;
  retry: () => void;
}) {
  useLocale();
  if (loading) return <InlineLoading label={t("Loading runtime events")} />;
  if (error && !events.length)
    return (
      <InlineError
        title={t("Runtime events unavailable")}
        detail={t("Other observability data remains available.")}
        retry={retry}
      />
    );
  if (!events.length)
    return (
      <EmptyState
        title={t("No runtime events")}
        description={t("Deployment, health, access, and configuration events will appear here.")}
      />
    );
  return (
    <div className="agent-runtime-event-list">
      {error && <p role="alert">{t("Refresh failed. Previous data is retained.")}{" "}<button onClick={retry}>{t("Retry")}</button></p>}
      {events.map((event) => (
        <div key={event.id}>
          <span
            className={`agent-status-dot agent-status-dot--${event.level === "error" ? "danger" : "muted"}`}
          />
          <span>
            <strong>{event.message}</strong>
            <small>{formatDate(event.created_at)}</small>
          </span>
          <StatusBadge status={event.level} />
        </div>
      ))}
    </div>
  );
}

function AgentPublish({ agent, apiContext, queryClient }: { agent: Agent; apiContext: ApiContextValue; queryClient: QueryClient }) {
  useLocale();
  const refreshAgentData = useAgentQueryRefresh("control");
  const [attachmentDialog, setAttachmentDialog] = useState<
    "computer" | "mobile" | null
  >(null);
  const location = useLocation();
  const navigate = useNavigate();
  const [attachmentParams, setAttachmentParams] = useSearchParams();
  const requestedAttachment = attachmentParams.get("attach");
  const returnToPrivateDisplay = attachmentParams.get("return") === "private-display";
  useEffect(() => {
    if (requestedAttachment === "computer" && agent.computer_requirement !== "disabled") setAttachmentDialog("computer");
    else if (requestedAttachment === "mobile" && agent.mobile_requirement !== "disabled") setAttachmentDialog("mobile");
  }, [requestedAttachment, agent.computer_requirement, agent.mobile_requirement]);
  const closeAttachment = () => {
    setAttachmentDialog(null);
    if (requestedAttachment || attachmentParams.has("return")) {
      const params = new URLSearchParams(attachmentParams);
      params.delete("attach");
      params.delete("return");
      setAttachmentParams(params, { replace: true });
    }
  };
  const computerBindings = useQuery({
    queryKey: [
      "agent-computer-bindings",
      agent.id,
      apiContext.token,
      apiContext.tenantId,
      apiContext.projectId,
    ],
    queryFn: () => api.agentComputerBindings(apiContext, agent.id),
    enabled:
      Boolean(apiContext.tenantId) &&
      agent.computer_requirement !== "disabled",
  });
  const mobileBindings = useQuery({
    queryKey: [
      "agent-mobile-bindings",
      agent.id,
      apiContext.token,
      apiContext.tenantId,
      apiContext.projectId,
    ],
    queryFn: () => api.agentMobileBindings(apiContext, agent.id),
    enabled:
      Boolean(apiContext.tenantId) && agent.mobile_requirement !== "disabled",
  });
  const activeComputerBinding =
    (computerBindings.data ?? []).find(
      (binding) => binding.status === "active" && binding.is_default,
    ) ??
    (computerBindings.data ?? []).find(
      (binding) => binding.status === "active",
    );
  const activeMobileBinding =
    (mobileBindings.data ?? []).find(
      (binding) => binding.status === "active" && binding.is_default,
    ) ??
    (mobileBindings.data ?? []).find(
      (binding) => binding.status === "active",
    );
  const returnTo = `${location.pathname}${location.search}`;
  const finishAttachment = async (kind: "computer" | "mobile") => {
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: [
          kind === "computer"
            ? "agent-computer-bindings"
            : "agent-mobile-bindings",
          agent.id,
        ],
      }),
      queryClient.invalidateQueries({
        queryKey: ["agent-interactor", apiContext, agent.id],
      }),
    ]);
    if (returnToPrivateDisplay) {
      setAttachmentDialog(null);
      navigate(`/agents/${agent.id}/private-display`, {
        replace: true,
        state: { returnTo: agentDisplayEntryUrl(location) },
      });
    } else closeAttachment();
  };
  const testActions = (<>
              <Link
                className="btn"
                to={`/agents/${agent.id}/private-display`}
                state={{ returnTo: agentDisplayEntryUrl(location) }}
              >
                <LockKeyhole size={14} />{t("Private Display (test)")}</Link>
              {agent.computer_requirement === "disabled" ? (
                <div className="flex min-h-10 items-center border border-line bg-paper px-3 text-xs font-medium text-muted">{t("Computer: Not requested")}</div>
              ) : (
                <div className="min-w-0">
                  <button
                    type="button"
                    className="btn w-full justify-center"
                    onClick={() => setAttachmentDialog("computer")}
                    disabled={computerBindings.isLoading}
                  >
                    {computerBindings.isLoading ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Terminal size={14} />
                    )}
                    {activeComputerBinding
                      ? t("Change Computer")
                      : t("Attach Computer")}
                  </button>
                  <div
                    className="mt-1 max-w-44 truncate text-center text-[11px] text-muted"
                    title={
                      activeComputerBinding?.connection_name ||
                      t("No Computer attached")
                    }
                  >
                    {activeComputerBinding?.connection_name ||
                      t("No Computer attached")}
                  </div>
                </div>
              )}
              {agent.mobile_requirement === "disabled" ? (
                <div className="flex min-h-10 items-center border border-line bg-paper px-3 text-xs font-medium text-muted">{t("Mobile: Not requested")}</div>
              ) : (
                <div className="min-w-0">
                  <button
                    type="button"
                    className="btn w-full justify-center"
                    onClick={() => setAttachmentDialog("mobile")}
                    disabled={mobileBindings.isLoading}
                  >
                    {mobileBindings.isLoading ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Smartphone size={14} />
                    )}
                    {activeMobileBinding ? t("Change Mobile") : t("Attach Mobile")}
                  </button>
                  <div
                    className="mt-1 max-w-44 truncate text-center text-[11px] text-muted"
                    title={
                      activeMobileBinding?.device_name || t("No Mobile attached")
                    }
                  >
                    {activeMobileBinding?.device_name || t("No Mobile attached")}
                  </div>
                </div>
              )}
  </>);
  return (<>
    <AgentPublicationWorkspace agent={agent} apiContext={apiContext} onSaved={() => refreshAgentData(queryClient, agent.id)} testActions={testActions} />
      <AgentComputerAttachDialog
        open={attachmentDialog === "computer"}
        agentId={agent.id}
        agentName={agent.name}
        declaredScopes={agent.workspace_capabilities ?? []}
        apiContext={apiContext}
        returnTo={returnTo}
        onClose={closeAttachment}
        onAttached={() => finishAttachment("computer")}
      />
      <AgentMobileAttachDialog
        open={attachmentDialog === "mobile"}
        agentId={agent.id}
        agentName={agent.name}
        declaredScopes={agent.mobile_capabilities ?? []}
        apiContext={apiContext}
        returnTo={returnTo}
        onClose={closeAttachment}
        onAttached={() => finishAttachment("mobile")}
      />
  </>);
}

function AgentSettings({
  agent,
  apiContext,
  queryClient,
}: {
  agent: Agent;
  apiContext: ApiContextValue;
  queryClient: QueryClient;
}) {
  useLocale();
  const refreshAgentData = useAgentQueryRefresh("control");
  const { projects } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState(agent.name);
  const [projectId, setProjectId] = useState(agent.project_id || "");
  const [releaseNotes, setReleaseNotes] = useState("");
  const [confirm, setConfirm] = useState<
    "transfer" | "archive" | "delete" | null
  >(null);
  useEffect(() => {
    setName(agent.name);
    setProjectId(agent.project_id || "");
  }, [agent.id, agent.name, agent.project_id]);
  const refresh = () => refreshAgentData(queryClient, agent.id);
  const versions = useQuery<AgentVersion[]>({
    queryKey: [
      "agent-versions",
      apiContext.token,
      apiContext.tenantId,
      agent.id,
    ],
    queryFn: () => api.agentVersions(apiContext, agent.id),
  });
  const saveName = useMutation({
    mutationFn: () =>
      api.updateAgent(apiContext, agent.id, { name: name.trim() }),
    onSuccess: async () => {
      toast.success(t("Agent name saved"));
      await refresh();
    },
    onError: mutationError("Failed to save Agent name"),
  });
  const transfer = useMutation({
    mutationFn: () =>
      api.updateAgent(apiContext, agent.id, {
        project_id: projectId || null,
        team_id: null,
      }),
    onSuccess: async () => {
      setConfirm(null);
      toast.success(t("Agent transferred"));
      await refresh();
    },
    onError: mutationError("Failed to transfer Agent"),
  });
  const lifecycle = useMutation({
    mutationFn: (status: "active" | "disabled" | "archived") =>
      api.updateAgent(apiContext, agent.id, { status }),
    onSuccess: async () => {
      setConfirm(null);
      toast.success(t("Agent lifecycle updated"));
      await refresh();
    },
    onError: mutationError("Failed to update lifecycle"),
  });
  const clone = useMutation({
    mutationFn: () => api.cloneAgent(apiContext, agent.id),
    onSuccess: async (copy) => {
      toast.success(t("{{0}} created as a draft", { 0: copy.name }));
      await queryClient.invalidateQueries({ queryKey: ["agents"] });
      navigate(`/agents/${copy.id}/runtime?setup=1`);
    },
    onError: mutationError("Failed to clone Agent"),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteAgent(apiContext, agent.id),
    onSuccess: async () => {
      toast.success(t("Agent deleted"));
      await queryClient.invalidateQueries({ queryKey: ["agents"] });
      navigate("/agents");
    },
    onError: mutationError("Failed to delete Agent"),
  });
  const publishVersion = useMutation({
    mutationFn: () =>
      api.publishAgentVersion(apiContext, agent.id, releaseNotes),
    onSuccess: async (version) => {
      setReleaseNotes("");
      toast.success(t("{{0}} published", { 0: version.version }));
      await queryClient.invalidateQueries({ queryKey: ["agent-versions"] });
      await refresh();
    },
    onError: mutationError("Failed to publish Agent version"),
  });
  const rollbackVersion = useMutation({
    mutationFn: (version: string) =>
      api.rollbackAgentVersion(apiContext, agent.id, version),
    onSuccess: async (version) => {
      toast.success(t("Current version changed to {{0}}", { 0: version.version }));
      await queryClient.invalidateQueries({ queryKey: ["agent-versions"] });
      await refresh();
    },
    onError: mutationError("Failed to change Agent version"),
  });
  const running = isAgentRunning(agent);
  const canRename = canAgent(agent, "rename");
  const canTransfer = canAgent(agent, "transfer");
  const canClone = canAgent(agent, "clone");
  const canDelete = canAgent(agent, "delete");
  return (
    <div className="agent-workspace-layout">
      <section className="agent-workspace-primary">
        <SectionHeading
          eyebrow={t("Settings")}
          title={t("Identity and lifecycle")}
          description={t("Runtime, access and resource settings are managed in their own sections.")}
        />
        <div className="agent-control-section">
          <div className="agent-control-section__header">
            <div>
              <p className="agent-control-eyebrow">{t("Identity")}</p>
              <h2>{t("Agent name")}</h2>
              <p>{t("The technical ID remains unchanged when the display name changes.")}</p>
            </div>
          </div>
          <div className="agent-inline-form agent-inline-form--wide">
            <Field label={t("Agent name")}>
              <input
                className="input"
                value={name}
                disabled={!canRename}
                onChange={(event) => setName(event.target.value)}
              />
            </Field>
            {canRename && (
              <button
                className="btn btn-primary"
                onClick={() => saveName.mutate()}
                disabled={
                  !name.trim() ||
                  name.trim() === agent.name ||
                  saveName.isPending
                }
              >
                <Save size={15} />{t("Save name")}</button>
            )}
          </div>
        </div>
        <div className="agent-control-section">
          <div className="agent-control-section__header">
            <div>
              <p className="agent-control-eyebrow">{t("Version settings")}</p>
              <h2>{t("Published version")}</h2>
              <p>{t("Publish a new immutable version or choose a previous published version as current.")}</p>
            </div>
            <StatusBadge
              status={agent.current_version ? "published" : "not published"}
            />
          </div>
          <div className="agent-detail-grid">
            <InspectorValue
              label={t("Current version")}
              value={agent.current_version || "Not published"}
            />
            <InspectorValue
              label={t("Published versions")}
              value={formatNumber(agent.version_count || 0)}
            />
          </div>
          {versions.isLoading ? (
            <InlineLoading label={t("Loading Agent versions")} />
          ) : versions.isError ? (
            <InlineError
              title={t("Versions unavailable")}
              detail={
                versions.error instanceof Error
                  ? versions.error.message
                  : t("Version history could not be loaded.")
              }
              retry={() => void versions.refetch()}
            />
          ) : (
            <div className="agent-object-list">
              {(versions.data ?? []).map((version) => (
                <div
                  className={`agent-object-row ${version.version === agent.current_version ? "is-selected" : ""}`}
                  key={version.id}
                >
                  <span className="agent-runtime-capsule">
                    <CircleDot size={15} />
                  </span>
                  <span>
                    <strong>{version.version}</strong>
                    <small>{t("Published")}{" "}{formatDate(version.created_at)}</small>
                  </span>
                  <StatusBadge
                    status={
                      version.version === agent.current_version
                        ? "current"
                        : version.status
                    }
                  />
                  {canAgent(agent, "settings") &&
                    version.version !== agent.current_version && (
                      <button
                        className="btn"
                        onClick={() => rollbackVersion.mutate(version.version)}
                        disabled={rollbackVersion.isPending}
                      >{t("Set current")}</button>
                    )}
                </div>
              ))}
              {!versions.data?.length && (
                <p className="agent-inline-help">{t("No version has been published yet.")}</p>
              )}
            </div>
          )}
          {canAgent(agent, "settings") && (
            <div className="grid gap-3 border-t border-line p-4">
              <Field label={t("Release notes (optional)")}>
                <textarea
                  className="textarea min-h-28"
                  maxLength={20000}
                  value={releaseNotes}
                  onChange={(event) => setReleaseNotes(event.target.value)}
                  placeholder={t("Summarize version-specific changes using Markdown.")}
                />
              </Field>
              <div className="agent-section-footer !border-0 !p-0">
                <span>{t("Publishing captures the current Agent configuration and these immutable version notes.")}</span>
                <button
                  className="btn btn-primary"
                  onClick={() => publishVersion.mutate()}
                  disabled={publishVersion.isPending}
                >
                  {publishVersion.isPending && (
                    <Loader2 size={15} className="animate-spin" />
                  )}{t("Publish new version")}</button>
              </div>
            </div>
          )}
        </div>
        <div className="agent-control-section">
          <div className="agent-control-section__header">
            <div>
              <p className="agent-control-eyebrow">{t("Ownership scope")}</p>
              <h2>{t("Project")}</h2>
              <p>{t("Stop the Runtime before moving the Agent to another project.")}</p>
            </div>
          </div>
          <div className="agent-inline-form agent-inline-form--wide">
            <Field label={t("Project")}>
              <select
                className="select"
                value={projectId}
                disabled={!canTransfer}
                onChange={(event) => setProjectId(event.target.value)}
              >
                <option value="">{t("All projects")}</option>
                {projects.map((project) => (
                  <option value={project.id} key={project.id}>
                    {project.name}
                  </option>
                ))}
              </select>
            </Field>
            {canTransfer && (
              <button
                className="btn"
                onClick={() => setConfirm("transfer")}
                disabled={running || projectId === (agent.project_id || "")}
              >{t("Transfer")}</button>
            )}
          </div>
          {canTransfer && running && (
            <p className="agent-inline-help">{t("Transfer is unavailable while the production Runtime is active.")}</p>
          )}
        </div>
        <div className="agent-control-section">
          <div className="agent-control-section__header">
            <div>
              <p className="agent-control-eyebrow">{t("Lifecycle")}</p>
              <h2>{t("Availability")}</h2>
              <p>{t("Disable access temporarily, archive completed work, or create a clean draft copy.")}</p>
            </div>
            <StatusBadge status={agent.lifecycle_status || agent.status} />
          </div>
          <div className="agent-action-cluster">
            {["disabled", "archived"].includes(agent.status)
              ? canAgent(agent, "enable") && (
                  <button
                    className="btn btn-primary"
                    onClick={() => lifecycle.mutate("active")}
                    disabled={lifecycle.isPending}
                  >{t("Enable Agent")}</button>
                )
              : canAgent(agent, "disable") && (
                  <button
                    className="btn"
                    onClick={() => lifecycle.mutate("disabled")}
                    disabled={lifecycle.isPending}
                  >{t("Disable Agent")}</button>
                )}
            {canAgent(agent, "archive") && (
              <button
                className="btn"
                onClick={() => setConfirm("archive")}
                disabled={lifecycle.isPending || agent.status === "archived"}
              >{t("Archive")}</button>
            )}
            {canClone && (
              <button
                className="btn"
                onClick={() => clone.mutate()}
                disabled={clone.isPending}
              >{t("Clone as draft")}</button>
            )}
            {!canAgent(agent, "settings") && (
              <span className="agent-inline-help">{t("Lifecycle controls are read-only for your current role.")}</span>
            )}
          </div>
        </div>
        <div className="agent-danger-zone">
          <div>
            <p className="agent-control-eyebrow">{t("Danger zone")}</p>
            <h2>{t("Delete Agent")}</h2>
            <p>{t("Deletion is only allowed after Runtime operations are stopped.")}</p>
          </div>
          {canDelete && (
            <button
              className="btn"
              onClick={() => setConfirm("delete")}
              disabled={running}
            >
              <Trash2 size={15} />{t("Delete Agent")}</button>
          )}
        </div>
      </section>
      <ResponsiveAgentInspector
        eyebrow={t("Settings")}
        title={agent.name}
        description={t("These controls change Agent identity and lifecycle only.")}
      >
        <InspectorValue label={t("Agent ID")} value={compactId(agent.id)} />
        <InspectorValue
          label={t("Lifecycle")}
          value={humanize(agent.lifecycle_status || agent.status)}
        />
        <InspectorValue
          label={t("Project")}
          value={
            agent.project_id ? compactId(agent.project_id) : "All projects"
          }
        />
        <InspectorValue label={t("Runtime active")} value={running ? "Yes" : "No"} />
      </ResponsiveAgentInspector>
      <ConfirmDialog
        open={confirm === "transfer"}
        title={t("Transfer Agent?")}
        description={t("The Agent will move to the selected project. Existing project-scoped access may no longer apply.")}
        confirmLabel={t("Transfer Agent")}
        busy={transfer.isPending}
        onConfirm={() => transfer.mutate()}
        onClose={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === "archive"}
        title={t("Archive Agent?")}
        description={t("The Agent leaves active Organization views until it is enabled again.")}
        confirmLabel={t("Archive Agent")}
        busy={lifecycle.isPending}
        onConfirm={() => lifecycle.mutate("archived")}
        onClose={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === "delete"}
        title={t("Delete Agent?")}
        description={t("Delete {{0}} from normal Organization views. This action requires all Runtime operations to be stopped.", { 0: agent.name })}
        confirmLabel={t("Delete Agent")}
        busy={remove.isPending}
        destructive
        onConfirm={() => remove.mutate()}
        onClose={() => setConfirm(null)}
      />
    </div>
  );
}

function AgentHexNode({
  label,
  tone,
}: {
  label: string;
  tone: "active" | "muted";
}) {
  useLocale();
  return (
    <span
      className={`agent-hex-node agent-hex-node--${tone}`}
      aria-label={t("{{0}} Agent node", { 0: label })}
    >
      <span>{label.slice(0, 1).toUpperCase() || "A"}</span>
    </span>
  );
}

function AgentControlSkeleton() {
  useLocale();
  return (
    <div
      className="agent-control-shell animate-pulse"
      aria-label={t("Loading Agent control")}
    >
      <div className="h-24 border-b border-line bg-white" />
      <div className="mt-5 h-16 border border-line bg-white" />
      <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="h-96 border border-line bg-white" />
        <div className="h-80 bg-ink-soft" />
      </div>
    </div>
  );
}

function InlineLoading({ label }: { label: string }) {
  useLocale();
  return (
    <div className="agent-inline-state">
      <Loader2 size={17} className="animate-spin" />
      <span>{label}</span>
    </div>
  );
}

function InlineError({
  title,
  detail,
  retry,
}: {
  title: string;
  detail: string;
  retry: () => void;
}) {
  useLocale();
  return (
    <div className="agent-inline-error">
      <AlertCircle size={18} />
      <div>
        <strong>{title}</strong>
        <p>{detail}</p>
      </div>
      <button className="btn" onClick={retry}>
        <RefreshCw size={14} />{t("Retry")}</button>
    </div>
  );
}

function InlineNotice({
  title,
  detail,
  tone = "neutral",
}: {
  title: string;
  detail: string;
  tone?: "neutral" | "lume" | "warning";
}) {
  useLocale();
  return (
    <div className={`agent-inline-notice agent-inline-notice--${tone}`}>
      <ShieldCheck size={17} />
      <div>
        <strong>{title}</strong>
        <p>{detail}</p>
      </div>
    </div>
  );
}

function DeploymentRow({ deployment }: { deployment: AgentRuntimeDeployment }) {
  useLocale();
  return (
    <div className="agent-deployment-row">
      <span className="agent-runtime-capsule">
        <Gauge size={15} />
      </span>
      <span>
        <strong>
          {deployment.runtime_kind === "docker"
            ? deployment.image_ref || t("Nexus Container")
            : t("OpenWrt {{0}}", { 0: deployment.edge_transport === "relay" ? "Relay" : "IPv6" })}
        </strong>
        <small>{t("Production · updated")}{" "}{formatDate(deployment.updated_at)}</small>
      </span>
      <StatusBadge status={deployment.status} />
      <StatusBadge status={deployment.health_status} />
    </div>
  );
}

function JobBanner({ job }: { job: Job }) {
  useLocale();
  const terminal = ["succeeded", "failed", "canceled"].includes(job.status);
  const failed = ["failed", "canceled"].includes(job.status);
  return (
    <div
      className={`agent-job-banner ${failed ? "is-failed" : terminal ? "is-complete" : "is-running"}`}
    >
      {terminal ? (
        failed ? (
          <AlertCircle size={17} />
        ) : (
          <Check size={17} />
        )
      ) : (
        <Loader2 size={17} className="animate-spin" />
      )}
      <div>
        <strong>
          {humanize(job.job_type.replace("agents.runtime.", ""))} ·{" "}
          {humanize(job.status)}
        </strong>
        <p>
          {failed
            ? job.error_message || t("The operation did not complete.")
            : terminal
              ? t("Runtime state refreshed.")
              : t("Nexus is tracking this operation. You may leave and return to this page.")}
        </p>
      </div>
    </div>
  );
}

function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  busy,
  destructive = false,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  busy: boolean;
  destructive?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  useLocale();
  return (
    <NexilumeDialog
      open={open}
      title={title}
      eyebrow={destructive ? t("Destructive action") : t("Confirm action")}
      description={description}
      busy={busy}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>{t("Cancel")}</button>
          <button
            className={`btn ${destructive ? "agent-danger-button" : "btn-primary"}`}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy && <Loader2 size={15} className="animate-spin" />}
            {confirmLabel}
          </button>
        </>
      }
    >
      <InlineNotice
        tone={destructive ? "warning" : "neutral"}
        title={t("Review before continuing")}
        detail={description}
      />
    </NexilumeDialog>
  );
}





function runtimeLabel(agent: Agent) {
  if (isOpenWrtRuntime(agent.runtime_kind))
    return t("OpenWrt {{0}} · {{1}}", { 0: agent.runtime_kind === "openwrt_relay" ? "Relay" : "IPv6", 1: humanize(agent.runtime_status || "Configured") });
  if (!agent.current_image_id) return t("Not configured");
  if (!agent.runtime_status) return t("Not deployed");
  return humanize(agent.runtime_status);
}

function agentLifecycle(agent: Agent): {
  label: string;
  tone: "success" | "warn" | "danger" | "muted";
} {
  const lifecycle = agent.lifecycle_status || agent.status;
  if (lifecycle === "archived") return { label: t("Archived"), tone: "muted" };
  if (lifecycle === "disabled") return { label: t("Disabled"), tone: "muted" };
  if (lifecycle === "draft" || !hasRuntimeTarget(agent))
    return { label: t("Setup required"), tone: "warn" };
  if (agent.status === "failed" || agent.status === "error")
    return { label: t("Action required"), tone: "danger" };
  return { label: t("Configured"), tone: "success" };
}


function healthTone(status?: string): "success" | "warn" | "danger" | "muted" {
  const normalized = (status || "").toLowerCase();
  if (["healthy", "active", "online"].includes(normalized)) return "success";
  if (["failed", "unhealthy", "error"].includes(normalized)) return "danger";
  if (["degraded", "unknown"].includes(normalized)) return "warn";
  return "muted";
}

function formatBytes(value: number) {
  if (!value) return "0 B";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

async function copyText(value: string) {
  await navigator.clipboard.writeText(value);
  toast.success(t("Copied"));
}
