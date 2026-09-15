import { t, useLocale, getLocale } from "../localization";
import { useAgentQueryRefresh } from "../components/AgentQueryRefresh";
import { useResourceManagementCopy } from "../components/ResourcePublishing";
import { StatusStripItem, AgentInventoryFact } from "../components/AgentPresentation";
import { AgentPublicationSummary, AgentSettingsPublication } from "../components/ResourcePublishing";
import { AgentModal, KeyResultCard, humanizeToken, workspaceScopeLabel, copy, setOptionalSearchParam } from "../components/AgentPresentation";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { AlertCircle, ChevronRight, Copy, Filter, KeyRound, Loader2, MonitorPlay, MoreHorizontal, PackagePlus, Plus, RefreshCw, Rocket, Search, Settings, Share2, ShieldCheck, Smartphone, Square, Terminal, Upload } from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "../app/AuthContext";
import { AgentMcpSetup } from "../components/AgentMcpSetup";
import { api, type ApiContext } from "../lib/api";

import type { Agent, AgentDisplayRun, AgentLog, AgentMemoryItem, AgentMcpExport, AgentRuntimeDeployment, Job } from "../lib/types";
import { compactId, formatDate, formatNumber } from "../lib/format";

import { DataTable } from "../components/DataTable";
import { CursorPageControls } from "../components/CursorPageControls";
import { useCatalogSearch, useCursorPage } from "../lib/useCursorPage";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Form";
import { Badge, StatusBadge } from "../components/Badge";
import { ResourceAccessState, ResourceOwnershipBadge, ResourceOwnershipPicker } from "../components/ResourceOwnership";
import { ResourceSharingAction, ShareResourceModal, type ShareResourceTarget } from "../components/ShareResourceModal";

import { NexilumeDialog } from "../components/NexilumeControls";
import { AgentRuntimeImages } from "../components/AgentRuntimeImages";

const agentNamePattern = "^[A-Za-z][A-Za-z0-9_\\-]{0,63}$";
const agentNameRegex = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const agentNameHint =
  "Use English letters, numbers, underscores, or hyphens. Start with a letter.";
const workspaceCapabilityOptions = [
  "connection.list",
  "connection.create",
  "connection.update",
  "connection.delete",
  "connection.test",
  "connection.bind",
  "files.list",
  "files.read",
  "files.write",
  "command.execute",
  "browser.control",
] as const;

type AgentAction =
  | "overview"
  | "container"
  | "deploy"
  | "access"
  | "mobile"
  | "activity"
  | "settings";
type AgentListSort = "updated" | "created" | "name";
type AgentListFilter = "all" | "active" | "draft" | "archived";
type OwnershipFilter = "all" | "organization" | "project";

export function AgentsPage() {
  useLocale();
  const {
    apiContext,
    isContextReady,
    projects,
    projectId,
  } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const leavingInventoryRef = useRef(false);

  const [showCreate, setShowCreate] = useState(false);
  const [showAgentFilters, setShowAgentFilters] = useState(false);
  const [agentSearch, setAgentSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<AgentListFilter>("all");
  const [ownershipFilter, setOwnershipFilter] =
    useState<OwnershipFilter>("all");
  const [agentSort, setAgentSort] = useState<AgentListSort>("updated");

  const [shareTarget, setShareTarget] = useState<ShareResourceTarget | null>(
    null,
  );

  const catalogSearch = useCatalogSearch(agentSearch);
  const agentPaging = useCursorPage([apiContext, catalogSearch, statusFilter, ownershipFilter, agentSort]);
  const agentPage = useQuery({
    queryKey: [
      "agents",
      apiContext.token,
      apiContext.tenantId,
      apiContext.projectId,
      catalogSearch, statusFilter, ownershipFilter, agentSort, agentPaging.cursor,
    ],
    queryFn: ({ signal }) => api.agentsPage(apiContext, { q: catalogSearch, lifecycle: statusFilter, ownership: ownershipFilter, sort: agentSort, cursor: agentPaging.cursor }, signal),
    // Preserve the bounded page while searching so the input/menu DOM does not
    // unmount and lose focus. Never carry rows across an auth/context boundary.
    placeholderData: (previous, previousQuery) => previousQuery?.queryKey[1] === apiContext.token
      && previousQuery?.queryKey[2] === apiContext.tenantId
      && previousQuery?.queryKey[3] === apiContext.projectId ? previous : undefined,
    gcTime: 0,
    enabled: isContextReady,
    refetchInterval: 30000,
    refetchOnWindowFocus: true,
  });
  const agents = { ...agentPage, data: agentPage.data?.items };
  const requestedAgentId = searchParams.get("agent") ?? "";
  const requestedAgent = useQuery({
    queryKey: ["agents", apiContext, "detail", requestedAgentId],
    queryFn: () => api.agent(apiContext, requestedAgentId),
    enabled: isContextReady && Boolean(requestedAgentId) && Boolean(agents.data) && !agents.data?.some(agent => agent.id === requestedAgentId),
  });

  const agentCapabilities = useQuery({
    queryKey: ["agent-capabilities", apiContext.token, apiContext.tenantId, apiContext.projectId],
    queryFn: () => api.agentCapabilities(apiContext),
    enabled: isContextReady,
  });

  useEffect(() => {
    setShareTarget(null);
  }, [projectId]);

  const agentData = agents.data ?? [];
  const runningCount = agentData.filter(isAgentRunning).length;
  const attentionCount = agentData.filter(
    (agent) => getAgentNextAction(agent).action !== "overview",
  ).length;
  const canCreateAgent = agentCapabilities.data?.create ?? false;
  useEffect(() => {
    if (!canCreateAgent || searchParams.get("create") !== "1") return;
    setShowCreate(true);
    const next = new URLSearchParams(searchParams);
    next.delete("create");
    setSearchParams(next, { replace: true });
  }, [canCreateAgent, searchParams, setSearchParams]);
  const filteredAgents = useMemo(
    () =>
      filterAgents(agentData, {
        search: agentSearch,
        status: statusFilter,
        ownership: ownershipFilter,
        sort: agentSort,
      }),
    [agentData, agentSearch, statusFilter, ownershipFilter, agentSort],
  );
  const selectedAgent = useMemo(() => {
    const offPage = requestedAgent.data?.id === requestedAgentId
      ? filterAgents([requestedAgent.data], { search: agentSearch, status: statusFilter, ownership: ownershipFilter, sort: agentSort })[0]
      : null;
    return (
      filteredAgents.find((agent) => agent.id === requestedAgentId) ??
      offPage ??
      (requestedAgent.isFetching ? null :
      defaultAgentSelection(filteredAgents)
      )
    );
  }, [filteredAgents, requestedAgentId, requestedAgent.data, requestedAgent.isFetching, agentSearch, statusFilter, ownershipFilter, agentSort]);
  const selectedAgentRuns = useQuery({
    queryKey: [
      "agent-display-runs",
      apiContext.token,
      apiContext.tenantId,
      apiContext.projectId,
      selectedAgent?.id,
    ],
    queryFn: () => api.agentDisplayRuns(apiContext, selectedAgent!.id),
    enabled:
      isContextReady && Boolean(selectedAgent?.id),
  });

  const projectNameById = useMemo(
    () => new Map(projects.map((project) => [project.id, project.name])),
    [projects],
  );

  useEffect(() => {
    // A refetch after creation must not overwrite the pending Runtime navigation.
    if (showCreate || searchParams.get("create") === "1" || leavingInventoryRef.current || agents.isLoading || agents.isError || requestedAgent.isFetching) return;
    const selectedId = selectedAgent?.id ?? "";
    if ((searchParams.get("agent") ?? "") === selectedId) return;
    const next = new URLSearchParams(searchParams);
    setOptionalSearchParam(next, "agent", selectedId);
    setSearchParams(next, { replace: true });
  }, [
    agents.isError,
    agents.isLoading,
    requestedAgent.isFetching,
    searchParams,
    selectedAgent?.id,
    setSearchParams,
    showCreate,
  ]);

  function openAgent(agent: Agent, action: AgentAction = "overview") {
    leavingInventoryRef.current = true;
    const section =
      action === "container" || action === "deploy"
        ? "runtime"
        : action === "activity"
          ? "observability"
          : action === "mobile"
            ? "access"
            : action;
    navigate(`/agents/${agent.id}/${section}`);
  }

  function selectAgent(agent: Agent) {
    const next = new URLSearchParams(searchParams);
    setOptionalSearchParam(next, "agent", agent.id);
    setSearchParams(next, { replace: true });
  }

  return (
    <div className="grid gap-6">

<div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className="text-[1.75rem] font-semibold leading-9 tracking-[-0.025em] text-ink">{t("Agents")}</h1>
            <p className="mt-1 text-sm text-muted">{t("Select an Agent, resolve its next operational step, and open the dedicated command surface.")}</p>
          </div>
          {canCreateAgent && (
            <button
              className="btn btn-primary w-full sm:w-fit"
              onClick={() => setShowCreate(true)}
            >
              <Plus size={16} />{t("Create agent")}</button>
          )}
        </div>

<section className="agent-inventory-shell" aria-label={t("Agent inventory")}>
          {agents.isLoading ? (
            <AgentListSkeleton />
          ) : agents.isError ? (
            <div className="p-5">
              <LoadErrorState
                title={t("Agents unavailable")}
                description={
                  agents.error instanceof Error
                    ? agents.error.message
                    : t("Agents could not be loaded.")
                }
                onRetry={() => void agents.refetch()}
              />
            </div>
          ) : agentData.length === 0 && !agentSearch && statusFilter === "all" && ownershipFilter === "all" && agentPaging.page === 1 ? (
            <div className="p-5">
              <EmptyState
                title={
                  canCreateAgent
                    ? t("Create your first agent")
                    : t("No agents available")
                }
                description={
                  canCreateAgent
                    ? t("Start with an agent name, then connect a runtime and deploy when you are ready.")
                    : t("You can view agents after an administrator shares one with you.")
                }
                action={
                  canCreateAgent ? (
                    <button
                      className="btn btn-primary"
                      onClick={() => setShowCreate(true)}
                    >{t("Create agent")}</button>
                  ) : undefined
                }
              />
            </div>
          ) : (
            <>
              <AgentStatusStrip
                total={agentData.length}
                attention={attentionCount}
                running={runningCount}
                publicationSummary={<AgentPublicationSummary surface="page" agents={agentData} />}
              />
              <p className="px-5 text-xs text-muted">{t("Summary for this page. Search and filters apply to the entire directory.")}</p>
              <AgentToolbar
                search={agentSearch}
                onSearch={setAgentSearch}
                showMobileFilters={showAgentFilters}
                onToggleMobileFilters={() =>
                  setShowAgentFilters((value) => !value)
                }
              >
                <select
                  className="select h-10"
                  value={statusFilter}
                  onChange={(event) =>
                    setStatusFilter(event.target.value as AgentListFilter)
                  }
                  aria-label={t("Filter by lifecycle")}
                >
                  <option value="all">{t("All lifecycle")}</option>
                  <option value="active">{t("Ready")}</option>
                  <option value="draft">{t("Setup required")}</option>
                  <option value="archived">{t("Archived")}</option>
                </select>
                <select
                  className="select h-10"
                  value={ownershipFilter}
                  onChange={(event) =>
                    setOwnershipFilter(event.target.value as OwnershipFilter)
                  }
                  aria-label={t("Filter by ownership")}
                >
                  <option value="all">{t("All ownership")}</option>
                  <option value="organization">{t("Organization shared")}</option>
                  <option value="project">{t("Project")}</option>
                </select>
                <select
                  className="select h-10"
                  value={agentSort}
                  onChange={(event) =>
                    setAgentSort(event.target.value as AgentListSort)
                  }
                  aria-label={t("Sort agents")}
                >
                  <option value="updated">{t("Recently updated")}</option>
                  <option value="created">{t("Recently created")}</option>
                  <option value="name">{t("Name")}</option>
                </select>
              </AgentToolbar>
              <div className="px-5"><CursorPageControls page={agentPaging.page} hasMore={Boolean(agentPage.data?.next_cursor)} busy={agentPage.isFetching} onPrevious={agentPaging.previous} onNext={() => agentPaging.next(agentPage.data?.next_cursor)} /></div>
              <div className="agent-inventory-body">
                {filteredAgents.length === 0 ? (
                  <EmptyState
                    title={t("No agents match these filters")}
                    description={t("Clear the current search and filters to see all agents.")}
                    action={
                      <button
                        className="btn"
                        onClick={() => {
                          setAgentSearch("");
                          setStatusFilter("all");
                          setOwnershipFilter("all");
                        }}
                      >{t("Reset filters")}</button>
                    }
                  />
                ) : (
                  <div className="agent-inventory-workspace">
                    <AgentList
                      agents={filteredAgents}
                      selectedId={selectedAgent?.id ?? ""}
                      projectNameById={projectNameById}
                      onSelect={selectAgent}
                      onOpen={openAgent}
                      onShare={(agent) =>
                        setShareTarget(agentShareTarget(agent))
                      }
                    />
                    <AgentInventoryInspector
                      agent={selectedAgent}
                      projectName={
                        selectedAgent?.project_id
                          ? projectNameById.get(selectedAgent.project_id)
                          : undefined
                      }
                      latestRun={selectedAgentRuns.data?.[0] ?? null}
                      runsLoading={selectedAgentRuns.isLoading}
                      onOpen={openAgent}
                    />
                  </div>
                )}
              </div>
            </>
          )}
        </section>



{showCreate && canCreateAgent && (
        <CreateAgentModal
          apiContext={apiContext}
          queryClient={queryClient}
          onCreated={(agent) => {
            leavingInventoryRef.current = true;
            setShowCreate(false);
            navigate(`/agents/${agent.id}/runtime?setup=1`);
          }}
          onClose={() => setShowCreate(false)}
        />
      )}

<ShareResourceModal
        target={shareTarget}
        onClose={() => setShareTarget(null)}
      />

    </div>
  );
}

function agentShareTarget(agent: Agent): ShareResourceTarget {
  return {
    resourceType: "agent",
    resourceKind: "Agent",
    resourceId: agent.id,
    resourceName: agent.name,
  };
}

function AgentStatusStrip({
  total,
  attention,
  running,
  publicationSummary,
}: {
  total: number;
  attention: number;
  running: number;
  publicationSummary: React.ReactNode;
}) {
  useLocale();
  return (
    <div
      className="agent-inventory-status"
      aria-label={t("Agent operational summary")}
    >
      <StatusStripItem label={t("Total")} value={total} />
      <StatusStripItem
        label={t("Needs attention")}
        value={attention}
        tone={attention > 0 ? "warn" : "neutral"}
      />
      <StatusStripItem
        label={t("Running")}
        value={running}
        tone={running > 0 ? "success" : "neutral"}
      />
      {publicationSummary}
    </div>
  );
}

function AgentListSkeleton({ compact = false }: { compact?: boolean }) {
  useLocale();
  return (
    <div className="animate-pulse p-5" aria-label={t("Loading agents")}>
      {!compact && <div className="mb-5 h-10 rounded-md bg-slate-100" />}
      <div className="grid gap-3">
        {Array.from({ length: compact ? 2 : 3 }, (_, index) => (
          <div
            key={index}
            className="grid min-h-20 gap-3 rounded-lg border border-line p-4 sm:grid-cols-[1.5fr_1fr_1fr]"
          >
            <div className="h-4 w-2/3 rounded bg-slate-200" />
            <div className="h-4 w-1/2 rounded bg-slate-100" />
            <div className="h-4 w-3/4 rounded bg-slate-100" />
          </div>
        ))}
      </div>
    </div>
  );
}


function AgentToolbar({
  search,
  onSearch,
  showMobileFilters,
  onToggleMobileFilters,
  children,
}: {
  search: string;
  onSearch: (value: string) => void;
  showMobileFilters: boolean;
  onToggleMobileFilters: () => void;
  children: React.ReactNode;
}) {
  useLocale();
  return (
    <div className="agent-inventory-toolbar">
      <div className="flex flex-wrap gap-2">
        <div className="relative min-w-0 flex-1 md:max-w-xs xl:max-w-sm">
          <Search
            className="pointer-events-none absolute left-3 top-3 text-muted"
            size={16}
          />
          <input
            className="input h-10 pl-9"
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            placeholder={t("Search agents")}
            aria-label={t("Search agents")}
          />
        </div>
        <button
          className={`btn h-10 px-3 md:hidden ${showMobileFilters ? "border-accent text-accent" : ""}`}
          onClick={onToggleMobileFilters}
          aria-expanded={showMobileFilters}
        >
          <Filter size={16} />{t("Filters")}</button>
        <div
          className={`${showMobileFilters ? "grid" : "hidden"} w-full gap-2 md:ml-auto md:flex md:w-auto md:flex-1 md:justify-end`}
        >
          {children}
        </div>
      </div>
    </div>
  );
}

function AgentList({
  agents,
  selectedId,
  projectNameById,
  onSelect,
  onOpen,
  onShare,
}: {
  agents: Agent[];
  selectedId: string;
  projectNameById: Map<string, string>;
  onSelect: (agent: Agent) => void;
  onOpen: (agent: Agent, action?: AgentAction) => void;
  onShare: (agent: Agent) => void;
}) {
  useLocale();
  return (
    <>
      <div className="agent-inventory-track hidden lg:block">
        <div className="agent-inventory-track__header">
          <div>{t("Agent")}</div>
          <div>{t("Runtime")}</div>
          <div>{t("Governance")}</div>
        </div>
        <div>
          {agents.map((agent) => (
            <AgentDesktopRow
              key={agent.id}
              agent={agent}
              selected={agent.id === selectedId}
              projectName={
                agent.project_id
                  ? projectNameById.get(agent.project_id)
                  : undefined
              }
              onSelect={onSelect}
              onShare={onShare}
              onOpen={onOpen}
            />
          ))}
        </div>
      </div>
      <div className="agent-inventory-mobile-list grid gap-3 lg:hidden">
        {agents.map((agent) => (
          <AgentMobileCard
            key={agent.id}
            agent={agent}
            projectName={
              agent.project_id
                ? projectNameById.get(agent.project_id)
                : undefined
            }
            onOpen={onOpen}
            onShare={onShare}
          />
        ))}
      </div>
    </>
  );
}

function AgentDesktopRow({
  agent,
  selected,
  projectName,
  onSelect,
  onOpen,
  onShare,
}: {
  agent: Agent;
  selected: boolean;
  projectName?: string;
  onSelect: (agent: Agent) => void;
  onOpen: (agent: Agent, action?: AgentAction) => void;
  onShare: (agent: Agent) => void;
}) {
  useLocale();
  return (
    <div className={`agent-inventory-row ${selected ? "is-selected" : ""}`}>
      <button
        className="agent-inventory-row__select"
        onClick={() => onSelect(agent)}
        aria-pressed={selected}
      >
        <span className="agent-inventory-row__identity">
          <span
            className={`agent-inventory-node ${isAgentRunning(agent) ? "is-active" : ""}`}
            aria-hidden="true"
          >
            {displayAgentName(agent).slice(0, 1).toUpperCase()}
          </span>
          <span className="min-w-0">
            <span className="flex min-w-0 flex-wrap items-center gap-2 font-semibold text-ink">
              <span className="truncate">{displayAgentName(agent)}</span>
              <ResourceAccessState access={agent.access} />
            </span>
            <span className="mt-1 flex min-w-0 items-center gap-2 text-xs text-muted">
              <ResourceOwnershipBadge ownership={agent.ownership} />
              {!agent.ownership && <span className="truncate">{agentProjectLabel(agent, projectName)}</span>}
            </span>
          </span>
        </span>
        <span className="agent-inventory-row__runtime">
          <span>
            <i
              className={isAgentRunning(agent) ? "is-active" : ""}
              aria-hidden="true"
            />
            {runtimeLabel(agent)}
          </span>
          <small className={agent.configuration_drift ? "is-warning" : ""}>
            {agent.configuration_drift
              ? t("Update available")
              : humanizeToken(agent.runtime_health_status || "Not checked")}
          </small>
        </span>
        <span className="agent-inventory-row__governance">
          <AgentLifecycleBadge agent={agent} />
          <AgentPublicationSummary surface="badge" agent={agent} />
        </span>
      </button>
      <div className="agent-inventory-row__menu">
        <AgentOverflowMenu agent={agent} onOpen={onOpen} onShare={onShare} />
      </div>
    </div>
  );
}

function AgentMobileCard({
  agent,
  projectName,
  onOpen,
  onShare,
}: {
  agent: Agent;
  projectName?: string;
  onOpen: (agent: Agent, action?: AgentAction) => void;
  onShare: (agent: Agent) => void;
}) {
  useLocale();
  const nextAction = getAgentNextAction(agent);
  return (
    <article className="agent-inventory-mobile border border-line bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <button
          className="flex min-w-0 items-center gap-3 text-left"
          onClick={() => onOpen(agent)}
        >
          <span
            className={`agent-inventory-node ${isAgentRunning(agent) ? "is-active" : ""}`}
            aria-hidden="true"
          >
            {displayAgentName(agent).slice(0, 1).toUpperCase()}
          </span>
          <span className="min-w-0">
            <span className="block truncate font-semibold text-ink">
              {displayAgentName(agent)}
            </span>
            <span className="mt-1 block truncate text-xs text-muted">
              {agentProjectLabel(agent, projectName)}
            </span>
          </span>
        </button>
        <AgentOverflowMenu agent={agent} onOpen={onOpen} onShare={onShare} />
      </div>
      <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
        <div>
          <div className="mb-1 text-xs text-muted">{t("Lifecycle")}</div>
          <AgentLifecycleBadge agent={agent} />
        </div>
          <div>
            <div className="mb-1 text-xs text-muted">{t("Ownership")}</div>
            <ResourceOwnershipBadge ownership={agent.ownership} />
            {!agent.ownership && <span className="text-sm text-ink">{agentOwnershipLabel(agent)}</span>}
        </div>
        <div className="col-span-2">
          <div className="mb-1 text-xs text-muted">{t("Runtime")}</div>
          <div className="flex items-center gap-2 text-ink">
            <span
              className={`h-2 w-2 rounded-full ${isAgentRunning(agent) ? "bg-success" : "bg-slate-300"}`}
            />
            {runtimeLabel(agent)}
          </div>
          {agent.configuration_drift && (
            <div className="mt-1 text-xs text-warn">{t("A newer runtime image is ready to deploy.")}</div>
          )}
        </div>
      </div>
      <button
        className="btn mt-4 w-full"
        onClick={() => onOpen(agent, nextAction.action)}
      >{t("Open Agent")}<ChevronRight size={15} />
      </button>
    </article>
  );
}

function AgentInventoryInspector({
  agent,
  projectName,
  latestRun,
  runsLoading,
  onOpen,
}: {
  agent: Agent | null;
  projectName?: string;
  latestRun: AgentDisplayRun | null;
  runsLoading: boolean;
  onOpen: (agent: Agent, action?: AgentAction) => void;
}) {
  useLocale();
  if (!agent)
    return (
      <aside className="agent-inventory-inspector">
        <p className="agent-inventory-inspector__empty">{t("Select an Agent to inspect its runtime and next operation.")}</p>
      </aside>
    );
  const nextAction = getAgentNextAction(agent);
  const imageReference =
    agent.deployed_image_ref || agent.current_image_ref || "Not configured";
  return (
    <aside
      className="agent-inventory-inspector"
      aria-label={t("{{0}} inspector", { 0: displayAgentName(agent) })}
    >
      <p className="agent-inventory-inspector__eyebrow">{t("Inspector · Selected Agent")}</p>
      <div className="agent-inventory-inspector__identity">
        <span
          className={`agent-hex-node ${isAgentRunning(agent) ? "agent-hex-node--active" : ""}`}
          aria-hidden="true"
        >
          {displayAgentName(agent).slice(0, 1).toUpperCase()}
        </span>
        <span>
          <strong>{displayAgentName(agent)}</strong>
          <small>{agentProjectLabel(agent, projectName)}</small>
        </span>
      </div>
      <div className="agent-inventory-inspector__priority">
        <small>{t("Current priority")}</small>
        <h2>{nextAction.label}</h2>
        <p>{nextAction.description}</p>
        <button
          className="btn btn-primary w-full"
          onClick={() => onOpen(agent, nextAction.action)}
        >{t("Open Agent")}<ChevronRight size={15} />
        </button>
      </div>
      <dl>
        <AgentInventoryFact label={t("Runtime")} value={runtimeLabel(agent)} />
        <AgentInventoryFact
          label={t("Health")}
          value={humanizeToken(agent.runtime_health_status || "Not checked")}
        />
        <AgentInventoryFact
          label={t("Ownership")}
          value={agentOwnershipLabel(agent)}
        />
        <AgentPublicationSummary surface="fact" agent={agent} />
        <AgentInventoryFact
          label={t("Latest invocation")}
          value={
            runsLoading
              ? "Loading"
              : latestRun
                ? `${humanizeToken(latestRun.status)} · ${formatDate(latestRun.started_at)}`
                : "No Runs yet"
          }
        />
        <AgentInventoryFact
          label={t("Updated")}
          value={formatDate(agent.updated_at)}
        />
      </dl>
      <AgentInspectorCopy label={t("Agent ID")} value={agent.id} />
      <AgentInspectorCopy
        label={t("Runtime image")}
        value={imageReference}
        disabled={imageReference === "Not configured"}
      />
    </aside>
  );
}


function AgentInspectorCopy({
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
    <div className="agent-inventory-inspector__copy">
      <span>
        <small>{label}</small>
        <code title={value}>{value}</code>
      </span>
      <button
        type="button"
        aria-label={t("Copy {{0}}", { 0: label })}
        disabled={disabled}
        onClick={() => void copy(value)}
      >
        <Copy size={14} />
      </button>
    </div>
  );
}

function AgentOverflowMenu({
  agent,
  onOpen,
  onShare,
}: {
  agent: Agent;
  onOpen: (agent: Agent, action?: AgentAction) => void;
  onShare: (agent: Agent) => void;
}) {
  useLocale();
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function closeFromOutside(event: MouseEvent) {
      if (rootRef.current?.contains(event.target as Node)) return;
      setOpen(false);
    }
    function closeFromEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    }
    document.addEventListener("mousedown", closeFromOutside);
    document.addEventListener("keydown", closeFromEscape);
    return () => {
      document.removeEventListener("mousedown", closeFromOutside);
      document.removeEventListener("keydown", closeFromEscape);
    };
  }, [open]);

  function focusMenuItem(position: "first" | "last" | "next" | "previous") {
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>(
        '[role="menuitem"]',
      ) ?? [],
    );
    if (items.length === 0) return;
    const currentIndex = items.indexOf(
      document.activeElement as HTMLButtonElement,
    );
    const nextIndex =
      position === "first"
        ? 0
        : position === "last"
          ? items.length - 1
          : position === "next"
            ? (currentIndex + 1) % items.length
            : (currentIndex - 1 + items.length) % items.length;
    items[nextIndex]?.focus();
  }

  function runAction(action: () => void) {
    setOpen(false);
    action();
  }

  return (
    <div className="agent-overflow-menu" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="btn h-10 w-10 p-0"
        aria-label={t("More actions for {{0}}", { 0: displayAgentName(agent) })}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => {
          setOpen((value) => !value);
          if (!open) window.requestAnimationFrame(() => focusMenuItem("first"));
        }}
      >
        <MoreHorizontal size={16} />
      </button>
      {open && (
        <div
          id={menuId}
          ref={menuRef}
          className="agent-overflow-menu__panel"
          role="menu"
          aria-label={t("Actions for {{0}}", { 0: displayAgentName(agent) })}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              focusMenuItem("next");
            }
            if (event.key === "ArrowUp") {
              event.preventDefault();
              focusMenuItem("previous");
            }
            if (event.key === "Home") {
              event.preventDefault();
              focusMenuItem("first");
            }
            if (event.key === "End") {
              event.preventDefault();
              focusMenuItem("last");
            }
          }}
        >
          <button
            role="menuitem"
            onClick={() => runAction(() => onOpen(agent, "activity"))}
          >
            <RefreshCw size={14} />{" "}{t("Activity")}</button>
          {agentCan(agent, "manage_access") && (
            <button
              role="menuitem"
              onClick={() => runAction(() => onOpen(agent, "access"))}
            >
              <KeyRound size={14} />{" "}{t("Manage access")}</button>
          )}
          {agentCan(agent, "share") && (
            <ResourceSharingAction>
            <button
              role="menuitem"
              onClick={() => runAction(() => onShare(agent))}
            >
              <Share2 size={14} />{" "}{t("Share")}</button>
            </ResourceSharingAction>
          )}
          {agentCan(agent, "settings") && (
            <button
              role="menuitem"
              onClick={() => runAction(() => onOpen(agent, "settings"))}
            >
              <Settings size={14} />{" "}{t("Settings")}</button>
          )}
        </div>
      )}
    </div>
  );
}

function AgentLifecycleBadge({ agent }: { agent: Agent }) {
  useLocale();
  const lifecycle = agentLifecycle(agent);
  return <Badge tone={lifecycle.tone}>{lifecycle.label}</Badge>;
}

function AgentOverviewModal({
  agent,
  projectName,
  onAction,
  onShare,
  onClose,
}: {
  agent: Agent;
  projectName?: string;
  onAction: (action: AgentAction) => void;
  onShare: () => void;
  onClose: () => void;
}) {
  useLocale();
  const nextAction = getAgentNextAction(agent);
  return (
    <AgentModal
      title={displayAgentName(agent)}
      description={agentProjectLabel(agent, projectName)}
      onClose={onClose}
      maxWidth="max-w-4xl"
    >
      <div className="grid gap-5">
        <div className="flex flex-col gap-4 rounded-lg border border-line bg-slate-50 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <AgentLifecycleBadge agent={agent} />
              <Badge tone={isAgentRunning(agent) ? "success" : "muted"}>
                {runtimeLabel(agent)}
              </Badge>
              {agent.edge_binding_mode &&
                agent.edge_binding_mode !== "manual" && (
                  <Badge
                    tone={
                      agent.edge_binding_mode === "managed" ? "info" : "warn"
                    }
                  >
                    {agent.edge_binding_mode === "managed"
                      ? t("OpenWrt managed")
                      : t("OpenWrt managed paused")}
                  </Badge>
                )}
              <ResourceOwnershipBadge ownership={agent.ownership} />
            </div>
            <p className="mt-3 text-sm text-muted">
              {nextAction.action === "overview"
                ? t("This agent is configured. Manage its runtime and settings below.")
                : t("Next step: {{0}}", { 0: nextAction.description })}
            </p>
          </div>
          {nextAction.action !== "overview" && (
            <button
              className="btn btn-primary shrink-0"
              onClick={() => onAction(nextAction.action)}
            >
              {nextAction.label}
              <ChevronRight size={15} />
            </button>
          )}
        </div>

        <div className="divide-y divide-line rounded-lg border border-line">
          <AgentDetailSection
            icon={<PackagePlus size={17} />}
            title={t("Runtime & deployment")}
            description={
              isOpenWrtRuntime(agent.runtime_kind)
                ? `${agent.edge_router_id || "OpenWrt"} · ${agent.edge_endpoint_url || runtimeLabel(agent)}`
                : agent.current_image_ref
                  ? `${agent.current_image_ref} · ${runtimeLabel(agent)}`
                  : t("Connect a container image or an OpenWrt Agent.")
            }
            actions={
              <>
                {agentCan(agent, "configure_runtime") && (
                  <button className="btn" onClick={() => onAction("container")}>{t("Configure runtime")}</button>
                )}
                {agentCan(agent, "deploy") && (
                  <button className="btn" onClick={() => onAction("deploy")}>
                    {agent.configuration_drift ? "Deploy update" : "Deploy"}
                  </button>
                )}
              </>
            }
          />
          <AgentDetailSection
            icon={<KeyRound size={17} />}
            title={t("Access channels")}
            description={t("Control API access and connect this agent to Computer or Mobile.")}
            actions={
              <>
                {agentCan(agent, "manage_access") && (
                  <button className="btn" onClick={() => onAction("access")}>{t("Manage access")}</button>
                )}
                {agentCan(agent, "manage_access") && (
                  <button className="btn" onClick={() => onAction("mobile")}>
                    <Smartphone size={14} />{t("Mobile")}</button>
                )}
              </>
            }
          />
          <AgentDetailSection
            icon={<RefreshCw size={17} />}
            title={t("Activity")}
            description={t("Review deployment, runtime, access, and configuration events.")}
            actions={
              <button className="btn" onClick={() => onAction("activity")}>{t("View activity")}</button>
            }
          />
          <AgentDetailSection
            icon={<Settings size={17} />}
            title={t("Agent settings")}
            description={t("Manage access and resource settings.")}
            actions={
              <>
                {agentCan(agent, "share") && (
                  <ResourceSharingAction>
                  <button className="btn" onClick={onShare}>
                    <Share2 size={14} />{t("Share")}</button>
                  </ResourceSharingAction>
                )}
                {agentCan(agent, "settings") && (
                  <button className="btn" onClick={() => onAction("settings")}>{t("Settings")}</button>
                )}
              </>
            }
          />
        </div>
      </div>
    </AgentModal>
  );
}

function AgentDetailSection({
  icon,
  title,
  description,
  actions,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  actions: React.ReactNode;
}) {
  useLocale();
  return (
    <div className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 gap-3">
        <div className="mt-0.5 text-muted">{icon}</div>
        <div className="min-w-0">
          <div className="font-medium text-ink">{title}</div>
          <div className="mt-1 break-words text-sm text-muted">
            {description}
          </div>
        </div>
      </div>
      <div className="flex shrink-0 flex-wrap gap-2 pl-7 sm:pl-0">
        {actions}
      </div>
    </div>
  );
}

function LoadErrorState({
  title,
  description,
  onRetry,
}: {
  title: string;
  description: string;
  onRetry: () => void;
}) {
  useLocale();
  return (
    <div className="flex min-h-48 flex-col items-center justify-center rounded-xl border border-red-200 bg-red-50/60 px-6 py-10 text-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-red-200 bg-white text-danger">
        <AlertCircle size={18} />
      </div>
      <h3 className="mt-4 text-sm font-semibold text-ink">{title}</h3>
      <p className="mt-1.5 max-w-md text-sm leading-6 text-muted">
        {friendlyLoadError(description)}
      </p>
      <button className="btn mt-4" onClick={onRetry}>
        <RefreshCw size={15} />{t("Retry")}</button>
      <details className="mt-3 text-left text-xs text-muted">
        <summary className="cursor-pointer">{t("Technical details")}</summary>
        <div className="mt-2 max-w-lg break-all rounded-md border border-line bg-white p-2 font-mono">
          {description}
        </div>
      </details>
    </div>
  );
}

function CreateAgentModal({
  apiContext,
  queryClient,
  onCreated,
  onClose,
}: {
  apiContext: Parameters<typeof api.createAgent>[0];
  queryClient: ReturnType<typeof useQueryClient>;
  onCreated: (agent: Agent) => void;
  onClose: () => void;
}) {
  useLocale();
  const { projects, projectId } = useAuth();
  const [agentName, setAgentName] = useState("");
  const [ownership, setOwnership] = useState<import("../lib/types").ResourceOwnershipInput>(
    projectId ? { scope: "project", project_id: projectId } : { scope: "organization", project_id: null },
  );
  const inputRef = useRef<HTMLInputElement>(null);
  const agentNameIsValid = agentNameRegex.test(agentName.trim());
  const createAgent = useMutation({
    mutationFn: () => api.createAgent(apiContext, { name: agentName.trim(), ownership }),
    onSuccess: async (agent) => {
      toast.success(t("Agent created"));
      await queryClient.invalidateQueries({ queryKey: ["agents"] });
      onCreated(agent);
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Failed to create agent"),
      ),
  });

  function submit() {
    if (!agentNameIsValid) {
      toast.error(agentNameHint);
      return;
    }
    createAgent.mutate();
  }

  return (
    <NexilumeDialog
      open
      title={t("Create Agent")}
      eyebrow={t("Agent identity")}
      description={t("Name the Agent now. Runtime setup continues in its dedicated control surface.")}
      busy={createAgent.isPending}
      initialFocusRef={inputRef}
      onClose={onClose}
      footer={
        <>
          <button
            className="btn"
            type="button"
            onClick={onClose}
            disabled={createAgent.isPending}
          >{t("Cancel")}</button>
          <button
            className="btn btn-primary"
            type="button"
            onClick={submit}
            disabled={createAgent.isPending || !agentNameIsValid}
          >
            {createAgent.isPending && (
              <Loader2 size={16} className="animate-spin" />
            )}{t("Create Agent")}{!createAgent.isPending && <ChevronRight size={15} />}
          </button>
        </>
      }
    >
      <form
        className="grid gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Field label={t("Agent name")}>
          <input
            ref={inputRef}
            className="input"
            placeholder={t("support-agent")}
            value={agentName}
            onChange={(event) => setAgentName(event.target.value)}
            pattern={agentNamePattern}
            title={agentNameHint}
            required
          />
        </Field>
        <ResourceOwnershipPicker projects={projects} value={ownership} onChange={setOwnership} />
        <div className="text-xs text-muted">{agentNameHint}</div>
        <div className="border border-line bg-paper p-3 text-sm leading-6 text-muted">{t("The Agent remains a draft until you choose Nexus Container or OpenWrt and deploy a healthy Runtime.")}</div>
      </form>
    </NexilumeDialog>
  );
}

function AgentContainerModal({
  agent,
  apiContext,
  queryClient,
  onContinue,
  onClose,
}: {
  agent: Agent;
  apiContext: Parameters<typeof api.createAgent>[0];
  queryClient: ReturnType<typeof useQueryClient>;
  onContinue: () => void;
  onClose: () => void;
}) {
  useLocale();
  const [imageRef, setImageRef] = useState("");
  const [uploadImageRef, setUploadImageRef] = useState("");
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [pairingCode, setPairingCode] = useState("");

  const runtime = useQuery({
    queryKey: ["agent-runtime-status", apiContext, agent.id],
    queryFn: () => api.agentRuntimeStatus(apiContext, agent.id),
    enabled: Boolean(agent.id),
  });
  const refreshAgentData = useAgentQueryRefresh("runtime");
  async function refreshAgentQueries() {
    await refreshAgentData(queryClient);
  }

  const registerImage = useMutation({
    mutationFn: () =>
      api.registerAgentRuntimeImage(apiContext, agent.id, {
        image_ref: imageRef.trim(),
      }),
    onSuccess: async (image) => {
      await api.setCurrentAgentRuntimeImage(apiContext, agent.id, image.id);
      toast.success(t("Container image registered"));
      setImageRef("");
      await refreshAgentQueries();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : t("Failed to register container image"),
      ),
  });

  const uploadImage = useMutation({
    mutationFn: () => {
      if (!imageFile) throw new Error("Select a Docker image tar file first");
      return api.uploadAgentRuntimeImage(
        apiContext,
        agent.id,
        imageFile,
        uploadImageRef,
      );
    },
    onSuccess: async (image) => {
      await api.setCurrentAgentRuntimeImage(apiContext, agent.id, image.id);
      toast.success(t("Container image uploaded"));
      setImageFile(null);
      setUploadImageRef("");
      await refreshAgentQueries();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : t("Failed to upload container image"),
      ),
  });

  const createPairingCode = useMutation({
    mutationFn: () => api.createEdgePairingCode(apiContext),
    onSuccess: (result) => {
      setPairingCode(result.pairing_code);
      toast.success(t("OpenWrt pairing code created"));
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : t("Failed to create pairing code"),
      ),
  });

  const currentEdgeDeployment =
    runtime.data?.deployments.find(
      (deployment) =>
        isOpenWrtRuntime(deployment.runtime_kind) &&
        deployment.status === "active",
    ) ??
    runtime.data?.deployments.find((deployment) =>
      isOpenWrtRuntime(deployment.runtime_kind),
    );

  return (
    <AgentModal
      title={t("Container {{0}}", { 0: agent.name })}
      description={
        agent.current_image_ref ||
        t("{{0}} / no current container", { 0: compactId(agent.id) })
      }
      onClose={onClose}
      maxWidth="max-w-6xl"
    >
      <div className="grid gap-5">
        <div className="rounded-lg border border-blue-200 bg-blue-50/60 p-4">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div className="flex items-center gap-2 font-medium text-ink">
                <MonitorPlay size={17} />{" "}{t("Use my OpenWrt Agent")}</div>
              <p className="mt-1 max-w-2xl text-sm text-muted">{t("Run this Agent on your own nexus_openwrt node through Direct IPv6 or an outbound Relay tunnel. Relay mode needs no public inbound port or Docker runtime.")}</p>
            </div>
            <Badge
              tone={isOpenWrtRuntime(agent.runtime_kind) ? "success" : "info"}
            >
              {isOpenWrtRuntime(agent.runtime_kind)
                ? t("Connected")
                : t("User hosted")}
            </Badge>
          </div>
          {isOpenWrtRuntime(agent.runtime_kind) ? (
            <div className="mt-4 grid gap-3 md:grid-cols-3 xl:grid-cols-5">
              <SummaryBox
                label={t("Router")}
                value={agent.edge_router_id || "OpenWrt"}
              />
              <SummaryBox
                label={t("Transport")}
                value={
                  agent.runtime_kind === "openwrt_relay"
                    ? "Relay · outbound only"
                    : "Direct IPv6 · mTLS"
                }
              />
              <SummaryBox
                label={t("Connection")}
                value={
                  currentEdgeDeployment
                    ? `${currentEdgeDeployment.status} · ${currentEdgeDeployment.health_status}`
                    : "Loading"
                }
              />
              {agent.runtime_kind === "openwrt_relay" && (
                <SummaryBox
                  label={t("Relay / lease")}
                  value={
                    currentEdgeDeployment
                      ? `${currentEdgeDeployment.edge_relay_id || "Relay"} · ${currentEdgeDeployment.edge_lease_expires_at ? formatDate(currentEdgeDeployment.edge_lease_expires_at) : "lease unavailable"}`
                      : "Loading"
                  }
                />
              )}
              <SummaryBox label={t("Compute charge")} value="¥0 Docker" />
            </div>
          ) : (
            <div className="mt-4 grid gap-3">
              <div className="border border-line bg-white p-3 text-sm text-muted">
                <strong className="block text-ink">{t("OpenWrt managed provisioning")}</strong>
                <span className="mt-1 block">{t("The current Nexus Connector creates eligible Agents automatically after Router sync. Manual binding from older connector versions is no longer supported in the workspace UI.")}</span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  className="btn"
                  onClick={() => createPairingCode.mutate()}
                  disabled={createPairingCode.isPending}
                >
                  {createPairingCode.isPending ? (
                    <Loader2 size={15} className="animate-spin" />
                  ) : (
                    <KeyRound size={15} />
                  )}{t("Generate pairing code")}</button>
                {pairingCode && (
                  <div className="flex min-w-0 items-center gap-2 rounded-md border border-line bg-white px-3 py-2">
                    <code className="min-w-0 break-all text-xs text-ink">
                      {pairingCode}
                    </code>
                    <button
                      className="btn h-8 shrink-0 px-2"
                      onClick={() => copy(pairingCode)}
                      aria-label={t("Copy pairing code")}
                    >
                      <Copy size={14} />
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3 text-xs uppercase tracking-wide text-muted">
          <span className="h-px flex-1 bg-line" />{t("Or use Nexus Docker")}<span className="h-px flex-1 bg-line" />
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          <SummaryBox
            label={t("Default image")}
            value={agent.current_image_ref || "No container"}
          />
          <SummaryBox
            label={t("Version")}
            value={agent.current_image_version || "Unversioned"}
          />
          <SummaryBox
            label={t("Runtime")}
            value={agent.runtime_status || "not_deployed"}
          />
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-md border border-line p-4">
            <div className="mb-3 font-medium text-ink">{t("Register image reference")}</div>
            <div className="grid gap-3">
              <input
                className="input font-mono"
                placeholder={t("registry.example.com/team/agent:v1")}
                value={imageRef}
                onChange={(event) => setImageRef(event.target.value)}
              />
              <button
                className="btn btn-primary w-fit"
                onClick={() => registerImage.mutate()}
                disabled={registerImage.isPending || !imageRef.trim()}
              >
                {registerImage.isPending ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <PackagePlus size={16} />
                )}{t("Register container")}</button>
            </div>
            <div className="mt-2 text-xs text-muted">{t("Use a real Docker image with an explicit tag or digest. Docker runner verifies local image or registry manifest availability.")}</div>
          </div>

          <div className="rounded-md border border-line p-4">
            <div className="mb-3 font-medium text-ink">{t("Upload image tar")}</div>
            <div className="grid gap-3">
              <input
                className="input"
                type="file"
                accept=".tar,application/x-tar,application/octet-stream"
                onChange={(event) =>
                  setImageFile(event.target.files?.[0] ?? null)
                }
              />
              <input
                className="input font-mono"
                placeholder={t("Optional image ref override")}
                value={uploadImageRef}
                onChange={(event) => setUploadImageRef(event.target.value)}
              />
              <div className="flex flex-wrap items-center gap-3">
                <button
                  className="btn btn-primary"
                  onClick={() => uploadImage.mutate()}
                  disabled={uploadImage.isPending || !imageFile}
                >
                  {uploadImage.isPending ? (
                    <Loader2 size={16} className="animate-spin" />
                  ) : (
                    <Upload size={16} />
                  )}{t("Upload image")}</button>
                {imageFile && (
                  <span className="max-w-full truncate text-xs text-muted">
                    {imageFile.name} / {formatFileSize(imageFile.size)}
                  </span>
                )}
              </div>
            </div>
            <div className="mt-2 text-xs text-muted">{t("Upload a tar archive produced by `docker save`. Nexus loads it into local Docker, registers the loaded image, and sets it as current.")}</div>
          </div>
        </div>

        {(runtime.data?.images ?? []).length === 0 ? (
          <EmptyState
            title={t("No containers")}
            description={t("Register a Docker image that runs the agent MCP server.")}
          />
        ) : (
          <AgentRuntimeImages agentId={agent.id} apiContext={apiContext} images={runtime.data?.images ?? []}
            currentImageId={agent.current_image_id} canManage={agentCan(agent, "configure_runtime")}
            onChanged={refreshAgentQueries} />
        )}
        {agent.current_image_id && (
          <div className="flex flex-col gap-3 rounded-lg border border-blue-200 bg-blue-50 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="font-medium text-ink">{t("Runtime image ready")}</div>
              <div className="text-sm text-muted">{t("Continue to deploy this image and verify runtime health.")}</div>
            </div>
            <button className="btn btn-primary shrink-0" onClick={onContinue}>{t("Continue to deploy")}<ChevronRight size={15} />
            </button>
          </div>
        )}
      </div>
    </AgentModal>
  );
}

function AgentAccessModal({
  agent,
  apiContext,
  onFinish,
  onClose,
}: {
  agent: Agent;
  apiContext: Parameters<typeof api.createAgent>[0];
  onFinish: () => void;
  onClose: () => void;
}) {
  useLocale();
  const [plaintextKey, setPlaintextKey] = useState("");
  const managementCopy = useResourceManagementCopy();
  const [mcpPayload, setMcpPayload] = useState<AgentMcpExport | null>(null);

  const logs = useQuery({
    queryKey: ["agent-logs", apiContext, agent.id],
    queryFn: () => api.agentLogs(apiContext, agent.id, 50),
    enabled: Boolean(agent.id),
  });

  const createKey = useMutation({
    mutationFn: () => api.createAgentKey(apiContext, agent.id),
    onSuccess: (key) => {
      setPlaintextKey(key.plaintext_key);
      exportMcp.mutate();
      toast.success(t("Agent API key created"));
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Failed to create key"),
      ),
  });

  const exportMcp = useMutation({
    mutationFn: () => api.exportAgentMcp(apiContext, agent.id),
    onSuccess: setMcpPayload,
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Failed to export MCP config"),
      ),
  });

  const logColumns = useMemo<Array<ColumnDef<AgentLog, unknown>>>(
    () => [
      {
        header: t("Time"),
        cell: ({ row }) => formatDate(row.original.created_at),
      },
      {
        header: t("Level"),
        cell: ({ row }) => (
          <StatusBadge status={row.original.level || "info"} />
        ),
      },
      { header: t("Message"), cell: ({ row }) => row.original.message },
    ],
    [getLocale()],
  );

  return (
    <AgentModal
      title={t("Access {{0}}", { 0: agent.name })}
      description={`${compactId(agent.id)} / ${agentOwnershipLabel(agent)}`}
      onClose={onClose}
      maxWidth="max-w-6xl"
    >
      <div className="grid gap-5">
        {plaintextKey && (
          <KeyResultCard
            title={t("Save this agent key now")}
            value={plaintextKey}
            onDismiss={() => setPlaintextKey("")}
          />
        )}

        <div className="grid gap-4">
          <div className="rounded-md border border-line p-4">
            <div className="mb-3 font-medium text-ink">{t("Scoped API key")}</div>
            <button
              className="btn"
              onClick={() => createKey.mutate()}
              disabled={createKey.isPending}
            >
              <KeyRound size={16} />{t("Create key")}</button>
          </div>
        </div>

        <AgentMcpSetup
          config={mcpPayload}
          plaintextKey={plaintextKey}
          loading={exportMcp.isPending}
          onLoad={() => exportMcp.mutate()}
          onForgetPlaintext={() => setPlaintextKey("")}
        />

        {(logs.data ?? []).length === 0 ? (
          <EmptyState
            title={t("No recent events")}
            description={managementCopy.agentEventsEmpty}
          />
        ) : (
          <DataTable data={logs.data ?? []} columns={logColumns} />
        )}
        <div className="flex justify-end">
          <button className="btn btn-primary" onClick={onFinish}>{t("Finish setup")}</button>
        </div>
      </div>
    </AgentModal>
  );
}

function AgentActivityModal({
  agent,
  apiContext,
  onClose,
}: {
  agent: Agent;
  apiContext: Parameters<typeof api.agentLogs>[0];
  onClose: () => void;
}) {
  useLocale();
  const logs = useQuery({
    queryKey: ["agent-logs", apiContext, agent.id],
    queryFn: () => api.agentLogs(apiContext, agent.id, 100),
    enabled: Boolean(agent.id),
  });
  const runs = useQuery<AgentDisplayRun[]>({
    queryKey: ["agent-display-runs", apiContext, agent.id],
    queryFn: () => api.agentDisplayRuns(apiContext, agent.id),
    enabled: Boolean(agent.id),
  });
  const memories = useQuery<AgentMemoryItem[]>({
    queryKey: ["agent-memory", apiContext, agent.id],
    queryFn: () => api.agentMemory(apiContext, agent.id),
    enabled: Boolean(agent.id),
  });
  return (
    <AgentModal
      title={t("Observability · {{0}}", { 0: agent.name })}
      description={t("Sanitized Runs, caller-scoped lineage, Memory, Output status, and runtime activity")}
      onClose={onClose}
      maxWidth="max-w-6xl"
    >
      <div className="mb-5 grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(20rem,0.6fr)]">
        <section className="border border-line">
          <div className="border-b border-line px-4 py-3 font-medium text-ink">{t("Invocation Runs")}</div>
          <div className="divide-y divide-line">
            {(runs.data ?? []).slice(0, 20).map((run) => (
              <div
                key={run.id}
                className="grid gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_7rem_7rem_7rem] sm:items-center"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-ink">
                    {run.title || compactId(run.id)}
                  </div>
                  <div className="mt-1 font-mono text-[11px] text-muted">
                    {run.caller} / {formatDate(run.started_at)}
                  </div>
                </div>
                <StatusBadge status={run.computer_status} />
                <StatusBadge status={run.mobile_status} />
                <StatusBadge status={run.status} />
              </div>
            ))}
            {!runs.isLoading && (runs.data ?? []).length === 0 && (
              <div className="px-4 py-6 text-sm text-muted">{t("No invocation Runs yet.")}</div>
            )}
          </div>
        </section>
        <section className="border border-line">
          <div className="border-b border-line px-4 py-3 font-medium text-ink">{t("Memory lineage")}</div>
          <div className="divide-y divide-line">
            {(memories.data ?? []).slice(0, 10).map((memory) => (
              <div key={memory.id} className="px-4 py-3">
                <div className="line-clamp-2 text-sm text-ink">
                  {memory.content_text || humanizeToken(memory.memory_type)}
                </div>
                <div className="mt-1 font-mono text-[11px] uppercase tracking-[0.1em] text-muted">
                  {memory.scope} / {memory.consent_status} /{" "}
                  {memory.source_run_id
                    ? compactId(memory.source_run_id)
                    : t("no run")}
                </div>
              </div>
            ))}
            {!memories.isLoading && (memories.data ?? []).length === 0 && (
              <div className="px-4 py-6 text-sm text-muted">{t("No memory lineage yet.")}</div>
            )}
          </div>
        </section>
      </div>
      {logs.isLoading ? (
        <AgentListSkeleton compact />
      ) : logs.isError ? (
        <LoadErrorState
          title={t("Activity unavailable")}
          description={
            logs.error instanceof Error
              ? logs.error.message
              : t("Agent activity could not be loaded.")
          }
          onRetry={() => void logs.refetch()}
        />
      ) : (logs.data ?? []).length === 0 ? (
        <EmptyState
          title={t("No activity yet")}
          description={t("Agent runtime, deployment, and access events will appear here.")}
        />
      ) : (
        <div className="divide-y divide-line rounded-lg border border-line">
          {(logs.data ?? []).map((log) => (
            <div key={log.id} className="flex gap-3 px-4 py-3">
              <span
                className={`mt-2 h-2 w-2 shrink-0 rounded-full ${log.level === "error" ? "bg-danger" : "bg-accent"}`}
              />
              <div className="min-w-0 flex-1">
                <div className="break-words text-sm text-ink">
                  {log.message}
                </div>
                <div className="mt-1 text-xs text-muted">
                  {formatDate(log.created_at)}
                </div>
              </div>
              <StatusBadge status={humanizeToken(log.level || "info")} />
            </div>
          ))}
        </div>
      )}
    </AgentModal>
  );
}

function AgentDeployModal({
  agent,
  apiContext,
  queryClient,
  onContinue,
  onClose,
}: {
  agent: Agent;
  apiContext: Parameters<typeof api.agentRuntimeStatus>[0];
  queryClient: ReturnType<typeof useQueryClient>;
  onContinue: () => void;
  onClose: () => void;
}) {
  useLocale();
  const runtimeEnv = "prod";
  const [activeJobId, setActiveJobId] = useState("");
  const handledJobId = useRef("");

  const runtime = useQuery({
    queryKey: ["agent-runtime-status", apiContext, agent.id],
    queryFn: () => api.agentRuntimeStatus(apiContext, agent.id),
    enabled: Boolean(agent.id),
    refetchInterval: activeJobId ? 1500 : false,
  });
  const operationJob = useQuery<Job>({
    queryKey: ["job", apiContext, activeJobId],
    queryFn: () => api.job(apiContext, activeJobId),
    enabled: Boolean(activeJobId),
    refetchInterval: (query) => {
      const jobStatus = query.state.data?.status;
      return jobStatus &&
        ["succeeded", "failed", "canceled"].includes(jobStatus)
        ? false
        : 1500;
    },
  });
  const refreshAgentData = useAgentQueryRefresh("runtime");
  async function refreshAgentQueries() {
    await refreshAgentData(queryClient);
  }

  useEffect(() => {
    const job = operationJob.data;
    if (
      !job ||
      !["succeeded", "failed", "canceled"].includes(job.status) ||
      handledJobId.current === job.id
    )
      return;
    handledJobId.current = job.id;
    void refreshAgentQueries();
    if (job.status === "succeeded")
      toast.success(t("Runtime operation completed"));
    else toast.error(job.error_message || t("Runtime operation failed"));
  }, [operationJob.data]);

  const deployRuntime = useMutation({
    mutationFn: () =>
      api.deployAgentRuntime(apiContext, agent.id, { env: runtimeEnv }),
    onSuccess: async (deployment) => {
      toast.success(
        deployment.job_id
          ? t("Runtime deploy queued: {{0}}", { 0: compactId(deployment.job_id) })
          : t("Runtime deploy queued"),
      );
      setActiveJobId(deployment.job_id || "");
      await refreshAgentQueries();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Failed to deploy runtime"),
      ),
  });

  const stopRuntime = useMutation({
    mutationFn: () =>
      isOpenWrtRuntime(currentRuntime?.runtime_kind)
        ? api.disconnectAgentEdgeRuntime(apiContext, agent.id, runtimeEnv)
        : api.stopAgentRuntime(apiContext, agent.id, { env: runtimeEnv }),
    onSuccess: async (deployment) => {
      toast.success(
        isOpenWrtRuntime(deployment.runtime_kind)
          ? t("OpenWrt Agent disconnected")
          : t("Runtime stop queued"),
      );
      setActiveJobId(deployment.job_id || "");
      await Promise.all([
        refreshAgentQueries(),
        queryClient.invalidateQueries({
          queryKey: ["edge-agent-registrations"],
        }),
      ]);
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Failed to stop runtime"),
      ),
  });

  const healthCheckRuntime = useMutation({
    mutationFn: () =>
      api.healthCheckAgentRuntime(apiContext, agent.id, { env: runtimeEnv }),
    onSuccess: async (deployment) => {
      toast.success(t("Runtime health check queued"));
      setActiveJobId(deployment.job_id || "");
      await refreshAgentQueries();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Failed to run health check"),
      ),
  });

  const runtimeColumns = useMemo<
    Array<ColumnDef<AgentRuntimeDeployment, unknown>>
  >(
    () => [
      {
        header: t("Env"),
        cell: ({ row }) => (
          <span className="font-mono text-xs">{row.original.env}</span>
        ),
      },
      {
        header: t("Target"),
        cell: ({ row }) =>
          isOpenWrtRuntime(row.original.runtime_kind)
            ? `OpenWrt · ${row.original.runtime_kind === "openwrt_relay" ? "Relay" : "IPv6"} · ${row.original.edge_router_id || "edge"}`
            : compactId(row.original.image_ref || ""),
      },
      {
        header: t("Status"),
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
      {
        header: t("Health"),
        cell: ({ row }) => <StatusBadge status={row.original.health_status} />,
      },
      {
        header: t("Computer"),
        cell: () => <span className="text-xs text-muted">{t("Caller scoped")}</span>,
      },
      {
        header: t("MCP URL"),
        cell: ({ row }) => (
          <span className="font-mono text-xs">
            {row.original.internal_mcp_url || "-"}
          </span>
        ),
      },
      {
        header: t("Updated"),
        cell: ({ row }) => formatDate(row.original.updated_at),
      },
    ],
    [getLocale()],
  );
  const currentRuntime =
    (runtime.data?.deployments ?? []).find(
      (deployment) => deployment.env === runtimeEnv,
    ) ?? null;
  const operationBusy = Boolean(
    operationJob.data &&
    ["queued", "running"].includes(operationJob.data.status),
  );
  const runtimeReady =
    currentRuntime?.status === "active" &&
    currentRuntime.health_status === "healthy";

  return (
    <AgentModal
      title={t("Deploy {{0}}", { 0: agent.name })}
      description={
        isOpenWrtRuntime(agent.runtime_kind)
          ? t("{{0}} / user-hosted {{1}}", { 0: agent.edge_router_id, 1: agent.runtime_kind === "openwrt_relay" ? "Relay" : "IPv6" })
          : agent.current_image_ref ||
            t("{{0}} / no current container", { 0: compactId(agent.id) })
      }
      onClose={onClose}
      maxWidth="max-w-6xl"
    >
      <div className="grid gap-5">
        {operationJob.data && (
          <RuntimeOperationBanner job={operationJob.data} />
        )}
        <div className="grid gap-4 md:grid-cols-3">
          <SummaryBox
            label={
              isOpenWrtRuntime(agent.runtime_kind)
                ? t("Runtime location")
                : t("Default image")
            }
            value={
              isOpenWrtRuntime(agent.runtime_kind)
                ? `OpenWrt · ${agent.runtime_kind === "openwrt_relay" ? "Relay" : "IPv6"} · ${agent.edge_router_id || "Edge"}`
                : agent.current_image_ref || "No container"
            }
          />
          <SummaryBox
            label={t("Version")}
            value={agent.current_image_version || "Unversioned"}
          />
          <SummaryBox
            label={t("Runtime")}
            value={
              currentRuntime?.status
                ? humanizeToken(currentRuntime.status)
                : runtimeLabel(agent)
            }
          />
        </div>

        <div className="rounded-md border border-line p-4">
          <div className="mb-3 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <div className="font-medium text-ink">
                {isOpenWrtRuntime(agent.runtime_kind)
                  ? t("OpenWrt {{0}} runtime", { 0: agent.runtime_kind === "openwrt_relay" ? "Relay" : "IPv6" })
                  : t("Deploy default image")}
              </div>
              <div className="text-xs text-muted">
                {isOpenWrtRuntime(agent.runtime_kind)
                  ? agent.runtime_kind === "openwrt_relay"
                    ? t("The router owns the Agent lifecycle and reaches Nexus through an outbound authenticated Relay tunnel; no public inbound port is needed.")
                    : t("The remote Agent owns its process lifecycle; Nexus verifies the IPv6, JWT, and mTLS call path.")
                  : t("Deploy uses the default image selection. It may differ from the running image.")}
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <div className="flex h-9 items-center rounded-md border border-line bg-slate-50 px-3 text-sm">
                <span className="text-muted">{t("Environment")}</span>
                <span className="ml-2 font-medium text-ink">{t("Production")}</span>
              </div>
              {!isOpenWrtRuntime(agent.runtime_kind) && (
                <button
                  className="btn btn-primary h-9 px-3"
                  onClick={() => deployRuntime.mutate()}
                  disabled={
                    deployRuntime.isPending ||
                    operationBusy ||
                    !agent.current_image_id
                  }
                >
                  {deployRuntime.isPending ? (
                    <Loader2 size={15} className="animate-spin" />
                  ) : (
                    <Rocket size={15} />
                  )}
                  {agent.configuration_drift
                    ? t("Deploy update")
                    : currentRuntime?.status === "active"
                      ? t("Redeploy")
                      : t("Deploy")}
                </button>
              )}
              <button
                className="btn h-9 px-3"
                onClick={() => healthCheckRuntime.mutate()}
                disabled={
                  healthCheckRuntime.isPending ||
                  operationBusy ||
                  !currentRuntime
                }
              >
                <RefreshCw size={15} />{t("Health")}</button>
              <button
                className="btn h-9 px-3"
                onClick={() => stopRuntime.mutate()}
                disabled={
                  stopRuntime.isPending ||
                  operationBusy ||
                  !currentRuntime ||
                  !["active", "deploying"].includes(currentRuntime.status)
                }
              >
                <Square size={15} />
                {isOpenWrtRuntime(currentRuntime?.runtime_kind)
                  ? t("Disconnect")
                  : t("Stop")}
              </button>
            </div>
          </div>
          {!agent.current_image_id && !isOpenWrtRuntime(agent.runtime_kind) && (
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{t("Register a container and set it as current before deploying.")}</div>
          )}
        </div>

        {!isOpenWrtRuntime(agent.runtime_kind) && (
          <div className="border-y border-line px-1 py-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <div className="font-medium text-ink">{t("Caller-owned Computer")}</div>
                <div className="text-xs text-muted">{t("Computers are selected by each caller at invocation time. Runtime deployment never receives SSH details or a shared developer workspace.")}</div>
              </div>
              <Link className="btn h-9 shrink-0 px-3" to="/remote-workspaces">
                <Terminal size={14} />{t("My Computers")}</Link>
            </div>
          </div>
        )}

        {(runtime.data?.deployments ?? []).length === 0 ? (
          <EmptyState
            title={t("No runtime deployments")}
            description={t("Runtime deployments will appear after deploy is queued.")}
          />
        ) : (
          <DataTable
            data={runtime.data?.deployments ?? []}
            columns={runtimeColumns}
          />
        )}
        {runtimeReady && !operationBusy && (
          <div className="flex flex-col gap-3 rounded-lg border border-green-200 bg-green-50 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="font-medium text-ink">{t("Runtime is healthy")}</div>
              <div className="text-sm text-muted">{t("Continue to create scoped access and connect clients.")}</div>
            </div>
            <button className="btn btn-primary shrink-0" onClick={onContinue}>{t("Continue to access")}<ChevronRight size={15} />
            </button>
          </div>
        )}
      </div>
    </AgentModal>
  );
}

function RuntimeOperationBanner({ job }: { job: Job }) {
  useLocale();
  const terminal = ["succeeded", "failed", "canceled"].includes(job.status);
  const failed = ["failed", "canceled"].includes(job.status);
  return (
    <div
      className={`flex items-start gap-3 rounded-lg border p-4 ${failed ? "border-red-200 bg-red-50" : terminal ? "border-green-200 bg-green-50" : "border-blue-200 bg-blue-50"}`}
    >
      {!terminal ? (
        <Loader2 className="mt-0.5 animate-spin text-accent" size={17} />
      ) : failed ? (
        <AlertCircle className="mt-0.5 text-danger" size={17} />
      ) : (
        <ShieldCheck className="mt-0.5 text-success" size={17} />
      )}
      <div>
        <div className="font-medium text-ink">
          {humanizeToken(job.job_type.replace("agents.runtime.", ""))} ·{" "}
          {humanizeToken(job.status)}
        </div>
        <div className="mt-1 text-sm text-muted">
          {failed
            ? job.error_message || t("The operation did not complete.")
            : terminal
              ? t("The runtime state has been refreshed.")
              : t("Nexus is tracking this operation automatically. You can keep this dialog open.")}
        </div>
      </div>
    </div>
  );
}

function AgentSettingsModal({
  agent,
  apiContext,
  queryClient,
  onClose,
}: {
  agent: Agent;
  apiContext: ApiContext;
  queryClient: ReturnType<typeof useQueryClient>;
  onClose: () => void;
}) {
  useLocale();
  const { projects } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState(agent.name);
  const [transferProjectId, setTransferProjectId] = useState(
    agent.project_id || "",
  );
  const [resources, setResources] = useState({ cpu: "1", memory: "512Mi" });
  const [computerRequirement, setComputerRequirement] = useState(
    agent.computer_requirement || "optional",
  );
  const [workspaceCapabilities, setWorkspaceCapabilities] = useState<string[]>(
    agent.workspace_capabilities ?? [],
  );
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    setName(agent.name);
    setTransferProjectId(agent.project_id || "");
    setComputerRequirement(agent.computer_requirement || "optional");
    setWorkspaceCapabilities(agent.workspace_capabilities ?? []);
  }, [
    agent.id,
    agent.project_id,
    agent.computer_requirement,
    agent.workspace_capabilities,
  ]);

  const refreshAgentData = useAgentQueryRefresh("settings");
  async function refreshAgentQueries() {
    await refreshAgentData(queryClient);
  }

  const setAgentResources = useMutation({
    mutationFn: () => api.setAgentResources(apiContext, agent.id, resources),
    onSuccess: async () => {
      toast.success(t("Resources updated"));
      await refreshAgentQueries();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Failed to update resources"),
      ),
  });

  const updateBasics = useMutation({
    mutationFn: () =>
      api.updateAgent(apiContext, agent.id, { name: name.trim() }),
    onSuccess: async () => {
      toast.success(t("Agent name updated"));
      await refreshAgentQueries();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Failed to update agent"),
      ),
  });

  const updateWorkspacePolicy = useMutation({
    mutationFn: () =>
      api.updateAgent(apiContext, agent.id, {
        computer_requirement: computerRequirement as
          "required" | "optional" | "disabled",
        workspace_capabilities:
          computerRequirement === "disabled" ? [] : workspaceCapabilities,
      }),
    onSuccess: async () => {
      toast.success(t("Workspace capability declaration updated"));
      await refreshAgentQueries();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : t("Failed to update Workspace policy"),
      ),
  });

  function toggleWorkspaceCapability(scope: string) {
    setWorkspaceCapabilities((current) =>
      current.includes(scope)
        ? current.filter((item) => item !== scope)
        : [...current, scope],
    );
  }

  const transferAgent = useMutation({
    mutationFn: () =>
      api.updateAgent(apiContext, agent.id, {
        project_id: transferProjectId || null,
        team_id: null,
      }),
    onSuccess: async () => {
      toast.success(t("Agent scope transferred"));
      await refreshAgentQueries();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Failed to transfer agent"),
      ),
  });

  const updateLifecycle = useMutation({
    mutationFn: (status: "active" | "disabled" | "archived") =>
      api.updateAgent(apiContext, agent.id, { status }),
    onSuccess: async () => {
      toast.success(t("Agent lifecycle updated"));
      await refreshAgentQueries();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Failed to update lifecycle"),
      ),
  });

  const cloneAgent = useMutation({
    mutationFn: () => api.cloneAgent(apiContext, agent.id),
    onSuccess: async (clone) => {
      toast.success(t("{{0}} created as a draft", { 0: clone.name }));
      await refreshAgentQueries();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Failed to clone agent"),
      ),
  });

  const deleteAgent = useMutation({
    mutationFn: () => api.deleteAgent(apiContext, agent.id),
    onSuccess: async () => {
      toast.success(t("Agent deleted"));
      await refreshAgentQueries();
      onClose();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Failed to delete agent"),
      ),
  });



  return (
    <AgentModal
      title={t("Settings {{0}}", { 0: agent.name })}
      description={`${compactId(agent.id)} / ${agentOwnershipLabel(agent)}`}
      onClose={onClose}
      maxWidth="max-w-5xl"
    >
      <div className="grid gap-5">
        <div className="rounded-lg border border-line p-4">
          <div className="mb-3 font-medium text-ink">{t("Basics")}</div>
          <div className="grid gap-4 md:grid-cols-2">
            <Field label={t("Agent name")}>
              <div className="flex gap-2">
                <input
                  className="input"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
                <button
                  className="btn shrink-0"
                  onClick={() => updateBasics.mutate()}
                  disabled={
                    updateBasics.isPending ||
                    !name.trim() ||
                    name.trim() === agent.name
                  }
                >{t("Save")}</button>
              </div>
            </Field>
            <Field label={t("Project")}>
              <div className="flex gap-2">
                <select
                  className="select"
                  value={transferProjectId}
                  onChange={(event) => setTransferProjectId(event.target.value)}
                >
                  <option value="">{t("All projects")}</option>
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </select>
                <button
                  className="btn shrink-0"
                  onClick={() => transferAgent.mutate()}
                  disabled={
                    transferAgent.isPending ||
                    transferProjectId === (agent.project_id || "")
                  }
                >{t("Transfer")}</button>
              </div>
            </Field>
          </div>
          <p className="mt-3 text-xs text-muted">{t("Stop the runtime before transferring an Agent to another project.")}</p>
        </div>

        <div className="border border-line bg-paper p-4">
          <div className="font-mono text-xs font-semibold uppercase tracking-[0.16em] text-ink">{t("Workspace capability declaration")}</div>
          <p className="mt-2 text-sm leading-6 text-muted">{t("These are maximum permissions requested by the Agent. Every caller must authorize a subset before the container receives a short-lived Workspace delegate.")}</p>
          <div className="mt-4 grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
            <Field label={t("Computer requirement")}>
              <select
                className="select"
                value={computerRequirement}
                onChange={(event) => setComputerRequirement(event.target.value)}
                disabled={agent.computer_declared_by_sdk}
              >
                <option value="optional">{t("Optional")}</option>
                <option value="required">{t("Required")}</option>
                <option value="disabled">{t("Disabled")}</option>
              </select>
            </Field>
            <fieldset
              disabled={
                computerRequirement === "disabled" ||
                agent.computer_declared_by_sdk ||
                updateWorkspacePolicy.isPending
              }
            >
              <legend className="text-xs font-semibold uppercase tracking-wide text-muted">{t("Requested permissions")}</legend>
              <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {workspaceCapabilityOptions.map((scope) => (
                  <label
                    key={scope}
                    className="flex min-h-11 items-center gap-3 border border-line bg-white px-3 py-2 text-sm text-ink"
                  >
                    <input
                      type="checkbox"
                      checked={workspaceCapabilities.includes(scope)}
                      onChange={() => toggleWorkspaceCapability(scope)}
                    />
                    <span>{workspaceScopeLabel(scope)}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          </div>
          <div className="mt-4 flex items-center justify-between gap-3 border-t border-line pt-4">
            <span className="text-xs text-muted">
              {agent.computer_declared_by_sdk
                ? t("Declared by SDK. Restart the Python Agent after changing its manifest.")
                : t("Changing this declaration never expands an existing caller grant automatically.")}
            </span>
            <button
              className="btn btn-primary shrink-0"
              onClick={() => updateWorkspacePolicy.mutate()}
              disabled={
                updateWorkspacePolicy.isPending ||
                agent.computer_declared_by_sdk
              }
            >
              {updateWorkspacePolicy.isPending && (
                <Loader2 size={15} className="animate-spin" />
              )}{t("Save declaration")}</button>
          </div>
        </div>

        <AgentSettingsPublication agent={agent} apiContext={apiContext} onSaved={refreshAgentQueries} />

        <div className="grid gap-4 lg:grid-cols-3">
          <div className="rounded-md border border-line p-4">
            <div className="mb-3 font-medium text-ink">{t("Ownership & access")}</div>
            <div className="grid gap-3">
              <div><ResourceOwnershipBadge ownership={agent.ownership} />{!agent.ownership && <span className="text-sm text-ink">{agentOwnershipLabel(agent)}</span>}</div>
              <p className="text-sm text-muted">{t("Ownership controls discovery. Roles and explicit grants control use and management.")}</p>
              <button className="btn w-fit" onClick={() => { onClose(); navigate(`/agents/${agent.id}/publish`); }}>{t("Manage access")}</button>
            </div>
          </div>
          <AgentPublicationSummary surface="settings-shortcut" agent={agent} onOpen={() => { onClose(); navigate(`/agents/${agent.id}/publish`); }} />
          <div className="rounded-md border border-line p-4">
            <div className="mb-3 font-medium text-ink">{t("Resources")}</div>
            <div className="grid gap-3">
              <input
                className="input"
                value={resources.cpu}
                onChange={(event) =>
                  setResources((current) => ({
                    ...current,
                    cpu: event.target.value,
                  }))
                }
              />
              <input
                className="input"
                value={resources.memory}
                onChange={(event) =>
                  setResources((current) => ({
                    ...current,
                    memory: event.target.value,
                  }))
                }
              />
              <button
                className="btn w-fit"
                onClick={() => setAgentResources.mutate()}
                disabled={setAgentResources.isPending}
              >{t("Set resources")}</button>
            </div>
          </div>
        </div>

        <div className="rounded-lg border border-line p-4">
          <div className="font-medium text-ink">{t("Lifecycle")}</div>
          <p className="mt-1 text-sm text-muted">{t("Disable access temporarily, archive completed work, or create a clean draft copy.")}</p>
          <div className="mt-4 flex flex-wrap gap-2">
            {["disabled", "archived"].includes(agent.status) ? (
              <button
                className="btn"
                onClick={() => updateLifecycle.mutate("active")}
                disabled={updateLifecycle.isPending}
              >{t("Enable")}</button>
            ) : (
              <>
                <button
                  className="btn"
                  onClick={() => updateLifecycle.mutate("disabled")}
                  disabled={updateLifecycle.isPending}
                >{t("Disable")}</button>
                <button
                  className="btn"
                  onClick={() => updateLifecycle.mutate("archived")}
                  disabled={updateLifecycle.isPending}
                >{t("Archive")}</button>
              </>
            )}
            <button
              className="btn"
              onClick={() => cloneAgent.mutate()}
              disabled={cloneAgent.isPending}
            >{t("Clone as draft")}</button>
          </div>
        </div>

        <div className="rounded-lg border border-red-200 bg-red-50/50 p-4">
          <div className="font-medium text-ink">{t("Delete agent")}</div>
          <p className="mt-1 text-sm text-muted">{t("Deletion is only allowed after all runtime operations are stopped.")}</p>
          {!confirmDelete ? (
            <button
              className="btn mt-4 border-red-200 text-danger"
              onClick={() => setConfirmDelete(true)}
            >{t("Delete agent")}</button>
          ) : (
            <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
              <span className="text-sm text-danger">{t("Delete")}{" "}{agent.name}{t("? This removes it from normal workspace views.")}</span>
              <button
                className="btn border-red-200 text-danger"
                onClick={() => deleteAgent.mutate()}
                disabled={deleteAgent.isPending}
              >{t("Confirm delete")}</button>
              <button className="btn" onClick={() => setConfirmDelete(false)}>{t("Cancel")}</button>
            </div>
          )}
        </div>
      </div>
    </AgentModal>
  );
}

function SummaryBox({ label, value }: { label: string; value: string }) {
  useLocale();
  return (
    <div className="rounded-md border border-line p-4">
      <div className="label">{label}</div>
      <div className="mt-2 break-all text-lg font-semibold text-ink">
        {value}
      </div>
    </div>
  );
}

function CopyPanel({ label, value }: { label: string; value: string }) {
  useLocale();
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_2rem] items-center gap-2 rounded-md border border-line bg-slate-50 p-2">
      <div className="min-w-0">
        <div className="text-xs font-semibold uppercase text-muted">
          {label}
        </div>
        <code className="mt-1 block truncate font-mono text-xs text-slate-800">
          {value || "-"}
        </code>
      </div>
      <button
        className="btn h-8 w-8 shrink-0 p-0"
        onClick={() => void copy(value)}
        disabled={!value}
      >
        <Copy size={14} />
      </button>
    </div>
  );
}

function defaultAgentSelection(agents: Agent[]) {
  const byRecentUpdate = [...agents].sort(
    (left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at),
  );
  return (
    byRecentUpdate.find(
      (agent) => getAgentNextAction(agent).action !== "overview",
    ) ??
    byRecentUpdate[0] ??
    null
  );
}

function filterAgents(
  agents: Agent[],
  filters: {
    search: string;
    status: AgentListFilter;
    ownership: OwnershipFilter;
    sort: AgentListSort;
  },
) {
  const keyword = filters.search.trim().toLowerCase();
  const filtered = agents.filter((agent) => {
    if (
      keyword &&
      ![
        agent.name,
        agent.current_image_ref,
        agent.current_image_version,
        agent.status,
        agentOwnershipLabel(agent),
      ]
        .join(" ")
        .toLowerCase()
        .includes(keyword)
    ) {
      return false;
    }
    if (filters.status !== "all") {
      const normalized = normalizeAgentStatus(agent);
      if (filters.status === "draft" && normalized !== "needs_setup")
        return false;
      if (filters.status === "active" && normalized !== "ready") return false;
      if (filters.status === "archived" && agent.status !== "archived")
        return false;
    }
    const ownershipScope = agent.ownership?.scope || (agent.project_id ? "project" : "organization");
    if (filters.ownership !== "all" && ownershipScope !== filters.ownership) return false;
    return true;
  });
  return filtered.sort((a, b) => {
    if (filters.sort === "name") return a.name.localeCompare(b.name);
    if (filters.sort === "created")
      return b.created_at.localeCompare(a.created_at);
    return b.updated_at.localeCompare(a.updated_at);
  });
}

function normalizeAgentStatus(agent: Agent) {
  if (agent.lifecycle_status)
    return agent.lifecycle_status === "draft"
      ? "needs_setup"
      : agent.lifecycle_status === "configured"
        ? t("ready")
        : agent.lifecycle_status;
  if (agent.status === "archived") return t("archived");
  if (!agent.current_image_id) return "needs_setup";
  if (agent.status === "active") return t("ready");
  return agent.status || t("unknown");
}

function agentLifecycle(agent: Agent): {
  label: string;
  tone: "success" | "warn" | "danger" | "muted" | "info";
} {
  const lifecycle = agent.lifecycle_status || agent.status;
  if (lifecycle === "archived") return { label: t("Archived"), tone: "muted" };
  if (lifecycle === "disabled") return { label: t("Disabled"), tone: "muted" };
  if (
    lifecycle === "draft" ||
    (!agent.current_image_id && !isOpenWrtRuntime(agent.runtime_kind))
  )
    return { label: t("Setup required"), tone: "warn" };
  if (lifecycle === "configured" || agent.status === "active")
    return { label: t("Configured"), tone: "success" };
  if (agent.status === "failed" || agent.status === "error")
    return { label: t("Action required"), tone: "danger" };
  return { label: humanizeToken(agent.status || "Draft"), tone: "info" };
}

function getAgentNextAction(agent: Agent): {
  label: string;
  description: string;
  action: AgentAction;
} {
  if (
    ["archived", "disabled"].includes(agent.lifecycle_status || agent.status)
  ) {
    return {
      label: t("Open details"),
      description: t("review this agent."),
      action: "overview",
    };
  }
  if (isOpenWrtRuntime(agent.runtime_kind)) {
    if (
      isAgentRunning(agent) &&
      ["degraded", "unhealthy", "unknown"].includes(
        (agent.runtime_health_status || "").toLowerCase(),
      ) &&
      agentCan(agent, "health_check")
    ) {
      return {
        label: t("Check edge health"),
        description: t("verify the registered IPv6 endpoint and JWT/mTLS path."),
        action: "deploy",
      };
    }
    return {
      label: t("Open details"),
      description: t("review the connected OpenWrt Agent."),
      action: "overview",
    };
  }
  if (!agent.current_image_id) {
    if (!agentCan(agent, "configure_runtime"))
      return {
        label: t("Open details"),
        description: t("review this agent."),
        action: "overview",
      };
    return {
      label: t("Configure runtime"),
      description:
        t("connect a container image before this agent can be deployed."),
      action: "container",
    };
  }
  if ((agent.runtime_status || "").toLowerCase() === "deploying") {
    return {
      label: t("Track deployment"),
      description: t("wait for the active deployment operation to complete."),
      action: "deploy",
    };
  }
  if (agent.configuration_drift && agentCan(agent, "deploy")) {
    return {
      label: t("Deploy update"),
      description:
        t("deploy the selected image to replace the currently running image."),
      action: "deploy",
    };
  }
  if (
    isAgentRunning(agent) &&
    ["degraded", "unhealthy", "unknown"].includes(
      (agent.runtime_health_status || "").toLowerCase(),
    ) &&
    agentCan(agent, "health_check")
  ) {
    return {
      label: t("Check health"),
      description: t("run a health check and review the runtime status."),
      action: "deploy",
    };
  }
  if (
    !isAgentRunning(agent) &&
    agent.status !== "archived" &&
    agentCan(agent, "deploy")
  ) {
    return {
      label: agent.runtime_status === "failed" ? t("Retry deploy") : t("Deploy"),
      description:
        t("deploy the configured runtime and verify that it is healthy."),
      action: "deploy",
    };
  }
  return {
    label: t("Open details"),
    description: t("review this agent."),
    action: "overview",
  };
}

function isAgentRunning(agent: Agent) {
  return ["active", "running"].includes(
    (agent.runtime_status || "").toLowerCase(),
  );
}

function runtimeLabel(agent: Agent) {
  if (isOpenWrtRuntime(agent.runtime_kind)) {
    const transport = agent.runtime_kind === "openwrt_relay" ? "Relay" : "IPv6";
    return agent.runtime_status
      ? t("OpenWrt {{0}} · {{1}}", { 0: transport, 1: humanizeToken(agent.runtime_status) })
      : t("OpenWrt {{0}}", { 0: transport });
  }
  if (!agent.current_image_id) return t("Not configured");
  if (!agent.runtime_status) return t("Not deployed");
  return humanizeToken(agent.runtime_status);
}

function isOpenWrtRuntime(runtimeKind?: string | null) {
  return runtimeKind === "openwrt_ipv6" || runtimeKind === "openwrt_relay";
}

function displayAgentName(agent: Agent) {
  const name = agent.name?.trim();
  return !name || name === "??" ? "Unnamed agent" : name;
}

function agentCan(agent: Agent, action: string) {
  if (!Array.isArray(agent.allowed_actions)) return true;
  return agent.allowed_actions.includes(action);
}

function agentProjectLabel(agent: Agent, projectName?: string) {
  if (!agent.project_id) return t("All projects");
  return projectName || t("Project unavailable");
}

function agentOwnershipLabel(agent: Agent) {
  if (agent.ownership?.label) return agent.ownership.label;
  return agent.project_id ? t("Project resource") : t("Organization shared");
}

function friendlyLoadError(description: string) {
  if (/internal server error/i.test(description)) {
    return "Nexus could not load this data. Retry now or review the technical details if the problem continues.";
  }
  return description || "This data could not be loaded. Please retry.";
}

function formatFileSize(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(
    Math.floor(Math.log(value) / Math.log(1024)),
    units.length - 1,
  );
  return `${(value / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}
