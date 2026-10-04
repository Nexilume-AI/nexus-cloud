import { t, useLocale, getLocale } from "../localization";
import { useEffect, useRef, useState } from "react";
import { createPortal, flushSync } from "react-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Bell, BellOff, Check, CircleAlert, CircleCheck, Clock3, ExternalLink, RefreshCw } from "lucide-react";
import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import type { InboxItem } from "../lib/inboxTypes";
import { NexilumeDialog } from "./NexilumeControls";
import "./notifications.css";

type DrawerMode = "attention" | "progress" | "recent";

export function NotificationCenter() {
  useLocale();
  const auth = useAuth();
  return <ScopedWorkInbox key={`${auth.user?.user_id}:${auth.tenantId}`} />;
}

function ScopedWorkInbox() {
  useLocale();
  const auth = useAuth();
  const navigate = useNavigate();
  const queries = useQueryClient();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<DrawerMode>("attention");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const firstControl = useRef<HTMLButtonElement>(null);
  const ctx = { ...auth.apiContext, projectId: null };
  const enabled = auth.isAuthenticated && Boolean(auth.tenantId);
  const key = ["private", "work-inbox", auth.user?.user_id, auth.tenantId];
  const summary = useQuery({ queryKey: [...key, "summary"], queryFn: ({ signal }) => api.inboxSummary(ctx, signal), enabled,
    retry: false, staleTime: 10_000, refetchInterval: open ? false : 30_000 });
  const state = mode === "attention" ? "needs_action" : mode === "progress" ? "in_progress" : undefined;
  const list = useQuery({ queryKey: [...key, "drawer", mode], queryFn: ({ signal }) => api.inboxItems(ctx, { state }, signal),
    enabled: enabled && open, retry: false, staleTime: 5_000, refetchInterval: 30_000 });

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let controller: AbortController | undefined;
    let timer: number | undefined;
    let delay = 1_000;
    let generation = 0;
    const connect = async (current: number) => {
      if (stopped || document.hidden || current !== generation) return;
      controller = new AbortController();
      try {
        await api.streamInbox(ctx, () => { void queries.invalidateQueries({ queryKey: key }); }, controller.signal);
        delay = 1_000;
      } catch { delay = Math.min(delay * 2, 30_000); }
      if (!stopped && !document.hidden && current === generation) timer = window.setTimeout(() => void connect(current), delay);
    };
    const visibility = () => {
      generation += 1;
      if (timer) clearTimeout(timer);
      controller?.abort();
      if (!document.hidden) void connect(generation);
    };
    document.addEventListener("visibilitychange", visibility);
    void connect(generation);
    return () => { stopped = true; controller?.abort(); if (timer) clearTimeout(timer); document.removeEventListener("visibilitychange", visibility); };
    // Scope identity is intentionally the reconnect boundary.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, auth.tenantId, auth.user?.user_id]);

  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const listener = (event: MessageEvent) => {
      if (event.data?.type === "nexus-inbox-open" && typeof event.data.url === "string" && /^\/inbox(?:\?|$)/.test(event.data.url)) navigate(event.data.url);
      if (event.data?.type === "nexus-inbox-invalidate") void queries.invalidateQueries({ queryKey: key });
    };
    navigator.serviceWorker.addEventListener("message", listener);
    return () => navigator.serviceWorker.removeEventListener("message", listener);
  }, [key, navigate, queries]);

  async function refresh() { await queries.invalidateQueries({ queryKey: key }); }
  async function act(item: InboxItem, operation: "read" | "open" | "snooze") {
    if (busy) return;
    setBusy(item.id); setError("");
    try {
      if (operation === "read") await api.readInboxItem(ctx, item.id);
      if (operation === "snooze") await api.snoozeInboxItem(ctx, item.id, new Date(Date.now() + 60 * 60_000).toISOString());
      if (operation === "open") {
        const target = await api.openInboxItem(ctx, item.id);
        if (target.tenant_id !== auth.tenantId || !target.url.startsWith("/") || target.url.startsWith("//")) throw new Error("This Inbox destination is unavailable.");
        flushSync(() => { auth.setProjectId(target.project_id); setOpen(false); });
        navigate(target.url);
      }
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update Work Inbox."); }
    finally { setBusy(null); }
  }

  // The drawer opens on Needs attention. Showing all unread historical Run
  // outcomes here made the badge disagree with the queue the user actually
  // sees (and quickly inflated it to 99+).
  const count = summary.data?.badge_count ?? 0;
  const organization = auth.tenants.find(item => item.id === auth.tenantId)?.name ?? "Current organization";
  return <>
    <button type="button" className="nexilume-shell-icon-button notification-trigger"
      aria-label={t("Work Inbox{{0}}", { 0: count ? `, ${count} need attention` : "" })}
      aria-haspopup="dialog" aria-expanded={open} title={t("Work Inbox{{0}}", { 0: count ? `, ${count} need attention` : "" })} onClick={() => { setOpen(true); setError(""); }}>
      <Bell size={17} aria-hidden="true" />
      {count ? <span className="notification-trigger__count" aria-hidden="true">{count > 99 ? "99+" : count}</span> : summary.isError ? <span aria-hidden="true" className="notification-trigger__error">!</span> : null}
    </button>
    {createPortal(<NexilumeDialog open={open} onClose={() => setOpen(false)} title={t("Work Inbox")} eyebrow={organization}
      description={t("Personal work and shared role queues across authorized Projects. Reading never completes the source task.")}
      variant="drawer" initialFocusRef={firstControl}>
      <div className="notification-center work-inbox-drawer">
        <div className="work-inbox-drawer__modes" role="tablist" aria-label={t("Inbox summary")}>
          {(["attention", "progress", "recent"] as DrawerMode[]).map((value, index) => <button key={value} ref={index === 0 ? firstControl : undefined}
            type="button" role="tab" aria-selected={mode === value} onClick={() => setMode(value)}>
            {value === "attention" ? t("Needs attention · {{0}}", { 0: summary.data?.needs_attention ?? 0 }) : value === "progress" ? t("In progress · {{0}}", { 0: summary.data?.in_progress ?? 0 }) : t("Recent")}
          </button>)}
        </div>
        {error ? <p role="alert" className="notification-center__error">{error}</p> : null}
        {list.isError ? <div role="alert" className="notification-center__error">{t("Work Inbox could not refresh.")}<button className="btn btn-secondary" onClick={() => void refresh()} type="button">{t("Retry")}</button></div> : null}
        {list.isPending ? <p role="status">{t("Loading Work Inbox…")}</p> : null}
        {list.data && !list.data.results.length ? <div className="notification-center__empty"><BellOff size={28} aria-hidden="true" />
          <h3>{mode === "attention" ? t("Nothing needs your attention") : mode === "progress" ? t("No work is running") : t("No recent work")}</h3>
          <p>{t("Agent actions, background tasks, approvals, and actionable operations will appear here.")}</p>
        </div> : null}
        <ol className="notification-center__list">
          {list.data?.results.slice(0, 12).map(item => <li key={item.id} className={!item.is_read ? "is-unread" : ""}>
            <div className="notification-center__status" aria-hidden="true">{item.state === "needs_action" || item.state === "failed" ? <CircleAlert size={19} /> : item.state === "in_progress" ? <Clock3 size={19} /> : <CircleCheck size={19} />}</div>
            <div className="notification-center__item">
              <div className="notification-center__heading"><h3>{item.title}</h3><span>{item.ownership === "role" ? t("Shared role queue") : item.state.replace("_", " ")}</span></div>
              <p className="notification-center__resource">{item.resource_name || item.category} · {item.project_name ?? t("Organization")}</p>
              <p>{item.message}</p>
              <time dateTime={item.occurred_at}>{new Date(item.occurred_at).toLocaleString(getLocale(), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time>
              <div className="notification-center__actions">
                <button className="btn btn-secondary" type="button" disabled={Boolean(busy)} onClick={() => void act(item, "open")}><ExternalLink size={15} /> {item.action_label}</button>
                {!item.is_read ? <button type="button" className="btn btn-ghost" disabled={Boolean(busy)} onClick={() => void act(item, "read")}><Check size={15} />{" "}{t("Mark read")}</button> : null}
                {["needs_action", "failed"].includes(item.state) ? <button type="button" className="btn btn-ghost" disabled={Boolean(busy)} onClick={() => void act(item, "snooze")}><Clock3 size={15} />{" "}{t("Remind in 1h")}</button> : null}
              </div>
            </div>
          </li>)}
        </ol>
        <footer className="notification-center__footer work-inbox-drawer__footer">
          <button className="btn btn-secondary" type="button" onClick={() => { setOpen(false); navigate("/inbox"); }}>{t("Open Work Inbox")}</button>
          <button className="btn btn-ghost" type="button" onClick={() => { setOpen(false); navigate("/inbox?settings=notifications"); }}>{t("Notification settings")}</button>
          <button className="btn btn-ghost" type="button" aria-label={t("Refresh Work Inbox")} onClick={() => void refresh()}><RefreshCw size={15} /></button>
        </footer>
      </div>
    </NexilumeDialog>, document.body)}
  </>;
}
