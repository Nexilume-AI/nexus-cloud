import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Loader2, SlidersHorizontal } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import { decisionRole, orderedDecisionModels } from "../lib/decisionHarness";
import { decisionConnectionError, prepareDecisionConnection, type RouterDecisionSettings } from "../lib/routerDecisionConnection";
import type { Router } from "../lib/types";
import { useCursorPage } from "../lib/useCursorPage";
import { t, useLocale } from "../localization";
import { CursorPageControls } from "./CursorPageControls";
import { Field } from "./Form";
import { NexilumeDialog } from "./NexilumeControls";
import "./router-routing.css";

export type { RouterDecisionSettings } from "../lib/routerDecisionConnection";

export function savedDecisionSettings(router: Router): RouterDecisionSettings {
  const config: RouterDecisionSettings = { provider_connection_id: "", model: "", timeout_ms: 8000, model_source: "catalog", ...router.decision_config };
  if (config.model_order) {
    const active = new Set((router.child_bindings ?? []).filter(row => row.status === "active").map(row => row.id));
    config.model_order = config.model_order.filter(id => active.has(id));
  }
  return config;
}

const modes = [
  { value: "manual_priority", title: "Specified models", description: "Clients choose the model." },
  { value: "task_type", title: "Rules", description: "Match the model to the task." },
  { value: "system_one", title: "Decision Model", description: "Let a model choose the route." },
];

export function RouterModelSelection({ router, canEdit }: { router: Router; canEdit: boolean }) {
  useLocale();
  const [editing, setEditing] = useState(false);
  const automatic = ["task_type", "system_one"].includes(router.strategy);
  const title = modes.find(mode => mode.value === router.strategy)?.title ?? router.strategy;
  return <>
    <section className="router-routing-summary" aria-label={t("Model selection")}>
      <div className="router-routing-summary__content">
        <span className="router-routing-eyebrow">{t("Model selection")}</span>
        <h3>{t(title)}{automatic && <code>auto</code>}</h3>
        <p>{automatic ? t("Call auto to choose a model, or use a model name directly.") : t("Each model name uses its assigned Execution Router.")}</p>
        {router.strategy === "system_one" && <small>{router.decision_config?.model || t("Choose a decision model")}<span aria-hidden="true"> · </span>{t("Rules fallback")}</small>}
      </div>
      <button className="btn" disabled={!canEdit} onClick={() => setEditing(true)}><SlidersHorizontal size={15} />{t("Edit routing")}</button>
    </section>
    {editing && <RouterModelSelectionDialog key={router.id} router={router} onClose={() => setEditing(false)} />}
  </>;
}

function RouterModelSelectionDialog({ router, onClose }: { router: Router; onClose: () => void }) {
  const { apiContext } = useAuth();
  const queryClient = useQueryClient();
  const [strategy, setStrategy] = useState(router.strategy);
  const [config, setConfig] = useState(() => savedDecisionSettings(router));
  const rows = orderedDecisionModels(router.child_bindings ?? [], config.model_order);
  const changed = strategy !== router.strategy || (strategy === "system_one" && JSON.stringify(config) !== JSON.stringify(savedDecisionSettings(router)));
  const save = useMutation({
    mutationFn: async () => {
      const decision = strategy === "system_one" ? await prepareDecisionConnection(apiContext, config, router, setConfig) : undefined;
      return api.updateRouter(apiContext, router.id, { strategy, ...(decision ? { decision_config: decision } : {}) });
    },
    gcTime: 0,
    onSettled: () => { void queryClient.invalidateQueries({ queryKey: ["system-one-routing-providers"] }); void queryClient.invalidateQueries({ queryKey: ["provider-connections"] }); },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["routers"] }),
        queryClient.invalidateQueries({ queryKey: ["router-detail"] }),
        queryClient.invalidateQueries({ queryKey: ["router-aggregation-candidates"] }),
        queryClient.invalidateQueries({ queryKey: ["topology"] }),
      ]);
      toast.success(t("Routing settings saved"));
      onClose();
    },
  });
  function move(index: number, direction: -1 | 1) {
    const order = rows.map(row => row.id);
    const target = index + direction;
    if (target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    setConfig(current => ({ ...current, model_order: order }));
  }
  const invalid = strategy === "system_one" && Boolean(decisionConnectionError(config));
  return <NexilumeDialog open title={t("Model selection")} eyebrow={router.name} description={t("Choose how this API selects a model.")} size="large" busy={save.isPending} onClose={onClose}
    footer={<><button className="btn" disabled={save.isPending} onClick={onClose}>{t("Cancel")}</button><button className="btn btn-primary" disabled={!changed || invalid || save.isPending} onClick={() => save.mutate()}>{save.isPending && <Loader2 size={16} className="animate-spin" />}{t("Save routing")}</button></>}>
    <fieldset className="router-routing-editor" disabled={save.isPending}>
      <legend className="sr-only">{t("Routing settings")}</legend>
      <div className="router-routing-modes" role="radiogroup" aria-label={t("Selection method")}>
        {modes.map(mode => <label key={mode.value} className={strategy === mode.value ? "is-selected" : ""}>
          <input type="radio" name="router-selection-method" value={mode.value} checked={strategy === mode.value} onChange={() => setStrategy(mode.value)} />
          <span><strong>{t(mode.title)}</strong><small>{t(mode.description)}</small></span>
        </label>)}
      </div>
      {strategy === "system_one" ? <div className="router-routing-columns">
        <section className="router-routing-section"><h3>{t("Decision service")}</h3><SystemOneRoutingSettings value={config} onChange={setConfig} /></section>
        <section className="router-routing-section">
          <h3>{t("Model preference order")}</h3>
          <p>{t("Order strongest to fastest. Simple tasks prefer Fast; harder tasks use a stronger model.")}</p>
          {rows.length ? <ol className="router-routing-order">{rows.map((row, index) => <li key={row.id}>
            <span className="router-routing-rank">{index + 1}</span><span className="router-routing-model"><strong>{row.exposed_model_name}</strong><small>{t(decisionRole(index, rows.length))}{!row.enabled && ` · ${t("Disabled")}`}</small></span>
            <div className="router-routing-move"><button className="btn" aria-label={t("Move {{0}} up", { 0: row.exposed_model_name })} disabled={index === 0} onClick={() => move(index, -1)}><ArrowUp size={15} /></button><button className="btn" aria-label={t("Move {{0}} down", { 0: row.exposed_model_name })} disabled={index === rows.length - 1} onClick={() => move(index, 1)}><ArrowDown size={15} /></button></div>
          </li>)}</ol> : <p>{t("Add models to this Router to define their order.")}</p>}
          <p className="router-routing-footnote">{t("Capacity and reasoning capabilities come from Sources. Tool continuations keep their model; new tasks are evaluated again.")}</p>
        </section>
      </div> : <div className="router-routing-explanation">
        <h3>{strategy === "task_type" ? t("Automatic routing, without a decision request") : t("Direct model access")}</h3>
        <p>{strategy === "task_type" ? t("Clients request auto. Rules choose an eligible model for the task; requests with a model name keep their direct mapping.") : t("Clients request a model from the API catalog. Automatic selection is off; the auto alias is not available.")}</p>
      </div>}
      {save.isError && <p className="router-routing-error" role="alert">{save.error instanceof Error ? save.error.message : t("Routing settings could not be saved.")}</p>}
    </fieldset>
  </NexilumeDialog>;
}

export function SystemOneRoutingSettings({ value, onChange, disabled = false }: { value: RouterDecisionSettings; onChange: (value: RouterDecisionSettings) => void; disabled?: boolean }) {
  const { apiContext, isContextReady } = useAuth();
  const paging = useCursorPage(apiContext);
  const [search, setSearch] = useState("");
  const existingDraft = useRef<RouterDecisionSettings | null>(null);
  const connections = useQuery({ queryKey: ["system-one-routing-providers", apiContext, paging.cursor], queryFn: ({ signal }) => api.providerConnectionsPage(apiContext, { protocol: "system_one", cursor: paging.cursor }, signal), enabled: isContextReady });
  const provider = useQuery({ queryKey: ["provider-connection", apiContext, value.provider_connection_id], queryFn: ({ signal }) => api.providerConnection(apiContext, value.provider_connection_id, signal), enabled: isContextReady && Boolean(value.provider_connection_id) });
  const models = (provider.data?.models ?? []).filter(row => ["detected", "confirmed"].includes(row.status));
  const visibleModels = models.filter(row => row.upstream_model_id === value.model || row.upstream_model_id.toLowerCase().includes(search.trim().toLowerCase()));
  const fresh = value.new_connection;
  return <fieldset className="router-decision-fields" disabled={disabled}>
    <legend className="sr-only">{t("System One connection")}</legend>
    <div className="router-connection-switch" role="group" aria-label={t("Connection source")}>
      <button type="button" aria-pressed={!fresh} onClick={() => { if (fresh) { const { new_connection: _draft, ...existing } = value; onChange({ ...(existingDraft.current ?? existing), timeout_ms: value.timeout_ms, model_order: value.model_order }); } }}>{t("Existing Provider")}</button>
      <button type="button" aria-pressed={Boolean(fresh)} onClick={() => { if (!fresh) { existingDraft.current = value; onChange({ ...value, model: "", new_connection: { name: "System One", url: "", key: "" } }); } }}>{t("New connection")}</button>
    </div>
    {fresh ? <>
      <Field label={t("Connection name")}><input className="input" value={fresh.name} maxLength={255} onChange={event => onChange({ ...value, new_connection: { ...fresh, name: event.target.value } })} /></Field>
      <Field label={t("Endpoint / Base URL")} hint={t("System One native API. A /systemone URL is also accepted.")}><input className="input" type="url" inputMode="url" placeholder="https://api.example.com/v1" value={fresh.url} maxLength={1024} autoComplete="off" spellCheck={false} onChange={event => onChange({ ...value, new_connection: { ...fresh, url: event.target.value } })} /></Field>
      <Field label={t("API key")} hint={t("Stored encrypted in Providers. Never returned to the browser.")}><input className="input" type="password" autoComplete="new-password" spellCheck={false} value={fresh.key} maxLength={4096} onChange={event => onChange({ ...value, new_connection: { ...fresh, key: event.target.value } })} /></Field>
      <Field label={t("Decision model ID")}><input className="input" value={value.model} placeholder="decision-model-preview" maxLength={128} spellCheck={false} onChange={event => onChange({ ...value, model: event.target.value })} /></Field>
      <p className="router-routing-footnote">{t("Saving creates a reusable Provider connection in this Router's organization or project. Model availability is checked when used.")}</p>
    </> : <>
    <Field label={t("Decision Provider")}><select className="select" value={value.provider_connection_id} onChange={event => { setSearch(""); onChange({ ...value, needs_start: false, provider_connection_id: event.target.value, model: "", model_source: "catalog" }); }}>
      <option value="">{connections.isPending ? t("Loading providers…") : t("Choose a System One Provider")}</option>
      {value.provider_connection_id && !(connections.data?.items ?? []).some(row => row.id === value.provider_connection_id) && <option value={value.provider_connection_id}>{provider.data?.name || value.provider_connection_id}</option>}
      {(connections.data?.items ?? []).filter(row => ["system_one", "system-one"].includes(row.upstream_provider)).map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
    </select></Field>
    {(paging.page > 1 || connections.data?.next_cursor) && <CursorPageControls page={paging.page} hasMore={Boolean(connections.data?.next_cursor)} busy={connections.isFetching} onPrevious={paging.previous} onNext={() => paging.next(connections.data?.next_cursor)} />}
    {value.model_source !== "explicit" && models.length > 10 && <input className="input" type="search" aria-label={t("Search decision models")} placeholder={t("Search decision models")} value={search} onChange={event => setSearch(event.target.value)} />}
    {value.model_source === "explicit" ? <Field label={t("Decision model ID")}><input className="input" value={value.model} maxLength={128} onChange={event => onChange({ ...value, model: event.target.value })} /></Field> : <Field label={t("Decision model")}><select className="select" value={value.model} disabled={!value.provider_connection_id || provider.isPending} onChange={event => onChange({ ...value, model: event.target.value })}>
      <option value="">{value.provider_connection_id && provider.isPending ? t("Loading models…") : t("Choose a decision model")}</option>
      {value.model && !models.some(row => row.upstream_model_id === value.model) && <option value={value.model}>{value.model}{provider.isSuccess && ` · ${t("Not in catalog")}`}</option>}
      {visibleModels.map(row => <option key={row.id} value={row.upstream_model_id}>{row.upstream_model_id}</option>)}
    </select></Field>}
    <label className="router-decision-explicit"><input type="checkbox" checked={value.model_source === "explicit"} onChange={event => onChange({ ...value, model_source: event.target.checked ? "explicit" : "catalog" })} />{t("Enter a model ID manually")}</label>
    {value.model_source === "explicit" && <p className="router-routing-footnote">{t("An explicit ID does not verify availability or enable a disabled model.")}</p>}
    {value.needs_start && <p className="router-routing-footnote" role="status">{t("Connection saved. Save routing again to retry starting it; the API key does not need to be entered again.")}</p>}
    {(connections.isError || provider.isError) && <p role="alert">{t("Decision Provider could not be loaded.")} <button className="btn" onClick={() => { void connections.refetch(); if (value.provider_connection_id) void provider.refetch(); }}>{t("Retry")}</button></p>}
    </>}
    <Field label={t("Decision timeout")}><select className="select" value={value.timeout_ms} onChange={event => onChange({ ...value, timeout_ms: Number(event.target.value) })}>
      {Array.from(new Set([1000, 3000, 5000, 8000, value.timeout_ms])).sort((a, b) => a - b).map(ms => <option key={ms} value={ms}>{ms / 1000} s</option>)}
    </select></Field>
    <p className="router-routing-footnote">{t("Timeouts and failures use the Router's fallback policy. Sends up to 4,000 characters of user text and candidate summaries to this Provider; usage may incur charges.")}</p>
  </fieldset>;
}
