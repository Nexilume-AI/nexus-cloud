import type { ReactNode } from "react";
import {
  Activity,
  Bot,
  Boxes,
  Database,
  Inbox,
  LayoutDashboard,
  Server,
  Settings,
  Smartphone,
  Terminal,
  Router as RouterIcon,
  Waypoints,
  type LucideIcon
} from "lucide-react";

export type NavigationDomainId = "build" | "operate" | "discover" | "govern";
/** Custom style tokens belong to the build-selected navigation definition. */
export type NavigationLaneVariant = "standard" | "resources" | "capability" | (string & {});

export type NavigationItem = {
  to: string;
  label: string;
  description: string;
  icon: LucideIcon;
  superuserOnly?: boolean;
  opensInWindow?: boolean;
  windowName?: string;
  step?: string;
  verb?: string;
  /** Optional build-owned decoration. Routing and accessibility remain shared. */
  decoration?: ReactNode;
};

export type NavigationLane = {
  id: string;
  label?: string;
  variant: NavigationLaneVariant;
  items: NavigationItem[];
};

export type NavigationDomain = {
  id: NavigationDomainId;
  label: string;
  verb: string;
  description: string;
  isPublic?: boolean;
  lanes: NavigationLane[];
};

export const homeItem: NavigationItem = {
  to: "/",
  label: "Overview",
  description: "AI capability network, health, spend, and next actions",
  icon: LayoutDashboard
};

export const coreNavigationDomains: NavigationDomain[] = [
  {
    id: "build",
    label: "Build",
    verb: "Compose",
    description: "Create governed agents, data assets, and model capability",
    lanes: [
      {
        id: "resources",
        label: "AI Resources",
        variant: "resources",
        items: [
          { to: "/agents", label: "Agents", description: "Build and publish agents", icon: Bot },
          { to: "/data-assets", label: "Data Assets", description: "Collections and deliverables", icon: Database }
        ]
      },
      {
        id: "model-fabric",
        label: "Model Fabric",
        variant: "capability",
        items: [
          { to: "/providers", label: "Providers", description: "Credentials and runtimes", icon: Server, step: "01", verb: "Supply" },
          { to: "/model-pool", label: "Model Pool", description: "Published model capacity", icon: Boxes, step: "02", verb: "Compose" },
          { to: "/routers", label: "Routers", description: "Traffic policy and fallback", icon: Waypoints, step: "03", verb: "Route" }
        ]
      }
    ]
  },
  {
    id: "operate",
    label: "Operate",
    verb: "Run",
    description: "Operate computers, edge routers, mobile devices, and telemetry",
    lanes: [
      {
        id: "surfaces",
        variant: "standard",
        items: [
          { to: "/remote-workspaces", label: "Computer", description: "Connect and operate remote computers", icon: Terminal },
          { to: "/inbox", label: "Inbox", description: "Personal work and shared role queues", icon: Inbox },
          { to: "/openwrt-routers", label: "OpenWrt Routers", description: "Register and manage private edge Routers", icon: RouterIcon },
          { to: "/mobile", label: "Mobile", description: "Pair and operate mobile devices", icon: Smartphone },
          { to: "/observability", label: "Observability", description: "Reliability and audit", icon: Activity }
        ]
      }
    ]
  }
];

export function itemMatchesPath(item: NavigationItem, pathname: string) {
  return pathname === item.to || (item.to !== "/" && pathname.startsWith(`${item.to}/`));
}

export function domainForPath(pathname: string, domains: NavigationDomain[]) {
  return domains.find((domain) => domain.lanes.some((lane) => lane.items.some((item) => itemMatchesPath(item, pathname))));
}

export function itemsForDomain(domain: NavigationDomain) {
  return domain.lanes.flatMap((lane) => lane.items);
}

export function visibleNavigationDomains(isAuthenticated: boolean, isSuperuser: boolean, domains: NavigationDomain[]) {
  return domains
    .filter((domain) => isAuthenticated || domain.isPublic)
    .map((domain) => ({
      ...domain,
      lanes: domain.lanes
        .map((lane) => ({
          ...lane,
          items: lane.items.filter((item) => !item.superuserOnly || isSuperuser)
        }))
        .filter((lane) => lane.items.length > 0)
    }));
}
