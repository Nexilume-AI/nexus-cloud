export type MonitoringCapabilities = {
  read: boolean; manage: boolean; financial: boolean; reports: boolean; audit: boolean;
  platform_diagnostics?: boolean;
  scope: string; project_id: string;
  metrics: { metric: string; unit: string; kind: string; description: string; resource_types: string[] }[];
};
export type MonitoringHealth = { state: string; as_of: string; delivery_transport?: string; delivery_notice?: string; components: {
  component: string; state: string; last_success_at: string | null; error_code: string; max_age_seconds: number;
}[] };
export type MonitoringFreshness = {
  state: string; as_of?: string; last_updated_at?: string | null;
  affected_areas?: string[];
};

export function notificationsOnlyDegraded(health?: MonitoringFreshness): boolean {
  const areas = health?.affected_areas;
  // Unknown or additional impacts must not be mistaken for available issue data.
  return health?.state === "degraded" && Array.isArray(areas) && areas.length > 0 &&
    areas.every((area) => area === "notifications");
}
