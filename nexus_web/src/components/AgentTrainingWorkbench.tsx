import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, request, type ApiContext } from "../lib/api";
import { t, useLocale } from "../localization";
import { downloadApiFile } from "./DataAssetPresentation";

type Message = { role: string; content: string; weight?: number; tool_call_id?: string; tool_calls?: { id: string; function: { name: string; arguments: string } }[] };
type Annotation = { decision: string; outcome: string; score: number | null; feedback: string; feedback_source: string; trainable_assistant_indices: number[] | null };
type Sample = { id: string; revision: number; context: { task_id?: string; client?: string }; execution_status: string; message_count: number;
  warnings: string[]; annotation: Annotation; source: { kind: string }; trajectory?: { messages: Message[]; tools: unknown[] } };
type Page<T> = { items: T[]; total: number; next_cursor?: string | null; summary: Record<string, number> };
type Export = { id: string; version: string; training: { counts: Record<string, number>; skipped_count: number }; files: { id: string; file_name: string; download_url: string }[] };

function label(value: string) {
  const labels: Record<string, string> = {
    pending: "Pending", approved: "Approved", excluded: "Excluded",
    unknown: "Unknown", success: "Success", failure: "Failure",
    completed: "Completed", failed: "Failed", cancelled: "Cancelled",
    external_client: "Client trajectory", nexus_run: "Nexus run", correction: "Corrected sample",
    missing_tool_result: "A tool call has no matching result",
    orphan_tool_result: "A tool result has no matching call",
    missing_user_message: "User input is missing", missing_assistant_message: "Assistant output is missing",
    missing_final_answer: "The final answer is missing", missing_tool_definition: "A tool definition is missing",
    invalid_tool_arguments: "Tool arguments are not valid JSON", invalid_tool_schema: "A tool parameter schema is invalid",
    missing_task_or_session: "Add a task or session ID for grouping",
    unmatched_message_delta: "A message fragment has no start event", unmatched_tool_delta: "A tool fragment has no start event",
  };
  return labels[value] ? t(labels[value]) : value;
}
const example = { schema: "nexus.trajectory.v1", context: { task_id: "example-task", session_id: "example-session", client: "my-agent" },
  execution_status: "completed", tools: [{ type: "function", function: { name: "add", description: "Add two numbers", parameters: { type: "object", properties: { a: { type: "number" }, b: { type: "number" } }, required: ["a", "b"] } } }],
  messages: [{ role: "user", content: "2 + 3 = ?" }, { role: "assistant", content: "", tool_calls: [{ id: "call_1", type: "function", function: { name: "add", arguments: '{"a":2,"b":3}' } }] },
    { role: "tool", tool_call_id: "call_1", content: "5" }, { role: "assistant", content: "2 + 3 = 5." }] };

export function AgentTrainingWorkbench({ ctx, datasetId, canManage, isPrivate }: { ctx: ApiContext; datasetId: string; canManage: boolean; isPrivate: boolean }) {
  useLocale();
  const cache = useQueryClient();
  const base = `/api/v1/datasets/${datasetId}/training`;
  const [cursor, setCursor] = useState("");
  const [filter, setFilter] = useState("");
  const [outcome, setOutcome] = useState("");
  const [selected, setSelected] = useState("");
  const [mode, setMode] = useState<"samples" | "import" | "exports">("samples");
  const [busy, setBusy] = useState(false);
  const [json, setJson] = useState("");
  const [rights, setRights] = useState(false);
  const [importKey, setImportKey] = useState(() => crypto.randomUUID());
  const [correctionOf, setCorrectionOf] = useState<string | null>(null);
  const [agent, setAgent] = useState("");
  const [runs, setRuns] = useState<string[]>([]);
  const [importResults, setImportResults] = useState<string[]>([]);
  const [seed, setSeed] = useState("nexus-training-v1");
  const [validation, setValidation] = useState(10);
  const [test, setTest] = useState(10);
  const [includeFailures, setIncludeFailures] = useState(false);
  const [exportKey, setExportKey] = useState(() => crypto.randomUUID());
  const [exportCursor, setExportCursor] = useState("");
  const list = useQuery({ queryKey: ["training", ctx, datasetId, filter, outcome, cursor], enabled: canManage,
    queryFn: () => request<Page<Sample>>(`${base}/samples/?limit=25&decision=${filter}&outcome=${outcome}&cursor=${encodeURIComponent(cursor)}`, {}, ctx) });
  const detail = useQuery({ queryKey: ["training-sample", ctx, datasetId, selected], enabled: canManage && !!selected,
    queryFn: () => request<Sample>(`${base}/samples/${selected}/`, {}, ctx) });
  const exports = useQuery({ queryKey: ["training-exports", ctx, datasetId, exportCursor], enabled: canManage && mode === "exports",
    queryFn: () => request<Page<Export>>(`${base}/exports/?limit=10&cursor=${encodeURIComponent(exportCursor)}`, {}, ctx) });
  const agents = useQuery({ queryKey: ["training-agents", ctx], enabled: canManage && mode === "import", queryFn: () => api.agents(ctx) });
  const agentRuns = useQuery({ queryKey: ["training-runs", ctx, agent], enabled: canManage && mode === "import" && !!agent, queryFn: () => api.agentDisplayRuns(ctx, agent) });
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    try { await fn(); await Promise.all(["training", "training-exports", "datasets", "dataset-detail", "dataset-pull", "dataset-versions"].map(key => cache.invalidateQueries({ queryKey: [key] }))); }
    catch (e) { toast.error(e instanceof Error ? e.message : t("Operation failed")); }
    finally { setBusy(false); }
  }
  async function importJson() {
    const trajectory: unknown = JSON.parse(json);
    const sample = await request<Sample>(`${base}/samples/`, { method: "POST", body: JSON.stringify({ trajectory, rights_confirmed: rights, request_key: importKey, ...(correctionOf ? { correction_of: correctionOf } : {}) }) }, ctx);
    setSelected(sample.id); setMode("samples"); setJson(""); setRights(false); setImportKey(crypto.randomUUID()); setCorrectionOf(null);
    toast.success(t("Trajectory collected. Review before training."));
  }
  if (!canManage) return <p>{t("Collection management permission is required to curate training samples.")}</p>;
  return <div className="grid min-w-0 gap-5" aria-label={t("Agent training workbench")}>
    <header><h3 className="text-lg font-semibold">{t("Agent training")}</h3><p className="text-sm text-muted">{t("Collect complete trajectories, review outcomes, then export a reproducible SFT dataset.")}</p></header>
    {!isPrivate && <p role="status">{t("This collection is public. Unpublish it before adding, reviewing or exporting training data.")}</p>}
    <div className="flex flex-wrap gap-3 text-sm">{[["samples", t("Samples")], ["approved", t("Approved")], ["failed", t("Failed runs")], ["success", t("Successful outcomes")]].map(([key, title]) => <span className="rounded border border-line px-3 py-2" key={key}>{title} · {list.data?.summary[key] ?? 0}</span>)}</div>
    <nav className="flex flex-wrap gap-2" aria-label={t("Training actions")}>
      {(["samples", "import", "exports"] as const).map((key, i) => <button className={mode === key ? "btn btn-primary" : "btn"} aria-pressed={mode === key} key={key} onClick={() => setMode(key)}>{[t("Review samples"), t("Collect trajectories"), t("Export training data")][i]}</button>)}
    </nav>
    {list.isError && <p role="alert">{list.error.message}</p>}
    {mode === "samples" && <>
      <div className="flex flex-wrap gap-3">
        <label>{t("Review status")} <select value={filter} onChange={e => { setFilter(e.target.value); setCursor(""); }}><option value="">{t("All")}</option><option value="pending">{t("Pending")}</option><option value="approved">{t("Approved")}</option><option value="excluded">{t("Excluded")}</option></select></label>
        <label>{t("Task outcome")} <select value={outcome} onChange={e => { setOutcome(e.target.value); setCursor(""); }}><option value="">{t("All")}</option><option value="unknown">{t("Unknown")}</option><option value="success">{t("Success")}</option><option value="failure">{t("Failure")}</option></select></label>
      </div>
      {list.isLoading && <p role="status">{t("Loading samples…")}</p>}
      {list.data?.total === 0 && <p className="rounded border border-line p-5">{t("No matching samples. Collect a complete client trajectory or an existing Agent run.")}</p>}
      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(180px,1fr)_minmax(0,2fr)]">
        <div className="grid content-start gap-2">{list.data?.items.map(sample => <button key={sample.id} className={`rounded border p-3 text-left ${selected === sample.id ? "border-blue-500" : "border-line"}`} onClick={() => setSelected(sample.id)}>
          <strong className="block break-all">{sample.context.task_id || sample.id.slice(0, 8)}</strong><span className="text-sm">{sample.message_count} {t("messages")} · {label(sample.annotation.decision)} · {label(sample.annotation.outcome)}</span>
          <span className="block text-xs text-muted">{sample.context.client || label(sample.source.kind)} · {label(sample.execution_status)}</span>
          {!!sample.warnings.length && <span className="block text-sm text-amber-700">{t("Needs repair")} · {sample.warnings.length}</span>}
        </button>)}</div>
        <div className="min-w-0">{detail.isLoading && <p role="status">{t("Loading trajectory…")}</p>}{detail.isError && <p role="alert">{detail.error.message}</p>}
          {detail.data && <SampleReview key={`${detail.data.id}:${detail.data.revision}`} sample={detail.data} busy={busy || !isPrivate} onCorrect={() => {
            setJson(JSON.stringify(detail.data!.trajectory, null, 2)); setCorrectionOf(detail.data!.id); setMode("import"); setRights(false); setImportKey(crypto.randomUUID());
          }} onSave={annotation => action(async () => {
            await request(`${base}/samples/${detail.data!.id}/`, { method: "PATCH", body: JSON.stringify({ revision: detail.data!.revision, ...annotation }) }, ctx);
            await cache.invalidateQueries({ queryKey: ["training-sample"] }); toast.success(t("Review saved"));
          })} />}</div>
      </div>
      <div className="flex gap-2"><button className="btn" disabled={!cursor || busy} onClick={() => setCursor("")}>{t("First page")}</button><button className="btn" disabled={!list.data?.next_cursor || list.isFetching} onClick={() => setCursor(list.data!.next_cursor!)}>{t("Next page")}</button><span>{list.data?.total ?? 0} {t("samples")}</span></div>
    </>}
    {mode === "import" && <fieldset disabled={busy || !isPrivate} className="grid min-w-0 gap-4">
      <p className="text-sm text-muted">{t("MCP server logs may omit the client's prompt, model messages and tool definitions. Import the complete client trajectory for training; missing data is flagged rather than invented.")}</p>
      <label className="flex items-start gap-2"><input type="checkbox" checked={rights} onChange={e => setRights(e.target.checked)} />{t("I have permission to store and use these trajectories for training with the collection's authorized users.")}</label>
      <section className="grid gap-3 rounded border border-line p-4"><h4>{t("Import client trajectory")}</h4>
        {correctionOf && <p>{t("New corrected sample linked to")} {correctionOf.slice(0, 8)} <button className="btn" onClick={() => { setCorrectionOf(null); setJson(""); setImportKey(crypto.randomUUID()); }}>{t("Cancel correction")}</button></p>}
        <div className="flex flex-wrap gap-2"><button className="btn" onClick={() => { setJson(JSON.stringify(example, null, 2)); setImportKey(crypto.randomUUID()); }}>{t("Show format example")}</button>
          <label className="btn">{t("Choose JSON file")}<input className="sr-only" type="file" accept=".json,.jsonl" onChange={e => { const file = e.target.files?.[0]; if (!file) return; if (file.size > 1024 * 1024) { toast.error(t("Maximum 1 MiB per trajectory")); return; } void file.text().then(value => { setJson(value); setImportKey(crypto.randomUUID()); }); }} /></label></div>
        <label>{t("Trajectory JSON (one complete task)")}<textarea className="mt-1 w-full rounded border border-line p-3 font-mono text-xs" rows={12} value={json} onChange={e => { setJson(e.target.value); setImportKey(crypto.randomUUID()); }} /></label>
        <button className="btn btn-primary justify-self-start" disabled={!rights || !json.trim()} onClick={() => void action(importJson)}>{t("Collect trajectory")}</button>
      </section>
      <section className="grid gap-3 rounded border border-line p-4"><h4>{t("Collect existing Agent runs")}</h4>
        <label>Agent <select value={agent} onChange={e => { setAgent(e.target.value); setRuns([]); setImportResults([]); setImportKey(crypto.randomUUID()); }}><option value="">{t("Choose Agent")}</option>{agents.data?.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
        {(agents.isError || agentRuns.isError) && <p role="alert">{agents.error?.message || agentRuns.error?.message}</p>}
        <div className="grid max-h-64 gap-2 overflow-auto">{agentRuns.data?.filter(r => ["completed", "failed"].includes(r.status)).map(run => <label className="flex gap-2" key={run.id}><input type="checkbox" checked={runs.includes(run.id)} disabled={!runs.includes(run.id) && runs.length >= 20} onChange={e => { setRuns(current => e.target.checked ? [...current, run.id] : current.filter(id => id !== run.id)); setImportKey(crypto.randomUUID()); }} /><span>{run.id.slice(0, 8)} · {run.status} · {run.redaction_status}</span></label>)}</div>
        <button className="btn justify-self-start" disabled={!rights || !runs.length} onClick={() => void action(async () => {
          const ready: string[] = [], failures: string[] = [];
          for (const id of runs) { try { if (agentRuns.data?.find(r => r.id === id)?.redaction_status !== "passed") await api.redactAgentDisplayRun(ctx, agent, id); ready.push(id); } catch (e) { failures.push(`${id.slice(0, 8)}: ${e instanceof Error ? e.message : "Error"}`); } }
          if (ready.length) {
            const result = await request<{ results: { run_id: string; sample?: Sample; error?: unknown }[] }>(`${base}/import-runs/`, { method: "POST", body: JSON.stringify({ agent_id: agent, run_ids: ready, rights_confirmed: rights, request_key: importKey }) }, ctx);
            setImportResults([...result.results.map(r => `${r.run_id.slice(0, 8)}: ${r.sample ? t("collected") : JSON.stringify(r.error)}`), ...failures]);
          } else setImportResults(failures);
        })}>{t("Redact and collect selected runs")} ({runs.length}/20)</button>
        <div aria-live="polite">{importResults.map((result, i) => <p className="break-words text-sm" key={i}>{result}</p>)}</div>
      </section>
    </fieldset>}
    {mode === "exports" && <div className="grid gap-4">
      <fieldset disabled={busy || !isPrivate} className="grid gap-3 rounded border border-line p-4"><h4>{t("Create immutable SFT version")}</h4>
        <p className="text-sm text-muted">{t("Only reviewed, structurally complete samples are included. Exact duplicates are removed. Related tasks, sessions and retries stay in one split. Small datasets may have empty splits.")}</p>
        <label>{t("Split seed")} <input value={seed} maxLength={64} onChange={e => { setSeed(e.target.value); setExportKey(crypto.randomUUID()); }} /></label>
        <div className="flex flex-wrap gap-3"><label>{t("Validation %")} <input className="w-20" type="number" min={0} max={99} value={validation} onChange={e => { setValidation(Number(e.target.value)); setExportKey(crypto.randomUUID()); }} /></label><label>{t("Test %")} <input className="w-20" type="number" min={0} max={99} value={test} onChange={e => { setTest(Number(e.target.value)); setExportKey(crypto.randomUUID()); }} /></label><span>{t("Train")} {100 - validation - test}%</span></div>
        <label className="flex gap-2"><input type="checkbox" checked={includeFailures} onChange={e => { setIncludeFailures(e.target.checked); setExportKey(crypto.randomUUID()); }} />{t("Also train on approved failure trajectories (off by default)")}</label>
        <button className="btn btn-primary justify-self-start" disabled={!seed.trim() || validation + test >= 100 || validation < 0 || test < 0} onClick={() => void action(async () => {
          await request(`${base}/exports/`, { method: "POST", body: JSON.stringify({ request_key: exportKey, seed, validation_percent: validation, test_percent: test, include_failures: includeFailures }) }, ctx);
          setExportKey(crypto.randomUUID()); setExportCursor(""); toast.success(t("Training version created"));
        })}>{busy ? t("Preparing…") : t("Create training version")}</button>
      </fieldset>
      {exports.isLoading && <p role="status">{t("Loading versions…")}</p>}{exports.isError && <p role="alert">{exports.error.message}</p>}
      {exports.data?.items.map(version => <section className="rounded border border-line p-4" key={version.id}><h4>{version.version} · {t("Training export")}</h4><p className="text-sm">{Object.entries(version.training.counts).map(([name, count]) => `${name}: ${count}`).join(" · ")} · {t("Skipped")}: {version.training.skipped_count}</p><div className="mt-2 flex flex-wrap gap-2">{version.files.map(file => <button className="btn" key={file.id} onClick={() => void downloadApiFile(file.download_url, file.file_name, ctx)}>{file.file_name.split("/").pop()}</button>)}</div></section>)}
      <div className="flex gap-2"><button className="btn" disabled={!exportCursor} onClick={() => setExportCursor("")}>{t("First page")}</button><button className="btn" disabled={!exports.data?.next_cursor} onClick={() => setExportCursor(exports.data!.next_cursor!)}>{t("Next page")}</button></div>
    </div>}
    {busy && <p role="status">{t("Saving…")}</p>}
  </div>;
}

function SampleReview({ sample, busy, onSave, onCorrect }: { sample: Sample; busy: boolean; onSave: (value: Annotation) => Promise<void>; onCorrect: () => void }) {
  const [form, setForm] = useState<Annotation>(sample.annotation);
  const messages = sample.trajectory?.messages ?? [];
  const selected = form.trainable_assistant_indices ?? messages.flatMap((m, i) => m.role === "assistant" && m.weight !== 0 ? [i] : []);
  return <div className="grid min-w-0 gap-4">
    <p className="text-sm">{t("Source")}: {label(sample.source.kind)} · {t("Revision")}: {sample.revision}</p>
    <button className="btn justify-self-start" disabled={busy} onClick={onCorrect}>{t("Edit as a new corrected sample")}</button>
    {!!sample.warnings.length && <div role="status" className="rounded border border-amber-300 p-3"><strong>{t("Repair before approval")}</strong><ul className="list-inside list-disc">{sample.warnings.map(w => <li key={w}>{label(w)}</li>)}</ul></div>}
    <details><summary>{t("Tool definitions")} ({sample.trajectory?.tools.length ?? 0})</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(sample.trajectory?.tools, null, 2)}</pre></details>
    <ol className="grid max-h-[36rem] min-w-0 gap-3 overflow-auto">{messages.map((message, i) => <li className="min-w-0 rounded border border-line p-3" key={i}><div className="mb-2 flex flex-wrap justify-between gap-2"><strong>{i + 1}. {message.role}</strong>{message.role === "assistant" && <label className="text-sm"><input type="checkbox" disabled={busy} checked={selected.includes(i)} onChange={e => setForm({ ...form, trainable_assistant_indices: e.target.checked ? [...selected, i] : selected.filter(n => n !== i) })} /> {t("Train this step")}</label>}</div>
      <pre className="whitespace-pre-wrap break-words text-sm">{message.content}</pre>{message.tool_call_id && <p className="break-all text-xs text-muted">{message.tool_call_id}</p>}
      {message.tool_calls?.map(call => <div className="mt-2 rounded bg-slate-50 p-2" key={call.id}><strong>{call.function.name}</strong><span className="ml-2 text-xs">{call.id}</span><pre className="whitespace-pre-wrap break-all text-xs">{call.function.arguments}</pre></div>)}</li>)}</ol>
    <fieldset disabled={busy} className="grid gap-3 rounded border border-line p-3">
      <label>{t("Task outcome")} <select aria-label={t("Task outcome")} value={form.outcome} onChange={e => setForm({ ...form, outcome: e.target.value })}><option value="unknown">{t("Unknown")}</option><option value="success">{t("Success")}</option><option value="failure">{t("Failure")}</option></select></label>
      <label>{t("Review decision")} <select aria-label={t("Review decision")} value={form.decision} onChange={e => setForm({ ...form, decision: e.target.value })}><option value="pending">{t("Pending")}</option><option value="approved" disabled={!!sample.warnings.length}>{t("Approved")}</option><option value="excluded">{t("Excluded")}</option></select></label>
      <label>{t("Score (0–1, optional)")} <input className="w-24" type="number" min={0} max={1} step={0.01} value={form.score ?? ""} onChange={e => setForm({ ...form, score: e.target.value === "" ? null : Number(e.target.value) })} /></label>
      <label>{t("Feedback source")} <select aria-label={t("Feedback source")} value={form.feedback_source} onChange={e => setForm({ ...form, feedback_source: e.target.value })}><option value="human">{t("Human review")}</option><option value="evaluator">{t("Evaluator")}</option><option value="user_feedback">{t("User feedback")}</option></select></label>
      <label>{t("Feedback / failure reason")}<textarea className="mt-1 w-full rounded border border-line p-2" maxLength={2000} rows={3} value={form.feedback} onChange={e => setForm({ ...form, feedback: e.target.value })} /></label>
      <button className="btn btn-primary justify-self-start" onClick={() => void onSave(form)}>{t("Save review")}</button>
    </fieldset>
  </div>;
}
