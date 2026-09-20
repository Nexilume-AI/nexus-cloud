import { t, tToken, useLocale } from "../localization";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  Check,
  ChevronRight,
  Copy,
  Loader2,
  Pencil,
  Power,
  Plus,
  Radio,
  RefreshCw,
  Router,
  ShieldCheck,
  Trash2,
  Wifi,
  WifiOff,
} from "lucide-react";
import { toast } from "sonner";
import { Link } from "react-router-dom";

import { useAuth } from "../app/AuthContext";
import { StatusBadge } from "../components/Badge";
import { RouterRegistrationDiagnostic, useRouterStatusLabel } from '../components/EdgeRegistrationDiagnostic';
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Form";
import { ResourceAccessState, ResourceOwnershipBadge, ResourceOwnershipPicker } from "../components/ResourceOwnership";
import { NexilumeDialog, NexilumeTabs } from "../components/NexilumeControls";
import { api } from "../lib/api";
import { compactId, formatDate } from "../lib/format";
import type { EdgeNode, EdgeNodeDetail, EdgeRelayEndpoint, EdgeRelayServiceStatus } from "../lib/types";

type RouterScope = "own" | "admin";

export function OpenWrtRoutersPage() {
  useLocale();
  const routerStatusLabel = useRouterStatusLabel();
  const { apiContext, isContextReady, projects } = useAuth();
  const queryClient = useQueryClient();
  const [scope, setScope] = useState<RouterScope>("own");
  const [selectedId, setSelectedId] = useState("");
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [revokeOpen, setRevokeOpen] = useState(false);

  const capabilities = useQuery({
    queryKey: ["edge-router-capabilities", apiContext.token, apiContext.tenantId],
    queryFn: () => api.edgeRouterCapabilities(apiContext),
    enabled: isContextReady,
  });
  const relayService = useQuery({
    queryKey: ["edge-relay-service", apiContext.token, apiContext.tenantId],
    queryFn: () => api.edgeRelayService(apiContext),
    enabled: isContextReady,
    refetchInterval: 30000,
    refetchOnWindowFocus: true,
  });
  const relayControl = useMutation({
    mutationFn: (enabled: boolean) => api.setEdgeRelayService(apiContext, enabled),
    onSuccess: async (result) => {
      queryClient.setQueryData(["edge-relay-service", apiContext.token, apiContext.tenantId], result);
      await queryClient.invalidateQueries({ queryKey: ["edge-router-capabilities"] });
      toast.success(result.enabled ? t("Cloud Relay enabled") : t("Cloud Relay disabled"));
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const nodes = useQuery({
    queryKey: ["edge-nodes", apiContext.token, apiContext.tenantId, scope],
    queryFn: () => api.edgeNodes(apiContext, { scope }),
    enabled: isContextReady && (scope === "own" || Boolean(capabilities.data?.audit)),
    refetchInterval: registerOpen ? 5000 : 30000,
    refetchOnWindowFocus: true,
  });

  useEffect(() => {
    const items = nodes.data ?? [];
    if (!items.length) {
      setSelectedId("");
      return;
    }
    if (!items.some((node) => node.id === selectedId)) setSelectedId(items[0].id);
  }, [nodes.data, selectedId]);

  useEffect(() => {
    if (scope === "admin" && capabilities.data && !capabilities.data.audit) setScope("own");
  }, [capabilities.data, scope]);

  const detail = useQuery({
    queryKey: ["edge-node", apiContext.token, apiContext.tenantId, scope, selectedId],
    queryFn: () => api.edgeNode(apiContext, selectedId, scope),
    enabled: isContextReady && Boolean(selectedId),
    refetchInterval: 30000,
    refetchOnWindowFocus: true,
  });

  const selected = detail.data ?? nodes.data?.find((node) => node.id === selectedId) ?? null;
  const totals = useMemo(() => summarizeNodes(nodes.data ?? []), [nodes.data]);

  async function refreshRouters() {
    await queryClient.invalidateQueries({ queryKey: ["edge-nodes"] });
    await queryClient.invalidateQueries({ queryKey: ["edge-node"] });
  }

  return (
    <div className={`edge-router-page ${mobileDetailOpen ? "has-mobile-detail" : ""}`}>
      <header className="edge-router-header">
        <div>
          <p className="edge-router-eyebrow">{t("Organization · Private edge fabric")}</p>
          <h1>{t("OpenWrt Routers")}</h1>
          <p>{t("Register physical edge Routers you own, inspect their published Agent registrations, and control where they may be used.")}</p>
        </div>
        <div className="edge-router-header__actions">
          <button className="btn" type="button" onClick={() => void refreshRouters()} disabled={nodes.isFetching || detail.isFetching}>
            <RefreshCw size={15} className={nodes.isFetching ? "animate-spin" : ""} />{t("Refresh")}</button>
          {capabilities.data?.create_own && (
            <button className="btn btn-primary" type="button" onClick={() => { setScope("own"); setRegisterOpen(true); }}>
              <Plus size={16} />{t("Register Router")}</button>
          )}
        </div>
      </header>

      <div className="edge-router-overview">
        <section className="edge-router-status" aria-label={t("OpenWrt Router status summary")}>
          <RouterMetric label={t("Total")} value={totals.total} detail={scope === "admin" ? t("Audited routers") : t("Your routers")} />
          <RouterMetric label={t("Online")} value={totals.online} detail={t("Connected now")} tone="healthy" />
          <RouterMetric label={t("Attention")} value={totals.attention} detail={t("Offline or degraded")} tone={totals.attention ? "warning" : "neutral"} />
          <RouterMetric label={t("Known Agents")} value={totals.registrations} detail={t("Registration records")} />
        </section>

        <RouterInfrastructureBar
          status={relayService.data}
          loading={relayService.isLoading}
          error={relayService.isError ? errorMessage(relayService.error) : ""}
          busy={relayControl.isPending}
          onRefresh={() => void relayService.refetch()}
          onToggle={(enabled) => relayControl.mutate(enabled)}
        />
      </div>

      {capabilities.data?.audit && (
        <div className="edge-router-scope">
          <NexilumeTabs
            label={t("Router view")}
            variant="compact"
            value={scope}
            onChange={(value) => { setScope(value); setMobileDetailOpen(false); }}
            options={[
              { value: "own", label: t("My Routers") },
              { value: "admin", label: t("Admin audit") },
            ]}
          />
          <p>{scope === "admin" ? t("Audit is read-only except for emergency revocation. Other users' Routers cannot be bound or edited.") : t("Only Routers registered by your current identity are listed.")}</p>
        </div>
      )}

      {nodes.isError ? (
        <RouterError title={t("Routers unavailable")} detail={errorMessage(nodes.error)} retry={() => void nodes.refetch()} />
      ) : nodes.isLoading ? (
        <div className="edge-router-loading"><Loader2 className="animate-spin" size={20} />{t("Loading private Router inventory")}</div>
      ) : (nodes.data ?? []).length === 0 ? (
        <EmptyState
          title={scope === "admin" ? t("No Routers to audit") : t("No OpenWrt Routers registered")}
          description={scope === "admin" ? t("Registered and legacy Organization Routers will appear here.") : t("Generate a one-time code, then pair from LuCI or UCI on your Router.")}
          action={scope === "own" && capabilities.data?.create_own ? <button className="btn btn-primary" type="button" onClick={() => setRegisterOpen(true)}><Plus size={16} />{t("Register Router")}</button> : undefined}
        />
      ) : (
        <div className="edge-router-workspace">
          <section className="edge-router-track" aria-label={t("OpenWrt Routers")}>
            <header><span>{scope === "admin" ? t("Audit inventory") : t("Private inventory")}</span><strong>{nodes.data?.length ?? 0}{" "}{t("routers")}</strong></header>
            <div className="edge-router-track__list">
              {(nodes.data ?? []).map((node) => (
                <button
                  className={`edge-router-row ${node.id === selectedId ? "is-selected" : ""}`}
                  key={node.id}
                  type="button"
                  onClick={() => { setSelectedId(node.id); setMobileDetailOpen(true); }}
                >
                  <RouterGlyph status={node.connection_status} />
                  <span className="edge-router-row__copy">
                    <strong className="flex flex-wrap items-center gap-2">{node.display_name}<ResourceAccessState access={node.access} /></strong>
                    <small>{node.router_id}</small>
                    <ResourceOwnershipBadge ownership={node.ownership} />
                    {node.access?.can_read !== false && <em>{node.connectivity_mode === "relay" ? t("Relay") : t("Direct IPv6")}</em>}
                  </span>
                  <span className={`edge-router-row__status is-${node.connection_status}`}>{routerStatusLabel(node)}</span>
                  <span className="edge-router-row__count">{node.registration_count}{" "}{t("Agents")}</span>
                </button>
              ))}
            </div>
          </section>

          <section className="edge-router-detail" aria-live="polite">
            <button className="edge-router-mobile-back" type="button" onClick={() => setMobileDetailOpen(false)}><ArrowLeft size={16} />{t("All Routers")}</button>
            {detail.isError ? (
              <RouterError title={t("Router details unavailable")} detail={errorMessage(detail.error)} retry={() => void detail.refetch()} />
            ) : !selected ? (
              <div className="edge-router-empty-detail">{t("Select a Router to inspect its edge identity and Agent registrations.")}</div>
            ) : (
              <RouterDetail node={selected} projects={projects} />
            )}
          </section>

          <aside className="edge-router-inspector">
            {selected ? (
              <>
                <p className="edge-router-inspector__eyebrow">{t("Inspector · Router control")}</p>
                <div className="edge-router-inspector__identity"><RouterGlyph status={selected.connection_status} /><span><strong>{selected.display_name}</strong><small>{compactId(selected.id)}</small></span></div>
                <dl>
                  <InspectorFact label={t("Owner")} value={selected.owner_label} />
                  <InspectorFact label={t("Project")} value={projectName(selected.project_id, projects)} />
                  <InspectorFact label={t("Connection")} value={selected.connectivity_mode === "relay" ? "Outbound Relay" : "Direct IPv6"} />
                  <InspectorFact label={t("Last heartbeat")} value={formatDate(selected.last_presence_at)} />
                  <InspectorFact label={t("Presence expires")} value={formatDate(selected.presence_expires_at)} />
                </dl>
                <div className="edge-router-inspector__actions">
                  {selected.allowed_actions?.includes("update") && <button className="btn" type="button" onClick={() => setEditOpen(true)}><Pencil size={15} />{t("Rename or move")}</button>}
                  {selected.allowed_actions?.includes("revoke") && <button className="btn edge-router-danger" type="button" onClick={() => setRevokeOpen(true)}><Trash2 size={15} />{t("Revoke Router")}</button>}
                  {selected.owner_relation === "other" && <p>{t("Audit access does not grant permission to bind or impersonate this device.")}</p>}
                  {selected.is_legacy && <p>{t("This legacy Router has no verified owner. Revoke it, then ask the user to register it again.")}</p>}
                </div>
              </>
            ) : <p className="edge-router-inspector__empty">{t("No Router selected.")}</p>}
          </aside>
        </div>
      )}

      <RegisterRouterDialog
        open={registerOpen}
        apiContext={apiContext}
        projects={projects}
        currentProjectId={apiContext.projectId ?? ""}
        relayAvailable={capabilities.data?.relay_available}
        relayRouterEndpoint={relayService.data?.router_endpoint ?? null}
        onClose={() => setRegisterOpen(false)}
        onRegistered={async (node) => {
          setSelectedId(node.id);
          await refreshRouters();
        }}
      />
      <EditRouterDialog
        open={editOpen}
        node={selected}
        projects={projects}
        onClose={() => setEditOpen(false)}
        onSave={async (body) => {
          if (!selected) return;
          await api.updateEdgeNode(apiContext, selected.id, body);
          setEditOpen(false);
          toast.success(t("Router updated"));
          await refreshRouters();
        }}
      />
      <RevokeRouterDialog
        open={revokeOpen}
        node={selected}
        scope={scope}
        onClose={() => setRevokeOpen(false)}
        onRevoke={async () => {
          if (!selected) return;
          await api.revokeEdgeNode(apiContext, selected.id, scope);
          setRevokeOpen(false);
          setMobileDetailOpen(false);
          toast.success(t("Router revoked and device credentials invalidated"));
          await refreshRouters();
        }}
      />
    </div>
  );
}

function RouterDetail({ node, projects }: { node: EdgeNode | EdgeNodeDetail; projects: Array<{ id: string; name: string }> }) {
  useLocale();
  const { apiContext } = useAuth();
  if (node.access && !node.access.can_read) {
    return <div className="grid content-start gap-4 p-5">
      <div className="flex flex-wrap items-center gap-2"><h2>{node.display_name}</h2><ResourceOwnershipBadge ownership={node.ownership} /><ResourceAccessState access={node.access} /></div>
      <p className="text-sm leading-6 text-muted">{t("This Router can be discovered in the catalog, but its network identity, health, and Agent registrations require a Role or explicit Share.")}</p>
    </div>;
  }
  const queryClient = useQueryClient();
  const registrations = "registrations" in node ? node.registrations : [];
  const capabilityNames = Object.keys(node.capabilities ?? {});
  const resumeManaged = useMutation({
    mutationFn: (registrationId: string) => api.resumeManagedEdgeAgent(apiContext, registrationId),
    onSuccess: async () => {
      toast.success(t("Managed Agent binding resumed"));
      await queryClient.invalidateQueries({ queryKey: ["edge-node"] });
      await queryClient.invalidateQueries({ queryKey: ["edge-nodes"] });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  return (
    <>
      <header className="edge-router-detail__header">
        <div><p className="edge-router-eyebrow">{t("Physical edge device")}</p><h2>{node.display_name}</h2><p>{node.router_id} · {projectName(node.project_id, projects)}</p></div>
        <StatusBadge status={node.connection_status} />
      </header>
      <div className="edge-router-facts">
        <DetailFact label={t("Transport")} value={node.connectivity_mode === "relay" ? "Outbound Relay" : "Direct IPv6"} icon={node.connectivity_mode === "relay" ? <Radio size={16} /> : <Wifi size={16} />} />
        <DetailFact label={t("Software")} value={node.software_version || "Not reported"} />
        <DetailFact label={t("IPv6 mode")} value={humanize(node.ipv6_mode)} />
        <DetailFact label={t("Last heartbeat")} value={formatDate(node.last_presence_at)} />
      </div>
      {node.connection_status_reason === "firmware_upgrade_required" && (
        <div className="edge-router-presence-warning" role="status">
          <AlertTriangle size={18} />
          <div>
            <strong>{t("Firmware upgrade required")}</strong>
            <p>{t("This Router uses the legacy connector and cannot prove live cloud presence. Existing Agent leases may continue until they expire.")}</p>
          </div>
        </div>
      )}
      {node.connection_status_reason === "heartbeat_expired" && (
        <div className="edge-router-presence-warning" role="status">
          <WifiOff size={18} />
          <div><strong>{t("Router heartbeat expired")}</strong><p>{t("No Presence renewal arrived within five minutes.")}</p></div>
        </div>
      )}
      {node.connection_status === "degraded" && <RouterRegistrationDiagnostic node={node} />}
      <section className="edge-router-capabilities">
        <div><p className="edge-router-eyebrow">{t("Device capabilities")}</p><h3>{t("Router capability surface")}</h3></div>
        {capabilityNames.length ? <div>{capabilityNames.map((name) => <span key={name}>{humanize(name)}</span>)}</div> : <p>{t("No device capabilities have been reported.")}</p>}
      </section>
      <section className="edge-router-registrations">
        <header><div><p className="edge-router-eyebrow">{t("Agent Registrations")}</p><h3>{t("Published by this Router")}</h3></div><strong>{node.registration_count}</strong></header>
        {!registrations.length ? <p className="edge-router-empty-registrations">{node.connection_status === "degraded" ? t("No Agent registrations to display. Resolve the sync issue above before waiting for registration.") : t("No active Agent registrations. Healthy Router agents will appear after the next connector sync.")}</p> : registrations.map((registration) => (
          <div className="edge-router-registration" key={registration.id}>
            <span className="edge-router-agent-node"><Bot size={16} /></span>
            <span>
              <strong>{registration.agent?.name || registration.managed_agent_name || registration.origin}</strong>
              <small>{registration.binding_mode === "manual" ? t("Manual binding") : t("OpenWrt managed")} / {registration.transport === "relay" ? t("Relay") : t("Direct IPv6")}</small>
              <small>{registration.origin}</small>
            </span>
            <StatusBadge status={registration.provisioning_state || registration.health_status} />
            <span className="edge-router-registration__lease">
              {registration.lease_active ? t("Lease until {{0}}", { 0: formatDate(registration.lease_expires_at) }) : t("Lease expired")}
              {registration.manifest_digest ? ` / Manifest ${compactId(registration.manifest_digest)}` : ""}
            </span>
            <span className="edge-router-registration__actions">
              {registration.agent?.id && <Link className="btn" to={`/agents/${registration.agent.id}`}>{t("Open Agent")}</Link>}
              {registration.binding_mode === "suppressed" && node.allowed_actions?.includes("use") && (
                <button className="btn btn-primary" type="button" disabled={resumeManaged.isPending} onClick={() => resumeManaged.mutate(registration.id)}>{t("Resume automatic binding")}</button>
              )}
            </span>
          </div>
        ))}
      </section>
    </>
  );
}

function RegisterRouterDialog({ open, apiContext, projects, currentProjectId, relayAvailable, relayRouterEndpoint, onClose, onRegistered }: {
  open: boolean;
  apiContext: ReturnType<typeof useAuth>["apiContext"];
  projects: Array<{ id: string; name: string }>;
  currentProjectId: string;
  relayAvailable?: boolean;
  relayRouterEndpoint: EdgeRelayEndpoint | null;
  onClose: () => void;
  onRegistered: (node: EdgeNode) => Promise<void>;
}) {
  useLocale();
  const [ownership, setOwnership] = useState<import("../lib/types").ResourceOwnershipInput>(
    currentProjectId ? { scope: "project", project_id: currentProjectId } : { scope: "organization", project_id: null },
  );
  const [pairing, setPairing] = useState<{ pairing_code: string; expires_at: string } | null>(null);
  const [baseline, setBaseline] = useState<Record<string, number>>({});
  const [registered, setRegistered] = useState<EdgeNode | null>(null);
  const initialRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setOwnership(currentProjectId ? { scope: "project", project_id: currentProjectId } : { scope: "organization", project_id: null });
    setPairing(null);
    setRegistered(null);
    setBaseline({});
  }, [currentProjectId, open]);

  const polling = useQuery({
    queryKey: ["edge-pairing-poll", apiContext.token, apiContext.tenantId, pairing?.pairing_code],
    queryFn: () => api.edgeNodes(apiContext),
    enabled: open && Boolean(pairing) && !registered,
    refetchInterval: 5000,
  });

  useEffect(() => {
    if (!pairing || registered) return;
    const connected = (polling.data ?? []).find((node) => {
      const previousGeneration = baseline[node.id];
      return previousGeneration === undefined || node.enrollment_generation > previousGeneration;
    });
    if (!connected) return;
    setRegistered(connected);
    toast.success(t("{{0}} connected", { 0: connected.display_name }));
    void onRegistered(connected);
  }, [baseline, onRegistered, pairing, polling.data, registered]);

  const create = useMutation({
    mutationFn: async () => {
      const existing = await api.edgeNodes(apiContext);
      setBaseline(Object.fromEntries(existing.map((node) => [node.id, node.enrollment_generation])));
      return api.createEdgePairingCode(apiContext, { ownership });
    },
    onSuccess: (result) => setPairing(result),
  });

  const command = pairing ? [
    "uci set nexus_cloud.main.enabled='1'",
    `uci set nexus_cloud.main.base_url='${window.location.origin}'`,
    `uci set nexus_cloud.main.pairing_code='${pairing.pairing_code}'`,
    "uci commit nexus_cloud",
    "/etc/init.d/nexus-cloud restart",
  ].join("\n") : "";

  return (
    <NexilumeDialog
      open={open}
      title={registered ? t("Router connected") : pairing ? t("Pair your OpenWrt Router") : t("Register OpenWrt Router")}
      eyebrow={t("Private device enrollment")}
      description={t("The one-time code assigns this Router to your current Nexus identity. It cannot be claimed by another user.")}
      busy={create.isPending}
      initialFocusRef={initialRef}
      onClose={onClose}
      footer={registered ? <button className="btn btn-primary" type="button" onClick={onClose}>{t("Done")}</button> : pairing ? <><button className="btn" type="button" onClick={onClose}>{t("Close")}</button><button className="btn btn-primary" type="button" onClick={() => void polling.refetch()} disabled={polling.isFetching}><RefreshCw size={15} className={polling.isFetching ? "animate-spin" : ""} />{t("Check now")}</button></> : <><button className="btn" type="button" onClick={onClose} disabled={create.isPending}>{t("Cancel")}</button><button className="btn btn-primary" type="button" onClick={() => create.mutate()} disabled={create.isPending}>{create.isPending && <Loader2 size={15} className="animate-spin" />}{t("Generate pairing code")}</button></>}
    >
      {registered ? (
        <div className="edge-router-success">
          <Check size={22} />
          <div><strong>{registered.display_name}</strong><p>{t("The Router is now private to your identity and ready to publish Agent registrations.")}</p></div>
          <div className="edge-router-lineage"><span>{t("User")}</span><i /><span>{registered.display_name}</span><i /><span>{registered.registration_count}{" "}{t("Agent Registrations")}</span></div>
        </div>
      ) : pairing ? (
        <div className="edge-router-pairing">
          <div className="edge-router-pairing__code"><span>{t("One-time pairing code")}</span><code>{pairing.pairing_code}</code><button className="btn" type="button" onClick={() => copyText(pairing.pairing_code)}><Copy size={14} />{t("Copy")}</button></div>
          <p className="edge-router-pairing__expiry">{t("Expires")}{" "}{formatDate(pairing.expires_at)}{t(". Nexus checks for a new Router every five seconds.")}</p>
          {relayAvailable === false && <div className="edge-router-revoke-warning"><AlertTriangle size={18} /><div><strong>{t("Relay is unavailable on this Nexus Cloud")}</strong><p>{t("Use Direct IPv6 on the Router. Relay enrollment is blocked before the code is consumed.")}</p></div></div>}
          {relayRouterEndpoint && <div className="edge-router-relay-hint"><Radio size={18} /><div><strong>{t("Relay Router target")}</strong><p>{t("The Connector will receive")}{" "}<code>{formatRelayEndpoint(relayRouterEndpoint)}</code>{t(". Allow outbound TCP port")}{" "}<b>{relayRouterEndpoint.port}</b>.</p></div></div>}
          <div className="edge-router-pairing__method"><strong>{t("LuCI")}</strong><p>{t("Open")}{" "}<b>{t("Status → Agent Routing → Nexus Cloud")}</b>{t(", paste the Nexus URL and pairing code, enable the connector, then Save & Apply. On an already enrolled Router, the new code replaces its existing Cloud identity.")}</p></div>
          <div className="edge-router-pairing__method"><strong>{t("UCI command")}</strong><pre>{command}</pre><button className="btn" type="button" onClick={() => copyText(command)}><Copy size={14} />{t("Copy command")}</button></div>
          <div className="edge-router-waiting">{polling.isFetching ? <Loader2 size={16} className="animate-spin" /> : <Radio size={16} />}{t("Waiting for the Router to complete enrollment")}</div>
          {polling.isError && <p className="edge-router-field-error">{errorMessage(polling.error)}</p>}
        </div>
      ) : (
        <div className="edge-router-register-form" ref={initialRef}><ResourceOwnershipPicker projects={projects as import("../lib/types").Project[]} value={ownership} onChange={setOwnership} />
          <div className="edge-router-security-note"><ShieldCheck size={18} /><p>{t("The device Token and certificate identity are returned only to the Router. They are never shown in this page or Agent configuration.")}</p></div>
          {create.isError && <p className="edge-router-field-error">{errorMessage(create.error)}</p>}
        </div>
      )}
    </NexilumeDialog>
  );
}

function RouterInfrastructureBar({ status, loading, error, busy, onRefresh, onToggle }: {
  status?: EdgeRelayServiceStatus;
  loading: boolean;
  error: string;
  busy: boolean;
  onRefresh: () => void;
  onToggle: (enabled: boolean) => void;
}) {
  useLocale();
  if (loading) return <div className="edge-router-infrastructure is-loading"><Loader2 className="animate-spin" size={16} />{t("Loading Router infrastructure")}</div>;
  if (error || !status) return (
    <section className="edge-router-infrastructure is-error" aria-label={t("Router infrastructure")}>
      <AlertTriangle size={16} /><span><strong>{t("Infrastructure status unavailable")}</strong><small>{error || t("No status was returned.")}</small></span>
      <button className="btn btn-sm" type="button" onClick={onRefresh}><RefreshCw size={14} />{t("Retry")}</button>
    </section>
  );
  const router = status.router_endpoint;
  const cloud = status.cloud_invoke_endpoint;
  const sweeper = status.presence_sweeper;
  const sweeperState = sweeper?.running ? "ready" : sweeper?.reason === "starting" ? "standby" : "offline";
  const relayState = status.available ? "ready" : status.running ? "standby" : "offline";
  return (
    <section className="edge-router-infrastructure" aria-label={t("Router infrastructure")}>
      <header>
        <ShieldCheck size={16} />
        <span><small>{t("Router infrastructure")}</small><strong>{t("Automatic services")}</strong></span>
      </header>
      <div className="edge-router-infrastructure__service">
        <span className={`edge-router-infrastructure__state is-${sweeperState}`}><i />{sweeper?.running ? t("Running") : sweeper?.reason === "starting" ? t("Starting") : t("Attention")}</span>
        <strong>{t("Presence Sweeper")}</strong>
        <small>{sweeper?.automatic ? t("Automatic · every {{0}}s", { 0: sweeper.interval_seconds }) : t("Automatic schedule unavailable")}</small>
        <em>{sweeper?.last_sweep_at ? t("Last sweep {{0}}", { 0: formatDate(sweeper.last_sweep_at) }) : t("Waiting for first sweep")}</em>
      </div>
      <div className="edge-router-infrastructure__service is-relay">
        <span className={`edge-router-infrastructure__state is-${relayState}`}><i />{status.available ? t("Enabled") : status.running ? t("Standby") : t("Unavailable")}</span>
        <strong>{t("Nexus Relay")}</strong>
        <small>{t("Router port")}{" "}<b>{router?.port ?? "--"}</b>{" "}{t("· Cloud internal")}{" "}<b>{cloud?.port ?? "--"}</b></small>
        <code title={router ? formatRelayEndpoint(router) : ""}>{router ? formatRelayEndpoint(router) : relayStatusCopy(status)}</code>
      </div>
      <div className="edge-router-infrastructure__actions">
        <button className="btn btn-sm" type="button" aria-label={t("Refresh Router infrastructure status")} onClick={onRefresh}><RefreshCw size={14} /></button>
        {status.can_manage && (
          <button
            className={status.enabled ? "btn btn-sm" : "btn btn-primary btn-sm"}
            type="button"
            aria-label={status.enabled ? t("Disable Relay") : t("Enable Relay")}
            disabled={busy || (!status.enabled && (!status.configured || !status.running))}
            onClick={() => onToggle(!status.enabled)}
          >
            {busy ? <Loader2 size={15} className="animate-spin" /> : <Power size={15} />}
            {status.enabled ? t("Disable") : t("Enable")}
          </button>
        )}
      </div>
    </section>
  );
}

function EditRouterDialog({ open, node, projects, onClose, onSave }: {
  open: boolean;
  node: EdgeNode | EdgeNodeDetail | null;
  projects: Array<{ id: string; name: string }>;
  onClose: () => void;
  onSave: (body: { display_name: string; project_id: string | null }) => Promise<void>;
}) {
  useLocale();
  const [name, setName] = useState("");
  const [projectId, setProjectId] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const initialRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!open || !node) return;
    setName(node.display_name);
    setProjectId(node.project_id || "");
    setError("");
  }, [node, open]);
  async function submit() {
    setBusy(true);
    setError("");
    try { await onSave({ display_name: name.trim(), project_id: projectId || null }); }
    catch (nextError) { setError(errorMessage(nextError)); }
    finally { setBusy(false); }
  }
  return (
    <NexilumeDialog open={open} title={t("Rename or move Router")} eyebrow={t("Router identity")} description={t("A Router with active Agent bindings must be disconnected before its Project can change.")} busy={busy} initialFocusRef={initialRef} onClose={onClose} footer={<><button className="btn" type="button" onClick={onClose} disabled={busy}>{t("Cancel")}</button><button className="btn btn-primary" type="button" onClick={() => void submit()} disabled={busy || !name.trim()}>{busy && <Loader2 size={15} className="animate-spin" />}{t("Save Router")}</button></>}>
      <div className="edge-router-edit-form">
        <Field label={t("Router name")}><input ref={initialRef} className="input" value={name} onChange={(event) => setName(event.target.value)} /></Field>
        <Field label={t("Project")}><select className="select" value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">{t("All Projects · Organization Router")}</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></Field>
        {error && <p className="edge-router-field-error">{error}</p>}
      </div>
    </NexilumeDialog>
  );
}

function RevokeRouterDialog({ open, node, scope, onClose, onRevoke }: { open: boolean; node: EdgeNode | EdgeNodeDetail | null; scope: RouterScope; onClose: () => void; onRevoke: () => Promise<void> }) {
  useLocale();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function revoke() {
    setBusy(true); setError("");
    try { await onRevoke(); } catch (nextError) { setError(errorMessage(nextError)); } finally { setBusy(false); }
  }
  return (
    <NexilumeDialog open={open} title={t("Revoke OpenWrt Router?")} eyebrow={scope === "admin" ? t("Emergency administrator action") : t("Destructive action")} description={t("This immediately invalidates {{0}}'s device credential, withdraws its Agent registrations, and marks bound Runtime deployments failed.", { 0: node?.display_name || "the Router" })} busy={busy} onClose={onClose} footer={<><button className="btn" type="button" onClick={onClose} disabled={busy}>{t("Cancel")}</button><button className="btn btn-danger" type="button" onClick={() => void revoke()} disabled={busy}>{busy && <Loader2 size={15} className="animate-spin" />}{t("Revoke Router")}</button></>}>
      <div className="edge-router-revoke-warning"><AlertTriangle size={20} /><div><strong>{t("Re-pairing will require a new code")}</strong><p>{t("The same physical Router ID may enroll again, but its Token, certificate identity, owner, and registrations will be rebuilt.")}</p></div></div>
      {error && <p className="edge-router-field-error">{error}</p>}
    </NexilumeDialog>
  );
}

function RouterMetric({ label, value, detail, tone = "neutral" }: { label: string; value: number; detail: string; tone?: "neutral" | "healthy" | "warning" }) {
  useLocale();
  return <div className={`edge-router-metric edge-router-metric--${tone}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function RouterGlyph({ status }: { status: string }) {
  useLocale();
  const online = status === "online";
  return <span className={`edge-router-glyph is-${status}`} aria-label={t("Router {{0}}", { 0: status })}><Router size={18} /><i>{online ? <Wifi size={10} /> : <WifiOff size={10} />}</i></span>;
}

function DetailFact({ label, value, icon }: { label: string; value: string; icon?: ReactNode }) {
  useLocale();
  return <div>{icon && <span>{icon}</span>}<small>{label}</small><strong>{value}</strong></div>;
}

function InspectorFact({ label, value }: { label: string; value: string }) {
  useLocale();
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function RouterError({ title, detail, retry }: { title: string; detail: string; retry: () => void }) {
  useLocale();
  return <div className="edge-router-error"><AlertTriangle size={20} /><div><strong>{title}</strong><p>{detail}</p></div><button className="btn" type="button" onClick={retry}><RefreshCw size={14} />{t("Retry")}</button></div>;
}

function summarizeNodes(nodes: EdgeNode[]) {
  return {
    total: nodes.length,
    online: nodes.filter((node) => node.connection_status === "online").length,
    attention: nodes.filter((node) => !["online", "pending"].includes(node.connection_status)).length,
    registrations: nodes.reduce((sum, node) => sum + node.registration_count, 0),
  };
}


function projectName(projectId: string | null, projects: Array<{ id: string; name: string }>) {
  return projectId ? projects.find((project) => project.id === projectId)?.name || "Project" : "All projects";
}

function humanize(value: string) {
  return tToken(value);
}

function formatRelayEndpoint(endpoint: EdgeRelayEndpoint) {
  return `${endpoint.scheme}://${endpoint.host}:${endpoint.port}${endpoint.path}`;
}

function relayStatusCopy(status: EdgeRelayServiceStatus) {
  if (status.reason === "ready") return t("Healthy listeners are advertised to OpenWrt Routers and available for new Relay assignments.");
  if (status.reason === "operator_disabled") return t("The Relay process is healthy and standing by. A super administrator can advertise it with one click.");
  if (status.reason === "relay_process_unavailable") return t("One or both Relay listeners are offline. Enabling remains locked until the process is healthy.");
  return t("Relay trust or endpoint configuration is incomplete. Check the Nexus Cloud startup logs.");
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : t("The request could not be completed.");
}

function copyText(value: string) {
  void navigator.clipboard.writeText(value).then(() => toast.success(t("Copied")), () => toast.error(t("Copy failed")));
}
