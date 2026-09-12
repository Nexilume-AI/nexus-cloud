import { useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Boxes, CircleDot, Cpu, GitBranch, Layers3, Server, Tag, Waypoints } from "lucide-react";

import type {
  RoutingTrace,
  TopologyModelOffer,
  TopologyPayload,
  TopologyPool,
  TopologyRouter,
  TopologyRuntime,
  TopologySource,
} from "../lib/types";
import { compactId } from "../lib/format";
import { ResourceOwnershipBadge } from "./ResourceOwnership";
import { ApplicationDistributionContext } from "../app/distribution";
import type { TopologyPublicationPolicy } from "../app/resourcePublishing";

type Inspectable = {
  id: string;
  type: "router" | "api_model" | "pool" | "source" | "offer" | "runtime";
  name: string;
  status: string;
  eyebrow: string;
  fields: Array<[string, string | number | null | undefined]>;
};

export function NexilumeTopology({
  data,
  routerId,
  trace,
  title = "AI capability network",
  description,
  onRouterChange,
}: {
  data: TopologyPayload;
  routerId?: string;
  trace?: RoutingTrace | null;
  title?: string;
  description?: string;
  onRouterChange?: (routerId: string) => void;
}) {
  const publication = useContext(ApplicationDistributionContext)?.resourcePublishing?.topologyPolicy;
  const routers = useMemo(
    () => [...data.routers].sort((a, b) => String(b.last_request_at ?? "").localeCompare(String(a.last_request_at ?? ""))),
    [data.routers],
  );
  const router = routers.find((item) => item.id === routerId) ?? routers[0] ?? null;
  const [apiModelName, setApiModelName] = useState("");
  const isAggregation = router?.router_type === "aggregation";
  const tracedApiModelName = trace?.aggregation?.model || "";

  useEffect(() => {
    if (!isAggregation || !router) {
      setApiModelName("");
      return;
    }
    const availableNames = new Set(router.api_models.map((model) => model.model_name));
    if (tracedApiModelName && availableNames.has(tracedApiModelName)) {
      setApiModelName(tracedApiModelName);
      return;
    }
    setApiModelName((current) => availableNames.has(current)
      ? current
      : router.api_models.find((model) => model.available)?.model_name || router.api_models[0]?.model_name || "");
  }, [isAggregation, router?.id, router?.api_models, tracedApiModelName]);

  const selectedApiModel = isAggregation
    ? router?.api_models.find((model) => model.model_name === apiModelName) ?? router?.api_models[0] ?? null
    : null;
  const executionRouter = isAggregation
    ? routers.find((item) => item.id === selectedApiModel?.execution_router_id) ?? null
    : router;
  const lineageRouter = executionRouter;
  const executionTrace = isAggregation ? trace?.child_router : trace;
  const boundPools = lineageRouter
    ? lineageRouter.pool_ids.map((id) => data.pools.find((pool) => pool.id === id)).filter(Boolean) as TopologyPool[]
    : [];
  const selectedPoolId = executionTrace?.router.selected_pool_id || lineageRouter?.last_selected_pool_id || boundPools[0]?.id || "";
  const selectedSourceId = executionTrace?.pool.selected_source_id || lineageRouter?.last_selected_source_id || "";
  const [inspected, setInspected] = useState<Inspectable | null>(null);

  useEffect(() => {
    setInspected(router ? inspectRouter(router) : null);
  }, [router]);

  if (!router) {
    return (
      <section className="topology-empty">
        <Waypoints size={22} />
        <div>
          <h2>{title}</h2>
          <p>Create a Router and bind at least one Model Pool to reveal the capability network.</p>
        </div>
      </section>
    );
  }

  const visiblePools = boundPools.slice(0, 4);
  const selectedPool = boundPools.find((pool) => pool.id === selectedPoolId) ?? visiblePools[0];
  const sourceIds = new Set(selectedPool?.sources.map((source) => source.source_id) ?? []);
  const visibleSources = data.sources.filter((source) => sourceIds.has(source.id)).slice(0, 5);
  const runtimeIds = new Set(visibleSources.map((source) => source.runtime_id).filter(Boolean));
  const visibleRuntimes = data.runtimes.filter((runtime) => runtimeIds.has(runtime.id)).slice(0, 5);
  const visibleOfferIds = new Set(visibleSources.map((source) => source.model_offer_id).filter(Boolean));
  const visibleOffers = visibleRuntimes.flatMap((runtime) =>
    runtime.model_offers
      .filter((offer) => visibleOfferIds.has(offer.id))
      .map((offer) => ({ offer, runtimeId: runtime.id, runtimeName: runtime.name, runtimeOwnership: runtime.resource_ownership })),
  );
  const wireLayout = isAggregation
    ? { runtimeEnd: 125, offerStart: 145, offerEnd: 250, sourceStart: 270, sourceEnd: 390, poolStart: 410, poolEnd: 605, poolMid: 615, routerBend: 620, routerStart: 635 }
    : { runtimeEnd: 145, offerStart: 190, offerEnd: 320, sourceStart: 355, sourceEnd: 500, poolStart: 540, poolEnd: 810, poolMid: 835, routerBend: 860, routerStart: 885 };

  return (
    <section className="topology-frame" aria-label={title}>
      <header className="topology-header">
        <div>
          <div className="tech-label">NEXILUME / CONTROL FABRIC</div>
          <h2>{title}</h2>
          <p>{description || (isAggregation ? "Each API model connects to one Execution Router and its complete Model Pool lineage." : "Router selects a Pool. The selected Pool then selects one Source.")}</p>
        </div>
        <div className="topology-header-controls">
          {isAggregation && router.api_models.length > 1 && <label className="topology-router-select"><span>API model</span><select value={selectedApiModel?.model_name || ""} onChange={(event) => setApiModelName(event.target.value)}>{router.api_models.map((model) => <option value={model.model_name} key={model.binding_id}>{model.model_name}</option>)}</select></label>}
          {onRouterChange && routers.length > 1 && (
            <label className="topology-router-select">
              <span>Active router</span>
              <select value={router.id} onChange={(event) => onRouterChange(event.target.value)}>
                {routers.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
              </select>
            </label>
          )}
        </div>
      </header>

      <div className={`topology-workspace ${isAggregation ? "is-aggregation" : ""}`}>
        <div className={`topology-canvas ${isAggregation ? "is-aggregation" : ""}`} data-flow={isAggregation ? "RUNTIME → MODEL OFFER → SOURCE → POOL → EXECUTION ROUTER → API MODEL → AGGREGATION API" : "RUNTIME → MODEL OFFER → SOURCE → POOL → ROUTER"} role="group" aria-label={isAggregation ? "Runtime to Model Offer to Source to Pool to Execution Router to API Model to Aggregation Router capability lineage" : "Runtime to Model Offer to Source to Pool to Router capability lineage"}>
          <svg className="topology-wires" viewBox="0 0 1040 500" preserveAspectRatio="none" aria-hidden="true">
            {visiblePools.map((pool, index) => {
              const y = poolY(index, visiblePools.length);
              const selected = pool.id === selectedPool?.id;
              return <path key={`router-${pool.id}`} className={selected ? "topology-wire is-active" : "topology-wire"} d={`M ${wireLayout.poolEnd} ${y} L ${wireLayout.poolMid} ${y} L ${wireLayout.routerBend} 250 L ${wireLayout.routerStart} 250`} />;
            })}
            {visibleSources.map((source, index) => {
              if (!source.model_offer_id) return null;
              const sourceY = sourceYPosition(index, visibleSources.length);
              const offerIndex = Math.max(visibleOffers.findIndex(({ offer }) => offer.id === source.model_offer_id), 0);
              const offerY = sourceYPosition(offerIndex, Math.max(visibleOffers.length, 1));
              const selectedPoolIndex = Math.max(visiblePools.findIndex((pool) => pool.id === selectedPool?.id), 0);
              const selectedPoolY = poolY(selectedPoolIndex, Math.max(visiblePools.length, 1));
              const selected = source.id === selectedSourceId;
              const wireClass = selected ? "topology-wire is-active" : "topology-wire";
              return (
                <g key={`source-${source.id}`}>
                  <path className={wireClass} d={`M ${wireLayout.offerEnd} ${offerY} L ${wireLayout.offerEnd + 8} ${offerY} L ${wireLayout.sourceStart - 8} ${sourceY} L ${wireLayout.sourceStart} ${sourceY}`} />
                  <path className={wireClass} d={`M ${wireLayout.sourceEnd} ${sourceY} L ${wireLayout.sourceEnd + 8} ${sourceY} L ${wireLayout.poolStart - 8} ${selectedPoolY} L ${wireLayout.poolStart} ${selectedPoolY}`} />
                </g>
              );
            })}
            {visibleOffers.map(({ offer, runtimeId }, index) => {
              const offerY = sourceYPosition(index, visibleOffers.length);
              const runtimeIndex = Math.max(visibleRuntimes.findIndex((runtime) => runtime.id === runtimeId), 0);
              const runtimeY = sourceYPosition(runtimeIndex, Math.max(visibleRuntimes.length, 1));
              const selected = visibleSources.some((source) => source.id === selectedSourceId && source.model_offer_id === offer.id);
              return <path key={`offer-${offer.id}`} className={selected ? "topology-wire is-active" : "topology-wire"} d={`M ${wireLayout.runtimeEnd} ${runtimeY} L ${wireLayout.runtimeEnd + 7} ${runtimeY} L ${wireLayout.offerStart - 7} ${offerY} L ${wireLayout.offerStart} ${offerY}`} />;
            })}
            {isAggregation && selectedApiModel && <>
              <path className={selectedApiModel.available ? "topology-wire is-active" : "topology-wire"} d="M 780 250 L 800 250" />
              <path className={selectedApiModel.available ? "topology-wire is-active" : "topology-wire"} d="M 905 250 L 925 250" />
            </>}
          </svg>

          {isAggregation ? <>
            <button className="topology-node topology-router-node topology-execution-router-node" title={`Execution Router: ${selectedApiModel?.execution_router_name || "Unavailable"}`} onClick={() => setInspected(executionRouter ? inspectRouter(executionRouter) : inspectRestrictedExecution(selectedApiModel))}>
              <GitBranch size={18} />
              <span><small>EXECUTION ROUTER</small><strong>{selectedApiModel?.execution_router_name || "No mapping"}</strong><em>{selectedApiModel?.execution_model_name || "Unavailable"}</em>{executionRouter && <ResourceOwnershipBadge ownership={executionRouter.resource_ownership} />}</span>
              <StatusDot status={selectedApiModel?.execution_router_status || "unavailable"} />
            </button>
            <button className="topology-node topology-api-model-node" title={`API Model: ${selectedApiModel?.model_name || "Unavailable"}`} onClick={() => selectedApiModel && setInspected(inspectApiModel(selectedApiModel))}>
              <Tag size={17} />
              <span><small>API MODEL</small><strong>{selectedApiModel?.model_name || "No model"}</strong><em>1:1 mapping</em></span>
              <StatusDot status={selectedApiModel?.available ? "healthy" : "unavailable"} />
            </button>
            <button className="topology-node topology-router-node topology-aggregation-router-node" title={`Aggregation Router: ${router.name}`} onClick={() => setInspected(inspectRouter(router))}>
              <GitBranch size={18} />
              <span><small>AGGREGATION API</small><strong>{router.name}</strong><em>{router.api_models.length} models</em><ResourceOwnershipBadge ownership={router.resource_ownership} /></span>
              <StatusDot status={router.status} />
            </button>
          </> : <button className="topology-node topology-router-node" title={`Router: ${router.name}`} onClick={() => setInspected(inspectRouter(router))}>
            <GitBranch size={19} />
            <span><small>ROUTER</small><strong>{router.name}</strong><em>{router.strategy.replaceAll("_", " ")}</em><ResourceOwnershipBadge ownership={router.resource_ownership} /></span>
            <StatusDot status={router.status} />
          </button>}

          <div className="topology-pool-stack" role="list" aria-label="Model Pools">
            {visiblePools.map((pool) => {
              const isSelected = pool.id === selectedPool?.id;
              const poolTrace = executionTrace?.router.candidates.find((candidate) => candidate.pool_id === pool.id);
              return (
                <button
                  key={pool.id}
                  title={`Model Pool: ${pool.display_name}`}
                  className={`topology-pool-node ${isSelected ? "is-selected" : ""} ${poolTrace?.exclusion_reason ? "is-excluded" : ""}`}
                  onClick={() => setInspected(inspectPool(pool))}
                  role="listitem"
                >
                  <span className="topology-pool-heading">
                    <span><small>MODEL POOL {poolTrace?.rank ? `/ 0${poolTrace.rank}` : ""}</small><strong>{pool.display_name}</strong></span>
                    <StatusDot status={pool.health_status} />
                  </span>
                  <span className="topology-pool-meta">{pool.routing_strategy.replaceAll("_", " ")} · {pool.enabled_source_count} sources</span>
                  <ResourceOwnershipBadge ownership={pool.resource_ownership} />
                  {isSelected && visibleSources.length > 0 && (
                    <span className="topology-source-rail">
                      {visibleSources.slice(0, 3).map((source) => (
                        <span key={source.id} className={source.id === selectedSourceId ? "is-hit" : ""}>{source.name}</span>
                      ))}
                    </span>
                  )}
                  {poolTrace?.exclusion_reason && <span className="topology-exclusion">{poolTrace.exclusion_reason.replaceAll("_", " ")}</span>}
                </button>
              );
            })}
            {boundPools.length > visiblePools.length && <div className="topology-more">+{boundPools.length - visiblePools.length} aggregated pools</div>}
          </div>

          <div className="topology-source-stack" role="list" aria-label="Sources in selected Pool">
            {visibleSources.map((source) => (
              <button key={source.id} title={`Source: ${source.name}`} className={`topology-source-node ${source.id === selectedSourceId ? "is-selected" : ""}`} onClick={() => setInspected(inspectSource(source))} role="listitem">
                <CircleDot size={15} />
                <span><small>SOURCE</small><strong>{source.name}</strong><em>{source.provider} · {source.latency_ms || "–"} ms</em><ResourceOwnershipBadge ownership={source.resource_ownership} /></span>
                <StatusDot status={source.health_status} />
              </button>
            ))}
          </div>

          <div className="topology-offer-stack" role="list" aria-label="Model Offers used by selected Sources">
            {visibleOffers.map(({ offer, runtimeName, runtimeOwnership }) => (
              <button
                key={offer.id}
                title={`Model Offer: ${offer.canonical_model_key || offer.upstream_model_id}`}
                className={`topology-offer-node ${visibleSources.some((source) => source.id === selectedSourceId && source.model_offer_id === offer.id) ? "is-selected" : ""}`}
                onClick={() => setInspected(inspectOffer(offer, runtimeName, publication))}
                role="listitem"
              >
                <Layers3 size={15} />
                <span><small>MODEL OFFER</small><strong>{offer.canonical_model_key || offer.upstream_model_id}</strong><em>{offer.upstream_model_id}</em><ResourceOwnershipBadge ownership={runtimeOwnership} /></span>
                <StatusDot status={offer.health_status} />
              </button>
            ))}
          </div>

          <div className="topology-runtime-stack" role="list" aria-label="Provider Runtimes">
            {visibleRuntimes.map((runtime) => (
              <button key={runtime.id} title={`Provider Runtime: ${runtime.name}`} className="topology-runtime-node" onClick={() => setInspected(inspectRuntime(runtime, publication))} role="listitem">
                <Cpu size={16} />
                <span><small>PROVIDER RUNTIME</small><strong>{runtime.name}</strong><em>{runtime.publisher}</em><ResourceOwnershipBadge ownership={runtime.resource_ownership} /></span>
                <StatusDot status={runtime.status} />
              </button>
            ))}
          </div>
        </div>

        <TopologyInspector item={inspected} />
      </div>

      <div className="topology-mobile" aria-label="Routing stages">
        {visibleRuntimes.map((runtime) => <Stage key={runtime.id} icon={<Server size={16} />} label="Runtime" value={runtime.name} detail={`${runtime.publisher}${runtime.resource_ownership ? ` · ${runtime.resource_ownership.label}` : ""}`} active={visibleSources.some((source) => source.id === selectedSourceId && source.runtime_id === runtime.id)} />)}
        {visibleOffers.map(({ offer, runtimeName }) => <Stage key={offer.id} icon={<Layers3 size={16} />} label="Model Offer" value={offer.canonical_model_key || offer.upstream_model_id} detail={`${runtimeName} · ${offer.health_status}`} active={visibleSources.some((source) => source.id === selectedSourceId && source.model_offer_id === offer.id)} />)}
        {visibleSources.map((source) => <Stage key={source.id} icon={<CircleDot size={16} />} label="Source" value={source.name} detail={`${source.provider} · ${source.health_status}${source.resource_ownership ? ` · ${source.resource_ownership.label}` : ""}`} active={source.id === selectedSourceId} />)}
        {visiblePools.map((pool) => <Stage key={pool.id} icon={<Boxes size={16} />} label="Pool" value={pool.display_name} detail={`${pool.routing_strategy} · ${pool.enabled_source_count} sources${pool.resource_ownership ? ` · ${pool.resource_ownership.label}` : ""}`} active={pool.id === selectedPool?.id} />)}
        {isAggregation ? <>
          <Stage icon={<GitBranch size={16} />} label="Execution Router" value={selectedApiModel?.execution_router_name || "Unavailable"} detail={`${selectedApiModel?.execution_model_name || selectedApiModel?.message || "No mapped model"}${executionRouter?.resource_ownership ? ` · ${executionRouter.resource_ownership.label}` : ""}`} active={Boolean(selectedApiModel?.available)} />
          <Stage icon={<Tag size={16} />} label="API Model" value={selectedApiModel?.model_name || "No model"} detail="1:1 mapping" active={Boolean(selectedApiModel?.available)} />
          <Stage icon={<GitBranch size={16} />} label="Aggregation API" value={router.name} detail={`${router.api_models.length} models${router.resource_ownership ? ` · ${router.resource_ownership.label}` : ""}`} active />
        </> : <Stage icon={<GitBranch size={16} />} label="Router" value={router.name} detail={`${router.strategy}${router.resource_ownership ? ` · ${router.resource_ownership.label}` : ""}`} active />}
      </div>
    </section>
  );
}

function TopologyInspector({ item }: { item: Inspectable | null }) {
  return (
    <aside className="topology-inspector" aria-live="polite">
      <div className="tech-label">INSPECTOR</div>
      {item ? (
        <>
          <div className="topology-inspector-title"><span>{item.type.replaceAll("_", " ")}</span><StatusDot status={item.status} /></div>
          <h3>{item.name}</h3>
          <div className="topology-inspector-id">{compactId(item.id)}</div>
          <dl>
            {item.fields.filter(([, value]) => value !== null && value !== undefined && value !== "").map(([label, value]) => (
              <div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>
            ))}
          </dl>
        </>
      ) : <p>Select a node to inspect its routing state and lineage.</p>}
    </aside>
  );
}

function Stage({ icon, label, value, detail, active }: { icon: ReactNode; label: string; value: string; detail: string; active?: boolean }) {
  return <div className={`topology-stage ${active ? "is-active" : ""}`}><span className="topology-stage-icon">{icon}</span><span><small>{label}</small><strong>{value}</strong><em>{detail.replaceAll("_", " ")}</em></span></div>;
}

function StatusDot({ status }: { status: string }) {
  return <span className={`topology-status topology-status-${status}`} title={status}><span />{status.replaceAll("_", " ")}</span>;
}

function inspectRouter(router: TopologyRouter): Inspectable {
  return router.router_type === "aggregation"
    ? { id: router.id, type: "router", name: router.name, status: router.status, eyebrow: "Aggregation Router", fields: [["API models", router.api_models.length], ["Execution Routers", router.execution_router_count], ["Requests", router.request_count], ["Avg latency", `${router.average_latency_ms} ms`], ["Cost", router.total_cost], ["Version", router.current_version || "Built-in"]] }
    : { id: router.id, type: "router", name: router.name, status: router.status, eyebrow: "Execution Router", fields: [["Strategy", router.strategy], ["Pools", router.pool_count], ["Requests", router.request_count], ["Avg latency", `${router.average_latency_ms} ms`], ["Cost", router.total_cost], ["Version", router.current_version || "Built-in"]] };
}

function inspectApiModel(model: TopologyRouter["api_models"][number]): Inspectable {
  return { id: model.binding_id, type: "api_model", name: model.model_name, status: model.available ? "healthy" : "unavailable", eyebrow: "API Model", fields: [["Execution Router", model.execution_router_name], ["Execution model", model.execution_model_name], ["Mapping", "1:1"], ["Availability", model.available ? "Available" : "Unavailable"], ["Reason", model.message]] };
}

function inspectRestrictedExecution(model: TopologyRouter["api_models"][number] | null): Inspectable {
  return { id: model?.binding_id || "restricted", type: "router", name: model?.execution_router_name || "Execution Router unavailable", status: model?.execution_router_status || "unavailable", eyebrow: "Execution Router", fields: [["Model", model?.execution_model_name], ["Availability", model?.available ? "Available" : "Unavailable"], ["Reason", model?.message]] };
}

function inspectPool(pool: TopologyPool): Inspectable {
  return { id: pool.id, type: "pool", name: pool.display_name, status: pool.health_status, eyebrow: "Model Pool", fields: [["Model", pool.name], ["Source strategy", pool.routing_strategy], ["Sources", pool.source_count], ["Enabled", pool.enabled_source_count], ["Lowest price", pool.lowest_price_per_1k_tokens], ["Lowest latency", pool.lowest_latency_ms ? `${pool.lowest_latency_ms} ms` : "–"]] };
}

function inspectSource(source: TopologySource): Inspectable {
  return { id: source.id, type: "source", name: source.name, status: source.health_status, eyebrow: "Source", fields: [["Provider", source.provider], ["Canonical model", source.canonical_model_key], ["Upstream model", source.upstream_model_id], ["Origin", source.source_type], ["Publisher", source.publisher], ["Price / 1K", source.price_per_1k_tokens], ["Latency", `${source.latency_ms || 0} ms`]] };
}

export function inspectOffer(offer: TopologyModelOffer, runtimeName: string, publication?: TopologyPublicationPolicy): Inspectable {
  return {
    id: offer.id,
    type: "offer",
    name: offer.canonical_model_key || offer.upstream_model_id,
    status: offer.health_status,
    eyebrow: "Model Offer",
    fields: [
      ["Runtime", runtimeName],
      ["Canonical model", offer.canonical_model_key],
      ["Upstream model", offer.upstream_model_id],
      ["Offer status", offer.status],
      ["Sources", offer.source_ids.length],
      ...(publication?.offerFields(offer) ?? []),
    ],
  };
}

export function inspectRuntime(runtime: TopologyRuntime, publication?: TopologyPublicationPolicy): Inspectable {
  return { id: runtime.id, type: "runtime", name: runtime.name, status: runtime.status, eyebrow: "Provider Runtime", fields: [["Runtime", runtime.runtime_type], ["Model offers", runtime.model_offers.length], ["Publisher", runtime.publisher], ["Ownership", runtime.ownership], ...(publication?.runtimeFields(runtime) ?? [])] };
}

function poolY(index: number, count: number) { return 80 + ((index + 0.5) * 340) / Math.max(count, 1); }
function sourceYPosition(index: number, count: number) { return 70 + ((index + 0.5) * 360) / Math.max(count, 1); }
