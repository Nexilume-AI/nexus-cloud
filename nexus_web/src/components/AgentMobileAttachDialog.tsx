import { t, useLocale } from "../localization";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, Smartphone } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { api } from "../lib/api";
import type { MobileDevice } from "../lib/types";
import { NexilumeDialog } from "./NexilumeControls";

type ApiContext = Parameters<typeof api.mobileDevices>[0];

export function AgentMobileAttachDialog({
  open,
  agentId,
  agentName,
  declaredScopes,
  apiContext,
  returnTo,
  onClose,
  onAttached,
}: {
  open: boolean;
  agentId: string;
  agentName: string;
  declaredScopes: string[];
  apiContext: ApiContext;
  returnTo: string;
  onClose: () => void;
  onAttached: () => void | Promise<void>;
}) {
  useLocale();
  const queryClient = useQueryClient();
  const devices = useQuery({
    queryKey: ["mobile-devices", apiContext.token, apiContext.tenantId, apiContext.projectId],
    queryFn: () => api.mobileDevices(apiContext),
    enabled: open,
  });
  const bindings = useQuery({
    queryKey: ["agent-mobile-bindings", agentId, apiContext.token, apiContext.tenantId, apiContext.projectId],
    queryFn: () => api.agentMobileBindings(apiContext, agentId),
    enabled: open && Boolean(agentId),
  });
  const grant = useQuery({
    queryKey: ["agent-mobile-grant", agentId, apiContext.token, apiContext.tenantId, apiContext.projectId],
    queryFn: () => api.agentMobileGrant(apiContext, agentId),
    enabled: open && Boolean(agentId),
  });
  const effectiveDeclaredScopes = grant.isSuccess
    ? grant.data.declared_scopes
    : declaredScopes;
  const policyReady = grant.isSuccess;
  const authorized = policyReady && (effectiveDeclaredScopes.length === 0 || (
    grant.data?.status === "active" && effectiveDeclaredScopes.every((scope) => grant.data?.scopes.includes(scope))
  ));
  const defaultDeviceId = useMemo(
    () => bindings.data?.find((binding) => binding.is_default)?.device_id || bindings.data?.[0]?.device_id || "",
    [bindings.data],
  );
  const [selectedDeviceId, setSelectedDeviceId] = useState("");
  const [complete, setComplete] = useState(false);

  useEffect(() => {
    if (!open) {
      setComplete(false);
      setSelectedDeviceId("");
      return;
    }
    if (!selectedDeviceId) setSelectedDeviceId(defaultDeviceId || devices.data?.[0]?.id || "");
  }, [defaultDeviceId, devices.data, open, selectedDeviceId]);

  const attach = useMutation({
    mutationFn: async () => {
      if (!selectedDeviceId) throw new Error(t("Select a paired Mobile device."));
      if (!policyReady) throw new Error(t("Wait for the latest Mobile policy before attaching."));
      if (!authorized) await api.setAgentMobileGrant(apiContext, agentId, effectiveDeclaredScopes);
      return api.createAgentMobileBinding(apiContext, agentId, { device_id: selectedDeviceId, is_default: true });
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["agent-mobile-bindings", agentId] }),
        queryClient.invalidateQueries({ queryKey: ["agent-mobile-grant", agentId] }),
      ]);
      setComplete(true);
      toast.success(t("Mobile attached for future Runs"));
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("Unable to attach Mobile")),
  });
  const busy = attach.isPending;
  const selected = devices.data?.find((device) => device.id === selectedDeviceId);

  return (
    <NexilumeDialog
      open={open}
      onClose={onClose}
      title={t("Attach caller Mobile")}
      eyebrow={t("PRIVATE RUN SETUP")}
      description={t("{{0}} can operate only the phone you explicitly authorize for this Agent.", { 0: agentName })}
      busy={busy}
      size="large"
      footer={complete ? (
        <button type="button" className="btn btn-primary min-h-11" onClick={() => void onAttached()}>{t("Done")}</button>
      ) : (
        <div className="flex w-full justify-between gap-3">
          <button type="button" className="btn min-h-11" onClick={() => void Promise.all([devices.refetch(), grant.refetch()])} disabled={busy}>
            <RefreshCw size={15} />{t("Refresh status")}</button>
          <div className="flex gap-2">
            <button type="button" className="btn min-h-11" onClick={onClose} disabled={busy}>{t("Cancel")}</button>
            <button type="button" className="btn btn-primary min-h-11" disabled={busy || !selectedDeviceId || !policyReady} onClick={() => attach.mutate()}>
              {busy ? <Loader2 size={15} className="animate-spin" /> : <Smartphone size={15} />}
              {authorized ? t("Attach Mobile") : t("Allow and attach")}
            </button>
          </div>
        </div>
      )}
    >
      {complete ? (
        <section className="border border-emerald-200 bg-emerald-50 p-5 text-emerald-950" aria-live="polite">
          <div className="flex gap-3"><CheckCircle2 size={20} /><div><h3 className="font-semibold">{t("Mobile ready")}</h3><p className="mt-1 text-sm">{selected?.name || t("The selected phone")}{" "}{t("is the default for the next Run. An active Run keeps its original device.")}</p></div></div>
        </section>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <section aria-labelledby="mobile-permission-title" className="border border-line bg-paper p-4">
            <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">{t("01 · Permissions")}</div>
            <h3 id="mobile-permission-title" className="mt-2 font-semibold text-ink">{t("Allow requested capabilities")}</h3>
            <p className="mt-1 text-sm leading-6 text-muted">{t("The grant is caller-private and can be revoked immediately.")}</p>
            <ul className="mt-4 grid gap-2 text-sm text-ink">
              {effectiveDeclaredScopes.map((scope) => <li key={scope} className="flex items-center gap-2 border-t border-line pt-2"><span className="h-2 w-2 rounded-full bg-lume ring-1 ring-ink" />{scope.replace("mobile.", "").replaceAll("_", " ")}</li>)}
              {!effectiveDeclaredScopes.length && policyReady ? <li>{t("No Mobile actions requested.")}</li> : null}
            </ul>
            {grant.isError ? <div role="alert" className="mt-4 border border-danger/30 bg-red-50 p-3 text-sm text-danger">{t("The latest Agent Mobile policy could not be loaded. Retry before attaching.")}</div> : null}
            <div className="mt-4 font-mono text-[10px] uppercase tracking-[0.08em] text-muted">{grant.isLoading ? t("Checking latest policy") : grant.isError ? t("Policy unavailable") : authorized ? t("Permission granted") : t("Approval required")}</div>
          </section>
          <section aria-labelledby="mobile-device-title">
            <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">{t("02 · Device")}</div>
            <h3 id="mobile-device-title" className="mt-2 font-semibold text-ink">{t("Choose your phone")}</h3>
            {devices.isLoading || bindings.isLoading ? <div className="mt-4 flex min-h-28 items-center justify-center border border-line text-sm text-muted"><Loader2 className="mr-2 animate-spin" size={16} />{" "}{t("Loading caller devices")}</div> : null}
            {devices.isError ? <div role="alert" className="mt-4 border border-danger/30 bg-red-50 p-3 text-sm text-danger">{t("Mobile devices could not be loaded.")}</div> : null}
            {!devices.isLoading && !devices.isError && !devices.data?.length ? (
              <div className="mt-4 border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
                <div className="flex gap-2 font-semibold"><AlertTriangle size={17} />{" "}{t("No paired Mobile")}</div>
                <p className="mt-2 leading-6">{t("Pair a caller-owned phone in Operate &gt; Mobile, then return here.")}</p>
                <Link className="btn mt-3 min-h-11" to={`/mobile?return=${encodeURIComponent(returnTo)}`} onClick={onClose}>{t("Pair Mobile")}</Link>
              </div>
            ) : (
              <div className="mt-4 grid gap-2" role="radiogroup" aria-label={t("Caller Mobile devices")}>
                {(devices.data || []).map((device) => <DeviceChoice key={device.id} device={device} selected={device.id === selectedDeviceId} onSelect={() => setSelectedDeviceId(device.id)} />)}
              </div>
            )}
          </section>
        </div>
      )}
    </NexilumeDialog>
  );
}

function DeviceChoice({ device, selected, onSelect }: { device: MobileDevice; selected: boolean; onSelect: () => void }) {
  useLocale();
  const online = device.lifecycle_status === "online";
  return <button type="button" role="radio" aria-checked={selected} onClick={onSelect} className={`flex min-h-16 items-center justify-between border px-3 py-3 text-left ${selected ? "border-ink bg-lume/10" : "border-line bg-white"}`}>
    <span><span className="flex items-center gap-2 text-sm font-semibold text-ink"><span className={`h-2.5 w-2.5 rounded-full border ${selected ? "border-ink bg-lume" : "border-muted"}`} />{device.name}</span><span className={`mt-1 block pl-[18px] text-xs ${online ? "text-muted" : "text-amber-700"}`}>{online ? t("Online") : device.lifecycle_detail || device.lifecycle_status}</span></span>
    <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">{online ? t("ready") : t("unavailable")}</span>
  </button>;
}
