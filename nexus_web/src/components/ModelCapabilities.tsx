import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import { capabilityDraftError, effortInput, type CapabilityOverride, type ModelCapabilities as Capabilities } from "../lib/modelCapabilities";
import { t, useLocale } from "../localization";

const limits = [["context_window", "Context"], ["max_input_tokens", "Input limit"], ["max_output_tokens", "Output limit"]] as const;
const sources: Record<string, string> = { upstream: "Upstream", administrator: "Administrator", provider_override: "Provider override", source_override: "Source override", "models.dev": "models.dev", unknown: "Unknown" };

export function ModelCapabilitiesEditor({ data, provider, busy, error, saved, onSave, onRefresh, onReload }: {
  data: Capabilities; provider: boolean; busy: boolean; error?: string; saved: boolean;
  onSave: (override: CapabilityOverride | null, expected: CapabilityOverride | null) => void; onRefresh: () => void; onReload: () => void;
}) {
  useLocale();
  const [draft, setDraft] = useState<CapabilityOverride>(data.override ?? {});
  const [manual, setManual] = useState(!!data.override);
  const [dirty, setDirty] = useState(false);
  const [baseOverride, setBaseOverride] = useState(data.override);
  const [levelText, setLevelText] = useState(data.override?.reasoning_effort_levels?.join(", ") ?? "");
  useEffect(() => { if (!dirty) { setDraft(data.override ?? {}); setManual(!!data.override); setBaseOverride(data.override);
    setLevelText(data.override?.reasoning_effort_levels?.join(", ") ?? ""); } }, [data, dirty]);
  const editable = data.can_edit && !busy;
  const effective = data.model_info;
  const provenance = data.fields.reasoning_efforts ?? data.fields.reasoning_supported;
  const problem = manual ? capabilityDraftError(draft) : null;
  const inheritedLevels = data.inherited_model_info.reasoning_effort_levels ?? [];
  const available = [...new Set([...(draft.reasoning_effort_levels ?? inheritedLevels), ...(draft.default_reasoning_effort ? [draft.default_reasoning_effort] : [])])];
  const edit = (values: Partial<CapabilityOverride>) => { setDraft({ ...draft, ...values }); setDirty(true); };
  const supports = effective.supports_reasoning === null ? t("Unknown") : effective.supports_reasoning ? t("Supported") : t("Not supported");
  return <section className="grid gap-4 pb-5" aria-label={t("Model capabilities")}>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h3 className="font-semibold">{t("Model capabilities")}</h3><p className="mt-1 text-sm text-muted">{t("Capacity and reasoning are shared with clients through LiteLLM model info.")}</p></div>
      <button className="btn" disabled={busy || dirty || (provider && !data.can_edit)} onClick={onRefresh}>{busy ? t("Loading…") : provider ? t("Refresh from upstream") : t("Reload metadata")}</button>
    </div>
    <div className="grid grid-cols-3 gap-3 text-sm">{limits.map(([name, label]) => <div key={name}><span className="block text-muted">{t(label)}</span><strong>{effective[name]?.toLocaleString() ?? t("Unknown")}</strong></div>)}</div>
    <div className="grid gap-2 text-sm">
      <div className="flex flex-wrap justify-between gap-2"><span>{t("Reasoning")} · {supports}</span><span className="text-muted">{t("Default effort")}: {effective.reasoning?.default_effort ?? effective.default_reasoning_effort ?? t("Service default")}</span></div>
      <p className="break-words" data-testid="reasoning-levels">{effective.reasoning_effort_levels === null ? t("Upstream has not reported selectable levels.") : effective.reasoning_effort_levels.length ? effective.reasoning_effort_levels.join(" · ") : t("No selectable reasoning levels.")}</p>
      <p className="text-xs text-muted">{(provenance?.sources ?? ["unknown"]).map(source => t(sources[source] ?? source)).join(" · ")}{provenance?.stale ? ` · ${t("Cached metadata may be stale")}` : ""}{provenance?.updated_at ? ` · ${new Date(provenance.updated_at).toLocaleString()}` : ""}</p>
    </div>
    {data.can_edit && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={manual} disabled={!editable} onChange={event => { setManual(event.target.checked); setDirty(true); }} />{t("Override model capabilities")}</label>}
    {manual && data.can_edit && <div className="grid gap-4">
      <p className="text-xs text-muted">{provider ? t("Sources inherit these values. Empty fields use upstream metadata or the exact catalog reference.") : t("These values apply only to this Source. Empty fields inherit the Provider configuration.")}</p>
      <div className="grid gap-3 sm:grid-cols-3">{limits.map(([name, label]) => <label className="grid gap-1 text-sm" key={name}>{t(label)}<input className="input w-full" type="number" min="1" max="100000000" step="1" disabled={!editable}
        value={draft[name] ?? ""} placeholder={data.inherited_model_info[name]?.toLocaleString() ?? t("Automatic")}
        onChange={event => edit({ [name]: event.target.value === "" ? undefined : Number(event.target.value) })} /></label>)}</div>
      <div className="grid gap-3 sm:grid-cols-2"><label className="grid gap-1 text-sm">{t("Reasoning support")}<select aria-label={t("Reasoning support")} className="select w-full" disabled={!editable} value={draft.supports_reasoning === undefined ? "auto" : String(draft.supports_reasoning)} onChange={event => {
        const value = event.target.value;
        if (value === "false") { edit({ supports_reasoning: false, reasoning_effort_levels: [], default_reasoning_effort: undefined }); setLevelText(""); }
        else edit({ supports_reasoning: value === "auto" ? undefined : true, reasoning_effort_levels: effortInput(levelText) });
      }}><option value="auto">{t("Automatic")}</option><option value="true">{t("Supported")}</option><option value="false">{t("Not supported")}</option></select></label>
      <label className="grid gap-1 text-sm">{t("Default effort")}<select aria-label={t("Default effort")} className="select w-full" value={draft.default_reasoning_effort ?? ""} disabled={!editable || draft.supports_reasoning === false} onChange={event => edit({ default_reasoning_effort: event.target.value || undefined })}>
        <option value="">{t("Service default")}</option>{available.map(value => <option key={value} value={value}>{value}</option>)}</select></label></div>
      <label className="grid gap-1 text-sm">{t("Supported reasoning levels")}<input className="input w-full" disabled={!editable || draft.supports_reasoning === false} value={levelText}
        placeholder={inheritedLevels.length ? inheritedLevels.join(", ") : t("Automatic")}
        onChange={event => { setLevelText(event.target.value); edit({ reasoning_effort_levels: effortInput(event.target.value) }); }} /></label>
      <p className="text-xs text-muted">{t("Separate exact API values with commas. none is an explicit value; an omitted effort keeps the service default.")}</p>
    </div>}
    {problem && <p role="alert" className="text-sm text-red-700">{t(problem)}</p>}
    {error && <p role="alert" className="text-sm text-red-700">{error} <button className="underline" disabled={busy} onClick={() => { setDirty(false); onReload(); }}>{t("Reload metadata")}</button></p>}
    {saved && !dirty && <p role="status" className="text-sm">{t("Capabilities saved and available to clients.")}</p>}
    {data.can_edit && <div className="flex flex-wrap gap-2"><button className="btn btn-primary" disabled={!editable || !dirty || !!problem} onClick={() => onSave(manual ? draft : null, baseOverride)}>{t("Save capabilities")}</button>
      {data.override && <button className="btn" disabled={!editable} onClick={() => onSave(null, baseOverride)}>{provider ? t("Restore automatic discovery") : t("Restore inheritance")}</button>}</div>}
  </section>;
}

export function ModelCapabilities({ sourceId, providerId, offerId }: { sourceId?: string; providerId?: string; offerId?: string }) {
  useLocale();
  const { apiContext } = useAuth();
  const cache = useQueryClient();
  const [revision, setRevision] = useState(0);
  const path = providerId && offerId ? `/api/v1/provider-connections/${encodeURIComponent(providerId)}/models/${encodeURIComponent(offerId)}/model-capabilities/`
    : `/api/v1/deployments/${encodeURIComponent(sourceId!)}/model-capabilities/`;
  const key = ["model-capabilities", apiContext, path];
  const current = useQuery({ queryKey: key, queryFn: () => api.modelCapabilities(apiContext, path) });
  const changed = (result: Capabilities) => { cache.setQueryData(key, result); void cache.invalidateQueries({ queryKey: ["model-capabilities"] }); };
  const save = useMutation({ mutationFn: ({ override, expected }: { override: CapabilityOverride | null; expected: CapabilityOverride | null }) => api.saveModelCapabilities(apiContext, path, override, expected),
    onSuccess: result => { changed(result); setRevision(value => value + 1); } });
  const refresh = useMutation({ mutationFn: () => providerId ? api.refreshModelCapabilities(apiContext, path) : api.modelCapabilities(apiContext, path), onSuccess: changed });
  if (current.isPending) return <p role="status" className="py-5 text-sm text-muted">{t("Loading model capabilities…")}</p>;
  if (current.isError) return <p role="alert" className="py-5 text-sm">{t("Model capabilities could not load.")} <button className="underline" onClick={() => void current.refetch()}>{t("Retry")}</button></p>;
  return <ModelCapabilitiesEditor key={`${path}:${revision}`} data={current.data} provider={!!providerId} busy={save.isPending || refresh.isPending} saved={save.isSuccess}
    error={save.error?.message || refresh.error?.message} onSave={(override, expected) => { refresh.reset(); save.mutate({ override, expected }); }}
    onRefresh={() => { save.reset(); refresh.mutate(); }} onReload={() => { save.reset(); refresh.reset(); void current.refetch(); }} />;
}
