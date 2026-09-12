import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Loader2, X } from "lucide-react";
import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import { IMAGE_UPLOAD_LIMIT, ImagePreparationError, prepareImageUpload, validateImageSource } from "../lib/imageUpload";
import { NexilumeDialog } from "./NexilumeControls";

export type RunImageAttachment = { asset_id: string; file: File; originalSize?: number };
export type AttachmentIntake = { add: (files: File[]) => void };
type PendingImage = { file: File; error?: string; prepared?: Awaited<ReturnType<typeof prepareImageUpload>> };

export function AttachmentImagePreview({ src, name }: { src: string; name: string }) {
  const [open, setOpen] = useState(false);
  const [dimensions, setDimensions] = useState("");
  return <>
    <button type="button" aria-label={`Preview ${name}`} className="flex min-h-11 min-w-11 items-center justify-center rounded-md bg-black/5" onClick={() => setOpen(true)}>
      <img src={src} alt={name} onLoad={event => setDimensions(`${event.currentTarget.naturalWidth} × ${event.currentTarget.naturalHeight}`)} className="h-14 w-14 rounded-md object-contain" />
    </button>
    <NexilumeDialog open={open} title={name} description={dimensions || "Private image preview"} onClose={() => setOpen(false)} size="large">
      <img src={src} alt={name} className="max-h-[70vh] w-full object-contain" />
    </NexilumeDialog>
  </>;
}

function LocalPreview({ file }: { file: File }) {
  const [url, setUrl] = useState("");
  useEffect(() => { const next = URL.createObjectURL(file); setUrl(next); return () => URL.revokeObjectURL(next); }, [file]);
  return url ? <AttachmentImagePreview src={url} name={file.name} /> : null;
}

/** File references and vision inputs are distinct contracts. Never silently substitute. */
export const RunImageAttachments = forwardRef<AttachmentIntake, {
  value: RunImageAttachment[]; onChange: (value: RunImageAttachment[]) => void;
  onBusyChange: (busy: boolean) => void; disabled: boolean; reserved?: number;
}>(function RunImageAttachments({ value, onChange, onBusyChange, disabled, reserved = 0 }, ref) {
  const { apiContext } = useAuth();
  const active = useRef(true);
  const latest = useRef(value); latest.current = value;
  const [pending, setPending] = useState<PendingImage[]>([]);
  const pendingRef = useRef(pending);
  const jobs = useRef<File[]>([]);
  const running = useRef<{ file: File; controller: AbortController } | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  function update(next: PendingImage[]) { pendingRef.current = next; if (active.current) setPending(next); }
  useEffect(() => { active.current = true; return () => { active.current = false; running.current?.controller.abort(); jobs.current = []; }; }, []);
  useEffect(() => onBusyChange(pending.length > 0), [pending.length, onBusyChange]);
  async function pump() {
    if (running.current || !active.current) return;
    const file = jobs.current.shift();
    if (!file) return;
    const controller = new AbortController();
    running.current = { file, controller };
    try {
      const prepared = pendingRef.current.find(row => row.file === file)?.prepared || await prepareImageUpload(file, controller.signal);
      if (controller.signal.aborted || !active.current) return;
      update(pendingRef.current.map(row => row.file === file ? { ...row, prepared } : row));
      const asset = await api.uploadMediaAsset(apiContext, prepared.file, { purpose: "chat_input", expires_at: new Date(Date.now() + 3600_000).toISOString() }, controller.signal);
      if (active.current && pendingRef.current.some(row => row.file === file)) {
        latest.current = [...latest.current, { asset_id: asset.id, ...prepared }];
        onChange(latest.current);
        update(pendingRef.current.filter(row => row.file !== file));
      }
    } catch (error) {
      if (active.current) update(pendingRef.current.map(row => row.file === file ? { ...row, error: error instanceof ImagePreparationError ? error.message : "Upload interrupted. Retry or remove this image; your message is kept." } : row));
    } finally { running.current = null; void pump(); }
  }
  useImperativeHandle(ref, () => ({ add(files) {
    if (disabled) return;
    const rejected: string[] = [];
    for (const file of files) {
      try { validateImageSource(file); } catch (error) { rejected.push(`${file.name}: ${(error as Error).message}`); continue; }
      if (latest.current.length + pendingRef.current.length + reserved >= 4) { rejected.push(`${file.name}: attach at most four images per message, including saved images.`); continue; }
      update([...pendingRef.current, { file }]); jobs.current.push(file);
    }
    setErrors(rejected); void pump();
  }}));
  function remove(file: File) {
    jobs.current = jobs.current.filter(item => item !== file);
    update(pendingRef.current.filter(row => row.file !== file));
    if (running.current?.file === file) running.current.controller.abort();
    latest.current = latest.current.filter(row => row.file !== file); onChange(latest.current);
  }
  if (!value.length && !pending.length && !errors.length) return null;
  return <section aria-label="Private image attachments" className="my-2 grid gap-2">
    {[...value.map(row => ({ file: row.file, ready: true, error: "", prepared: { file: row.file, originalSize: row.originalSize } })), ...pending.map(row => ({ ...row, ready: false }))].map((row, index) => <div key={index} className="flex min-w-0 items-center gap-2 rounded-md border border-black/10 bg-white p-2">
      {row.prepared || row.file.size <= IMAGE_UPLOAD_LIMIT ? <LocalPreview file={row.prepared?.file || row.file} /> : <div className="h-14 w-14 shrink-0 rounded-md bg-black/5" aria-label="Image preview available after preparation" />}
      <div className="min-w-0 flex-1"><p className="truncate text-sm" title={(row.prepared?.file || row.file).name}>{(row.prepared?.file || row.file).name}</p><p className="text-xs text-muted">{Math.ceil((row.prepared?.file || row.file).size / 1024)} KiB · {row.ready ? "Ready to send" : row.error ? "Upload failed" : !row.prepared && row.file.size > IMAGE_UPLOAD_LIMIT ? "Preparing upload copy…" : "Uploading image…"}</p>
        {row.prepared?.originalSize ? <p className="text-xs text-muted">Optimized copy · {Math.ceil(row.prepared.originalSize / 1024)} → {Math.ceil(row.prepared.file.size / 1024)} KiB. Original unchanged.</p> : null}
        {row.error ? <p role="alert" className="text-xs text-red-700">{row.error}</p> : null}
        {row.error ? <button type="button" disabled={disabled} className="min-h-11 text-xs underline" onClick={() => { update(pendingRef.current.map(item => item.file === row.file ? { ...item, error: undefined } : item)); jobs.current.push(row.file); void pump(); }}>Retry image upload</button> : null}</div>
      {!row.ready && !row.error ? <Loader2 size={16} className="shrink-0 animate-spin" /> : null}
      <button type="button" disabled={disabled} aria-label={`Remove image ${row.file.name}`} className="flex min-h-11 min-w-11 items-center justify-center rounded-md hover:bg-black/5" onClick={() => remove(row.file)}><X size={16} /></button>
    </div>)}
    {errors.length ? <div role="alert" className="text-sm text-red-700">{errors.map((error, index) => <p key={index}>{error}</p>)}<button type="button" className="min-h-11 underline" onClick={() => setErrors([])}>Dismiss rejected images</button></div> : null}
  </section>;
});
