import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../app/AuthContext";
import { api, type ApiContext } from "../lib/api";
import type { ModelReferenceIdentity, ReferenceCapacity } from "../lib/modelReference";
import { t, useLocale } from "../localization";
import { ModelCapabilities } from "./ModelCapabilities";

function Capacity({ value }: { value?: ReferenceCapacity }) {
  return <div className="grid grid-cols-3 gap-3 text-sm">{([
    ["Context", value?.context_window], ["Input limit", value?.max_input_tokens], ["Output limit", value?.max_output_tokens],
  ] as const).map(([label, count]) => <div key={label}><span className="block text-muted">{t(label)}</span><strong>{count?.toLocaleString() ?? t("Unknown")}</strong></div>)}</div>;
}

function CatalogSelect({ ctx, provider = "", label, value, disabled, onChange }: {
  ctx: ApiContext; provider?: string; label: string; value: string; disabled: boolean; onChange: (id: string, capacity?: ReferenceCapacity) => void;
}) {
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  useEffect(() => { const timer = setTimeout(() => { setQuery(search); setOffset(0); }, 200); return () => clearTimeout(timer); }, [search]);
  const result = useQuery({ queryKey: ["model-reference-catalog", ctx, provider, query, offset],
    queryFn: () => api.modelReferenceCatalog(ctx, provider, query, offset), enabled: !disabled });
  return <div className="grid min-w-0 gap-2">
    <label className="grid gap-2 text-sm">{t(label)}<input type="search" className="input w-full" aria-label={t(`Search ${label}`)}
      placeholder={t("Search by ID")} value={search} disabled={disabled} onChange={e => setSearch(e.target.value)} /></label>
    <select className="select w-full" aria-label={t(`Select ${label}`)} value={value} disabled={disabled || result.isPending}
      onChange={e => onChange(e.target.value, result.data?.results.find(row => row.id === e.target.value)?.capacity)}>
      <option value="">{t("Select an exact ID")}</option>
      {value && !result.data?.results.some(row => row.id === value) && <option value={value}>{value}</option>}
      {result.data?.results.map(row => <option key={row.id} value={row.id}>{row.id}</option>)}
    </select>
    {result.isError && <p role="alert" className="text-sm">{t("Catalog could not load.")} <button className="underline" onClick={() => void result.refetch()}>{t("Retry")}</button></p>}
    {result.data && <div className="flex items-center justify-between gap-2 text-xs text-muted">
      <span>{result.data.available ? `${result.data.total} ${t("matches")}` : t("Catalog unavailable. You can enter IDs manually.")}</span>
      <div className="flex gap-3"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>{t("Previous")}</button>
        <button disabled={result.data.next_offset === null} onClick={() => setOffset(result.data!.next_offset!)}>{t("Next")}</button></div>
    </div>}
  </div>;
}

export function SourceModelReference(props: { sourceId?: string; providerId?: string; offerId?: string }) {
  return <><ModelCapabilities {...props} /><ReferenceMapping {...props} /></>;
}

function ReferenceMapping({ sourceId, providerId, offerId }: { sourceId?: string; providerId?: string; offerId?: string }) {
  useLocale();
  const { apiContext } = useAuth();
  const cache = useQueryClient();
  const key = ["source-model-reference", apiContext, sourceId, providerId, offerId];
  const current = useQuery({ queryKey: key, queryFn: () => providerId && offerId
    ? api.providerModelReference(apiContext, providerId, offerId) : api.sourceModelReference(apiContext, sourceId!) });
  const [draft, setDraft] = useState<ModelReferenceIdentity | null>(null);
  const [dirty, setDirty] = useState(false);
  const [manual, setManual] = useState(false);
  const [preview, setPreview] = useState<ReferenceCapacity>();
  const [saved, setSaved] = useState(false);
  useEffect(() => { if (current.data && !dirty) { setDraft(current.data.effective); setPreview(current.data.capacity); } }, [current.data, dirty]);
  const save = useMutation({ mutationFn: (mapping: ModelReferenceIdentity | null) => providerId && offerId
    ? api.saveProviderModelReference(apiContext, providerId, offerId, mapping, current.data!.mapping)
    : api.saveSourceModelReference(apiContext, sourceId!, mapping, current.data!.mapping),
    onSuccess: result => { cache.setQueryData(key, result); setDirty(false); setSaved(true);
      void cache.invalidateQueries({ queryKey: ["source-model-reference"] });
      void cache.invalidateQueries({ queryKey: ["model-capabilities"] }); } });
  const edit = (value: ModelReferenceIdentity, capacity?: ReferenceCapacity) => { setDraft(value); setPreview(capacity); setDirty(true); setSaved(false); save.reset(); };
  if (current.isPending) return <section className="border-t border-line py-5" role="status">{t("Loading model reference…")}</section>;
  if (current.isError) return <section className="border-t border-line py-5"><p role="alert">{t("Model reference could not load.")}</p><button className="btn" onClick={() => void current.refetch()}>{t("Retry")}</button></section>;
  const editable = current.data.can_edit && !save.isPending;
  const values = draft ?? current.data.effective;
  return <section className="grid gap-4 border-t border-line py-5" aria-label={t("Model capacity reference")}>
    <div><h3 className="font-semibold">{t("Model capacity reference")}</h3><p className="mt-1 text-sm text-muted">{providerId ? t("Set the models.dev mapping for this Provider model. Associated Sources inherit it unless they have an override.") : t("Override the inherited models.dev mapping for this Source only.")}</p><p className="mt-1 text-sm text-muted">{t("Reference limits fill missing metadata; upstream and administrator values take priority. Routing is unchanged.")}</p></div>
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm"><span>{current.data.mapping ? (providerId ? t("Provider model mapping") : t("Source override")) : t("Default mapping")}</span>
      {current.data.can_edit && <label className="flex items-center gap-2"><input type="checkbox" checked={manual} disabled={!editable} onChange={e => setManual(e.target.checked)} />{t("Enter IDs manually")}</label>}</div>
    {!current.data.can_edit ? <p className="break-all text-sm">{values.provider} / {values.model}</p> : manual ? <div className="grid gap-4 sm:grid-cols-2">
      <label className="grid gap-2 text-sm">{t("Provider ID")}<input className="input w-full" value={values.provider} maxLength={200} disabled={!editable} onChange={e => edit({ provider: e.target.value, model: values.model })} /></label>
      <label className="grid gap-2 text-sm">{t("Model ID")}<input className="input w-full" value={values.model} maxLength={255} disabled={!editable} onChange={e => edit({ provider: values.provider, model: e.target.value })} /></label>
    </div> : <div className="grid gap-4 sm:grid-cols-2">
      <CatalogSelect ctx={apiContext} label="Provider" value={values.provider} disabled={!editable} onChange={provider => edit({ provider, model: "" })} />
      <CatalogSelect key={values.provider} ctx={apiContext} provider={values.provider} label="Model" value={values.model} disabled={!editable || !values.provider} onChange={(model, capacity) => edit({ provider: values.provider, model }, capacity)} />
    </div>}
    <Capacity value={preview} />
    <p className="text-xs text-muted">{t("models.dev is a reference, not verification of this deployment's hard limits.")}{current.data.updated_at && ` ${t("Catalog updated:")} ${new Date(current.data.updated_at).toLocaleString()}`}</p>
    {!dirty && !current.data.matched && <p role="status" className="text-sm">{t("No exact catalog match. Unknown limits remain unknown.")}</p>}
    {manual && dirty && <p className="text-sm text-muted">{t("Manual IDs are case-sensitive. An unmatched mapping is saved without inventing capacity values.")}</p>}
    {save.isError && <p role="alert" className="text-sm text-red-700">{save.error.message} <button className="underline" onClick={() => { setDirty(false); void current.refetch(); }}>{t("Reload mapping")}</button></p>}
    {saved && <p role="status" className="text-sm">{t("Mapping saved.")}</p>}
    {current.data.can_edit && <div className="flex flex-wrap gap-2"><button className="btn btn-primary min-h-10" disabled={!editable || !dirty || !values.provider.trim() || !values.model.trim()}
      onClick={() => save.mutate({ provider: values.provider.trim(), model: values.model.trim() })}>{save.isPending ? t("Saving…") : t("Save mapping")}</button>
      <button className="btn min-h-10" disabled={!editable || !current.data.mapping} onClick={() => save.mutate(null)}>{providerId ? t("Restore default") : t("Restore inheritance")}</button></div>}
  </section>;
}
