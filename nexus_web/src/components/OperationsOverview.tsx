import { t, useLocale } from "../localization";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AlertTriangle, ArrowRight, Bell, CheckCircle2, Clock3, Copy, Search, Send } from "lucide-react";
import { toast } from "sonner";
import { StatusBadge } from "./Badge";
import { NexilumeDialog } from "./NexilumeControls";
import { formatDate, formatMoney, formatNumber } from "../lib/format";
import { notificationsOnlyDegraded, type MonitoringFreshness } from "../lib/monitoring";
import type { ObservabilityIssue } from "../pages/ObservabilityPage";

const kindLabels = { alert: "Alert", job: "Job", delivery: "Delivery" };
const actionLabels = { jobs: "Inspect failed job", alerts: "Inspect alert", reports: "Inspect report delivery" };

export function OperationsOverview({ issues, firingAlerts, failedJobs, failedDeliveries, incomplete, unavailable, loading, alertCount,
  metrics, onReview, onCreateAlert, onActivity, onRetry, canManage,
}: {
  issues: ObservabilityIssue[]; firingAlerts: number; failedJobs: number; failedDeliveries: number;
  incomplete: boolean; unavailable: boolean; loading: boolean; alertCount: number; metrics: Record<string, unknown>;
  onReview: (issue: ObservabilityIssue) => void; onCreateAlert: () => void;
  onActivity: () => void; onRetry: () => void; canManage: boolean;
}) {
  useLocale();
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [detailOpen, setDetailOpen] = useState(false);
  const [wide, setWide] = useState(() => window.matchMedia("(min-width: 1100px)").matches);
  const closeDetail = useCallback(() => setDetailOpen(false), []);
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const media = window.matchMedia("(min-width: 1100px)");
    const update = () => setWide(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const visible = issues.filter((issue) => (!kind || issue.kind === kind) &&
    `${issue.title} ${issue.detail}`.toLowerCase().includes(query.trim().toLowerCase()));
  const selectedId = params.get("issue");
  const selected = selectedId ? visible.find((issue) => issue.id === selectedId) : visible[0];
  const countKnown = metrics.summary !== undefined && !loading;
  const total = firingAlerts + failedJobs + failedDeliveries;
  const notificationOnly = !unavailable && notificationsOnlyDegraded(metrics.monitoring as MonitoringFreshness | undefined);
  const issueEvidenceIncomplete = unavailable || (incomplete && !notificationOnly);
  const state = loading ? "Checking" : issueEvidenceIncomplete ? "Status incomplete" : total > 0 ? "Needs attention" : notificationOnly ? t("Notifications limited") : t("All clear");
  const emptyTitle = unavailable ? t("Issue status unavailable") : query || kind ? t("No matching recent issues") : total > 0 ? t("More issues outside this preview") : issueEvidenceIncomplete ? t("No issues in the latest data") : t("No active issues");
  const emptyDescription = unavailable ? t("Retry to establish the current state.") : query || kind ? t("Clear this filter or search the full history in Activity and Automations.") : total > 0 ? t("Open Activity or Automations for the full history.") : issueEvidenceIncomplete ? t("Monitoring updates are delayed or unavailable. An empty result does not confirm that your operations are healthy.") : t("No unresolved issue was found in the current scope.");
  function select(issue: ObservabilityIssue) {
    const next = new URLSearchParams(params);
    next.set("issue", issue.id);
    setParams(next, { replace: true, preventScrollReset: true });
    if (!wide) setDetailOpen(true);
  }
  function resetSelection() {
    const next = new URLSearchParams(params);
    next.delete("issue");
    setParams(next, { replace: true, preventScrollReset: true });
    setDetailOpen(false);
    if (listRef.current) listRef.current.scrollTop = 0;
  }
  const detail = selected ? <IssueEvidence issue={selected} onReview={onReview} /> :
    <div className="ops-empty"><h3>{selectedId ? t("Issue no longer in this preview") : t("Select an issue")}</h3>
      <p>{selectedId ? t("It may have resolved or moved outside the recent results.") : t("Its evidence and next action will appear here.")}</p>
      {selectedId && <button className="btn" onClick={resetSelection}>{t("Back to available issues")}</button>}</div>;
  return <div className="ops-overview">
    <section className="ops-health" aria-label={t("Current operational status")}>
      <div className={`ops-business-state ${loading || issueEvidenceIncomplete ? "is-incomplete" : total > 0 || notificationOnly ? "has-issues" : "is-clear"}`}>
        {loading || incomplete || total > 0 ? <AlertTriangle size={20} aria-hidden="true" /> : <CheckCircle2 size={20} aria-hidden="true" />}
        <div><span>{t("Operations")}</span><strong>{state}</strong></div>
      </div>
      <div className="ops-counts">
        {[[t("Firing alerts"), firingAlerts], [t("Failed jobs"), failedJobs], [t("Failed deliveries"), failedDeliveries]].map(([label, value]) =>
          <div key={label}><span>{label}</span><strong>{countKnown ? value : "—"}</strong></div>)}
      </div>
    </section>
    <section className="ops-queue" aria-labelledby="ops-queue-title">
      <header className="ops-queue-header"><div><h2 id="ops-queue-title">{t("Needs attention")}</h2>
        <span>{loading ? t("Loading recent evidence…") : t("{{0}} in this preview{{1}}", { 0: visible.length, 1: countKnown ? ` · ${total} in scope` : "" })}</span></div>
        <button className="btn obs-quiet-action" onClick={onActivity}>{t("View activity")}{" "}<ArrowRight size={16} /></button>
      </header>
      <div className="ops-queue-tools">
        <label className="obs-search"><Search size={16} aria-hidden="true" /><span className="sr-only">{t("Search recent issues")}</span>
          <input className="input" value={query} placeholder={t("Search recent issues")} onChange={(event) => { setQuery(event.target.value); resetSelection(); }} /></label>
        <select className="select" aria-label={t("Filter recent issues")} value={kind} onChange={(event) => { setKind(event.target.value); resetSelection(); }}>
          <option value="">{t("All issues")}</option><option value="alert">{t("Alerts")}</option><option value="job">{t("Jobs")}</option><option value="delivery">{t("Deliveries")}</option>
        </select>
      </div>
      <div className="ops-issue-workspace">
        <div className="ops-issue-list" ref={listRef} aria-label={t("Recent issues")}>
          {loading ? <div className="obs-loading-rows" aria-label={t("Loading issues")}>{[0, 1, 2].map((i) => <span key={i} />)}</div> : visible.length ? visible.map((issue) => {
            const Icon = issue.kind === "alert" ? Bell : issue.kind === "job" ? Clock3 : Send;
            return <button type="button" key={issue.id} className={`ops-issue ${selected?.id === issue.id ? "is-selected" : ""}`}
              aria-pressed={selected?.id === issue.id} onClick={() => select(issue)}>
              <Icon size={18} aria-hidden="true" /><span className="ops-issue-copy"><strong>{issue.title}</strong><small>{issue.detail}</small>
                <span className="ops-issue-meta">{t(kindLabels[issue.kind])} · <time dateTime={issue.timestamp}>{formatDate(issue.timestamp)}</time></span></span>
              <span className="ops-issue-state"><StatusBadge status={issue.status} /><ArrowRight size={16} aria-hidden="true" /></span>
            </button>;
          }) : <div className="ops-empty"><h3>{emptyTitle}</h3>
            <p>{emptyDescription}</p>
            {unavailable ? <button className="btn" onClick={onRetry}>{t("Retry issue data")}</button> :
              query || kind ? <button className="btn" onClick={() => { setQuery(""); setKind(""); resetSelection(); }}>{t("Clear filters")}</button> :
              !issueEvidenceIncomplete && !loading && alertCount === 0 && canManage ? <button className="btn btn-primary" onClick={onCreateAlert}>{t("Create first alert")}</button> : null}
          </div>}
        </div>
        {wide && !loading && visible.length > 0 && <aside className="ops-evidence" aria-label={t("Selected issue inspector")}>{detail}</aside>}
      </div>
      <p className="ops-preview-note">{t("Recent preview only. Search full histories in Activity and Automations.")}</p>
    </section>
    <RequestQuality metrics={metrics} />
    <NexilumeDialog open={!wide && detailOpen} onClose={closeDetail} title={t("Issue details")} variant="drawer"
      footer={<button className="btn" onClick={closeDetail}>{t("Back to issues")}</button>}>
      <div className="ops-evidence">{detail}</div>
    </NexilumeDialog>
  </div>;
}

function IssueEvidence({ issue, onReview }: { issue: ObservabilityIssue; onReview: (issue: ObservabilityIssue) => void }) {
  useLocale();
  return <div className="ops-evidence-content">
    <header><span>{t(kindLabels[issue.kind])}{" "}{t("evidence")}</span><StatusBadge status={issue.status} /></header>
    <h3>{issue.title}</h3><p className="ops-evidence-reason">{issue.detail}</p>
    <dl><div><dt>{t("Recorded")}</dt><dd><time dateTime={issue.timestamp}>{formatDate(issue.timestamp)}</time></dd></div>
      <div><dt>{t("Evidence source")}</dt><dd>{issue.targetMode === "jobs" ? t("Job record") : issue.targetMode === "alerts" ? t("Alert event and rule") : t("Report delivery")}</dd></div></dl>
    <div className="ops-next-action"><h4>{t("Next action")}</h4><p>{issue.targetMode === "jobs" ? t("Review the failure and job events before retrying the operation.") : issue.targetMode === "alerts" ? t("Review the observed value, rule and notification history.") : t("Review the delivery failure and retry availability.")}</p>
      <button className="btn btn-primary" onClick={() => onReview(issue)}>{t(actionLabels[issue.targetMode])}<ArrowRight size={16} /></button></div>
    <details className="obs-technical-details"><summary>{t("Technical details")}</summary><div>
      <span>{t("Related record ID")}</span><code>{issue.targetId}</code><button className="btn" onClick={async () => {
        try { await navigator.clipboard.writeText(issue.targetId); toast.success(t("Record ID copied")); } catch { toast.error(t("Could not copy. Select the full ID above.")); }
      }}><Copy size={15} />{t("Copy record ID")}</button>
    </div></details>
  </div>;
}

function RequestQuality({ metrics }: { metrics: Record<string, unknown> }) {
  useLocale();
  const window = metrics.window as Record<string, unknown> | undefined;
  const usage = window?.usage as Record<string, unknown> | undefined;
  const quality = window?.quality as Record<string, unknown> | undefined;
  const surfaces = window?.surfaces as Record<string, Record<string, unknown>> | undefined;
  const number = (value: unknown) => value === undefined || value === null ? "No samples" : formatNumber(Number(value));
  const latency = (value: unknown) => value === undefined || value === null ? "No samples" : `${formatNumber(Math.round(Number(value)))} ms`;
  return <section className="ops-quality" aria-labelledby="ops-quality-title">
    <header><h2 id="ops-quality-title">{t("Request quality")}</h2><span>{t("Selected time window")}</span></header>
    <dl className="ops-quality-strip">
      <div><dt>{t("Requests")}</dt><dd>{number(usage?.requests)}</dd></div>
      <div><dt>{t("Error rate")}</dt><dd>{quality?.error_rate === undefined || quality.error_rate === null ? t("No samples") : `${Number(quality.error_rate).toFixed(2)}%`}</dd></div>
      <div><dt>{t("Gateway P95")}</dt><dd>{latency(surfaces?.gateway?.p95_latency_ms)}</dd></div>
      <div><dt>{t("Agent P95")}</dt><dd>{latency(surfaces?.agent?.p95_latency_ms)}</dd></div>
    </dl>
    <details className="ops-quality-details"><summary>{t("Usage and measurement details")}</summary><dl className="monitoring-grid">
      <div><dt>{t("Tokens")}</dt><dd>{number(usage?.tokens)}</dd></div>
      <div><dt>{t("Observed call cost")}</dt><dd>{!window ? t("Unavailable") : usage?.amount === undefined ? t("Restricted") : formatMoney(String(usage.amount), "USD")}</dd></div>
      <div><dt>{t("Gateway P99")}</dt><dd>{latency(surfaces?.gateway?.p99_latency_ms)}</dd></div>
      <div><dt>{t("Agent P99")}</dt><dd>{latency(surfaces?.agent?.p99_latency_ms)}</dd></div>
      <div><dt>{t("Gateway fallbacks")}</dt><dd>{number(quality?.fallbacks)}</dd></div>
    </dl><p>{t("Retained request records only. Gateway and Agent calls are separate surfaces; nested calls may contribute to both. Latency is end-to-end, not time to first token.")}</p></details>
  </section>;
}
