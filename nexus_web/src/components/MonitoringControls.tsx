import { t, useLocale } from "../localization";
import { useEffect, useState } from "react";
import { AlertTriangle, Check, ChevronRight, Clock3 } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import { notificationsOnlyDegraded, type MonitoringFreshness } from "../lib/monitoring";
import type { useCursorPage } from "../lib/useCursorPage";
import { formatDate } from "../lib/format";
import "./monitoring.css";

export function MonitoringPager({ label, control, data, busy }: { label: string; control: ReturnType<typeof useCursorPage>; data?: { has_more: boolean; next_cursor: string | null }; busy: boolean }) {
  useLocale();
  return <nav className="monitoring-pager" aria-label={t("{{0}} pages", { 0: label })}>
    <span>{label}{" "}{t("· Page")}{" "}{control.page}{" "}{t("· up to 50 records")}</span>
    <button className="btn" disabled={busy || control.page === 1} onClick={control.previous}>{t("Previous")}</button>
    <button className="btn" disabled={busy || !data?.has_more} onClick={() => control.next(data?.next_cursor)}>{t("Next")}</button>
    <button className="btn" disabled={busy || control.page === 1} onClick={control.reset}>{t("Latest")}</button>
  </nav>;
}

export function MonitoringWindow({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  useLocale();
  return <label className="monitoring-window"><span className="sr-only">{t("Metric window")}</span>
    <select className="select" value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="3600">{t("Last hour")}</option><option value="86400">{t("Last 24 hours")}</option><option value="604800">{t("Last 7 days")}</option>
    </select>
  </label>;
}

const areaLabels: Record<string, string> = { metrics: "Request metrics", alerts: "Alert status", notifications: "Email notifications", reports: "Scheduled reports" };

export function MonitoringStatus({ metrics, loading = false, failed = false, onRetry }: {
  metrics: Record<string, unknown>; loading?: boolean; failed?: boolean; onRetry: () => void;
}) {
  useLocale();
  const [now, setNow] = useState(Date.now);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 30_000); return () => window.clearInterval(timer); }, []);
  // Even an older server response must never render its component names/errors here.
  const health = metrics.monitoring as MonitoringFreshness | undefined;
  const state = failed ? "unavailable" : health?.state ?? "unknown";
  const notificationOnly = !failed && notificationsOnlyDegraded(health);
  const stamp = health?.last_updated_at;
  const timestamp = stamp ? Date.parse(stamp) : NaN;
  const age = Number.isFinite(timestamp) ? Math.max(0, Math.floor((now - timestamp) / 1000)) : null;
  const updated = age === null ? t("Data up to date") : age < 60 ? t("Updated just now") : age < 3600 ? `Updated ${Math.floor(age / 60)} min ago` : age < 86400 ? `Updated ${Math.floor(age / 3600)} hr ago` : `Updated ${Math.floor(age / 86400)} days ago`;
  const affected = [...new Set((health?.affected_areas ?? []).filter((area) => area in areaLabels))];
  if (loading) return <span className="data-freshness is-checking" role="status"><Clock3 size={15} aria-hidden="true" />{t("Checking data…")}</span>;
  if (state === "healthy") return <span className="data-freshness is-current" role="status"><Check size={15} aria-hidden="true" />
    {age === null ? updated : <time dateTime={stamp!} title={formatDate(stamp!)}>{updated}</time>}</span>;
  const label = notificationOnly ? t("Email notifications limited") : state === "stale" ? t("Data delayed") : t("Some data unavailable");
  return <details className={`data-freshness is-notice is-${state}`} key={state} onKeyDown={(event) => {
    if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); }
  }}>
    <summary><AlertTriangle size={15} aria-hidden="true" /><span>{label}</span><ChevronRight size={14} className="data-freshness-chevron" aria-hidden="true" /></summary>
    <div className="data-freshness-panel">
      <strong>{notificationOnly ? t("Email delivery needs attention") : state === "stale" ? t("Updates are taking longer than usual") : t("We can’t verify all monitoring data")}</strong>
      <p>{notificationOnly ? t("Issue records remain available, but email notifications may not be delivered.") : state === "stale" ? t("Some values may not reflect recent activity.") : t("Available records remain visible. Missing data does not mean your operations are healthy.")}</p>
      {affected.length > 0 && <div className="data-freshness-affected"><span>{t("Affected")}</span><ul>{affected.map((area) => <li key={area}>{t(areaLabels[area])}</li>)}</ul></div>}
      {age !== null && <p className="data-freshness-time">{t("Last complete update")}{" "}<time dateTime={stamp!}>{formatDate(stamp!)}</time></p>}
      <button className="btn" onClick={onRetry}>{notificationOnly ? t("Check status") : t("Retry updates")}</button>
      <small>{notificationOnly ? t("Checking status does not configure email delivery. Contact your administrator if this continues.") : t("If this continues, contact support.")}</small>
    </div>
  </details>;
}

export function MonitoringActions({ kind, id, actions, disabled = false }: { kind: string; id: string; actions: { action: string; label: string }[]; disabled?: boolean }) {
  useLocale();
  const { apiContext } = useAuth();
  const client = useQueryClient();
  const mutation = useMutation({ mutationFn: (action: string) => api.monitoringAction(apiContext, kind, id, action),
    onSuccess: async () => { toast.success(t("Monitoring record updated")); await Promise.all(["alerts", "alert-events", "monitoring-rule", "report-deliveries", "system-metrics"].map((key) => client.invalidateQueries({ queryKey: [key] }))); },
    onError: (error: Error) => toast.error(error.message) });
  return <div className="monitoring-controls">{actions.map(({ action, label }) => <button key={action} className="btn" disabled={disabled || mutation.isPending} onClick={() => mutation.mutate(action)}>{label}</button>)}</div>;
}

type RequestRecord = { kind: string; id: string; status: string; latency_ms: number; router_id?: string; pool_id?: string; source_id?: string; agent_id?: string; run_id?: string };
export function RequestLookup() {
  useLocale();
  const { apiContext } = useAuth();
  const [value, setValue] = useState("");
  const [requestId, setRequestId] = useState("");
  const result = useQuery({ queryKey: ["monitoring-request", apiContext, requestId], queryFn: ({ signal }) => api.monitoringDetail<{ items: RequestRecord[] }>(apiContext, `metrics/requests/${encodeURIComponent(requestId)}`, signal), enabled: !!requestId, retry: false });
  return <details className="ops-request-lookup" aria-label={t("Request correlation")}>
    <summary>{t("Follow a request")}</summary><p>{t("Use the full request ID to locate its retained Router / Pool / Source or Agent Run records in this scope. Resource detail permissions still apply.")}</p>
    <form className="monitoring-controls" onSubmit={(event) => { event.preventDefault(); setRequestId(value.trim()); }}>
      <label>{t("Request ID")}{" "}<input className="input font-mono" value={value} maxLength={64} onChange={(event) => setValue(event.target.value)} required /></label>
      <button className="btn" disabled={result.isFetching}>{t("Find request")}</button>
    </form>
    {result.isError && <p role="alert">{t("No accessible retained request records, or the lookup failed. Check the scope and request ID.")}</p>}
    {!result.isError && result.data?.items.map((row) => <div key={`${row.kind}-${row.id}`} className="monitoring-request">
      <strong>{row.kind} · {row.status} · {row.latency_ms}{" "}{t("ms")}</strong>
      {row.kind === "gateway" ? <><p>{t("Pool:")}{" "}{row.pool_id || t("Historical route unavailable")}{" "}{t("· Source:")}{" "}{row.source_id || t("Unavailable")}</p>
        {row.router_id && <Link to={`/routers?router=${encodeURIComponent(row.router_id)}`}>{t("Open Router")}</Link>}</> : <>
        <p>{t("Run:")}{" "}{row.run_id || t("Historical Run unavailable")}</p><Link to={`/agents/${row.agent_id}/observability?run=${encodeURIComponent(row.run_id || "")}`}>{t("Open Agent observability")}</Link></>}
    </div>)}
  </details>;
}
