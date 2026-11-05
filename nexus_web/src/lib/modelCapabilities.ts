export type CapabilityOverride = {
  context_window?: number; max_input_tokens?: number; max_output_tokens?: number;
  supports_reasoning?: boolean; reasoning_effort_levels?: string[]; default_reasoning_effort?: string;
  supported_openai_params?: string[];
};
export type CapabilityInfo = {
  context_window: number | null; max_input_tokens: number | null; max_output_tokens: number | null;
  supports_reasoning: boolean | null; reasoning_effort_levels: string[] | null;
  default_reasoning_effort: string | null; supported_openai_params: string[] | null;
  reasoning?: { default_effort: string | null };
};
export type CapabilityField = { value: unknown; sources: string[]; updated_at: string | null; stale: boolean; upstream_confirmed: boolean };
export type ModelCapabilities = {
  override: CapabilityOverride | null; model_info: CapabilityInfo; inherited_model_info: CapabilityInfo;
  fields: Record<string, CapabilityField>; inherited_fields: Record<string, CapabilityField>; can_edit: boolean;
};

export function effortInput(value: string): string[] | undefined {
  const values = value.split(/[,\s]+/).filter(Boolean);
  return values.length ? [...new Set(values)] : undefined;
}
export function capabilityDraftError(draft: CapabilityOverride): string | null {
  const levels = draft.reasoning_effort_levels;
  if (levels && (levels.length > 64 || levels.some(value => !/^[A-Za-z0-9_-]{1,64}$/.test(value)))) return "Use short effort names separated by commas.";
  if (draft.default_reasoning_effort && levels && !levels.includes(draft.default_reasoning_effort)) return "The default must be one of the configured levels.";
  if (draft.supports_reasoning === false && (levels?.length || draft.default_reasoning_effort)) return "Disabled reasoning cannot advertise levels or a default.";
  for (const name of ["context_window", "max_input_tokens", "max_output_tokens"] as const) {
    const value = draft[name];
    if (value !== undefined && (!Number.isInteger(value) || value < 1 || value > 100_000_000)) return "Token limits must be positive whole numbers.";
  }
  return null;
}
