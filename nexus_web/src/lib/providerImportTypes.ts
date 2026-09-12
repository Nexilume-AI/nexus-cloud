export type ProviderImportRow = {
  sheet: string; line: number; api_ref: string; name: string;
  status: "pending" | "invalid" | "skipped" | "created" | "updated" | "failed";
  action?: "create" | "update"; message: string; connection_id?: string;
};
export type ProviderImportBatch = {
  id: string; status: string; duplicate_mode: "skip" | "update"; expires_at: string;
  results: ProviderImportRow[]; remaining: number; failed: number; invalid: number;
};
