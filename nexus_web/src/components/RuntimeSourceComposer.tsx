import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Check, Loader2, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "../app/AuthContext";
import { useApplicationDistribution } from "../app/distribution";
import { api } from "../lib/api";
import type { Deployment, ModelGroup } from "../lib/types";
import { createRuntimeSources, type SourceCreator } from "../lib/sourceCreation";
import { StatusBadge } from "./Badge";
import { NexilumeDialog } from "./NexilumeControls";

export type RuntimeSourceComposerOffer = {
  id: string;
  canonicalModelKey: string;
  canonicalModelName: string;
  upstreamModelId: string;
  status: string;
  healthStatus: string;
  canonicalMapped?: boolean;
  existingSourceIds?: string[];
};

export type SourceComposerOrigin = {
  id: string;
  name: string;
  status: string;
  offers: RuntimeSourceComposerOffer[];
};
export type RuntimeSourceComposerOrigin = SourceComposerOrigin & { kind: "provider_runtime" };

export function RuntimeSourceComposer(props: { origin: RuntimeSourceComposerOrigin | null; initialOfferIds?: string[]; onClose: () => void }) {
  return <SourceComposer {...props} createSources={createRuntimeSources} />;
}

/** The host supplies a submission strategy; this view never selects a commercial origin. */
export function SourceComposer({
  origin,
  initialOfferIds,
  onClose,
  createSources,
  lineagePrefix,
}: {
  origin: SourceComposerOrigin | null;
  initialOfferIds?: string[];
  onClose: () => void;
  createSources: SourceCreator;
  lineagePrefix?: ReactNode;
}) {
  const { apiContext } = useAuth();
  const newPoolVisibility = useApplicationDistribution().resourceOwnership?.newPoolVisibility ?? "private";
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [created, setCreated] = useState<Deployment[]>([]);
  const [error, setError] = useState("");
  const pools = useQuery({
    queryKey: ["models", apiContext, "runtime-source-composer"],
    queryFn: () => api.models(apiContext),
    enabled: Boolean(origin),
  });
  const eligibleOffers = useMemo(
    () =>
      (origin?.offers ?? []).filter(
        (offer) =>
          origin?.status === "active" &&
          ["detected", "confirmed"].includes(offer.status) &&
          offer.canonicalMapped !== false &&
          ["healthy", "degraded"].includes(offer.healthStatus) &&
          !(offer.existingSourceIds?.length),
      ),
    [origin],
  );
  const initialOfferKey = initialOfferIds?.join(",") ?? "all";

  useEffect(() => {
    if (!origin) return;
    const allowed = new Set(eligibleOffers.map((offer) => offer.id));
    const requested = initialOfferIds?.length
      ? initialOfferIds.filter((id) => allowed.has(id))
      : [...allowed];
    setStep(1);
    setSelected(Object.fromEntries(requested.map((id) => [id, true])));
    setTargets({});
    setCreated([]);
    setError("");
  }, [origin?.id, initialOfferKey]);

  const selectedOffers = eligibleOffers.filter((offer) => selected[offer.id]);
  const create = useMutation({
    mutationFn: () => {
      if (!origin) throw new Error("Provider Runtime is unavailable.");
      if (!selectedOffers.length) throw new Error("Select at least one available Model Offer.");
      return createSources(apiContext, { runtimeId: origin.id, offers: selectedOffers, targets, newPoolVisibility });
    },
    onSuccess: async (sources) => {
      setCreated(sources);
      setStep(3);
      setError("");
      toast.success(sources.length === 1 ? "Source connected" : `${sources.length} Sources connected atomically`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["deployments"] }),
        queryClient.invalidateQueries({ queryKey: ["models"] }),
        queryClient.invalidateQueries({ queryKey: ["topology"] }),
        queryClient.invalidateQueries({ queryKey: ["provider-runtimes"] }),
        queryClient.invalidateQueries({ queryKey: ["provider-connections"] }),
      ]);
    },
    onError: (value) => {
      const message = value instanceof Error ? value.message : "Source creation failed";
      setError(message);
      toast.error(message);
    },
  });

  function close() {
    if (!create.isPending) onClose();
  }

  return (
    <NexilumeDialog
      open={Boolean(origin)}
      onClose={close}
      busy={create.isPending}
      size="large"
      eyebrow={`Source composer · ${String(step).padStart(2, "0")}/03`}
      title={step === 3 ? "Sources connected" : "Use Model Offers in Sources"}
      description={
        step === 1
          ? "Choose mapped, healthy Offers from this Runtime. Each Offer becomes one immutable Source."
          : step === 2
            ? "Place every single-model Source into an existing compatible Pool or a new Pool."
            : "Source identity is fixed; Pool membership policy now controls selection and fallback."
      }
      footer={
        step === 1 ? (
          <>
            <button className="btn" onClick={close}>Cancel</button>
            <button
              className="btn btn-primary"
              disabled={!selectedOffers.length}
              onClick={() => {
                setError("");
                setStep(2);
              }}
            >
              Choose Pools <ArrowRight size={15} />
            </button>
          </>
        ) : step === 2 ? (
          <>
            <button className="btn" onClick={() => setStep(1)} disabled={create.isPending}>Back</button>
            <button className="btn btn-primary" onClick={() => create.mutate()} disabled={create.isPending}>
              {create.isPending && <Loader2 size={15} className="animate-spin" />}
              Create Sources
            </button>
          </>
        ) : (
          <>
            <button className="btn" onClick={close}>Done</button>
            <button className="btn" onClick={() => { close(); navigate("/model-pool?view=sources"); }}>
              View Sources
            </button>
            <button className="btn btn-primary" onClick={() => { close(); navigate("/model-pool?view=pool"); }}>
              Open Pool <ArrowRight size={15} />
            </button>
          </>
        )
      }
    >
      <div className="source-task-steps" aria-label="Source creation progress">
        {["Select Offers", "Choose Pools", "Connected"].map((label, index) => (
          <div key={label} className={step >= index + 1 ? "is-active" : ""}>
            <span>{String(index + 1).padStart(2, "0")}</span>{label}
          </div>
        ))}
      </div>

      {origin && step === 1 && (
        <div className="space-y-3">
          {origin.offers.map((offer) => {
            const available = eligibleOffers.some((item) => item.id === offer.id);
            return (
              <label key={offer.id} className="grid min-h-16 gap-3 border border-border p-3 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center">
                <input
                  type="checkbox"
                  checked={Boolean(selected[offer.id])}
                  disabled={!available}
                  onChange={(event) => setSelected((current) => ({ ...current, [offer.id]: event.target.checked }))}
                  aria-label={`Use ${offer.canonicalModelName} in Source`}
                />
                <span className="min-w-0">
                  <strong className="block">{offer.canonicalModelName}</strong>
                  <small className="block truncate text-muted">{offer.canonicalModelKey} · upstream {offer.upstreamModelId}</small>
                  {!available && (
                    <small className="mt-1 block text-amber-800">
                      {offer.existingSourceIds?.length
                        ? "Source already exists"
                        : origin.status !== "active"
                          ? "Runtime is not active"
                          : offer.canonicalMapped === false
                            ? "Canonical model mapping is pending"
                            : !["detected", "confirmed"].includes(offer.status)
                              ? "Offer is not currently advertised"
                            : `Health is ${offer.healthStatus}`}
                    </small>
                  )}
                </span>
                <StatusBadge status={offer.existingSourceIds?.length ? "connected" : offer.healthStatus} />
              </label>
            );
          })}
          {!origin.offers.length && (
            <div className="source-task-empty"><TriangleAlert size={18} /><div><strong>No Model Offers</strong><p>Refresh the Runtime model catalog before creating Sources.</p></div></div>
          )}
          {origin.offers.length > 0 && !eligibleOffers.length && (
            <div className="source-task-empty"><TriangleAlert size={18} /><div><strong>No available Offers</strong><p>Start the Runtime, refresh model health and mapping, or review its existing Source lineage.</p></div></div>
          )}
        </div>
      )}

      {origin && step === 2 && (
        <div className="space-y-3">
          {selectedOffers.map((offer) => {
            const compatible = (pools.data ?? []).filter(
              (pool: ModelGroup) => pool.canonical_model_key === offer.canonicalModelKey,
            );
            return (
              <div key={offer.id} className="grid gap-3 border border-border p-3 md:grid-cols-[minmax(0,1fr)_minmax(14rem,1fr)] md:items-center">
                <div><strong>{offer.canonicalModelName}</strong><small className="block text-muted">Immutable Source · {offer.canonicalModelKey}</small></div>
                <select
                  className="select"
                  value={targets[offer.id] ?? "new"}
                  onChange={(event) => setTargets((current) => ({ ...current, [offer.id]: event.target.value }))}
                  aria-label={`Target Pool for ${offer.canonicalModelName}`}
                >
                  <option value="new">Create Pool: {offer.canonicalModelKey}</option>
                  {compatible.map((pool) => (
                    <option key={pool.id} value={`pool:${pool.id}`}>Existing: {pool.display_name || pool.name}</option>
                  ))}
                </select>
              </div>
            );
          })}
        </div>
      )}

      {origin && step === 3 && created.length > 0 && (
        <div className="source-lineage-success">
          <div className="source-lineage-success__mark"><Check size={24} /></div>
          {created.map((source) => (
            <div key={source.id} className={`source-lineage-success__track ${lineagePrefix ? "is-five-stage" : ""}`} aria-label="Created Source lineage">
              {lineagePrefix}
              <span><small>Runtime</small><strong>{origin.name}</strong></span><i aria-hidden="true" />
              <span><small>Model Offer</small><strong>{source.canonical_model_key}</strong></span><i aria-hidden="true" />
              <span><small>Source</small><strong>{source.deployment_id}</strong></span><i aria-hidden="true" />
              <span><small>Pool</small><strong>{source.canonical_model_key}</strong></span>
            </div>
          ))}
        </div>
      )}

      {error && <div className="source-task-error mt-3" role="alert"><TriangleAlert size={16} />{error}</div>}
    </NexilumeDialog>
  );
}
