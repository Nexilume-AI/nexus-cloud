import { DataAssetsWorkspace, DataLibraryNavigation } from "../components/DataLibrary";
import { LoadingState, ErrorState, datasetOwnershipLabel, humanizeLifecycle, Detail, formatBytes, downloadApiFile } from "../components/DataAssetPresentation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import {
  AlertTriangle,
  Brain,
  CheckCircle2,
  Database,
  Download,
  FileArchive,
  FileOutput,
  FileSearch,
  ListChecks,
  Loader2,
  MessageSquareText,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Share2,
  Trash2,
  Upload,
} from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import { DatasetImports, DatasetPageControls } from "../components/DatasetImports";
import { DatasetFileImport } from "../components/DatasetFileImport";
import { DatasetImagePreview } from "../components/DatasetImagePreview";
import { DatasetPublicationSummary, DatasetPublicationScope, DatasetPublicationPanel } from "../components/ResourcePublishing";
import { useApplicationDistribution } from "../app/distribution";
import { datasetNextAction, datasetManagementPresentation } from "../components/DatasetRecommendations";
import type {
  Agent,
  AgentDisplayRun,
  AgentMemoryItem,
  AgentOutputArtifact,
  Dataset,
  DatasetContentSearchResult,
  DatasetFile,
  DatasetSearchAllResult,
  DatasetVersion,
  DatasetVersionFileSnapshot,
} from "../lib/types";
import { compactId, formatDate, formatNumber } from "../lib/format";
import { Card } from "../components/Card";
import { DataTable } from "../components/DataTable";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Form";
import { StatusBadge } from "../components/Badge";
import {
  ResourceAccessState,
  ResourceOwnershipBadge,
  ResourceOwnershipPicker,
} from "../components/ResourceOwnership";
import {
  ShareResourceModal,
  type ShareResourceTarget,
} from "../components/ShareResourceModal";

import { NexilumeDialog, NexilumeTabs } from "../components/NexilumeControls";
import {
  collectionStageForDataset,
  DataAssetShape,
  DataStatus,
  StageHeading,
  type CollectionStage,
} from "../components/DataAssetFabric";

type SearchScope = "metadata" | "content" | "all";
type AgentAssetSource = "agent_trace" | "agent_memory" | "agent_artifact";
type ReleaseReadiness = "ready" | "blocked";

type DataAssetsModal =
  | "collectionSearch"
  | "createCollection"
  | "collectionSettings"
  | "importAsset"
  | "createRelease"
  | "releaseHistory"
  | "releaseManifest"
  | null;
type AssetImportMode = "trace" | "memory" | "artifact";
type AssetFileLike = {
  file_name: string;
  metadata_json?: Record<string, unknown>;
};

export function DataAssetsPage() {
  return <DataAssetsWorkspace collections={<OwnedCollectionsWorkspace />} />;
}

function OwnedCollectionsWorkspace() {
  const {
    apiContext,
    isContextReady,
    projects,
    projectId,
  } = useAuth();
  const publishing = useApplicationDistribution().resourcePublishing;
  const [selectionRevision, setSelectionRevision] = useState(0);
  const queryClient = useQueryClient();
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [datasetOwnership, setDatasetOwnership] = useState<
    import("../lib/types").ResourceOwnershipInput
  >(
    projectId
      ? { scope: "project", project_id: projectId }
      : { scope: "organization", project_id: null },
  );
  const [datasetForm, setDatasetForm] = useState({
    name: "",
    rename: "",
    max_size: "1GB",
  });
  const [releaseNotes, setReleaseNotes] = useState("");
  const [searchForm, setSearchForm] = useState<{
    q: string;
    scope: SearchScope;
  }>({ q: "", scope: "all" });
  const [searchPayload, setSearchPayload] = useState<
    Dataset[] | DatasetContentSearchResult[] | DatasetSearchAllResult | null
  >(null);

  const [collectionFilter, setCollectionFilter] = useState("");
  const [collectionCursor, setCollectionCursor] = useState("");
  const [fileCursor, setFileCursor] = useState("");
  const [versionCursor, setVersionCursor] = useState("");
  useEffect(() => { setVersionCursor(""); }, [selectedDatasetId, collectionCursor]);
  const importKeys = useRef(new Map<string, string>());

  function submitImport(kind: AssetImportMode, inputs: Record<string, unknown>) {
    const identity = JSON.stringify([apiContext.tenantId, selectedDataset!.id, kind, inputs]);
    let key = importKeys.current.get(identity);
    if (!key) {
      key = crypto.randomUUID();
      importKeys.current.set(identity, key);
    }
    return api.createDatasetImport(apiContext, selectedDataset!.id, { kind, inputs, request_key: key }).then(result => {
      importKeys.current.delete(identity);
      return result;
    });
  }

  useEffect(() => { setCollectionCursor(""); setSelectedDatasetId(""); }, [collectionFilter, apiContext.tenantId, apiContext.projectId]);
  useEffect(() => { setFileCursor(""); }, [selectedDatasetId, collectionCursor, apiContext.tenantId, apiContext.projectId]);
  const [collectionStage, setCollectionStage] =
    useState<CollectionStage>("collect");
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [importStep, setImportStep] = useState(1);
  const [importSource, setImportSource] = useState<"files" | "agent">("files");
  const [fileImportBusy, setFileImportBusy] = useState(false);
  const [selectedReleaseId, setSelectedReleaseId] = useState("");
  const [selectedMemoryItemIds, setSelectedMemoryItemIds] = useState<string[]>(
    [],
  );
  const [selectedOutputArtifactId, setSelectedOutputArtifactId] = useState("");
  const [activeModal, setActiveModal] = useState<DataAssetsModal>(null);
  const [shareTarget, setShareTarget] = useState<ShareResourceTarget | null>(
    null,
  );
  const [assetImportMode, setAssetImportMode] =
    useState<AssetImportMode>("trace");
  const [assetForm, setAssetForm] = useState({
    agent_id: "",
    run_id: "",
  });
  const datasetPage = useQuery({
    queryKey: ["datasets", apiContext, collectionCursor, collectionFilter],
    queryFn: () => api.datasetPage(apiContext, collectionCursor, collectionFilter),
    enabled: isContextReady,
  });
  const datasets = { ...datasetPage, data: datasetPage.data?.items };

  const datasetCapabilities = useQuery({
    queryKey: ["dataset-capabilities", apiContext],
    queryFn: () => api.datasetCapabilities(apiContext),
    enabled: isContextReady,
  });

  const agents = useQuery({
    queryKey: ["agents", apiContext],
    queryFn: () => api.agents(apiContext),
    enabled: isContextReady,
  });

  const selectedDatasetEntry = useMemo(
    () =>
      (datasets.data ?? []).find(
        (dataset) => dataset.id === selectedDatasetId,
      ) ?? (datasets.data ?? [])[0],
    [datasets.data, selectedDatasetId],
  );
  const selectedDatasetDetail = useQuery({
    queryKey: ["dataset-detail", apiContext, selectedDatasetEntry?.id],
    queryFn: () => api.dataset(apiContext, selectedDatasetEntry!.id),
    enabled: Boolean(isContextReady && selectedDatasetEntry?.id && selectedDatasetEntry.access?.can_read),
  });
  const selectedDataset = selectedDatasetDetail.data ?? selectedDatasetEntry;

  useEffect(() => {
    if (!selectedDataset) return;
    setCollectionStage(collectionStageForDataset(selectedDataset, publishing?.datasetPolicy));
  }, [selectedDataset?.id, selectedDataset?.lifecycle_status, publishing?.datasetPolicy]);

  const datasetPull = useQuery({
    queryKey: ["dataset-pull", apiContext, selectedDataset?.id, fileCursor],
    queryFn: () => api.datasetFilePage(apiContext, selectedDataset!.id, fileCursor),
    enabled: Boolean(isContextReady && selectedDataset?.id),
  });

  const versionPage = useQuery({
    queryKey: ["dataset-versions", apiContext, selectedDataset?.id, versionCursor],
    queryFn: () => api.datasetVersionPage(apiContext, selectedDataset!.id, versionCursor),
    enabled: Boolean(isContextReady && selectedDataset?.id),
  });
  const datasetVersions = { ...versionPage, data: versionPage.data?.items };

  const selectedAgent = useMemo(
    () =>
      (agents.data ?? []).find((agent) => agent.id === assetForm.agent_id) ??
      null,
    [agents.data, assetForm.agent_id],
  );

  const selectedAgentMemory = useQuery({
    queryKey: ["agent-memory", apiContext, assetForm.agent_id],
    queryFn: () => api.agentMemory(apiContext, assetForm.agent_id),
    enabled: Boolean(isContextReady && assetForm.agent_id),
  });

  const selectedAgentRuns = useQuery({
    queryKey: ["agent-display-runs", apiContext, assetForm.agent_id],
    queryFn: () => api.agentDisplayRuns(apiContext, assetForm.agent_id),
    enabled: Boolean(isContextReady && assetForm.agent_id),
  });
  const selectedTraceRun = useMemo(
    () =>
      (selectedAgentRuns.data ?? []).find((run) => run.id === assetForm.run_id),
    [assetForm.run_id, selectedAgentRuns.data],
  );
  const selectedOutputArtifacts = useQuery({
    queryKey: [
      "agent-output-artifacts",
      apiContext,
      assetForm.agent_id,
      assetForm.run_id,
    ],
    queryFn: () =>
      api.agentOutputArtifacts(
        apiContext,
        assetForm.agent_id,
        assetForm.run_id,
      ),
    enabled: Boolean(
      isContextReady &&
      assetForm.agent_id &&
      assetForm.run_id &&
      assetImportMode === "artifact",
    ),
  });
  const selectedOutputArtifact = useMemo(
    () =>
      (selectedOutputArtifacts.data ?? []).find(
        (artifact) => artifact.id === selectedOutputArtifactId,
      ) ?? null,
    [selectedOutputArtifactId, selectedOutputArtifacts.data],
  );

  const createDataset = useMutation({
    mutationFn: () =>
      api.createDataset(apiContext, {
        name: datasetForm.name,
        ownership: datasetOwnership,
      }),
    onSuccess: async (dataset) => {
      toast.success("Collection created");
      setSelectedDatasetId(dataset.id);
                      setSelectionRevision((current) => current + 1);
      setDatasetForm((current) => ({
        ...current,
        name: "",
        rename: dataset.name,
      }));
      setActiveModal(null);
      await refreshDatasets();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Failed to create collection",
      ),
  });

  const renameDataset = useMutation({
    mutationFn: () =>
      api.renameDataset(apiContext, selectedDataset!.id, {
        name: datasetForm.rename,
      }),
    onSuccess: async () => {
      toast.success("Collection renamed");
      setActiveModal(null);
      await refreshDatasets();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Failed to rename collection",
      ),
  });

  const deleteDataset = useMutation({
    mutationFn: () => api.deleteDataset(apiContext, selectedDataset!.id),
    onSuccess: async () => {
      toast.success("Collection deleted");
      setSelectedDatasetId("");
      setActiveModal(null);
      await refreshDatasets();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Failed to delete collection",
      ),
  });

  const exportTrace = useMutation({
    mutationFn: () =>
      submitImport("trace", {
        agent_id: assetForm.agent_id,
        run_id: assetForm.run_id,
      }),
    onSuccess: async () => {
      toast.success("Trace import queued — follow Import activity");
      setActiveModal(null);
      await queryClient.invalidateQueries({ queryKey: ["dataset-imports"] });
      await refreshDatasets();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Failed to export Agent trace",
      ),
  });

  const redactTraceRun = useMutation({
    mutationFn: () =>
      api.redactAgentDisplayRun(
        apiContext,
        assetForm.agent_id,
        assetForm.run_id,
      ),
    onSuccess: async () => {
      toast.success("Trace redaction completed");
      await queryClient.invalidateQueries({ queryKey: ["agent-display-runs"] });
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Failed to redact trace",
      ),
  });

  const exportMemory = useMutation({
    mutationFn: () =>
      submitImport("memory", {
        agent_id: assetForm.agent_id,
        memory_item_ids: selectedMemoryItemIds,
      }),
    onSuccess: async () => {
      toast.success("Memory import queued — follow Import activity");
      setActiveModal(null);
      await queryClient.invalidateQueries({ queryKey: ["dataset-imports"] });
      await refreshDatasets();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to export Agent memory",
      ),
  });

  const captureArtifact = useMutation({
    mutationFn: () =>
      submitImport("artifact", {
        agent_id: assetForm.agent_id,
        artifact_id: selectedOutputArtifactId,
      }),
    onSuccess: async () => {
      toast.success("Output import queued — follow Import activity");
      setActiveModal(null);
      await queryClient.invalidateQueries({ queryKey: ["dataset-imports"] });
      await refreshDatasets();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to capture Agent artifact",
      ),
  });

  const scanOutputArtifact = useMutation({
    mutationFn: () =>
      api.scanAgentOutputArtifact(
        apiContext,
        assetForm.agent_id,
        assetForm.run_id,
        selectedOutputArtifactId,
      ),
    onSuccess: async () => {
      toast.success("Output scan completed");
      await queryClient.invalidateQueries({
        queryKey: ["agent-output-artifacts"],
      });
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Failed to scan output file",
      ),
  });

  const createRelease = useMutation({
    mutationFn: () =>
      api.createDatasetVersion(apiContext, selectedDataset!.id, releaseNotes),
    onSuccess: async (version) => {
      toast.success(`Created release ${version.version}`);
      setSelectedReleaseId(version.id);
      setReleaseNotes("");
      setActiveModal(null);
      await refreshDatasets();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Failed to create release",
      ),
  });

  const setQuota = useMutation({
    mutationFn: () =>
      api.setDatasetQuota(apiContext, selectedDataset!.id, {
        max_size: datasetForm.max_size,
      }),
    onSuccess: async () => {
      toast.success("Collection quota updated");
      await refreshDatasets();
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Failed to update quota",
      ),
  });

  const searchDatasets = useMutation({
    mutationFn: () =>
      api.searchDatasets(apiContext, searchForm.q, searchForm.scope),
    onSuccess: (payload) => setSearchPayload(payload),
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Search failed"),
  });

  async function refreshDatasets() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["datasets"] }),
      queryClient.invalidateQueries({ queryKey: ["dataset-detail"] }),
      queryClient.invalidateQueries({ queryKey: ["dataset-pull"] }),
      queryClient.invalidateQueries({ queryKey: ["dataset-versions"] }),
    ]);
  }

  const fileColumns = useMemo<Array<ColumnDef<DatasetFile, unknown>>>(
    () => [
      {
        header: "File",
        cell: ({ row }) => (
          <div className="font-medium text-ink">{row.original.file_name}</div>
        ),
      },
      {
        header: "Source",
        cell: ({ row }) => (
          <AssetSourceBadge source={assetSource(row.original.metadata_json)} />
        ),
      },
      { header: "Type", cell: ({ row }) => row.original.content_type || "-" },
      {
        header: "Size",
        cell: ({ row }) => formatBytes(row.original.size_bytes),
      },
      {
        header: "Status",
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
      {
        header: "",
        cell: ({ row }) => (
          <div className="flex flex-wrap gap-2">
          <DatasetImagePreview context={apiContext} file={row.original} />
          <button
            className="btn min-h-11"
            aria-label={`Download ${row.original.file_name}`}
            onClick={() =>
              void downloadApiFile(
                row.original.download_url,
                row.original.file_name,
                apiContext,
              )
            }
          >
            <Download size={15} />
          </button>
          </div>
        ),
      },
    ],
    [apiContext],
  );

  const versionColumns = useMemo<Array<ColumnDef<DatasetVersion, unknown>>>(
    () => [
      {
        header: "Release",
        cell: ({ row }) => (
          <div>
            <div className="font-medium text-ink">{row.original.version}</div>
            <div className="text-xs text-muted">
              {formatDate(row.original.created_at)}
            </div>
          </div>
        ),
      },
      {
        header: "Assets",
        cell: ({ row }) => {
          if (row.original.snapshot_json.paginated) return `${row.original.file_count} files`;
          const stats = countAssetSources(
            row.original.snapshot_json?.files ?? [],
          );
          return `${stats.trace} trace / ${stats.memory} memory / ${stats.artifact} output / ${stats.upload} imported`;
        },
      },
      {
        header: "Readiness",
        cell: ({ row }) => {
          if (row.original.snapshot_json.paginated) return "Review manifest";
          const readiness = releaseReadinessForFiles(
            row.original.snapshot_json?.files ?? [],
          );
          return (
            <StatusBadge
              status={readiness.status === "ready" ? "Ready" : "Blocked"}
            />
          );
        },
      },
      {
        header: "Size",
        cell: ({ row }) => formatBytes(row.original.size_bytes),
      },
      {
        header: "Status",
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
      {
        header: "Created",
        cell: ({ row }) => formatDate(row.original.created_at),
      },
      {
        header: "",
        cell: ({ row }) => (
          <button
            className="btn h-8 px-2"
            onClick={() => openReleaseManifest(row.original)}
          >
            <FileSearch size={14} />
            View
          </button>
        ),
      },
    ],
    [],
  );

  const datasetFiles = datasetPull.data?.files ?? [];
  const allCollections = datasets.data ?? [];
  const collectionTotal = datasetPage.data?.total ?? 0;
  const totalDatasetBytes = datasetPage.data?.summary.size_bytes ?? 0;
  const draftCollectionCount = datasetPage.data?.summary.drafts ?? 0;
  const filteredCollections = allCollections.filter((dataset) =>
    dataset.name
      .toLocaleLowerCase()
      .includes(collectionFilter.trim().toLocaleLowerCase()),
  );
  const selectedAssetCounts = datasetPull.data?.summary ? {
    trace: datasetPull.data.summary.trace ?? 0, memory: datasetPull.data.summary.memory ?? 0,
    artifact: datasetPull.data.summary.artifact ?? 0, upload: datasetPull.data.summary.upload ?? 0, agent_total: datasetPull.data.summary.agent_total ?? 0,
  } : countAssetSources(datasetFiles);
  const assetPolicyPassed = selectedDataset?.publication_readiness?.checks.some(check => check.key === "policy" && check.ok);
  const currentReleaseReadiness = (datasetPull.data?.total ?? 0) > datasetFiles.length ? {
    status: (assetPolicyPassed ? "ready" : "blocked") as ReleaseReadiness,
    reasons: assetPolicyPassed ? [] : ["The collection has asset policy blockers. Review all pages; publication is validated on the server."],
    counts: selectedAssetCounts,
  } : releaseReadinessForFiles(datasetFiles);
  const nextAction = datasetNextAction(selectedDataset, publishing?.datasetPolicy);
  const management = datasetManagementPresentation(selectedDataset, publishing?.datasetPolicy);
  const eligibleMemoryIds = (selectedAgentMemory.data ?? [])
    .filter(
      (item) =>
        item.consent_status === "approved" &&
        ["approved", "internal"].includes(item.license_status),
    )
    .map((item) => item.id);
  const selectedRelease = useMemo(() => {
    const versions = datasetVersions.data ?? [];
    return (
      versions.find((version) => version.id === selectedReleaseId) ??
      versions[0] ??
      null
    );
  }, [datasetVersions.data, selectedReleaseId]);
  const searchResults = normalizeSearchResults(searchPayload);

  function openCollectionSettings() {
    if (!selectedDataset) return;
    setDeleteConfirmation("");
    setSelectionRevision((current) => current + 1);
    setDatasetForm((current) => ({
      ...current,
      rename: selectedDataset.name,
      max_size: selectedDataset.quota?.max_size || "1GB",
    }));
    setActiveModal("collectionSettings");
  }

  function runNextAction() {
    if (!selectedDataset) return;
    if (nextAction.key === "import") {
      setImportStep(1);
      setActiveModal("importAsset");
      return;
    }
    if (nextAction.key === "release") {
      setCollectionStage("release");
      return;
    }
    setCollectionStage("distribute");
  }

  function openReleaseManifest(release: DatasetVersion) {
    setSelectedReleaseId(release.id);
    setActiveModal("releaseManifest");
  }

  function renderCollectionWorkspace() {
    return (
      <div
        className="data-fabric-workspace"
        id="data-assets-view-panel-collections"
        role="tabpanel"
        aria-labelledby="data-assets-view-tab-collections"
      >
        <aside className="data-object-track" aria-label="Owned collections">
          <div className="data-object-track__header">
            <div>
              <span>COLLECTION INDEX</span>
              <strong>
                {formatNumber(filteredCollections.length)} visible
              </strong>
            </div>
            <button
              className="icon-btn"
              onClick={() => setActiveModal("collectionSearch")}
              aria-label="Search Data Assets"
            >
              <Search size={16} />
            </button>
          </div>
          <label className="data-track-search">
            <Search size={15} aria-hidden="true" />
            <input
              value={collectionFilter}
              onChange={(event) => setCollectionFilter(event.target.value)}
              placeholder="Find a collection"
              aria-label="Find a collection"
            />
          </label>
          {datasets.isLoading ? (
            <LoadingState label="Loading collections" />
          ) : datasets.isError ? (
            <ErrorState
              label="Collections could not be loaded."
              onRetry={() => void datasets.refetch()}
            />
          ) : filteredCollections.length === 0 ? (
            <EmptyState
              title={
                allCollections.length
                  ? "No matching collections"
                  : "No collections"
              }
              description={
                allCollections.length
                  ? "Try a different name."
                  : "Create a governed collection to begin."
              }
            />
          ) : (
            <div className="data-object-track__list">
              {filteredCollections.map((dataset) => {
                const selected = selectedDataset?.id === dataset.id;
                return (
                  <button
                    key={dataset.id}
                    type="button"
                    className={`data-object-row${selected ? " is-selected" : ""}`}
                    aria-current={selected ? "true" : undefined}
                    onClick={() => {
                      setSelectedDatasetId(dataset.id);
                      setSelectionRevision((current) => current + 1);
                      setDatasetForm((current) => ({
                        ...current,
                        rename: dataset.name,
                        max_size: dataset.quota?.max_size || "1GB",
                      }));
                    }}
                  >
                    <DataAssetShape state={dataset.lifecycle_status} />
                    <span className="data-object-row__copy">
                      <strong className="flex flex-wrap items-center gap-2">
                        {dataset.name}
                        <ResourceAccessState access={dataset.access} />
                      </strong>
                      <ResourceOwnershipBadge ownership={dataset.ownership} />
                      <span>
                        {dataset.current_version || "Draft"} ·{" "}
                        {formatNumber(dataset.file_count)} assets
                      </span>
                    </span>
                    <span className="data-object-row__state">
                      {humanizeLifecycle(dataset.lifecycle_status)}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          <DatasetPageControls total={collectionTotal} next={datasetPage.data?.next_cursor} loading={datasets.isFetching} previous={!!collectionCursor} onFirst={() => setCollectionCursor("")} onNext={setCollectionCursor} />
        </aside>

        {selectedDataset ? (
          <main className="data-lifecycle-workbench">
            <header className="data-workbench-header">
              <div>
                <span className="data-fabric-eyebrow">
                  COLLECTION · {compactId(selectedDataset.id)}
                </span>
                <h2>{selectedDataset.name}</h2>
                <p>
                  {humanizeLifecycle(selectedDataset.lifecycle_status)} ·
                  Updated {formatDate(selectedDataset.updated_at)}
                </p>
              </div>
              <button
                className="btn btn-primary min-h-11"
                onClick={runNextAction}
              >
                {nextAction.icon}
                {nextAction.label}
              </button>
            </header>
            <NexilumeTabs<CollectionStage>
              idBase="data-asset-lifecycle"
              label="Collection lifecycle"
              variant="stage"
              value={collectionStage}
              options={[
                {
                  value: "collect",
                  label: "Collect",
                  eyebrow: "01",
                  count: selectedDataset.file_count,
                },
                {
                  value: "govern",
                  label: "Govern",
                  eyebrow: "02",
                  count: currentReleaseReadiness.reasons.length,
                },
                {
                  value: "release",
                  label: "Release",
                  eyebrow: "03",
                  count: datasetVersions.data?.length ?? 0,
                },
                { value: "distribute", label: "Distribute", eyebrow: "04" },
              ]}
              onChange={setCollectionStage}
            />
            <section
              id={`data-asset-lifecycle-panel-${collectionStage}`}
              role="tabpanel"
              aria-labelledby={`data-asset-lifecycle-tab-${collectionStage}`}
              className="data-stage-panel"
            >
              {collectionStage === "collect" && (
                <>
                  <StageHeading
                    eyebrow="COLLECT / ASSETS"
                    title="Current assets"
                    description="Import checked files and images, or archive governed Agent traces, memory and outputs."
                  />
                  <div className="data-evidence-strip">
                    <Detail label="Imported files" value={formatNumber(selectedAssetCounts.upload)} />
                    <Detail
                      label="Trace"
                      value={formatNumber(selectedAssetCounts.trace)}
                    />
                    <Detail
                      label="Memory"
                      value={formatNumber(selectedAssetCounts.memory)}
                    />
                    <Detail
                      label="Output"
                      value={formatNumber(selectedAssetCounts.artifact)}
                    />
                    <Detail
                      label="Total size"
                      value={formatBytes(selectedDataset.size_bytes)}
                    />
                  </div>
                  {datasetPull.isLoading ? (
                    <LoadingState label="Loading current assets" />
                  ) : datasetPull.isError ? (
                    <ErrorState
                      label="Current assets could not be loaded."
                      onRetry={() => void datasetPull.refetch()}
                    />
                  ) : datasetFiles.length === 0 ? (
                    <EmptyState
                      title="Import your first asset"
                      description="Import a file or image, or archive a governed Agent trace, memory or output."
                    />
                  ) : (
                    <DataTable data={datasetFiles} columns={fileColumns} />
                  )}
                  <DatasetPageControls total={datasetPull.data?.total ?? 0} next={datasetPull.data?.next_cursor} loading={datasetPull.isFetching} previous={!!fileCursor} onFirst={() => setFileCursor("")} onNext={setFileCursor} />
                  {selectedDataset.allowed_actions?.includes("import_asset") && <DatasetImports key={selectedDataset.id} ctx={apiContext} datasetId={selectedDataset.id} />}
                </>
              )}
              {collectionStage === "govern" && (
                <>
                  <StageHeading
                    eyebrow="GOVERN / POLICY GATES"
                    title="Release readiness"
                    description="License, sensitivity, consent, provenance, and scan state decide whether this collection can advance."
                  />
                  <ReleaseReadinessPanel
                    readiness={currentReleaseReadiness}
                    fileCount={datasetFiles.length}
                  />
                  <div className="data-stage-next">
                    <div>
                      <span>NEXT OPERATION</span>
                      <strong>
                        {currentReleaseReadiness.status === "ready"
                          ? "Create immutable release"
                          : "Resolve the named blockers"}
                      </strong>
                    </div>
                    <button
                      className="btn"
                      disabled={currentReleaseReadiness.status !== "ready"}
                      onClick={() => setCollectionStage("release")}
                    >
                      Continue to Release
                    </button>
                  </div>
                </>
              )}
              {collectionStage === "release" && (
                <>
                  <StageHeading
                    eyebrow="RELEASE / IMMUTABLE SNAPSHOT"
                    title="Version history"
                    description="A release freezes the exact governed manifest. Later collection changes never alter it."
                    action={
                      <button
                        className="btn btn-primary min-h-11"
                        onClick={() => setActiveModal("createRelease")}
                        disabled={datasetFiles.length === 0}
                      >
                        <FileArchive size={15} />
                        Create release
                      </button>
                    }
                  />
                  <LatestReleaseInline
                    release={selectedRelease}
                    onView={() =>
                      selectedRelease && openReleaseManifest(selectedRelease)
                    }
                  />
                  {datasetVersions.isLoading ? (
                    <LoadingState label="Loading releases" />
                  ) : (datasetVersions.data ?? []).length === 0 ? (
                    <EmptyState
                      title="No immutable release yet"
                      description="Review the current governed manifest, then create the first release."
                    />
                  ) : (
                    <DataTable
                      data={datasetVersions.data ?? []}
                      columns={versionColumns}
                    />
                  )}
                  <DatasetPageControls total={versionPage.data?.total ?? 0} next={versionPage.data?.next_cursor} loading={versionPage.isFetching} previous={!!versionCursor} onFirst={() => setVersionCursor("")} onNext={setVersionCursor} />
                </>
              )}
              {collectionStage === "distribute" && (
                <DatasetPublicationPanel variant="workspace" onShare={() => setShareTarget(datasetShareTarget(selectedDataset))} capacityControl={
<Field label="Download quota">
                      <div className="data-inline-control">
                        <input
                          className="input"
                          value={datasetForm.max_size}
                          onChange={(event) =>
                            setDatasetForm((current) => ({
                              ...current,
                              max_size: event.target.value,
                            }))
                          }
                        />
                        <button
                          className="btn"
                          onClick={() => setQuota.mutate()}
                          disabled={setQuota.isPending}
                        >
                          Save
                        </button>
                      </div>
                    </Field>
} />
              )}
            </section>
          </main>
        ) : (
          <main className="data-lifecycle-workbench">
            <EmptyState
              title="No collection selected"
              description="Create or select a collection to manage its lifecycle."
            />
          </main>
        )}

        {selectedDataset && (
          <aside className="data-inspector" aria-label="Collection inspector">
            <div className="data-inspector__eyebrow">INSPECTOR / OWNED</div>
            <div className="data-inspector__identity">
              <DataAssetShape state={selectedDataset.lifecycle_status} />
              <div>
                <strong>{selectedDataset.name}</strong>
                <span>{compactId(selectedDataset.id)}</span>
              </div>
            </div>
            <div className="data-inspector__facts">
              <Detail
                label="Release"
                value={selectedDataset.current_version || "Draft"}
              />
              <Detail
                label="Ownership"
                value={datasetOwnershipLabel(selectedDataset)}
              />
              <DatasetPublicationSummary surface="inspector" dataset={selectedDataset} />
              <Detail
                label="Quota"
                value={selectedDataset.quota?.max_size || "Not set"}
              />
              <Detail
                label="Status"
                value={humanizeLifecycle(selectedDataset.lifecycle_status)}
              />
            </div>
            <div className="data-inspector__section">
              <span>IDENTITY</span>
              <Field label="Collection name">
                <div className="data-inline-control">
                  <input
                    className="input"
                    value={datasetForm.rename}
                    onChange={(event) =>
                      setDatasetForm((current) => ({
                        ...current,
                        rename: event.target.value,
                      }))
                    }
                  />
                  <button
                    className="btn"
                    onClick={() => renameDataset.mutate()}
                    disabled={
                      !datasetForm.rename.trim() || renameDataset.isPending
                    }
                  >
                    <Pencil size={14} />
                    Save
                  </button>
                </div>
              </Field>
            </div>
            <div className="data-inspector__section data-inspector__danger">
              <span>DANGER ZONE</span>
              <p>
                {management.deletionSummary}
              </p>
              <input
                className="input"
                value={deleteConfirmation}
                onChange={(event) => setDeleteConfirmation(event.target.value)}
                placeholder={`Type ${selectedDataset.name}`}
                aria-label="Confirm collection deletion"
              />
              <button
                className="btn text-red-700"
                onClick={() => deleteDataset.mutate()}
                disabled={
                  deleteConfirmation !== selectedDataset.name ||
                  deleteDataset.isPending
                }
              >
                <Trash2 size={14} />
                Delete collection
              </button>
            </div>
          </aside>
        )}
      </div>
    );
  }

  return (
    <DatasetPublicationScope dataset={selectedDataset} apiContext={apiContext} onSaved={refreshDatasets} selectionRevision={selectionRevision} releaseSizeBytes={selectedRelease?.size_bytes ?? selectedDataset?.size_bytes ?? 0}>
    <div className="grid gap-6">
      <>
          <header className="data-fabric-header">
            <div>
              <div className="data-fabric-eyebrow">
                AI Resources · Data Asset Fabric
              </div>
              <h1>Data Assets</h1>
              <p>
                Collect Agent evidence, govern it, create immutable releases,
                and distribute trusted data.
              </p>
            </div>
            <div className="data-fabric-header__actions">
              <button
                className="btn"
                onClick={() => void refreshDatasets()}
                aria-label="Refresh Data Assets"
              >
                <RefreshCw size={16} />
                Refresh
              </button>
              <button
                className="btn btn-primary"
                onClick={() => setActiveModal("createCollection")}
                disabled={datasetCapabilities.data?.can_create === false}
              >
                <Plus size={16} />
                New collection
              </button>
            </div>
          </header>
          <div
            className="data-fabric-status"
            aria-label="Data Asset workspace summary"
          >
            <DataStatus
              label="Collections"
              value={formatNumber(collectionTotal)}
              detail="Owned"
            />
            <DataStatus
              label="Assets"
              value={formatNumber(
                datasetPage.data?.summary.files ?? 0,
              )}
              detail={formatBytes(totalDatasetBytes)}
            />
            <DatasetPublicationSummary publishedCount={datasetPage.data?.summary.published ?? 0} />
            <DataStatus
              label="Drafts"
              value={formatNumber(draftCollectionCount)}
              detail="No assets yet"
              tone={draftCollectionCount ? "warning" : "healthy"}
            />

          </div>
          <DataLibraryNavigation collectionTotal={collectionTotal} />
      </>

      {renderCollectionWorkspace()}

      {false && (
        <>
          <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
            <Card
              title="Collections"
              description="Select a collection to manage its governed assets."
              action={
                <button
                  className="btn btn-primary min-h-11 px-3"
                  onClick={() => setActiveModal("createCollection")}
                  disabled={datasetCapabilities.data?.can_create === false}
                  title={
                    datasetCapabilities.data?.can_create === false
                      ? "Dataset admin permission is required"
                      : undefined
                  }
                >
                  <Plus size={14} />
                  New
                </button>
              }
            >
              <div className="relative mb-3">
                <Search
                  className="pointer-events-none absolute left-3 top-2.5 text-muted"
                  size={15}
                />
                <input
                  className="input pl-9"
                  value={collectionFilter}
                  onChange={(event) => setCollectionFilter(event.target.value)}
                  placeholder="Find a collection"
                />
              </div>
              {datasets.isLoading ? (
                <LoadingState label="Loading collections" />
              ) : datasets.isError ? (
                <ErrorState
                  label="Collections could not be loaded."
                  onRetry={() => void datasets.refetch()}
                />
              ) : allCollections.length === 0 ? (
                <EmptyState
                  title="No collections"
                  description="Create a collection before importing files, images or Agent assets."
                />
              ) : filteredCollections.length === 0 ? (
                <EmptyState
                  title="No matching collections"
                  description="Try a different collection name."
                />
              ) : (
                <div className="grid max-h-[680px] gap-2 overflow-y-auto pr-1">
                  {filteredCollections.map((dataset) => (
                    <button
                      key={dataset.id}
                      type="button"
                      className={`min-h-20 rounded-lg border p-3 text-left transition ${
                        selectedDataset?.id === dataset.id
                          ? "border-slate-900 bg-slate-900 text-white shadow-sm"
                          : "border-line bg-white hover:border-slate-400 hover:bg-slate-50"
                      }`}
                      onClick={() => {
                        setSelectedDatasetId(dataset.id);
                      setSelectionRevision((current) => current + 1);
                        setDatasetForm((current) => ({
                          ...current,
                          rename: dataset.name,
                          max_size: dataset.quota?.max_size || "1GB",
                        }));
                      }}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <span className="min-w-0 truncate font-semibold">
                          {dataset.name}
                        </span>
                        <span
                          className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                            selectedDataset?.id === dataset.id
                              ? "bg-white/15 text-white"
                              : "bg-slate-100 text-slate-700"
                          }`}
                        >
                          {humanizeLifecycle(dataset.lifecycle_status)}
                        </span>
                      </div>
                      <div
                        className={`mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs ${selectedDataset?.id === dataset.id ? "text-slate-300" : "text-muted"}`}
                      >
                        <span>{formatNumber(dataset.file_count)} assets</span>
                        <span>{formatBytes(dataset.size_bytes)}</span>
                        <span>{dataset.current_version || "No release"}</span>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </Card>

            {selectedDataset ? (
              <div className="grid gap-6">
                <Card
                  title={selectedDataset.name}
                  description={`${humanizeLifecycle(selectedDataset.lifecycle_status)} · Updated ${formatDate(selectedDataset.updated_at)}`}
                  action={
                    <div className="flex flex-wrap justify-end gap-2">
                      <button
                        className="btn min-h-11 px-3"
                        onClick={() =>
                          setShareTarget(datasetShareTarget(selectedDataset))
                        }
                      >
                        <Share2 size={14} />
                        Share
                      </button>
                      <button
                        className="btn min-h-11 px-3"
                        onClick={openCollectionSettings}
                      >
                        <Settings size={14} />
                        Settings
                      </button>
                      <button
                        className="btn min-h-11 px-3"
                        onClick={() => setActiveModal("releaseHistory")}
                        disabled={(datasetVersions.data ?? []).length === 0}
                      >
                        <FileSearch size={14} />
                        Releases
                      </button>
                    </div>
                  }
                >
                  <div className="grid gap-4">
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-md bg-slate-900 text-white">
                        <Database size={18} />
                      </div>
                      <div className="min-w-0">
                        <div className="truncate font-semibold text-ink">
                          {selectedDataset.current_version ||
                            "Draft collection"}
                        </div>
                        <div className="text-sm text-muted">
                          {compactId(selectedDataset.id)}
                        </div>
                      </div>
                      <div className="ml-auto">
                        <StatusBadge status={selectedDataset.visibility} />
                      </div>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
                      <Detail
                        label="Files"
                        value={formatNumber(selectedDataset.file_count)}
                      />
                      <Detail
                        label="Size"
                        value={formatBytes(selectedDataset.size_bytes)}
                      />
                      <Detail
                        label="Trace"
                        value={formatNumber(selectedAssetCounts.trace)}
                      />
                      <Detail
                        label="Memory"
                        value={formatNumber(selectedAssetCounts.memory)}
                      />
                      <Detail
                        label="Output"
                        value={formatNumber(selectedAssetCounts.artifact)}
                      />
                      <Detail
                        label="Quota"
                        value={selectedDataset.quota?.max_size || "Not set"}
                      />
                    </div>
                    <div className="data-stage-next">
                      <div>
                        <div className="data-fabric-eyebrow">
                          Next operation
                        </div>
                        <div className="mt-1 font-semibold text-slate-950">
                          {nextAction.label}
                        </div>
                        <div className="mt-1 text-sm text-slate-600">
                          {nextAction.description}
                        </div>
                      </div>
                      <button
                        className="btn btn-primary min-h-11 shrink-0 px-4"
                        onClick={runNextAction}
                      >
                        {nextAction.icon}
                        {nextAction.label}
                      </button>
                    </div>
                    <LatestReleaseInline
                      release={selectedRelease}
                      onView={() =>
                        selectedRelease && openReleaseManifest(selectedRelease)
                      }
                    />
                  </div>
                </Card>

                <div className="grid gap-6 2xl:grid-cols-[1fr_360px]">
                  <Card
                    title="Current assets"
                    description="Checked files, images, and governed Agent traces, memory and outputs."
                    action={
                      <button
                        className="btn min-h-11 px-3"
                        onClick={() => {
                          setImportStep(1);
                          setActiveModal("importAsset");
                        }}
                      >
                        <Upload size={14} />
                        Import
                      </button>
                    }
                  >
                    {datasetPull.isLoading ? (
                      <LoadingState label="Loading current assets" />
                    ) : datasetPull.isError ? (
                      <ErrorState
                        label="Current assets could not be loaded."
                        onRetry={() => void datasetPull.refetch()}
                      />
                    ) : datasetFiles.length === 0 ? (
                      <EmptyState
                        title="No assets"
                        description="Import files, images or Agent outputs to start this collection."
                      />
                    ) : (
                      <DataTable data={datasetFiles} columns={fileColumns} />
                    )}
                  </Card>

                  <Card
                    title="Release readiness"
                    description="Create immutable releases after current assets pass publication gates."
                    action={
                      <button
                        className="btn btn-primary h-8 px-2"
                        onClick={() => setActiveModal("createRelease")}
                        disabled={datasetFiles.length === 0}
                      >
                        <FileArchive size={14} />
                        Review
                      </button>
                    }
                  >
                    <ReleaseReadinessPanel
                      readiness={currentReleaseReadiness}
                      fileCount={datasetFiles.length}
                    />
                  </Card>
                </div>
              </div>
            ) : (
              <Card title="Selected collection">
                <EmptyState
                  title="No collection selected"
                  description="Create or select a collection to manage files, images and Agent assets."
                />
              </Card>
            )}
          </div>
        </>
      )}

      <Modal
        open={activeModal === "collectionSearch"}
        title="Collection search"
        onClose={() => setActiveModal(null)}
        size="wide"
      >
        <div className="grid gap-4">
          <div className="grid gap-3 md:grid-cols-[1fr_180px_auto]">
            <input
              className="input"
              value={searchForm.q}
              onChange={(event) =>
                setSearchForm((current) => ({
                  ...current,
                  q: event.target.value,
                }))
              }
              placeholder="Search keyword"
            />
            <select
              className="select"
              value={searchForm.scope}
              onChange={(event) =>
                setSearchForm((current) => ({
                  ...current,
                  scope: event.target.value as SearchScope,
                }))
              }
            >
              <option value="all">all</option>
              <option value="metadata">metadata</option>
              <option value="content">content</option>
            </select>
            <button
              className="btn btn-primary"
              onClick={() => searchDatasets.mutate()}
              disabled={searchDatasets.isPending || !searchForm.q}
            >
              {searchDatasets.isPending ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Search size={16} />
              )}
              Search
            </button>
          </div>
          {searchPayload ? (
            <div className="grid gap-4 md:grid-cols-2">
              <SearchPanel
                title="Collection matches"
                icon={<Database size={16} />}
                items={searchResults.datasets.map(
                  (dataset) => `${dataset.name} (${compactId(dataset.id)})`,
                )}
              />
              <SearchPanel
                title="Content matches"
                icon={<FileSearch size={16} />}
                items={searchResults.content.map(
                  (item) =>
                    `${item.dataset.name}/${item.file.file_name}: ${item.matches.join(" | ")}`,
                )}
              />
            </div>
          ) : (
            <p className="text-sm text-muted">
              Search public collection metadata, indexed file content, or both.
            </p>
          )}
        </div>
      </Modal>

      <Modal
        open={activeModal === "createCollection"}
        title="Create collection"
        onClose={() => setActiveModal(null)}
      >
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            createDataset.mutate();
          }}
        >
          <Field label="Name">
            <input
              className="input"
              value={datasetForm.name}
              onChange={(event) =>
                setDatasetForm((current) => ({
                  ...current,
                  name: event.target.value,
                }))
              }
              placeholder="customer-support-assets"
              required
            />
          </Field>
          <ResourceOwnershipPicker
            projects={projects}
            value={datasetOwnership}
            onChange={setDatasetOwnership}
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="btn"
              onClick={() => setActiveModal(null)}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={createDataset.isPending || !datasetForm.name.trim()}
            >
              {createDataset.isPending ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Plus size={16} />
              )}
              Create
            </button>
          </div>
        </form>
      </Modal>

      <Modal
        open={activeModal === "collectionSettings" && Boolean(selectedDataset)}
        title={management.settingsTitle}
        onClose={() => setActiveModal(null)}
        size="wide"
      >
        <div className="grid gap-6">
          <section className="grid gap-4 rounded-lg border border-line p-4">
            <div>
              <h3 className="font-semibold text-ink">Collection details</h3>
              <p className="mt-1 text-sm text-muted">
                {management.settingsDescription}
              </p>
            </div>
            <Field label="Name">
              <div className="flex flex-col gap-2 sm:flex-row">
                <input
                  className="input"
                  value={datasetForm.rename}
                  onChange={(event) =>
                    setDatasetForm((current) => ({
                      ...current,
                      rename: event.target.value,
                    }))
                  }
                />
                <button
                  type="button"
                  className="btn"
                  onClick={() => renameDataset.mutate()}
                  disabled={
                    renameDataset.isPending || !datasetForm.rename.trim()
                  }
                >
                  {renameDataset.isPending ? (
                    <Loader2 size={16} className="animate-spin" />
                  ) : (
                    <Pencil size={16} />
                  )}
                  Rename
                </button>
              </div>
            </Field>
          </section>

          {selectedDataset && <DatasetPublicationPanel variant="settings" onShare={() => setShareTarget(datasetShareTarget(selectedDataset))} capacityControl={
<Field label="Collection capacity">
                <div className="flex gap-2">
                  <select
                    className="select"
                    value={datasetForm.max_size}
                    onChange={(event) =>
                      setDatasetForm((current) => ({
                        ...current,
                        max_size: event.target.value,
                      }))
                    }
                  >
                    <option value="100MB">100 MB</option>
                    <option value="1GB">1 GB</option>
                    <option value="10GB">10 GB</option>
                    <option value="100GB">100 GB</option>
                  </select>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => setQuota.mutate()}
                    disabled={setQuota.isPending || !datasetForm.max_size}
                  >
                    Save
                  </button>
                </div>
              </Field>
} />}

          <section className="grid gap-3 rounded-lg border border-red-200 bg-red-50 p-4">
            <div className="text-sm font-semibold text-red-900">
              Delete collection
            </div>
            <div className="text-sm text-red-900">
              {management.deletionBlockReason
                ?? `Type “${selectedDataset?.name}” to confirm permanent removal of the collection and its release records.`}
            </div>
            {!management.deletionBlockReason && (
              <input
                className="input max-w-md border-red-300"
                value={deleteConfirmation}
                onChange={(event) => setDeleteConfirmation(event.target.value)}
                placeholder={selectedDataset?.name}
                aria-label="Confirm collection name"
              />
            )}
            <button
              type="button"
              className="btn btn-danger w-fit min-h-11"
              onClick={() => deleteDataset.mutate()}
              disabled={
                deleteDataset.isPending ||
                Boolean(management.deletionBlockReason) ||
                deleteConfirmation !== selectedDataset?.name
              }
            >
              {deleteDataset.isPending ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Trash2 size={16} />
              )}
              Delete collection
            </button>
          </section>
        </div>
      </Modal>

      <Modal
        open={activeModal === "importAsset" && Boolean(selectedDataset)}
        title="Import assets"
        busy={fileImportBusy}
        onClose={() => { if (!fileImportBusy) setActiveModal(null); }}
        size="wide"
      >
        <div className="grid gap-5">
          <NexilumeTabs label="Import source" options={[{ value: "files", label: "Files & images" }, { value: "agent", label: "Agent assets" }]}
            value={importSource} onChange={value => { if (!fileImportBusy) setImportSource(value); }} variant="compact" idBase="asset-import-source" />
          {importSource === "files" && selectedDataset && <div role="tabpanel" id="asset-import-source-panel-files" aria-labelledby="asset-import-source-tab-files">
            <DatasetFileImport key={`${selectedDataset.id}|${apiContext.tenantId}`} context={apiContext} datasetId={selectedDataset.id}
              policy={datasetCapabilities.data?.file_import} onBusy={setFileImportBusy} onComplete={async () => {
                await refreshDatasets();
                await datasetPull.refetch();
              }} />
          </div>}
          {importSource === "agent" && <div role="tabpanel" id="asset-import-source-panel-agent" aria-labelledby="asset-import-source-tab-agent" className="grid gap-5">
          <WizardSteps
            current={importStep}
            steps={["Choose Agent", "Asset type", "Select & import"]}
          />

          {importStep === 1 && (
            <div className="grid gap-5 rounded-lg border border-line p-5 lg:grid-cols-[minmax(240px,340px)_1fr]">
              <Field label="Agent">
                <select
                  className="select"
                  value={assetForm.agent_id}
                  onChange={(event) => {
                    setSelectedMemoryItemIds([]);
                    setSelectedOutputArtifactId("");
                    setAssetForm((current) => ({
                      ...current,
                      agent_id: event.target.value,
                      run_id: "",
                    }));
                  }}
                >
                  <option value="">Select Agent</option>
                  {(agents.data ?? []).map((agent) => (
                    <option key={agent.id} value={agent.id}>
                      {agent.name}
                    </option>
                  ))}
                </select>
              </Field>
              <div className="grid gap-3 md:grid-cols-4">
                <AssetStat
                  icon={<MessageSquareText size={16} />}
                  label="Trace"
                  detail="AG-UI run events"
                />
                <AssetStat
                  icon={<Brain size={16} />}
                  label="Memory"
                  detail={`${selectedAgentMemory.data?.length ?? 0} items`}
                />
                <AssetStat
                  icon={<FileOutput size={16} />}
                  label="Output"
                  detail="Run files and generated images"
                />
                <AssetStat
                  icon={<Database size={16} />}
                  label="Collection"
                  detail={selectedDataset?.name ?? "-"}
                />
              </div>
              <div className="flex justify-end lg:col-span-2">
                <button
                  type="button"
                  className="btn btn-primary min-h-11"
                  onClick={() => setImportStep(2)}
                  disabled={!selectedAgent}
                >
                  Continue
                </button>
              </div>
            </div>
          )}

          {importStep === 2 && (
            <div className="grid gap-4 rounded-lg border border-line p-5">
              <div className="grid gap-3 md:grid-cols-3">
                <button
                  type="button"
                  className={`data-import-choice${assetImportMode === "trace" ? " is-selected" : ""}`}
                  onClick={() => setAssetImportMode("trace")}
                >
                  <MessageSquareText size={15} />
                  <span className="mt-3 block font-semibold">
                    Conversation Trace
                  </span>
                  <span className="mt-1 block text-xs text-muted">
                    Completed AG-UI events with redaction.
                  </span>
                </button>
                <button
                  type="button"
                  className={`data-import-choice${assetImportMode === "memory" ? " is-selected" : ""}`}
                  onClick={() => setAssetImportMode("memory")}
                >
                  <Brain size={15} />
                  <span className="mt-3 block font-semibold">Agent Memory</span>
                  <span className="mt-1 block text-xs text-muted">
                    Explicitly selected, consented memory.
                  </span>
                </button>
                <button
                  type="button"
                  className={`data-import-choice${assetImportMode === "artifact" ? " is-selected" : ""}`}
                  onClick={() => setAssetImportMode("artifact")}
                >
                  <FileOutput size={15} />
                  <span className="mt-3 block font-semibold">Output files & images</span>
                  <span className="mt-1 block text-xs text-muted">
                    Scanned files and output.image() pictures from a run.
                  </span>
                </button>
              </div>
              <div className="flex justify-between gap-2">
                <button
                  type="button"
                  className="btn min-h-11"
                  onClick={() => setImportStep(1)}
                >
                  Back
                </button>
                <button
                  type="button"
                  className="btn btn-primary min-h-11"
                  onClick={() => setImportStep(3)}
                >
                  Continue
                </button>
              </div>
            </div>
          )}

          {importStep === 3 && assetImportMode === "trace" && (
            <div className="grid gap-3 rounded-md border border-line bg-slate-50 p-4">
              <button
                type="button"
                className="btn w-fit min-h-11"
                onClick={() => setImportStep(2)}
              >
                Back to asset types
              </button>
              <RunSelect
                runs={selectedAgentRuns.data ?? []}
                value={assetForm.run_id}
                onChange={(runId) =>
                  setAssetForm((current) => ({ ...current, run_id: runId }))
                }
                requireApprovedTrace
              />
              {selectedTraceRun && <TraceReadiness run={selectedTraceRun} />}
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn"
                  onClick={() => redactTraceRun.mutate()}
                  disabled={
                    redactTraceRun.isPending ||
                    !selectedAgent ||
                    !traceRunCanRedact(selectedTraceRun)
                  }
                >
                  {redactTraceRun.isPending ? (
                    <Loader2 size={16} className="animate-spin" />
                  ) : (
                    <CheckCircle2 size={16} />
                  )}
                  Run redaction
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => exportTrace.mutate()}
                  disabled={
                    exportTrace.isPending ||
                    !selectedAgent ||
                    !traceRunReady(selectedTraceRun)
                  }
                >
                  {exportTrace.isPending ? (
                    <Loader2 size={16} className="animate-spin" />
                  ) : (
                    <Upload size={16} />
                  )}
                  Export approved trace
                </button>
              </div>
            </div>
          )}

          {importStep === 3 && assetImportMode === "memory" && (
            <div className="grid gap-3 rounded-md border border-line bg-slate-50 p-4">
              <button
                type="button"
                className="btn w-fit min-h-11"
                onClick={() => setImportStep(2)}
              >
                Back to asset types
              </button>
              <div className="rounded-md border border-line bg-white p-3 text-sm text-muted">
                Select the exact memory items to export. Empty selection never
                exports data.
              </div>
              <div className="flex flex-wrap justify-between gap-2">
                <button
                  type="button"
                  className="btn min-h-11"
                  onClick={() => setSelectedMemoryItemIds(eligibleMemoryIds)}
                  disabled={eligibleMemoryIds.length === 0}
                >
                  <ListChecks size={16} />
                  Select all eligible ({eligibleMemoryIds.length})
                </button>
                <button
                  type="button"
                  className="btn btn-primary min-h-11"
                  onClick={() => exportMemory.mutate()}
                  disabled={
                    exportMemory.isPending ||
                    !selectedAgent ||
                    selectedMemoryItemIds.length === 0
                  }
                >
                  {exportMemory.isPending ? (
                    <Loader2 size={16} className="animate-spin" />
                  ) : (
                    <Upload size={16} />
                  )}
                  Export {selectedMemoryItemIds.length || ""} selected
                </button>
              </div>
              {selectedAgent && (
                <MemoryPreview
                  items={selectedAgentMemory.data ?? []}
                  selectedIds={selectedMemoryItemIds}
                  onToggle={(memoryId) =>
                    setSelectedMemoryItemIds((current) =>
                      current.includes(memoryId)
                        ? current.filter((item) => item !== memoryId)
                        : [...current, memoryId],
                    )
                  }
                />
              )}
            </div>
          )}

          {importStep === 3 && assetImportMode === "artifact" && (
            <div className="grid gap-3 rounded-md border border-line bg-slate-50 p-4">
              <button
                type="button"
                className="btn w-fit min-h-11"
                onClick={() => setImportStep(2)}
              >
                Back to asset types
              </button>
              <RunSelect
                runs={selectedAgentRuns.data ?? []}
                value={assetForm.run_id}
                labelMode="output"
                onChange={(runId) => {
                  setAssetForm((current) => ({ ...current, run_id: runId }));
                  setSelectedOutputArtifactId("");
                }}
              />
              <OutputArtifactPicker
                artifacts={selectedOutputArtifacts.data ?? []}
                selectedId={selectedOutputArtifactId}
                loading={selectedOutputArtifacts.isLoading}
                onSelect={setSelectedOutputArtifactId}
              />
              {selectedOutputArtifact && (
                <OutputReadiness artifact={selectedOutputArtifact} />
              )}
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn"
                  onClick={() => scanOutputArtifact.mutate()}
                  disabled={
                    scanOutputArtifact.isPending ||
                    !selectedAgent ||
                    !assetForm.run_id ||
                    !selectedOutputArtifact
                  }
                >
                  {scanOutputArtifact.isPending ? (
                    <Loader2 size={16} className="animate-spin" />
                  ) : (
                    <FileSearch size={16} />
                  )}
                  Scan output
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => captureArtifact.mutate()}
                  disabled={
                    captureArtifact.isPending ||
                    !selectedAgent ||
                    !outputArtifactReady(selectedOutputArtifact)
                  }
                >
                  {captureArtifact.isPending ? (
                    <Loader2 size={16} className="animate-spin" />
                  ) : (
                    <Upload size={16} />
                  )}
                  Capture approved output
                </button>
              </div>
            </div>
          )}
          </div>}
        </div>
      </Modal>

      <Modal
        open={activeModal === "createRelease" && Boolean(selectedDataset)}
        title="Create release"
        onClose={() => setActiveModal(null)}
      >
        <div className="grid gap-4">
          <ReleaseConfirmPanel
            readiness={currentReleaseReadiness}
            fileCount={datasetFiles.length}
          />
          <Field label="Release notes (optional)">
            <textarea
              className="textarea min-h-32"
              maxLength={20000}
              value={releaseNotes}
              onChange={(event) => setReleaseNotes(event.target.value)}
              placeholder="Summarize what changed in this immutable release using Markdown."
            />
          </Field>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="btn"
              onClick={() => setActiveModal(null)}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => createRelease.mutate()}
              disabled={
                createRelease.isPending ||
                currentReleaseReadiness.status !== "ready"
              }
            >
              {createRelease.isPending ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <FileArchive size={16} />
              )}
              Create release
            </button>
          </div>
        </div>
      </Modal>

      <Modal
        open={activeModal === "releaseHistory" && Boolean(selectedDataset)}
        title="Release history"
        onClose={() => setActiveModal(null)}
        size="wide"
      >
        {(datasetVersions.data ?? []).length === 0 ? (
          <EmptyState
            title="No releases"
            description="Create a release after imported files and Agent assets pass the readiness checks."
          />
        ) : (
          <DataTable
            data={datasetVersions.data ?? []}
            columns={versionColumns}
          />
        )}
      </Modal>

      <Modal
        open={activeModal === "releaseManifest" && Boolean(selectedRelease)}
        title="Release manifest"
        onClose={() => setActiveModal(null)}
        size="wide"
      >
        <ReleaseManifest key={selectedRelease?.id} release={selectedRelease} apiContext={apiContext} datasetId={selectedDataset?.id ?? ""} />
      </Modal>
      <ShareResourceModal
        target={shareTarget}
        onClose={() => setShareTarget(null)}
      />
    </div>
    </DatasetPublicationScope>
  );
}

function sortedUnique(values: string[]) {
  return Array.from(new Set(values.filter(Boolean))).sort((left, right) =>
    left.localeCompare(right),
  );
}

function WizardSteps({ current, steps }: { current: number; steps: string[] }) {
  return (
    <ol className="grid gap-2 sm:grid-cols-3" aria-label="Import progress">
      {steps.map((step, index) => {
        const number = index + 1;
        const active = number === current;
        const complete = number < current;
        return (
          <li
            key={step}
            className={`data-task-step${active ? " is-active" : ""}${complete ? " is-complete" : ""}`}
          >
            <span>{complete ? "✓" : number}</span>
            <span className="text-sm font-medium">{step}</span>
          </li>
        );
      })}
    </ol>
  );
}

function datasetShareTarget(dataset: Dataset): ShareResourceTarget {
  return {
    resourceType: "dataset",
    resourceKind: "Collection",
    resourceId: dataset.id,
    resourceName: dataset.name,
  };
}

function Modal({
  open,
  title,
  onClose,
  children,
  size = "default",
  busy = false,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  size?: "default" | "wide";
  busy?: boolean;
}) {
  return (
    <NexilumeDialog
      open={open}
      title={title}
      eyebrow="DATA ASSET TASK"
      onClose={onClose}
      busy={busy}
      size={size === "wide" ? "large" : "medium"}
    >
      {children}
    </NexilumeDialog>
  );
}

function LatestReleaseInline({
  release,
  onView,
}: {
  release: DatasetVersion | null;
  onView: () => void;
}) {
  if (!release) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line bg-slate-50 px-3 py-2">
        <div>
          <span className="label">Latest release</span>
          <span className="ml-2 text-sm text-muted">None</span>
        </div>
      </div>
    );
  }
  const files = release.snapshot_json?.files ?? [];
  const readiness = releaseReadinessForFiles(files);
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line bg-slate-50 px-3 py-2">
      <div className="min-w-0">
        <div className="label">Latest release</div>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <span className="font-medium text-ink">{release.version}</span>
          <span className="text-sm text-muted">
            {formatNumber(release.file_count)} files
          </span>
          <span className="text-sm text-muted">
            {formatDate(release.created_at)}
          </span>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <StatusBadge
          status={release.snapshot_json.paginated ? "Immutable" : readiness.status === "ready" ? "Ready" : "Blocked"}
        />
        <button className="btn h-8 px-2" onClick={onView}>
          <FileSearch size={14} />
          Manifest
        </button>
      </div>
    </div>
  );
}

function ReleaseConfirmPanel({
  readiness,
  fileCount,
}: {
  readiness: ReturnType<typeof releaseReadinessForFiles>;
  fileCount: number;
}) {
  const ready = readiness.status === "ready";
  return (
    <div
      className={`rounded-md border p-4 ${ready ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="font-semibold text-ink">
            {ready ? "Ready to create release" : "Release is blocked"}
          </div>
          <div className="mt-1 text-sm text-muted">
            {formatNumber(fileCount)} current asset files will be snapshotted.
          </div>
        </div>
        <StatusBadge status={ready ? "Ready" : "Blocked"} />
      </div>
      {readiness.reasons.length > 0 && (
        <div className="mt-3 grid gap-2">
          {readiness.reasons.slice(0, 5).map((reason) => (
            <div
              key={reason}
              className="flex items-center gap-2 text-sm text-amber-900"
            >
              <AlertTriangle size={15} />
              <span>{reason}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ReleaseManifest({
  release,
  apiContext,
  datasetId,
}: {
  release: DatasetVersion | null;
  apiContext: ReturnType<typeof useAuth>["apiContext"];
  datasetId: string;
}) {
  const [cursor, setCursor] = useState("");
  const manifest = useQuery({
    queryKey: ["dataset-version-files", apiContext, datasetId, release?.id, cursor],
    queryFn: () => api.datasetVersionFilePage(apiContext, datasetId, release!.id, cursor),
    enabled: Boolean(release && datasetId),
  });
  const files = manifest.data?.items ?? [];
  const readiness = releaseReadinessForFiles(files);
  const columns = useMemo<
    Array<ColumnDef<DatasetVersionFileSnapshot, unknown>>
  >(
    () => [
      {
        header: "File",
        cell: ({ row }) => (
          <div>
            <div className="font-medium text-ink">{row.original.file_name}</div>
            <div className="font-mono text-xs text-muted">
              {compactId(row.original.file_id)}
            </div>
          </div>
        ),
      },
      {
        header: "Source",
        cell: ({ row }) => (
          <AssetSourceBadge source={assetSource(row.original.metadata_json)} />
        ),
      },
      {
        header: "Agent",
        cell: ({ row }) => (
          <span className="font-mono text-xs">
            {compactId(stringMeta(row.original.metadata_json, "agent_id")) ||
              "-"}
          </span>
        ),
      },
      {
        header: "Run",
        cell: ({ row }) => (
          <span className="font-mono text-xs">
            {compactId(stringMeta(row.original.metadata_json, "run_id")) || "-"}
          </span>
        ),
      },
      {
        header: "Gate",
        cell: ({ row }) => (
          <StatusBadge
            status={
              fileReadiness(row.original).status === "ready"
                ? "Ready"
                : "Blocked"
            }
          />
        ),
      },
      {
        header: "Size",
        cell: ({ row }) => formatBytes(row.original.size_bytes),
      },
      { header: "SHA256", cell: ({ row }) => compactId(row.original.sha256) },
      {
        header: "",
        cell: ({ row }) => (
          <div className="flex flex-wrap gap-2">
          <DatasetImagePreview context={apiContext} file={row.original} />
          <button
            className="btn min-h-11"
            aria-label={`Download ${row.original.file_name}`}
            onClick={() =>
              void downloadApiFile(
                row.original.download_url,
                row.original.file_name,
                apiContext,
              )
            }
          >
            <Download size={15} />
          </button>
          </div>
        ),
      },
    ],
    [apiContext],
  );

  if (!release) return null;
  if (manifest.isPending) return <LoadingState label="Loading immutable manifest" />;
  if (manifest.isError) return <ErrorState label="Manifest unavailable" onRetry={() => void manifest.refetch()} />;

  return (
    <div className="grid gap-3 rounded-md border border-line bg-slate-50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-ink">
            Release manifest: {release.version}
          </div>
          <div className="mt-1 text-xs text-muted">
            {formatNumber(manifest.data?.total ?? 0)} files -{" "}
            {formatBytes(release.size_bytes)} - created{" "}
            {formatDate(release.created_at)}
          </div>
        </div>
        <StatusBadge
          status={readiness.status === "ready" ? "Ready" : "Blocked"}
        />
      </div>
      {readiness.reasons.length > 0 && (
        <div className="grid gap-2">
          {readiness.reasons.slice(0, 4).map((reason) => (
            <div
              key={reason}
              className="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
            >
              <AlertTriangle size={15} />
              <span>{reason}</span>
            </div>
          ))}
        </div>
      )}
      <DataTable data={files} columns={columns} />
      {manifest.data?.warning_code === "LEGACY_MANIFEST_INCOMPLETE" && <p role="status" className="text-sm text-amber-900">Historical manifest details are unavailable for {manifest.data.unavailable_entries} files. The original records have been preserved; no download identity or checksum has been guessed.</p>}
      <DatasetPageControls total={manifest.data?.total ?? 0} next={manifest.data?.next_cursor} loading={manifest.isFetching} previous={!!cursor} onFirst={() => setCursor("")} onNext={setCursor} />
    </div>
  );
}

function ReleaseReadinessPanel({
  readiness,
  fileCount,
}: {
  readiness: ReturnType<typeof releaseReadinessForFiles>;
  fileCount: number;
}) {
  const stats = readiness.counts;
  const ready = readiness.status === "ready";
  return (
    <div
      className={`rounded-md border p-4 ${ready ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div
            className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-md ${ready ? "bg-emerald-700" : "bg-amber-700"} text-white`}
          >
            {ready ? <CheckCircle2 size={17} /> : <ListChecks size={17} />}
          </div>
          <div>
            <div className="font-semibold text-ink">Release readiness</div>
            <div className="mt-1 text-sm text-muted">
              {formatNumber(fileCount)} files - {stats.trace} trace /{" "}
              {stats.memory} memory / {stats.artifact} output
            </div>
          </div>
        </div>
        <StatusBadge status={ready ? "Ready" : "Blocked"} />
      </div>
      {readiness.reasons.length > 0 && (
        <div className="mt-3 grid gap-2">
          {readiness.reasons.slice(0, 5).map((reason) => (
            <div
              key={reason}
              className="flex items-center gap-2 text-sm text-amber-900"
            >
              <AlertTriangle size={15} />
              <span>{reason}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SearchPanel({
  title,
  icon,
  items,
}: {
  title: string;
  icon: React.ReactNode;
  items: string[];
}) {
  return (
    <div className="rounded-md border border-line bg-white p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-ink">
        {icon}
        {title}
      </div>
      {items.length === 0 ? (
        <p className="text-sm text-muted">No matches</p>
      ) : (
        <div className="grid gap-2">
          {items.map((item, index) => (
            <div
              key={`${item}-${index}`}
              className="rounded-md bg-slate-50 p-2 text-sm text-slate-700"
            >
              {item}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AssetStat({
  icon,
  label,
  detail,
}: {
  icon: React.ReactNode;
  label: string;
  detail: string;
}) {
  return (
    <div className="rounded-md border border-line bg-white p-3">
      <div className="flex items-center gap-2 text-sm font-semibold text-ink">
        {icon}
        <span className="truncate">{label}</span>
      </div>
      <div className="mt-1 truncate text-xs text-muted">{detail}</div>
    </div>
  );
}

function RunSelect({
  runs,
  value,
  onChange,
  optional = false,
  requireApprovedTrace = false,
  labelMode = "trace",
}: {
  runs: AgentDisplayRun[];
  value: string;
  onChange: (runId: string) => void;
  optional?: boolean;
  requireApprovedTrace?: boolean;
  labelMode?: "trace" | "output";
}) {
  return (
    <Field label={optional ? "Source run" : "Run"}>
      <select
        className="select"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">
          {runs.length === 0
            ? "No Agent runs available"
            : optional
              ? "No source run"
              : "Select Agent run"}
        </option>
        {runs.map((run) => (
          <option key={run.id} value={run.id}>
            {runLabel(run, labelMode)}
          </option>
        ))}
      </select>
      {requireApprovedTrace && runs.length > 0 && !runs.some(traceRunReady) && (
        <div className="mt-1 text-xs text-muted">
          Select a completed run, run redaction, then export after redaction
          passes.
        </div>
      )}
    </Field>
  );
}

function TraceReadiness({ run }: { run: AgentDisplayRun }) {
  const ready = traceRunReady(run);
  const canRedact = traceRunCanRedact(run);
  return (
    <div
      className={`rounded-md border p-3 ${ready ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-ink">
            {ready ? "Trace ready for export" : "Trace needs redaction"}
          </div>
          <div className="mt-1 text-xs text-muted">
            {run.status} - redaction {run.redaction_status} -{" "}
            {formatNumber(run.latest_seq)} events
          </div>
        </div>
        <StatusBadge
          status={ready ? "Ready" : canRedact ? "Can redact" : "Blocked"}
        />
      </div>
      {!ready && (
        <div className="mt-2 text-sm text-amber-900">
          {traceRunBlockReason(run)}
        </div>
      )}
    </div>
  );
}

function OutputArtifactPicker({
  artifacts,
  selectedId,
  loading,
  onSelect,
}: {
  artifacts: AgentOutputArtifact[];
  selectedId: string;
  loading: boolean;
  onSelect: (artifactId: string) => void;
}) {
  if (loading) {
    return (
      <div className="rounded-md border border-line bg-white p-3 text-sm text-muted">
        Loading output files...
      </div>
    );
  }
  if (artifacts.length === 0) {
    return (
      <EmptyState
        title="No output files"
        description="This run has not reported Agent-generated output files."
      />
    );
  }
  return (
    <div className="grid gap-2">
      {artifacts.map((artifact) => {
        const selected = artifact.id === selectedId;
        return (
          <button
            type="button"
            key={artifact.id}
            className={`rounded-md border p-3 text-left ${selected ? "border-brand bg-brand/5" : "border-line bg-white hover:border-slate-300"}`}
            onClick={() => onSelect(artifact.id)}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate font-mono text-sm text-ink">
                  {artifact.original_file_name}
                </div>
                <div className="mt-1 truncate font-mono text-xs text-muted">
                  {artifact.workspace_path}
                </div>
              </div>
              <div className="flex flex-wrap gap-1">
                <StatusBadge status={`scan ${artifact.scan_status}`} />
                <StatusBadge status={`policy ${artifact.policy_status}`} />
                <StatusBadge status={`license ${artifact.license_status}`} />
              </div>
            </div>
            <div className="mt-2 text-xs text-muted">
              {artifact.content_type || "unknown type"} -{" "}
              {formatBytes(artifact.size_bytes)} -{" "}
              {artifact.sha256 ? compactId(artifact.sha256) : "not hashed"}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function OutputReadiness({ artifact }: { artifact: AgentOutputArtifact }) {
  const ready = outputArtifactReady(artifact);
  return (
    <div
      className={`rounded-md border p-3 ${ready ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-ink">
            {ready
              ? "Output ready for capture"
              : "Output needs scan or policy approval"}
          </div>
          <div className="mt-1 text-xs text-muted">
            scan {artifact.scan_status} - policy {artifact.policy_status} -
            license {artifact.license_status}
          </div>
        </div>
        <StatusBadge status={ready ? "Ready" : "Blocked"} />
      </div>
      {!ready && (
        <div className="mt-2 text-sm text-amber-900">
          {outputArtifactBlockReason(artifact)}
        </div>
      )}
    </div>
  );
}

function MemoryPreview({
  items,
  selectedIds,
  onToggle,
}: {
  items: AgentMemoryItem[];
  selectedIds: string[];
  onToggle: (memoryId: string) => void;
}) {
  if (items.length === 0) {
    return (
      <p className="text-sm text-muted">
        No memory items for the selected Agent.
      </p>
    );
  }
  return (
    <div className="grid gap-2">
      {items.slice(0, 4).map((item) => (
        <div
          key={item.id}
          className="rounded-md border border-line bg-slate-50 p-2"
        >
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={selectedIds.includes(item.id)}
              onChange={() => onToggle(item.id)}
              disabled={
                item.consent_status !== "approved" ||
                !["approved", "internal"].includes(item.license_status)
              }
              aria-label={`Select memory ${compactId(item.id)}`}
            />
            <span className="font-mono text-xs text-muted">
              {compactId(item.id)}
            </span>
            <StatusBadge status={item.memory_type} />
            <StatusBadge status={item.consent_status} />
            <StatusBadge status={`license ${item.license_status}`} />
          </div>
          <div className="mt-1 line-clamp-2 text-sm text-slate-700">
            {item.content_text || JSON.stringify(item.content_json)}
          </div>
        </div>
      ))}
      {items.length > 4 && (
        <div className="text-xs text-muted">
          {items.length - 4} more memory items
        </div>
      )}
      {selectedIds.length === 0 && (
        <div className="text-xs text-amber-700">
          Select at least one eligible memory item to continue.
        </div>
      )}
    </div>
  );
}

function AssetSourceBadge({ source }: { source: AgentAssetSource | string }) {
  const labels: Record<string, string> = {
    agent_trace: "Trace",
    agent_memory: "Memory",
    agent_artifact: "Output",
    user_upload: "Imported file",
  };
  return <StatusBadge status={labels[source] ?? source} />;
}

function assetSource(
  metadata: Record<string, unknown> | undefined,
): AgentAssetSource | string {
  return String(metadata?.source_type || "unsupported");
}

function countAssetSources(files: AssetFileLike[]) {
  const counts = { trace: 0, memory: 0, artifact: 0, upload: 0, agent_total: 0 };
  for (const file of files) {
    const source = assetSource(file.metadata_json);
    if (source === "agent_trace") counts.trace += 1;
    if (source === "agent_memory") counts.memory += 1;
    if (source === "agent_artifact") counts.artifact += 1;
    if (source === "user_upload") counts.upload += 1;
  }
  counts.agent_total = counts.trace + counts.memory + counts.artifact;
  return counts;
}

function releaseReadinessForFiles(files: AssetFileLike[]) {
  const reasons = files.flatMap((file) => fileReadiness(file).reasons);
  if (files.length === 0)
    reasons.push(
      "Add at least one checked file or Agent asset before creating a release.",
    );
  return {
    status:
      reasons.length === 0
        ? ("ready" as ReleaseReadiness)
        : ("blocked" as ReleaseReadiness),
    reasons,
    counts: countAssetSources(files),
  };
}

function fileReadiness(file: AssetFileLike) {
  const metadata = file.metadata_json ?? {};
  const source = assetSource(metadata);
  const reasons: string[] = [];

  if (!["agent_trace", "agent_memory", "agent_artifact", "user_upload"].includes(source)) {
    reasons.push(`${file.file_name}: unsupported source type "${source}".`);
  }
  if (source !== "user_upload" && !stringMeta(metadata, "agent_id")) {
    reasons.push(`${file.file_name}: missing Agent provenance.`);
  }
  if (
    source === "agent_trace" &&
    stringMeta(metadata, "redaction_status") !== "passed"
  ) {
    reasons.push(`${file.file_name}: trace redaction must be passed.`);
  }
  if (source === "agent_memory") {
    if (stringMeta(metadata, "consent_status") !== "approved") {
      reasons.push(`${file.file_name}: memory consent must be approved.`);
    }
    if (
      !["approved", "internal"].includes(stringMeta(metadata, "license_status"))
    ) {
      reasons.push(
        `${file.file_name}: memory license must be approved or internal.`,
      );
    }
  }
  if (source === "user_upload" && stringMeta(metadata, "consent_status") !== "approved") {
    reasons.push(`${file.file_name}: import consent is required.`);
  }
  if (source === "agent_artifact" || source === "user_upload") {
    if (stringMeta(metadata, "scan_status") !== "passed") {
      reasons.push(`${file.file_name}: output file scan must be passed.`);
    }
    if (stringMeta(metadata, "policy_status") !== "approved") {
      reasons.push(`${file.file_name}: output file policy must be approved.`);
    }
    if (
      !["approved", "internal"].includes(stringMeta(metadata, "license_status"))
    ) {
      reasons.push(
        `${file.file_name}: output file license must be approved or internal.`,
      );
    }
  }

  return {
    status:
      reasons.length === 0
        ? ("ready" as ReleaseReadiness)
        : ("blocked" as ReleaseReadiness),
    reasons,
  };
}

function stringMeta(
  metadata: Record<string, unknown> | undefined,
  key: string,
) {
  const value = metadata?.[key];
  return typeof value === "string" ? value : "";
}

function runLabel(run: AgentDisplayRun, mode: "trace" | "output" = "trace") {
  const title = run.title || "Untitled run";
  if (mode === "output") {
    const readiness =
      run.status === "completed"
        ? "ready to scan outputs"
        : "run not completed";
    return `${title} - ${run.status} - ${run.latest_seq} events - ${readiness}`;
  }
  const readiness = traceRunReady(run)
    ? "ready"
    : `blocked: ${traceRunBlockReason(run)}`;
  return `${title} - ${run.status} - redaction ${run.redaction_status} - ${run.latest_seq} events - ${readiness}`;
}

function traceRunReady(run: AgentDisplayRun | undefined) {
  return Boolean(
    run &&
    run.status === "completed" &&
    run.redaction_status === "passed" &&
    run.latest_seq > 0,
  );
}

function traceRunCanRedact(run: AgentDisplayRun | undefined) {
  return Boolean(run && run.status === "completed" && run.latest_seq > 0);
}

function traceRunBlockReason(run: AgentDisplayRun) {
  if (run.status !== "completed") return "run not completed";
  if (run.redaction_status !== "passed") return "redaction not passed";
  if (run.latest_seq <= 0) return "no events";
  return "not ready";
}

function outputArtifactReady(artifact: AgentOutputArtifact | null | undefined) {
  return Boolean(
    artifact &&
    artifact.scan_status === "passed" &&
    artifact.policy_status === "approved" &&
    ["approved", "internal"].includes(artifact.license_status) &&
    artifact.sha256,
  );
}

function outputArtifactBlockReason(artifact: AgentOutputArtifact) {
  if (artifact.scan_status !== "passed") return "system scan not passed";
  if (artifact.policy_status !== "approved") return "policy not approved";
  if (!["approved", "internal"].includes(artifact.license_status))
    return "license not approved or internal";
  if (!artifact.sha256) return "missing scanned hash";
  return "not ready";
}

function normalizeSearchResults(
  payload:
    Dataset[] | DatasetContentSearchResult[] | DatasetSearchAllResult | null,
) {
  if (!payload)
    return {
      datasets: [] as Dataset[],
      content: [] as DatasetContentSearchResult[],
    };
  if (Array.isArray(payload)) {
    const first = payload[0] as
      Dataset | DatasetContentSearchResult | undefined;
    if (first && "file" in first)
      return {
        datasets: [] as Dataset[],
        content: payload as DatasetContentSearchResult[],
      };
    return {
      datasets: payload as Dataset[],
      content: [] as DatasetContentSearchResult[],
    };
  }
  return {
    datasets: payload.datasets ?? [],
    content: payload.content ?? [],
  };
}
