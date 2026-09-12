import type { ApiContext } from "../lib/api";
import type { Tenant, Project } from "../lib/types";

/** Build-injected context discovery, not a grant of resource access.
 * Tenant/Project wire metadata remains shared while backend separation continues.
 */
export type ContextDirectory = {
  tenants: (context: ApiContext) => Promise<Tenant[]>;
  projects: (context: ApiContext) => Promise<Project[]>;
};

export function requireContextDirectory(directory: ContextDirectory | undefined): ContextDirectory {
  if (!directory || typeof directory.tenants !== "function" || typeof directory.projects !== "function") {
    throw new Error("Application context directory is not configured.");
  }
  return directory;
}
