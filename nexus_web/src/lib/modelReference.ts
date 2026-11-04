export type ModelReferenceIdentity = { provider: string; model: string };
export type ReferenceCapacity = { context_window?: number | null; max_input_tokens?: number | null; max_output_tokens?: number | null };
export type SourceModelReference = {
  mapping: ModelReferenceIdentity | null;
  effective: ModelReferenceIdentity;
  matched: boolean;
  capacity: ReferenceCapacity;
  updated_at: string | null;
  can_edit: boolean;
};
export type ModelReferenceCatalog = {
  results: { id: string; capacity?: ReferenceCapacity; model_count?: number }[];
  total: number; next_offset: number | null; available: boolean; updated_at: string | null;
};
