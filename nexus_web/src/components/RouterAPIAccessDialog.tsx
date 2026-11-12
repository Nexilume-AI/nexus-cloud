import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Copy, KeyRound, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import type { Router, RouterAPIAccess } from "../lib/types";
import { t, useLocale } from "../localization";
import { NexilumeDialog, NexilumeTabs } from "./NexilumeControls";

type Example = "python" | "curl" | "env";
type Protocol = "openai" | "systemone";

export function RouterAPIAccessDialog({ router, canCreate, onClose }: {
  router: Router; canCreate: boolean; onClose: () => void;
}) {
  useLocale();
  const { apiContext } = useAuth();
  const [access, setAccess] = useState<RouterAPIAccess | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0);
  const [confirmRotation, setConfirmRotation] = useState(false);
  const credentials = access?.credentials;
  const [protocolChoice, setProtocolChoice] = useState<Protocol | null>(null);
  const native = credentials?.systemone;
  const nativeModels = native?.available_models ?? [];
  const hasChat = Object.values(credentials?.model_operations ?? {}).some((ops) => ops.includes("chat.completions"));
  const protocol = protocolChoice ?? (nativeModels.length && !hasChat ? "systemone" : "openai");
  const isNative = protocol === "systemone";
  const [example, setExample] = useState<Example>("python");
  const [copyError, setCopyError] = useState("");
  const create = useMutation({
    // Plaintext exists only in this mounted component, never in query/browser storage.
    mutationFn: async (rotate: boolean) => {
      setAccess(await api.ensureRouterAPIAccess(apiContext, router.id, rotate
        ? { action: "rotate", expected_id: access?.credential_id ?? "", confirm_rotation: true }
        : { action: "ensure" }));
      setConfirmRotation(false);
    },
    retry: false,
  });
  useEffect(() => {
    let active = true;
    setAccess(null);
    setLoadError("");
    setLoading(true);
    if (!canCreate) { setLoading(false); return; }
    void api.getRouterAPIAccess(apiContext, router.id)
      .then((result) => { if (active) setAccess(result); })
      .catch((error: Error) => { if (active) setLoadError(error.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [apiContext, router.id, canCreate, reload]);
  const blocked = !canCreate
    ? t("Router use permission is required to create your API key.")
    : router.status !== "deployed" ? t("Deploy this Router before creating an API key.") : "";
  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopyError("");
      toast.success(t("Copied"));
    } catch { setCopyError(t("Copy failed. Select the text and copy it manually.")); }
  }
  function row(label: string, value: string, secret = false) {
    return <label className="grid min-w-0 gap-2 text-sm font-medium">
      {label}
      <span className="flex min-w-0 gap-2">
        <input className="input min-w-0 flex-1 font-mono text-xs" type={secret ? "password" : "text"}
          readOnly value={value} aria-label={label} onFocus={(event) => event.target.select()} />
        <button type="button" className="btn min-h-11 shrink-0" aria-label={t("Copy {{0}}", { 0: label })}
          onClick={() => void copy(value)}><Copy size={16} /></button>
      </span>
    </label>;
  }
  const python = credentials?.python ?? [
    "from openai import OpenAI", "", "client = OpenAI()", "",
    "response = client.chat.completions.create(",
    `    model=${JSON.stringify(credentials?.recommended_model ?? "")},`,
    '    messages=[{"role": "user", "content": "hello"}],', ")",
    "print(response.choices[0].message.content)",
  ].join("\n");
  const snippet = credentials ? (example === "env" ? credentials.env
    : isNative ? native?.[example] ?? "" : example === "python" ? python : credentials.curl) : "";
  return <NexilumeDialog open title={t("API & keys")} description={router.name}
    onClose={() => { if (!create.isPending) onClose(); }}
    footer={<div className="flex flex-wrap justify-end gap-2">
      {!access?.credential_id && <button className="btn btn-primary min-h-11" disabled={Boolean(blocked) || loading || Boolean(loadError) || create.isPending}
        onClick={() => create.mutate(false)}>{create.isPending ? <Loader2 size={16} className="animate-spin" /> : <KeyRound size={16} />}
        {t(create.isPending ? "Creating API key…" : "Create API key")}</button>}
      {access?.credential_id && !confirmRotation && <button className="btn min-h-11" disabled={Boolean(blocked) || create.isPending}
        onClick={() => setConfirmRotation(true)}>{t("Rotate API key")}</button>}
      <button className="btn min-h-11" onClick={onClose} disabled={create.isPending}>{t("Close")}</button>
    </div>}>
    <div className="grid min-w-0 gap-4">
      <label className="grid gap-2 text-sm font-medium">{t("API protocol")}
        <select className="input" aria-label={t("API protocol")} value={protocol} onChange={(event) => setProtocolChoice(event.target.value as Protocol)}>
          <option value="openai">OpenAI</option>
          <option value="systemone">{t("System One · native")}</option>
        </select>
      </label>
      <p className="text-sm text-muted">{t(isNative
        ? "Send state and questions to /systemone and receive native answers. Use the same Router API key; no additional Organization or Router headers are needed."
        : "Use this Router with any OpenAI-compatible client: Base URL, API key and model. No Organization or Router ID is required in your requests.")}</p>
      <p className="text-sm text-muted">{t("This key follows the Router's current models. Added models become available automatically; removed models stop working. Router permissions and key limits still apply.")}</p>
      <p className="text-sm text-muted">{t("This is your personal Router key. Use separate keys for each person to attribute usage; no additional request headers are needed.")}</p>
      {blocked && <p role="status" className="rounded-lg border border-line p-3 text-sm">{blocked}</p>}
      {(loadError || create.isError) && <div role="alert" className="text-sm text-danger">
        {loadError || create.error?.message}<button className="btn min-h-11" disabled={create.isPending} onClick={() => {
          create.reset(); setConfirmRotation(false); setReload((value) => value + 1);
        }}>{t("Reload API key")}</button>
      </div>}
      {loading && <p role="status">{t("Loading API key…")}</p>}
      {copyError && <p role="alert" className="text-sm text-danger">{copyError}</p>}
      {confirmRotation && <section aria-label={t("Confirm API key rotation")} className="grid gap-3 rounded-lg border border-danger p-3">
        <p className="text-sm">{t("Rotating replaces this key immediately. Applications using it must be updated. Other API keys are not changed.")}</p>
        <div className="flex flex-wrap gap-2">
          <button className="btn min-h-11" disabled={create.isPending} onClick={() => setConfirmRotation(false)}>{t("Cancel")}</button>
          <button className="btn btn-primary min-h-11" disabled={create.isPending || Boolean(blocked)} onClick={() => create.mutate(true)}>
            {create.isPending && <Loader2 size={16} className="animate-spin" />}{t("Confirm rotation")}</button>
        </div>
      </section>}
      {access?.credential_id && !credentials && <p role="status" className="rounded-lg border border-line p-3 text-sm">
        {t("This saved key is unavailable, expired or revoked. Reload to check its status, or explicitly rotate it to reconnect.")}
        {access.prefix && <span className="block font-mono">{access.prefix}…</span>}
      </p>}
      {credentials ? <>
        <p role="status" className="rounded-lg border border-line p-3 text-sm">{t("This key is stored encrypted. You can return to view and copy the same key. Opening this dialog does not change it.")}</p>
        {row(t("Base URL"), credentials.gateway_api_base_url)}
        {isNative && row(t("Request URL"), native?.endpoint ?? credentials.gateway_api_base_url.replace(/\/$/, "") + "/systemone")}
        {row(t("API key"), credentials.gateway_api_key, true)}
        {row(t("Model"), isNative ? native?.recommended_model ?? "" : credentials.recommended_model)}
        <p className="break-words text-sm text-muted">{t("Available models")}: {(isNative ? nativeModels : credentials.available_models ?? []).join(", ") || t("None")}</p>
        {isNative && !nativeModels.length && <p role="status" className="text-sm text-muted">
          {t("This Router does not export a System One model yet. Add the Provider model as a Source in a Model Pool, then connect that Pool to this Router. A decision model used only for intelligent routing is not a published API model.")}
        </p>}
        {isNative && <p className="text-sm text-muted">{t("Supports Choice, Score and Noul questions. Native requests do not stream or retry automatically. Reusing an Idempotency-Key returns 409 without repeating the decision.")}</p>}
        <NexilumeTabs<Example> label={t("Client example")} idBase="router-api-example" value={example} onChange={setExample}
          options={[{ value: "python", label: "Python" }, { value: "curl", label: "cURL" }, { value: "env", label: t("Environment") }]} />
        {example === "python" && <p className="text-sm text-muted">{t(isNative
          ? "Uses Python's standard library. Set OPENAI_BASE_URL and OPENAI_API_KEY from Environment."
          : "Install openai, then set OPENAI_BASE_URL and OPENAI_API_KEY from Environment in your application environment.")}</p>}
        <section role="tabpanel" id={`router-api-example-panel-${example}`} aria-labelledby={`router-api-example-tab-${example}`} className="min-w-0 overflow-hidden rounded-lg border border-line">
          <div className="flex justify-end border-b border-line p-2"><button className="btn min-h-11" onClick={() => void copy(snippet)}><Copy size={15} />{t("Copy example")}</button></div>
          <pre className="max-h-72 overflow-auto whitespace-pre p-3 font-mono text-xs" tabIndex={0}>{snippet}</pre>
        </section>
      </> : !loading && !loadError && !access?.credential_id && <p className="rounded-lg border border-line p-3 text-sm">{t("Viewing this guide does not create a key. Create one when you are ready to connect your application.")}
        {" "}{t("Previously exported keys remain valid, but their plaintext cannot be recovered. They are not replaced automatically.")}</p>}
    </div>
  </NexilumeDialog>;
}
