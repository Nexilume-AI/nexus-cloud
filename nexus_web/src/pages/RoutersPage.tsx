import { t, useLocale, getLocale } from "../localization";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Copy,
  ArrowLeft,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  Braces,
  CheckCircle2,
  CircleAlert,
  Clock3,
  FlaskConical,
  GitBranch,
  KeyRound,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  Rocket,
  Search,
  Share2,
  SlidersHorizontal,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Link, useSearchParams } from "react-router-dom";

import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import { useCatalogSearch, useCursorPage } from "../lib/useCursorPage";
import { CursorPageControls } from "../components/CursorPageControls";
import type { ModelGroup, Router, RouterAggregationCandidate, RouterCredentials, RouterTestResult, RouterTraceRecord } from "../lib/types";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Form";
import { StatusBadge } from "../components/Badge";
import { ResourceAccessState, ResourceOwnershipBadge, ResourceOwnershipPicker } from "../components/ResourceOwnership";
import { NexilumeTopology } from "../components/NexilumeTopology";
import { NexilumeTabs } from "../components/NexilumeControls";
import {
  ShareResourceModal,
  ResourceSharingAction,
  type ShareResourceTarget,
} from "../components/ShareResourceModal";

const starterRouter = `def route(request, candidates, context):
    ordered = sorted(
        candidates,
        key=lambda item: (
            item.get("best_health_status") != "healthy",
            float(item.get("lowest_price_per_1k_tokens") or 0),
            item.get("lowest_latency_ms") or 10_000_000,
        ),
    )
    return {
        "ordered_pool_ids": [item["id"] for item in ordered],
        "reason": "Prefer healthy, low-cost Model Pools",
    }
`;

type RouterModal = "create" | "advanced" | "rename" | "mapping" | "delete" | null;
type RouterWorkspaceView = "overview" | "configure" | "activity";
type RouterTypeFilter = "all" | "execution" | "aggregation";
type RouterStatusFilter = "all" | "attention";

export function RoutersPage() {
  useLocale();
  const { apiContext, isContextReady, projects, projectId } = useAuth();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialView = searchParams.get("view");
  const [selectedId, setSelectedIdState] = useState(searchParams.get("router") ?? "");
  const [routerView, setRouterViewState] = useState<RouterWorkspaceView>(initialView === "configure" || initialView === "activity" ? initialView : "overview");
  const [mobileDetailOpen, setMobileDetailOpen] = useState(Boolean(searchParams.get("router")));
  const [activeModal, setActiveModal] = useState<RouterModal>(null);
  const [routerName, setRouterName] = useState("");
  const [createRouterType, setCreateRouterType] = useState<"execution" | "aggregation">("execution");
  const [createOwnership, setCreateOwnership] = useState<import("../lib/types").ResourceOwnershipInput>(
    projectId ? { scope: "project", project_id: projectId } : { scope: "organization", project_id: null },
  );
  const [createStrategy, setCreateStrategy] = useState("manual_priority");
  const [createPoolId, setCreatePoolId] = useState("");
  const [routerEditName, setRouterEditName] = useState("");
  const [selectedModelGroupIds, setSelectedModelGroupIds] = useState<string[]>(
    [],
  );
  const [policy, setPolicy] = useState("manual_priority");
  const [routerCode, setRouterCode] = useState(starterRouter);
  const [credentials, setCredentials] = useState<RouterCredentials | null>(
    null,
  );
  const [shareTarget, setShareTarget] = useState<ShareResourceTarget | null>(
    null,
  );
  const [poolToAdd, setPoolToAdd] = useState("");
  const [childRouterId, setChildRouterId] = useState("");
  const [childOutputId, setChildOutputId] = useState("");
  const [childModelName, setChildModelName] = useState("");
  const [dryRun, setDryRun] = useState<RouterTestResult | null>(null);
  const [dryRunModel, setDryRunModel] = useState("");
  const [dryRunPrompt, setDryRunPrompt] = useState("Explain the best route for this request.");
  const [selectedTrace, setSelectedTrace] = useState<RouterTraceRecord | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const poolSelectRef = useRef<HTMLSelectElement>(null);
  const routerTypeFilter: RouterTypeFilter = searchParams.get("type") === "execution" || searchParams.get("type") === "aggregation" ? searchParams.get("type") as RouterTypeFilter : "all";
  const routerStatusFilter: RouterStatusFilter = searchParams.get("status") === "attention" ? "attention" : "all";

  function updateRouterUrl(patch: Record<string, string | null>, replace = false) {
    const next = new URLSearchParams(searchParams);
    Object.entries(patch).forEach(([key, value]) => {
      if (!value || value === "all" || (key === "view" && value === "overview")) next.delete(key);
      else next.set(key, value);
    });
    setSearchParams(next, { replace });
  }

  function selectRouter(routerId: string, openDetail = true) {
    setSelectedIdState(routerId);
    setMobileDetailOpen(openDetail);
    setDryRun(null);
    setSelectedTrace(null);
    updateRouterUrl({ router: routerId || null });
  }

  function setRouterView(view: RouterWorkspaceView) {
    setRouterViewState(view);
    updateRouterUrl({ view });
  }

  function setRouterTypeFilter(value: RouterTypeFilter) {
    updateRouterUrl({ type: value });
  }

  function setRouterStatusFilter(value: RouterStatusFilter) {
    updateRouterUrl({ status: value });
  }

  function handleRouterListKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const options = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("[role='option']"));
    if (!options.length) return;
    const current = Math.max(0, options.indexOf(document.activeElement as HTMLButtonElement));
    const nextIndex = event.key === "Home"
      ? 0
      : event.key === "End"
        ? options.length - 1
        : event.key === "ArrowDown"
          ? Math.min(options.length - 1, current + 1)
          : Math.max(0, current - 1);
    event.preventDefault();
    options[nextIndex].focus();
    options[nextIndex].click();
  }

  const [routerSearch, setRouterSearch] = useState(searchParams.get("q") ?? "");
  const catalogSearch = useCatalogSearch(routerSearch);
  const routerPaging = useCursorPage([apiContext, catalogSearch]);
  const routerPage = useQuery({
    queryKey: ["routers", apiContext, catalogSearch, routerPaging.cursor],
    queryFn: ({ signal }) => api.routersPage(apiContext, { q: catalogSearch, cursor: routerPaging.cursor }, signal),
    gcTime: 0,
    enabled: isContextReady,
  });
  const routers = { ...routerPage, data: routerPage.data?.items };
  const selectedListRouter = (routers.data ?? []).find((router) => router.id === selectedId);
  const visibleRouters = useMemo(
    () => (routers.data ?? []).filter((router) =>
      (routerTypeFilter === "all" || router.router_type === routerTypeFilter)
      && (routerStatusFilter === "all" || routerNeedsAttention(router)),
    ),
    [routers.data, routerStatusFilter, routerTypeFilter],
  );
  const selectedDetail = useQuery({
    queryKey: ["routers", apiContext, "detail", selectedId],
    queryFn: () => api.router(apiContext, selectedId),
    enabled: isContextReady && Boolean(selectedId) && Boolean(routerPage.data) && !routerPage.data?.items.some(row => row.id === selectedId),
  });

  const models = useQuery({
    queryKey: ["models", apiContext],
    queryFn: () => api.models(apiContext),
    enabled: isContextReady,
  });

  const topology = useQuery({
    queryKey: ["topology", apiContext],
    queryFn: () => api.topology(apiContext),
    enabled: isContextReady && Boolean(selectedId) && routerView === "overview" && selectedListRouter?.access?.can_read !== false,
  });

  const traces = useQuery({
    queryKey: ["router-traces", apiContext, selectedId],
    queryFn: () => api.routerTraces(apiContext, selectedId),
    enabled: isContextReady && Boolean(selectedId) && routerView === "activity" && selectedListRouter?.access?.can_read !== false,
  });

  const routerSource = useQuery({
    queryKey: ["router-source", apiContext, selectedId],
    queryFn: () => api.routerSource(apiContext, selectedId),
    enabled: isContextReady && Boolean(selectedId),
  });

  const selectedRouter = useMemo(
    () =>
      (routers.data ?? []).find((router) => router.id === selectedId) ?? selectedDetail.data ?? null,
    [routers.data, selectedId, selectedDetail.data],
  );

  useEffect(() => {
    const urlRouter = searchParams.get("router") ?? "";
    const urlView = searchParams.get("view");
    const normalizedView: RouterWorkspaceView = urlView === "configure" || urlView === "activity" ? urlView : "overview";
    const urlQuery = searchParams.get("q") ?? "";
    if (urlRouter !== selectedId) setSelectedIdState(urlRouter);
    if (normalizedView !== routerView) setRouterViewState(normalizedView);
    if (urlQuery !== routerSearch) setRouterSearch(urlQuery);
  }, [searchParams]);

  useEffect(() => {
    if (routerPage.isPending || visibleRouters.length === 0) return;
    if (!visibleRouters.some((router) => router.id === selectedId)) selectRouter(visibleRouters[0].id, false);
  }, [routerPage.isPending, selectedId, visibleRouters]);
  const aggregationCandidates = useQuery({
    queryKey: ["router-aggregation-candidates", apiContext, selectedId],
    queryFn: () => api.routerAggregationCandidates(apiContext, selectedId),
    enabled: isContextReady && Boolean(selectedId) && selectedRouter?.router_type === "aggregation",
  });
  useEffect(() => {
    if (!selectedId && (routers.data ?? []).length > 0) selectRouter(routers.data![0].id, false);
  }, [routers.data, selectedId]);
  useEffect(() => {
    if (selectedRouter?.strategy) {
      setPolicy(selectedRouter.strategy);
      setSelectedModelGroupIds(selectedRouter.model_group_ids ?? []);
      setRouterEditName(selectedRouter.name);
    }
  }, [
    selectedRouter?.id,
    selectedRouter?.name,
    selectedRouter?.strategy,
    selectedRouter?.model_group_ids,
  ]);

  useEffect(() => {
    setChildRouterId("");
    setChildOutputId("");
    setChildModelName("");
  }, [selectedRouter?.id]);

  useEffect(() => {
    if (selectedRouter?.router_type !== "aggregation") return;
    const candidates = aggregationCandidates.data ?? [];
    if (candidates.some((candidate) => candidate.router_id === childRouterId && candidate.available)) return;
    setChildRouterId("");
    setChildOutputId("");
    setChildModelName("");
  }, [aggregationCandidates.data, childRouterId, selectedRouter?.router_type]);

  useEffect(() => {
    if (activeModal !== "advanced" || !routerSource.data) return;
    setRouterCode(
      routerSource.data.has_source ? routerSource.data.source : starterRouter,
    );
  }, [activeModal, routerSource.data]);

  const createRouter = useMutation({
    mutationFn: () => api.createRouter(apiContext, {
      name: routerName.trim(),
      router_type: createRouterType,
      ownership: createOwnership,
      strategy: createRouterType === "execution" ? createStrategy : "manual_priority",
      model_group_ids: createRouterType === "execution" && createPoolId ? [createPoolId] : [],
    }),
    onSuccess: async (router) => {
      toast.success(t("Router created"));
      setRouterName("");
      setCreateRouterType("execution");
      setCreateStrategy("manual_priority");
      setCreatePoolId("");
      setRouterEditName(router.name);
      selectRouter(router.id);
      setActiveModal(null);
      await queryClient.invalidateQueries({ queryKey: ["routers"] });
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Failed to create router"),
      ),
  });

  const applyRouterChanges = useMutation({
    mutationFn: () =>
      api.updateRouter(
        apiContext,
        selectedRouter!.id,
        selectedRouter!.router_type === "aggregation"
          ? { name: routerEditName.trim() }
          : {
              name: routerEditName.trim(),
              strategy: policy,
              model_group_ids: selectedModelGroupIds,
            },
      ),
    onSuccess: async () => {
      toast.success(t("Router changes applied"));
      if (activeModal === "rename") setActiveModal(null);
      await queryClient.invalidateQueries({ queryKey: ["routers"] });
      await queryClient.invalidateQueries({ queryKey: ["router-aggregation-candidates"] });
      await queryClient.invalidateQueries({ queryKey: ["topology"] });
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Failed to apply Router changes"),
      ),
  });

  const upload = useMutation({
    mutationFn: () => {
      const file = new File([routerCode], "router.py", {
        type: "text/x-python",
      });
      return api.uploadRouterFile(apiContext, selectedRouter!.id, file);
    },
    onSuccess: async () => {
      toast.success("router.py uploaded");
      await queryClient.invalidateQueries({ queryKey: ["routers"] });
      await queryClient.invalidateQueries({ queryKey: ["router-aggregation-candidates"] });
      await queryClient.invalidateQueries({ queryKey: ["router-source"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : t("Upload failed")),
  });

  const deploy = useMutation({
    mutationFn: () => api.deployRouter(apiContext, selectedRouter!.id),
    onSuccess: async () => {
      toast.success(t("Router deployed"));
      await queryClient.invalidateQueries({ queryKey: ["routers"] });
      await queryClient.invalidateQueries({ queryKey: ["router-aggregation-candidates"] });
      await queryClient.invalidateQueries({ queryKey: ["topology"] });
      await queryClient.invalidateQueries({ queryKey: ["router-source"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : t("Deploy failed")),
  });

  const deleteRouter = useMutation({
    mutationFn: () => api.deleteRouter(apiContext, selectedRouter!.id),
    onSuccess: async () => {
      toast.success(t("Router archived"));
      setActiveModal(null);
      setDeleteConfirmation("");
      selectRouter("", false);
      await queryClient.invalidateQueries({ queryKey: ["routers"] });
      await queryClient.invalidateQueries({ queryKey: ["topology"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : t("Failed to archive Router")),
  });

  const addChildBinding = useMutation({
    mutationFn: () => api.addRouterChildBinding(apiContext, selectedRouter!.id, {
      exposed_model_name: childModelName.trim(),
      child_output_id: childOutputId,
    }),
    onSuccess: async () => {
      toast.success(t("API model added"));
      setChildRouterId("");
      setChildOutputId("");
      setChildModelName("");
      setActiveModal(null);
      await queryClient.invalidateQueries({ queryKey: ["routers"] });
      await queryClient.invalidateQueries({ queryKey: ["router-aggregation-candidates"] });
      await queryClient.invalidateQueries({ queryKey: ["topology"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("Failed to add Router output")),
  });

  const removeChildBinding = useMutation({
    mutationFn: (bindingId: string) => api.removeRouterChildBinding(apiContext, selectedRouter!.id, bindingId),
    onSuccess: async () => {
      toast.success(t("Router output removed"));
      await queryClient.invalidateQueries({ queryKey: ["routers"] });
      await queryClient.invalidateQueries({ queryKey: ["router-aggregation-candidates"] });
      await queryClient.invalidateQueries({ queryKey: ["topology"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("Failed to remove Router output")),
  });

  const exportCredentials = useMutation({
    mutationFn: (routerId: string) =>
      api.exportRouterCredentials(apiContext, routerId),
    onSuccess: (payload) => {
      setCredentials(payload);
      toast.success(t("Router credentials ready"));
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : t("Failed to export router credentials"),
      ),
  });

  const testRouting = useMutation({
    mutationFn: () => {
      const fallbackModel = (models.data ?? []).find((model) => selectedModelGroupIds.includes(model.id));
      return api.testRouter(apiContext, selectedRouter!.id, {
        model: dryRunModel || selectedRouter!.output_models?.[0] || fallbackModel?.name || "routing-probe",
        messages: [{ role: "user", content: dryRunPrompt.trim() || "Nexus routing dry-run" }],
      });
    },
    onSuccess: (result) => setDryRun(result),
    onError: (error) => toast.error(error instanceof Error ? error.message : t("Routing dry-run failed")),
  });

  function openRouterModal(router: Router, modal: "advanced" | "rename" | "delete") {
    selectRouter(router.id);
    setRouterEditName(router.name);
    setPolicy(router.strategy);
    setActiveModal(modal);
  }

  function toggleModelGroup(modelId: string, checked: boolean) {
    setSelectedModelGroupIds((current) =>
      checked
        ? Array.from(new Set([...current, modelId]))
        : current.filter((id) => id !== modelId),
    );
  }

  function movePool(modelId: string, direction: -1 | 1) {
    setSelectedModelGroupIds((current) => {
      const index = current.indexOf(modelId);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  function addPool() {
    if (!poolToAdd) return;
    setSelectedModelGroupIds((current) => Array.from(new Set([...current, poolToAdd])));
    setPoolToAdd("");
  }

  function chooseChildRouter(candidate: RouterAggregationCandidate) {
    setChildRouterId(candidate.router_id);
    setChildOutputId(candidate.output_id);
    setChildModelName(candidate.model_name);
  }

  function openAggregationMapping() {
    setChildRouterId("");
    setChildOutputId("");
    setChildModelName("");
    setActiveModal("mapping");
  }

  function closeAggregationMapping() {
    if (addChildBinding.isPending) return;
    setChildRouterId("");
    setChildOutputId("");
    setChildModelName("");
    setActiveModal(null);
  }

  const customStrategyBlocked =
    policy === "custom" &&
    (routerSource.isLoading || !routerSource.data?.is_runtime_source);

  const hasUnsavedChanges = Boolean(
    selectedRouter &&
      (routerEditName.trim() !== selectedRouter.name ||
        (selectedRouter.router_type === "execution" &&
          (policy !== selectedRouter.strategy ||
            selectedModelGroupIds.join("|") !== selectedRouter.model_group_ids.join("|")))),
  );
  const visibleBoundModels = (models.data ?? []).filter((model) => selectedModelGroupIds.includes(model.id));
  const effectiveOutputModels = selectedRouter?.output_models?.length
    ? selectedRouter.output_models
    : visibleBoundModels.map((model) => model.name);
  const childBindings = selectedRouter?.child_bindings?.filter((binding) => binding.status !== "deleted" && binding.enabled) ?? [];
  const selectedChildRouter = (aggregationCandidates.data ?? []).find((candidate) => candidate.router_id === childRouterId);
  const candidateModelByOutputId = new Map(
    (aggregationCandidates.data ?? []).flatMap((candidate) => candidate.models.map((model) => [model.output_id, model] as const)),
  );
  const aggregationCatalog = Array.from(
    childBindings.reduce((groups, binding) => {
      const current = groups.get(binding.exposed_model_name) ?? [];
      current.push(binding);
      groups.set(binding.exposed_model_name, current);
      return groups;
    }, new Map<string, typeof childBindings>()),
  );
  const aggregationRouterUseCounts = childBindings.reduce((counts, binding) => {
    const routerId = binding.child_output.router_id;
    counts.set(routerId, (counts.get(routerId) ?? 0) + 1);
    return counts;
  }, new Map<string, number>());
  const hasAggregationOneToOneConflict = aggregationCatalog.some(([, bindings]) => bindings.length !== 1)
    || Array.from(aggregationRouterUseCounts.values()).some((count) => count !== 1);
  const aggregationUnavailableCount = aggregationCandidates.isSuccess
    ? childBindings.filter((binding) => !candidateModelByOutputId.get(binding.child_output.id)?.available).length
    : 0;
  const availableAggregationCandidates = (aggregationCandidates.data ?? []).filter((candidate) => candidate.available);
  const mappedAggregationCandidates = (aggregationCandidates.data ?? []).filter((candidate) => !candidate.available && candidate.already_bound);
  const unavailableAggregationCandidates = (aggregationCandidates.data ?? []).filter((candidate) => !candidate.available && !candidate.already_bound);
  const unavailablePoolCount = models.isSuccess
    ? selectedModelGroupIds.filter((id) => !visibleBoundModels.some((model) => model.id === id)).length
    : 0;
  const deployBlockedReason = selectedRouter?.router_type === "aggregation" && !childBindings.length
    ? t("Add at least one Execution Router mapping before deployment.")
    : selectedRouter?.router_type === "aggregation" && hasAggregationOneToOneConflict
      ? t("Repair legacy mappings so every API model and Execution Router has exactly one mapping.")
    : selectedRouter?.router_type === "execution" && !selectedModelGroupIds.length
      ? t("Add and apply at least one Model Pool before deployment.")
    : policy === "custom" && !routerSource.data?.is_runtime_source
      ? t("Upload and deploy router.py before using Custom policy.")
      : "";
  const dryRunBlockedReason = hasUnsavedChanges
    ? t("Apply your pending changes before running a routing test.")
    : selectedRouter?.status !== "deployed"
      ? t("Deploy this Router before running a production-equivalent test.")
      : !effectiveOutputModels.length
        ? t("Add at least one output model to run a routing test.")
        : unavailablePoolCount > 0
          ? t("One or more bound Model Pools are unavailable in the current project. Resolve those bindings before testing.")
          : "";
  const selectedCanRead = selectedRouter?.access?.can_read ?? true;
  const selectedCanEdit = selectedRouter?.access?.can_edit ?? true;
  const selectedNeedsConfiguration = selectedRouter?.router_type === "aggregation"
    ? childBindings.length === 0 || hasAggregationOneToOneConflict || aggregationUnavailableCount > 0
    : selectedModelGroupIds.length === 0 || unavailablePoolCount > 0;
  const recommendedAction = !selectedCanRead
    ? "access"
    : selectedNeedsConfiguration
      ? "configure"
      : hasUnsavedChanges
        ? "apply"
        : selectedRouter?.status !== "deployed"
          ? "deploy"
          : routerView === "activity" && traces.isSuccess && (traces.data?.items ?? []).length > 0
            ? "sdk"
            : "test";

  function renderRecommendedAction() {
    if (!selectedRouter) return null;
    if (recommendedAction === "access") return <Link className="btn btn-primary" to="/access?tab=roles"><CircleAlert size={15} />{t("Review access")}</Link>;
    if (recommendedAction === "configure") return <button className="btn btn-primary" onClick={() => setRouterView("configure")}><SlidersHorizontal size={15} />{t("Configure router")}</button>;
    if (recommendedAction === "apply") return <button className="btn btn-primary" onClick={() => applyRouterChanges.mutate()} disabled={applyRouterChanges.isPending || !selectedCanEdit}>{applyRouterChanges.isPending ? <Loader2 size={15} className="animate-spin" /> : <CheckCircle2 size={15} />}{t("Apply changes")}</button>;
    if (recommendedAction === "deploy") return <button className="btn btn-primary" onClick={() => deploy.mutate()} disabled={deploy.isPending || Boolean(deployBlockedReason) || !selectedCanEdit} title={deployBlockedReason}>{deploy.isPending ? <Loader2 size={15} className="animate-spin" /> : <Rocket size={15} />}{t("Deploy router")}</button>;
    if (recommendedAction === "sdk") return <button className="btn btn-primary" onClick={() => exportCredentials.mutate(selectedRouter.id)} disabled={exportCredentials.isPending}><KeyRound size={15} />{t("SDK setup")}</button>;
    return <button className="btn btn-primary" onClick={() => setRouterView("activity")}><FlaskConical size={15} />{t("Test router")}</button>;
  }

  function routerPyStatusText() {
    if (routerSource.isLoading) return t("Loading current router source...");
    if (!routerSource.data?.has_source)
      return t("No router.py uploaded yet. The editor is showing a starter template.");
    if (!routerSource.data.is_deployed)
      return t("Loaded draft {{0}}. Deploy it before using Custom router.py.", { 0: routerSource.data.version });
    if (selectedRouter?.strategy === "custom")
      return t("Loaded deployed {{0}}. This router.py is active.", { 0: routerSource.data.version });
    return t("Loaded deployed {{0}}. It will not run until Strategy is set to Custom router.py.", { 0: routerSource.data.version });
  }

  return (
    <div className="router-page grid gap-4">
      <div className="router-page-heading">
        <div>
          <h1 className="text-2xl font-semibold text-ink">{t("Routers")}</h1>
          <p className="mt-1 text-sm text-muted">{t("Operate model routes, inspect their lineage, and expose stable APIs.")}</p>
        </div>
        {(routers.data ?? []).length > 0 && (
          <button className="btn" onClick={() => setActiveModal("create")}>
            <Plus size={16} />{t("New router")}</button>
        )}
      </div>

      {routerPage.isPending ? <div className="router-page-state" role="status"><Loader2 size={18} className="animate-spin" />{t("Loading routers…")}</div> : routerPage.isError ? <div className="router-page-state is-error" role="alert"><CircleAlert size={18} /><span>{t("Routers could not be loaded.")}</span><button className="btn" onClick={() => { routerPaging.reset(); void routerPage.refetch(); }}>{t("Retry")}</button></div> : (routers.data ?? []).length === 0 ? (
        <EmptyState title={t("No routers")} description={t("Create a Router to choose between your Model Pools.")} action={<button className="btn btn-primary" onClick={() => setActiveModal("create")}><Plus size={16} />{t("New router")}</button>} />
      ) : selectedRouter ? (
        <section className={`router-studio ${mobileDetailOpen ? "has-mobile-detail" : ""}`}>
          <aside className="router-studio-rail">
            <div className="router-index-heading"><div><div className="tech-label">{t("ROUTER INDEX")}</div><strong>{routerPage.data?.items.length ?? 0}{" "}{t("visible")}</strong></div></div>
            <label className="router-index-search"><span className="sr-only">{t("Search routers")}</span><Search size={15} /><input type="search" placeholder={t("Search routers")} value={routerSearch} onChange={(event) => { setRouterSearch(event.target.value); updateRouterUrl({ q: event.target.value || null }, true); }} /></label>
            <div className="router-type-filter" role="group" aria-label={t("Router type filter")}>
              {(["all", "execution", "aggregation"] as const).map((value) => <button key={value} className={routerTypeFilter === value ? "is-active" : ""} aria-pressed={routerTypeFilter === value} onClick={() => setRouterTypeFilter(value)}>{value === "all" ? t("All") : value === "execution" ? t("Execution") : t("Aggregation")}</button>)}
            </div>
            <button className={`router-attention-filter ${routerStatusFilter === "attention" ? "is-active" : ""}`} onClick={() => setRouterStatusFilter(routerStatusFilter === "attention" ? "all" : "attention")} aria-pressed={routerStatusFilter === "attention"}><CircleAlert size={14} />{t("Needs attention")}</button>
            <div className="router-studio-list" role="listbox" aria-label={t("Routers")} onKeyDown={handleRouterListKeyDown}>
              {visibleRouters.map((router) => (
                <button key={router.id} role="option" aria-selected={router.id === selectedRouter.id} className={router.id === selectedRouter.id ? "is-selected" : ""} onClick={() => selectRouter(router.id)} title={router.name}>
                  <span className="router-list-title"><GitBranch size={15} /><strong>{router.name}</strong></span>
                  <span className="router-list-meta"><span>{router.router_type === "aggregation" ? t("Aggregation") : t("Execution")}</span><span>{router.output_models?.length || 0}{" "}{t("models")}</span></span>
                  <span className="router-list-footer"><ResourceOwnershipBadge ownership={router.ownership} /><StatusBadge status={router.status} /></span>
                  <ResourceAccessState access={router.access} />
                </button>
              ))}
              {visibleRouters.length === 0 && <div className="router-index-empty"><strong>{t("No matching routers")}</strong><span>{t("Clear the search or status filters.")}</span><button className="btn" onClick={() => { setRouterSearch(""); updateRouterUrl({ q: null, type: null, status: null }); }}>{t("Clear filters")}</button></div>}
            </div>
            <div className="router-index-pagination"><CursorPageControls page={routerPaging.page} hasMore={Boolean(routerPage.data?.next_cursor)} busy={routerPage.isFetching} onPrevious={routerPaging.previous} onNext={() => routerPaging.next(routerPage.data?.next_cursor)} /></div>
          </aside>

          <div className="router-studio-main">
            <header className="router-studio-toolbar">
              <button className="router-mobile-back" onClick={() => setMobileDetailOpen(false)}><ArrowLeft size={16} />{t("Routers")}</button>
              <div className="router-identity">
                <div className="router-identity-meta"><span>{selectedRouter.router_type === "aggregation" ? t("Aggregation Router") : t("Execution Router")}</span><StatusBadge status={selectedRouter.status} /><ResourceOwnershipBadge ownership={selectedRouter.ownership} /></div>
                <h2 title={selectedRouter.name}>{selectedRouter.name}</h2>
                <p>{selectedRouter.router_type === "aggregation" ? t("One API model maps to one Execution Router.") : t("Select a Model Pool, then let the Pool select its Source.")}</p>
                <small>{t("Updated")}{" "}{formatRouterTimestamp(selectedRouter.updated_at)}</small>
              </div>
              <div className="router-toolbar-actions">
                {renderRecommendedAction()}
                <details className="router-more-menu">
                  <summary className="btn"><MoreHorizontal size={16} />{t("More")}</summary>
                  <div>
                    <button onClick={() => openRouterModal(selectedRouter, "rename")}><Pencil size={15} />{t("Rename")}</button>
                    <ResourceSharingAction><button onClick={() => setShareTarget(routerShareTarget(selectedRouter))}><Share2 size={15} />{t("Share configuration")}</button></ResourceSharingAction>
                    {selectedRouter.router_type === "execution" && <button onClick={() => openRouterModal(selectedRouter, "advanced")}><Braces size={15} />{t("Custom policy")}</button>}
                    <button className="is-danger" onClick={() => openRouterModal(selectedRouter, "delete")}><Trash2 size={15} />{t("Archive router")}</button>
                  </div>
                </details>
              </div>
            </header>

            {!selectedCanRead ? <section className="router-access-required" aria-label={t("Router access required")}><CircleAlert size={22} /><div><h3>{t("Access required")}</h3><p>{t("You can discover this Router, but its configuration, topology, and traffic are hidden. Ask an Organization or Project administrator for a Role or explicit Share.")}</p></div></section> : <>
            <div className="router-workspace-tabs">
              <NexilumeTabs<RouterWorkspaceView> label={t("Router workspace")} options={[{ value: "overview", label: t("Overview") }, { value: "configure", label: t("Configure") }, { value: "activity", label: t("Test & traffic") }]} value={routerView} onChange={setRouterView} variant="view" idBase="router-workspace" />
            </div>

            {routerView === "overview" && <div className="router-overview-strip" role="tabpanel" id="router-workspace-panel-overview" aria-labelledby="router-workspace-tab-overview">
              <div><span>{t("Type")}</span><strong>{selectedRouter.router_type === "aggregation" ? t("Aggregation") : t("Execution")}</strong></div>
              <div><span>{t("Models")}</span><strong>{effectiveOutputModels.length}</strong></div>
              <div><span>{t("Configuration")}</span><strong>{selectedNeedsConfiguration ? t("Needs attention") : t("Ready")}</strong></div>
              <div><span>{t("Access")}</span><strong>{selectedCanEdit ? t("Editable") : selectedCanRead ? t("Read only") : t("Required")}</strong></div>
            </div>}

            {routerView === "overview" && selectedRouter.router_type === "execution" && <div className="router-overview-content"><RouterReadiness
              router={selectedRouter}
              hasUnsavedChanges={hasUnsavedChanges}
              deployBlockedReason={deployBlockedReason}
              unavailablePoolCount={unavailablePoolCount}
              onAddTarget={() => poolSelectRef.current?.focus()}
              onApply={() => applyRouterChanges.mutate()}
              onDeploy={() => deploy.mutate()}
              applying={applyRouterChanges.isPending}
              deploying={deploy.isPending}
              showAction={false}
            />

            {topology.isError ? (
              <div className="topology-empty is-error"><GitBranch size={22} /><div><h2>{t("Capability network unavailable")}</h2><p>{t("The Router editor is still available. Retry after the topology API recovers.")}</p></div></div>
            ) : topology.data?.routers.some((router) => router.id === selectedRouter.id) ? (
              <NexilumeTopology data={topology.data} routerId={selectedRouter.id} trace={selectedTrace?.trace || dryRun?.trace} title={t("Router decision fabric")} description={t("The highlighted route is the latest dry-run or persisted request trace.")} />
            ) : (
              <RouterDecisionFallback router={selectedRouter} models={models.data ?? []} />
            )}</div>}

            {routerView === "configure" && selectedRouter.router_type === "execution" && <div className="router-editor-grid" role="tabpanel" id="router-workspace-panel-configure" aria-labelledby="router-workspace-tab-configure">
              <section className="router-composer-panel">
                <div className="router-panel-heading"><div><div className="tech-label">{t("01 / POOL ORDER")}</div><h3>{t("Model Pool priority")}</h3></div><span className="router-selection-count">{selectedModelGroupIds.length}{" "}{t("selected")}</span></div>
                <div className="router-pool-order">
                  {selectedModelGroupIds.map((id, index) => {
                    const model = (models.data ?? []).find((item) => item.id === id);
                    if (!model) return <div key={id} className="router-pool-order-row is-unavailable"><span className="router-pool-index">{String(index + 1).padStart(2, "0")}</span><span className="min-w-0 flex-1"><strong>{t("Bound Pool unavailable")}</strong><small>{t("Not visible in this project ·")}{" "}{id.slice(0, 8)}</small></span><button className="btn h-8 px-2" onClick={() => toggleModelGroup(id, false)}>{t("Remove")}</button></div>;
                    return <div key={id} className="router-pool-order-row"><span className="router-pool-index">{String(index + 1).padStart(2, "0")}</span><span className="min-w-0 flex-1"><strong>{model.display_name || model.name}</strong><small>{model.routing_strategy.replaceAll("_", " ")} · {model.deployments.filter((source) => source.enabled).length}{" "}{t("enabled Sources")}</small></span><button className="btn h-8 w-8 p-0" aria-label={t("Move {{0}} up", { 0: model.name })} onClick={() => movePool(id, -1)} disabled={index === 0}><ArrowUp size={14} /></button><button className="btn h-8 w-8 p-0" aria-label={t("Move {{0}} down", { 0: model.name })} onClick={() => movePool(id, 1)} disabled={index === selectedModelGroupIds.length - 1}><ArrowDown size={14} /></button><button className="btn h-8 px-2" onClick={() => toggleModelGroup(id, false)}>{t("Remove")}</button></div>;
                  })}
                  {selectedModelGroupIds.length === 0 && <div className="router-empty-slot"><strong>{t("No Model Pools added")}</strong><span>{t("Add the first Pool below to make this Router deployable.")}</span></div>}
                </div>
                <div className="router-add-pool"><select ref={poolSelectRef} className="select" value={poolToAdd} onChange={(event) => setPoolToAdd(event.target.value)}><option value="">{t("Add a Model Pool…")}</option>{(models.data ?? []).filter((model) => !selectedModelGroupIds.includes(model.id)).map((model) => <option key={model.id} value={model.id}>{model.display_name || model.name}</option>)}</select><button className="btn" onClick={addPool} disabled={!poolToAdd} title={poolToAdd ? t("Add the selected Model Pool") : t("Choose a Model Pool first")}><Plus size={14} />{t("Add")}</button></div>
                {(models.data ?? []).length === 0 && <p className="router-inline-help is-warning">{selectedModelGroupIds.length > 0 ? t("{{0}} bound Model Pool{{1}} unavailable in the current project. The bindings remain active until you apply a removal.", { 0: selectedModelGroupIds.length, 1: selectedModelGroupIds.length === 1 ? " is" : "s are" }) : t("No Model Pools are available. Create a Model Pool before configuring this Router.")}</p>}
              </section>

              <section className="router-composer-panel">
                <div className="router-panel-heading"><div><div className="tech-label">{t("02 / ROUTER POLICY")}</div><h3>{t("Routing policy")}</h3></div><StatusBadge status={policy} /></div>
                <div className="router-name-editor">
                  <label htmlFor="router-workspace-name">{t("Router name")}</label>
                  <input id="router-workspace-name" className="input" value={routerEditName} onChange={(event) => setRouterEditName(event.target.value)} />
                </div>
                <select className="select" aria-label={t("Routing policy")} value={policy} onChange={(event) => setPolicy(event.target.value)}><option value="manual_priority">{t("Pool priority")}</option><option value="lowest_cost_pool">{t("Lowest cost")}</option><option value="lowest_latency_pool">{t("Lowest latency")}</option><option value="best_health_pool">{t("Best availability")}</option><option value="custom">{t("Custom policy")}</option></select>
                <p className="router-strategy-note">{strategyExplanation(policy)}</p>
                {customStrategyBlocked && policy === "custom" && <p className="router-inline-help is-warning">{t("Deploy a valid router.py before selecting Custom policy.")}</p>}
              </section>
            </div>}

            {routerView !== "activity" && selectedRouter.router_type === "aggregation" && <div className={routerView === "overview" ? "router-overview-content grid gap-4" : "router-aggregation-configure"}>
              {routerView === "overview" && <section className="router-readiness">
                <span className="router-readiness-icon">{childBindings.length > 0 && !hasAggregationOneToOneConflict && aggregationUnavailableCount === 0 ? <CheckCircle2 size={20} /> : <CircleAlert size={20} />}</span>
                <div>
                  <strong>{childBindings.length === 0 ? t("Add the first API model") : hasAggregationOneToOneConflict ? t("Repair duplicate mappings") : aggregationUnavailableCount > 0 ? t("{{0}} model{{1}} attention", { 0: aggregationUnavailableCount, 1: aggregationUnavailableCount === 1 ? " needs" : "s need" }) : selectedRouter.status === "deployed" ? t("API catalog is ready") : t("Ready to deploy")}</strong>
                  <p>{childBindings.length === 0 ? t("Choose the Execution Router that should answer this model name.") : hasAggregationOneToOneConflict ? t("Every model name and Execution Router must appear once.") : aggregationUnavailableCount > 0 ? t("Open the affected model below to see what must be fixed.") : selectedRouter.status === "deployed" ? t("{{0}} model{{1}} available through this API.", { 0: aggregationCatalog.length, 1: aggregationCatalog.length === 1 ? " is" : "s are" }) : t("Deploy this catalog when the model list looks right.")}</p>
                </div>
                <div>{childBindings.length === 0 || hasAggregationOneToOneConflict || aggregationUnavailableCount > 0 ? <span className="router-attention"><CircleAlert size={16} />{t("Needs attention")}</span> : selectedRouter.status !== "deployed" ? <span className="router-attention"><CircleAlert size={16} />{t("Draft")}</span> : <span className="router-operational"><CheckCircle2 size={16} />{t("Operational")}</span>}</div>
              </section>}

              {routerView === "overview" && (topology.isError ? (
                <div className="topology-empty is-error"><GitBranch size={22} /><div><h2>{t("Router Fabric unavailable")}</h2><p>{t("The API model catalog remains editable. Retry after the topology API recovers.")}</p></div></div>
              ) : topology.data?.routers.some((router) => router.id === selectedRouter.id) ? (
                <NexilumeTopology data={topology.data} routerId={selectedRouter.id} trace={selectedTrace?.trace || dryRun?.trace} title={t("Aggregation Router fabric")} description={t("Each API model connects directly to one Execution Router and its Model Pool lineage.")} />
              ) : (
                <RouterDecisionFallback router={selectedRouter} models={models.data ?? []} />
              ))}

              {routerView === "configure" && <section className="router-composer-panel" role="tabpanel" id="router-workspace-panel-configure" aria-labelledby="router-workspace-tab-configure">
                <div className="router-panel-heading router-aggregation-catalog-heading">
                  <div><div className="tech-label">{t("API MODEL CATALOG")}</div><h3>{t("Models available through this API")}</h3></div>
                  <div className="flex items-center gap-2"><span className="router-selection-count">{aggregationCatalog.length} {aggregationCatalog.length === 1 ? t("model") : t("models")}</span>{childBindings.length > 0 && <button className="btn h-9 px-3" onClick={openAggregationMapping}><Plus size={14} />{t("Add model")}</button>}</div>
                </div>
                <p className="router-strategy-note router-aggregation-note">{t("Clients choose a model name. That name forwards directly to one Execution Router.")}</p>
                {aggregationCatalog.length === 0 ? <div className="router-empty-slot mt-4"><strong>{t("This API has no models yet")}</strong><span>{t("Add an Execution Router as the first model.")}</span><button className="btn btn-primary mx-auto mt-3" onClick={openAggregationMapping}><Plus size={14} />{t("Add first model")}</button></div> : <div className="mt-4 overflow-hidden rounded-lg border border-line" role="table" aria-label={t("Aggregation API models")}>
                  <div className="hidden grid-cols-[minmax(9rem,0.8fr)_minmax(14rem,1.5fr)_minmax(8rem,0.7fr)_auto] gap-4 border-b border-line bg-slate-50 px-4 py-2 text-xs font-medium text-muted md:grid" role="row">
                    <span role="columnheader">{t("API model")}</span><span role="columnheader">{t("Execution Router")}</span><span role="columnheader">{t("Status")}</span><span role="columnheader">{t("Action")}</span>
                  </div>
                  <div className="divide-y divide-line">{aggregationCatalog.flatMap(([modelName, bindings]) => bindings.map((binding) => {
                    const state = candidateModelByOutputId.get(binding.child_output.id);
                    const hasConflict = bindings.length !== 1 || (aggregationRouterUseCounts.get(binding.child_output.router_id) ?? 0) !== 1;
                    const mappingStatus = !hasConflict && state?.available ? "healthy" : "degraded";
                    return <div key={binding.id} className="grid gap-3 px-4 py-4 md:grid-cols-[minmax(9rem,0.8fr)_minmax(14rem,1.5fr)_minmax(8rem,0.7fr)_auto] md:items-center md:gap-4" role="row">
                      <div role="cell"><span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-muted md:hidden">{t("API model")}</span><strong className="break-words text-sm text-ink">{modelName}</strong>{hasConflict && <small className="mt-1 block text-xs text-amber-700">{t("Duplicate legacy mapping")}</small>}</div>
                      <div className="flex min-w-0 items-center gap-3" role="cell"><span className="router-pool-index shrink-0"><ArrowRight size={14} /></span><span className="min-w-0"><strong className="block truncate text-sm text-ink" title={binding.child_output.router_name}>{binding.child_output.router_name}</strong><small className="block break-words text-xs text-muted">{t("Router model ·")}{" "}{binding.child_output.model_name}</small></span></div>
                      <div role="cell"><StatusBadge status={mappingStatus} />{state?.message && <small className="mt-1 block text-xs text-amber-700">{state.message}</small>}</div>
                      <div className="md:text-right" role="cell"><button className="btn h-9 px-3" disabled={removeChildBinding.isPending} onClick={() => removeChildBinding.mutate(binding.id)}>{t("Remove")}</button></div>
                    </div>;
                  }))}</div>
                </div>}
              </section>}
            </div>}

            {routerView === "configure" && selectedRouter.router_type === "execution" && <div className={`router-change-bar ${hasUnsavedChanges ? "is-dirty" : ""}`}>
              <div>{hasUnsavedChanges ? <><CircleAlert size={17} /><span><strong>{t("Unapplied changes")}</strong><small>{t("Name, policy and Pool order will be applied together.")}</small></span></> : <><CheckCircle2 size={17} /><span><strong>{t("Configuration up to date")}</strong><small>{t("The Execution Router configuration is saved.")}</small></span></>}</div>
              <button className="btn btn-primary" onClick={() => applyRouterChanges.mutate()} disabled={!hasUnsavedChanges || applyRouterChanges.isPending || !routerEditName.trim() || customStrategyBlocked}>{applyRouterChanges.isPending ? <Loader2 size={15} className="animate-spin" /> : <SlidersHorizontal size={15} />}{t("Apply changes")}</button>
            </div>}

            {routerView === "activity" && selectedRouter.router_type === "execution" && <div className="router-diagnostics-grid" role="tabpanel" id="router-workspace-panel-activity" aria-labelledby="router-workspace-tab-activity">
              <section className="router-diagnostic-panel">
                <div className="router-panel-heading"><div><div className="tech-label">{t("TEST ROUTING")}</div><h3>{t("Explain the next route")}</h3></div><button className="btn btn-primary" onClick={() => testRouting.mutate()} disabled={testRouting.isPending || Boolean(dryRunBlockedReason)} title={dryRunBlockedReason || t("Run a production-equivalent routing test")}>{testRouting.isPending ? <Loader2 size={15} className="animate-spin" /> : <FlaskConical size={15} />}{t("Run test")}</button></div>
                <div className="router-test-inputs">
                  <label><span>{t("Requested model")}</span><select className="select" value={dryRunModel} onChange={(event) => setDryRunModel(event.target.value)}>{effectiveOutputModels.map((modelName) => <option key={modelName} value={modelName}>{modelName}</option>)}</select></label>
                  <label><span>{t("Request")}</span><input className="input" value={dryRunPrompt} onChange={(event) => setDryRunPrompt(event.target.value)} /></label>
                </div>
                {dryRunBlockedReason && <p className="router-inline-help is-warning">{dryRunBlockedReason}</p>}
                {dryRun ? <RoutingTraceSummary trace={dryRun.trace} /> : <p className="router-diagnostic-empty">{t("This uses the production resolver without sending the request to a provider.")}</p>}
              </section>
              <section className="router-diagnostic-panel">
                <div className="router-panel-heading"><div><div className="tech-label">{t("RECENT TRAFFIC")}</div><h3>{t("Routing decisions")}</h3></div><Clock3 size={16} className="text-muted" /></div>
                <div className="router-trace-list">
                  {(traces.data?.items ?? []).slice(0, 6).map((item) => <button key={item.id} className={selectedTrace?.id === item.id ? "is-selected" : ""} onClick={() => setSelectedTrace(item)}><span><strong>{item.selected_pool || item.model || t("Routing request")}</strong><small>{item.selected_source || item.error_code || t("No Source was selected")}</small></span><span><StatusBadge status={item.status} /><small>{item.latency_ms}{" "}{t("ms")}</small></span></button>)}
                  {traces.isError && <p className="router-diagnostic-empty is-error">{t("Recent routing traces are temporarily unavailable.")}</p>}
                  {!traces.isLoading && (traces.data?.items ?? []).length === 0 && <p className="router-diagnostic-empty">{t("No requests yet. Run a routing test or call this Router through the SDK.")}</p>}
                  {selectedTrace && <TraceDetails trace={selectedTrace} />}
                </div>
              </section>
            </div>}

            {routerView === "activity" && selectedRouter.router_type === "aggregation" && <div className="router-diagnostics-grid" role="tabpanel" id="router-workspace-panel-activity" aria-labelledby="router-workspace-tab-activity">
              <section className="router-diagnostic-panel">
                <div className="router-panel-heading"><div><div className="tech-label">{t("TEST API MODEL")}</div><h3>{t("Test an API model")}</h3></div><button className="btn btn-primary" onClick={() => testRouting.mutate()} disabled={testRouting.isPending || Boolean(dryRunBlockedReason)} title={dryRunBlockedReason || t("Test the Aggregation Router resolver")}>{testRouting.isPending ? <Loader2 size={15} className="animate-spin" /> : <FlaskConical size={15} />}{t("Run test")}</button></div>
                <div className="router-test-inputs"><label><span>{t("API model")}</span><select className="select" value={dryRunModel} onChange={(event) => setDryRunModel(event.target.value)}>{effectiveOutputModels.map((modelName) => <option key={modelName} value={modelName}>{modelName}</option>)}</select></label><label><span>{t("Request")}</span><input className="input" value={dryRunPrompt} onChange={(event) => setDryRunPrompt(event.target.value)} /></label></div>
                {dryRunBlockedReason && <p className="router-inline-help is-warning">{dryRunBlockedReason}</p>}
                {dryRun ? <RoutingTraceSummary trace={dryRun.trace} /> : <p className="router-diagnostic-empty">{t("Checks that this model reaches its mapped Execution Router.")}</p>}
              </section>
              <section className="router-diagnostic-panel">
                <div className="router-panel-heading"><div><div className="tech-label">{t("RECENT API CALLS")}</div><h3>{t("Recent requests")}</h3></div><Clock3 size={16} className="text-muted" /></div>
                <div className="router-trace-list">{(traces.data?.items ?? []).slice(0, 6).map((item) => <button key={item.id} className={selectedTrace?.id === item.id ? "is-selected" : ""} onClick={() => setSelectedTrace(item)}><span><strong>{item.model || t("API request")}</strong><small>{item.selected_pool || item.error_code || t("No Execution Router was selected")}</small></span><span><StatusBadge status={item.status} /><small>{item.latency_ms}{" "}{t("ms")}</small></span></button>)}{traces.isError && <p className="router-diagnostic-empty is-error">{t("Recent API calls are temporarily unavailable.")}</p>}{!traces.isLoading && (traces.data?.items ?? []).length === 0 && <p className="router-diagnostic-empty">{t("No API calls yet.")}</p>}{selectedTrace && <TraceDetails trace={selectedTrace} />}</div>
              </section>
            </div>}
            </>}
          </div>
        </section>
      ) : null}

      {activeModal === "create" && (
        <RouterModalFrame
          title={t("Create router")}
          description={t("Choose the Router level first. Each type has a focused configuration workspace.")}
          onClose={() => setActiveModal(null)}
          maxWidth="max-w-xl"
        >
          <form
            className="grid gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              createRouter.mutate();
            }}
          >
            <fieldset className="grid gap-2">
              <legend className="mb-1 text-sm font-medium text-ink">{t("Router type")}</legend>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className={`cursor-pointer rounded-lg border p-4 ${createRouterType === "execution" ? "border-primary bg-primary/5" : "border-line"}`}>
                  <input className="sr-only" type="radio" name="router-type" value="execution" aria-label={t("Execution Router")} checked={createRouterType === "execution"} onChange={() => setCreateRouterType("execution")} />
                  <strong className="block text-sm text-ink">{t("Execution Router")}</strong>
                  <span className="mt-1 block text-xs text-muted">{t("Routes one or more named models to Model Pools and Sources.")}</span>
                </label>
                <label className={`cursor-pointer rounded-lg border p-4 ${createRouterType === "aggregation" ? "border-primary bg-primary/5" : "border-line"}`}>
                  <input className="sr-only" type="radio" name="router-type" value="aggregation" aria-label={t("Aggregation Router")} checked={createRouterType === "aggregation"} onChange={() => { setCreateRouterType("aggregation"); setCreatePoolId(""); }} />
                  <strong className="block text-sm text-ink">{t("Aggregation Router")}</strong>
                  <span className="mt-1 block text-xs text-muted">{t("Combines outputs from deployed Execution Routers behind one API.")}</span>
                </label>
              </div>
            </fieldset>
            <Field label={t("Name")}>
              <input
                className="input"
                placeholder={t("pool-router")}
                value={routerName}
                onChange={(event) => setRouterName(event.target.value)}
                required
              />
            </Field>
            <ResourceOwnershipPicker projects={projects} value={createOwnership} onChange={setCreateOwnership} />
            {createRouterType === "execution" && <Field label={t("Routing policy")}>
              <select className="select" aria-label={t("Create routing policy")} value={createStrategy} onChange={(event) => setCreateStrategy(event.target.value)}>
                <option value="manual_priority">{t("Pool priority")}</option>
                <option value="lowest_cost_pool">{t("Lowest cost")}</option>
                <option value="lowest_latency_pool">{t("Lowest latency")}</option>
                <option value="best_health_pool">{t("Best availability")}</option>
              </select>
            </Field>}
            {createRouterType === "execution" && <Field label={t("First Model Pool (optional)")}>
              <select className="select" aria-label={t("First Model Pool")} value={createPoolId} onChange={(event) => setCreatePoolId(event.target.value)}>
                <option value="">{t("Configure later")}</option>
                {(models.data ?? []).map((model) => <option key={model.id} value={model.id}>{model.display_name || model.name}</option>)}
              </select>
            </Field>}
            <p className="router-inline-help">{createRouterType === "execution" ? t("After creation, configure only Model Pools and execution policy.") : t("After creation, map only deployed Execution Router outputs to downstream model names.")}{" "}{t("The Router remains a draft until you deploy it.")}</p>
            <button
              className="btn btn-primary w-fit"
              disabled={createRouter.isPending || !routerName.trim()}
            >
              {createRouter.isPending && (
                <Loader2 size={16} className="animate-spin" />
              )}{t("Create draft")}</button>
          </form>
        </RouterModalFrame>
      )}

      {activeModal === "mapping" && selectedRouter?.router_type === "aggregation" && (
        <RouterModalFrame
          title={t("Add API model")}
          description={t("{{0}} · connect one model name to one Execution Router.", { 0: selectedRouter.name })}
          onClose={closeAggregationMapping}
          maxWidth="max-w-2xl"
        >
          <form
            className="grid gap-6"
            onSubmit={(event) => {
              event.preventDefault();
              if (selectedChildRouter?.available && childOutputId && childModelName.trim()) addChildBinding.mutate();
            }}
          >
            <section aria-labelledby="aggregation-router-step">
              <div className="flex items-start gap-3">
                <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-ink font-mono text-xs font-semibold text-white">1</span>
                <div><h3 id="aggregation-router-step" className="text-base font-semibold text-ink">{t("Choose an Execution Router")}</h3><p className="mt-1 text-sm text-muted">{t("Only deployed, callable Routers can be added.")}</p></div>
              </div>
              {aggregationCandidates.isLoading ? <div className="router-diagnostic-empty mt-4"><Loader2 size={16} className="animate-spin" />{t("Loading Execution Routers…")}</div> : aggregationCandidates.isError ? <div className="router-diagnostic-empty is-error mt-4">{t("Execution Routers could not be loaded. Close this dialog and try again.")}</div> : availableAggregationCandidates.length === 0 ? <div className="router-empty-slot mt-4"><strong>{mappedAggregationCandidates.length > 0 && unavailableAggregationCandidates.length === 0 ? t("All available Routers are already added") : t("No Execution Router is ready")}</strong><span>{mappedAggregationCandidates.length > 0 && unavailableAggregationCandidates.length === 0 ? t("Create or deploy another Execution Router to add a model.") : t("Deploy an Execution Router with a healthy Model Pool, then return here.")}</span></div> : <div className="mt-4 grid gap-2" role="listbox" aria-label={t("Available Execution Routers")}>{availableAggregationCandidates.map((candidate) => <button key={candidate.router_id} type="button" role="option" aria-selected={childRouterId === candidate.router_id} className={`flex min-h-14 w-full items-center justify-between gap-4 rounded-lg border px-4 py-3 text-left transition-colors ${childRouterId === candidate.router_id ? "border-primary bg-primary/5" : "border-line bg-white hover:bg-slate-50"}`} onClick={() => chooseChildRouter(candidate)}><span className="min-w-0"><strong className="block truncate text-sm text-ink">{candidate.name}</strong><small className="mt-1 block text-xs text-muted">{t("Default model ·")}{" "}{candidate.model_name}</small></span><span className="shrink-0">{childRouterId === candidate.router_id ? <span className="router-operational"><CheckCircle2 size={15} />{t("Selected")}</span> : <StatusBadge status="available" />}</span></button>)}</div>}
              {mappedAggregationCandidates.length > 0 && <details className="mt-4 rounded-lg border border-line bg-slate-50"><summary className="cursor-pointer px-4 py-3 text-sm font-medium text-ink">{t("Already added (")}{mappedAggregationCandidates.length})</summary><div className="grid gap-3 border-t border-line px-4 py-3">{mappedAggregationCandidates.map((candidate) => <div key={candidate.router_id} className="grid gap-1 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start"><div><strong className="text-sm text-ink">{candidate.name}</strong><p className="mt-1 text-xs text-muted">{t("Available as")}{" "}{candidate.bound_model_name}.</p></div><StatusBadge status="mapped" /></div>)}</div></details>}
              {unavailableAggregationCandidates.length > 0 && <details className="mt-4 rounded-lg border border-line bg-slate-50"><summary className="cursor-pointer px-4 py-3 text-sm font-medium text-ink">{t("Needs attention (")}{unavailableAggregationCandidates.length})</summary><div className="grid gap-3 border-t border-line px-4 py-3">{unavailableAggregationCandidates.map((candidate) => <div key={candidate.router_id} className="grid gap-1 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start"><div><strong className="text-sm text-ink">{candidate.name}</strong><p className="mt-1 text-xs text-amber-700">{candidate.message || t("This Router is not ready to use.")}</p></div><StatusBadge status="unavailable" /></div>)}</div></details>}
            </section>

            <section className="border-t border-line pb-14 pt-5" aria-labelledby="aggregation-name-step">
              <div className="flex items-start gap-3">
                <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-full font-mono text-xs font-semibold ${selectedChildRouter ? "bg-ink text-white" : "bg-slate-200 text-muted"}`}>2</span>
                <div className="min-w-0 flex-1"><h3 id="aggregation-name-step" className="text-base font-semibold text-ink">{t("Name the API model")}</h3><p className="mt-1 text-sm text-muted">{t("This is the model name callers will use. The Router default is filled in for you.")}</p>
                  <label className="mt-4 block"><span className="mb-1 block text-sm font-medium text-ink">{t("API model name")}</span><input className="input" aria-label={t("API model name")} value={childModelName} onChange={(event) => setChildModelName(event.target.value)} disabled={!selectedChildRouter} placeholder={selectedChildRouter ? t("Enter a model name") : t("Choose an Execution Router first")} /></label>
                  {selectedChildRouter && <div className="mt-4 flex flex-wrap items-center gap-2 rounded-lg border border-line bg-slate-50 px-4 py-3 text-sm" aria-label={t("Mapping preview")}><strong className="break-words text-ink">{childModelName.trim() || t("API model")}</strong><ArrowRight size={15} className="text-muted" /><span className="break-words text-muted">{selectedChildRouter.name}</span></div>}
                </div>
              </div>
            </section>

            <div className="router-modal-actions flex flex-wrap justify-end gap-2 border-t border-line">
              <button type="button" className="btn" onClick={closeAggregationMapping} disabled={addChildBinding.isPending}>{t("Cancel")}</button>
              <button className="btn btn-primary" disabled={!selectedChildRouter?.available || !childOutputId || !childModelName.trim() || addChildBinding.isPending}>{addChildBinding.isPending ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}{t("Add API model")}</button>
            </div>
          </form>
        </RouterModalFrame>
      )}

      {activeModal === "advanced" && selectedRouter && (
        <RouterModalFrame
          title={t("Custom routing policy")}
          description={t("{{0}} · router.py is optional and intended for advanced routing logic.", { 0: selectedRouter.name })}
          onClose={() => setActiveModal(null)}
        >
          <div className="grid gap-5">
            <div className="rounded-md border border-line bg-slate-50 p-3">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <div className="font-medium text-ink">router.py</div>
                  <div className="mt-1 text-sm text-muted">
                    {routerPyStatusText()}
                  </div>
                </div>
                {routerSource.data?.has_source && (
                  <div className="flex shrink-0 flex-wrap gap-2">
                    <StatusBadge status={routerSource.data.source_kind} />
                    <StatusBadge status={routerSource.data.version_status} />
                  </div>
                )}
              </div>
            </div>

            <Field label={t("Source")}>
              <textarea
                className="textarea min-h-96 font-mono text-xs"
                value={routerCode}
                onChange={(event) => setRouterCode(event.target.value)}
                disabled={routerSource.isLoading}
              />
            </Field>
            <div className="flex gap-2">
              <button
                className="btn"
                onClick={() => upload.mutate()}
                disabled={upload.isPending}
              >
                {upload.isPending ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <Upload size={16} />
                )}{t("Upload draft")}</button>
              <button
                className="btn btn-primary"
                onClick={() => deploy.mutate()}
                disabled={deploy.isPending}
              >
                {deploy.isPending ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <Rocket size={16} />
                )}{t("Deploy")}</button>
            </div>
          </div>
        </RouterModalFrame>
      )}

      {activeModal === "rename" && selectedRouter && (
        <RouterModalFrame
          title={t("Rename Aggregation Router")}
          description={t("This changes the workspace label only. The Router ID, API credentials and model catalog stay the same.")}
          onClose={() => {
            setRouterEditName(selectedRouter.name);
            setActiveModal(null);
          }}
          maxWidth="max-w-lg"
        >
          <form
            className="grid gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              applyRouterChanges.mutate();
            }}
          >
            <Field label={t("Router name")}>
              <input
                className="input"
                value={routerEditName}
                onChange={(event) => setRouterEditName(event.target.value)}
                autoFocus
                required
              />
            </Field>
            <div className="flex flex-wrap justify-end gap-2">
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setRouterEditName(selectedRouter.name);
                  setActiveModal(null);
                }}
              >{t("Cancel")}</button>
              <button
                className="btn btn-primary"
                disabled={
                  applyRouterChanges.isPending ||
                  !routerEditName.trim() ||
                  routerEditName.trim() === selectedRouter.name
                }
              >
                {applyRouterChanges.isPending && <Loader2 size={15} className="animate-spin" />}{t("Save name")}</button>
            </div>
          </form>
        </RouterModalFrame>
      )}

      {activeModal === "delete" && selectedRouter && (
        <RouterModalFrame
          title={t("Archive router")}
          description={t("Archive {{0}} and disable its active endpoint.", { 0: selectedRouter.name })}
          onClose={() => { setActiveModal(null); setDeleteConfirmation(""); }}
          maxWidth="max-w-xl"
        >
          <div className="grid gap-4">
            <div className="router-archive-warning">
              <CircleAlert size={20} />
              <div><strong>{t("This Router will stop accepting requests.")}</strong><p>{t("Model Pools and Sources are not deleted. Historical traces remain available for audit.")}</p></div>
            </div>
            <Field label={t("Type {{0}} to confirm", { 0: selectedRouter.name })}>
              <input className="input" value={deleteConfirmation} onChange={(event) => setDeleteConfirmation(event.target.value)} autoComplete="off" />
            </Field>
            <div className="flex flex-wrap justify-end gap-2">
              <button className="btn" onClick={() => { setActiveModal(null); setDeleteConfirmation(""); }}>{t("Cancel")}</button>
              <button className="btn router-danger-button" onClick={() => deleteRouter.mutate()} disabled={deleteRouter.isPending || deleteConfirmation !== selectedRouter.name}>{deleteRouter.isPending && <Loader2 size={15} className="animate-spin" />}{t("Archive router")}</button>
            </div>
          </div>
        </RouterModalFrame>
      )}

      {credentials && (
        <RouterCredentialsDialog
          credentials={credentials}
          onClose={() => setCredentials(null)}
        />
      )}
      <ShareResourceModal
        target={shareTarget}
        onClose={() => setShareTarget(null)}
      />
    </div>
  );
}

function strategyExplanation(strategy: string) {
  const descriptions: Record<string, string> = {
    manual_priority: "Try Model Pools in the order shown. Each Pool uses its own Source fallback before the Router tries the next Pool.",
    lowest_cost_pool: "Choose the Pool with the lowest currently available Source price, then let that Pool select its Source.",
    lowest_latency_pool: "Choose the Pool with the fastest recent Source latency, then let that Pool select its Source.",
    best_health_pool: "Choose the Pool with the strongest Source availability, then use that Pool's fallback policy.",
    custom: "Use the deployed custom policy to order sanitized Model Pool summaries. The policy can return Pool IDs only.",
  };
  return descriptions[strategy] || descriptions.manual_priority;
}

function routerNeedsAttention(router: Router) {
  if (router.access && !router.access.can_read) return true;
  if (router.status !== "deployed") return true;
  if (router.router_type === "aggregation") return (router.child_bindings ?? []).filter((binding) => binding.enabled && binding.status !== "deleted").length === 0;
  return (router.model_group_ids ?? []).length === 0;
}

function formatRouterTimestamp(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "recently";
  return new Intl.DateTimeFormat(getLocale(), { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function RouterReadiness({
  router,
  hasUnsavedChanges,
  deployBlockedReason,
  unavailablePoolCount,
  onAddTarget,
  onApply,
  onDeploy,
  applying,
  deploying,
  showAction = true,
}: {
  router: Router;
  hasUnsavedChanges: boolean;
  deployBlockedReason: string;
  unavailablePoolCount: number;
  onAddTarget: () => void;
  onApply: () => void;
  onDeploy: () => void;
  applying: boolean;
  deploying: boolean;
  showAction?: boolean;
}) {
  useLocale();
  const hasPools = router.model_group_ids.length > 0;
  const hasChildOutputs = (router.child_bindings ?? []).some((binding) => binding.status !== "deleted" && binding.enabled);
  const hasTargets = hasPools || hasChildOutputs;
  let title = "Operational";
  let message = "This Router is deployed and ready to receive requests.";
  let action: ReactNode = <span className="router-operational"><CheckCircle2 size={16} />{t("Ready")}</span>;

  if (!hasTargets && !hasUnsavedChanges) {
    title = "Add the first route target";
    message = router.router_type === "aggregation"
      ? "Choose a deployed Execution Router output and give it a model name for downstream clients."
      : "Choose the first Model Pool that this Router should execute against.";
    action = <button className="btn btn-primary" onClick={onAddTarget}><Plus size={15} />{router.router_type === "aggregation" ? t("Add child output") : t("Add Model Pool")}</button>;
  } else if (hasUnsavedChanges) {
    title = "Review and apply changes";
    message = "Your edits are local until they are applied together.";
    action = <button className="btn btn-primary" onClick={onApply} disabled={applying}>{applying && <Loader2 size={15} className="animate-spin" />}{t("Apply changes")}</button>;
  } else if (unavailablePoolCount > 0) {
    title = "Bound Model Pools need attention";
    message = `${unavailablePoolCount} bound Model Pool${unavailablePoolCount === 1 ? " is" : "s are"} unavailable in the current project. Switch project or remove the stale binding before testing.`;
    action = <span className="router-attention"><CircleAlert size={16} />{t("Needs attention")}</span>;
  } else if (router.status !== "deployed") {
    title = deployBlockedReason ? "Router is not ready to deploy" : "Deploy this Router";
    message = deployBlockedReason || "Deployment activates the Router endpoint and enables routing tests.";
    action = <button className="btn btn-primary" onClick={onDeploy} disabled={deploying || Boolean(deployBlockedReason)} title={deployBlockedReason}>{deploying ? <Loader2 size={15} className="animate-spin" /> : <Rocket size={15} />}{t("Deploy router")}</button>;
  }

  const quietState = title === "Operational"
    ? <span className="router-operational"><CheckCircle2 size={16} />{t("Ready")}</span>
    : <span className="router-attention"><CircleAlert size={16} />{t("Needs attention")}</span>;
  return <section className="router-readiness"><span className="router-readiness-icon">{title === "Operational" ? <CheckCircle2 size={20} /> : <CircleAlert size={20} />}</span><div><strong>{title}</strong><p>{message}</p></div><div>{showAction ? action : quietState}</div></section>;
}

function RouterDecisionFallback({ router, models }: { router: Router; models: ModelGroup[] }) {
  useLocale();
  const pools = router.model_group_ids.map((id) => models.find((model) => model.id === id)).filter(Boolean) as ModelGroup[];
  const childBindings = (router.child_bindings ?? []).filter((binding) => binding.status !== "deleted" && binding.enabled);
  return (
    <section className="router-decision-summary" aria-label={t("Router decision path")}>
      <header><div className="tech-label">{t("ROUTER DECISION PATH")}</div><h3>{t("How requests move through this Router")}</h3></header>
      {router.router_type === "aggregation" && childBindings.length > 0 ? (
        <div className="router-decision-path">
          <span><small>{t("REQUEST")}</small><strong>{t("Named API model")}</strong><em>{router.output_models.join(" · ")}</em></span><ArrowRight size={17} />
          <span><small>{t("AGGREGATION")}</small><strong>{childBindings.length}{" "}{t("child routes")}</strong></span><ArrowRight size={17} />
          <span><small>{t("EXECUTION ROUTER")}</small><strong>{t("Independent policy")}</strong></span><ArrowRight size={17} />
          <span><small>{t("MODEL POOL")}</small><strong>{t("Source fallback")}</strong></span>
        </div>
      ) : router.router_type === "aggregation" ? (
        <div className="router-decision-empty"><GitBranch size={22} /><div><strong>{t("Add the first child output")}</strong><p>{t("Select an output from a deployed Execution Router and assign its downstream model name.")}</p></div></div>
      ) : router.model_group_ids.length === 0 ? (
        <div className="router-decision-empty"><GitBranch size={22} /><div><strong>{t("Add the first Model Pool")}</strong><p>{t("This Router already exists. Add a Pool below to complete its decision path.")}</p></div></div>
      ) : (
        <div className="router-decision-path">
          <span><small>{t("REQUEST")}</small><strong>{t("Incoming model request")}</strong></span><ArrowRight size={17} />
          <span><small>{t("ROUTER POLICY")}</small><strong>{strategyLabel(router.strategy)}</strong></span><ArrowRight size={17} />
          <span><small>{t("MODEL POOLS")}</small><strong>{router.model_group_ids.length}{" "}{t("configured")}</strong><em>{pools.length === router.model_group_ids.length ? pools.map((pool) => pool.display_name || pool.name).join(" · ") : t("{{0}} visible · {{1}} unavailable", { 0: pools.length, 1: router.model_group_ids.length - pools.length })}</em></span><ArrowRight size={17} />
          <span><small>{t("SOURCE FALLBACK")}</small><strong>{t("Owned by selected Pool")}</strong></span>
        </div>
      )}
    </section>
  );
}

function TraceDetails({ trace }: { trace: RouterTraceRecord }) {
  useLocale();
  return (
    <div className={`router-trace-details ${trace.status === "failed" ? "is-error" : ""}`}>
      <strong>{trace.status === "failed" ? t("Why this request failed") : t("Selected route")}</strong>
      <p>{trace.status === "failed" ? trace.error_code || t("The resolver did not return an error code.") : `${trace.selected_pool || "Unknown Pool"} → ${trace.selected_source || "Unknown Source"}`}</p>
      <dl><div><dt>{t("Model")}</dt><dd>{trace.model || "–"}</dd></div><div><dt>{t("Fallbacks")}</dt><dd>{trace.fallback_count}</dd></div><div><dt>{t("Latency")}</dt><dd>{trace.latency_ms}{" "}{t("ms")}</dd></div></dl>
    </div>
  );
}

function strategyLabel(strategy: string) {
  const labels: Record<string, string> = {
    cost: "Lowest cost",
    manual_priority: "Pool priority",
    lowest_cost_pool: "Lowest cost",
    lowest_latency_pool: "Lowest latency",
    best_health_pool: "Best availability",
    custom: "Custom policy",
  };
  return labels[strategy] || strategy.replaceAll("_", " ");
}

function RoutingTraceSummary({ trace }: { trace: RouterTestResult["trace"] }) {
  useLocale();
  const selectedPool = trace.router.candidates.find((candidate) => candidate.selected);
  const selectedSource = trace.pool.candidates.find((candidate) => candidate.selected);
  return (
    <div className="routing-trace-summary">
      <div><span>{t("ROUTER STAGE")}</span><strong>{selectedPool?.pool || t("No Pool selected")}</strong><small>{trace.router.reason}</small></div>
      <ArrowRight size={16} />
      <div><span>{t("POOL STAGE")}</span><strong>{selectedSource?.source || t("No Source selected")}</strong><small>{trace.pool.strategy.replaceAll("_", " ")} · {selectedSource?.provider || "–"}</small></div>
    </div>
  );
}

function routerShareTarget(router: Router): ShareResourceTarget {
  return {
    resourceType: "router",
    resourceKind: "Router",
    resourceId: router.id,
    resourceName: router.name,
  };
}

function RouterModalFrame({
  title,
  description,
  children,
  onClose,
  maxWidth = "max-w-4xl",
}: {
  title: string;
  description?: string;
  children: ReactNode;
  onClose: () => void;
  maxWidth?: string;
}) {
  useLocale();
  const titleId = useId();
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/45 p-3 sm:p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`max-h-[90vh] w-full ${maxWidth} overflow-hidden rounded-lg border border-line bg-white shadow-2xl`}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <div id={titleId} className="text-lg font-semibold text-ink">{title}</div>
            {description && (
              <div className="mt-1 break-all text-sm text-muted">
                {description}
              </div>
            )}
          </div>
          <button className="btn h-8 shrink-0 px-2" onClick={onClose} aria-label={t("Close dialog")}>
            <X size={16} />
          </button>
        </div>
        <div className="max-h-[calc(90vh-73px)] overflow-auto p-4 sm:p-5">
          {children}
        </div>
      </div>
    </div>
  );
}

function RouterCredentialsDialog({
  credentials,
  onClose,
}: {
  credentials: RouterCredentials;
  onClose: () => void;
}) {
  useLocale();
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/45 p-3 sm:p-4">
      <div className="max-h-[90vh] w-full max-w-3xl overflow-hidden rounded-lg border border-line bg-white shadow-2xl">
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-lg font-semibold text-ink">
              <KeyRound className="shrink-0" size={18} />{t("Router access")}</div>
            <div className="mt-1 break-all text-sm text-muted">{t("OpenAI-compatible gateway credentials for")}{" "}
              {credentials.router_name}
            </div>
          </div>
          <button className="btn h-8 shrink-0 px-2" onClick={onClose}>
            <X size={16} />
          </button>
        </div>
        <div className="max-h-[calc(90vh-73px)] overflow-auto p-4 sm:p-5">
          <div className="grid gap-4">
            <div className="grid min-w-0 gap-2 sm:grid-cols-2">
              <CopyRow
                label={t("Base URL")}
                value={credentials.gateway_api_base_url}
              />
              <CopyRow label={t("Router ID")} value={credentials.router_id} />
              <CopyRow label={t("Model")} value={credentials.recommended_model} />
              <CopyRow label={t("Available models")} value={(credentials.available_models ?? [credentials.recommended_model]).join(", ")} />
              <CopyRow label={t("Strategy")} value={credentials.router_strategy} />
            </div>

            <SecretRow label={t("API key")} value={credentials.gateway_api_key} />

            <SnippetBlock title={t(".env")} text={credentials.env} />
            <SnippetBlock title={t("curl")} text={credentials.curl} />
          </div>
        </div>
      </div>
    </div>
  );
}

function CopyRow({ label, value }: { label: string; value: string }) {
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

function SecretRow({ label, value }: { label: string; value: string }) {
  useLocale();
  return (
    <div className="min-w-0 rounded-md border border-line bg-slate-950 p-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="min-w-0 text-xs font-semibold uppercase text-slate-400">
          {label}
        </div>
        <button
          className="btn h-8 w-8 shrink-0 border-slate-700 bg-slate-900 p-0 text-slate-100 hover:bg-slate-800"
          onClick={() => void copy(value)}
          disabled={!value}
        >
          <Copy size={14} />
        </button>
      </div>
      <code className="block break-all font-mono text-xs leading-5 text-slate-100">
        {value || "-"}
      </code>
    </div>
  );
}

function SnippetBlock({ title, text }: { title: string; text: string }) {
  useLocale();
  return (
    <div className="min-w-0 overflow-hidden rounded-md border border-line">
      <div className="flex items-center justify-between gap-3 border-b border-line bg-slate-50 px-3 py-2">
        <div className="min-w-0 text-sm font-medium text-ink">{title}</div>
        <button
          className="btn h-8 shrink-0 px-2"
          onClick={() => void copy(text)}
        >
          <Copy size={14} />{t("Copy")}</button>
      </div>
      <pre className="max-h-72 overflow-auto whitespace-pre bg-slate-950 p-4 font-mono text-xs leading-5 text-slate-100">
        {text}
      </pre>
    </div>
  );
}

async function copy(value: string) {
  await navigator.clipboard.writeText(value || "");
  toast.success(t("Copied"));
}
