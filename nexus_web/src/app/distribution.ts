/** Build-selected application composition. Never select a distribution from
 * storage, a URL, API response or an environment fallback in the browser.
 * This boundary does not assert that every shared page is exportable yet.
 */
import { createContext, useContext, type ReactNode, type ComponentType } from "react";
import { requireContextDirectory, type ContextDirectory } from "./contextDirectory";
import type { NavigationDomain } from "./shell/navigation";
import type { ResourcePublishingExtension } from "./resourcePublishing";
import type { DataLibraryExtension } from "./dataLibrary";
import type { ResourceSharingExtension } from "./resourceSharing";
import type { OrganizationSettingsExtension } from "./organizationSettings";
import type { ApiContext } from "../lib/api";
import type { ResourceOwnershipExtension } from "./resourceOwnership";
import type { RunPresentationExtension } from "./runPresentation";
import { validateAgentDiscovery, type AgentDiscoveryExtension } from "./agentDiscovery";
import type { AgentDisplayNavigation } from '../lib/agentDisplayNavigation';
import { validateSourceDiscovery, type SourceDiscoveryExtension } from './sourceDiscovery';
import type { EdgeNode } from '../lib/types';
import { validateComputerPairing, type ComputerPairingPresentation } from './computerPairing';
import { validateAuthPresentation, type AuthPresentation } from './authPresentation';

export type ApplicationRoute = { path: string; element: ReactNode };
export type RouteInsertion = { before: string; routes: ApplicationRoute[] };
export type ContextSwitcherProps = { presentation?: "rail" | "account" };
export type ApplicationDistribution = {
  id: string;
  workspaceRoutes: RouteInsertion[];
  standaloneRoutes: RouteInsertion[];
  navigation: NavigationDomain[];
  guestOverview: ReactNode;
  workspaceFrame?: (pathname: string, content: ReactNode) => ReactNode | undefined;
  resourcePublishing?: ResourcePublishingExtension;
  dataLibrary?: DataLibraryExtension;
  resourceSharing?: ResourceSharingExtension;
  resourceOwnership?: ResourceOwnershipExtension;
  organizationSettings?: OrganizationSettingsExtension;
  contextDirectory?: ContextDirectory;
  contextSwitcher?: ComponentType<ContextSwitcherProps>;
  datasetImagePreview?: (context: ApiContext, path: string) => Promise<Blob>;
  runPresentation?: RunPresentationExtension;
  agentDiscovery?: AgentDiscoveryExtension;
  agentDisplayNavigation?: AgentDisplayNavigation;
  sourceDiscovery?: SourceDiscoveryExtension;
  computerPairing?: ComputerPairingPresentation;
  authPresentation?: AuthPresentation;
  edgeRouterPresentation?: {
    RegistrationDiagnostic: ComponentType<{ node: EdgeNode }>;
    statusLabel: (node: EdgeNode) => string;
  };
};

export const ApplicationDistributionContext = createContext<ApplicationDistribution | null>(null);

export function useApplicationDistribution() {
  const value = useContext(ApplicationDistributionContext);
  if (!value) throw new Error("Application distribution is not configured.");
  return value;
}

function routeKey(path: string) {
  if (typeof path !== "string" || !path || (path !== "*" && !path.startsWith("/"))
      || path.includes("#") || path.includes("//")
      || path.split("/").some((part) => part.includes("?") && !/^:[\w]+\?$/.test(part))) {
    throw new Error("Invalid application route.");
  }
  // React Router matches these case-insensitively and ignores trailing slashes.
  return path.replace(/\/+$/, "").replace(/:[^/?]+/g, ":param").toLowerCase();
}

export function composeApplicationRoutes(base: ApplicationRoute[], insertions: RouteInsertion[]) {
  const result = base.map((route) => ({ ...route }));
  const keys = new Set<string>();
  for (const route of result) {
    const key = routeKey(route.path);
    if (keys.has(key)) throw new Error("Duplicate application route.");
    keys.add(key);
  }
  for (const insertion of insertions) {
    const index = result.findIndex((route) => route.path === insertion.before);
    if (index < 0) throw new Error("Application route anchor is missing.");
    if (!insertion.routes.length) throw new Error("Empty application route insertion.");
    for (const route of insertion.routes) {
      const key = routeKey(route.path);
      if (keys.has(key)) throw new Error("Duplicate application route.");
      keys.add(key);
    }
    result.splice(index, 0, ...insertion.routes.map((route) => ({ ...route })));
  }
  return result;
}

export function validateApplicationDistribution(distribution: ApplicationDistribution) {
  if (!distribution?.id || !Array.isArray(distribution.navigation)
      || !Array.isArray(distribution.workspaceRoutes) || !Array.isArray(distribution.standaloneRoutes)
      || distribution.guestOverview === undefined) {
    throw new Error("Application distribution is incomplete.");
  }
  const domains = new Set<string>();
  if (distribution.authPresentation !== undefined) validateAuthPresentation(distribution.authPresentation);
  if (distribution.computerPairing !== undefined) validateComputerPairing(distribution.computerPairing);
  if (distribution.edgeRouterPresentation !== undefined &&
      (typeof distribution.edgeRouterPresentation?.RegistrationDiagnostic !== 'function'
       || typeof distribution.edgeRouterPresentation?.statusLabel !== 'function')) {
    throw new Error('Edge Router presentation extension is incomplete.');
  }
  if (distribution.agentDisplayNavigation !== undefined && typeof distribution.agentDisplayNavigation !== 'function') {
    throw new Error('Agent display navigation is incomplete.');
  }
  const datasetManagement = distribution.resourcePublishing?.datasetPolicy?.management;
  const releasedStage = distribution.resourcePublishing?.datasetPolicy?.releasedStage;
  if (releasedStage !== undefined && typeof releasedStage !== "function") {
    throw new Error("Dataset management extension is incomplete.");
  }
  if (datasetManagement && (typeof datasetManagement.settingsTitle !== "string" || !datasetManagement.settingsTitle.trim()
      || typeof datasetManagement.settingsDescription !== "string" || !datasetManagement.settingsDescription.trim()
      || typeof datasetManagement.deletionSummary !== "string" || !datasetManagement.deletionSummary.trim()
      || typeof datasetManagement.deletionBlockReason !== "function")) {
    throw new Error("Dataset management extension is incomplete.");
  }
  if (distribution.agentDiscovery) validateAgentDiscovery(distribution.agentDiscovery);
  if (distribution.sourceDiscovery) validateSourceDiscovery(distribution.sourceDiscovery);
  if (distribution.runPresentation && (!distribution.runPresentation.Summary
      || typeof distribution.runPresentation.queuedTurnDescription !== "function"
      || typeof distribution.runPresentation.followUpReason !== "function"
      || typeof distribution.runPresentation.historyRemovalDescription !== "string"
      || !distribution.runPresentation.historyRemovalDescription.trim())) {
    throw new Error("Run presentation extension is incomplete.");
  }
  if (distribution.resourceOwnership && (!distribution.resourceOwnership.Picker
      || !distribution.resourceOwnership.Badge || !distribution.resourceOwnership.AccessState
      || !["project", "private"].includes(distribution.resourceOwnership.newPoolVisibility))) {
    throw new Error("Resource ownership extension is incomplete.");
  }
  if (distribution.datasetImagePreview !== undefined && typeof distribution.datasetImagePreview !== "function") {
    throw new Error("Dataset image preview extension is incomplete.");
  }
  if (distribution.contextDirectory) requireContextDirectory(distribution.contextDirectory);
  if (distribution.organizationSettings && (!distribution.organizationSettings.Panel
      || !/^\/(?!\/)[^\s\\#]+$/.test(distribution.organizationSettings.legacyDeveloperPath))) {
    throw new Error("Organization settings extension is incomplete.");
  }
  if (distribution.resourceSharing && !distribution.resourceSharing.Dialog) {
    throw new Error("Resource sharing extension is incomplete.");
  }
  if (distribution.dataLibrary && (!distribution.dataLibrary.Workspace || !distribution.dataLibrary.Navigation
      || typeof distribution.dataLibrary.matchesLocation !== "function")) {
    throw new Error("Data library extension is incomplete.");
  }
  if (distribution.resourcePublishing && (!distribution.resourcePublishing.ListingEditor || !distribution.resourcePublishing.ProviderDialog
      || !distribution.resourcePublishing.ProviderSummary || !distribution.resourcePublishing.ProviderModelDialog
      || !distribution.resourcePublishing.AgentPanel || !distribution.resourcePublishing.AgentSettingsPanel
      || !distribution.resourcePublishing.AgentSummary || !distribution.resourcePublishing.DatasetSummary
      || !distribution.resourcePublishing.agentPolicy?.sectionLabel?.trim()
      || typeof distribution.resourcePublishing.agentPolicy?.recommend !== "function"
      || typeof distribution.resourcePublishing.agentPolicy?.status !== "function"
      || !distribution.resourcePublishing.DatasetScope || !distribution.resourcePublishing.DatasetPanel
      || typeof distribution.resourcePublishing.datasetPolicy?.recommend !== "function"
      || typeof distribution.resourcePublishing.providerPolicy?.canPublish !== "function"
      || typeof distribution.resourcePublishing.providerPolicy?.needsPublication !== "function")) {
    throw new Error("Resource publishing extension is incomplete.");
  }
  if (distribution.resourcePublishing?.agentRefreshKeys !== undefined
      && typeof distribution.resourcePublishing.agentRefreshKeys !== "function") {
    throw new Error("Agent refresh extension is incomplete.");
  }
  const destinations = new Set<string>();
  if (distribution.resourcePublishing?.managementCopy !== undefined) {
    const copy = distribution.resourcePublishing.managementCopy;
    if (!copy || ![copy.agentEventsEmpty, copy.providerDeletion, copy.providerDeletionWarning].every(text => typeof text === 'string' && text.trim())
        || (copy.providerPublishedLabel !== undefined && (typeof copy.providerPublishedLabel !== 'string' || !copy.providerPublishedLabel.trim()))) {
      throw new Error("Resource management presentation is incomplete.");
    }
  }
  for (const domain of distribution.navigation) {
    if (domains.has(domain.id)) throw new Error("Duplicate navigation domain.");
    domains.add(domain.id);
    for (const lane of domain.lanes) {
      for (const item of lane.items) {
        const key = routeKey(item.to);
        if (destinations.has(key)) throw new Error("Duplicate navigation destination.");
        destinations.add(key);
      }
    }
  }
  return distribution;
}
