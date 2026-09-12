import { Suspense } from "react";
import type { ResourceManagementCopy } from "../app/resourcePublishing";
import type { AgentPublicationSummaryProps, DatasetPublicationSummaryProps } from "../app/resourcePublishing";
import { useApplicationDistribution } from "../app/distribution";
import type { ProviderPublicationSummaryProps, AgentSettingsPublicationProps, DatasetPublicationPanelProps, DatasetPublicationScopeProps, AgentPublicationPanelProps, ProviderModelSettingsDialogProps, ProviderPublicationDialogProps, ResourceListingEditorProps } from "../app/resourcePublishing";

const personalManagementCopy: ResourceManagementCopy = {
  agentEventsEmpty: "Container, deployment, and access events will appear here.",
  providerDeletion: "This removes the connection and its active Nexus configuration. Usage and audit history are preserved.",
  providerDeletionWarning: "Nexus will stop this Provider, remove generated Sources, and erase stored credentials.",
};

/** Presentation only: never changes authorization, API requests or deletion behavior. */
export function useResourceManagementCopy(): ResourceManagementCopy {
  return useApplicationDistribution().resourcePublishing?.managementCopy ?? personalManagementCopy;
}

/** Missing extensions mount no component, query or mutation. Authorization stays server-side. */
export function ResourceListingEditor(props: ResourceListingEditorProps) {
  const Editor = useApplicationDistribution().resourcePublishing?.ListingEditor;
  return Editor ? <Suspense fallback={<p role="status">Loading listing editor…</p>}><Editor {...props} /></Suspense> : null;
}

export function ProviderPublicationSummary(props: ProviderPublicationSummaryProps) {
  const Summary = useApplicationDistribution().resourcePublishing?.ProviderSummary;
  return Summary ? <Summary {...props} /> : null;
}

export function AgentPublicationSummary(props: AgentPublicationSummaryProps) {
  const Summary = useApplicationDistribution().resourcePublishing?.AgentSummary;
  return Summary ? <Summary {...props} /> : null;
}

export function DatasetPublicationSummary(props: DatasetPublicationSummaryProps) {
  const Summary = useApplicationDistribution().resourcePublishing?.DatasetSummary;
  return Summary ? <Summary {...props} /> : null;
}

export function AgentSettingsPublication(props: AgentSettingsPublicationProps) {
  const Panel = useApplicationDistribution().resourcePublishing?.AgentSettingsPanel;
  return Panel ? <Suspense fallback={<p role="status">Loading publishing tools…</p>}><Panel {...props} /></Suspense> : null;
}

export function ProviderPublicationDialog(props: ProviderPublicationDialogProps) {
  const Dialog = useApplicationDistribution().resourcePublishing?.ProviderDialog;
  return Dialog && props.provider ? <Suspense fallback={<p role="status">Loading publishing tools…</p>}><Dialog {...props} /></Suspense> : null;
}

export function ProviderModelSettingsDialog(props: ProviderModelSettingsDialogProps) {
  const Dialog = useApplicationDistribution().resourcePublishing?.ProviderModelDialog;
  return Dialog && props.offer ? <Suspense fallback={<p role="status">Loading model settings…</p>}><Dialog {...props} /></Suspense> : null;
}

export function AgentPublicationWorkspace(props: AgentPublicationPanelProps) {
  const Panel = useApplicationDistribution().resourcePublishing?.AgentPanel;
  const testing = <section className="agent-control-section" aria-label="Test Agent">
    <div className="agent-control-section__header"><div>
      <h2>Test your Agent</h2>
      <p>Attach your devices and open Private Display to test this Agent.</p>
    </div></div>
    <div className="agent-action-cluster">{props.testActions}</div>
  </section>;
  return Panel ? <Suspense fallback={<><p role="status">Loading publishing tools…</p>{testing}</>}><Panel {...props} /></Suspense> : testing;
}

/** One optional scope owns drafts across the workspace and settings surfaces. */
export function DatasetPublicationScope(props: DatasetPublicationScopeProps) {
  const Scope = useApplicationDistribution().resourcePublishing?.DatasetScope;
  return Scope ? <Scope {...props} /> : <>{props.children}</>;
}

export function DatasetPublicationPanel(props: DatasetPublicationPanelProps) {
  const Panel = useApplicationDistribution().resourcePublishing?.DatasetPanel;
  return Panel ? <Panel {...props} /> : <>{props.capacityControl}</>;
}
