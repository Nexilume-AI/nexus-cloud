import { t, useLocale } from "../localization";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type AgentFileTransfer } from "../lib/api";
import { useAuth } from "../app/AuthContext";
import type { AttachmentIntake } from "./RunImageAttachments";

type Entry = { file: File; transfer?: AgentFileTransfer; error?: string };
export const RunFileAttachments = forwardRef<AttachmentIntake, {
  agentId: string; onChange: (ids: string[]) => void; onBusyChange: (busy: boolean) => void; disabled: boolean;
  onTransfersChange?: (files: AgentFileTransfer[]) => void;
  onDraftTransfersChange?: (files: AgentFileTransfer[]) => void;
  reserved?: number;
}>(function RunFileAttachments({ agentId, onChange, onBusyChange, disabled, onTransfersChange, onDraftTransfersChange, reserved = 0 }, ref) {
  const { apiContext } = useAuth();
  const [entries, setEntries] = useState<Entry[]>([]);
  const [error, setError] = useState("");
  const [maxBytes, setMaxBytes] = useState(0);
  const [maxFiles, setMaxFiles] = useState(8);
  const [limitError, setLimitError] = useState(false);
  const entriesRef = useRef(entries); entriesRef.current = entries;
  const jobs = useRef<Entry[]>([]);
  const active = useRef(true);
  const abort = useRef<AbortController | null>(null);
  const activeFile = useRef<File | null>(null);
  const canceled = useRef(new Set<File>());
  const [working, setWorking] = useState(false);
  async function loadLimits() {
    setLimitError(false);
    try {
      const value = await api.agentFileLimits(apiContext);
      if (active.current) { setMaxBytes(value.max_file_bytes); setMaxFiles(value.max_files ?? 8); }
    } catch { if (active.current) setLimitError(true); }
  }
  useEffect(() => {
    active.current = true;
    void loadLimits();
    return () => { active.current = false; abort.current?.abort(); jobs.current = []; };
  }, [agentId, apiContext.token, apiContext.tenantId, apiContext.projectId]);
  useEffect(() => {
    onChange(entries.filter(item => item.transfer?.state === "ready").map(item => item.transfer!.file_id));
    onTransfersChange?.(entries.filter(item => item.transfer?.state === "ready").map(item => item.transfer!));
    onDraftTransfersChange?.(entries.filter(item => item.transfer).map(item => item.transfer!));
    onBusyChange(entries.some(item => item.transfer?.state !== "ready"));
  }, [entries, onChange, onBusyChange, onTransfersChange, onDraftTransfersChange]);
  const update = (file: File, changes: Partial<Entry>) => {
    if (active.current && !canceled.current.has(file)) setEntries(current => current.map(item => item.file === file ? { ...item, ...changes } : item));
  };
  async function transfer(entry: Entry) {
    if (activeFile.current || !active.current) return;
    setWorking(true);
    activeFile.current = entry.file;
    abort.current = new AbortController();
    update(entry.file, { error: "" });
    try {
      let state = entry.transfer ? await api.agentFileStatus(apiContext, entry.transfer.file_id) : await api.createAgentFile(apiContext, agentId, entry.file);
      if (canceled.current.has(entry.file) || !active.current) { await api.cancelAgentFile(apiContext, state.file_id); return; }
      update(entry.file, { transfer: state });
      while (state.received_bytes < entry.file.size && active.current && !canceled.current.has(entry.file)) {
        const offset = state.received_bytes;
        const chunk = entry.file.slice(offset, offset + state.chunk_bytes);
        const digest = await crypto.subtle.digest("SHA-256", await chunk.arrayBuffer());
        const sha = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");
        state = await api.putAgentFileChunk(apiContext, state.file_id, offset, chunk, sha, abort.current.signal);
        update(entry.file, { transfer: state });
      }
      if (!active.current || canceled.current.has(entry.file)) return;
      state = await api.completeAgentFile(apiContext, state.file_id);
      update(entry.file, { transfer: state });
      while (["queued", "processing"].includes(state.state) && active.current && !canceled.current.has(entry.file)) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        state = await api.agentFileStatus(apiContext, state.file_id);
        update(entry.file, { transfer: state });
      }
      if (state.state !== "ready") throw new Error("File verification failed. Retry or remove the upload.");
    } catch (cause) {
      update(entry.file, { error: cause instanceof Error ? cause.message : "Upload interrupted. Retry continues from the last confirmed chunk." });
    } finally {
      activeFile.current = null;
      if (active.current) { setWorking(false); const next = jobs.current.shift(); if (next) void transfer(next); }
    }
  }
  useImperativeHandle(ref, () => ({ add(files) {
    if (disabled) return;
    if (!maxBytes) { setError("File limits are not available yet. Wait or retry the file service, then select these files again."); return; }
    const rejected: string[] = [];
    for (const file of files) {
      if (file.size > maxBytes) { rejected.push(`${file.name}: exceeds the per-file limit.`); continue; }
      if (entriesRef.current.length + reserved >= maxFiles) { rejected.push(`${file.name}: at most ${maxFiles} files and recordings per message, including saved attachments.`); continue; }
      const entry = { file };
      entriesRef.current = [...entriesRef.current, entry]; jobs.current.push(entry);
    }
    setEntries(entriesRef.current); setError(rejected.join("\n"));
    if (!activeFile.current) { const next = jobs.current.shift(); if (next) void transfer(next); }
  }}));
  async function remove(entry: Entry) {
    canceled.current.add(entry.file);
    jobs.current = jobs.current.filter(item => item.file !== entry.file);
    if (activeFile.current === entry.file) abort.current?.abort();
    if (entry.transfer) {
      try { await api.cancelAgentFile(apiContext, entry.transfer.file_id); }
      catch { setError("Upload cancellation could not be confirmed; it expires automatically after 24 hours."); }
    }
    setEntries(current => current.filter(item => item.file !== entry.file));
  }
  return <section aria-label={t("Private file attachments")} className={entries.length || limitError || error ? "my-2 min-w-0" : "min-w-0"}>
    {entries.length ? <p className="text-xs text-muted">{maxBytes ? t("{{0}} per file", { 0: maxBytes >= 1024**3 ? `${(maxBytes / 1024**3).toFixed(1)} GiB` : `${Math.ceil(maxBytes / 1024**2)} MiB` }) : t("Loading file limit…")}{" "}{t("· Uploading does not mean the Agent has read the file.")}</p> : null}
    {limitError ? <p role="alert" className="text-sm text-red-700">{t("File service unavailable.")}{" "}<button type="button" className="min-h-11 underline" onClick={() => void loadLimits()}>{t("Retry file service")}</button></p> : null}
    {entries.map((entry, index) => <div key={index} className="mt-2 border-t border-black/10 pt-2 text-sm">
      <div className="break-all">{entry.file.name}</div>
      <progress aria-label={t("Upload {{0}}", { 0: entry.file.name })} max={entry.file.size || 1} value={entry.transfer?.received_bytes || 0} className="w-full" />
      <div aria-live="polite">{entry.transfer?.state === "ready" ? t("Ready to send") : entry.transfer?.state === "queued" || entry.transfer?.state === "processing" ? t("Verifying file…") : t("{{0}}% uploaded", { 0: Math.floor((entry.transfer?.received_bytes || 0) / Math.max(entry.file.size,1) * 100) })}</div>
      {entry.error ? <p role="alert" className="text-red-700">{entry.error}</p> : null}
      {entry.error ? <button type="button" disabled={working} className="btn min-h-11" onClick={() => void transfer(entry)}>{t("Retry upload")}</button> : null}
      <button type="button" className="btn min-h-11" disabled={disabled} onClick={() => void remove(entry)}>{t("Remove")}{" "}{entry.file.name}</button>
    </div>)}
    {error ? <div role="alert" className="whitespace-pre-wrap text-sm text-red-700">{error}<button type="button" className="block min-h-11 underline" onClick={() => setError("")}>{t("Dismiss rejected files")}</button></div> : null}
  </section>;
});

export function RunInputFiles({ runId, displayToken }: { runId: string; displayToken: string }) {
  useLocale();
  const { apiContext } = useAuth();
  const files = useQuery({ queryKey: ["private-run-input-files", apiContext, runId, displayToken],
    queryFn: () => api.privateRunFiles(apiContext, runId, displayToken), enabled: Boolean(displayToken) });
  const download = useMutation({
    mutationFn: (file: AgentFileTransfer) => api.prepareAgentDownload(apiContext, `/api/v1/agent-runs/${runId}/files/${file.file_id}/download/`, displayToken),
    onSuccess: value => { const anchor = document.createElement("a"); anchor.href = value.url; anchor.download = value.file_name; document.body.appendChild(anchor); anchor.click(); anchor.remove(); },
  });
  return <section aria-label={t("Run input files")}>
    <h3 className="text-sm font-semibold">{t("Input files")}</h3>
    {files.isPending ? <p className="text-sm">{t("Loading files…")}</p> : null}
    {files.isError ? <button type="button" className="btn min-h-11" onClick={() => void files.refetch()}>{t("Retry input files")}</button> : null}
    {files.data?.length === 0 ? <p className="text-sm text-muted">{t("No input files for this Run.")}</p> : null}
    {files.data?.map(file => <div key={file.file_id} className="my-2 border-b border-black/10 py-2">
      <p className="break-all text-sm">{file.name}</p><p className="text-xs text-muted">{(file.size_bytes / 1024**2).toFixed(1)}{" "}{t("MiB · private input")}</p>
      <button type="button" className="btn min-h-11" disabled={download.isPending} onClick={() => download.mutate(file)}>{t("Download")}{" "}{file.name}</button>
    </div>)}
    {download.isError ? <p role="alert" className="text-sm text-red-700">{t("Download unavailable. Retry to renew access.")}</p> : null}
  </section>;
}
