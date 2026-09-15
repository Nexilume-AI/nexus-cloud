import { getLocale } from "../localization";
import { t, useLocale } from "../localization";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Check, Code2, Download, Loader2, Plus, Trash2, Upload, X } from "lucide-react";
import { api, ApiError, type ApiContext } from "../lib/api";
import type { AgentPythonBuild } from "../lib/types";
import { NexilumeDialog } from "./NexilumeControls";
import "./agent-python-supply.css";

const steps = [
  { id: "queued", label: "Queued" }, { id: "dependencies", label: "Dependencies" },
  { id: "image", label: "Image" }, { id: "verify", label: "Verify tools" }, { id: "ready", label: "Ready" },
];

export function AgentPythonSupply({ agentId, apiContext, deployedImageId, onChanged, onDeployment }: {
  agentId: string; apiContext: ApiContext; deployedImageId?: string;
  onChanged: () => Promise<unknown>; onDeployment: (jobId: string) => void;
}) {
  useLocale();
  const [open, setOpen] = useState(false);
  const [deployBuild, setDeployBuild] = useState<AgentPythonBuild | null>(null);
  const [deleteBuilds, setDeleteBuilds] = useState<Array<Pick<AgentPythonBuild, "id" | "filename">>>([]);
  const [deleteNotice, setDeleteNotice] = useState("");
  const sectionRef = useRef<HTMLElement>(null);
  const queries = useQueryClient();
  const builds = useQuery({
    queryKey: ["agent-python-builds", apiContext, agentId],
    queryFn: () => api.agentPythonBuilds(apiContext, agentId),
    refetchInterval: (query) => query.state.data?.results.some((b) => ["queued", "running"].includes(b.status)) ? 2000 : false,
  });
  const refreshRef = useRef(onChanged);
  refreshRef.current = onChanged;
  const latestBuild = builds.data?.results[0];
  useEffect(() => {
    if (latestBuild?.status === "succeeded") void refreshRef.current();
  }, [latestBuild?.id, latestBuild?.status]);
  const deploy = useMutation({
    mutationFn: async (build: AgentPythonBuild) => {
      const result = await api.deployAgentRuntime(apiContext, agentId, { env: "prod", image_id: build.image_id! });
      await api.setCurrentAgentRuntimeImage(apiContext, agentId, build.image_id!);
      return result;
    },
    onSuccess: async (result) => { onDeployment(result.job_id || ""); setDeployBuild(null); await onChanged(); },
  });
  const pending = builds.data?.results.some((b) => ["queued", "running"].includes(b.status));
  const failedBuilds = builds.data?.failed_builds ?? builds.data?.results.filter(b => b.status === "failed" && !b.image_id) ?? [];
  const showBulkDelete = failedBuilds.length > 1 || failedBuilds.some(failed => !builds.data?.results.some(row => row.id === failed.id));
  const remove = useMutation({
    mutationFn: (selected: Array<Pick<AgentPythonBuild, "id" | "filename">>) => api.deleteAgentPythonBuilds(apiContext, agentId, selected.map(build => build.id)),
    onSuccess: async (result) => {
      const ids = new Set(result.deleted_ids);
      queries.setQueryData<import("../lib/types").AgentPythonBuildList>(["agent-python-builds", apiContext, agentId], previous =>
        previous ? { ...previous, results: previous.results.filter(build => !ids.has(build.id)),
          failed_builds: previous.failed_builds?.filter(build => !ids.has(build.id)) } : previous);
      setDeleteBuilds([]);
      setDeleteNotice(`${ids.size} failed build${ids.size === 1 ? "" : "s"} deleted. Residual artifacts will be cleaned in the background. Your deployment is unchanged.`);
      // The original row no longer exists when the dialog restores focus.
      window.requestAnimationFrame(() => sectionRef.current?.focus());
      await Promise.all([builds.refetch(), queries.invalidateQueries({ queryKey: ["private", "work-inbox"] })]);
    },
  });
  function confirmDelete(selected: Array<Pick<AgentPythonBuild, "id" | "filename">>) { remove.reset(); setDeleteNotice(""); setDeleteBuilds(selected); }
  return <section ref={sectionRef} tabIndex={-1} className="python-supply" aria-label={t("Python source builds")}>
    <div className="python-supply__intro">
      <span className="python-supply__node" aria-hidden="true"><Code2 size={24} /></span>
      <div><p className="agent-control-eyebrow">{t("Python → Container")}</p><h2>{t("Start with your Agent code")}</h2>
        <p>{t("Upload one Python file. Nexus builds a private image and verifies its tools; you choose when to deploy.")}</p>
      </div>
      <button className="btn btn-primary" disabled={!builds.data?.configuration.enabled || pending || builds.isError} onClick={() => setOpen(true)}><Upload size={16} />{t("Upload Python")}</button>
    </div>
    <div className="python-supply__support"><a href={`${import.meta.env.BASE_URL}examples/nexus_python_agent.py`} download><Download size={16} />{t("Download working example")}</a><a href={`${import.meta.env.BASE_URL}examples/dual_runtime_agent.py`} download><Download size={16} />{t("OpenWrt + Docker example")}</a><span>{t("Python 3.12 · Nexus SDK / FastMCP")}</span></div>
    {builds.isLoading && <p role="status">{t("Loading source builds…")}</p>}
    {builds.isError && <p role="alert">{t("Python builds could not be loaded.")}{" "}<button className="btn" onClick={() => void builds.refetch()}>{t("Retry")}</button></p>}
    {builds.data && !builds.data.configuration.enabled && <p className="python-supply__notice">{t("Python upload is not enabled on this installation yet. The platform operator must configure the build worker. Registry images and Docker archives remain available below.")}</p>}
    {deleteNotice && <p role="status">{deleteNotice}</p>}
    {showBulkDelete && <div className="python-supply__cleanup"><span>{failedBuilds.length}{" "}{t("failed builds available to clear")}</span><button className="btn" disabled={remove.isPending} onClick={() => confirmDelete(failedBuilds)}><Trash2 size={16} />{t("Delete failed builds (")}{failedBuilds.length})</button></div>}
    {builds.data?.results.map((build, index) => <article className="python-build" key={build.id}>
      <div className="python-build__identity"><strong>{build.filename}</strong><span>{build.framework} · {build.entrypoint}</span><span className="python-build__state">{build.status === "succeeded" ? (build.image_removed ? t("Build history") : t("Candidate ready")) : build.status}</span></div>
      {index === 0 && <ol className="python-build__steps" aria-label={t("Build progress")}>{steps.map((step, i) => <li key={step.id} aria-current={step.id === build.stage ? "step" : undefined} className={i <= steps.findIndex(s => s.id === build.stage) ? "is-reached" : ""}><span aria-hidden="true">{i < steps.findIndex(s => s.id === build.stage) ? <Check size={12} /> : i + 1}</span>{t(step.label)}</li>)}</ol>}
      {["queued", "running"].includes(build.status) && <p role="status"><Loader2 size={15} className="animate-spin" />{t("Building in the background. You can leave this page and return later.")}</p>}
      {build.status === "failed" && <p role="alert">{build.error_message}{" "}{t("Your active deployment has not changed.")}</p>}
      {build.status === "failed" && !build.image_id && <button className="btn" aria-label={t("Delete failed build {{0}}", { 0: build.filename })} disabled={remove.isPending} onClick={() => confirmDelete([build])}><Trash2 size={16} />{t("Delete build")}</button>}
      {build.status === "succeeded" && build.image_removed && <p>{t("Image removed · Build history retained. Upload again to create a new deployable version.")}</p>}
      {build.status === "succeeded" && !build.image_removed && <div className="python-build__actions"><p>{build.tool_count} {build.tool_count === 1 ? t("tool") : t("tools")}{" "}{t("verified · no tools invoked")}</p>
        {build.image_id === deployedImageId ? <Link className="btn btn-primary" to={`/agents/${agentId}/private-display`} state={{ returnTo: `/agents/${agentId}/publish` }}>{t("Test Agent privately")}</Link> : <button className="btn" onClick={() => { deploy.reset(); setDeployBuild(build); }}>{t("Deploy this version")}</button>}
      </div>}
      <details><summary>{t("Build details")}</summary><dl><dt>{t("Build ID")}</dt><dd>{build.id}</dd><dt>{t("Source SHA-256")}</dt><dd>{build.source_sha256}</dd></dl>
        {build.diagnostics.map(line => <p key={line}>{line}</p>)}
        {build.dependency_lock.length > 0 && <><h4>{t("Resolved dependency versions")}</h4><pre>{build.dependency_lock.join("\n")}</pre></>}
      </details>
    </article>)}
    {open && <PythonUploadDialog apiContext={apiContext} agentId={agentId} onClose={() => setOpen(false)} onUploaded={async () => { setOpen(false); await builds.refetch(); await onChanged(); }} />}
    <NexilumeDialog open={deleteBuilds.length > 0} title={deleteBuilds.length === 1 ? t("Delete failed build?") : t("Delete failed builds?")} description={t("This cannot be undone. Successful versions and the active deployment will not be changed.")} busy={remove.isPending} onClose={() => setDeleteBuilds([])} footer={<>
      <button className="btn" disabled={remove.isPending} onClick={() => setDeleteBuilds([])}>{t("Cancel")}</button>
      <button className="btn btn-danger" disabled={remove.isPending} onClick={() => remove.mutate(deleteBuilds)}>{remove.isPending ? t("Deleting…") : deleteBuilds.length === 1 ? t("Delete build") : t("Delete {{count}} builds", { count: deleteBuilds.length })}</button>
    </>}>
      <p>{t("Remove")}{" "}{deleteBuilds.length}{" "}{t("failed build")}{getLocale() === "zh-CN" ? "" : deleteBuilds.length === 1 ? "" : "s"}{t(", including uploaded source, saved secrets and related Inbox notifications. Revision space is freed immediately; unreferenced build artifacts are cleaned by the assigned worker.")}</p>
      <ul className="python-supply__delete-list">{deleteBuilds.map(build => <li key={build.id}>{build.filename}</li>)}</ul>
      {remove.isError && <p role="alert">{remove.error instanceof Error ? remove.error.message : t("The builds could not be deleted. Try again.")}</p>}
    </NexilumeDialog>
    <NexilumeDialog open={Boolean(deployBuild)} title={t("Deploy Python Agent")} description={t("Deploy this verified image using the existing Nexus Container runtime. This replaces the running version, if any.")} busy={deploy.isPending} onClose={() => setDeployBuild(null)} footer={<><button className="btn" disabled={deploy.isPending} onClick={() => setDeployBuild(null)}>{t("Cancel")}</button><button className="btn btn-primary" disabled={deploy.isPending} onClick={() => deployBuild && deploy.mutate(deployBuild)}>{deploy.isPending ? t("Queuing deployment…") : t("Deploy version")}</button></>}>
      <p>{deployBuild?.filename} · {deployBuild?.tool_count}{" "}{t("tools")}</p>
      <p>{t("Computer and Mobile access still require caller authorization. This does not publish the Agent.")}</p>
      {deploy.isError && <p role="alert">{deploy.error instanceof Error ? deploy.error.message : t("Deployment could not be queued.")}</p>}
    </NexilumeDialog>
  </section>;
}

function PythonUploadDialog({ agentId, apiContext, onClose, onUploaded }: {
  agentId: string; apiContext: ApiContext; onClose: () => void; onUploaded: () => Promise<void>;
}) {
  useLocale();
  const [file, setFile] = useState<File | null>(null);
  const [requirements, setRequirements] = useState<File | null>(null);
  const [entrypoint, setEntrypoint] = useState("");
  const [secrets, setSecrets] = useState<Array<{ key: string; value: string }>>([]);
  const [localError, setLocalError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const submit = useMutation({
    mutationFn: () => api.uploadAgentPython(apiContext, agentId, file!, requirements, entrypoint, Object.fromEntries(secrets.map(s => [s.key.trim(), s.value]))),
    onSuccess: onUploaded,
  });
  const valid = Boolean(file) && !localError && secrets.every(s => /^[A-Z][A-Z0-9_]*$/.test(s.key.trim()) && s.value) && new Set(secrets.map(s => s.key.trim())).size === secrets.length;
  const errorMessage = submit.error instanceof ApiError || submit.error instanceof Error ? submit.error.message : t("The upload could not be completed.");
  const fieldError = (field: string) => submit.isError && errorMessage.startsWith(`${field}:`) ? errorMessage.slice(field.length + 1).trim() : "";
  return <NexilumeDialog open title={t("Upload Python Agent")} eyebrow={t("Runtime source")} description={t("Upload a single-file NexusAgent, NexusMCPServer or FastMCP Agent. Nexus detects the instance and serves its tools without an OpenWrt Router.")} busy={submit.isPending} onClose={onClose} initialFocusRef={fileRef} footer={<><button className="btn" disabled={submit.isPending} onClick={onClose}>{t("Cancel")}</button><button className="btn btn-primary" disabled={!valid || submit.isPending} onClick={() => submit.mutate()}>{submit.isPending ? t("Uploading…") : t("Build image")}</button></>}>
    <div className="python-upload">
      <label className="python-upload__file">{t("Python file")}{" "}<span>{t("UTF-8 · maximum 1 MiB")}</span><input ref={fileRef} type="file" accept=".py" disabled={submit.isPending} onChange={event => {
        const value = event.target.files?.[0] || null;
        setFile(value); setLocalError(value && (!value.name.endsWith(".py") || value.size > 1048576) ? "Choose a .py file no larger than 1 MiB." : ""); submit.reset();
      }} /></label>
      {(localError || fieldError("file")) && <p role="alert">{localError || fieldError("file")}</p>}
      <label>{t("requirements.txt")}{" "}<span>{t("Optional · public wheel packages only · 32 KiB")}</span><input type="file" accept=".txt" disabled={submit.isPending} onChange={event => setRequirements(event.target.files?.[0] || null)} /></label>
      {fieldError("requirements") && <p role="alert">{fieldError("requirements")}</p>}
      <p>{t("Nexus SDK and FastMCP are included. Dependencies are resolved at build time. Ordinary scripts, system packages and private package indexes are not supported in this first version.")}</p>
      <p>{t("Keep your Agent instance and decorators at module level. Put agent.run() or server.run() inside the __main__ guard. Computer and Mobile declarations are applied only when the verified version is deployed; callers still authorize their own resources.")}</p>
      <details open={Boolean(submit.isError)}><summary>{t("Advanced configuration")}</summary><label>{t("Agent / server instance name")}{" "}<span>{t("Only needed if the file defines multiple instances")}</span><input value={entrypoint} disabled={submit.isPending} onChange={e => setEntrypoint(e.target.value)} placeholder={t("Auto-detect")} maxLength={64} /></label>
        {fieldError("entrypoint") && <p role="alert">{fieldError("entrypoint")}</p>}
        <h3>{t("Runtime secrets")}</h3><p>{t("Encrypted separately and injected only at deployment. Do not put credentials in your Python file. Validation runs without secrets or external network access.")}</p>
        {secrets.map((secret, index) => <div className="python-upload__secret" key={index}><input aria-label={t("Secret {{0}} name", { 0: index + 1 })} placeholder={t("API_KEY")} value={secret.key} disabled={submit.isPending} onChange={e => setSecrets(secrets.map((s, i) => i === index ? { ...s, key: e.target.value } : s))} /><input aria-label={t("Secret {{0}} value", { 0: index + 1 })} type="password" autoComplete="new-password" value={secret.value} disabled={submit.isPending} onChange={e => setSecrets(secrets.map((s, i) => i === index ? { ...s, value: e.target.value } : s))} /><button className="btn" aria-label={t("Remove secret {{0}}", { 0: index + 1 })} disabled={submit.isPending} onClick={() => setSecrets(secrets.filter((_, i) => i !== index))}><X size={16} /></button></div>)}
        <button className="btn" disabled={submit.isPending || secrets.length >= 20} onClick={() => setSecrets([...secrets, { key: "", value: "" }])}><Plus size={15} />{t("Add secret")}</button>
        {fieldError("secrets") && <p role="alert">{fieldError("secrets")}</p>}
      </details>
      {submit.isError && !["file", "requirements", "entrypoint", "secrets"].some(fieldError) && <p role="alert">{errorMessage}{" "}{t("Your files and inputs are retained; correct them and retry.")}</p>}
      <p className="python-supply__notice">{t("Build only — no tools are called, no public listing is created, and no running Agent is replaced.")}</p>
    </div>
  </NexilumeDialog>;
}
