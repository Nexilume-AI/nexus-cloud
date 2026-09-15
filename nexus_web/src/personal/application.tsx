/** Statically selected personal host. No commercial extensions or signup. */
import { Settings } from "lucide-react";
import { ProtectedRoute } from "../app/RouteAccess";
import { coreNavigationDomains } from "../app/shell/navigation";
import { validateApplicationDistribution } from "../app/distribution";
import { personalContextDirectory } from "./contextDirectory";

export const personalDistribution = validateApplicationDistribution({
  id: "community",
  contextDirectory: personalContextDirectory,
  navigation: [
    ...coreNavigationDomains,
    { id: "govern", label: "Settings", verb: "Personal", description: "Profile and account security",
      lanes: [{ id: "personal", variant: "standard", items: [
        { to: "/settings", label: "Settings", description: "Profile and account security", icon: Settings },
      ] }] },
  ],
  guestOverview: <ProtectedRoute reason={"Sign in with the owner account configured for this personal installation."}>{null}</ProtectedRoute>,
  workspaceRoutes: [],
  standaloneRoutes: [],
});
