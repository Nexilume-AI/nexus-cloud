import { t, useLocale, getLocale } from "../localization";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  FlaskConical,
  History,
  PowerOff,
  RotateCcw,
  Share2,
  ShieldAlert,
  Trash2,
  Waypoints,
} from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "../app/AuthContext";
import { ProviderEntryActions, useSourceDiscovery } from '../components/SourceDiscovery';
import { api, type ApiContext } from "../lib/api";
import type {
  Deployment,
  ModelGroup,
  ModelGroupRoutingDraftSource,
  ModelGroupRoutingPreview,
  ModelGroupSource,
  TopologyPool,
} from "../lib/types";
import { formatDate, formatMoney } from "../lib/format";
import { EmptyState } from "../components/EmptyState";
import { StatusBadge } from "../components/Badge";
import { NexilumeTopology } from "../components/NexilumeTopology";
import { NexilumeDialog, NexilumeTabs } from "../components/NexilumeControls";
import {
  ShareResourceModal,
  ResourceSharingAction,
  type ShareResourceTarget,
} from "../components/ShareResourceModal";

type PoolRow = {
  id: string;
  name: string;
  displayName: string;
  visibility: string;
  status: string;
  routingStrategy: string;
  modelGroup: ModelGroup;
  memberships: ModelGroupSource[];
  sources: Deployment[];
  topology?: TopologyPool;
};
type SourceDangerAction = {
  kind: "disable" | "remove";
  source: Deployment;
} | null;

const STRATEGIES = [
  {
    value: "fallback",
    label: "Ordered fallback",
    help: "Try enabled Sources in an explicit, unique fallback order.",
  },
  {
    value: "best_health",
    label: "Best health",
    help: "Prefer the strongest current health state, then Source priority.",
  },
  {
    value: "lowest_cost",
    label: "Lowest cost",
    help: "Prefer the lowest current per-token price among callable Sources.",
  },
  {
    value: "lowest_latency",
    label: "Lowest latency",
    help: "Prefer the lowest latest measured latency among callable Sources.",
  },
  {
    value: "weighted",
    label: "Weighted traffic",
    help: "Distribute first-choice traffic proportionally across positive Source weights.",
  },
] as const;

export function ModelPoolPage() {
  useLocale();
  const sourceDiscovery=useSourceDiscovery();
  const { apiContext, isContextReady } = useAuth();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialView = searchParams.get("view");
  const [view, setViewState] = useState<"topology" | "pool" | "sources">(
    initialView === "pool" || initialView === "sources"
      ? initialView
      : "topology",
  );
  const [shareTarget, setShareTarget] = useState<ShareResourceTarget | null>(
    null,
  );
  const [dangerAction, setDangerAction] = useState<SourceDangerAction>(null);

  const deployments = useQuery({
    queryKey: ["deployments", apiContext],
    queryFn: () => api.deployments(apiContext),
    enabled: isContextReady,
  });
  const models = useQuery({
    queryKey: ["models", apiContext],
    queryFn: () => api.models(apiContext),
    enabled: isContextReady,
  });
  const topology = useQuery({
    queryKey: ["topology", apiContext],
    queryFn: () => api.topology(apiContext),
    enabled: isContextReady,
  });
  const poolRows = useMemo(
    () => buildPoolRows(models.data ?? [], topology.data?.pools ?? []),
    [models.data, topology.data?.pools],
  );
  const sources = deployments.data ?? [];
  const sourceLinks = useMemo(() => {
    const map = new Map<
      string,
      Array<{ model: ModelGroup; link: ModelGroupSource }>
    >();
    for (const model of models.data ?? [])
      for (const link of safeDeployments(model)) {
        if (!link.deployment?.id) continue;
        const memberships = map.get(link.deployment.id) ?? [];
        memberships.push({ model, link });
        map.set(link.deployment.id, memberships);
      }
    return map;
  }, [models.data]);

  const selectedPoolId = searchParams.get("pool");
  const selectedPool =
    poolRows.find((pool) => pool.id === selectedPoolId) ?? poolRows[0] ?? null;
  const selectedSourceId = searchParams.get("source");
  const selectedSource =
    sources.find((source) => source.id === selectedSourceId) ??
    sources[0] ??
    null;
  useEffect(() => {
    if (view !== "pool" || !selectedPool || selectedPoolId === selectedPool.id)
      return;
    const params = new URLSearchParams(searchParams);
    params.set("pool", selectedPool.id);
    setSearchParams(params, { replace: true });
  }, [selectedPool, selectedPoolId, setSearchParams, searchParams, view]);

  function selectPool(poolId: string) {
    const params = new URLSearchParams(searchParams);
    params.set("view", "pool");
    params.set("pool", poolId);
    setViewState("pool");
    setSearchParams(params, { replace: true });
  }
  function setView(next: "topology" | "pool" | "sources") {
    setViewState(next);
    const params = new URLSearchParams(searchParams);
    params.set("view", next);
    setSearchParams(params, { replace: true });
  }
  function selectSource(sourceId: string) {
    const params = new URLSearchParams(searchParams);
    params.set("view", "sources");
    params.set("source", sourceId);
    setViewState("sources");
    setSearchParams(params, { replace: true });
  }
  async function refreshModelPool() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["deployments"] }),
      queryClient.invalidateQueries({ queryKey: ["models"] }),
      queryClient.invalidateQueries({ queryKey: ["topology"] }),
      queryClient.invalidateQueries({ queryKey: ["model-routing-history"] }),
    ]);
  }

  const healthCheck = useMutation({
    mutationFn: (id: string) => api.healthCheckDeployment(apiContext, id),
    onSuccess: async () => {
      toast.success(t("Health check completed"));
      await refreshModelPool();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Health check failed"),
      ),
  });
  const disable = useMutation({
    mutationFn: (id: string) => api.disableDeployment(apiContext, id),
    onSuccess: async () => {
      toast.success(t("Model Source disabled"));
      setDangerAction(null);
      await refreshModelPool();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : t("Failed to disable Model Source"),
      ),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteDeployment(apiContext, id),
    onSuccess: async () => {
      toast.success(t("Model Source removed"));
      setDangerAction(null);
      await refreshModelPool();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : t("Failed to remove Model Source"),
      ),
  });

  const incomplete = deployments.isError || models.isError || topology.isError;
  const callablePools = poolRows.filter(
    (pool) => callableSources(pool.memberships).length > 0,
  ).length;
  const needsAttention = poolRows.filter(
    (pool) => poolHealth(pool.memberships) !== "healthy",
  ).length;
  const singleSource = poolRows.filter(
    (pool) => callableSources(pool.memberships).length === 1,
  ).length;

  return (
    <div className="grid min-w-0 grid-cols-1 gap-6">
      <header>
        <p className="model-pool-eyebrow">{t("BUILD · MODEL FABRIC · 02 COMPOSE")}</p>
        <h1 className="mt-2 text-2xl font-semibold text-ink">{t("Model Pool")}</h1>
        <p className="mt-1 text-sm text-muted">{t("Compose compatible Provider Sources into safe, observable production capabilities.")}</p>
      </header>
      <div
        className="capability-status-strip model-pool-status"
        aria-label={t("Model Pool status")}
      >
        <SummaryTile
          icon={<CheckCircle2 size={18} />}
          label={t("Callable pools")}
          value={incomplete ? "—" : callablePools}
        />
        <SummaryTile
          icon={<AlertTriangle size={18} />}
          label={t("Needs attention")}
          value={incomplete ? "—" : needsAttention}
        />
        <SummaryTile
          icon={<ShieldAlert size={18} />}
          label={t("Single-source risk")}
          value={incomplete ? "—" : singleSource}
        />
        <SummaryTile
          icon={<Activity size={18} />}
          label={t("Status evidence")}
          value={incomplete ? "Incomplete" : "Current"}
        />
      </div>
      {incomplete && (
        <div className="model-pool-partial-status" role="status">
          <AlertTriangle size={18} />
          <div>
            <strong>{t("Status incomplete")}</strong>
            <span>{t("Some Model Fabric evidence is unavailable. Existing data is preserved; retry before changing production routing.")}</span>
          </div>
          <button
            className="btn min-h-10"
            onClick={() => void refreshModelPool()}
          >{t("Retry")}</button>
        </div>
      )}
      <div className="model-pool-view-switcher">
        <NexilumeTabs
          label={t("Model Pool views")}
          idBase="model-pool-views"
          value={view}
          onChange={setView}
          options={[
            { value: "topology", label: t("Topology"), eyebrow: t("Network") },
            {
              value: "pool",
              label: t("Model Pool"),
              eyebrow: t("Compose"),
              count: poolRows.length,
            },
            {
              value: "sources",
              label: t("Sources"),
              eyebrow: t("Operate"),
              count: sources.length,
            },
          ]}
        />
      </div>
      <section
        role="tabpanel"
        id={`model-pool-views-panel-${view}`}
        aria-labelledby={`model-pool-views-tab-${view}`}
        tabIndex={0}
      >
        {view === "topology" ? (
          topology.isError ? (
            <div className="topology-empty is-error">
              <AlertTriangle size={22} />
              <div>
                <h2>{t("Pool topology unavailable")}</h2>
                <p>{t("Use Model Pool while the topology evidence recovers.")}</p>
              </div>
            </div>
          ) : topology.data ? (
            <NexilumeTopology
              data={topology.data}
              title={t("Pool topology")}
              description={t("Runtime → Model Offer → Source → Pool → Router. Pool boundaries own Source selection.")}
            />
          ) : (
            <div className="topology-empty">
              <Activity className="animate-pulse" size={22} />
              <div>
                <h2>{t("Loading Pool topology")}</h2>
                <p>{t("Resolving Source strategies, health, price, latency, and lineage.")}</p>
              </div>
            </div>
          )
        ) : view === "pool" ? (
          poolRows.length === 0 ? (
            <EmptyState
              title={t("No model capabilities")}
              description={sourceDiscovery.emptyDescription}
              action={<ProviderEntryActions />}
            />
          ) : (
            <PoolOperationsDesk
              pools={poolRows}
              selected={selectedPool}
              onSelect={selectPool}
              onShare={(pool) => setShareTarget(modelPoolShareTarget(pool))}
              onRefresh={refreshModelPool}
              apiContext={apiContext}
            />
          )
        ) : sources.length === 0 ? (
          <EmptyState
            title={t("No model sources")}
            description={t("Sources are created from their real Runtime origin.")}
            action={<ProviderEntryActions />}
          />
        ) : (
          <SourceOperationsSurface
            sources={sources}
            selected={selectedSource}
            memberships={sourceLinks}
            onSelect={selectSource}
            onOpenPool={selectPool}
            onCheck={(source) => healthCheck.mutate(source.deployment_id)}
            onDanger={setDangerAction}
            checking={healthCheck.isPending}
          />
        )}
      </section>
      <ShareResourceModal
        target={shareTarget}
        onClose={() => setShareTarget(null)}
      />
      <SourceDangerDialog
        action={dangerAction}
        memberships={
          dangerAction ? (sourceLinks.get(dangerAction.source.id) ?? []) : []
        }
        busy={disable.isPending || remove.isPending}
        onClose={() => setDangerAction(null)}
        onConfirm={() => {
          if (!dangerAction) return;
          if (dangerAction.kind === "disable")
            disable.mutate(dangerAction.source.deployment_id);
          else remove.mutate(dangerAction.source.deployment_id);
        }}
      />
    </div>
  );
}

function PoolOperationsDesk({
  pools,
  selected,
  onSelect,
  onShare,
  onRefresh,
  apiContext,
}: {
  pools: PoolRow[];
  selected: PoolRow | null;
  onSelect: (id: string) => void;
  onShare: (pool: PoolRow) => void;
  onRefresh: () => Promise<void>;
  apiContext: ApiContext;
}) {
  useLocale();
  const [strategy, setStrategy] = useState("fallback");
  const [draftSources, setDraftSources] = useState<
    ModelGroupRoutingDraftSource[]
  >([]);
  const [preview, setPreview] = useState<ModelGroupRoutingPreview | null>(null);
  useEffect(() => {
    if (!selected) return;
    setStrategy(selected.modelGroup.routing_strategy || "fallback");
    setDraftSources(
      selected.memberships.map((link) => ({
        id: link.id,
        enabled: link.enabled,
        priority: link.priority,
        weight: link.weight,
        fallback_order: link.fallback_order,
      })),
    );
    setPreview(null);
  }, [selected?.id, selected?.modelGroup.routing_revision]);
  const history = useQuery({
    queryKey: ["model-routing-history", apiContext, selected?.id],
    queryFn: () => api.modelRoutingHistory(apiContext, selected!.id),
    enabled: Boolean(selected),
  });
  function policyBody() {
    return {
      expected_revision: selected!.modelGroup.routing_revision,
      routing_strategy: strategy,
      routing_config: {},
      sources: draftSources,
    };
  }
  const previewMutation = useMutation({
    mutationFn: () =>
      api.previewModelRouting(apiContext, selected!.id, policyBody()),
    onSuccess: setPreview,
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Policy validation failed"),
      ),
  });
  const applyMutation = useMutation({
    mutationFn: () =>
      api.updateModelRouting(apiContext, selected!.id, policyBody()),
    onSuccess: async () => {
      toast.success(t("Production routing policy applied"));
      setPreview(null);
      await onRefresh();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : t("Routing policy could not be applied"),
      ),
  });
  const rollbackMutation = useMutation({
    mutationFn: (revision: number) =>
      api.rollbackModelRouting(
        apiContext,
        selected!.id,
        revision,
        selected!.modelGroup.routing_revision,
      ),
    onSuccess: async () => {
      toast.success(t("Routing policy rolled back as a new revision"));
      await onRefresh();
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : t("Rollback failed")),
  });
  function editSource(
    id: string,
    patch: Partial<ModelGroupRoutingDraftSource>,
  ) {
    setDraftSources((current) =>
      current.map((source) =>
        source.id === id ? { ...source, ...patch } : source,
      ),
    );
    setPreview(null);
  }
  if (!selected) return null;
  const canUpdate = selected.modelGroup.access?.can_edit !== false;
  const strategyInfo =
    STRATEGIES.find((item) => item.value === strategy) ?? STRATEGIES[0];
  const routeFieldLabel =
    strategy === "weighted"
      ? t("Weight")
      : strategy === "fallback"
        ? t("Fallback order")
        : t("Tie-break priority");
  const sourceByLink = new Map(
    selected.memberships.map((item) => [item.id, item.deployment]),
  );
  return (
    <div className="model-pool-desk">
      <aside className="model-pool-track" aria-label={t("Model Pools")}>
        <header>
          <span>{t("Capabilities")}</span>
          <strong>{pools.length}</strong>
        </header>
        <div role="listbox" aria-label={t("Select a Model Pool")}>
          {pools.map((pool) => {
            const callable = callableSources(pool.memberships).length;
            const active = pool.id === selected.id;
            return (
              <button
                key={pool.id}
                type="button"
                role="option"
                aria-selected={active}
                className={`model-pool-track__row ${active ? "is-selected" : ""}`}
                onClick={() => onSelect(pool.id)}
              >
                <span className="model-pool-glyph">
                  <i>{callable}</i>
                </span>
                <span>
                  <strong>{pool.displayName || pool.name}</strong>
                  <small>
                    {pool.modelGroup.canonical_model_key} ·{" "}
                    {humanStrategy(pool.routingStrategy)}
                  </small>
                </span>
                <StatusBadge status={poolHealth(pool.memberships)} />
              </button>
            );
          })}
        </div>
      </aside>
      <div className="model-pool-policy">
        <header className="model-pool-policy__header">
          <div>
            <span>{t("Production routing")}</span>
            <h2>{selected.displayName || selected.name}</h2>
            <p>
              {selected.modelGroup.canonical_model_key}{" "}{t("· Revision")}{" "}
              {selected.modelGroup.routing_revision}
            </p>
          </div>
          <div className="model-pool-policy__header-actions">
            <StatusBadge status={poolHealth(selected.memberships)} />
            <ResourceSharingAction>
            <button
              className="btn min-h-10"
              onClick={() => onShare(selected)}
              disabled={!canUpdate}
            >
              <Share2 size={15} />{t("Access")}</button>
            </ResourceSharingAction>
          </div>
        </header>
        <div
          className="model-pool-policy__evidence"
          aria-label={t("Selected Pool evidence")}
        >
          <div>
            <span>{t("Callable")}</span>
            <strong>{callableSources(selected.memberships).length}</strong>
          </div>
          <div>
            <span>{t("Lowest cost")}</span>
            <strong>{poolPrice(selected)}</strong>
          </div>
          <div>
            <span>{t("Best latency")}</span>
            <strong>{poolLatency(selected)}</strong>
          </div>
          <div>
            <span>{t("Scope")}</span>
            <strong>{selected.visibility}</strong>
          </div>
        </div>
        <section className="model-pool-policy__strategy">
          <div>
            <span className="model-pool-section-label">{t("Routing mode")}</span>
            <strong>{strategyInfo.label}</strong>
            <p>{strategyInfo.help}</p>
          </div>
          <label>
            <span className="sr-only">{t("Source selection strategy")}</span>
            <select
              className="select min-h-10"
              value={strategy}
              disabled={!canUpdate || applyMutation.isPending}
              onChange={(event) => {
                setStrategy(event.target.value);
                setPreview(null);
              }}
            >
              {STRATEGIES.map((item) => (
                <option key={item.value} value={item.value}>
                  {t(item.label)}
                </option>
              ))}
            </select>
          </label>
        </section>
        <section
          className="model-pool-source-editor"
          aria-label={t("Source policy draft")}
        >
          <header>
            <div>
              <span className="model-pool-section-label">{t("Source policy")}</span>
              <strong>
                {draftSources.filter((item) => item.enabled).length}{" "}{t("of")}{" "}
                {draftSources.length}{" "}{t("included")}</strong>
            </div>
            <span>{routeFieldLabel}</span>
          </header>
          {draftSources.map((draft) => {
            const source = sourceByLink.get(draft.id)!;
            const routeValue =
              strategy === "weighted"
                ? draft.weight
                : strategy === "fallback"
                  ? draft.fallback_order
                  : draft.priority;
            return (
              <div className="model-pool-source-editor__row" key={draft.id}>
                <div>
                  <strong>{source.deployment_id}</strong>
                  <small>
                    {source.provider} · {source.canonical_model_key}
                  </small>
                </div>
                <div className="model-pool-source-editor__evidence">
                  <StatusBadge status={source.health_status} />
                  <small>
                    {formatMoney(source.pricing_rate)} ·{" "}
                    {source.last_latency_ms
                      ? t("{{0}} ms", { 0: source.last_latency_ms })
                      : t("No latency")}
                  </small>
                </div>
                <div className="model-pool-source-editor__controls">
                  <label>
                    <span>{routeFieldLabel}</span>
                    <input
                      className="input min-h-10"
                      type="number"
                      min="0"
                      value={routeValue}
                      disabled={!canUpdate}
                      onChange={(event) =>
                        editSource(
                          draft.id,
                          strategy === "weighted"
                            ? { weight: Number(event.target.value) }
                            : strategy === "fallback"
                              ? { fallback_order: Number(event.target.value) }
                              : { priority: Number(event.target.value) },
                        )
                      }
                    />
                  </label>
                  <label className="model-pool-switch">
                    <input
                      type="checkbox"
                      checked={draft.enabled}
                      disabled={!canUpdate}
                      onChange={(event) =>
                        editSource(draft.id, { enabled: event.target.checked })
                      }
                    />
                    <span>{draft.enabled ? t("Included") : t("Excluded")}</span>
                  </label>
                </div>
              </div>
            );
          })}
        </section>
        <footer className="model-pool-policy__actions">
          <p>
            <strong>{t("Draft changes are not live until applied.")}</strong>{" "}{t("Review impact before changing traffic.")}</p>
          <button
            className="btn min-h-10"
            disabled={
              !canUpdate || previewMutation.isPending || applyMutation.isPending
            }
            onClick={() => previewMutation.mutate()}
          >
            <FlaskConical size={16} />{t("Review impact")}</button>
          <button
            className="btn btn-primary min-h-10"
            disabled={
              !canUpdate ||
              !preview ||
              previewMutation.isPending ||
              applyMutation.isPending
            }
            onClick={() => applyMutation.mutate()}
          >
            <CheckCircle2 size={16} />{t("Apply policy")}</button>
        </footer>
        {preview && <PolicyPreview preview={preview} />}
        <details className="model-pool-history">
          <summary>
            <span>
              <History size={15} />{" "}{t("Change history")}</span>
            <small>{history.data?.length ?? 0}{" "}{t("revisions")}</small>
          </summary>
          {history.isLoading ? (
            <p>{t("Loading revisions…")}</p>
          ) : history.isError ? (
            <p>{t("History unavailable.")}</p>
          ) : (
            (history.data ?? []).slice(0, 5).map((item) => (
              <div key={item.revision}>
                <span>
                  <strong>{t("Revision")}{" "}{item.revision}</strong>
                  <small>
                    {humanStrategy(item.routing_strategy)} ·{" "}
                    {formatDate(item.created_at)}
                  </small>
                </span>
                {!item.is_current && canUpdate && (
                  <button
                    type="button"
                    aria-label={t("Rollback to revision {{0}}", { 0: item.revision })}
                    disabled={rollbackMutation.isPending}
                    onClick={() => rollbackMutation.mutate(item.revision)}
                  >
                    <RotateCcw size={14} />
                  </button>
                )}
              </div>
            ))
          )}
        </details>
      </div>
    </div>
  );
}

function PolicyPreview({ preview }: { preview: ModelGroupRoutingPreview }) {
  useLocale();
  return (
    <section className="model-pool-preview" aria-live="polite">
      <header>
        <div>
          <span>{t("Validated draft")}</span>
          <h3>
            {preview.candidate_count}{" "}{t("callable candidates ·")}{" "}
            {preview.affected_router_count}{" "}{t("affected Routers")}</h3>
        </div>
        <StatusBadge
          status={
            preview.risks.some((risk) => risk.severity === "critical")
              ? "degraded"
              : "ready"
          }
        />
      </header>
      {preview.risks.length > 0 && (
        <div className="model-pool-preview__risks">
          {preview.risks.map((risk) => (
            <div key={risk.code} data-severity={risk.severity}>
              <AlertTriangle size={15} />
              <span>{risk.message}</span>
            </div>
          ))}
        </div>
      )}
      <div className="model-pool-preview__candidates">
        {preview.candidates.map((candidate) => (
          <div key={candidate.source_id}>
            <strong>
              #{candidate.rank} · {candidate.source}
            </strong>
            <span>
              {candidate.provider} · {candidate.health_status}
              {candidate.traffic_share_percent != null
                ? t("· {{0}}% target traffic", { 0: candidate.traffic_share_percent })
                : ""}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function SourceOperationsSurface({
  sources,
  selected,
  memberships,
  onSelect,
  onOpenPool,
  onCheck,
  onDanger,
  checking,
}: {
  sources: Deployment[];
  selected: Deployment | null;
  memberships: Map<
    string,
    Array<{ model: ModelGroup; link: ModelGroupSource }>
  >;
  onSelect: (id: string) => void;
  onOpenPool: (id: string) => void;
  onCheck: (source: Deployment) => void;
  onDanger: (action: SourceDangerAction) => void;
  checking: boolean;
}) {
  useLocale();
  const sourceDiscovery=useSourceDiscovery();
  if (!selected) return null;
  const selectedMemberships = memberships.get(selected.id) ?? [];
  const canEdit = selected.access?.can_edit !== false;
  return (
    <div className="model-source-surface">
      <aside className="model-source-track" aria-label={t("Model Sources")}>
        <header>
          <div>
            <span className="model-pool-section-label">{t("Existing Sources")}</span>
            <h2>{sources.length}{" "}{t("managed origins")}</h2>
          </div>
          <p>{sourceDiscovery.sourceDescription}</p>
        </header>
        <div role="listbox" aria-label={t("Select a Model Source")}>
          {sources.map((source) => (
            <button
              type="button"
              role="option"
              aria-selected={source.id === selected.id}
              className={`model-source-track__row ${source.id === selected.id ? "is-selected" : ""}`}
              key={source.id}
              onClick={() => onSelect(source.id)}
            >
              <span className="model-source-glyph">
                <Waypoints size={17} />
              </span>
              <span>
                <strong>{source.deployment_id}</strong>
                <small>
                  {source.provider} · {source.canonical_model_key}
                </small>
              </span>
              <StatusBadge status={source.health_status} />
            </button>
          ))}
        </div>
      </aside>
      <section
        className="model-source-detail"
        aria-label={t("Selected Source details")}
      >
        <header>
          <div>
            <span className="model-pool-section-label">{t("Immutable Source")}</span>
            <h2>{selected.deployment_id}</h2>
            <p>
              {selected.provider} · {selected.canonical_model_key}
            </p>
          </div>
          <StatusBadge status={selected.health_status} />
        </header>
        <div className="model-source-evidence">
          <div>
            <span>{t("Price")}</span>
            <strong>{formatMoney(selected.pricing_rate)}</strong>
          </div>
          <div>
            <span>{t("Latency")}</span>
            <strong>
              {selected.last_latency_ms
                ? t("{{0}} ms", { 0: selected.last_latency_ms })
                : t("Not measured")}
            </strong>
          </div>
          <div>
            <span>{t("Last check")}</span>
            <strong>
              {selected.last_checked_at
                ? formatDate(selected.last_checked_at)
                : t("Never")}
            </strong>
          </div>
        </div>
        <section className="model-source-memberships">
          <div>
            <span className="model-pool-section-label">{t("Pool membership")}</span>
            <h3>
              {selectedMemberships.length
                ? t("{{0}} connected Pools", { 0: selectedMemberships.length })
                : t("Not connected")}
            </h3>
          </div>
          {selectedMemberships.length ? (
            <div>
              {selectedMemberships.map(({ model, link }) => (
                <button
                  type="button"
                  key={link.id}
                  onClick={() => onOpenPool(model.id)}
                >
                  <span>
                    <strong>{model.display_name || model.name}</strong>
                    <small>
                      {humanStrategy(model.routing_strategy)}{" "}{t("· Priority")}{" "}
                      {link.priority}
                    </small>
                  </span>
                  <span>{t("Open policy")}</span>
                </button>
              ))}
            </div>
          ) : (
            <p>{t("Choose this Model Offer from its Provider Runtime to create a new Pool membership.")}</p>
          )}
        </section>
        {canEdit && (
          <footer className="model-source-actions">
            <button
              className="btn min-h-10"
              onClick={() => onCheck(selected)}
              disabled={checking}
            >
              <Activity size={15} /> {checking ? t("Checking…") : t("Check health")}
            </button>
            <button
              className="btn min-h-10"
              onClick={() => onDanger({ kind: "disable", source: selected })}
            >
              <PowerOff size={15} />{" "}{t("Disable")}</button>
            <button
              className="btn btn-danger min-h-10"
              onClick={() => onDanger({ kind: "remove", source: selected })}
            >
              <Trash2 size={15} />{" "}{t("Remove")}</button>
          </footer>
        )}
      </section>
    </div>
  );
}

function SourceDangerDialog({
  action,
  memberships,
  busy,
  onClose,
  onConfirm,
}: {
  action: SourceDangerAction;
  memberships: Array<{ model: ModelGroup; link: ModelGroupSource }>;
  busy: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  useLocale();
  if (!action) return null;
  const lastSourcePools = memberships.filter(
    ({ model }) => callableSources(model.deployments).length <= 1,
  );
  return (
    <NexilumeDialog
      open
      title={`${action.kind === "remove" ? "Remove" : "Disable"} ${action.source.deployment_id}`}
      eyebrow={t("Production impact")}
      description={t("Review Pool dependencies before changing this immutable Source.")}
      busy={busy}
      onClose={onClose}
      footer={
        <>
          <button className="btn min-h-10" onClick={onClose} disabled={busy}>{t("Keep Source")}</button>
          <button
            className="btn btn-danger min-h-10"
            onClick={onConfirm}
            disabled={busy}
          >
            {action.kind === "remove" ? "Remove Source" : "Disable Source"}
          </button>
        </>
      }
    >
      <div className="grid gap-4 p-1 text-sm">
        <p>{t("This Source belongs to")}{" "}{memberships.length}{" "}{t("Model Pool")}{getLocale() === "zh-CN" ? "" : memberships.length === 1 ? "" : "s"}:{" "}
          {memberships
            .map(({ model }) => model.display_name || model.name)
            .join(", ") || t("none")}
          .
        </p>
        {lastSourcePools.length > 0 && (
          <div className="model-pool-danger">
            <ShieldAlert size={18} />
            <span>
              <strong>{t("Traffic interruption risk")}</strong>
              <small>
                {lastSourcePools
                  .map(({ model }) => model.display_name || model.name)
                  .join(", ")}{" "}{t("will have no callable replacement.")}</small>
            </span>
          </div>
        )}
        <p className="text-muted">{t("Disable is operationally reversible only through the Provider lifecycle. Removal preserves historical usage but detaches this Source from every Pool.")}</p>
      </div>
    </NexilumeDialog>
  );
}
function modelPoolShareTarget(pool: PoolRow): ShareResourceTarget {
  return {
    resourceType: "model_pool",
    resourceKind: "Model Pool",
    resourceId: pool.id,
    resourceName: pool.displayName || pool.name,
  };
}
function SummaryTile({
  icon,
  label,
  value,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
}) {
  useLocale();
  return (
    <div className="capability-status-strip__item">
      <div className="capability-status-strip__icon">{icon}</div>
      <div>
        <div className="capability-status-strip__label">{label}</div>
        <div className="capability-status-strip__value">{value}</div>
      </div>
    </div>
  );
}
function buildPoolRows(
  models: ModelGroup[],
  topologyPools: TopologyPool[],
): PoolRow[] {
  const topologyById = new Map(topologyPools.map((pool) => [pool.id, pool]));
  return models.map((model) => {
    const memberships = safeDeployments(model);
    return {
      id: model.id,
      name: model.name,
      displayName: model.display_name,
      visibility: model.visibility,
      status: model.status,
      routingStrategy: model.routing_strategy || "fallback",
      modelGroup: model,
      memberships,
      sources: memberships.map((link) => link.deployment).filter(Boolean),
      topology: topologyById.get(model.id),
    };
  });
}
function safeDeployments(model: ModelGroup) {
  return Array.isArray(model.deployments) ? model.deployments : [];
}
function callableSources(memberships: ModelGroupSource[]) {
  return memberships
    .filter(
      (membership) =>
        membership.status === "active" &&
        membership.enabled &&
        membership.deployment.status === "active",
    )
    .map((membership) => membership.deployment);
}
function poolHealth(memberships: ModelGroupSource[]) {
  const sources = callableSources(memberships);
  if (sources.length === 0) return "unavailable";
  if (sources.some((source) => source.health_status === "healthy"))
    return "healthy";
  return "degraded";
}
function poolPrice(pool: PoolRow) {
  if (pool.topology)
    return pool.topology.lowest_price_per_1k_tokens
      ? formatMoney(pool.topology.lowest_price_per_1k_tokens)
      : "—";
  const prices = callableSources(pool.memberships)
    .map((source) => Number(source.pricing_rate))
    .filter(Number.isFinite);
  if (!prices.length) return "—";
  const min = Math.min(...prices).toFixed(6);
  const max = Math.max(...prices).toFixed(6);
  return min === max
    ? formatMoney(min)
    : `${formatMoney(min)} – ${formatMoney(max)}`;
}
function poolLatency(pool: PoolRow) {
  if (pool.topology)
    return pool.topology.lowest_latency_ms
      ? `${pool.topology.lowest_latency_ms} ms`
      : "—";
  const values = callableSources(pool.memberships)
    .map((source) => source.last_latency_ms)
    .filter((value): value is number => typeof value === "number" && value > 0);
  return values.length ? `${Math.min(...values)} ms` : "—";
}
function humanStrategy(strategy: string) {
  return (
    STRATEGIES.find((item) => item.value === strategy)?.label ??
    strategy.replaceAll("_", " ")
  );
}
