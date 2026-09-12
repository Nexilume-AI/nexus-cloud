import { Suspense, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { useApplicationDistribution } from "../app/distribution";
import type { DataLibraryNavigationProps } from "../app/dataLibrary";

export function DataAssetsWorkspace({ collections }: { collections: ReactNode }) {
  const extension = useApplicationDistribution().dataLibrary;
  const { search } = useLocation();
  const Workspace = extension?.Workspace;
  return Workspace && extension.matchesLocation(search)
    ? <Suspense fallback={<p role="status">Loading Data Assets…</p>}><Workspace /></Suspense>
    : <>{collections}</>;
}

export function DataLibraryNavigation(props: DataLibraryNavigationProps) {
  const Navigation = useApplicationDistribution().dataLibrary?.Navigation;
  return Navigation ? <Navigation {...props} /> : null;
}
