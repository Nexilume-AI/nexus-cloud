import { providerImportStatusLabel } from "../lib/providerImportStatus";
import { t, useLocale, getLocale } from "../localization";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Download, Upload } from "lucide-react";
import { useAuth } from "../app/AuthContext";
import { useApplicationDistribution } from "../app/distribution";
import { api } from "../lib/api";
import type { ProviderImportBatch } from "../lib/providerImportTypes";
import { NexilumeDialog, NexilumeTabs } from "./NexilumeControls";

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = filename; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ProviderImportDialog() {
  useLocale();
  const { apiContext, isContextReady } = useAuth();
  const scopedOwnership = Boolean(useApplicationDistribution().resourceOwnership);
  const queryClient = useQueryClient();
  const capabilities = useQuery({ queryKey: ["provider-import-capabilities", apiContext], queryFn: () => api.providerImportCapabilities(apiContext), enabled: isContextReady });
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"file" | "paste">("file");
  const [file, setFile] = useState<File | null>(null);
  const [apisText, setApisText] = useState("");
  const [duplicates, setDuplicates] = useState<"skip" | "update">("skip");
  const [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  const [batch, setBatch] = useState<ProviderImportBatch | null>(null);
  const recent = useQuery({ queryKey: ["provider-imports", apiContext], queryFn: () => api.providerImports(apiContext), enabled: open && !batch && !!capabilities.data?.can_import });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmUpdates, setConfirmUpdates] = useState(false);
  const [allowPartial, setAllowPartial] = useState(false);
  const [page, setPage] = useState(0);
  const [filter, setFilter] = useState("all");
  const pause = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; pause.current = true; }; }, []);
  const context = scopedOwnership ? (apiContext.projectId ? t("the selected Project in this Organization") : t("this Organization (Organization-owned)")) : "your personal installation";
  const inactive = batch && (["expired", "discarded", "complete"].includes(batch.status) || Date.parse(batch.expires_at) <= Date.now());
  const rows = (batch?.results ?? []).filter((row) => filter === "all" || (filter === "attention" ? ["failed", "invalid"].includes(row.status) : ["created", "updated", "skipped"].includes(row.status)));
  const mayCommit = batch && !inactive && (batch.duplicate_mode !== "update" || confirmUpdates) && (!batch.invalid || allowPartial);
  function changed() { setRequestKey(crypto.randomUUID()); setError(""); }
  async function action(fn: () => Promise<void>) {
    setBusy(true); setError("");
    try { await fn(); } catch (err) { if (mounted.current) setError(err instanceof Error ? err.message : t("Import request failed. Your existing results remain available.")); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function preview() {
    const body = new FormData();
    body.append("request_key", requestKey); body.append("duplicate_mode", duplicates);
    if (mode === "file" && file) { body.append("file", file); }
    else { body.append("apis_text", apisText); }
    const next = await api.previewProviderImport(apiContext, body);
    if (!mounted.current) return;
    setBatch(next); setFile(null); setApisText("");
    setPage(0); setConfirmUpdates(false); setAllowPartial(false);
  }
  async function commit(retry = false) {
    if (!batch || !mayCommit) return;
    pause.current = false;
    let current = batch;
    do {
      current = await api.commitProviderImport(apiContext, current.id, { confirm_updates: confirmUpdates, allow_partial: allowPartial, retry_failed: retry });
      retry = false;
      if (!mounted.current) return;
      setBatch(current);
      await Promise.all(["provider-connections", "provider-runtimes", "topology"].map((key) => queryClient.invalidateQueries({ queryKey: [key] })));
    } while (current.remaining > 0 && !pause.current && mounted.current);
  }
  async function fresh() {
    if (batch && !["complete", "discarded", "expired"].includes(batch.status)) await api.discardProviderImport(apiContext, batch.id);
    setBatch(null); setRequestKey(crypto.randomUUID()); setPage(0); setFilter("all");
    await queryClient.invalidateQueries({ queryKey: ["provider-imports"] });
  }
  const canPreview = mode === "file" ? !!file : !!apisText.trim();
  return <>
    <button className="btn btn-secondary min-h-11" disabled={!capabilities.data?.can_import} title={capabilities.isError ? t("Import permissions could not be loaded") : capabilities.data?.can_import ? t("Import API connections from a spreadsheet") : t("Provider creation permission required in the selected scope")} onClick={() => setOpen(true)}><Upload size={16} />{" "}{t("Import APIs")}</button>
    {capabilities.isError && <button className="btn btn-secondary min-h-11" onClick={() => void capabilities.refetch()}>{t("Retry import access")}</button>}
    <NexilumeDialog open={open} onClose={() => setOpen(false)} busy={busy} size="large" title={t("Import Provider APIs")} eyebrow={t("SUPPLY / BULK IMPORT")}
      description={t("Import API-key connections only. Model Offers are discovered automatically by the Runtime.")}>
      <div className="grid min-w-0 gap-5 text-base">
        <p className="border-l-2 border-ink pl-3">{t("Destination:")}{" "}{context}{t(". API keys are encrypted and excluded from previews and reports.")}</p>
        {!batch ? <>
          {!!recent.data?.length && <details className="border-b border-line pb-3"><summary className="min-h-11 cursor-pointer content-center">{t("Recent imports — resume or review")}</summary><div className="max-h-56 overflow-auto">{recent.data.map((item) => <button key={item.id} className="flex min-h-11 w-full flex-wrap justify-between gap-2 border-t border-line py-2 text-left" disabled={busy} onClick={() => void action(async () => { setBatch(await api.providerImport(apiContext, item.id)); setPage(0); setFilter("all"); setConfirmUpdates(false); setAllowPartial(false); setFile(null); setApisText(""); })}><span>{new Date(item.created_at).toLocaleString(getLocale())} · {item.rows}{" "}{t("rows")}</span><span>{providerImportStatusLabel(item.status)}</span></button>)}</div></details>}
          {recent.isError && <p role="status">{t("Recent imports could not load.")}{" "}<button className="underline" onClick={() => void recent.refetch()}>{t("Retry recent imports")}</button></p>}
          <div className="flex flex-wrap gap-2" aria-label={t("Import templates")}>
            {(["xlsx", "apis_csv"] as const).map((format) => <button key={format} className="btn btn-secondary min-h-11" disabled={busy} onClick={() => void action(async () => download(await api.providerImportTemplate(apiContext, format), format === "xlsx" ? "nexilume-provider-import.xlsx" : `${format}.csv`))}><Download size={16} /> {format === "xlsx" ? t("Excel template") : t("CSV template")}</button>)}
          </div>
          <p>{t("One API per")}{" "}<code>api_ref</code>{t(". Import creates the connection and its Runtime; start the Runtime to use automatic Model Offer discovery. No model list, pricing or model mappings are imported.")}</p>
          <NexilumeTabs idBase="provider-import-input" label={t("Import input")} value={mode} onChange={(next) => { setMode(next); changed(); }} options={[{ value: "file", label: t("Upload file") }, { value: "paste", label: t("Paste table") }]} />
          <section role="tabpanel" id={`provider-import-input-panel-${mode}`} aria-labelledby={`provider-import-input-tab-${mode}`} className="grid gap-4">
            {mode === "file" ? <>
              <label className="grid gap-2">{t("API workbook or CSV")}<input className="input min-h-11" type="file" accept=".xlsx,.csv" disabled={busy} onChange={(e) => { setFile(e.target.files?.[0] ?? null); changed(); }} /></label>

            </> : <>
              <label className="grid gap-2">{t("API table, including headers")}<textarea className="input min-h-36 font-mono" value={apisText} spellCheck={false} autoComplete="off" disabled={busy} onChange={(e) => { setApisText(e.target.value); changed(); }} placeholder={t("api_ref name base_url api_key")} /></label>

              <p className="text-muted">{t("Pasted tables may contain plaintext keys. Use a private screen; the inputs are cleared once preview succeeds.")}</p>
            </>}
          </section>
          <label className="grid gap-2">{t("Existing API identities")}<select className="select min-h-11" value={duplicates} disabled={busy} onChange={(e) => { setDuplicates(e.target.value as "skip" | "update"); changed(); }}><option value="skip">{t("Skip existing APIs (default)")}</option><option value="update" disabled={capabilities.data?.can_update_existing === false}>{t("Update matching APIs after confirmation")}</option></select></label>
          {capabilities.data?.can_update_existing === false && <p className="text-muted">{t("You can import new APIs. Updating existing APIs requires Provider management permission.")}</p>}
          <p className="text-muted">{t("Up to 100 APIs; 2 MiB per file. XLSX uses an APIs sheet. No formulas, macros or external links. Empty keys preserve existing credentials only during updates.")}</p>
          {duplicates === "update" && <p className="border border-line p-3">{t("Updates require a stopped, unpublished Direct API in the same scope with no Source dependencies. Existing Model Offers are not edited by import; discovery remains responsible for model data.")}</p>}
          <button className="btn btn-primary min-h-11 w-fit" disabled={busy || !canPreview} onClick={() => void action(preview)}>{busy ? t("Preparing preview…") : t("Validate & preview")}</button>
        </> : <>
          <div className="flex flex-wrap gap-x-6 gap-y-2 border-y border-line py-3" role="status"><span>{batch.remaining}{" "}{t("ready")}</span><span>{batch.results.filter((r) => ["created", "updated"].includes(r.status)).length}{" "}{t("imported")}</span><span>{batch.results.filter((r) => r.status === "skipped").length}{" "}{t("skipped")}</span><span>{batch.invalid + batch.failed}{" "}{t("need attention")}</span><span>{batch.status}</span></div>
          <p>{t("Preview expires")}{" "}{new Date(batch.expires_at).toLocaleTimeString(getLocale())}{t(". No upstream API calls are made during import. Start the Runtime for automatic Model Offer discovery and health checks.")}</p>
          {inactive && batch.status !== "complete" && <p role="status">{t("This preview is no longer executable. Start a new import; completed APIs are not undone.")}</p>}
          <label className="grid gap-2">{t("Result filter")}<select className="select min-h-11" value={filter} onChange={(e) => { setFilter(e.target.value); setPage(0); }}><option value="all">{t("All rows")}</option><option value="attention">{t("Errors only")}</option><option value="done">{t("Completed / skipped")}</option></select></label>
          <div className="grid min-w-0 gap-0" aria-label={t("Import row results")}>
            {rows.slice(page * 20, (page + 1) * 20).map((row) => <article key={`${row.sheet}-${row.line}`} className="grid min-w-0 gap-1 border-b border-line py-3">
              <p className="break-words font-semibold">{row.sheet}{" "}{t("row")}{" "}{row.line} · {row.name || row.api_ref} · {row.status}</p>
              <p className="break-all font-mono text-sm">{row.api_ref}{row.action ? ` · ${row.action}` : ""}</p>
              <p className="break-words">{row.message}</p>
              {row.connection_id && <Link className="min-h-11 w-fit content-center underline" aria-disabled={busy} tabIndex={busy ? -1 : 0} to={`/providers?provider=${encodeURIComponent(row.connection_id)}`} onClick={(event) => { if (busy) event.preventDefault(); else setOpen(false); }}>{t("Open Provider")}</Link>}
            </article>)}
          </div>
          <nav className="flex items-center gap-3" aria-label={t("Import result pages")}><button className="btn btn-secondary min-h-11" disabled={page === 0} onClick={() => setPage(page - 1)}>{t("Previous")}</button><span>{page + 1} / {Math.max(1, Math.ceil(rows.length / 20))}</span><button className="btn btn-secondary min-h-11" disabled={(page + 1) * 20 >= rows.length} onClick={() => setPage(page + 1)}>{t("Next")}</button></nav>
          {!inactive && <>
            {batch.duplicate_mode === "update" && <label className="flex min-h-11 items-center gap-3"><input type="checkbox" checked={confirmUpdates} disabled={busy} onChange={(e) => setConfirmUpdates(e.target.checked)} />{t("I confirm updating the matching API configurations shown above.")}</label>}
            {!!batch.invalid && <label className="flex min-h-11 items-center gap-3"><input type="checkbox" checked={allowPartial} disabled={busy} onChange={(e) => setAllowPartial(e.target.checked)} />{t("Import valid APIs only; leave invalid rows unchanged.")}</label>}
          </>}
          <div className="flex flex-wrap gap-2">
            {batch.remaining > 0 && !inactive && <button className="btn btn-primary min-h-11" disabled={busy || !mayCommit} onClick={() => void action(() => commit())}>{busy ? t("Importing…") : t("Import / continue valid APIs")}</button>}
            {batch.failed > 0 && !inactive && <button className="btn btn-secondary min-h-11" disabled={busy || !mayCommit} onClick={() => void action(() => commit(true))}>{t("Retry failed APIs only")}</button>}
            {busy && <button className="btn btn-secondary min-h-11" onClick={() => { pause.current = true; }}>{t("Pause after this batch")}</button>}
            <button className="btn btn-secondary min-h-11" disabled={busy} onClick={() => void action(async () => setBatch(await api.providerImport(apiContext, batch.id)))}>{t("Refresh results")}</button>
            <button className="btn btn-secondary min-h-11" disabled={busy} onClick={() => void action(async () => download(await api.providerImportReport(apiContext, batch.id), "provider-import-results.csv"))}>{t("Download safe report")}</button>
            <button className="btn btn-secondary min-h-11" disabled={busy} onClick={() => void action(fresh)}>{t("Start new import")}</button>
          </div>
        </>}
        {error && <p role="alert" className="break-words border border-line p-3">{error}</p>}
        <div className="flex justify-end"><button className="btn btn-secondary min-h-11" disabled={busy} onClick={() => setOpen(false)}>{t("Close")}</button></div>
      </div>
    </NexilumeDialog>
  </>;
}
