import { useAuth } from "../app/AuthContext";
import { OverviewWorkspace } from "../components/OverviewWorkspace";
import "./overview.css";

export function OverviewPage() {
  const { apiContext } = useAuth();
  // Expanded state and selections must never carry across tenant/project boundaries.
  return (
    <OverviewWorkspace key={`${apiContext.tenantId}:${apiContext.projectId}`} />
  );
}
