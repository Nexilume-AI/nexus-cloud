import type { ReactNode } from "react";

import type { Dataset } from "../lib/types";
import type { DatasetPublicationPolicy } from "../app/resourcePublishing";

export type CollectionStage = "collect" | "govern" | "release" | "distribute";

export function DataStatus({
  label,
  value,
  detail,
  tone = "neutral"
}: {
  label: string;
  value: string;
  detail: string;
  tone?: "neutral" | "healthy" | "warning";
}) {
  return <div className={`data-status data-status--${tone}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

export function DataAssetShape({ state }: { state?: string }) {
  return <span className="data-asset-shape" data-state={state || "unknown"} aria-hidden="true"><i /><i /><i /><b /></span>;
}

export function StageHeading({
  eyebrow,
  title,
  description,
  action
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return <header className="data-stage-heading"><div><span>{eyebrow}</span><h3>{title}</h3><p>{description}</p></div>{action}</header>;
}

export function AcquisitionLineage({
  publisher,
  release,
  onOpen
}: {
  publisher: string;
  release: string;
  onOpen?: () => void;
}) {
  return (
    <div className="data-acquisition-lineage" aria-label="Acquisition lineage">
      <span><small>PUBLISHER</small><strong>{publisher}</strong></span>
      <i aria-hidden="true" />
      <span><small>IMMUTABLE RELEASE</small><strong>{release}</strong></span>
      <i aria-hidden="true" />
      <span className="is-hit"><small>DESTINATION</small><strong>Acquired library</strong></span>
      {onOpen && <button type="button" className="btn" onClick={onOpen}>Open in Data Assets</button>}
    </div>
  );
}

export function collectionStageForDataset(dataset: Dataset, policy?: DatasetPublicationPolicy): CollectionStage {
  if (dataset.file_count === 0) return "collect";
  if (!dataset.current_version) return "govern";
  return policy?.releasedStage?.(dataset) ?? "release";
}
