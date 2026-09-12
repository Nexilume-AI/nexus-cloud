import { ProviderFact as Fact, DialogActions, providerRuntimeId, errorToast } from "../components/ProviderPresentation";
import { useEffect, useMemo, useRef, useState } from "react";

import { ProviderImportDialog } from "../components/ProviderImportDialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";
import { ChevronDown, Check, CircleAlert, KeyRound, Loader2, LogIn, Play, Plus, RefreshCw, Search, Share2, Square, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "../app/AuthContext";
import { useApplicationDistribution } from "../app/distribution";
import { useResourceManagementCopy } from "../components/ResourcePublishing";
import type { ProviderPublicationPolicy } from "../app/resourcePublishing";
import { api } from "../lib/api";
import { useCatalogSearch, useCursorPage } from "../lib/useCursorPage";
import { CursorPageControls } from "../components/CursorPageControls";
import type { ProviderConnection, ProviderRuntimeAccount, ProviderRuntimeModelOffer } from "../lib/types";
import { compactId } from "../lib/format";
import { StatusBadge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Form";
import { ResourceAccessState, ResourceOwnershipBadge, ResourceOwnershipPicker } from "../components/ResourceOwnership";
import { ProviderPublicationSummary, ProviderPublicationDialog, ProviderModelSettingsDialog } from "../components/ResourcePublishing";
import { NexilumeDialog, NexilumeTabs } from "../components/NexilumeControls";
import { RuntimeSourceComposer, type RuntimeSourceComposerOrigin } from "../components/RuntimeSourceComposer";

type ProviderStatusFilter = "all" | "attention" | "healthy" | "recovery";
type RuntimeRecommendedAction =
  | "wait"
  | "repair"
  | "login"
  | "start"
  | "health"
  | "refresh"
  | "use_source"
  | "publish"
  | "operational";
const cliProxyLoginProviders = [
  { value: "openai", label: "OpenAI" },
  { value: "claude", label: "Claude" },
] as const;

export function ProvidersPage() {
  const { apiContext, user, isContextReady } =
    useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const initialPrivateParams = useMemo(
    () => new URLSearchParams(location.search),
    [],
  );
  const [search, setSearch] = useState(
    initialPrivateParams.get("q") ?? "",
  );
  const [statusFilter, setStatusFilter] = useState<ProviderStatusFilter>(() => {
    const value = initialPrivateParams.get("status");
    return value === "attention" || value === "healthy" || value === "recovery"
      ? value
      : "all";
  });
  const [selectedId, setSelectedId] = useState(
    initialPrivateParams.get("provider") ?? "",
  );
  const [createAccountOpen, setCreateAccountOpen] = useState(false);
  const [publishProviderId, setPublishProviderId] = useState("");
  const [removeProviderId, setRemoveProviderId] = useState("");
  const [ownSourceRequest, setOwnSourceRequest] = useState<{
    providerId: string;
    offerIds?: string[];
  } | null>(null);
  const catalogSearch = useCatalogSearch(search);
  const providerPaging = useCursorPage([apiContext, catalogSearch, statusFilter]);
  const legacyPaging = useCursorPage(apiContext);
  const connectionPage = useQuery({
    queryKey: ["provider-connections", apiContext, user?.user_id, catalogSearch, statusFilter, providerPaging.cursor],
    queryFn: ({ signal }) => api.providerConnectionsPage(apiContext, { q: catalogSearch, status: statusFilter, cursor: providerPaging.cursor }, signal),
    placeholderData: (previous, previousQuery) => JSON.stringify(previousQuery?.queryKey[1]) === JSON.stringify(apiContext) && previousQuery?.queryKey[2] === user?.user_id ? previous : undefined,
    gcTime: 60_000,
    staleTime: 15_000,
    refetchInterval: (query) =>
      query.state.data?.items.some((provider) => ["starting", "stopping"].includes(provider.status))
        ? 2000
        : false,
    enabled: isContextReady,
  });
  const connections = { ...connectionPage, data: connectionPage.data?.items };
  const selectedConnection = useQuery({
    queryKey: ["provider-connections", apiContext, user?.user_id, "detail", selectedId],
    queryFn: ({ signal }) => api.providerConnection(apiContext, selectedId, signal),
    enabled: isContextReady && Boolean(selectedId) && Boolean(connections.data) && (
      connections.data?.some(item => item.id === selectedId && item.models_loaded === false && item.access?.can_read !== false)
      || (!search.trim() && statusFilter === "all" && !connections.data?.some(item => item.id === selectedId))
    ),
    gcTime: 60_000,
    staleTime: 15_000,
    refetchInterval: (query) => ["starting", "stopping"].includes(query.state.data?.status ?? "") ? 2000 : false,
  });
  const legacyPage = useQuery({
    queryKey: ["provider-runtimes", apiContext, user?.user_id, "legacy", legacyPaging.cursor],
    queryFn: ({ signal }) => api.legacyProviderRuntimesPage(apiContext, legacyPaging.cursor, signal),
    enabled: isContextReady,
    staleTime: 15_000,
    gcTime: 60_000,
  });
  const legacyRuntimes = { ...legacyPage, data: legacyPage.data?.items };
  const providers = useMemo(() => {
    const query = search.trim().toLowerCase();
    return (connections.data ?? [])
      .filter((provider) => {
        if (statusFilter === "attention") return providerNeedsAttention(provider);
        if (statusFilter === "healthy") return provider.status === "active";
        if (statusFilter === "recovery") return ["stopped", "repair_required", "failed"].includes(provider.status);
        return true;
      })
      .filter((provider) =>
        provider.models_loaded === false || !query ||
      [
        provider.name,
        provider.upstream_provider,
        provider.engine,
        ...provider.models.flatMap((offer) => [offer.canonical_model_key ?? "", offer.upstream_model_id]),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(query),
      )
      .sort(compareProviders);
  }, [connections.data, search, statusFilter]);
  const offPageProvider = !search.trim() && statusFilter === "all" && !selectedConnection.isError && selectedConnection.data?.id === selectedId ? selectedConnection.data : null;
  const selectedSummary = providers.find((provider) => provider.id === selectedId) ?? offPageProvider ?? (selectedId && selectedConnection.isFetching ? null : providers[0]) ?? null;
  const selectedProvider = selectedSummary?.models_loaded === false && selectedSummary.access?.can_read !== false
    && selectedConnection.data?.id === selectedSummary.id && !selectedConnection.isError
    ? selectedConnection.data : selectedSummary;
  const detailRequired = selectedProvider?.models_loaded === false && selectedProvider.access?.can_read !== false;
  const linkedRuntimeIds = new Set(
    (connections.data ?? []).map(providerRuntimeId).filter(Boolean),
  );
  const unlinkedRuntimes = (legacyRuntimes.data ?? []).filter((runtime) => !linkedRuntimeIds.has(runtime.id));

  useEffect(() => {
    if (connections.isLoading || selectedConnection.isFetching) return;
    if (selectedProvider && selectedProvider.id !== selectedId) setSelectedId(selectedProvider.id);
    if (!selectedProvider && selectedId) setSelectedId("");
  }, [connections.isLoading, selectedId, selectedProvider, selectedConnection.isFetching]);

  useEffect(() => {
    const params = new URLSearchParams();
    const providerId = selectedProvider?.id || (connections.isLoading || selectedConnection.isFetching ? selectedId : "");
    if (providerId) params.set("provider", providerId);
    if (statusFilter !== "all") params.set("status", statusFilter);
    if (search.trim()) params.set("q", search.trim());
    const nextSearch = params.toString();
    if (nextSearch !== location.search.replace(/^\?/, "")) {
      navigate({ pathname: location.pathname, search: nextSearch ? `?${nextSearch}` : "" }, { replace: true });
    }
  }, [
    location.pathname,
    location.search,
    connections.isLoading,
    selectedConnection.isFetching,
    selectedId,
    navigate,
    search,
    selectedProvider?.id,
    statusFilter,
  ]);
  const attentionCount = (connections.data ?? []).filter(providerNeedsAttention).length;
  const ownSourceProvider = ownSourceRequest
    ? (selectedProvider?.id === ownSourceRequest.providerId ? selectedProvider : null)
    : null;
  return (
    <section className="space-y-4" aria-labelledby="providers-title">
      <header className="provider-page-header">
        <div>
          <h1 id="providers-title">Providers</h1>
          <p>Connect and monitor your model providers from one place.</p>
          <div className="provider-page-header__summary" aria-label="Provider summary">
            <span><strong>{connections.data?.length ?? 0}</strong> connected · this page</span>
            <span><strong>{attentionCount}</strong> need attention</span>
            <ProviderPublicationSummary surface="page" providers={connections.data ?? []} />
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <ProviderImportDialog key={`${apiContext.tenantId}:${apiContext.projectId}`} />
          <button className="btn btn-primary" onClick={() => setCreateAccountOpen(true)}>
            <Plus size={16} /> Connect provider
          </button>
        </div>
      </header>

      <section className="provider-fabric-workspace">
        <div className="provider-fabric-workspace__controls">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <p className="font-mono text-xs uppercase tracking-widest text-muted">Your connections</p>
              {attentionCount > 0 && (
                <button
                  className="provider-attention-link"
                  onClick={() => setStatusFilter("attention")}
                >
                  <CircleAlert size={14} /> {attentionCount} need attention
                </button>
              )}
            </div>
            <p className="mt-1 text-sm text-muted">Select a Provider to review its health, models, and availability.</p>
          </div>
          <div className="provider-workbench-filters">
            <div className="relative min-w-0 flex-1">
              <Search size={16} className="pointer-events-none absolute left-3 top-3 text-muted" />
              <input
                className="input min-h-10 pl-9"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search providers or models"
                aria-label="Search providers"
              />
            </div>
            <label className="sr-only" htmlFor="provider-status-filter">Provider status</label>
            <select
              id="provider-status-filter"
              className="input provider-status-filter"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as ProviderStatusFilter)}
            >
              <option value="all">All statuses</option>
              <option value="attention">Needs attention</option>
              <option value="healthy">Healthy</option>
              <option value="recovery">Stopped / Recovery</option>
            </select>
          </div>
        </div>
        <div className="provider-fabric-workspace__body">
          <div className="px-4"><CursorPageControls page={providerPaging.page} hasMore={Boolean(connectionPage.data?.next_cursor)} busy={connectionPage.isFetching} onPrevious={providerPaging.previous} onNext={() => providerPaging.next(connectionPage.data?.next_cursor)} /></div>
          {connections.isLoading ? (
            <ProviderWorkbenchSkeleton />
          ) : (
            <>
              {(connections.isError || legacyRuntimes.isError) && (
                <ProviderPartialFailure
                  onRetry={() => void Promise.all([connections.refetch(), legacyRuntimes.refetch()])}
                />
              )}
              <ObjectWorkspace
                empty={!providers.length}
                emptyTitle={(connections.data?.length ?? 0) ? "No matching providers" : "No providers connected"}
                emptyDescription={
                  (connections.data?.length ?? 0)
                    ? "Try another search or clear the current status filter."
                    : "Connect a Provider to make its models available in Nexus."
                }
                onClearFilters={(connections.data?.length ?? 0) ? () => { setSearch(""); setStatusFilter("all"); } : undefined}
                list={providers.map((provider) => ({
                  id: provider.id,
                  title: provider.name,
                  subtitle: `${humanEngine(provider.engine)} · ${provider.upstream_provider} · ${provider.ownership?.label || "Ownership unavailable"}${provider.access && !provider.access.can_read ? " · Access required" : ""}`,
                  status: provider.status,
                  modelCount: provider.model_count ?? provider.models.length,
                  extraSummary: <ProviderPublicationSummary surface="row" provider={provider} />,
                  attentionCount: providerNeedsAttention(provider) ? 1 : 0,
                  lastCheckedAt: provider.last_checked_at,
                }))}
                selectedId={selectedProvider?.id ?? ""}
                onSelect={setSelectedId}
                openInspectorInitially={Boolean(initialPrivateParams.get("provider"))}
                inspector={
                  selectedProvider ? (
                    detailRequired ? <div className="p-5" aria-live="polite">
                      <h2>{selectedProvider.name}</h2>
                      <p className="mt-2 text-sm text-muted">{selectedConnection.isError ? "Provider details unavailable. Your connections are still available." : "Loading models and health history…"}</p>
                      {selectedConnection.isError && <button className="btn mt-3" onClick={() => void selectedConnection.refetch()}>Retry provider details</button>}
                    </div> :
                    <ProviderInspector
                      key={selectedProvider.id}
                      provider={selectedProvider}
                      onPublish={() => setPublishProviderId(selectedProvider.id)}
                      onUseSources={(offerIds) => setOwnSourceRequest({ providerId: selectedProvider.id, offerIds })}
                      onRemove={() => setRemoveProviderId(selectedProvider.id)}
                    />
                  ) : null
                }
              />
              {unlinkedRuntimes.length > 0 && (
                <div><LegacyRuntimeRecovery runtimes={unlinkedRuntimes} /><CursorPageControls page={legacyPaging.page} hasMore={Boolean(legacyPage.data?.next_cursor)} busy={legacyPage.isFetching} onPrevious={legacyPaging.previous} onNext={() => legacyPaging.next(legacyPage.data?.next_cursor)} /></div>
              )}
            </>
          )}
        </div>
      </section>
      <CreateAccountDialog
        open={createAccountOpen}
        onClose={() => setCreateAccountOpen(false)}
        onCreated={setSelectedId}
      />
      <ProviderPublicationDialog
        provider={selectedProvider?.id === publishProviderId && !detailRequired ? selectedProvider : null}
        onClose={() => setPublishProviderId("")}
      />
      <RuntimeSourceComposer
        origin={ownSourceProvider ? providerConnectionSourceOrigin(ownSourceProvider) : null}
        initialOfferIds={ownSourceRequest?.offerIds}
        onClose={() => setOwnSourceRequest(null)}
      />
      <RemoveProviderDialog
        provider={selectedProvider?.id === removeProviderId && !detailRequired ? selectedProvider : null}
        onClose={() => setRemoveProviderId("")}
        onRemoved={(id) => {
          setRemoveProviderId("");
          if (selectedId === id) setSelectedId("");
        }}
      />
    </section>
  );
}

function ObjectWorkspace({
  list,
  selectedId,
  onSelect,
  inspector,
  empty,
  emptyTitle,
  emptyDescription,
  onClearFilters,
  openInspectorInitially,
}: {
  list: Array<{
    id: string;
    title: string;
    subtitle: string;
    status: string;
    modelCount: number;
    extraSummary?: React.ReactNode;
    attentionCount: number;
    lastCheckedAt: string | null;
  }>;
  selectedId: string;
  onSelect: (id: string) => void;
  inspector: React.ReactNode;
  empty: boolean;
  emptyTitle: string;
  emptyDescription: string;
  onClearFilters?: () => void;
  openInspectorInitially: boolean;
}) {
  const [inspectorOpen, setInspectorOpen] = useState(openInspectorInitially);
  const inspectorRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const compactInspector = useCompactInspector();

  useEffect(() => {
    if (!inspectorOpen || !compactInspector) return;
    const inspectorElement = inspectorRef.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    inspectorElement?.querySelector<HTMLElement>("button, [href], input, select, textarea")?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setInspectorOpen(false);
        return;
      }
      if (event.key !== "Tab" || !inspectorElement) return;
      const focusable = Array.from(
        inspectorElement.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => !element.hasAttribute("hidden"));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [compactInspector, inspectorOpen]);

  function closeInspector() {
    setInspectorOpen(false);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  }

  if (empty)
    return (
      <div className="provider-workbench-empty">
        <EmptyState title={emptyTitle} description={emptyDescription} />
        {onClearFilters && (
          <button className="btn" onClick={onClearFilters}>Clear filters</button>
        )}
      </div>
    );
  return (
    <div className="provider-object-workspace">
      <div className="provider-object-track" role="listbox" aria-label="Providers">
        {list.map((item, index) => (
          <button
            key={item.id}
            type="button"
            onClick={() => {
              triggerRef.current = document.activeElement as HTMLButtonElement;
              onSelect(item.id);
              setInspectorOpen(true);
            }}
            onKeyDown={(event) => {
              if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
              event.preventDefault();
              const nextIndex = event.key === "Home"
                ? 0
                : event.key === "End"
                  ? list.length - 1
                  : event.key === "ArrowDown"
                    ? Math.min(index + 1, list.length - 1)
                    : Math.max(index - 1, 0);
              const nextButton = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("[data-provider-option]")[nextIndex];
              nextButton?.focus();
              if (list[nextIndex]) onSelect(list[nextIndex].id);
            }}
            role="option"
            aria-selected={selectedId === item.id}
            tabIndex={selectedId === item.id ? 0 : -1}
            data-provider-option
            className={selectedId === item.id ? "is-selected" : ""}
          >
            <span className="provider-object-track__shape provider-object-track__shape--provider">
              <KeyRound size={15} />
            </span>
            <span>
              <strong>{item.title}</strong>
              <small>{item.subtitle}</small>
              <span className="provider-object-track__metrics">
                {item.modelCount} model{item.modelCount === 1 ? "" : "s"}{item.extraSummary}
                {item.lastCheckedAt ? ` · Checked ${formatRelativeDate(item.lastCheckedAt)}` : ""}
              </span>
            </span>
            <span className="provider-object-track__state">
              <em>{humanStatus(item.status)}</em>
              {item.attentionCount > 0 && <small>{item.attentionCount} issue{item.attentionCount === 1 ? "" : "s"}</small>}
            </span>
          </button>
        ))}
      </div>
      <aside
        ref={inspectorRef}
        className={`provider-object-inspector ${inspectorOpen ? "is-open" : ""}`}
        aria-label="Provider inspector"
        role={compactInspector && inspectorOpen ? "dialog" : undefined}
        aria-modal={compactInspector && inspectorOpen ? true : undefined}
      >
        <div className="provider-object-inspector__heading">
          <div className="provider-object-inspector__label">Provider details</div>
          <button
            type="button"
            className="provider-object-inspector__close"
            aria-label="Close provider inspector"
            onClick={closeInspector}
          >
            {compactInspector ? "Back to providers" : "Close"}
          </button>
        </div>
        {inspector}
      </aside>
      <button
        type="button"
        className={`provider-object-inspector__backdrop ${inspectorOpen ? "is-open" : ""}`}
        aria-label="Dismiss provider inspector"
        onClick={closeInspector}
      />
    </div>
  );
}

function ProviderInspector({
  provider,
  onPublish,
  onUseSources,
  onRemove,
}: {
  provider: ProviderConnection;
  onPublish: () => void;
  onUseSources: (offerIds?: string[]) => void;
  onRemove: () => void;
}) {
  if (provider.access && !provider.access.can_read) {
    return (
      <div className="provider-inspector-workbench grid content-start gap-4 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-xl font-semibold text-ink">{provider.name}</h2>
          <ResourceOwnershipBadge ownership={provider.ownership} />
          <ResourceAccessState access={provider.access} />
        </div>
        <p className="text-sm leading-6 text-muted">This directory entry is discoverable, but Provider health, models, endpoint, quota, and credentials require an Organization Role or explicit Share.</p>
      </div>
    );
  }
  return (
    <div className="provider-inspector-workbench">
      <RuntimeInspector
        provider={provider}
        onPublish={onPublish}
        onUseSources={onUseSources}
        onRemove={onRemove}
      />
    </div>
  );
}

function RuntimeInspector({
  provider,
  onPublish,
  onUseSources,
  onRemove,
}: {
  provider: ProviderConnection;
  onPublish: () => void;
  onUseSources: (offerIds?: string[]) => void;
  onRemove: () => void;
}) {
  const { apiContext } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const loginPollGeneration = useRef(0);
  const [editingOffer, setEditingOffer] = useState<ProviderRuntimeModelOffer | null>(null);
  const [editProviderOpen, setEditProviderOpen] = useState(false);
  const refresh = useMutation({
    mutationFn: () => api.providerConnectionAction(apiContext, provider.id, "models/refresh"),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["provider-connections"] }),
    onError: errorToast("Model discovery failed"),
  });
  const refreshOffer = useMutation({
    mutationFn: (offerId: string) =>
      api.refreshProviderConnectionModel(apiContext, provider.id, offerId),
    onSuccess: () => {
      toast.success("Model refreshed");
      queryClient.invalidateQueries({ queryKey: ["provider-connections"] });
    },
    onError: errorToast("Model refresh failed"),
  });
  const lifecycle = useMutation({
    mutationFn: (action: "start" | "stop" | "health" | "repair") =>
      api.providerConnectionAction(apiContext, provider.id, action),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["provider-connections"] }),
    onError: errorToast("Provider action failed"),
  });
  const login = useMutation({
    mutationFn: ({ popup }: { popup: Window | null }) =>
      api.providerConnectionLoginInfo(apiContext, provider.id).then((info) => ({ info, popup })),
    onSuccess: ({ info, popup }) => {
      const loginUrl = info.login_url || info.public_login_path;
      if (!loginUrl) {
        popup?.close();
        toast.error("Provider sign-in URL is not available");
        return;
      }
      if (popup) popup.location.href = loginUrl;
      else window.open(loginUrl, "_blank", "noopener,noreferrer");
      toast.success("Secure sign-in opened");
      void pollRuntimeLogin();
    },
    onError: (error, variables) => {
      variables.popup?.close();
      errorToast("Provider sign-in failed")(error);
    },
  });

  useEffect(
    () => () => {
      loginPollGeneration.current += 1;
    },
    [provider.id, apiContext.tenantId, apiContext.projectId],
  );

  async function pollRuntimeLogin() {
    const generation = loginPollGeneration.current + 1;
    loginPollGeneration.current = generation;
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, attempt === 0 ? 2500 : 4000));
      if (loginPollGeneration.current !== generation) return;
      try {
        const next = await api.providerConnectionAction(apiContext, provider.id, "health");
        if (loginPollGeneration.current !== generation) return;
        await queryClient.invalidateQueries({ queryKey: ["provider-connections"] });
        if (next.status === "active") {
          toast.success("Runtime sign-in completed");
          return;
        }
        if (["failed", "unhealthy"].includes(next.status)) {
          toast.error(next.last_error || "Runtime sign-in did not complete");
          return;
        }
      } catch {
        if (attempt === 29) toast.error("Runtime sign-in status could not be verified");
      }
    }
    toast.info("Sign-in is still pending. Run Health after completing the browser flow.");
  }

  function openRuntimeLogin() {
    const popup = window.open("about:blank", "_blank");
    login.mutate({ popup });
  }
  const sourceEligibleOffers = provider.status === "active"
    ? provider.models.filter(isSourceEligibleOffer)
    : [];
  const publishing = useApplicationDistribution().resourcePublishing;
  const publicationEnabled = Boolean(publishing);
  const recommendedAction = getProviderRecommendedAction(provider, publishing?.providerPolicy);
  const hasPublishableOffers = publishing ? provider.models.some(publishing.providerPolicy.canPublish) : false;
  const actionPending = lifecycle.isPending || refresh.isPending || login.isPending;

  function runRecommendedAction() {
    if (recommendedAction === "wait") return;
    if (recommendedAction === "repair") lifecycle.mutate("repair");
    else if (recommendedAction === "login") openRuntimeLogin();
    else if (recommendedAction === "start") lifecycle.mutate("start");
    else if (recommendedAction === "health") lifecycle.mutate("health");
    else if (recommendedAction === "refresh") refresh.mutate();
    else if (recommendedAction === "use_source") onUseSources();
    else if (recommendedAction === "publish") onPublish();
  }

  const recommendedActionLabel: Record<RuntimeRecommendedAction, string> = {
    wait: provider.status === "stopping" ? "Stopping provider…" : "Starting provider…",
    repair: "Repair provider",
    login: provider.status === "login_required" ? "Sign in" : "Resume sign-in",
    start: "Start provider",
    health: "Review health",
    refresh: "Refresh models",
    use_source: "Use in source",
    publish: "Publish models",
    operational: "Operational",
  };

  return (
    <div className="provider-runtime-detail">
      <header className="provider-runtime-detail__header">
        <div className="min-w-0">
          <p className="font-mono text-xs uppercase tracking-widest text-muted">
            Provider
          </p>
          <h2 className="mt-1 text-xl font-semibold">{provider.name}</h2>
          <p className="mt-1 text-xs text-muted">
            {humanEngine(provider.engine)} · {provider.upstream_provider}
          </p>
        </div>
        <div className="provider-runtime-detail__actions">
          {recommendedAction === "operational" ? (
            <span className="provider-operational-state"><Check size={15} /> Operational</span>
          ) : (
            <button className="btn btn-primary" onClick={runRecommendedAction} disabled={actionPending || recommendedAction === "wait"}>
              {actionPending ? <Loader2 size={15} className="animate-spin" /> : runtimeActionIcon(recommendedAction)}
              {recommendedActionLabel[recommendedAction]}
            </button>
          )}
          <details className="provider-runtime-more">
            <summary className="btn">More actions <ChevronDown size={14} /></summary>
            <div className="provider-runtime-more__menu">
              <button onClick={() => setEditProviderOpen(true)}>
                Edit connection
              </button>
              {provider.status === "active" ? (
                <button onClick={() => lifecycle.mutate("stop")} disabled={lifecycle.isPending}>
                  <Square size={15} /> Stop provider
                </button>
              ) : recommendedAction !== "start" ? (
                <button onClick={() => lifecycle.mutate("start")} disabled={lifecycle.isPending}>
                  <Play size={15} /> Start provider
                </button>
              ) : null}
              {recommendedAction !== "health" && (
                <button onClick={() => lifecycle.mutate("health")} disabled={lifecycle.isPending}>
                  <RefreshCw size={15} /> Health check
                </button>
              )}
              {recommendedAction !== "refresh" && (
                <button onClick={() => refresh.mutate()} disabled={refresh.isPending}>
                  <RefreshCw size={15} /> Refresh models
                </button>
              )}
              {recommendedAction !== "use_source" && (
                <button onClick={() => onUseSources()} disabled={!sourceEligibleOffers.length}>
                  <Plus size={15} /> Use in source
                </button>
              )}
              {publicationEnabled && recommendedAction !== "publish" && (
                <button onClick={onPublish} disabled={!hasPublishableOffers}>
                  <Share2 size={15} /> <ProviderPublicationSummary surface="action" provider={provider} />
                </button>
              )}
              <button className="is-danger" onClick={onRemove}>
                <Trash2 size={15} /> Delete provider
              </button>
            </div>
          </details>
        </div>
      </header>
      <dl className="provider-runtime-facts" data-publishing={publicationEnabled}>
        <Fact label="Connection" value={provider.status} />
        <Fact label="Models" value={String(provider.models.length)} />
        <ProviderPublicationSummary surface="facts" provider={provider} />
        <Fact label="Quota" value={formatProviderQuota(provider)} />
        <Fact label="Last checked" value={formatRelativeDate(provider.last_checked_at)} />
      </dl>
      {provider.last_error && (
        <div className="provider-runtime-error" role="status">
          <CircleAlert size={17} />
          <div><strong>Provider needs attention</strong><p>{provider.last_error}</p></div>
        </div>
      )}
      {(provider.health_history ?? []).length > 0 && (
        <details className="provider-technical-details">
          <summary>Recent health checks</summary>
          <div className="divide-y divide-border">
            {(provider.health_history ?? []).map((check, index) => (
              <div className="flex items-start justify-between gap-4 py-3" key={`${check.checked_at}-${index}`}>
                <div className="min-w-0">
                  <StatusBadge status={check.status} />
                  {check.reason && <p className="mt-1 truncate text-xs text-muted" title={check.reason}>{check.reason}</p>}
                </div>
                <span className="shrink-0 text-xs text-muted">
                  {check.latency_ms ? `${check.latency_ms} ms · ` : ""}{formatRelativeDate(check.checked_at)}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}
      <section className="provider-offer-catalog">
        <div className="provider-offer-catalog__header">
          <div>
            <h3>Models</h3>
            <p className="text-sm text-muted">
              Review available models, health, and Source readiness.
            </p>
          </div>
          <button
            className="btn"
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending}
          >
            <RefreshCw
              size={15}
              className={refresh.isPending ? "animate-spin" : ""}
            />{" "}
            Refresh models
          </button>
        </div>
        {provider.models.length ? (
          <div className="provider-offer-table" data-publishing={publicationEnabled} role="table" aria-label="Provider models">
            <div className="provider-offer-table__head" role="row">
              <span role="columnheader">Model</span>
              <span role="columnheader">Nexus model</span>
              <span role="columnheader">Health</span>
              <ProviderPublicationSummary surface="price-heading" />
              <span role="columnheader">Source</span>
              <ProviderPublicationSummary surface="publication-heading" />
              <span role="columnheader">Actions</span>
            </div>
            {provider.models.map((offer) => (
              <div className="provider-offer-table__row" role="row" key={offer.id}>
                <div role="cell" data-label="Model">
                  <strong>{offer.upstream_model_id}</strong>
                  <small>{humanStatus(offer.status)}</small>
                </div>
                <div role="cell" data-label="Nexus model">
                  {offer.canonical_model_key || offer.upstream_model_id}
                </div>
                <div role="cell" data-label="Health"><StatusBadge status={offer.health_status} /></div>
                <ProviderPublicationSummary surface="price" offer={offer} />
                <div role="cell" data-label="Source">
                  {offer.source_ids.length ? (
                    <button className="provider-table-link" onClick={() => navigate("/model-pool?view=sources")}>Connected</button>
                  ) : <span>Not connected</span>}
                </div>
                <ProviderPublicationSummary surface="publication" offer={offer} />
                <div role="cell" data-label="Actions" className="provider-offer-table__actions">
                  <button
                    className="btn"
                    onClick={() => refreshOffer.mutate(offer.id)}
                    disabled={provider.status !== "active" || refreshOffer.isPending}
                    aria-label={`Refresh ${offer.upstream_model_id}`}
                    title={provider.status === "active" ? "Refresh this model" : "Start the Provider before refreshing this model."}
                  >
                    <RefreshCw
                      size={14}
                      className={refreshOffer.isPending && refreshOffer.variables === offer.id ? "animate-spin" : ""}
                    />
                    Refresh
                  </button>
                  {publishing && <button className="btn" onClick={() => setEditingOffer(offer)}>Edit</button>}
                  {!offer.source_ids.length && (
                    <button
                      className="btn"
                      onClick={() => onUseSources([offer.id])}
                      disabled={provider.status !== "active" || !isSourceEligibleOffer(offer)}
                      title={provider.status === "active" ? sourceOfferBlockReason(offer) : "Start the Provider before creating a Source."}
                    >
                      Use model
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="provider-offer-empty">
            <strong>No models discovered</strong>
            <p>{provider.status === "active" ? "Refresh the Provider catalog to discover available models." : "Start the Provider, then refresh its model catalog."}</p>
            {provider.status === "active" && <button className="btn" onClick={() => refresh.mutate()}>Refresh models</button>}
          </div>
        )}
      </section>
      <details className="provider-technical-details">
        <summary>Technical details</summary>
        <dl>
          <Fact label="Provider ID" value={compactId(provider.id)} />
          <Fact label="Engine" value={humanEngine(provider.engine)} />
          <Fact label="Internal runtime" value={compactId(providerRuntimeId(provider)) || "Missing"} />
          <Fact label="Authentication" value={humanAuthMode(provider.auth_mode)} />
        </dl>
      </details>
      <ProviderModelSettingsDialog
        provider={provider}
        offer={editingOffer}
        onClose={() => setEditingOffer(null)}
      />
      <EditProviderDialog
        provider={provider}
        open={editProviderOpen}
        onClose={() => setEditProviderOpen(false)}
      />
    </div>
  );
}

function EditProviderDialog({
  provider,
  open,
  onClose,
}: {
  provider: ProviderConnection;
  open: boolean;
  onClose: () => void;
}) {
  const { apiContext } = useAuth();
  const queryClient = useQueryClient();
  const [form, setForm] = useState({
    name: provider.name,
    engine: provider.engine as "direct_api" | "codex_proxy" | "cliproxyapi",
    upstreamProvider: provider.upstream_provider,
    url: provider.url,
    key: "",
  });
  useEffect(() => {
    if (!open) return;
    setForm({
      name: provider.name,
      engine: provider.engine as typeof form.engine,
      upstreamProvider: provider.upstream_provider,
      url: provider.url,
      key: "",
    });
  }, [open, provider.id, provider.name, provider.engine, provider.upstream_provider, provider.url]);
  const update = useMutation({
    mutationFn: () => api.updateProviderConnection(apiContext, provider.id, {
      name: form.name.trim(),
      engine: form.engine,
      upstream_provider: form.engine === "cliproxyapi" ? form.upstreamProvider : form.engine === "codex_proxy" ? "openai" : provider.upstream_provider,
      url: form.engine === "direct_api" ? form.url : "",
      ...(form.key ? { key: form.key } : {}),
    }),
    onSuccess: () => {
      toast.success("Provider updated");
      queryClient.invalidateQueries({ queryKey: ["provider-connections"] });
      onClose();
    },
    onError: errorToast("Provider update failed"),
  });
  const engineChanged = form.engine !== provider.engine || (
    form.engine === "cliproxyapi" && form.upstreamProvider !== provider.upstream_provider
  );
  const directNeedsKey = form.engine === "direct_api" && provider.engine !== "direct_api" && !form.key;
  const valid = Boolean(form.name.trim()) && (form.engine !== "direct_api" || Boolean(form.url.trim())) && !directNeedsKey;
  return (
    <NexilumeDialog
      open={open}
      onClose={onClose}
      busy={update.isPending}
      title="Edit provider"
      description="Update the connection label or access method. Stop the Provider before changing its Engine."
    >
      <div className="space-y-4">
        <Field label="Name"><input className="input" value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} /></Field>
        <Field label="Engine">
          <select className="input" value={form.engine} onChange={(event) => setForm((current) => ({ ...current, engine: event.target.value as typeof current.engine }))}>
            <option value="direct_api">Direct API</option>
            <option value="codex_proxy">Codex</option>
            <option value="cliproxyapi">CLIProxyAPI</option>
          </select>
        </Field>
        {form.engine === "cliproxyapi" && (
          <Field label="Provider">
            <select className="input" value={form.upstreamProvider} onChange={(event) => setForm((current) => ({ ...current, upstreamProvider: event.target.value }))}>
              {cliProxyLoginProviders.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </Field>
        )}
        {form.engine === "direct_api" && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="API base URL"><input className="input" type="url" value={form.url} onChange={(event) => setForm((current) => ({ ...current, url: event.target.value }))} /></Field>
            <Field label="API key" hint={provider.engine === "direct_api" ? "Leave blank to keep the current key." : "Required when switching to Direct API."}>
              <input className="input" type="password" value={form.key} onChange={(event) => setForm((current) => ({ ...current, key: event.target.value }))} autoComplete="new-password" />
            </Field>
          </div>
        )}
        {engineChanged && !["stopped", "created", "failed"].includes(provider.status) && (
          <p className="provider-offer-dialog__error">Stop this Provider before changing its Engine or upstream Provider.</p>
        )}
        <DialogActions onClose={onClose} onSubmit={() => update.mutate()} label="Save changes" disabled={!valid || update.isPending || (engineChanged && !["stopped", "created", "failed"].includes(provider.status))} />
      </div>
    </NexilumeDialog>
  );
}

function CreateAccountDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (accountId: string) => void;
}) {
  const { apiContext, projects, projectId } = useAuth();
  const queryClient = useQueryClient();
  const [ownership, setOwnership] = useState<import("../lib/types").ResourceOwnershipInput>(
    projectId ? { scope: "project", project_id: projectId } : { scope: "organization", project_id: null },
  );
  const [form, setForm] = useState({
    name: "",
    provider: "openai",
    url: "",
    key: "",
    engine: "direct_api" as "direct_api" | "codex_proxy" | "cliproxyapi",
  });
  const create = useMutation({
    mutationFn: () =>
      api.createProviderConnection(apiContext, {
        name: form.name.trim(),
        ownership,
        engine: form.engine,
        upstream_provider: form.engine === "cliproxyapi" ? form.provider : form.engine === "codex_proxy" ? "openai" : "openai-compatible",
        url: form.engine === "direct_api" ? form.url : "",
        key: form.engine === "direct_api" ? form.key : "",
      }),
    onSuccess: async (provider) => {
      toast.success("Provider connected");
      setForm((current) => ({ ...current, name: "", key: "" }));
      await queryClient.invalidateQueries({ queryKey: ["provider-connections"] });
      onCreated(provider.id);
      onClose();
    },
    onError: errorToast("Provider connection failed"),
  });
  const canSubmit =
    form.engine === "direct_api"
      ? Boolean(form.name.trim() && form.url.trim() && form.key)
      : Boolean(form.name.trim());
  return (
    <NexilumeDialog
      open={open}
      onClose={onClose}
      busy={create.isPending}
      title="Connect Provider"
      description="Name the connection and choose how Nexus should access it."
    >
      <div className="space-y-5">
        <div>
          <p className="mb-2 font-mono text-xs uppercase tracking-widest text-muted">Connection engine</p>
          <NexilumeTabs
            label="Provider engine"
            variant="compact"
            value={form.engine}
            onChange={(value) => setForm((current) => ({ ...current, engine: value as typeof current.engine }))}
            options={[
              { value: "direct_api", label: "API key" },
              { value: "codex_proxy", label: "Codex" },
              { value: "cliproxyapi", label: "CLIProxyAPI" },
            ]}
          />
        </div>
        <Field label="Name" hint="A label for this Provider Account in Nexus.">
          <input
            className="input"
            value={form.name}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                name: event.target.value,
              }))
            }
            placeholder="e.g. Production API or Team Claude"
            autoFocus
            required
          />
        </Field>
        <ResourceOwnershipPicker projects={projects} value={ownership} onChange={setOwnership} />

        {form.engine === "direct_api" ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="API base URL">
              <input
                className="input"
                type="url"
                value={form.url}
                onChange={(event) => setForm((current) => ({ ...current, url: event.target.value }))}
                required
              />
            </Field>
            <Field label="API key" hint="Stored encrypted and never returned.">
              <input
                className="input font-mono"
                type="password"
                autoComplete="new-password"
                value={form.key}
                onChange={(event) => setForm((current) => ({ ...current, key: event.target.value }))}
                required
              />
            </Field>
          </div>
        ) : (
          <div className="grid gap-4 border border-border p-4 sm:grid-cols-2">
            {form.engine === "cliproxyapi" && (
              <Field label="Provider">
                <select
                  className="input"
                  aria-label="Provider"
                  value={form.provider}
                  onChange={(event) => setForm((current) => ({ ...current, provider: event.target.value }))}
                >
                  {cliProxyLoginProviders.map((provider) => (
                    <option key={provider.value} value={provider.value}>{provider.label}</option>
                  ))}
                </select>
              </Field>
            )}
            <p className="text-sm leading-6 text-muted sm:col-span-2">
              {form.engine === "cliproxyapi"
                ? `CLIProxyAPI will open ${form.provider === "claude" ? "Claude" : "OpenAI"} browser sign-in and expose an OpenAI-compatible API.`
                : "After connecting, start the Provider and complete the secure browser sign-in."}
            </p>
          </div>
        )}
        <DialogActions
          onClose={onClose}
          onSubmit={() => create.mutate()}
          label="Connect provider"
          disabled={!canSubmit || create.isPending}
        />
      </div>
    </NexilumeDialog>
  );
}

function RemoveProviderDialog({
  provider,
  onClose,
  onRemoved,
}: {
  provider: ProviderConnection | null;
  onClose: () => void;
  onRemoved: (providerId: string) => void;
}) {
  const { apiContext } = useAuth();
  const queryClient = useQueryClient();
  const [confirmation, setConfirmation] = useState("");
  const managementCopy = useResourceManagementCopy();
  useEffect(() => setConfirmation(""), [provider?.id]);
  const impact = useQuery({
    queryKey: ["provider-connection-deletion-impact", apiContext, provider?.id],
    queryFn: () => api.providerConnectionDeletionImpact(apiContext, provider!.id),
    enabled: Boolean(provider),
  });
  const remove = useMutation({
    mutationFn: () => api.removeProviderConnection(apiContext, provider!.id, confirmation),
    onSuccess: async () => {
      const removedId = provider!.id;
      toast.success("Provider deleted");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["provider-connections"] }),
        queryClient.invalidateQueries({ queryKey: ["provider-runtimes"] }),
      ]);
      onRemoved(removedId);
    },
    onError: errorToast("Provider could not be deleted"),
  });
  const deletion = impact.data;
  const confirmed = !deletion?.requires_name_confirmation || confirmation === deletion.provider_name;
  return (
    <NexilumeDialog
      open={Boolean(provider)}
      onClose={onClose}
      busy={remove.isPending}
      title="Delete provider"
      description={managementCopy.providerDeletion}
    >
      {provider && (
        <div className="provider-remove-dialog">
          {impact.isLoading ? (
            <div className="provider-remove-dialog__loading"><Loader2 size={16} className="animate-spin" /> Checking dependencies…</div>
          ) : impact.isError ? (
            <div className="provider-runtime-error" role="alert">
              <CircleAlert size={17} />
              <div><strong>Deletion impact is unavailable</strong><p>Retry before removing this Provider.</p></div>
              <button className="btn" onClick={() => void impact.refetch()}>Retry</button>
            </div>
          ) : deletion ? (
            <>
              <dl className="provider-remove-dialog__impact">
                <Fact label="Provider" value={deletion.provider_name} />
                <Fact label="Status" value={deletion.runtime_status} />
                <Fact label="Models" value={String(deletion.model_count)} />
                {managementCopy.providerPublishedLabel && <Fact label={managementCopy.providerPublishedLabel} value={String(deletion.published_count)} />}
                <Fact label="Sources removed" value={String(deletion.source_count)} />
              </dl>
              <p className="provider-remove-dialog__warning">
                {managementCopy.providerDeletionWarning}
              </p>
              {deletion.requires_name_confirmation && (
                <Field label={`Enter ${deletion.provider_name} to confirm`}>
                  <input
                    className="input"
                    value={confirmation}
                    onChange={(event) => setConfirmation(event.target.value)}
                    autoComplete="off"
                  />
                </Field>
              )}
            </>
          ) : null}
          <DialogActions
            onClose={onClose}
            onSubmit={() => remove.mutate()}
            label="Delete provider"
            disabled={!deletion || !confirmed || remove.isPending}
          />
        </div>
      )}
    </NexilumeDialog>
  );
}

function LegacyRuntimeRecovery({ runtimes }: { runtimes: ProviderRuntimeAccount[] }) {
  const { apiContext } = useAuth();
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: (runtimeId: string) => api.deleteProviderRuntime(apiContext, runtimeId),
    onSuccess: () => {
      toast.success("Legacy runtime removed");
      queryClient.invalidateQueries({ queryKey: ["provider-runtimes"] });
    },
    onError: errorToast("Legacy runtime cleanup failed"),
  });
  return (
    <details className="provider-legacy-recovery">
      <summary><CircleAlert size={15} /> Legacy recovery · {runtimes.length}</summary>
      <p>These historical runtimes are not connected to a Provider. They are excluded from normal Provider status.</p>
      <div>
        {runtimes.map((runtime) => (
          <div key={runtime.id}>
            <span><strong>{runtime.name}</strong><small>{humanEngine(runtime.runtime_type)} · {humanStatus(runtime.status)}</small></span>
            <button className="btn" onClick={() => remove.mutate(runtime.id)} disabled={remove.isPending}>
              <Trash2 size={14} /> Remove
            </button>
          </div>
        ))}
      </div>
    </details>
  );
}



function ProviderWorkbenchSkeleton() {
  return (
    <div className="provider-workbench-skeleton" aria-label="Loading Provider operations">
      <div>{[0, 1, 2].map((item) => <span key={item} />)}</div>
      <div><span /><span /><span /></div>
    </div>
  );
}

function ProviderPartialFailure({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="provider-partial-failure" role="alert">
      <CircleAlert size={17} />
      <div>
        <strong>Some Provider status is unavailable</strong>
        <p>Loaded Providers remain usable. Retry to restore missing connection or model details.</p>
      </div>
      <button className="btn" onClick={onRetry}>Retry</button>
    </div>
  );
}

function useCompactInspector() {
  const [compact, setCompact] = useState(() =>
    typeof window !== "undefined" && window.matchMedia("(max-width: 1439px)").matches,
  );
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1439px)");
    const update = () => setCompact(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return compact;
}

function providerNeedsAttention(provider: ProviderConnection) {
  return ["failed", "login_required", "unhealthy", "repair_required", "degraded"].includes(provider.status);
}

function compareProviders(left: ProviderConnection, right: ProviderConnection) {
  const attentionDifference = Number(providerNeedsAttention(right)) - Number(providerNeedsAttention(left));
  if (attentionDifference) return attentionDifference;
  const leftDate = Date.parse(left.last_checked_at ?? left.updated_at) || 0;
  const rightDate = Date.parse(right.last_checked_at ?? right.updated_at) || 0;
  return rightDate - leftDate;
}

export function getProviderRecommendedAction(provider: ProviderConnection, publication?: ProviderPublicationPolicy): RuntimeRecommendedAction {
  if (["starting", "stopping"].includes(provider.status)) return "wait";
  const loginFailure = provider.engine !== "direct_api"
    && (provider.status === "login_required" || (provider.status === "failed" && /login|sign.?in|auth/i.test(provider.last_error)));
  if (loginFailure) return "login";
  if (["stopped", "created", "disabled"].includes(provider.status)) return "start";
  if (provider.status === "repair_required") return "repair";
  if (["failed", "unhealthy", "degraded"].includes(provider.status)) return "health";
  if (!provider.models.length) return "refresh";
  if (provider.status === "active" && provider.models.some(isSourceEligibleOffer)) return "use_source";
  if (publication?.needsPublication(provider.models)) return "publish";
  return "operational";
}

export function getRuntimeRecommendedAction(runtime: ProviderRuntimeAccount, publication?: ProviderPublicationPolicy): RuntimeRecommendedAction {
  if (["starting", "stopping"].includes(runtime.status)) return "wait";
  const loginFailure = runtime.runtime_type !== "direct_api"
    && (runtime.status === "login_required" || (runtime.status === "failed" && /login|sign.?in|auth/i.test(runtime.last_error)));
  if (loginFailure) return "login";
  if (["stopped", "disabled"].includes(runtime.status)) return "start";
  if (["failed", "unhealthy", "degraded"].includes(runtime.status)) return "health";
  if (!runtime.model_offers.length) return "refresh";
  if (runtime.status === "active" && runtime.model_offers.some(isSourceEligibleOffer)) return "use_source";
  if (publication?.needsPublication(runtime.model_offers)) return "publish";
  return "operational";
}

function runtimeActionIcon(action: RuntimeRecommendedAction) {
  if (action === "wait") return <Loader2 size={15} className="animate-spin" />;
  if (action === "repair") return <RefreshCw size={15} />;
  if (action === "login") return <LogIn size={15} />;
  if (action === "start") return <Play size={15} />;
  if (action === "health" || action === "refresh") return <RefreshCw size={15} />;
  if (action === "use_source") return <Plus size={15} />;
  if (action === "publish") return <Share2 size={15} />;
  return <Check size={15} />;
}

function formatProviderQuota(provider: ProviderConnection) {
  const tokens = provider.quota.remaining_tokens;
  const requests = provider.quota.remaining_requests;
  if (typeof tokens === "number") return `${tokens.toLocaleString()} tokens`;
  if (typeof requests === "number") return `${requests.toLocaleString()} requests`;
  return provider.status === "active" ? humanStatus(provider.quota.status) : "Not verified";
}

function formatRelativeDate(value: string | null) {
  if (!value) return "Not checked";
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "Not checked";
  const elapsed = Date.now() - timestamp;
  if (elapsed < 60_000) return "just now";
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)}m ago`;
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)}h ago`;
  if (elapsed < 604_800_000) return `${Math.floor(elapsed / 86_400_000)}d ago`;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(timestamp));
}

function isSourceEligibleOffer(offer: ProviderRuntimeModelOffer) {
  return (
    ["detected", "confirmed"].includes(offer.status) &&
    Boolean(offer.canonical_model_id) &&
    ["healthy", "degraded"].includes(offer.health_status) &&
    offer.source_ids.length === 0
  );
}

function sourceOfferBlockReason(offer: ProviderRuntimeModelOffer) {
  if (offer.source_ids.length) return "A Source already exists for this Model Offer.";
  if (!offer.canonical_model_id) return "Refresh this model to apply its upstream model name.";
  if (!["detected", "confirmed"].includes(offer.status)) return "This Model Offer is not currently advertised.";
  if (!["healthy", "degraded"].includes(offer.health_status)) return `Model Offer health is ${offer.health_status}.`;
  return "Create an immutable Source from this Model Offer.";
}

function providerConnectionSourceOrigin(provider: ProviderConnection): RuntimeSourceComposerOrigin {
  return {
    id: providerRuntimeId(provider),
    name: provider.name,
    kind: "provider_runtime",
    status: provider.status,
    offers: provider.models.map((offer) => ({
      id: offer.id,
      canonicalModelKey: offer.canonical_model_key || offer.upstream_model_id,
      canonicalModelName: offer.canonical_model_key || offer.upstream_model_id,
      upstreamModelId: offer.upstream_model_id,
      status: offer.status,
      healthStatus: offer.health_status,
      canonicalMapped: Boolean(offer.canonical_model_id),
      existingSourceIds: offer.source_ids,
    })),
  };
}

function humanAuthMode(value: string) {
  if (value === "api_key") return "API key";
  if (value === "username_password_login") return "CLIProxy credentials";
  if (value === "interactive_login") return "Browser sign-in";
  return value.replaceAll("_", " ");
}

function humanEngine(value: string) {
  if (value === "direct_api") return "Direct API";
  if (value === "codex_proxy") return "Codex";
  if (value === "cliproxyapi") return "CLIProxyAPI";
  return humanStatus(value);
}

function humanStatus(value: string) {
  return (value || "unknown").replaceAll("_", " ");
}
