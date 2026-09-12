import { ShieldCheck } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import { formatDate } from "../lib/format";
import type { MonitoringHealth } from "../lib/monitoring";

const states: Record<string, string> = { healthy: "Current", stale: "Delayed", degraded: "Needs attention", unknown: "Not verified" };
const names: Record<string, string> = { collector: "Metric collection", evaluator: "Alert evaluation", delivery: "Notification delivery", reports: "Report scheduling" };

// Mounted only after the server capability gate. The endpoint independently
// authorizes the platform administrator and returns no-store scoped evidence.
export function PlatformMonitoring() {
  const { apiContext } = useAuth();
  const result = useQuery({ queryKey: ["platform-monitoring", apiContext],
    queryFn: ({ signal }) => api.monitoringDetail<MonitoringHealth>(apiContext, "metrics/diagnostics", signal),
    retry: false, refetchInterval: 30_000, gcTime: 0 });
  const health = result.isError ? undefined : result.data;
  return <section className="platform-monitoring" aria-labelledby="platform-monitoring-title">
    <header><div><span className="platform-monitoring-label"><ShieldCheck size={15} aria-hidden="true" />Platform administrators only</span>
      <h2 id="platform-monitoring-title">Monitoring diagnostics</h2>
      <p>Collection and delivery evidence for the selected organization, across all projects.</p></div>
      {health && <span className="platform-monitoring-state">{states[health.state] ?? "Not verified"}</span>}
    </header>
    {result.isPending ? <p role="status">Loading diagnostic evidence…</p> : result.isError ? <div className="platform-monitoring-empty" role="alert">
      <h3>Diagnostics unavailable</h3><p>Access may have changed, or the service is unavailable.</p>
      <button className="btn" onClick={() => void result.refetch()}>Retry diagnostics</button></div> : <>
      <div className="platform-monitoring-table" role="table" aria-label="Monitoring components">
        <div className="platform-monitoring-row is-heading" role="row"><span role="columnheader">Component</span><span role="columnheader">State</span><span role="columnheader">Last completed</span><span role="columnheader">Freshness budget</span></div>
        {(health?.components ?? []).map((row) => <div className="platform-monitoring-row" role="row" key={row.component}>
          <div role="cell"><strong>{names[row.component] ?? row.component}</strong><code>{row.component}</code></div>
          <div role="cell" className={`is-${row.state}`}><span className="platform-monitoring-cell-label">State</span>{states[row.state] ?? "Not verified"}</div>
          <div role="cell"><span className="platform-monitoring-cell-label">Last completed</span>{row.last_success_at ? <time dateTime={row.last_success_at}>{formatDate(row.last_success_at)}</time> : "Never completed"}</div>
          <div role="cell"><span className="platform-monitoring-cell-label">Freshness budget</span>{row.max_age_seconds} seconds</div>
          {row.error_code && <details className="platform-monitoring-error"><summary>Error classification</summary><code>{row.error_code}</code></details>}
        </div>)}
      </div>
      {!health?.components?.length && <p className="platform-monitoring-empty">No component evidence is available.</p>}
      <div className="platform-monitoring-notes"><strong>Email transport</strong><p>{health?.delivery_transport === "test_only" ? "Test transport only. External alert email is not sent." : "Transport acceptance does not confirm delivery to a recipient."}</p></div>
      <p className="platform-monitoring-footnote">{health?.as_of && <>Checked {formatDate(health.as_of)}. </>}Browser refresh is not evidence of successful collection. Worker and Beat process health is not inferred from these task heartbeats.</p>
    </>}
  </section>;
}
