/** Optional, build-selected publisher UI contracts. No commercial implementation. */
import type { ComponentType, ReactNode } from "react";
import type { ApiContext } from "../lib/api";
import type { AgentRefreshKeys } from "../lib/agentQueryRefresh";
import type { Agent, Dataset, ProviderConnection, ProviderRuntimeModelOffer, TopologyModelOffer, TopologyRuntime } from "../lib/types";

export type ResourceListingEditorProps = {
  kind: "agents" | "data-assets" | "provider-runtimes";
  resourceId: string;
  resourceName: string;
  onSaved?: () => void | Promise<void>;
  onDirtyChange?: (dirty: boolean) => void;
};

export type ProviderPublicationDialogProps = {
  provider: ProviderConnection | null;
  onClose: () => void;
};

export type ResourcePublishingExtension = {
  managementCopy?: ResourceManagementCopy;
  ListingEditor: ComponentType<ResourceListingEditorProps>;
  ProviderDialog: ComponentType<ProviderPublicationDialogProps>;
  ProviderSummary: ComponentType<ProviderPublicationSummaryProps>;
  ProviderModelDialog: ComponentType<ProviderModelSettingsDialogProps>;
  AgentPanel: ComponentType<AgentPublicationPanelProps>;
  AgentSummary: ComponentType<AgentPublicationSummaryProps>;
  agentPolicy: AgentPublicationPolicy;
  DatasetSummary: ComponentType<DatasetPublicationSummaryProps>;
  AgentSettingsPanel: ComponentType<AgentSettingsPublicationProps>;
  DatasetScope: ComponentType<DatasetPublicationScopeProps>;
  DatasetPanel: ComponentType<DatasetPublicationPanelProps>;
  datasetPolicy: DatasetPublicationPolicy;
  providerPolicy: ProviderPublicationPolicy;
  topologyPolicy?: TopologyPublicationPolicy;
  agentRefreshKeys?: AgentRefreshKeys;
};

export type ResourceManagementCopy = {
  agentEventsEmpty: string;
  providerDeletion: string;
  providerDeletionWarning: string;
  providerPublishedLabel?: string;
};

/** Extra inspector rows are selected by the build, never inferred from payloads. */
export type TopologyPublicationPolicy = {
  offerFields: (offer: TopologyModelOffer) => Array<[string, string]>;
  runtimeFields: (runtime: TopologyRuntime) => Array<[string, string]>;
};

/** Optional inventory decorations; lifecycle and file safety remain core-owned. */
export type AgentPublicationSummaryProps =
  | { surface: "page"; agents: Agent[] }
  | { surface: "badge" | "fact" | "control-overview"; agent: Agent }
  | { surface: "settings-shortcut"; agent: Agent; onOpen: () => void };
export type DatasetPublicationSummaryProps =
  | { surface?: "page"; publishedCount: number }
  | { surface: "inspector"; dataset: Dataset };

export type AgentPublicationPolicy = {
  sectionLabel: string;
  recommend: (agent: Agent) => { label: string; description: string; section: "publish" } | null;
  status: (agent: Agent) => { label: string; value: string; tone: "success" | "muted" };
};

/** Read-only provider decorations; absence never removes operational data. */
export type ProviderPublicationSummaryProps =
  | { surface: "page"; providers: ProviderConnection[] }
  | { surface: "row" | "facts" | "action"; provider: ProviderConnection }
  | { surface: "price" | "publication"; offer: ProviderRuntimeModelOffer }
  | { surface: "price-heading" | "publication-heading" };

/** Optional historical settings surface. Core still manages versions and lifecycle. */
export type AgentSettingsPublicationProps = Omit<AgentPublicationPanelProps, "testActions">;

export type DatasetPublicationScopeProps = {
  dataset: Dataset | undefined;
  apiContext: ApiContext;
  onSaved: () => Promise<void>;
  selectionRevision: number;
  releaseSizeBytes: number;
  children: ReactNode;
};

export type DatasetPublicationPanelProps = {
  variant: "workspace" | "settings";
  capacityControl: ReactNode;
  onShare: () => void;
};

export type DatasetRecommendedAction = { key: string; label: string; description: string; icon: ReactNode };
export type DatasetManagementPolicy = {
  settingsTitle: string;
  settingsDescription: string;
  deletionSummary: string;
  deletionBlockReason: (dataset: Dataset) => string | null;
};
export type DatasetPublicationPolicy = {
  recommend: (dataset: Dataset) => DatasetRecommendedAction;
  releasedStage?: (dataset: Dataset) => "release" | "distribute";
  management?: DatasetManagementPolicy;
};

/** Core owns caller device attachment and test navigation, not publication. */
export type AgentPublicationPanelProps = {
  agent: Agent;
  apiContext: ApiContext;
  onSaved: () => Promise<void>;
  testActions: ReactNode;
};

export type ProviderModelSettingsDialogProps = {
  provider: ProviderConnection;
  offer: ProviderRuntimeModelOffer | null;
  onClose: () => void;
};

/** The shared workspace consumes decisions; the distribution owns their rules. */
export type ProviderPublicationPolicy = {
  canPublish: (offer: ProviderRuntimeModelOffer) => boolean;
  needsPublication: (offers: ProviderRuntimeModelOffer[]) => boolean;
};
