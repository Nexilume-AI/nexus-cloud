import { api, type ApiContext } from "./api";
import type { Deployment } from "./types";

export type SourceSelection = {
  runtimeId: string;
  offers: Array<{ id: string; canonicalModelKey: string }>;
  targets: Record<string, string>;
  newPoolVisibility?: "private" | "project";
};
export type SourceCreator = (ctx: ApiContext, selection: SourceSelection) => Promise<Deployment[]>;

/** Shared single-model Source/Pool mapping. Prefix is presentation, not authority. */
export function buildModelSourceItems(selection: SourceSelection, prefix: string) {
  return selection.offers.map(offer => ({
    model_offer_id: offer.id,
    source_id: `${prefix}-${selection.runtimeId.slice(0, 8)}-${offer.canonicalModelKey}`
      .replace(/[^a-zA-Z0-9_-]/g, "-").toLowerCase(),
    visibility: "private",
    ...(selection.targets[offer.id]?.startsWith("pool:")
      ? { model_group_id: selection.targets[offer.id].slice(5) }
      : { new_pool: { name: offer.canonicalModelKey, visibility: selection.newPoolVisibility ?? "project" } }),
  }));
}

export const createRuntimeSources: SourceCreator = (ctx, selection) => api.createModelSourcesBatch(ctx, {
  origin: { type: "provider_runtime", provider_runtime_id: selection.runtimeId },
  sources: buildModelSourceItems(selection, "runtime"),
});
