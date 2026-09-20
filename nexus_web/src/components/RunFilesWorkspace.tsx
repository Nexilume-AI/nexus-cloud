import { t, useLocale } from "../localization";
import { lazy, Suspense, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Download, FileText, Maximize2, MessageSquareQuote, X } from "lucide-react";
import { useAuth } from "../app/AuthContext";
import { api, type AgentFileTransfer } from "../lib/api";
import { useFilesState } from "../lib/runFilesState";
import { contextRevision, runContextKey, useContextScroll } from "../lib/runContextState";
import { runDisplayPolling } from "../lib/runDisplayPolling";
import type { AgentOutputArtifact, AgentRunMessage, PrivateAgentRunDisplay } from "../lib/types";
import { filePreviewKind, runFiles, type RunFile } from "../lib/runFiles";
import { ContentMarkdown } from "./ContentMarkdown";
import { NexilumeDialog } from "./NexilumeControls";

const control = "min-h-11 min-w-0 rounded-md border border-black/15 bg-white px-3 text-xs disabled:opacity-40";
const PdfPreview = lazy(() => import("./RunPdfPreview").catch(() => ({ default: () => <p role="alert" className="p-3 text-sm">{t("PDF viewer could not be loaded. Refresh the page or download the file.")}</p> })));
export function RunFilesWorkspace({ runId, displayToken, messages, computer, status, onReference }: {
  runId: string; displayToken: string; messages: AgentRunMessage[]; status: string;
  computer: PrivateAgentRunDisplay["computer"];
  onReference: (file: RunFile) => Promise<boolean>;
}) {
  useLocale();
  const { apiContext, user } = useAuth();
  const contextKey = runContextKey(user?.user_id, apiContext.tenantId, apiContext.projectId, runId);
  const scroll = useContextScroll(contextKey, "files");
  const [view, setView] = useFilesState(JSON.stringify([user?.user_id, apiContext.tenantId, apiContext.projectId, runId]));
  const { search, kind, turn, selected } = view;
  function filters(values: Partial<typeof view>) { setView(current => ({ ...current, ...values, inputCursors: [""], outputCursors: [""], selected: "", pdfPage: 1 })); }
  function setSearch(search: string) { filters({ search }); }
  function setKind(kind: string) { filters({ kind }); }
  function setTurn(turn: string) { filters({ turn }); }
  function setSelected(selected: string) { setView(current => ({ ...current, selected, pdfPage: 1 })); }
  const [debouncedSearch, debounce] = useState(search);
  useEffect(() => { const timer = setTimeout(() => debounce(search), 250); return () => clearTimeout(timer); }, [search]);
  const params = { q: debouncedSearch, turn };
  const inputs = useQuery({ queryKey: ["private-run-input-files", apiContext, runId, displayToken, params, view.inputCursors.at(-1)],
    queryFn: ({ signal }) => api.runFilePage<AgentFileTransfer>(apiContext, runId, displayToken, "files", { ...params, cursor: view.inputCursors.at(-1)! }, signal), enabled: Boolean(displayToken), gcTime: 60_000 });
  const outputPage = useQuery({ queryKey: ["private-run-file-outputs", apiContext, runId, displayToken, params, view.outputCursors.at(-1)],
    queryFn: ({ signal }) => api.runFilePage<AgentOutputArtifact>(apiContext, runId, displayToken, "outputs", { ...params, cursor: view.outputCursors.at(-1)! }, signal), enabled: Boolean(displayToken), gcTime: 60_000,
    refetchInterval: query => view.outputCursors.length > 1 ? false : runDisplayPolling({ status, attached: false, shell: false, files: true, pendingOutputs: query.state.data?.items.some(row => row.snapshot_status === "pending" || row.scan_status === "pending" || row.policy_status === "pending") }).outputs });
  const outputs = outputPage.data?.items ?? [];
  const outputLoading = outputPage.isPending;
  const outputError = outputPage.isError;
  const onRetryOutputs = () => { setView(current => ({ ...current, outputCursors: [""] })); void outputPage.refetch(); };
  // Include newly delivered inputs even if the panel stays open through another turn.
  const messageRevision = messages.map(message => `${message.id}:${message.sequence}:${message.content_blocks.length}`).join("|");
  useEffect(() => { if (displayToken) void inputs.refetch(); }, [runId, displayToken, messageRevision]); // eslint-disable-line react-hooks/exhaustive-deps
  const [expanded, setExpanded] = useState(false);
  const [layout, setLayout] = useState<"both" | "list" | "preview">(() => {
    try { const value = localStorage.getItem("nexus.run-files.layout"); if (value === "list" || value === "preview") return value; } catch { /* optional preference */ }
    return "both";
  });
  function changeLayout(value: "both" | "list" | "preview") {
    setLayout(value);
    try { localStorage.setItem("nexus.run-files.layout", value); } catch { /* optional preference */ }
  }
  function backToFiles() {
    const name = file?.name;
    setSelected("");
    if (layout === "preview") changeLayout("list");
    requestAnimationFrame(() => {
      const button = [...(split.current?.querySelectorAll<HTMLButtonElement>("button") ?? [])].find(item => item.getAttribute("aria-label") === `Preview ${name}`);
      button?.focus();
    });
  }
  const split = useRef<HTMLDivElement>(null);
  const [directoryWidth, setDirectoryWidth] = useState(() => {
    try { const value = Number(localStorage.getItem("nexus.run-files.directory-width")); if (value >= 22 && value <= 45) return value; } catch { /* optional preference */ }
    return 30;
  });
  function resizeDirectory(value: number) {
    const width = Math.min(45, Math.max(22, value));
    setDirectoryWidth(width);
    try { localStorage.setItem("nexus.run-files.directory-width", String(width)); } catch { /* optional preference */ }
  }
  const [downloadId, setDownloadId] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const [referencing, setReferencing] = useState(false);
  const [customTurn, setCustomTurn] = useState("");
  const [chooseTurn, setChooseTurn] = useState(false);
  const rows = useMemo(() => runFiles(runId, inputs.data?.items ?? [], outputs, messages).map(row => {
    if (row.kind !== "output" || !(computer.revision && computer.revision > 0)) return row;
    const output = outputs.find(item => row.key === `output:${item.id}`);
    if (!output) return row;
    const revision = output.computer_revision ?? 0;
    const name = computer.history?.find(item => item.revision === revision)?.computer_name || t("Original Computer");
    return { ...row, source: `Source: ${name} · attachment ${revision + 1}` };
  }), [runId, inputs.data, outputs, messages, computer]);
  const turns = [...new Set(rows.flatMap(row => row.turns))].sort((a, b) => b - a);
  const filtered = rows.filter(row => (kind === "all" || row.kind === kind) &&
    ((row.kind === "input" && !row.key.startsWith("image:") && inputs.data?.legacy === false) || turn === "all" || (turn === "unknown" ? !row.turns.length : row.turns.includes(Number(turn)))) &&
    `${row.name} ${row.mime} ${row.source}`.toLowerCase().includes(search.trim().toLowerCase()));
  const file = filtered.find(row => row.key === selected);
  async function download(row: RunFile) {
    setDownloadError(""); setDownloadId(row.key);
    try {
      // Message images use the same protected preview fetch, never a public asset URL.
      if (row.path.includes("/display-assets/")) {
        const blob = await api.previewRunFile(apiContext, runId, row.path, displayToken, 20 * 1024 ** 2, new AbortController().signal);
        const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
        anchor.href = url; anchor.download = row.name; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 30_000);
      } else {
        const value = await api.prepareAgentDownload(apiContext, row.path, displayToken);
        const anchor = document.createElement("a"); anchor.href = value.url; anchor.download = value.file_name;
        document.body.appendChild(anchor); anchor.click(); anchor.remove();
      }
    } catch { setDownloadError("Download unavailable. Retry to renew access."); }
    finally { setDownloadId(""); }
  }
  const preview = file ? <FilePreview key={`${runId}:${file.key}`} file={file} runId={runId} displayToken={displayToken} pdfPage={view.pdfPage} onPdfPage={pdfPage => setView(current => ({ ...current, pdfPage }))} /> : null;
  const actions = file ? <div className="flex flex-wrap gap-2">
    <button type="button" className={`${control} inline-flex items-center gap-2`} disabled={file.state !== "ready" || Boolean(downloadId)} onClick={() => void download(file)} aria-label={t("Download {{0}}", { 0: file.name })}><Download size={14} />{downloadId === file.key ? t("Preparing…") : t("Download")}</button>
    <button type="button" className={`${control} inline-flex items-center gap-2`} disabled={file.state !== "ready" || referencing} onClick={async () => { setReferencing(true); try { if (await onReference(file)) setExpanded(false); } finally { setReferencing(false); } }} title={t("Attach this Run-owned snapshot to your next message")}><MessageSquareQuote size={14} />{referencing ? t("Checking access…") : t("Reference in draft")}</button>
  </div> : null;
  const workspace = <div ref={scroll} data-context-scroll-root className="grid min-w-0 gap-3">
    <label className="flex items-center justify-between gap-2 text-xs">{t("Layout")}<select aria-label={t("Files layout")} className={control} value={layout} onChange={event => changeLayout(event.target.value as typeof layout)}><option value="both">{t("List and preview")}</option><option value="list">{t("File list only")}</option><option value="preview">{t("Preview only")}</option></select></label>
    <div className="run-files-container">
    <div ref={split} className={`run-files-split run-files-layout--${layout} ${file ? "run-files-split--selected" : ""}`} style={{ "--file-directory-width": `${directoryWidth}%` } as CSSProperties}>
    <div className="run-files-directory grid min-w-0 content-start gap-3">
    <label className="grid gap-1 text-xs text-[#535350]">{t("Search files")}<input type="search" value={search} onChange={event => setSearch(event.target.value)} className={control} placeholder={t("Name, type or source")} /></label>
    <div className="grid grid-cols-2 gap-2">
      <label className="grid gap-1 text-xs">{t("File kind")}<select aria-label={t("File kind")} className={control} value={kind} onChange={event => setKind(event.target.value)}><option value="all">{t("All files")}</option><option value="input">{t("Inputs")}</option><option value="output">{t("Outputs")}</option></select></label>
      <label className="grid gap-1 text-xs">{t("Turn")}<select aria-label={t("Turn")} className={control} value={turn} onChange={event => { if (event.target.value === "custom") setChooseTurn(true); else setTurn(event.target.value); }}><option value="all">{t("All turns")}</option>{[...new Set([...turns, ...(/^[1-9]/.test(turn) ? [Number(turn)] : [])])].sort((a, b) => b - a).map(value => <option key={value} value={value}>{t("Turn")}{" "}{value}</option>)}<option value="unknown">{t("Not recorded")}</option><option value="custom">{t("Specific turn…")}</option></select></label>
    </div>
    {chooseTurn ? <form className="flex min-w-0 gap-2" onSubmit={event => { event.preventDefault(); if (/^[1-9][0-9]{0,8}$/.test(customTurn)) { setTurn(customTurn); setChooseTurn(false); } }}><input aria-label={t("Specific turn number")} className={`${control} w-full`} type="number" min="1" max="999999999" value={customTurn} onChange={event => setCustomTurn(event.target.value)} /><button className={control} type="submit">{t("Apply")}</button></form> : null}
    {inputs.isPending ? <p role="status" className="text-xs">{t("Loading input files…")}</p> : null}
    {inputs.isError ? <div role="alert" className="text-xs">{t("Input files could not be loaded.")}{" "}<button type="button" className={control} onClick={() => { setView(current => ({ ...current, inputCursors: [""] })); void inputs.refetch(); }}>{t("Retry input files")}</button></div> : null}
    {outputLoading ? <p role="status" className="text-xs">{t("Loading output files…")}</p> : null}
    {outputError ? <div role="alert" className="text-xs">{t("Output files could not be loaded.")}{" "}<button type="button" className={control} onClick={onRetryOutputs}>{t("Retry output files")}</button></div> : null}
    {!filtered.length && !inputs.isPending && !outputLoading && !inputs.isError && !outputError ? <div className="py-4 text-sm text-[#858481]">{rows.length ? <><p>{t("No matching files.")}</p><button type="button" className={control} onClick={() => { setSearch(""); setKind("all"); setTurn("all"); }}>{t("Clear filters")}</button></> : t("No files have been attached or produced for this Run.")}</div> : null}
    <div data-context-scroll={`directory:${contextRevision([search, kind, turn, view.inputCursors.at(-1), view.outputCursors.at(-1)])}`} className="max-h-[60vh] overflow-y-auto" style={{ overflowAnchor: "none" }} aria-label={t("File directory")}>
      {(["input", "output"] as const).map(group => {
        const items = filtered.filter(row => row.kind === group);
        return (kind === "all" || kind === group) && (items.length || (group === "input" ? view.inputCursors : view.outputCursors).length > 1) ? <section key={group} aria-label={group === "input" ? t("Run input files") : t("Run output files")} className="mb-3">
          <h3 className="mb-1 text-xs font-semibold">{group === "input" ? t("Input files") : t("Output files")} · {items.length}</h3>
          {items.map(row => <div key={row.key} className={`mb-1 flex min-w-0 items-center gap-1 rounded-md border ${selected === row.key ? "border-[#6fa43f] bg-[#f3faea]" : "border-black/10"}`}>
            <button type="button" aria-label={t("Preview {{0}}", { 0: row.name })} aria-pressed={selected === row.key} className="min-h-11 min-w-0 flex-1 p-2 text-left" onClick={() => { setSelected(row.key); if (layout === "list") changeLayout("both"); }}>
              <span className="flex min-w-0 items-center gap-2"><FileText size={14} className="shrink-0" /><span className="truncate text-sm font-semibold" title={row.name}>{row.name}</span></span>
              <span className="mt-1 block truncate text-xs text-[#858481]">{row.turns.length ? t("Turn {{0}}", { 0: row.turns.join(", ") }) : t("Turn not recorded")} · {row.size === null ? t("Image") : formatSize(row.size)} · {row.state === "ready" ? row.source : fileState(row.state)}</span>
            </button>
            <button type="button" className="inline-flex h-11 w-11 shrink-0 items-center justify-center disabled:opacity-40" disabled={row.state !== "ready" || Boolean(downloadId)} aria-label={t("Download {{0}}", { 0: row.name })} onClick={() => void download(row)}><Download size={15} /></button>
          </div>)}
          {((group === "input" ? view.inputCursors : view.outputCursors).length > 1 || (group === "input" ? inputs.data : outputPage.data)?.next_cursor) ? <div className="flex items-center justify-between gap-2 pt-2" aria-label={t("{{0}} file pages", { 0: group })}>
            <button className={control} disabled={(group === "input" ? view.inputCursors : view.outputCursors).length <= 1} onClick={() => setView(current => ({ ...current, selected: "", [group === "input" ? "inputCursors" : "outputCursors"]: (group === "input" ? current.inputCursors : current.outputCursors).slice(0, -1) }))}>{group === "input" ? t("Previous inputs") : t("Previous outputs")}</button>
            <button className={control} disabled={!(group === "input" ? inputs.data : outputPage.data)?.next_cursor} onClick={() => setView(current => ({ ...current, selected: "", pdfPage: 1, [group === "input" ? "inputCursors" : "outputCursors"]: [...(group === "input" ? current.inputCursors : current.outputCursors), (group === "input" ? inputs.data : outputPage.data)!.next_cursor!] }))}>{group === "input" ? t("Next inputs") : t("Next outputs")}</button>
          </div> : null}
        </section> : null;
      })}
    </div>
    </div>
    <div role="separator" aria-label={t("Resize file directory")} aria-orientation="vertical" aria-valuemin={22} aria-valuemax={45} aria-valuenow={Math.round(directoryWidth)} aria-valuetext={`${Math.round(directoryWidth)} percent directory width`} tabIndex={0} className="run-files-divider"
      onDoubleClick={() => resizeDirectory(30)}
      onKeyDown={event => {
        const value = ({ ArrowLeft: directoryWidth - 2, ArrowRight: directoryWidth + 2, Home: 22, End: 45 } as Record<string, number>)[event.key];
        if (value !== undefined) { event.preventDefault(); resizeDirectory(value); }
      }}
      onPointerDown={event => { if (event.button !== 0) return; event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); }}
      onPointerMove={event => {
        if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
        const bounds = split.current?.getBoundingClientRect();
        if (bounds) resizeDirectory(100 * (event.clientX - bounds.left) / bounds.width);
      }}
      onPointerUp={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }} />
    <section aria-label={t("File preview")} className={`run-files-preview min-w-0 rounded-lg border border-black/15 bg-white ${!file ? "run-files-preview--empty" : ""}`}>
      {file ? <>
      <header className="flex min-w-0 items-center gap-2 border-b border-black/10 p-2"><button type="button" className="run-files-back h-11 w-11 shrink-0" aria-label={t("Back to files")} onClick={backToFiles}><ArrowLeft size={16} className="mx-auto" /></button><h3 className="min-w-0 flex-1 truncate text-sm font-semibold" title={file.name}>{file.name}</h3><button type="button" className="h-11 w-11 shrink-0" aria-label={t("Close file preview")} onClick={backToFiles}><X size={16} className="mx-auto" /></button></header>
      {preview}
      <div className="border-t border-black/10 p-2">{actions}</div>
      <p className="px-3 pb-3 text-xs text-[#858481]">{t("Adds a checked attachment to your draft. The Agent receives it when you send the message.")}</p>
      </> : <div className="p-6 text-sm text-[#858481]"><p>{t("Select a file to preview it here.")}</p>{layout === "preview" ? <button type="button" className={`${control} mt-3`} onClick={() => changeLayout("list")}>{t("Show file list")}</button> : null}</div>}
    </section>
    </div>
    </div>
    {downloadError ? <p role="alert" className="text-xs text-red-700">{downloadError}</p> : null}
  </div>;
  return <section aria-label={t("Run files workspace")} className="grid min-w-0 gap-3">
    <div className="flex items-center justify-between gap-2 text-xs text-[#535350]"><span>{t("Inputs and outputs")}</span><button type="button" className={`${control} inline-flex items-center gap-2`} onClick={() => setExpanded(true)}><Maximize2 size={14} />{t("Expand Files")}</button></div>
    {!expanded ? workspace : null}
    <NexilumeDialog open={expanded} onClose={() => setExpanded(false)} title={t("Files")} description={t("Browse files alongside their preview. Drag the divider to adjust the space.")} size="large">{expanded ? workspace : null}</NexilumeDialog>
  </section>;
}
function formatSize(value: number) { return value < 1024 ? `${value} B` : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KiB` : `${(value / 1024 ** 2).toFixed(1)} MiB`; }
function fileState(state: string) { return ({ pending: t("Preparing snapshot"), failed: t("Snapshot failed"), blocked: t("Blocked by policy"), scanning: t("Security scan in progress") } as Record<string, string>)[state] || state; }

function FilePreview({ file, runId, displayToken, pdfPage, onPdfPage }: { file: RunFile; runId: string; displayToken: string; pdfPage: number; onPdfPage: (page: number) => void }) {
  useLocale();
  const { apiContext } = useAuth();
  const [content, setContent] = useState<{ text?: string; url?: string; data?: Uint8Array<ArrayBuffer> } | null>(null);
  const [error, setError] = useState(""); const [attempt, setAttempt] = useState(0);
  const kind = filePreviewKind(file);
  const limit = (kind === "text" || kind === "markdown" ? 1 : 20) * 1024 ** 2;
  const unavailable = file.state !== "ready" ? fileState(file.state) : kind === "unsupported" ? t("Preview is not supported for this file type. Download it to open locally.") : file.size !== null && file.size > limit ? t("This file is too large to preview. Download it instead.") : "";
  useEffect(() => {
    setContent(null); setError(""); if (unavailable || !displayToken) return;
    const controller = new AbortController(); let url = "";
    async function load() {
      try {
        const blob = await api.previewRunFile(apiContext, runId, file.path, displayToken, limit, controller.signal);
        const bytes = new Uint8Array(await blob.arrayBuffer());
        if (controller.signal.aborted) return;
        if (kind === "text" || kind === "markdown") {
          const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
          if (text.includes("\0")) throw new Error(t("This file is not supported UTF-8 text. Download it instead."));
          setContent({ text });
        } else {
          let mime = "";
          if (kind === "pdf" && new TextDecoder().decode(bytes.slice(0, 5)) === "%PDF-") mime = "application/pdf";
          if (kind === "image") {
            if (bytes[0] === 137 && bytes[1] === 80 && bytes[2] === 78 && bytes[3] === 71) mime = "image/png";
            else if (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) mime = "image/jpeg";
            else if (new TextDecoder().decode(bytes.slice(0, 4)) === "RIFF" && new TextDecoder().decode(bytes.slice(8, 12)) === "WEBP") mime = "image/webp";
          }
          if (!mime) throw new Error(t("File content does not match a supported preview format."));
          if (kind === "pdf") setContent({ data: bytes });
          else { url = URL.createObjectURL(new Blob([bytes], { type: mime })); setContent({ url }); }
        }
      } catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : t("Preview unavailable.")); }
    }
    void load();
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [apiContext, runId, file.path, displayToken, kind, limit, unavailable, attempt]);
  if (unavailable) return <p className="p-3 text-sm">{unavailable}</p>;
  if (error) return <div role="alert" className="p-3 text-sm"><p>{error}</p><button type="button" className={`${control} mt-2`} onClick={() => setAttempt(value => value + 1)}>{t("Retry preview")}</button></div>;
  if (!content) return <p role="status" className="p-3 text-sm">{t("Loading preview…")}</p>;
  if (kind === "image") return <img src={content.url} alt={file.name} className="max-h-[65vh] w-full object-contain" onError={() => setError(t("Image could not be decoded. Download it instead."))} />;
  if (kind === "pdf" && content.data) return <Suspense fallback={<p role="status" className="p-3 text-sm">{t("Loading PDF viewer…")}</p>}><PdfPreview data={content.data} name={file.name} pageNumber={pdfPage} onPageChange={onPdfPage} scrollKey={`preview:${file.key}:${pdfPage}`} /></Suspense>;
  return <div data-context-scroll={`preview:${file.key}`} aria-label={t("File preview content")} className="max-h-[60vh] overflow-auto p-3 [overflow-wrap:anywhere]" style={{ overflowAnchor: "none" }}>{kind === "markdown" ? <ContentMarkdown value={content.text} empty={t("Empty file")} /> : <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-5">{content.text || t("Empty file")}</pre>}</div>;
}
