import { Suspense } from "react";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./AuthContext";
import { AppShell } from "./AppShell";
import { AppErrorBoundary } from "../components/AppErrorBoundary";
import { AuthDialog } from "../components/AuthDialog";
import { PageLoader } from "./RouteAccess";
import { coreStandaloneRoutes, coreWorkspaceRoutes } from "./coreRoutes";
import {
  ApplicationDistributionContext, composeApplicationRoutes, useApplicationDistribution,
  validateApplicationDistribution, type ApplicationDistribution,
} from "./distribution";

function ApplicationRoutes() {
  const location = useLocation();
  const distribution = useApplicationDistribution();
  const routes = composeApplicationRoutes(coreWorkspaceRoutes, distribution.workspaceRoutes);
  const routeContent = (
    <AppErrorBoundary key={location.pathname}>
      <Suspense fallback={<PageLoader />}>
        <Routes>
          {routes.map((route) => <Route key={route.path} path={route.path} element={route.element} />)}
        </Routes>
      </Suspense>
    </AppErrorBoundary>
  );
  const frame = distribution.workspaceFrame?.(location.pathname, routeContent);
  return frame === undefined
    ? <AppShell navigationDomains={distribution.navigation}>{routeContent}</AppShell>
    : frame;
}

export function App({ distribution }: { distribution: ApplicationDistribution }) {
  validateApplicationDistribution(distribution);
  const auth = useAuth();
  const routes = composeApplicationRoutes(
    [...coreStandaloneRoutes, { path: "/*", element: <ApplicationRoutes /> }],
    distribution.standaloneRoutes,
  );
  return (
    <ApplicationDistributionContext.Provider value={distribution}>
      <BrowserRouter>
        <Suspense fallback={<PageLoader />}>
          <div aria-hidden={auth.isLoginOpen || undefined} inert={auth.isLoginOpen || undefined}>
            <Routes>
              {routes.map((route) => <Route key={route.path} path={route.path} element={route.element} />)}
            </Routes>
          </div>
          <AuthDialog />
        </Suspense>
      </BrowserRouter>
    </ApplicationDistributionContext.Provider>
  );
}
