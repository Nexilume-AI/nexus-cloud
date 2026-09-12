import { Ellipsis, FolderKanban, Loader2, Pencil, Play, Plus, Search, SlidersHorizontal, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { AgentPrivateRun, AgentPrivateRunPage, RunHistoryFilter } from "../lib/types";
import { AgentDisplayPanel } from "./AgentDisplayFrame";

type Props = {
  projects: Array<{ id: string; name: string }>;
  projectId: string;
  onProjectChange: (projectId: string) => void;
  projectChangeDisabled?: boolean;
  runs: AgentPrivateRun[];
  filter: RunHistoryFilter;
  counts?: AgentPrivateRunPage["counts"];
  onFilterChange: (filter: RunHistoryFilter) => void;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  error: boolean;
  onRetry: () => void;
  activeRunId: string;
  loading?: boolean;
  deletingId?: string;
  query: string;
  onQueryChange: (value: string) => void;
  onNew: () => void;
  onSelect: (runId: string) => void;
  onRename: (run: AgentPrivateRun) => void;
  onDelete: (run: AgentPrivateRun) => void;
};

const FILTERS: Array<[RunHistoryFilter, string]> = [
  ["all", "All runs"], ["running", "Running"], ["input_required", "Needs input"],
  ["completed_unread", "Completed unread"], ["failed", "Failed / stopped"],
];

type RunGroup = "needs_input" | "running" | "failed" | "recent";
const GROUP_LABELS: Record<RunGroup, string> = {
  needs_input: "Needs input",
  running: "Running",
  failed: "Failed",
  recent: "Recent",
};

function runState(run: AgentPrivateRun) {
  return run.attention_state || (run.completion_unread ? "completed_unread" : run.status);
}

function runGroup(run: AgentPrivateRun): RunGroup {
  const state = runState(run);
  if (state === "input_required") return "needs_input";
  if (["running", "starting"].includes(state)) return "running";
  if (["failed", "cancelled", "expired"].includes(state)) return "failed";
  return "recent";
}

function statusLabel(run: AgentPrivateRun) {
  const state = runState(run);
  return ({ running: "Running", starting: "Starting", input_required: "Needs input",
    completed_unread: "Completed · Unread", completed: "Completed", failed: "Failed",
    cancelled: "Cancelled", expired: "Expired" } as Record<string, string>)[state] || state.replaceAll("_", " ");
}

function relativeDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const ranges: Array<[number, Intl.RelativeTimeFormatUnit]> = [[86400 * 365, "year"], [86400 * 30, "month"], [86400 * 7, "week"], [86400, "day"], [3600, "hour"], [60, "minute"]];
  const formatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  for (const [size, unit] of ranges) if (Math.abs(seconds) >= size) return formatter.format(Math.round(seconds / size), unit);
  return formatter.format(seconds, "second");
}

export function PrivateAgentRunRail({
  projects,
  projectId,
  onProjectChange,
  projectChangeDisabled,
  runs, filter, counts, onFilterChange, hasMore, loadingMore, onLoadMore, error, onRetry,
  activeRunId,
  loading,
  deletingId,
  query,
  onQueryChange,
  onNew,
  onSelect,
  onRename,
  onDelete,
}: Props) {
  const total = counts?.all ?? runs.length;
  const [toolsOpen, setToolsOpen] = useState(() => Boolean(query || filter !== "all" || total >= 3));
  const autoOpenedTools = useRef(Boolean(query || filter !== "all" || total >= 3));
  useEffect(() => {
    if (!autoOpenedTools.current && total >= 3) {
      autoOpenedTools.current = true;
      setToolsOpen(true);
    }
  }, [total]);
  const grouped = useMemo(() => {
    const values = new Map<RunGroup, AgentPrivateRun[]>([["needs_input", []], ["running", []], ["failed", []], ["recent", []]]);
    for (const run of runs) values.get(runGroup(run))!.push(run);
    return [...values.entries()].filter(([, items]) => items.length) as Array<[RunGroup, AgentPrivateRun[]]>;
  }, [runs]);

  function clearTools() {
    onQueryChange("");
    onFilterChange("all");
  }

  return (
    <AgentDisplayPanel as="aside">
      <div className="border-b border-black/10 p-3">
        <div className="flex items-end gap-2">
          <label className="min-w-0 flex-1">
            <span className="mb-1.5 flex items-center gap-2 font-mono text-[10px] font-semibold uppercase tracking-[0.08em] text-[#6f6e69]">
              <FolderKanban size={13} aria-hidden="true" /> New runs in
            </span>
            <select aria-label="Private Display Project" value={projectId} disabled={projectChangeDisabled} onChange={(event) => onProjectChange(event.target.value)} className="min-h-11 w-full min-w-0 rounded-md border border-black/10 bg-white px-3 text-sm font-semibold text-[#34322d] focus-visible:border-[#6fa43f] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#6fa43f] disabled:cursor-not-allowed disabled:opacity-60 lg:min-h-10">
              <option value="">All accessible projects</option>
              {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
          </label>
          <button type="button" onClick={onNew} className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md bg-[#1a1a19] text-white transition hover:bg-[#2b2b28] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#52783b] lg:h-10 lg:w-10" aria-label="New run" title="New run"><Plus size={16} /></button>
        </div>

        <div className="mt-3 flex min-h-9 items-center justify-between border-t border-black/10 pt-3">
          <div className="min-w-0"><h2 className="text-sm font-semibold text-[#34322d]">Runs</h2><p className="text-xs text-[#858481]">{total.toLocaleString("en-US")} in this view</p></div>
          <button type="button" aria-expanded={toolsOpen} aria-controls="private-run-tools" onClick={() => setToolsOpen(value => !value)} className={`inline-flex h-11 w-11 items-center justify-center rounded-md border transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#52783b] lg:h-10 lg:w-10 ${toolsOpen || query || filter !== "all" ? "border-[#91c85f] bg-[#efffdd] text-[#386a22]" : "border-black/10 bg-white text-[#6f6e69] hover:bg-[#f8f8f7]"}`} aria-label={toolsOpen ? "Hide run search and filters" : "Search and filter runs"} title={toolsOpen ? "Hide search and filters" : "Search and filter runs"}><SlidersHorizontal size={15} /></button>
        </div>

        {toolsOpen ? <div id="private-run-tools" className="mt-2 grid gap-2">
          <label className="flex min-h-11 items-center gap-2 rounded-md border border-black/10 bg-white px-3 focus-within:border-[#6fa43f] lg:min-h-10">
            <Search size={14} className="text-[#858481]" /><span className="sr-only">Search run history</span>
            <input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="Search runs" className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[#aaa9a5]" />
            {query ? <button type="button" onClick={() => onQueryChange("")} className="inline-flex h-10 w-10 items-center justify-center rounded text-[#858481] hover:bg-black/5 lg:h-8 lg:w-8" aria-label="Clear run search"><X size={14} /></button> : null}
          </label>
          <div className="flex items-center gap-2">
            <label className="min-w-0 flex-1"><span className="sr-only">Filter Run history</span><select value={filter} onChange={(event) => onFilterChange(event.target.value as RunHistoryFilter)} className="min-h-11 w-full min-w-0 rounded-md border border-black/10 bg-white px-3 text-sm text-[#535350] focus-visible:border-[#6fa43f] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#6fa43f] lg:min-h-10">{FILTERS.map(([id, label]) => <option key={id} value={id}>{label}{counts ? ` (${counts[id] ?? 0})` : ""}</option>)}</select></label>
            {query || filter !== "all" ? <button type="button" onClick={clearTools} className="min-h-11 shrink-0 rounded-md px-2 text-xs font-semibold text-[#535350] underline decoration-black/20 underline-offset-4 hover:text-[#1a1a19] lg:min-h-10">Clear</button> : null}
          </div>
        </div> : null}
        {error ? <div role="status" className="mt-2 text-xs text-amber-800">History could not refresh. Loaded conversations are kept.<button type="button" onClick={onRetry} className="ml-1 min-h-10 underline">Retry history</button></div> : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2" aria-label="Run history">
        {loading ? <div className="flex items-center justify-center gap-2 px-3 py-8 text-sm text-[#858481]"><Loader2 className="animate-spin" size={15} /> Loading runs</div> : null}
        {!loading && runs.length === 0 ? <div className="px-4 py-8 text-center"><Play className="mx-auto text-[#aaa9a5]" size={21} /><div className="mt-2 text-sm font-medium text-[#535350]">{query || filter !== "all" ? "No matching runs" : "No previous runs"}</div><p className="mt-1 text-xs leading-5 text-[#858481]">{query || filter !== "all" ? "Clear the search or choose another status." : "Start from the message box when you are ready."}</p>{query || filter !== "all" ? <button type="button" onClick={clearTools} className="mt-2 min-h-10 text-sm font-semibold text-[#386a22] underline underline-offset-4">Clear filters</button> : null}</div> : null}

        <div className="grid gap-3">
          {grouped.map(([group, items]) => <section key={group} aria-labelledby={`run-group-${group}`}>
            <div className="mb-1 flex items-center justify-between px-2"><h3 id={`run-group-${group}`} className="font-mono text-[10px] font-semibold uppercase tracking-[0.08em] text-[#858481]">{GROUP_LABELS[group]}</h3><span className="text-xs tabular-nums text-[#aaa9a5]">{items.length}</span></div>
            <div className="grid gap-1">
              {items.map((run) => {
                const active = run.id === activeRunId;
                const state = runState(run);
                return <div key={run.id} data-history-run={run.id} className={`group flex min-w-0 items-stretch rounded-md border transition ${active ? "border-[#91c85f] bg-[#efffdd]" : "border-transparent hover:border-black/10 hover:bg-black/[0.025]"}`}>
                  <button type="button" onClick={() => onSelect(run.id)} aria-current={active ? "page" : undefined} className="min-w-0 flex-1 px-3 py-2.5 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[#52783b]">
                    <div className="truncate text-sm font-semibold text-[#34322d]" title={run.title}>{run.title}</div><div className="mt-1 truncate text-xs text-[#858481]">{run.preview || run.tool_name}</div>
                    <div className="mt-1 flex min-w-0 items-center gap-2 text-xs text-[#858481]"><span className={`min-w-0 truncate ${state === "input_required" ? "font-semibold text-amber-800" : run.completion_unread ? "font-semibold text-[#386a22]" : ""}`}>{run.completion_unread ? <span aria-hidden="true" className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-[#5e9b2d]" /> : null}{statusLabel(run)}</span><span aria-hidden="true" className="text-[#c3c2bd]">·</span><time className="shrink-0 tabular-nums" dateTime={run.updated_at} title={new Date(run.updated_at).toLocaleString()}>{relativeDate(run.updated_at)}</time></div>
                  </button>
                  <details className="relative my-1 mr-1 shrink-0 self-start" onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.removeAttribute("open"); }} onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); event.currentTarget.removeAttribute("open"); event.currentTarget.querySelector("summary")?.focus(); } }}><summary className="inline-flex h-11 w-11 cursor-pointer list-none items-center justify-center rounded-md text-[#858481] hover:bg-white hover:text-[#34322d] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[#52783b] lg:h-10 lg:w-10 [&::-webkit-details-marker]:hidden" aria-label={`Actions for ${run.title}`} title="Run actions"><Ellipsis size={16} /></summary><div className="absolute right-0 top-10 z-20 min-w-36 overflow-hidden rounded-md border border-black/10 bg-white py-1 shadow-lg"><button type="button" onClick={() => onRename(run)} className="flex min-h-11 w-full items-center gap-2 px-3 text-left text-sm text-[#535350] hover:bg-[#f8f8f7] lg:min-h-10"><Pencil size={14} /> Rename</button><button type="button" onClick={() => onDelete(run)} disabled={deletingId === run.id} className="flex min-h-11 w-full items-center gap-2 px-3 text-left text-sm text-[#9b2c2c] hover:bg-[#fff5f5] disabled:opacity-50 lg:min-h-10">{deletingId === run.id ? <Loader2 className="animate-spin" size={14} /> : <Trash2 size={14} />} Remove</button></div></details>
                </div>;
              })}
            </div>
          </section>)}
        </div>
        {hasMore ? <button type="button" onClick={onLoadMore} disabled={loadingMore} className="mt-3 min-h-11 w-full rounded-md border border-black/15 text-sm">{loadingMore ? "Loading…" : "Load more runs"}</button> : null}
      </div>
    </AgentDisplayPanel>
  );
}
