import { lazy, useEffect } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "./AuthContext";
import { useApplicationDistribution, type ApplicationRoute } from "./distribution";
import { PageLoader, ProtectedRoute } from "./RouteAccess";
import { RemoteWorkspacePreview } from "../components/RemoteWorkspacePreview";

const AgentControlPage = lazy(() => import("../pages/AgentControlPage").then((module) => ({ default: module.AgentControlPage })));
const AgentsPage = lazy(() => import("../pages/AgentsPage").then((module) => ({ default: module.AgentsPage })));
const DataAssetsPage = lazy(() => import("../pages/DataAssetsPage").then((module) => ({ default: module.DataAssetsPage })));
const ModelPoolPage = lazy(() => import("../pages/DeploymentsPage").then((module) => ({ default: module.ModelPoolPage })));
const ObservabilityPage = lazy(() => import("../pages/ObservabilityPage").then((module) => ({ default: module.ObservabilityPage })));
const InboxPage = lazy(() => import("../pages/InboxPage").then((module) => ({ default: module.InboxPage })));
const OverviewPage = lazy(() => import("../pages/OverviewPage").then((module) => ({ default: module.OverviewPage })));
const PlaygroundPage = lazy(() => import("../pages/PlaygroundPage").then((module) => ({ default: module.PlaygroundPage })));
const PrivateAgentRunDisplayPage = lazy(() => import("../pages/PrivateAgentRunDisplayPage").then((module) => ({ default: module.PrivateAgentRunDisplayPage })));
const MobileDevicesPage = lazy(() => import("../pages/MobileDevicesPage").then((module) => ({ default: module.MobileDevicesPage })));
const OpenWrtRoutersPage = lazy(() => import("../pages/OpenWrtRoutersPage").then((module) => ({ default: module.OpenWrtRoutersPage })));
const ProvidersPage = lazy(() => import("../pages/ProvidersPage").then((module) => ({ default: module.ProvidersPage })));
const RoutersPage = lazy(() => import("../pages/RoutersPage").then((module) => ({ default: module.RoutersPage })));
const SettingsPage = lazy(() => import("../pages/SettingsPage").then((module) => ({ default: module.SettingsPage })));

function OverviewRoute() {
  const auth = useAuth();
  const distribution = useApplicationDistribution();
  if (auth.status === "checking") return <PageLoader />;
  return auth.isAuthenticated ? <OverviewPage /> : distribution.guestOverview;
}

function LoginEntryRoute() {
  const auth = useAuth();
  useEffect(() => {
    if (auth.status === "anonymous") auth.requestLogin("Sign in to open your Nexilume AI workspace.");
  }, [auth.requestLogin, auth.status]);
  return <OverviewRoute />;
}

export const coreWorkspaceRoutes: ApplicationRoute[] = [
  { path: "/", element: <OverviewRoute /> },
  { path: "/login", element: <LoginEntryRoute /> },
  { path: "/gateway", element: <Navigate to="/remote-workspaces" replace /> },
  { path: "/playground", element: <Navigate to="/remote-workspaces" replace /> },
  { path: "/model-pool", element: <ProtectedRoute reason="Sign in to view published model capacity, deployments, and service health."><ModelPoolPage /></ProtectedRoute> },
  { path: "/models", element: <Navigate to="/model-pool" replace /> },
  { path: "/deployments", element: <Navigate to="/model-pool" replace /> },
  { path: "/routers", element: <ProtectedRoute reason="Sign in to view and manage workspace routing policies."><RoutersPage /></ProtectedRoute> },
  { path: "/agents/:agentId/:section?", element: <ProtectedRoute reason="Sign in to control this Agent runtime, access, observability, and publication."><AgentControlPage /></ProtectedRoute> },
  { path: "/agents", element: <ProtectedRoute reason="Sign in to view your agents, deployments, logs, and publishing settings."><AgentsPage /></ProtectedRoute> },
  { path: "/data-assets", element: <ProtectedRoute reason="Sign in to access workspace collections, files, and releases."><DataAssetsPage /></ProtectedRoute> },
  { path: "/remote-workspaces", element: <ProtectedRoute
                reason="Sign in to open Computer and project-scoped execution sessions."
                anonymousFallback={<RemoteWorkspacePreview />}
              >
                <PlaygroundPage />
              </ProtectedRoute> },
  { path: "/mobile", element: <ProtectedRoute reason="Sign in to view and manage paired execution devices."><MobileDevicesPage /></ProtectedRoute> },
  { path: "/openwrt-routers", element: <ProtectedRoute reason="Sign in to register and manage your private OpenWrt Routers."><OpenWrtRoutersPage /></ProtectedRoute> },
  { path: "/providers", element: <ProtectedRoute reason="Sign in to access provider accounts, credentials, and runtime capacity."><ProvidersPage /></ProtectedRoute> },
  { path: "/observability", element: <ProtectedRoute reason="Sign in to view private metrics, alerts, jobs, and audit records."><ObservabilityPage /></ProtectedRoute> },
  { path: "/inbox", element: <ProtectedRoute reason="Sign in to open your personal Work Inbox and shared role queues."><InboxPage /></ProtectedRoute> },
  { path: "/audit", element: <Navigate to="/observability" replace /> },
  { path: "/settings", element: <ProtectedRoute reason="Sign in to manage your profile, Organization, Projects, and account security."><SettingsPage /></ProtectedRoute> },
  { path: "*", element: <Navigate to="/" replace /> },
];

export const coreStandaloneRoutes: ApplicationRoute[] = [
  { path: "/agents/:agentId/private-display", element: <ProtectedRoute reason="Sign in to open this Agent's Private Display."><PrivateAgentRunDisplayPage /></ProtectedRoute> },
  { path: "/agent-runs/:runId/display", element: <ProtectedRoute reason="Sign in as the caller who owns this Agent Run."><PrivateAgentRunDisplayPage /></ProtectedRoute> },
];
