import { useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Archive, BellRing, Check, ChevronRight, CircleAlert, CircleCheck, Clock3, ExternalLink, Inbox, RefreshCw, Search, Settings2, X } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "../app/AuthContext";
import { NexilumeDialog } from "../components/NexilumeControls";
import { api } from "../lib/api";
import type { InboxCategory, InboxItem, InboxPreferences } from "../lib/inboxTypes";
import { enableDesktopNotifications } from "../lib/pushNotifications";
import "./inbox.css";

const states = [
  ["", "All visible"], ["needs_action", "Needs action"], ["in_progress", "In progress"],
  ["failed", "Failed"], ["completed_unread", "Completed unread"], ["resolved", "Resolved"], ["snoozed", "Snoozed"]
] as const;
const categories = [["", "All categories"], ["agent", "Agent"], ["background", "Background tasks"], ["approval", "Approvals"], ["operations", "Operations"]] as const;

export function InboxPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const queries = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(params.get("settings") === "notifications");
  const [inspectorOpen, setInspectorOpen] = useState(Boolean(params.get("item")));
  const [compactInspector, setCompactInspector] = useState(() => typeof window !== "undefined" && window.matchMedia("(max-width: 1439px)").matches);
  const inspectorClose = useRef<HTMLButtonElement>(null);
  const inspectorPanel = useRef<HTMLElement>(null);
  const inspectorTrigger = useRef<HTMLButtonElement>(null);
  const ctx = { ...auth.apiContext, projectId: null };
  const filter = { state: params.get("state"), category: params.get("category"), project: params.get("project"), ownership: params.get("ownership"), q: params.get("q") };
  const key = ["private", "work-inbox", auth.user?.user_id, auth.tenantId];
  const summary = useQuery({ queryKey: [...key, "summary"], queryFn: ({ signal }) => api.inboxSummary(ctx, signal), refetchInterval: 30_000 });
  const list = useInfiniteQuery({ queryKey: [...key, "items", filter],
    initialPageParam: params.get("cursor") as string | null,
    queryFn: ({ signal, pageParam }) => api.inboxItems(ctx, { ...filter, cursor: pageParam }, signal),
    getNextPageParam: page => page.next_cursor ?? undefined, maxPages: 10, retry: false, refetchInterval: 30_000 });
  const rows = useMemo(() => [...new Map((list.data?.pages.flatMap(page => page.results) ?? []).map(item => [item.id, item])).values()], [list.data]);
  const selectedId = params.get("item");
  const selectedRow = rows.find(item => item.id === selectedId);
  const detail = useQuery({ queryKey: [...key, "item", selectedId],
    queryFn: ({ signal }) => api.inboxItem(ctx, selectedId!, signal),
    enabled: Boolean(selectedId && !selectedRow), retry: false, refetchInterval: 30_000 });
  const selected = selectedId ? selectedRow ?? detail.data ?? null : rows[0] ?? null;

  const urlSearch = params.get("q") ?? "";
  useEffect(() => setSearch(urlSearch), [urlSearch]);
  useEffect(() => {
    if (search === urlSearch) return;
    const timer = window.setTimeout(() => {
      const next = new URLSearchParams(params);
      if (search) next.set("q", search); else next.delete("q");
      next.delete("cursor"); next.delete("item");
      setInspectorOpen(false); setParams(next, { replace: true });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search, urlSearch, params, setParams]);

  useEffect(() => {
    if (!list.data) return;
    // A notification may point outside the current page. Fetch it by ID rather
    // than silently replacing the user's destination with the first row.
    if (selectedId || !selected) return;
    const next = new URLSearchParams(params);
    if (selected) next.set("item", selected.id); else next.delete("item");
    setParams(next, { replace: true });
  }, [list.data, params, selected, selectedId, setParams]);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 1439px)");
    const change = () => setCompactInspector(media.matches);
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);

  useEffect(() => {
    if (!inspectorOpen || !compactInspector) return;
    const focusFrame = window.requestAnimationFrame(() => inspectorClose.current?.focus());
    const closeInspector = () => {
      setInspectorOpen(false);
      window.requestAnimationFrame(() => inspectorTrigger.current?.focus());
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeInspector();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = [...(inspectorPanel.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])') ?? [])];
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", escape);
    return () => { window.cancelAnimationFrame(focusFrame); window.removeEventListener("keydown", escape); };
  }, [compactInspector, inspectorOpen]);

  function update(name: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value); else next.delete(name);
    next.delete("cursor"); next.delete("item"); setInspectorOpen(false); setParams(next, { replace: true });
  }
  async function refresh() { await queries.invalidateQueries({ queryKey: key }); }
  async function act(item: InboxItem, action: "open" | "read" | "snooze" | "archive") {
    setBusy(action); setError("");
    try {
      if (action === "read") await api.readInboxItem(ctx, item.id);
      if (action === "snooze") { await api.snoozeInboxItem(ctx, item.id, new Date(Date.now() + 4 * 60 * 60_000).toISOString()); setInspectorOpen(false); }
      if (action === "archive") { await api.archiveInboxItem(ctx, item.id); setInspectorOpen(false); }
      if (action === "open") {
        const target = await api.openInboxItem(ctx, item.id);
        if (target.tenant_id !== auth.tenantId || !target.url.startsWith("/") || target.url.startsWith("//")) throw new Error("This destination is unavailable.");
        flushSync(() => auth.setProjectId(target.project_id)); navigate(target.url); return;
      }
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update this item."); }
    finally { setBusy(""); }
  }

  const status = summary.data;
  return <section className="work-inbox-page" aria-labelledby="work-inbox-title">
    <header className="work-inbox-page__header">
      <div><p className="work-inbox-page__eyebrow">OPERATE · PERSONAL WORK</p><h1 id="work-inbox-title">Work Inbox</h1>
        <p>One place for tasks that need you. Approvals, replies, and retries still happen in their source workspace.</p></div>
      <div className="work-inbox-page__header-actions"><button className="btn btn-ghost" type="button" onClick={() => void refresh()}><RefreshCw size={16} /> Refresh</button>
        <button className="btn btn-secondary" type="button" onClick={() => setSettingsOpen(true)}><Settings2 size={16} /> Notifications</button></div>
    </header>
    <div className="work-inbox-evidence" aria-label="Work Inbox status">
      <Evidence label="Needs attention" value={status?.needs_attention} tone="danger" />
      <Evidence label="Running" value={status?.in_progress} />
      <Evidence label="Failed" value={status?.failed} tone="danger" />
      <Evidence label="Completed unread" value={status?.completed_unread} />
      <Evidence label="Desktop notifications" value={status?.enabled_push_devices ? "On" : status?.push_enabled ? "Available" : "Off"} />
    </div>
    <div className="work-inbox-filters" aria-label="Inbox filters">
      <label><span className="sr-only">Search Inbox</span><Search size={15} /><input value={search} onChange={event => setSearch(event.target.value)} placeholder="Search work" /></label>
      <select aria-label="State" value={params.get("state") ?? ""} onChange={event => update("state", event.target.value)}>{states.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      <select aria-label="Category" value={params.get("category") ?? ""} onChange={event => update("category", event.target.value)}>{categories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      <select aria-label="Project" value={params.get("project") ?? ""} onChange={event => update("project", event.target.value)}><option value="">All authorized Projects</option>{auth.projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select>
      <select aria-label="Ownership" value={params.get("ownership") ?? ""} onChange={event => update("ownership", event.target.value)}><option value="">Personal + role</option><option value="personal">Personal</option><option value="role">Shared role queue</option></select>
    </div>
    {error ? <p className="work-inbox-page__error" role="alert">{error}</p> : null}
    {list.isError ? <p className="work-inbox-page__error" role="alert">Some Inbox data is unavailable. Previously loaded items remain visible.</p> : null}
    {summary.isError ? <p className="work-inbox-page__error" role="alert">Inbox counts could not refresh. <button type="button" onClick={() => void summary.refetch()}>Retry counts</button></p> : null}
    {detail.isError ? <p className="work-inbox-page__error" role="alert">This work item is unavailable or you no longer have access. <button type="button" onClick={() => update("item", "")}>Return to work list</button></p> : null}
    <div className="work-inbox-workspace">
      <div className="work-inbox-track" aria-label="Work items">
        {list.isPending ? <p role="status">Loading work…</p> : null}
        {list.data && !rows.length ? <div className="work-inbox-empty"><Inbox size={30} /><h2>No matching work</h2><p>Clear filters or come back when a source task changes.</p></div> : null}
        {rows.map(item => <button key={item.id} type="button" className="work-inbox-row" aria-current={selected?.id === item.id ? "true" : undefined}
          onClick={event => {
            inspectorTrigger.current = event.currentTarget;
            const next = new URLSearchParams(params);
            next.set("item", item.id);
            setParams(next, { replace: true });
            flushSync(() => setInspectorOpen(true));
            window.requestAnimationFrame(() => window.requestAnimationFrame(() => inspectorClose.current?.focus()));
          }}>
          <StateIcon state={item.state} /><span className="work-inbox-row__body"><strong>{item.title}</strong><small>{item.resource_name || item.category} · {item.project_name ?? "Organization"}</small>
            <span>{item.message}</span></span><span className="work-inbox-row__meta"><em>{item.ownership === "role" ? "Shared role queue" : "Personal"}</em><time>{relative(item.occurred_at)}</time></span><ChevronRight size={16} />
        </button>)}
        {list.hasNextPage ? <button className="btn btn-secondary work-inbox-load" disabled={list.isFetching} type="button" onClick={() => void list.fetchNextPage()}>{list.isFetchingNextPage ? "Loading…" : "Load more"}</button> : null}
        {list.isFetchNextPageError ? <button className="btn btn-secondary" type="button" onClick={() => void list.fetchNextPage()}>Retry loading more</button> : null}
      </div>
      <aside ref={inspectorPanel} className={`work-inbox-inspector${inspectorOpen ? " is-open" : ""}`} aria-label="Selected work item"
        role={compactInspector && inspectorOpen ? "dialog" : undefined} aria-modal={compactInspector && inspectorOpen ? "true" : undefined}>
        <button ref={inspectorClose} type="button" className="work-inbox-inspector__close btn btn-ghost" aria-label="Close work details" onClick={() => { setInspectorOpen(false); window.requestAnimationFrame(() => inspectorTrigger.current?.focus()); }}><X size={17} /> Close</button>
        {selected ? <><div className="work-inbox-inspector__identity"><StateIcon state={selected.state} /><div><p>{selected.category.toUpperCase()}</p><h2>{selected.title}</h2></div></div>
          <p className="work-inbox-inspector__message">{selected.message}</p>
          <dl><div><dt>Status</dt><dd>{selected.state.replace("_", " ")}</dd></div><div><dt>Queue</dt><dd>{selected.ownership === "role" ? "Shared role queue" : "Personal"}</dd></div>
            <div><dt>Project</dt><dd>{selected.project_name ?? "Organization-wide"}</dd></div><div><dt>Source</dt><dd>{selected.resource_name || selected.category}</dd></div><div><dt>Updated</dt><dd>{new Date(selected.occurred_at).toLocaleString()}</dd></div></dl>
          <div className="work-inbox-inspector__history" aria-label="Status history"><h3>Status history</h3><ol>
            <li><span>Occurred</span><time>{new Date(selected.occurred_at).toLocaleString()}</time></li>
            {selected.read_at ? <li><span>Read</span><time>{new Date(selected.read_at).toLocaleString()}</time></li> : null}
            {selected.snoozed_until ? <li><span>Remind later</span><time>{new Date(selected.snoozed_until).toLocaleString()}</time></li> : null}
            {selected.resolved_at ? <li><span>Resolved</span><time>{new Date(selected.resolved_at).toLocaleString()}</time></li> : null}
          </ol></div>
          <div className="work-inbox-inspector__actions"><button className="btn btn-primary" disabled={Boolean(busy)} type="button" onClick={() => void act(selected, "open")}><ExternalLink size={16} /> {selected.action_label}</button>
            {!selected.is_read ? <button className="btn btn-secondary" disabled={Boolean(busy)} type="button" onClick={() => void act(selected, "read")}><Check size={16} /> Mark read</button> : null}
            {["needs_action", "failed"].includes(selected.state) ? <button className="btn btn-secondary" disabled={Boolean(busy)} type="button" onClick={() => void act(selected, "snooze")}><Clock3 size={16} /> Remind in 4 hours</button> : null}
            {selected.can_archive ? <button className="btn btn-ghost" disabled={Boolean(busy)} type="button" onClick={() => void act(selected, "archive")}><Archive size={16} /> Archive</button> : null}</div>
          <p className="work-inbox-inspector__note">Opening this item does not approve, reply, or retry anything. The source page applies current permissions and four-eyes controls.</p>
        </> : <div className="work-inbox-empty"><Inbox size={30} /><h2>Select work</h2><p>Its safe summary and next action will appear here.</p></div>}
      </aside>
    </div>
    <NotificationSettings open={settingsOpen} onClose={() => { setSettingsOpen(false); const next = new URLSearchParams(params); next.delete("settings"); setParams(next, { replace: true }); }} />
  </section>;
}

function Evidence({ label, value, tone }: { label: string; value?: number | string; tone?: string }) {
  return <div data-tone={tone}><span>{label}</span><strong>{value ?? "—"}</strong></div>;
}
function StateIcon({ state }: { state: InboxItem["state"] }) {
  return <span className="work-inbox-state" data-state={state} aria-label={state.replace("_", " ")}>{state === "failed" || state === "needs_action" ? <CircleAlert size={17} /> : state === "in_progress" ? <Clock3 size={17} /> : <CircleCheck size={17} />}</span>;
}
function relative(value: string) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60_000));
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h`;
  return `${Math.floor(minutes / 1440)}d`;
}

function NotificationSettings({ open, onClose }: { open: boolean; onClose: () => void }) {
  const auth = useAuth();
  const queries = useQueryClient();
  const ctx = { ...auth.apiContext, projectId: null };
  const preference = useQuery({ queryKey: ["private", "work-inbox-preferences", auth.tenantId], queryFn: () => api.inboxPreferences(ctx), enabled: open });
  const devices = useQuery({ queryKey: ["private", "work-inbox-devices", auth.user?.user_id], queryFn: () => api.pushDevices(ctx), enabled: open });
  const [draft, setDraft] = useState<InboxPreferences | null>(null);
  const [busy, setBusy] = useState("");
  const [browserPermission, setBrowserPermission] = useState<NotificationPermission | "unsupported">(() =>
    typeof Notification === "undefined" ? "unsupported" : Notification.permission
  );
  useEffect(() => { if (preference.data) setDraft(preference.data); }, [preference.data]);
  useEffect(() => { if (open) setBrowserPermission(typeof Notification === "undefined" ? "unsupported" : Notification.permission); }, [open]);
  async function save() {
    if (!draft) return; setBusy("save");
    try { await api.updateInboxPreferences(ctx, draft); await preference.refetch(); toast.success("Notification preferences saved"); }
    catch (reason) { toast.error(reason instanceof Error ? reason.message : "Could not save preferences"); }
    finally { setBusy(""); }
  }
  async function enable() {
    if (!draft?.push_enabled || !draft.vapid_public_key || !auth.user?.user_id) return;
    setBusy("enable");
    try { await enableDesktopNotifications(ctx, auth.user.user_id, draft.vapid_public_key); await devices.refetch(); toast.success("Desktop notifications enabled"); }
    catch (reason) { toast.error(reason instanceof Error ? reason.message : "Could not enable desktop notifications"); }
    finally { setBrowserPermission(typeof Notification === "undefined" ? "unsupported" : Notification.permission); setBusy(""); }
  }
  return <NexilumeDialog open={open} onClose={onClose} title="Notification settings" eyebrow="WORK INBOX" description="Choose what reaches each browser. Running progress stays in Inbox.">
    {!draft ? <p role="status">Loading preferences…</p> : <div className="inbox-settings">
      <section><h3>Desktop notifications</h3><p>Permission is requested only when you enable this browser. Lock-screen text never includes business details.</p>
        {!draft.push_enabled ? <p className="inbox-settings__notice">Web Push is not enabled by the Nexus administrator. Work Inbox remains available.</p>
          : browserPermission === "denied" ? <p className="inbox-settings__notice">Notifications are blocked for this site. Restore permission in your browser site settings, then reopen this panel.</p>
          : browserPermission === "unsupported" ? <p className="inbox-settings__notice">This browser does not support desktop notifications.</p>
          : <button className="btn btn-primary" type="button" disabled={Boolean(busy)} onClick={() => void enable()}><BellRing size={16} /> Enable this browser</button>}
        <ul>{devices.data?.results.map(device => <li key={device.id}><span><strong>{device.device_name}</strong><small>{device.enabled ? "Enabled" : "Disabled"} · {new Date(device.last_seen_at).toLocaleString()}</small></span>{device.enabled ? <button className="btn btn-ghost" type="button" onClick={async () => { await api.unsubscribePush(ctx, device.id); await devices.refetch(); }}>Disable</button> : null}</li>)}</ul>
      </section>
      <section><h3>Categories</h3>{(Object.keys(draft.categories) as InboxCategory[]).map(key => <label className="inbox-settings__toggle" key={key}><input type="checkbox" checked={draft.categories[key]} onChange={event => setDraft({ ...draft, categories: { ...draft.categories, [key]: event.target.checked } })} /><span>{key === "background" ? "Background tasks" : key[0].toUpperCase() + key.slice(1)}</span></label>)}</section>
      <section><h3>Events</h3>{Object.entries(draft.events).map(([key, value]) => <label className="inbox-settings__toggle" key={key}><input type="checkbox" checked={value} onChange={event => setDraft({ ...draft, events: { ...draft.events, [key]: event.target.checked } })} /><span>{key.replace("_", " ")}</span></label>)}</section>
      <section><h3>Quiet hours</h3><label className="inbox-settings__toggle"><input type="checkbox" checked={draft.dnd_enabled} onChange={event => setDraft({ ...draft, dnd_enabled: event.target.checked })} /><span>Enable quiet hours</span></label>
        <div className="inbox-settings__times"><label>Start<input type="time" value={draft.dnd_start?.slice(0, 5) ?? "22:00"} onChange={event => setDraft({ ...draft, dnd_start: event.target.value })} /></label><label>End<input type="time" value={draft.dnd_end?.slice(0, 5) ?? "08:00"} onChange={event => setDraft({ ...draft, dnd_end: event.target.value })} /></label><label>Timezone<input value={draft.timezone} onChange={event => setDraft({ ...draft, timezone: event.target.value })} /></label></div>
        <label className="inbox-settings__toggle"><input type="checkbox" checked={draft.urgent_bypass} onChange={event => setDraft({ ...draft, urgent_bypass: event.target.checked })} /><span>Allow urgent exceptions</span></label></section>
      <footer><button className="btn btn-primary" disabled={Boolean(busy)} type="button" onClick={() => void save()}>{busy === "save" ? "Saving…" : "Save preferences"}</button></footer>
    </div>}
  </NexilumeDialog>;
}
