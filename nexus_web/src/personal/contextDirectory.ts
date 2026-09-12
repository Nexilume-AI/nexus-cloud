import { request, type ApiContext } from "../lib/api";
import type { Tenant, Project } from "../lib/types";
import type { ContextDirectory } from "../app/contextDirectory";

type PersonalContext = { tenants: Tenant[]; projects: Project[] };
function directory(context: ApiContext) {
  return request<PersonalContext>("/api/v1/personal/context/", {}, context);
}
export const personalContextDirectory: ContextDirectory = {
  tenants: async (context) => (await directory(context)).tenants,
  projects: async (context) => (await directory(context)).projects,
};
