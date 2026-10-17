import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, request, type ApiContext } from "../lib/api";
import { getLocale, useLocale } from "../localization";
import { downloadApiFile } from "./DataAssetPresentation";

type Message = { role: string; content: string; weight?: number; tool_call_id?: string; tool_calls?: { id: string; function: { name: string; arguments: string } }[] };
type Annotation = { decision: string; outcome: string; score: number | null; feedback: string; feedback_source: string; trainable_assistant_indices: number[] | null };
type Sample = { id: string; revision: number; context: { task_id?: string; client?: string }; execution_status: string; message_count: number;
  warnings: string[]; annotation: Annotation; source: { kind: string }; trajectory?: { messages: Message[]; tools: unknown[] } };
type Page<T> = { items: T[]; total: number; next_cursor?: string | null; summary: Record<string, number> };
type Export = { id: string; version: string; training: { counts: Record<string, number>; skipped_count: number }; files: { id: string; file_name: string; download_url: string }[] };

function l(en: string, zh: string) { return getLocale() === "zh-CN" ? zh : en; }
function label(value: string) {
  const labels: Record<string, [string, string]> = {
    pending: ["Pending", "待审阅"], approved: ["Approved", "已批准"], excluded: ["Excluded", "已排除"],
    unknown: ["Unknown", "待确认"], success: ["Success", "成功"], failure: ["Failure", "失败"],
    completed: ["Completed", "运行完成"], failed: ["Failed", "运行失败"], cancelled: ["Cancelled", "已取消"],
    external_client: ["Client trajectory", "客户端轨迹"], nexus_run: ["Nexus run", "Nexus 运行"], correction: ["Corrected sample", "修正样本"],
    missing_tool_result: ["A tool call has no matching result", "工具调用缺少匹配的返回结果"],
    orphan_tool_result: ["A tool result has no matching call", "工具返回缺少匹配的调用"],
    missing_user_message: ["User input is missing", "缺少用户输入"], missing_assistant_message: ["Assistant output is missing", "缺少模型回复"],
    missing_final_answer: ["The final answer is missing", "缺少最终回复"], missing_tool_definition: ["A tool definition is missing", "缺少工具定义"],
    invalid_tool_arguments: ["Tool arguments are not valid JSON", "工具参数不是有效 JSON"], invalid_tool_schema: ["A tool parameter schema is invalid", "工具参数结构无效"],
    missing_task_or_session: ["Add a task or session ID for grouping", "需要任务或会话标识以正确划分数据集"],
    unmatched_message_delta: ["A message fragment has no start event", "消息片段缺少起始事件"], unmatched_tool_delta: ["A tool fragment has no start event", "工具片段缺少起始事件"],
  };
  return labels[value] ? l(...labels[value]) : value;
}
const example = { schema: "nexus.trajectory.v1", context: { task_id: "example-task", session_id: "example-session", client: "my-agent" },
  execution_status: "completed", tools: [{ type: "function", function: { name: "add", description: "Add two numbers", parameters: { type: "object", properties: { a: { type: "number" }, b: { type: "number" } }, required: ["a", "b"] } } }],
  messages: [{ role: "user", content: "What is 2 + 3?" }, { role: "assistant", content: "", tool_calls: [{ id: "call_1", type: "function", function: { name: "add", arguments: '{"a":2,"b":3}' } }] },
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
    catch (e) { toast.error(e instanceof Error ? e.message : l("Operation failed", "操作失败")); }
    finally { setBusy(false); }
  }
  async function importJson() {
    const trajectory: unknown = JSON.parse(json);
    const sample = await request<Sample>(`${base}/samples/`, { method: "POST", body: JSON.stringify({ trajectory, rights_confirmed: rights, request_key: importKey, ...(correctionOf ? { correction_of: correctionOf } : {}) }) }, ctx);
    setSelected(sample.id); setMode("samples"); setJson(""); setRights(false); setImportKey(crypto.randomUUID()); setCorrectionOf(null);
    toast.success(l("Trajectory collected. Review before training.", "轨迹已收集，请审阅后再用于训练。"));
  }
  if (!canManage) return <p>{l("Collection management permission is required to curate training samples.", "整理训练样本需要数据集管理权限。")}</p>;
  return <div className="grid min-w-0 gap-5" aria-label={l("Agent training workbench", "Agent 训练工作台")}>
    <header><h3 className="text-lg font-semibold">{l("Agent training", "Agent 训练")}</h3><p className="text-sm text-muted">{l("Collect complete trajectories, review outcomes, then export a reproducible SFT dataset.", "收集完整轨迹、审阅任务结果，导出可复现的 SFT 数据集。")}</p></header>
    {!isPrivate && <p role="status">{l("This collection is public. Unpublish it before adding, reviewing or exporting training data.", "该数据集已公开。请先取消发布，再添加、标注或导出训练数据。")}</p>}
    <div className="flex flex-wrap gap-3 text-sm">{[["samples", l("Samples", "样本")], ["approved", l("Approved", "已批准")], ["failed", l("Failed runs", "失败运行")], ["success", l("Successful outcomes", "成功结果")]].map(([key, title]) => <span className="rounded border border-line px-3 py-2" key={key}>{title} · {list.data?.summary[key] ?? 0}</span>)}</div>
    <nav className="flex flex-wrap gap-2" aria-label={l("Training actions", "训练操作")}>
      {(["samples", "import", "exports"] as const).map((key, i) => <button className={mode === key ? "btn btn-primary" : "btn"} aria-pressed={mode === key} key={key} onClick={() => setMode(key)}>{[l("Review samples", "审阅样本"), l("Collect trajectories", "收集轨迹"), l("Export training data", "导出训练数据")][i]}</button>)}
    </nav>
    {list.isError && <p role="alert">{list.error.message}</p>}
    {mode === "samples" && <>
      <div className="flex flex-wrap gap-3">
        <label>{l("Review status", "审阅状态")} <select value={filter} onChange={e => { setFilter(e.target.value); setCursor(""); }}><option value="">{l("All", "全部")}</option><option value="pending">{l("Pending", "待审阅")}</option><option value="approved">{l("Approved", "已批准")}</option><option value="excluded">{l("Excluded", "已排除")}</option></select></label>
        <label>{l("Task outcome", "任务结果")} <select value={outcome} onChange={e => { setOutcome(e.target.value); setCursor(""); }}><option value="">{l("All", "全部")}</option><option value="unknown">{l("Unknown", "待确认")}</option><option value="success">{l("Success", "成功")}</option><option value="failure">{l("Failure", "失败")}</option></select></label>
      </div>
      {list.isLoading && <p role="status">{l("Loading samples…", "正在加载样本…")}</p>}
      {list.data?.total === 0 && <p className="rounded border border-line p-5">{l("No matching samples. Collect a complete client trajectory or an existing Agent run.", "暂无匹配的样本。可以导入客户端完整轨迹，或收集已有 Agent 运行。")}</p>}
      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(180px,1fr)_minmax(0,2fr)]">
        <div className="grid content-start gap-2">{list.data?.items.map(sample => <button key={sample.id} className={`rounded border p-3 text-left ${selected === sample.id ? "border-blue-500" : "border-line"}`} onClick={() => setSelected(sample.id)}>
          <strong className="block break-all">{sample.context.task_id || sample.id.slice(0, 8)}</strong><span className="text-sm">{sample.message_count} {l("messages", "条消息")} · {label(sample.annotation.decision)} · {label(sample.annotation.outcome)}</span>
          <span className="block text-xs text-muted">{sample.context.client || label(sample.source.kind)} · {label(sample.execution_status)}</span>
          {!!sample.warnings.length && <span className="block text-sm text-amber-700">{l("Needs repair", "需要补全")} · {sample.warnings.length}</span>}
        </button>)}</div>
        <div className="min-w-0">{detail.isLoading && <p role="status">{l("Loading trajectory…", "正在加载轨迹…")}</p>}{detail.isError && <p role="alert">{detail.error.message}</p>}
          {detail.data && <SampleReview key={`${detail.data.id}:${detail.data.revision}`} sample={detail.data} busy={busy || !isPrivate} onCorrect={() => {
            setJson(JSON.stringify(detail.data!.trajectory, null, 2)); setCorrectionOf(detail.data!.id); setMode("import"); setRights(false); setImportKey(crypto.randomUUID());
          }} onSave={annotation => action(async () => {
            await request(`${base}/samples/${detail.data!.id}/`, { method: "PATCH", body: JSON.stringify({ revision: detail.data!.revision, ...annotation }) }, ctx);
            await cache.invalidateQueries({ queryKey: ["training-sample"] }); toast.success(l("Review saved", "审阅已保存"));
          })} />}</div>
      </div>
      <div className="flex gap-2"><button className="btn" disabled={!cursor || busy} onClick={() => setCursor("")}>{l("First page", "首页")}</button><button className="btn" disabled={!list.data?.next_cursor || list.isFetching} onClick={() => setCursor(list.data!.next_cursor!)}>{l("Next page", "下一页")}</button><span>{list.data?.total ?? 0} {l("samples", "条样本")}</span></div>
    </>}
    {mode === "import" && <fieldset disabled={busy || !isPrivate} className="grid min-w-0 gap-4">
      <p className="text-sm text-muted">{l("MCP server logs may omit the client's prompt, model messages and tool definitions. Import the complete client trajectory for training; missing data is flagged rather than invented.", "MCP 服务端日志可能缺少客户端提示、模型消息和工具定义。训练应接入客户端完整轨迹；缺失数据会明确提示，不会自动补造。")}</p>
      <label className="flex items-start gap-2"><input type="checkbox" checked={rights} onChange={e => setRights(e.target.checked)} />{l("I have permission to store and use these trajectories for training with the collection's authorized users.", "我有权存储这些轨迹，并与该数据集的授权用户将其用于训练。")}</label>
      <section className="grid gap-3 rounded border border-line p-4"><h4>{l("Import client trajectory", "导入客户端轨迹")}</h4>
        {correctionOf && <p>{l("New corrected sample linked to", "新修正样本，来源关联到")} {correctionOf.slice(0, 8)} <button className="btn" onClick={() => { setCorrectionOf(null); setJson(""); setImportKey(crypto.randomUUID()); }}>{l("Cancel correction", "取消修正")}</button></p>}
        <div className="flex flex-wrap gap-2"><button className="btn" onClick={() => { setJson(JSON.stringify(example, null, 2)); setImportKey(crypto.randomUUID()); }}>{l("Show format example", "显示格式示例")}</button>
          <label className="btn">{l("Choose JSON file", "选择 JSON 文件")}<input className="sr-only" type="file" accept=".json,.jsonl" onChange={e => { const file = e.target.files?.[0]; if (!file) return; if (file.size > 1024 * 1024) { toast.error(l("Maximum 1 MiB per trajectory", "单条轨迹最多 1 MiB")); return; } void file.text().then(value => { setJson(value); setImportKey(crypto.randomUUID()); }); }} /></label></div>
        <label>{l("Trajectory JSON (one complete task)", "轨迹 JSON（一个完整任务）")}<textarea className="mt-1 w-full rounded border border-line p-3 font-mono text-xs" rows={12} value={json} onChange={e => { setJson(e.target.value); setImportKey(crypto.randomUUID()); }} /></label>
        <button className="btn btn-primary justify-self-start" disabled={!rights || !json.trim()} onClick={() => void action(importJson)}>{l("Collect trajectory", "收集轨迹")}</button>
      </section>
      <section className="grid gap-3 rounded border border-line p-4"><h4>{l("Collect existing Agent runs", "收集已有 Agent 运行")}</h4>
        <label>Agent <select value={agent} onChange={e => { setAgent(e.target.value); setRuns([]); setImportResults([]); setImportKey(crypto.randomUUID()); }}><option value="">{l("Choose Agent", "选择 Agent")}</option>{agents.data?.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
        {(agents.isError || agentRuns.isError) && <p role="alert">{agents.error?.message || agentRuns.error?.message}</p>}
        <div className="grid max-h-64 gap-2 overflow-auto">{agentRuns.data?.filter(r => ["completed", "failed"].includes(r.status)).map(run => <label className="flex gap-2" key={run.id}><input type="checkbox" checked={runs.includes(run.id)} disabled={!runs.includes(run.id) && runs.length >= 20} onChange={e => { setRuns(current => e.target.checked ? [...current, run.id] : current.filter(id => id !== run.id)); setImportKey(crypto.randomUUID()); }} /><span>{run.id.slice(0, 8)} · {run.status} · {run.redaction_status}</span></label>)}</div>
        <button className="btn justify-self-start" disabled={!rights || !runs.length} onClick={() => void action(async () => {
          const ready: string[] = [], failures: string[] = [];
          for (const id of runs) { try { if (agentRuns.data?.find(r => r.id === id)?.redaction_status !== "passed") await api.redactAgentDisplayRun(ctx, agent, id); ready.push(id); } catch (e) { failures.push(`${id.slice(0, 8)}: ${e instanceof Error ? e.message : "Error"}`); } }
          if (ready.length) {
            const result = await request<{ results: { run_id: string; sample?: Sample; error?: unknown }[] }>(`${base}/import-runs/`, { method: "POST", body: JSON.stringify({ agent_id: agent, run_ids: ready, rights_confirmed: rights, request_key: importKey }) }, ctx);
            setImportResults([...result.results.map(r => `${r.run_id.slice(0, 8)}: ${r.sample ? l("collected", "已收集") : JSON.stringify(r.error)}`), ...failures]);
          } else setImportResults(failures);
        })}>{l("Redact and collect selected runs", "脱敏并收集所选运行")} ({runs.length}/20)</button>
        <div aria-live="polite">{importResults.map((result, i) => <p className="break-words text-sm" key={i}>{result}</p>)}</div>
      </section>
    </fieldset>}
    {mode === "exports" && <div className="grid gap-4">
      <fieldset disabled={busy || !isPrivate} className="grid gap-3 rounded border border-line p-4"><h4>{l("Create immutable SFT version", "创建不可变 SFT 版本")}</h4>
        <p className="text-sm text-muted">{l("Only reviewed, structurally complete samples are included. Exact duplicates are removed. Related tasks, sessions and retries stay in one split. Small datasets may have empty splits.", "只导出已批准且结构完整的样本，移除完全重复项。关联任务、会话和重试会留在同一分组，小数据集可能存在空分组。")}</p>
        <label>{l("Split seed", "划分种子")} <input value={seed} maxLength={64} onChange={e => { setSeed(e.target.value); setExportKey(crypto.randomUUID()); }} /></label>
        <div className="flex flex-wrap gap-3"><label>{l("Validation %", "验证集 %")} <input className="w-20" type="number" min={0} max={99} value={validation} onChange={e => { setValidation(Number(e.target.value)); setExportKey(crypto.randomUUID()); }} /></label><label>{l("Test %", "测试集 %")} <input className="w-20" type="number" min={0} max={99} value={test} onChange={e => { setTest(Number(e.target.value)); setExportKey(crypto.randomUUID()); }} /></label><span>{l("Train", "训练集")} {100 - validation - test}%</span></div>
        <label className="flex gap-2"><input type="checkbox" checked={includeFailures} onChange={e => { setIncludeFailures(e.target.checked); setExportKey(crypto.randomUUID()); }} />{l("Also train on approved failure trajectories (off by default)", "也训练已批准的失败轨迹（默认关闭）")}</label>
        <button className="btn btn-primary justify-self-start" disabled={!seed.trim() || validation + test >= 100 || validation < 0 || test < 0} onClick={() => void action(async () => {
          await request(`${base}/exports/`, { method: "POST", body: JSON.stringify({ request_key: exportKey, seed, validation_percent: validation, test_percent: test, include_failures: includeFailures }) }, ctx);
          setExportKey(crypto.randomUUID()); setExportCursor(""); toast.success(l("Training version created", "训练版本已创建"));
        })}>{busy ? l("Preparing…", "正在生成…") : l("Create training version", "创建训练版本")}</button>
      </fieldset>
      {exports.isLoading && <p role="status">{l("Loading versions…", "正在加载版本…")}</p>}{exports.isError && <p role="alert">{exports.error.message}</p>}
      {exports.data?.items.map(version => <section className="rounded border border-line p-4" key={version.id}><h4>{version.version} · {l("Training export", "训练导出")}</h4><p className="text-sm">{Object.entries(version.training.counts).map(([name, count]) => `${name}: ${count}`).join(" · ")} · {l("Skipped", "已跳过")}: {version.training.skipped_count}</p><div className="mt-2 flex flex-wrap gap-2">{version.files.map(file => <button className="btn" key={file.id} onClick={() => void downloadApiFile(file.download_url, file.file_name, ctx)}>{file.file_name.split("/").pop()}</button>)}</div></section>)}
      <div className="flex gap-2"><button className="btn" disabled={!exportCursor} onClick={() => setExportCursor("")}>{l("First page", "首页")}</button><button className="btn" disabled={!exports.data?.next_cursor} onClick={() => setExportCursor(exports.data!.next_cursor!)}>{l("Next page", "下一页")}</button></div>
    </div>}
    {busy && <p role="status">{l("Saving…", "正在处理…")}</p>}
  </div>;
}

function SampleReview({ sample, busy, onSave, onCorrect }: { sample: Sample; busy: boolean; onSave: (value: Annotation) => Promise<void>; onCorrect: () => void }) {
  const [form, setForm] = useState<Annotation>(sample.annotation);
  const messages = sample.trajectory?.messages ?? [];
  const selected = form.trainable_assistant_indices ?? messages.flatMap((m, i) => m.role === "assistant" && m.weight !== 0 ? [i] : []);
  return <div className="grid min-w-0 gap-4">
    <p className="text-sm">{l("Source", "来源")}: {label(sample.source.kind)} · {l("Revision", "修订")}: {sample.revision}</p>
    <button className="btn justify-self-start" disabled={busy} onClick={onCorrect}>{l("Edit as a new corrected sample", "修正并生成新样本")}</button>
    {!!sample.warnings.length && <div role="status" className="rounded border border-amber-300 p-3"><strong>{l("Repair before approval", "批准前需要补全")}</strong><ul className="list-inside list-disc">{sample.warnings.map(w => <li key={w}>{label(w)}</li>)}</ul></div>}
    <details><summary>{l("Tool definitions", "工具定义")} ({sample.trajectory?.tools.length ?? 0})</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(sample.trajectory?.tools, null, 2)}</pre></details>
    <ol className="grid max-h-[36rem] min-w-0 gap-3 overflow-auto">{messages.map((message, i) => <li className="min-w-0 rounded border border-line p-3" key={i}><div className="mb-2 flex flex-wrap justify-between gap-2"><strong>{i + 1}. {message.role}</strong>{message.role === "assistant" && <label className="text-sm"><input type="checkbox" disabled={busy} checked={selected.includes(i)} onChange={e => setForm({ ...form, trainable_assistant_indices: e.target.checked ? [...selected, i] : selected.filter(n => n !== i) })} /> {l("Train this step", "训练此步骤")}</label>}</div>
      <pre className="whitespace-pre-wrap break-words text-sm">{message.content}</pre>{message.tool_call_id && <p className="break-all text-xs text-muted">{message.tool_call_id}</p>}
      {message.tool_calls?.map(call => <div className="mt-2 rounded bg-slate-50 p-2" key={call.id}><strong>{call.function.name}</strong><span className="ml-2 text-xs">{call.id}</span><pre className="whitespace-pre-wrap break-all text-xs">{call.function.arguments}</pre></div>)}</li>)}</ol>
    <fieldset disabled={busy} className="grid gap-3 rounded border border-line p-3">
      <label>{l("Task outcome", "任务结果")} <select aria-label={l("Task outcome", "任务结果")} value={form.outcome} onChange={e => setForm({ ...form, outcome: e.target.value })}><option value="unknown">{l("Unknown", "待确认")}</option><option value="success">{l("Success", "成功")}</option><option value="failure">{l("Failure", "失败")}</option></select></label>
      <label>{l("Review decision", "审阅决定")} <select aria-label={l("Review decision", "审阅决定")} value={form.decision} onChange={e => setForm({ ...form, decision: e.target.value })}><option value="pending">{l("Pending", "待审阅")}</option><option value="approved" disabled={!!sample.warnings.length}>{l("Approved", "已批准")}</option><option value="excluded">{l("Excluded", "已排除")}</option></select></label>
      <label>{l("Score (0–1, optional)", "评分（0–1，可选）")} <input className="w-24" type="number" min={0} max={1} step={0.01} value={form.score ?? ""} onChange={e => setForm({ ...form, score: e.target.value === "" ? null : Number(e.target.value) })} /></label>
      <label>{l("Feedback source", "评价来源")} <select aria-label={l("Feedback source", "评价来源")} value={form.feedback_source} onChange={e => setForm({ ...form, feedback_source: e.target.value })}><option value="human">{l("Human review", "人工审阅")}</option><option value="evaluator">{l("Evaluator", "评测器")}</option><option value="user_feedback">{l("User feedback", "用户反馈")}</option></select></label>
      <label>{l("Feedback / failure reason", "评价 / 失败原因")}<textarea className="mt-1 w-full rounded border border-line p-2" maxLength={2000} rows={3} value={form.feedback} onChange={e => setForm({ ...form, feedback: e.target.value })} /></label>
      <button className="btn btn-primary justify-self-start" onClick={() => void onSave(form)}>{l("Save review", "保存审阅")}</button>
    </fieldset>
  </div>;
}
