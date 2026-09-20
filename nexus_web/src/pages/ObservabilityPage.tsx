import { t, tToken, useLocale } from "../localization";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  Bell,
  ChevronRight,
  Clock3,
  Copy,
  FileClock,
  Filter,
  Gauge,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Trash2,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "../app/AuthContext";
import { StatusBadge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Form";
import { NexilumeDialog, NexilumeTabs } from "../components/NexilumeControls";
import { api } from "../lib/api";
import { useCatalogSearch, useCursorPage } from "../lib/useCursorPage";
import { MonitoringStatus, MonitoringWindow, MonitoringPager, MonitoringActions, RequestLookup } from "../components/MonitoringControls";
import { OperationsOverview } from "../components/OperationsOverview";
import { PlatformMonitoring } from "../components/PlatformMonitoring";
import "../components/operations-desk.css";
import {
  compactId,
  formatDate,
  formatMoney,
} from "../lib/format";
import type {
  AlertEvent,
  AlertRule,
  AuditLog,
  Job,
  JobEvent,
  ReportDelivery,
  ReportSchedule,
} from "../lib/types";

export type ObservabilityView =
  "overview" | "activity" | "automations" | "audit" | "platform";
export type ObservabilityMode = "jobs" | "alerts" | "reports";
type ActivityMode = "jobs";
type AutomationMode = Extract<ObservabilityMode, "alerts" | "reports">;
type DialogKind = "alert" | "report" | "resource" | null;

export type ObservabilityIssue = {
  id: string;
  kind: "alert" | "job" | "delivery";
  title: string;
  detail: string;
  status: string;
  timestamp: string;
  targetId: string;
  targetMode: ObservabilityMode;
};


const metricLabels: Record<string, string> = {
  "system.wallet.balance": "Available balance",
  "system.usage.requests": "Requests",
  "system.usage.tokens": "Tokens",
  "system.usage.amount": "Spend",
  "system.counts.api_keys": "API keys",
  "system.counts.routers": "Routers",
  "system.counts.alerts": "Alert rules",
};
const operatorLabels: Record<string, string> = {
  gt: "is above",
  gte: "is at least",
  lt: "is below",
  lte: "is at most",
  eq: "equals",
};
const resourceLabels: Record<string, string> = {
  agent: "Agent",
  agent_runtime_deployment: "Agent runtime",
  router: "Router",
  model: "Model",
  model_group: "Model pool",
  dataset: "Dataset",
  api_key: "API key",
  provider_runtime: "Provider runtime",
};
const viewOptions = [
  { value: "overview" as const, label: "Overview" },
  { value: "activity" as const, label: "Activity" },
  { value: "automations" as const, label: "Automations" },
  { value: "audit" as const, label: "Audit" },
  { value: "platform" as const, label: "Platform" },
];

export function ObservabilityPage() {
  useLocale();
  const { apiContext, isContextReady, projectId } = useAuth();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [dialog, setDialog] = useState<DialogKind>(null);
  const closeDialog = useCallback(() => setDialog(null), []);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<{
    kind: "alert" | "report";
    id: string;
    label: string;
  } | null>(null);
  const persistentInspector = usePersistentInspector();

  const rawView = searchParams.get("view");
  const view: ObservabilityView = isObservabilityView(rawView)
    ? rawView
    : "overview";
  const rawMode = searchParams.get("mode");
  const activityMode: ActivityMode = "jobs";
  const automationMode: AutomationMode =
    rawMode === "reports" ? "reports" : "alerts";
  const [resource, setResource] = useState({ type: "router", id: "" });
  const [alertForm, setAlertForm] = useState({
    metric: "system.usage.requests",
    threshold: "100",
    operator: "gte",
    window_seconds: "300",
    cooldown_seconds: "300",
    resource_type: "",
    resource_id: "",
    email: "",
  });
  const [reportForm, setReportForm] = useState({
    email: "",
    interval: "daily",
  });
  const [jobSearch, setJobSearch] = useState("");
  const [jobFilters, setJobFilters] = useState({
    status: "",
    job_type: "",
    resource_type: "",
    resource_id: "",
  });
  const [auditQuery, setAuditQuery] = useState("");
  const [auditResourceType, setAuditResourceType] = useState("");

  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    let changed = false;
    if (!isObservabilityView(rawView)) {
      next.set("view", "overview");
      changed = true;
    }
    if (view === "activity" && rawMode !== "jobs") {
      next.set("mode", "jobs");
      changed = true;
    }
    if (
      view === "automations" &&
      rawMode !== "alerts" &&
      rawMode !== "reports"
    ) {
      next.set("mode", "alerts");
      changed = true;
    }
    if ((view === "overview" || view === "audit" || view === "platform") && next.has("mode")) {
      next.delete("mode");
      changed = true;
    }
    if (changed) setSearchParams(next, { replace: true });
  }, [rawMode, rawView, searchParams, setSearchParams, view]);

  const onOverview = view === "overview";
  const onJobs = view === "activity" && activityMode === "jobs";
  const onAlerts = view === "automations" && automationMode === "alerts";
  const onReports = view === "automations" && automationMode === "reports";
  const onAudit = view === "audit";

  const permissions = useQuery({ queryKey: ["monitoring-capabilities", apiContext],
    queryFn: () => api.monitoringCapabilities(apiContext), enabled: isContextReady });
  const canRead = permissions.data?.read === true && !permissions.isError;
  const canManage = permissions.data?.manage === true && !permissions.isError;
  const canFinance = permissions.data?.financial === true && !permissions.isError;
  const canReports = permissions.data?.reports === true && !permissions.isError;
  const canAudit = permissions.data?.audit === true && !permissions.isError;
  const canDiagnose = permissions.data?.platform_diagnostics === true && !permissions.isError;
  const visibleViews = viewOptions.filter((option) => (option.value !== "audit" || canAudit) && (option.value !== "platform" || canDiagnose));
  useEffect(() => {
    if (view === "platform" && !permissions.isPending && !canDiagnose) {
      setSearchParams({ view: "overview" }, { replace: true });
    }
  }, [view, permissions.isPending, canDiagnose, setSearchParams]);
  const [windowSeconds, setWindowSeconds] = useState("86400");
  const jobQuery = useCatalogSearch(jobSearch);
  const auditSearch = useCatalogSearch(auditQuery);
  const [ruleSearch, setRuleSearch] = useState("");
  const ruleQuery = useCatalogSearch(ruleSearch);
  const jobsPage = useCursorPage([apiContext, jobFilters, jobQuery]);
  const rulesPage = useCursorPage([apiContext, ruleQuery]);
  const schedulePage = useCursorPage(apiContext);
  const auditPage = useCursorPage([apiContext, auditSearch, auditResourceType]);

  // Each work surface loads only its authoritative API data. Disabled queries cannot keep an old
  // component visible after navigation, and Overview never substitutes stale card data for failures.
  const systemMetrics = useQuery({
    queryKey: ["system-metrics", apiContext, windowSeconds],
    queryFn: () => api.systemMetrics(apiContext, Number(windowSeconds)),
    enabled: isContextReady && canRead,

    refetchInterval: onOverview ? 30_000 : false,
  });
  const alerts = useQuery({
    queryKey: ["alerts", apiContext, onOverview ? "" : rulesPage.cursor, ruleQuery],
    queryFn: ({ signal }) => api.monitoringPage<AlertRule>(apiContext, "alerts", { cursor: onOverview ? "" : rulesPage.cursor, q: onOverview ? "" : ruleQuery }, signal),
    enabled: isContextReady && canRead && (onOverview || onAlerts),
  });
  const alertEvents = useQuery({
    queryKey: ["alert-events", apiContext],
    queryFn: ({ signal }) => api.monitoringPage<AlertEvent>(apiContext, "alerts/events", { status: "firing" }, signal),
    enabled: isContextReady && canRead && onOverview,
    refetchInterval: onOverview ? 30_000 : false,
  });
  const schedules = useQuery({
    queryKey: ["report-schedules", apiContext, schedulePage.cursor],
    queryFn: ({ signal }) => api.monitoringPage<ReportSchedule>(apiContext, "reports/schedules", { cursor: schedulePage.cursor }, signal),
    enabled: isContextReady && canFinance && onReports,
  });
  const deliveries = useQuery({
    queryKey: ["report-deliveries", apiContext],
    queryFn: ({ signal }) => api.monitoringPage<ReportDelivery>(apiContext, "reports/deliveries", { status: "failed" }, signal),
    enabled: isContextReady && canFinance && onOverview,
    refetchInterval: onOverview ? 30_000 : false,
  });
  const effectiveJobFilters = onJobs
    ? jobFilters
    : { status: "", job_type: "", resource_type: "", resource_id: "" };
  const jobs = useQuery({
    queryKey: ["jobs", apiContext, effectiveJobFilters, onJobs ? jobsPage.cursor : "", jobQuery, onOverview ? windowSeconds : ""],
    queryFn: ({ signal }) => api.monitoringPage<Job>(apiContext, "metrics/jobs", { ...effectiveJobFilters, cursor: onJobs ? jobsPage.cursor : "", q: onJobs ? jobQuery : "", window_seconds: onOverview ? windowSeconds : "" }, signal),
    enabled: isContextReady && canRead && (onOverview || onJobs),
    refetchInterval: onJobs ? 10_000 : onOverview ? 30_000 : false,
  });
  const resourceMetrics = useQuery({
    queryKey: ["resource-metrics", apiContext, resource.type, resource.id],
    queryFn: () => api.resourceMetrics(apiContext, resource.type, resource.id),
    enabled: Boolean(
      isContextReady &&
      dialog === "resource" &&
      resource.type &&
      resource.id.trim(),
    ),
  });
  const auditLogs = useQuery({
    queryKey: ["audit-logs", apiContext, auditPage.cursor, auditSearch, auditResourceType],
    queryFn: ({ signal }) => api.monitoringPage<AuditLog>(apiContext, "metrics/audit", { cursor: auditPage.cursor, q: auditSearch, resource_type: auditResourceType }, signal),
    enabled: isContextReady && canAudit && onAudit,
    refetchInterval: onAudit ? 30_000 : false,
  });

  // Do not render a cached payload after its authoritative request has failed.
  // This prevents an old work surface from being mistaken for current health.
  const validJobs = jobs.isError ? [] : (jobs.data?.items ?? []);
  const validAlerts = alerts.isError ? [] : (alerts.data?.items ?? []);
  const validAlertEvents = alertEvents.isError ? [] : (alertEvents.data?.items ?? []);
  const validSchedules = schedules.isError ? [] : (schedules.data?.items ?? []);
  const validDeliveries = deliveries.isError ? [] : (deliveries.data?.items ?? []);
  const validAuditLogs = auditLogs.isError ? [] : (auditLogs.data?.items ?? []);
  const validResourceMetrics = resourceMetrics.isError
    ? undefined
    : resourceMetrics.data;

  const filteredJobs = validJobs;
  const jobDetail = useQuery({ queryKey: ["monitoring-job", apiContext, searchParams.get("job")],
    queryFn: ({ signal }) => api.monitoringDetail<Job>(apiContext, `metrics/jobs/${encodeURIComponent(searchParams.get("job")!)}`, signal),
    enabled: isContextReady && canRead && onJobs && !!searchParams.get("job") });
  const selectedJob = useMemo(
    () =>
      searchParams.get("job") ? (jobDetail.isError ? null : jobDetail.data ?? filteredJobs.find((job) => job.id === searchParams.get("job"))) : chooseJob(filteredJobs),
    [filteredJobs, searchParams, jobDetail.data, jobDetail.isError],
  );
  const jobEventsPage = useCursorPage([apiContext, selectedJob?.id]);
  const jobEvents = useQuery({
    queryKey: ["job-events", apiContext, selectedJob?.id, jobEventsPage.cursor],
    queryFn: ({ signal }) => api.monitoringPage<JobEvent>(apiContext, `metrics/jobs/${selectedJob!.id}/events`, { cursor: jobEventsPage.cursor }, signal),
    enabled: Boolean(isContextReady && onJobs && selectedJob?.id),
    refetchInterval: onJobs ? 10_000 : false,
  });
  const ruleDetail = useQuery({ queryKey: ["monitoring-rule", apiContext, searchParams.get("alert")],
    queryFn: ({ signal }) => api.monitoringDetail<AlertRule>(apiContext, `alerts/${encodeURIComponent(searchParams.get("alert")!)}`, signal),
    enabled: isContextReady && canRead && onAlerts && !!searchParams.get("alert") });
  const selectedAlert = useMemo(
    () =>
      searchParams.get("alert") ? (ruleDetail.isError ? null : ruleDetail.data ?? validAlerts.find((rule) => rule.id === searchParams.get("alert"))) : chooseAlert(validAlerts, validAlertEvents),
    [searchParams, validAlertEvents, validAlerts, ruleDetail.data, ruleDetail.isError],
  );
  const selectedSchedule = useMemo(
    () =>
      validSchedules.find(
        (schedule) => schedule.id === searchParams.get("schedule"),
      ) ?? chooseSchedule(validSchedules),
    [searchParams, validSchedules],
  );
  const filteredAuditLogs = validAuditLogs;
  const selectedAudit = useMemo(
    () =>
      filteredAuditLogs.find((log) => log.id === searchParams.get("audit")) ??
      filteredAuditLogs[0] ??
      null,
    [filteredAuditLogs, searchParams],
  );
  const auditDetail = useQuery({
    queryKey: ["audit-log", apiContext, searchParams.get("audit") || selectedAudit?.id],
    queryFn: ({ signal }) => api.monitoringDetail<AuditLog>(apiContext, `metrics/audit/${encodeURIComponent(searchParams.get("audit") || selectedAudit!.id)}`, signal),
    enabled: Boolean(isContextReady && canAudit && onAudit && (searchParams.get("audit") || selectedAudit?.id)),
  });

  const createAlert = useMutation({
    mutationFn: () =>
      api.createAlert(apiContext, {
        metric: alertForm.metric,
        threshold: alertForm.threshold,
        operator: alertForm.operator,
        window_seconds: Number(alertForm.window_seconds),
        cooldown_seconds: Number(alertForm.cooldown_seconds),
        resource_type: alertForm.resource_type,
        resource_id: alertForm.resource_id,
        notification_channels: alertForm.email
          ? { emails: [alertForm.email] }
          : {},
      }),
    onSuccess: async () => {
      toast.success(t("Alert created"));
      setDialog(null);
      await queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
    onError: (error) =>
      toast.error(errorMessage(error, t("Failed to create alert"))),
  });
  const deleteAlert = useMutation({
    mutationFn: (id: string) => api.deleteAlert(apiContext, id),
    onSuccess: async () => {
      toast.success(t("Alert deleted"));
      setConfirmDelete(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["alerts"] }),
        queryClient.invalidateQueries({ queryKey: ["alert-events"] }),
      ]);
    },
    onError: (error) =>
      toast.error(errorMessage(error, t("Failed to delete alert"))),
  });
  const testAlert = useMutation({
    mutationFn: (id: string) => api.testAlert(apiContext, id),
    onSuccess: async () => {
      toast.success(t("Test event recorded; notification queued"));
      await queryClient.invalidateQueries({ queryKey: ["alert-events"] });
    },
    onError: (error) =>
      toast.error(errorMessage(error, t("Failed to test alert"))),
  });
  const createSchedule = useMutation({
    mutationFn: () => api.createReportSchedule(apiContext, reportForm),
    onSuccess: async () => {
      toast.success(t("Report schedule saved"));
      setDialog(null);
      await queryClient.invalidateQueries({ queryKey: ["report-schedules"] });
    },
    onError: (error) =>
      toast.error(errorMessage(error, t("Failed to create schedule"))),
  });
  const deleteSchedule = useMutation({
    mutationFn: (id: string) => api.deleteReportSchedule(apiContext, id),
    onSuccess: async () => {
      toast.success(t("Report schedule deleted"));
      setConfirmDelete(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["report-schedules"] }),
        queryClient.invalidateQueries({ queryKey: ["report-deliveries"] }),
      ]);
    },
    onError: (error) =>
      toast.error(errorMessage(error, t("Failed to delete schedule"))),
  });
  const sendNow = useMutation({
    mutationFn: (id: string) => api.sendReportNow(apiContext, id),
    onSuccess: async () => {
      toast.success(t("Report queued for delivery"));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["report-deliveries"] }),
        queryClient.invalidateQueries({ queryKey: ["report-schedules"] }),
      ]);
    },
    onError: (error) =>
      toast.error(errorMessage(error, t("Failed to send report"))),
  });

  const issues = useMemo(
    () => buildIssues(validJobs, validAlertEvents, validDeliveries),
    [validAlertEvents, validDeliveries, validJobs],
  );
  const overviewQueries = [
    systemMetrics,
    alerts,
    alertEvents,
    jobs,
    ...(canFinance ? [deliveries] : []),
  ];
  const overviewUnavailable = onOverview && overviewQueries.some((query) => query.isError);
  const overviewIncomplete =
    onOverview && (overviewUnavailable || (nested(systemMetrics.data ?? {}, "monitoring") as Record<string, unknown>).state !== "healthy");
  const overviewLoading =
    onOverview && overviewQueries.some((query) => query.isPending);
  const metrics = systemMetrics.isError ? {} : (systemMetrics.data ?? {});
  const summary = nested(metrics, "summary") as Record<string, unknown>;
  const firingAlerts = Number(summary.firing_alerts ?? 0);
  const failedJobs = Number(summary.failed_jobs ?? 0);
  const failedDeliveries = Number(summary.failed_deliveries ?? 0);

  function setRoute(
    nextView: ObservabilityView,
    mode?: ObservabilityMode,
    selection?: { key: string; value: string },
  ) {
    const next = new URLSearchParams();
    next.set("view", nextView);
    if (mode) next.set("mode", mode);
    if (selection) next.set(selection.key, selection.value);
    setSearchParams(next);
    setInspectorOpen(false);
  }
  function selectObject(
    key: "job" | "alert" | "schedule" | "audit",
    id: string,
  ) {
    const next = new URLSearchParams(searchParams);
    next.set(key, id);
    setSearchParams(next);
    if (!persistentInspector) setInspectorOpen(true);
  }
  function refreshCurrent() {
    const keys =
      view === "overview"
        ? [
            "system-metrics",
            "jobs",
            "alerts",
            "alert-events",
            "report-deliveries",
          ]
        : onJobs
          ? ["jobs", "job-events"]
          : onAlerts
            ? ["alerts", "alert-events"]
            : onReports
              ? ["report-schedules", "report-deliveries"]
              : onAudit
                ? ["audit-logs"]
                : [];
    [...new Set([...keys, "system-metrics", ...(view === "platform" && canDiagnose ? ["platform-monitoring"] : [])])].forEach(
      (key) => void queryClient.invalidateQueries({ queryKey: [key] }),
    );
    if (dialog === "resource" && resource.id) void resourceMetrics.refetch();
  }
  async function copy(value: string) {
    if (!value) return;
    await navigator.clipboard.writeText(value);
    toast.success(t("Copied"));
  }

  const currentInspector = onJobs ? (
    <JobInspector
      job={selectedJob}
      events={jobEvents.isError ? [] : (jobEvents.data?.items ?? [])}
      historyControls={<MonitoringPager label={t("Job events")} control={jobEventsPage} data={jobEvents.data} busy={jobEvents.isFetching} />}
      error={jobEvents.error || jobDetail.error}
      loading={jobEvents.isFetching}
    />
  ) : onAlerts ? (
    <AlertInspector
      canManage={canManage}
      rule={selectedAlert}
      onTest={(id) => testAlert.mutate(id)}
      onDelete={(rule) =>
        setConfirmDelete({
          kind: "alert",
          id: rule.id,
          label: alertSentence(rule),
        })
      }
      busy={testAlert.isPending || deleteAlert.isPending}
    />
  ) : onReports ? (
    <ReportInspector
      canManage={canReports}
      schedule={selectedSchedule}
      onSend={(id) => sendNow.mutate(id)}
      onDelete={(schedule) =>
        setConfirmDelete({
          kind: "report",
          id: schedule.id,
          label: `${schedule.interval} report to ${schedule.email}`,
        })
      }
      busy={sendNow.isPending || deleteSchedule.isPending}
    />
  ) : onAudit ? (
    <AuditInspector
      log={
        auditDetail.isError ? null : auditDetail.data || selectedAudit
      }
      loading={auditDetail.isFetching}
      onCopy={copy}
    />
  ) : null;
  const inspectorTitle = onJobs
    ? selectedJob?.job_type || t("Job details")
    : onAlerts
      ? selectedAlert
        ? metricLabel(selectedAlert.metric)
        : t("Alert details")
      : onReports
        ? selectedSchedule?.email || t("Report details")
        : selectedAudit?.action || t("Audit details");

  return (
    <div className="observability-desk ops-desk">
      <header className="observability-header">
        <div>
          <h1>{t("Observability")}</h1>
        </div>
        <div className="observability-header__refresh">
          {canRead && view !== "platform" && <MonitoringStatus key={JSON.stringify(apiContext)} metrics={metrics}
            loading={systemMetrics.isPending} failed={systemMetrics.isError || overviewUnavailable} onRetry={refreshCurrent} />}
          {canRead && onOverview && <MonitoringWindow value={windowSeconds} onChange={setWindowSeconds} />}
          <button
            className="btn obs-quiet-action"
            type="button"
            onClick={refreshCurrent}
            aria-label={t("Refresh current observability view")}
            disabled={systemMetrics.isFetching}
          >
            <RefreshCw size={15} />{t("Refresh")}</button>
        </div>
      </header>
      <div className="observability-mobile-switcher">
        <label htmlFor="observability-view">{t("Work surface")}</label>
        <select
          id="observability-view"
          className="select"
          value={view}
          onChange={(event) =>
            setRoute(event.target.value as ObservabilityView)
          }
        >
          {visibleViews.map((option) => (
            <option key={option.value} value={option.value}>
              {t(option.label)}
            </option>
          ))}
        </select>
      </div>
      <div className="observability-desktop-tabs">
        <NexilumeTabs
          label={t("Observability work surfaces")}
          options={visibleViews}
          value={view}
          onChange={(next) =>
            setRoute(
              next,
              next === "activity"
                ? "jobs"
                : next === "automations"
                  ? "alerts"
                  : undefined,
            )
          }
          idBase="observability-view"
        />
      </div>

      <section
        id={`observability-view-panel-${view}`}
        aria-labelledby={`observability-view-tab-${view}`}
        role="tabpanel"
        className="observability-work-surface"
      >
        {!canRead && <MonitoringUnavailable pending={permissions.isPending} error={permissions.error} onRetry={() => void permissions.refetch()} />}
        {canRead && view === "overview" && (
          <OperationsOverview
            key={JSON.stringify(apiContext)}
            issues={issues}
            firingAlerts={firingAlerts}
            failedJobs={failedJobs}
            failedDeliveries={failedDeliveries}
            incomplete={overviewIncomplete}
            unavailable={overviewUnavailable}
            loading={overviewLoading}
            alertCount={Number(summary.rule_count ?? 0)}
            canManage={canManage}
            metrics={metrics}
            onRetry={refreshCurrent}
            onReview={(issue) =>
              issue.targetMode === "jobs"
                ? setRoute("activity", "jobs", {
                    key: "job",
                    value: issue.targetId,
                  })
                : issue.targetMode === "alerts"
                  ? setRoute("automations", "alerts", {
                      key: "alert",
                      value: issue.targetId,
                    })
                  : setRoute("automations", "reports", {
                      key: "schedule",
                      value: issue.targetId,
                    })
            }
            onCreateAlert={() => {
              setRoute("automations", "alerts");
              setDialog("alert");
            }}
            onActivity={() => setRoute("activity", "jobs")}
          />
        )}
        {canRead && view === "activity" && (
          <div className="observability-mode-surface">
            <ModeHeader
              title={t("Activity")}
              description={t("Jobs and background operations.")}
              scope={projectId ? "Current project" : "All projects"}
            />
            <JobsSurface
              jobs={filteredJobs}
              selectedId={selectedJob?.id || ""}
              search={jobSearch}
              filters={jobFilters}
              loading={jobs.isPending}
              error={jobs.error}
              onSearch={setJobSearch}
              onFilters={setJobFilters}
              onSelect={(id) => selectObject("job", id)}
              onRetry={refreshCurrent}
              onResourceLookup={() => setDialog("resource")}
              inspector={currentInspector}
            />
          </div>
        )}
        {canRead && view === "automations" && (
          <div className="observability-mode-surface">
            <ModeHeader
              title={t("Automations")}
              description={t("Alert rules and report schedules.")}
              scope={projectId ? "Current project" : "Organization-wide"}
            >
              <NexilumeTabs
                label={t("Automation modes")}
                options={[
                  { value: "alerts" as const, label: t("Alerts") },
                  ...(canFinance ? [{ value: "reports" as const, label: t("Reports") }] : []),
                ]}
                value={automationMode}
                onChange={(mode) => setRoute("automations", mode)}
                variant="compact"
                idBase="observability-automations"
              />
            </ModeHeader>
            {automationMode === "alerts" ? (
              <div
                id="observability-automations-panel-alerts"
                role="tabpanel"
                aria-labelledby="observability-automations-tab-alerts"
              >
                <AlertsSurface search={ruleSearch} onSearch={setRuleSearch}
                  alerts={validAlerts}
                  selectedId={selectedAlert?.id || ""}
                  loading={alerts.isPending}
                  error={alerts.error || alertEvents.error}
                  inspector={currentInspector}
                  canManage={canManage}
                  onCreate={() => setDialog("alert")}
                  onSelect={(id) => selectObject("alert", id)}
                  onRetry={refreshCurrent}
                />
              </div>
            ) : (
              <div
                id="observability-automations-panel-reports"
                role="tabpanel"
                aria-labelledby="observability-automations-tab-reports"
              >
                {!canFinance ? <p>{t("Financial monitoring permission is required.")}</p> : <ReportsSurface
                  schedules={validSchedules}
                  selectedId={selectedSchedule?.id || ""}
                  loading={schedules.isPending}
                  error={schedules.error}
                  inspector={currentInspector}
                  canManage={canReports}
                  onCreate={() => setDialog("report")}
                  onSelect={(id) => selectObject("schedule", id)}
                  onRetry={refreshCurrent}
                />}
              </div>
            )}
          </div>
        )}
        {canAudit && view === "audit" && (
          <AuditSurface
            scope={projectId ? "Current project" : "Organization-wide"}
            logs={filteredAuditLogs}
            selectedId={selectedAudit?.id || ""}
            query={auditQuery}
            resourceType={auditResourceType}
            resourceTypes={Array.from(
              new Set(
                validAuditLogs.map((log) => log.resource_type).filter(Boolean),
              ),
            ).sort()}
            loading={auditLogs.isPending}
            error={auditLogs.error}
            inspector={currentInspector}
            onQuery={setAuditQuery}
            onResourceType={setAuditResourceType}
            onSelect={(id) => selectObject("audit", id)}
            onRetry={refreshCurrent}
          />
        )}
        {canRead && view === "platform" && canDiagnose && <PlatformMonitoring key={JSON.stringify(apiContext)} />}
        {canRead && onJobs && <MonitoringPager label={t("Jobs")} control={jobsPage} data={jobs.data} busy={jobs.isFetching} />}
        {canRead && onAlerts && <MonitoringPager label={t("Alert rules")} control={rulesPage} data={alerts.data} busy={alerts.isFetching} />}
        {canFinance && onReports && <MonitoringPager label={t("Report schedules")} control={schedulePage} data={schedules.data} busy={schedules.isFetching} />}
        {canAudit && onAudit && <MonitoringPager label={t("Audit events")} control={auditPage} data={auditLogs.data} busy={auditLogs.isFetching} />}
        {canRead && onAudit && <RequestLookup />}
      </section>

      <NexilumeDialog
        open={dialog === "alert" && canManage}
        title={t("Create alert")}
        eyebrow={t("AUTOMATIONS · ALERTS")}
        description={t("Choose a measure, condition and notification destination.")}
        onClose={closeDialog}
        busy={createAlert.isPending}
        size="large"
      >
        <AlertForm metricOptions={permissions.data?.metrics.map((metric) => metric.metric) ?? []}
          error={createAlert.error?.message}
          onCancel={closeDialog}
          form={alertForm}
          setForm={setAlertForm}
          isPending={createAlert.isPending}
          onSubmit={() => createAlert.mutate()}
        />
      </NexilumeDialog>
      <NexilumeDialog
        open={dialog === "report" && canReports}
        title={t("Schedule report")}
        eyebrow={t("AUTOMATIONS · REPORTS")}
        description={t("Create a recurring workspace-wide operational summary.")}
        onClose={closeDialog}
        busy={createSchedule.isPending}
      >
        <ReportForm
          error={createSchedule.error?.message}
          onCancel={closeDialog}
          form={reportForm}
          setForm={setReportForm}
          isPending={createSchedule.isPending}
          onSubmit={() => createSchedule.mutate()}
        />
      </NexilumeDialog>
      <NexilumeDialog
        open={dialog === "resource"}
        title={t("Resource lookup")}
        eyebrow={t("ACTIVITY · SECONDARY TOOL")}
        description={t("Query the existing Resource Metrics endpoint by an exact type and ID.")}
        onClose={closeDialog}
        size="large"
      >
        <ResourcesSurface
          resource={resource}
          onResource={setResource}
          data={validResourceMetrics}
          loading={resourceMetrics.isFetching}
          error={resourceMetrics.error}
          onRetry={() => void resourceMetrics.refetch()}
        />
      </NexilumeDialog>
      <NexilumeDialog
        open={inspectorOpen && Boolean(currentInspector)}
        title={inspectorTitle}
        eyebrow={t("OPERATIONS INSPECTOR")}
        onClose={() => setInspectorOpen(false)}
        variant="drawer"
      >
        {currentInspector}
      </NexilumeDialog>
      <NexilumeDialog
        open={Boolean(confirmDelete)}
        title={t("Delete {{0}}?", { 0: confirmDelete?.kind || "automation" })}
        eyebrow={t("DANGER · CONFIRMATION")}
        description={t("This operation cannot be undone. Historical records follow server retention.")}
        onClose={() => setConfirmDelete(null)}
        busy={deleteAlert.isPending || deleteSchedule.isPending}
        footer={
          <>
            <button
              className="btn"
              type="button"
              onClick={() => setConfirmDelete(null)}
            >{t("Cancel")}</button>
            <button
              className="btn obs-danger-action"
              type="button"
              disabled={deleteAlert.isPending || deleteSchedule.isPending}
              onClick={() => {
                if (!confirmDelete) return;
                confirmDelete.kind === "alert"
                  ? deleteAlert.mutate(confirmDelete.id)
                  : deleteSchedule.mutate(confirmDelete.id);
              }}
            >
              <Trash2 size={15} />{t("Delete")}</button>
          </>
        }
      >
        <p className="text-sm text-muted">{confirmDelete?.label}</p>
      </NexilumeDialog>
    </div>
  );
}



function JobsSurface({
  jobs,
  selectedId,
  search,
  filters,
  loading,
  error,
  onSearch,
  onFilters,
  onSelect,
  onRetry,
  onResourceLookup,
  inspector,
}: {
  jobs: Job[];
  selectedId: string;
  search: string;
  filters: {
    status: string;
    job_type: string;
    resource_type: string;
    resource_id: string;
  };
  loading: boolean;
  error: Error | null;
  onSearch: (value: string) => void;
  onFilters: (value: {
    status: string;
    job_type: string;
    resource_type: string;
    resource_id: string;
  }) => void;
  onSelect: (id: string) => void;
  onRetry: () => void;
  onResourceLookup: () => void;
  inspector: ReactNode;
}) {
  useLocale();
  return (
    <div className="obs-surface-stack">
      <div className="obs-toolbar">
        <label className="obs-search">
          <Search size={16} />
          <span className="sr-only">{t("Search jobs")}</span>
          <input
            className="input"
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            placeholder={t("Search jobs or resources")}
          />
        </label>
        <select
          className="select"
          aria-label={t("Filter jobs by status")}
          value={filters.status}
          onChange={(event) =>
            onFilters({ ...filters, status: event.target.value })
          }
        >
          <option value="">{t("All statuses")}</option>
          <option value="queued">{t("Queued")}</option>
          <option value="running">{t("Running")}</option>
          <option value="succeeded">{t("Succeeded")}</option>
          <option value="failed">{t("Failed")}</option>
          <option value="canceled">{t("Canceled")}</option>
        </select>
        <details className="obs-advanced-filters">
          <summary>
            <Filter size={15} />{t("Advanced filters")}</summary>
          <div>
            <Field label={t("Job type")}>
              <input
                className="input"
                value={filters.job_type}
                onChange={(event) =>
                  onFilters({ ...filters, job_type: event.target.value })
                }
              />
            </Field>
            <Field label={t("Resource type")}>
              <input
                className="input"
                value={filters.resource_type}
                onChange={(event) =>
                  onFilters({ ...filters, resource_type: event.target.value })
                }
              />
            </Field>
            <Field label={t("Resource ID")}>
              <input
                className="input font-mono"
                value={filters.resource_id}
                onChange={(event) =>
                  onFilters({ ...filters, resource_id: event.target.value })
                }
              />
            </Field>
          </div>
        </details>
        <button
          className="btn obs-resource-lookup"
          type="button"
          onClick={onResourceLookup}
        >
          <Gauge size={15} />{t("Resource lookup")}</button>
      </div>
      {Boolean(error) && (
        <QueryNotice
          title={t("Jobs unavailable")}
          error={errorMessage(error, "The jobs request failed.")}
          onRetry={onRetry}
        />
      )}
      {loading ? (
        <LoadingRows count={6} />
      ) : jobs.length === 0 ? (
        <EmptyState
          title={t("No matching jobs")}
          description={t("Try removing a filter. Runtime and background jobs appear here when they run.")}
        />
      ) : (
        <ObjectWorkspace
          label={t("Job inventory")}
          inspector={inspector}
          track={jobs.map((job) => (
            <button
              key={job.id}
              type="button"
              className={`obs-object-row ${selectedId === job.id ? "is-selected" : ""}`}
              aria-pressed={selectedId === job.id}
              onClick={() => onSelect(job.id)}
            >
              <JobShape status={job.status} />
              <span>
                <strong>{humanize(job.job_type)}</strong>
                <small>
                  {resourceLabel(job.resource_type)} ·{" "}
                  {compactId(job.resource_id)}
                </small>
              </span>
              <span className="obs-object-row__state">
                <StatusBadge status={job.status} />
                <time>{formatDate(job.started_at || job.created_at)}</time>
              </span>
              <span className="obs-object-row__duration">
                {formatDuration(job.started_at, job.completed_at)}
              </span>
              <ChevronRight size={16} />
            </button>
          ))}
        />
      )}
    </div>
  );
}

function ResourcesSurface({
  resource,
  onResource,
  data,
  loading,
  error,
  onRetry,
}: {
  resource: { type: string; id: string };
  onResource: (value: { type: string; id: string }) => void;
  data: Record<string, unknown> | undefined;
  loading: boolean;
  error: Error | null;
  onRetry: () => void;
}) {
  useLocale();
  return (
    <div className="obs-resource-surface">
      <div className="obs-resource-query">
        <Field label={t("Resource type")}>
          <select
            className="select"
            value={resource.type}
            onChange={(event) =>
              onResource({ ...resource, type: event.target.value })
            }
          >
            <option value="agent">{t("Agent")}</option>
            <option value="router">{t("Router")}</option>
            <option value="model">{t("Model")}</option>
            <option value="dataset">{t("Dataset")}</option>
            <option value="api_key">{t("API key")}</option>
          </select>
        </Field>
        <Field label={t("Resource ID")}>
          <input
            className="input font-mono"
            value={resource.id}
            onChange={(event) =>
              onResource({ ...resource, id: event.target.value })
            }
            placeholder={t("Paste a resource ID")}
          />
        </Field>
      </div>
      {error && (
        <QueryNotice
          title={t("Resource metrics unavailable")}
          error={errorMessage(error, "The resource request failed.")}
          onRetry={onRetry}
        />
      )}
      {!resource.id.trim() ? (
        <EmptyState
          title={t("Choose a resource")}
          description={t("Resource metrics load only after an ID is provided.")}
        />
      ) : loading ? (
        <LoadingRows count={4} />
      ) : data ? (
        <section
          className="obs-resource-result"
          aria-labelledby="resource-summary-title"
        >
          <SectionHeading
            eyebrow={t("{{0}} · SAFE SUMMARY", { 0: resourceLabel(resource.type).toUpperCase() })}
            title={t("Resource health")}
            id="resource-summary-title"
          />
          <KeyValueGrid value={data} />
          <TechnicalDetails>
            <Detail label={t("Full resource ID")} value={resource.id} mono />
            <JsonBlock label={t("Snapshot and raw payload")} value={data} />
          </TechnicalDetails>
        </section>
      ) : (
        <EmptyState
          title={t("No metrics returned")}
          description={t("This resource did not return a metrics snapshot.")}
        />
      )}
    </div>
  );
}

function AlertsSurface({
  search, onSearch,
  alerts,
  selectedId,
  loading,
  error,
  inspector,
  onCreate,
  canManage,
  onSelect,
  onRetry,
}: {
  search: string; onSearch: (value: string) => void;
  alerts: AlertRule[];
  selectedId: string;
  loading: boolean;
  error: Error | null;
  inspector: ReactNode;
  onCreate: () => void;
  canManage: boolean;
  onSelect: (id: string) => void;
  onRetry: () => void;
}) {
  useLocale();
  return (
    <div className="obs-surface-stack">
      <SurfaceAction
        title={t("Alert rules")}
        detail={t("{{0}} rule{{1}} on this page", { 0: alerts.length, 1: alerts.length === 1 ? "" : "s" })}
        tools={<label className="obs-search"><Search size={16} aria-hidden="true" /><span className="sr-only">{t("Search rules")}</span><input className="input" value={search} onChange={(event) => onSearch(event.target.value)} placeholder={t("Search metric or resource")} /></label>}
        action="Create alert"
        icon={<Plus />}
        onAction={onCreate} canManage={canManage}
      />
      {error && (
        <QueryNotice
          title={t("Alerts unavailable")}
          error={errorMessage(error, "The alerts request failed.")}
          onRetry={onRetry}
        />
      )}
      {loading ? (
        <LoadingRows count={5} />
      ) : alerts.length === 0 ? (
        <EmptyState
          title={t("No alert rules")}
          description={t("Create a rule to monitor balance, usage, or a scoped resource.")}

        />
      ) : (
        <ObjectWorkspace
          label={t("Alert rules")}
          inspector={inspector}
          track={alerts.map((rule) => (
            <button
              key={rule.id}
              type="button"
              className={`obs-object-row ${selectedId === rule.id ? "is-selected" : ""}`}
              aria-pressed={selectedId === rule.id}
              onClick={() => onSelect(rule.id)}
            >
              <AlertShape />
              <span>
                <strong>{metricLabel(rule.metric)}</strong>
                <small>
                  {conditionLabel(rule.operator)}{" "}
                  {formatThreshold(rule.metric, rule.threshold)}
                </small>
              </span>
              <span className="obs-object-row__state">
                <StatusBadge status={rule.status} />
                <time>
                  {formatDate(rule.last_triggered_at || rule.updated_at)}
                </time>
              </span>
              <ChevronRight size={16} />
            </button>
          ))}
        />
      )}
    </div>
  );
}

function ReportsSurface({
  schedules,
  selectedId,
  loading,
  error,
  inspector,
  onCreate,
  canManage,
  onSelect,
  onRetry,
}: {
  schedules: ReportSchedule[];
  selectedId: string;
  loading: boolean;
  error: Error | null;
  inspector: ReactNode;
  onCreate: () => void;
  canManage: boolean;
  onSelect: (id: string) => void;
  onRetry: () => void;
}) {
  useLocale();
  return (
    <div className="obs-surface-stack">
      <SurfaceAction
        title={t("Report schedules")}
        detail={t("{{0}} schedule{{1}} on this page", { 0: schedules.length, 1: schedules.length === 1 ? "" : "s" })}
        action="Schedule report"
        icon={<Plus />}
        onAction={onCreate} canManage={canManage}
      />
      {error && (
        <QueryNotice
          title={t("Reports unavailable")}
          error={errorMessage(error, "The reports request failed.")}
          onRetry={onRetry}
        />
      )}
      {loading ? (
        <LoadingRows count={5} />
      ) : schedules.length === 0 ? (
        <EmptyState
          title={t("No report schedules")}
          description={t("Schedule a daily, weekly, or monthly operational email.")}

        />
      ) : (
        <ObjectWorkspace
          label={t("Report schedules")}
          inspector={inspector}
          track={schedules.map((schedule) => (
            <button
              key={schedule.id}
              type="button"
              className={`obs-object-row ${selectedId === schedule.id ? "is-selected" : ""}`}
              aria-pressed={selectedId === schedule.id}
              onClick={() => onSelect(schedule.id)}
            >
              <ReportShape />
              <span>
                <strong>{schedule.email}</strong>
                <small>{humanize(schedule.interval)}{" "}{t("operational report")}</small>
              </span>
              <span className="obs-object-row__state">
                <StatusBadge status={schedule.status} />
                <time>
                  {formatDate(schedule.last_sent_at || schedule.updated_at)}
                </time>
              </span>
              <ChevronRight size={16} />
            </button>
          ))}
        />
      )}
    </div>
  );
}

function AuditSurface({
  scope,
  logs,
  selectedId,
  query,
  resourceType,
  resourceTypes,
  loading,
  error,
  inspector,
  onQuery,
  onResourceType,
  onSelect,
  onRetry,
}: {
  logs: AuditLog[];
  scope: string;
  selectedId: string;
  query: string;
  resourceType: string;
  resourceTypes: string[];
  loading: boolean;
  error: Error | null;
  inspector: ReactNode;
  onQuery: (value: string) => void;
  onResourceType: (value: string) => void;
  onSelect: (id: string) => void;
  onRetry: () => void;
}) {
  useLocale();
  return (
    <div className="observability-mode-surface">
      <ModeHeader
        title={t("Audit")}
        description={t("Security and resource changes.")}
        scope={scope}
      />
      <div className="obs-toolbar">
        <label className="obs-search">
          <Search size={16} />
          <span className="sr-only">{t("Search audit events")}</span>
          <input
            className="input"
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder={t("Search action, actor, or resource")}
          />
        </label>
        <select
          className="select"
          aria-label={t("Filter audit events by resource type")}
          value={resourceType}
          onChange={(event) => onResourceType(event.target.value)}
        >
          <option value="">{t("All resource types")}</option>
          {resourceTypes.map((type) => (
            <option key={type} value={type}>
              {resourceLabel(type)}
            </option>
          ))}
        </select>
      </div>
      {error && (
        <QueryNotice
          title={t("Audit unavailable")}
          error={errorMessage(
            error,
            "You may not have permission to view audit events.",
          )}
          onRetry={onRetry}
        />
      )}
      {loading ? (
        <LoadingRows count={6} />
      ) : logs.length === 0 ? (
        <EmptyState
          title={t("No matching audit events")}
          description={t("Write operations and governed system changes will appear here.")}
        />
      ) : (
        <ObjectWorkspace
          label={t("Audit events")}
          inspector={inspector}
          track={logs.map((log) => (
            <button
              key={log.id}
              type="button"
              className={`obs-object-row obs-object-row--audit ${selectedId === log.id ? "is-selected" : ""}`}
              aria-pressed={selectedId === log.id}
              onClick={() => onSelect(log.id)}
            >
              <AuditShape />
              <span>
                <strong>{humanize(log.action)}</strong>
                <small>
                  {log.actor_email || "System"} ·{" "}
                  {resourceLabel(log.resource_type)}
                </small>
              </span>
              <span className="obs-object-row__state">
                <span className="obs-result-label">{auditResult(log)}</span>
                <time>{formatDate(log.created_at)}</time>
              </span>
              <ChevronRight size={16} />
            </button>
          ))}
        />
      )}
    </div>
  );
}

function JobInspector({
  job,
  events,
  loading,
  historyControls,
  error,
}: {
  job: Job | null | undefined;
  events: JobEvent[];
  loading: boolean;
  historyControls: ReactNode;
  error: Error | null;
}) {
  useLocale();
  if (!job) return <InspectorEmpty title={t("No job selected")} />;
  return (
    <InspectorShell
      eyebrow={t("JOB INSPECTOR")}
      title={humanize(job.job_type)}
      status={job.status}
    >
      <InspectorFacts
        facts={[
          [
            "Resource",
            `${resourceLabel(job.resource_type)} · ${compactId(job.resource_id)}`,
          ],
          ["Started", formatDate(job.started_at || job.created_at)],
          ["Duration", formatDuration(job.started_at, job.completed_at)],
        ]}
      />
      {job.error_message && (
        <div className="obs-error-block">
          <AlertTriangle size={17} />
          <div>
            <strong>{job.error_code || t("Job failed")}</strong>
            <p>{job.error_message}</p>
          </div>
        </div>
      )}
      {Object.keys(job.result_json || {}).length > 0 && (
        <section className="obs-inspector-section">
          <h3>{t("Key result")}</h3>
          <KeyValueGrid value={job.result_json} />
        </section>
      )}
      <section className="obs-inspector-section">
        <h3>{t("Event timeline")}</h3>
        {error && <p role="alert">{t("Job history could not be loaded. Refresh to retry.")}</p>}
        {historyControls}
        {loading ? (
          <LoadingRows count={2} />
        ) : events.length === 0 ? (
          <p className="obs-muted-copy">{t("No recorded events.")}</p>
        ) : (
          <EventTimeline events={events} />
        )}
      </section>
      <TechnicalDetails>
        <Detail label={t("Job ID")} value={job.id} mono />
        <Detail
          label={t("Celery task ID")}
          value={job.celery_task_id || "Not available"}
          mono
        />
        <p>{t("Raw job inputs and results are excluded from this operational index.")}</p>
      </TechnicalDetails>
    </InspectorShell>
  );
}

function AlertInspector({
  rule,
  canManage,
  onTest,
  onDelete,
  busy,
}: {
  rule: AlertRule | null | undefined;
  canManage: boolean;
  onTest: (id: string) => void;
  onDelete: (rule: AlertRule) => void;
  busy: boolean;
}) {
  useLocale();
  const { apiContext } = useAuth();
  const page = useCursorPage([apiContext, rule?.id]);
  const history = useQuery({ queryKey: ["alert-events", apiContext, rule?.id, page.cursor],
    queryFn: ({ signal }) => api.monitoringPage<AlertEvent>(apiContext, "alerts/events", { rule: rule!.id, cursor: page.cursor }, signal),
    enabled: !!rule, refetchInterval: 30_000 });
  const events = history.isError ? [] : history.data?.items ?? [];
  if (!rule) return <InspectorEmpty title={t("No accessible alert selected")} />;
  return (
    <InspectorShell
      eyebrow={t("ALERT RULE")}
      title={metricLabel(rule.metric)}
      status={rule.status}
    >
      <p className="obs-rule-sentence">{alertSentence(rule)}</p>
      <InspectorFacts
        facts={[
          [
            "Scope",
            rule.resource_type
              ? `${resourceLabel(rule.resource_type)} · ${compactId(rule.resource_id)}`
              : "Organization-wide",
          ],
          ["Last evaluated", formatDate(rule.last_evaluated_at)],
          ["Last triggered", formatDate(rule.last_triggered_at)],
        ]}
      />
      {canManage && <div className="obs-inspector-actions">
        <button
          className="btn btn-primary"
          type="button"
          onClick={() => onTest(rule.id)}
          disabled={busy}
        >
          <Bell size={15} />{t("Send test")}</button>
        <button
          className="btn obs-danger-quiet"
          type="button"
          onClick={() => onDelete(rule)}
          disabled={busy}
          aria-label={t("Delete alert for {{0}}", { 0: metricLabel(rule.metric) })}
        >
          <Trash2 size={15} />{t("Delete")}</button>
      </div>}
      {canManage && <MonitoringActions kind="rules" id={rule.id} actions={[{ action: rule.status === "active" ? "pause" : "resume", label: rule.status === "active" ? t("Pause rule") : t("Resume rule") }, { action: rule.muted_until ? "unmute" : "mute", label: rule.muted_until ? t("Unmute") : t("Mute for one hour") }]} />}
      <p>{t("Window:")}{" "}{rule.window_seconds}{t("s · Cooldown:")}{" "}{rule.cooldown_seconds}{t("s · Evaluation:")}{" "}{rule.last_error_code || t("No recorded error")}</p>
      <section className="obs-inspector-section">
        <h3>{t("Event history")}</h3>
        {history.isError && <p role="alert">{t("Event history unavailable.")}{" "}<button className="btn" onClick={() => void history.refetch()}>{t("Retry")}</button></p>}
        <MonitoringPager label={t("Rule events")} control={page} data={history.data} busy={history.isFetching} />
        {events.length ? (
          <AlertEventTimeline events={events} canManage={canManage} />
        ) : (
          <p className="obs-muted-copy">{t("This rule has no events yet.")}</p>
        )}
      </section>
      <TechnicalDetails>
        <Detail label={t("Rule ID")} value={rule.id} mono />
        <Detail label={t("Metric key")} value={rule.metric} mono />
        <Detail
          label={t("Resource ID")}
          value={rule.resource_id || "Not scoped"}
          mono
        />
        <JsonBlock
          label={t("Notification channels")}
          value={rule.notification_channels}
        />
      </TechnicalDetails>
    </InspectorShell>
  );
}

function ReportInspector({
  schedule,
  canManage,
  onSend,
  onDelete,
  busy,
}: {
  schedule: ReportSchedule | null | undefined;
  canManage: boolean;
  onSend: (id: string) => void;
  onDelete: (schedule: ReportSchedule) => void;
  busy: boolean;
}) {
  useLocale();
  const { apiContext } = useAuth();
  const page = useCursorPage([apiContext, schedule?.id]);
  const history = useQuery({ queryKey: ["report-deliveries", apiContext, schedule?.id, page.cursor],
    queryFn: ({ signal }) => api.monitoringPage<ReportDelivery>(apiContext, "reports/deliveries", { schedule: schedule!.id, cursor: page.cursor }, signal),
    enabled: !!schedule, refetchInterval: 30_000 });
  const deliveries = history.isError ? [] : history.data?.items ?? [];
  if (!schedule) return <InspectorEmpty title={t("No accessible report selected")} />;
  return (
    <InspectorShell
      eyebrow={t("REPORT SCHEDULE")}
      title={schedule.email}
      status={schedule.status}
    >
      <InspectorFacts
        facts={[
          ["Cadence", humanize(schedule.interval)],
          ["Scope", "Organization-wide"],
          ["Last sent", formatDate(schedule.last_sent_at)],
        ]}
      />
      {canManage && <div className="obs-inspector-actions">
        <button
          className="btn btn-primary"
          type="button"
          onClick={() => onSend(schedule.id)}
          disabled={busy}
        >
          <Send size={15} />{t("Send now")}</button>
        <button
          className="btn obs-danger-quiet"
          type="button"
          onClick={() => onDelete(schedule)}
          disabled={busy}
          aria-label={t("Delete report schedule for {{0}}", { 0: schedule.email })}
        >
          <Trash2 size={15} />{t("Delete")}</button>
      </div>}
      <section className="obs-inspector-section">
        <h3>{t("Delivery history")}</h3>
        <p>{t("Sent means accepted by the email transport, not confirmed receipt. Retried SMTP delivery may duplicate email after a crash.")}</p>
        {history.isError && <p role="alert">{t("Delivery history unavailable.")}{" "}<button className="btn" onClick={() => void history.refetch()}>{t("Retry")}</button></p>}
        <MonitoringPager label={t("Report deliveries")} control={page} data={history.data} busy={history.isFetching} />
        {deliveries.length ? (
          <DeliveryTimeline deliveries={deliveries} canManage={canManage} />
        ) : (
          <p className="obs-muted-copy">{t("No report has been delivered yet.")}</p>
        )}
      </section>
      <TechnicalDetails>
        <Detail label={t("Schedule ID")} value={schedule.id} mono />
      </TechnicalDetails>
    </InspectorShell>
  );
}

function AuditInspector({
  log,
  loading,
  onCopy,
}: {
  log: AuditLog | null | undefined;
  loading: boolean;
  onCopy: (value: string) => Promise<void>;
}) {
  useLocale();
  if (!log) return <InspectorEmpty title={t("No audit event selected")} />;
  return (
    <InspectorShell
      eyebrow={t("AUDIT EVENT")}
      title={humanize(log.action)}
      status={auditResult(log)}
    >
      {loading && (
        <p className="obs-inline-loading">
          <Loader2 size={15} className="animate-spin" />{t("Loading complete event")}</p>
      )}
      <InspectorFacts
        facts={[
          ["Actor", log.actor_email || "System"],
          ["Resource", resourceLabel(log.resource_type)],
          ["Occurred", formatDate(log.created_at)],
        ]}
      />
      <div className="obs-request-id">
        <span>
          <small>{t("REQUEST ID")}</small>
          <strong style={{ overflowWrap: "anywhere" }}>{log.request_id || t("Not available")}</strong>
        </span>
        <button
          className="btn"
          type="button"
          onClick={() => void onCopy(log.request_id)}
          disabled={!log.request_id}
          aria-label={t("Copy request ID")}
        >
          <Copy size={15} />{t("Copy")}</button>
      </div>
      <TechnicalDetails>
        <Detail
          label={t("Full request ID")}
          value={log.request_id || "Not available"}
          mono
        />
        <Detail
          label={t("Resource ID")}
          value={log.resource_id || "Not available"}
          mono
        />
        <Detail
          label={t("IP address")}
          value={log.ip_address || "Not recorded"}
          mono
        />
        <JsonBlock label={t("Before")} value={log.before_snapshot} />
        <JsonBlock label={t("After")} value={log.after_snapshot} />
        <JsonBlock label={t("Metadata")} value={log.metadata} />
      </TechnicalDetails>
    </InspectorShell>
  );
}

function AlertForm({
  error, onCancel,
  metricOptions,
  form,
  setForm,
  isPending,
  onSubmit,
}: {
  error?: string; onCancel: () => void;
  metricOptions: string[];
  form: {
    metric: string;
    threshold: string;
    operator: string;
    resource_type: string;
    resource_id: string;
    email: string;
    window_seconds: string;
    cooldown_seconds: string;
  };
  setForm: (value: {
    metric: string;
    threshold: string;
    operator: string;
    resource_type: string;
    resource_id: string;
    email: string;
    window_seconds: string;
    cooldown_seconds: string;
  }) => void;
  isPending: boolean;
  onSubmit: () => void;
}) {
  useLocale();
  const sentence = `Notify when ${metricLabel(form.metric)} ${conditionLabel(form.operator)} ${formatThreshold(form.metric, form.threshold || "0")}.`;
  return (
    <form
      className="obs-task-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (!isPending) onSubmit();
      }}
    >
      <div className="obs-rule-preview">
        <span>{t("RULE PREVIEW")}</span>
        <strong>{sentence}</strong>
      </div>
      <fieldset className="ops-form-section"><legend>{t("Measure and condition")}</legend><div className="obs-form-grid">
        <Field label={t("Measure")}>
          <select
            className="select"
            value={form.metric}
            onChange={(event) =>
              setForm({ ...form, metric: event.target.value })
            }
          >
            {metricOptions.map((metric) => (
              <option key={metric} value={metric}>
                {metricLabel(metric)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("Condition")}>
          <select
            className="select"
            value={form.operator}
            onChange={(event) =>
              setForm({ ...form, operator: event.target.value })
            }
          >
            {Object.entries(operatorLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {t(label)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("Threshold")}>
          <input
            className="input"
            inputMode="decimal"
            value={form.threshold}
            onChange={(event) =>
              setForm({ ...form, threshold: event.target.value })
            }
            required
          />
        </Field>
      </div>
      </fieldset>
      <fieldset className="ops-form-section"><legend>{t("Evaluation")}</legend><div className="obs-form-grid">
        <Field label={t("Evaluation window (seconds)")}><input className="input" type="number" min="1" max="604800" required value={form.window_seconds} onChange={(event) => setForm({ ...form, window_seconds: event.target.value })} /></Field>
        <Field label={t("Cooldown (seconds)")}><input className="input" type="number" min="0" required value={form.cooldown_seconds} onChange={(event) => setForm({ ...form, cooldown_seconds: event.target.value })} /></Field>
      </div>
      </fieldset>
      <fieldset className="ops-form-section"><legend>{t("Notification")}</legend>
      <Field label={t("Notification email")}>
        <input
          className="input"
          type="email"
          value={form.email}
          onChange={(event) => setForm({ ...form, email: event.target.value })}
          placeholder={t("Optional")}
        />
      </Field>
      <p className="ops-form-hint">{t("Leave empty to record alerts without email.")}</p></fieldset>
      <details className="obs-form-advanced">
        <summary>{t("Advanced scope")}</summary>
        <div>
          <Field label={t("Internal metric key")}>
            <input className="input font-mono" value={form.metric} readOnly />
          </Field>
          <Field label={t("Resource type")}>
            <input
              className="input"
              value={form.resource_type}
              onChange={(event) =>
                setForm({ ...form, resource_type: event.target.value })
              }
              placeholder={t("Optional")}
            />
          </Field>
          <Field label={t("Resource ID")}>
            <input
              className="input font-mono"
              value={form.resource_id}
              onChange={(event) =>
                setForm({ ...form, resource_id: event.target.value })
              }
              placeholder={t("Optional")}
            />
          </Field>
        </div>
      </details>
      {error && <p className="ops-form-error" role="alert">{error}</p>}
      <div className="ops-form-footer"><button className="btn" type="button" onClick={onCancel} disabled={isPending}>{t("Cancel")}</button><button
        className="btn btn-primary obs-form-submit"
        type="submit"
        disabled={isPending}
      >
        {isPending && <Loader2 size={16} className="animate-spin" />}{t("Create alert")}</button></div>
    </form>
  );
}

function ReportForm({
  error, onCancel,
  form,
  setForm,
  isPending,
  onSubmit,
}: {
  error?: string; onCancel: () => void;
  form: { email: string; interval: string };
  setForm: (value: { email: string; interval: string }) => void;
  isPending: boolean;
  onSubmit: () => void;
}) {
  useLocale();
  return (
    <form
      className="obs-task-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (!isPending) onSubmit();
      }}
    >
      <div className="obs-rule-preview">
        <span>{t("SCHEDULE PREVIEW")}</span>
        <strong>{t("Send a")}{" "}{form.interval}{" "}{t("workspace-wide operational report to")}{" "}
          {form.email || t("the selected email")}.
        </strong>
      </div>
      <Field label={t("Email")}>
        <input
          className="input"
          type="email"
          value={form.email}
          onChange={(event) => setForm({ ...form, email: event.target.value })}
          required
        />
      </Field>
      <Field label={t("Cadence")}>
        <select
          className="select"
          value={form.interval}
          onChange={(event) =>
            setForm({ ...form, interval: event.target.value })
          }
        >
          <option value="daily">{t("Daily")}</option>
          <option value="weekly">{t("Weekly")}</option>
          <option value="monthly">{t("Monthly")}</option>
        </select>
      </Field>
      {error && <p className="ops-form-error" role="alert">{error}</p>}
      <div className="ops-form-footer"><button className="btn" type="button" onClick={onCancel} disabled={isPending}>{t("Cancel")}</button><button
        className="btn btn-primary obs-form-submit"
        type="submit"
        disabled={isPending}
      >
        {isPending && <Loader2 size={16} className="animate-spin" />}{t("Save schedule")}</button></div>
    </form>
  );
}

function ModeHeader({
  title,
  description,
  scope,
  children,
}: {
  title: string;
  description: string;
  scope: string;
  children?: ReactNode;
}) {
  useLocale();
  return (
    <header className="obs-mode-header">
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      <div className="obs-mode-header__controls">
        <ScopeBadge>{scope}</ScopeBadge>
        {children}
      </div>
    </header>
  );
}
function SurfaceAction({
  tools,
  canManage = true,
  title,
  detail,
  action,
  icon,
  onAction,
}: {
  tools?: ReactNode;
  canManage?: boolean;
  title: string;
  detail: string;
  action: string;
  icon: ReactNode;
  onAction: () => void;
}) {
  useLocale();
  return (
    <div className="obs-surface-action">
      <div>
        <strong>{title}</strong>
        <span>{detail}</span>
      </div>
      {tools}
      {canManage && <button className="btn btn-primary" type="button" onClick={onAction}>
        {icon}
        {action}
      </button>}
    </div>
  );
}
function ObjectWorkspace({
  track,
  inspector,
  label,
}: {
  track: ReactNode;
  inspector: ReactNode;
  label: string;
}) {
  useLocale();
  return (
    <div className="obs-object-workspace">
      <div className="obs-object-track" aria-label={label}>
        {track}
      </div>
      <aside className="obs-inspector" aria-label={t("{{0}} inspector", { 0: label })}>
        {inspector}
      </aside>
    </div>
  );
}
function InspectorShell({
  eyebrow,
  title,
  status,
  children,
}: {
  eyebrow: string;
  title: string;
  status: string;
  children: ReactNode;
}) {
  useLocale();
  return (
    <div className="obs-inspector-shell">
      <header>
        <div>
          <p>{eyebrow}</p>
          <h2>{title}</h2>
        </div>
        <StatusBadge status={status} />
      </header>
      {children}
    </div>
  );
}
function InspectorEmpty({ title }: { title: string }) {
  useLocale();
  return (
    <div className="obs-inspector-empty">
      <Gauge size={24} />
      <strong>{title}</strong>
      <p>{t("Select an item from the object track to inspect it.")}</p>
    </div>
  );
}
function InspectorFacts({ facts }: { facts: Array<[string, string]> }) {
  useLocale();
  return (
    <dl className="obs-inspector-facts">
      {facts.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
function TechnicalDetails({ children }: { children: ReactNode }) {
  useLocale();
  return (
    <details className="obs-technical-details">
      <summary>{t("Technical details")}</summary>
      <div>{children}</div>
    </details>
  );
}
function Detail({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  useLocale();
  return (
    <div className="obs-detail">
      <span>{label}</span>
      <strong className={mono ? "font-mono" : ""}>{value}</strong>
    </div>
  );
}
function JsonBlock({
  label,
  value,
}: {
  label: string;
  value: Record<string, unknown>;
}) {
  useLocale();
  return (
    <div className="obs-json">
      <span>{label}</span>
      <pre>{JSON.stringify(value || {}, null, 2)}</pre>
    </div>
  );
}
function KeyValueGrid({ value }: { value: Record<string, unknown> }) {
  useLocale();
  const entries = Object.entries(value)
    .filter(([, item]) => item === null || typeof item !== "object")
    .slice(0, 12);
  if (!entries.length)
    return (
      <p className="obs-muted-copy">{t("No safe summary fields were returned. Open Technical details to view the raw snapshot.")}</p>
    );
  return (
    <dl className="obs-key-values">
      {entries.map(([key, item]) => (
        <div key={key}>
          <dt>{humanize(key)}</dt>
          <dd>{String(item ?? "Not available")}</dd>
        </div>
      ))}
    </dl>
  );
}
function EventTimeline({ events }: { events: JobEvent[] }) {
  useLocale();
  return (
    <ol className="obs-timeline">
      {events.map((event) => (
        <li key={event.id}>
          <span />
          <div>
            <strong>{humanize(event.event_type)}</strong>
            <p>{event.message || t("No message")}</p>
            <time>{formatDate(event.created_at)}</time>
          </div>
        </li>
      ))}
    </ol>
  );
}
function AlertEventTimeline({ events, canManage }: { events: AlertEvent[]; canManage: boolean }) {
  useLocale();
  return (
    <ol className="obs-timeline">
      {events.map((event) => (
        <li key={event.id}>
          <span />
          <div>
            <strong>
              {event.is_test ? t("Test event (not an incident)") : event.status === "firing"
                ? t("Alert firing")
                : humanize(event.status)}
            </strong>
            <p>{t("Observed")}{" "}{event.value}{t("; threshold")}{" "}{event.threshold}.
            </p>
            <time>{formatDate(event.triggered_at)}</time>
            {event.acknowledged_at && <p>{t("Acknowledged")}{" "}{formatDate(event.acknowledged_at)}</p>}
            {canManage && !event.is_test && !event.acknowledged_at && <MonitoringActions kind="incidents" id={event.id} actions={[{ action: "acknowledge", label: t("Acknowledge incident") }]} />}
            {event.notifications?.map((notification) => <div key={notification.id}><p>{t("Notification:")}{" "}{notification.delivery_status}</p>{canManage && notification.delivery_status === "failed" && <MonitoringActions kind="notifications" id={notification.id} actions={[{ action: "retry", label: t("Retry notification") }]} />}</div>)}
          </div>
        </li>
      ))}
    </ol>
  );
}
function DeliveryTimeline({ deliveries, canManage }: { deliveries: ReportDelivery[]; canManage: boolean }) {
  useLocale();
  return (
    <ol className="obs-timeline">
      {deliveries.map((delivery) => (
        <li key={delivery.id}>
          <span />
          <div>
            <strong>{humanize(delivery.delivery_status)}</strong>
            <p>
              {delivery.error_message ||
                t("{{0}} report to {{1}}", { 0: humanize(delivery.interval), 1: delivery.email })}
            </p>
            <time>{formatDate(delivery.sent_at || delivery.created_at)}</time>
            <p>{formatDate(delivery.period_start)} – {formatDate(delivery.period_end)}</p>
            {canManage && delivery.delivery_status === "failed" && <MonitoringActions kind="deliveries" id={delivery.id} actions={[{ action: "retry", label: t("Retry report delivery") }]} />}
          </div>
        </li>
      ))}
    </ol>
  );
}
function SectionHeading({
  eyebrow,
  title,
  count,
  id,
}: {
  eyebrow: string;
  title: string;
  count?: number;
  id?: string;
}) {
  useLocale();
  return (
    <header className="obs-section-heading">
      <div>
        <span>{eyebrow}</span>
        <h2 id={id}>{title}</h2>
      </div>
      {typeof count === "number" && <strong>{count}</strong>}
    </header>
  );
}
function ScopeBadge({ children }: { children: ReactNode }) {
  useLocale();
  return (
    <span className="obs-scope-badge">
      <ShieldCheck size={13} />
      {children}
    </span>
  );
}
function MonitoringUnavailable({ pending, error, onRetry }: { pending: boolean; error: Error | null; onRetry: () => void }) {
  useLocale();
  const status = (error as (Error & { status?: number }) | null)?.status;
  const permission = status === 403 || /permission|forbidden/i.test(error?.message ?? "");
  const reference = (error as (Error & { requestId?: string }) | null)?.requestId ?? "";
  const safeReference = /^[a-zA-Z0-9_-]{1,80}$/.test(reference) ? reference : "";
  return <section className="ops-unavailable" aria-label={t("Monitoring access")} role={pending ? "status" : "alert"}>
    <Gauge size={28} aria-hidden="true" />
    <h2>{pending ? t("Checking monitoring access") : t("Monitoring unavailable")}</h2>
    <p>{pending ? t("Checking access for the selected organization and project…") : permission ? t("Monitoring access is not allowed in this scope. Choose an authorized project or ask your administrator.") : error ? t("We couldn’t load monitoring for this workspace. Retry, or contact support if this continues.") : t("Monitoring permission is required for this scope.")}</p>
    {!pending && <button className="btn" onClick={onRetry}>{t("Retry monitoring")}</button>}
    {!pending && safeReference && <p className="data-freshness-time">{t("Support reference:")}{" "}<code>{safeReference}</code></p>}
  </section>;
}
function QueryNotice({
  title,
  error,
  onRetry,
}: {
  title: string;
  error: string;
  onRetry: () => void;
}) {
  useLocale();
  return (
    <div className="obs-query-notice" role="alert">
      <AlertTriangle size={18} />
      <div>
        <strong>{title}</strong>
        <p>{error}</p>
      </div>
      <button className="btn" type="button" onClick={onRetry}>{t("Retry")}</button>
    </div>
  );
}
function LoadingRows({ count }: { count: number }) {
  useLocale();
  return (
    <div className="obs-loading-rows" aria-label={t("Loading")}>
      {Array.from({ length: count }, (_, index) => (
        <span key={index} />
      ))}
    </div>
  );
}
function JobShape({ status }: { status: string }) {
  useLocale();
  return (
    <span className={`obs-node obs-node--job is-${status}`}>
      <Clock3 />
    </span>
  );
}
function AlertShape() {
  useLocale();
  return (
    <span className="obs-node obs-node--alert">
      <Bell />
    </span>
  );
}
function ReportShape() {
  useLocale();
  return (
    <span className="obs-node obs-node--report">
      <FileClock />
    </span>
  );
}
function AuditShape() {
  useLocale();
  return (
    <span className="obs-node obs-node--audit">
      <ShieldCheck />
    </span>
  );
}

function usePersistentInspector() {
  const [persistent, setPersistent] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(min-width: 1440px)").matches,
  );
  useEffect(() => {
    const media = window.matchMedia("(min-width: 1440px)");
    const update = () => setPersistent(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return persistent;
}

function buildIssues(
  jobs: Job[],
  events: AlertEvent[],
  deliveries: ReportDelivery[],
): ObservabilityIssue[] {
  const result: Array<ObservabilityIssue & { priority: number }> = [];
  events
    .filter((event) => event.status === "firing" && !event.resolved_at)
    .forEach((event) =>
      result.push({
        id: `alert-${event.id}`,
        kind: "alert",
        title: t("{{0}} alert is firing", { 0: metricLabel(event.metric) }),
        detail: t("Observed {{0}}; threshold {{1}}.", { 0: event.value, 1: event.threshold }),
        status: "firing",
        timestamp: event.triggered_at,
        targetId: event.rule,
        targetMode: "alerts",
        priority: 0,
      }),
    );
  jobs
    .filter((job) => job.status === "failed")
    .forEach((job) =>
      result.push({
        id: `job-${job.id}`,
        kind: "job",
        title: t("{{0}} failed", { 0: humanize(job.job_type) }),
        detail:
          job.error_message ||
          `${resourceLabel(job.resource_type)} ${compactId(job.resource_id)}`,
        status: "failed",
        timestamp: job.completed_at || job.updated_at,
        targetId: job.id,
        targetMode: "jobs",
        priority: 1,
      }),
    );
  events.forEach((event) =>
    event.notifications
      .filter((notification) => notification.delivery_status === "failed")
      .forEach((notification) =>
        result.push({
          id: `notification-${notification.id}`,
          kind: "delivery",
          title: t("Alert notification failed"),
          detail:
            notification.error_message ||
            t("{{0}} delivery to {{1}}", { 0: notification.channel, 1: notification.target }),
          status: "failed",
          timestamp: event.updated_at,
          targetId: event.rule,
          targetMode: "alerts",
          priority: 2,
        }),
      ),
  );
  deliveries
    .filter((delivery) => delivery.delivery_status === "failed")
    .forEach((delivery) =>
      result.push({
        id: `delivery-${delivery.id}`,
        kind: "delivery",
        title: t("Report delivery failed"),
        detail: delivery.error_message || t("Delivery to {{0}}", { 0: delivery.email }),
        status: "failed",
        timestamp: delivery.sent_at || delivery.created_at,
        targetId: delivery.schedule,
        targetMode: "reports",
        priority: 2,
      }),
    );
  return result
    .sort(
      (left, right) =>
        left.priority - right.priority ||
        Date.parse(right.timestamp) - Date.parse(left.timestamp),
    )
    .map(({ priority: _priority, ...issue }) => issue);
}
function chooseJob(jobs: Job[]) {
  return (
    [...jobs].sort(
      (left, right) =>
        jobPriority(left) - jobPriority(right) ||
        Date.parse(right.updated_at) - Date.parse(left.updated_at),
    )[0] ?? null
  );
}
function chooseAlert(rules: AlertRule[], events: AlertEvent[]) {
  const firingIds = new Set(
    events
      .filter((event) => event.status === "firing" && !event.resolved_at)
      .map((event) => event.rule),
  );
  return (
    [...rules].sort(
      (left, right) =>
        Number(firingIds.has(right.id)) - Number(firingIds.has(left.id)) ||
        Date.parse(right.updated_at) - Date.parse(left.updated_at),
    )[0] ?? null
  );
}
function chooseSchedule(schedules: ReportSchedule[]) {
  return (
    [...schedules].sort(
      (left, right) =>
        Number(right.status === "active") - Number(left.status === "active") ||
        Date.parse(right.updated_at) - Date.parse(left.updated_at),
    )[0] ?? null
  );
}
function jobPriority(job: Job) {
  return job.status === "failed"
    ? 0
    : ["running", "queued"].includes(job.status)
      ? 1
      : 2;
}
function countFailedDeliveries(
  events: AlertEvent[],
  deliveries: ReportDelivery[],
) {
  return (
    deliveries.filter((delivery) => delivery.delivery_status === "failed")
      .length +
    events.reduce(
      (count, event) =>
        count +
        event.notifications.filter(
          (notification) => notification.delivery_status === "failed",
        ).length,
      0,
    )
  );
}
function formatDuration(start: string | null, end: string | null) {
  if (!start) return t("Not started");
  const ms = Math.max(
    0,
    (end ? Date.parse(end) : Date.now()) - Date.parse(start),
  );
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.floor((ms % 60_000) / 1000)}s`;
}
function metricLabel(metric: string) {
  return metricLabels[metric] ? t(metricLabels[metric]) : humanize(metric.split(".").at(-1) || metric);
}
function resourceLabel(resource: string) {
  return resourceLabels[resource] ? t(resourceLabels[resource]) : humanize(resource || "unknown resource");
}
function conditionLabel(operator: string) {
  return operatorLabels[operator] ? t(operatorLabels[operator]) : operator;
}
function alertSentence(rule: AlertRule) {
  return `Notify when ${metricLabel(rule.metric)} ${conditionLabel(rule.operator)} ${formatThreshold(rule.metric, rule.threshold)}.`;
}
function formatThreshold(metric: string, threshold: string) {
  return metric.includes("wallet") || metric.endsWith("amount")
    ? formatMoney(threshold, "USD")
    : threshold;
}
function humanize(value: string) {
  return tToken(value || "Unknown");
}
function auditResult(log: AuditLog) {
  return String(log.metadata?.result || log.metadata?.status || "recorded");
}
function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}
function isObservabilityView(value: string | null): value is ObservabilityView {
  return (
    value === "overview" ||
    value === "activity" ||
    value === "automations" ||
    value === "audit" ||
    value === "platform"
  );
}
function nested(data: Record<string, unknown>, key: string) {
  return (
    key
      .split(".")
      .reduce<unknown>(
        (cursor, part) =>
          cursor && typeof cursor === "object"
            ? (cursor as Record<string, unknown>)[part]
            : undefined,
        data,
      ) ?? {}
  );
}
