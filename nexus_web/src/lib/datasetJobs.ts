export type DatasetImportJob = {
  id: string;
  dataset_id: string;
  kind: "trace" | "memory" | "artifact";
  state: "queued" | "running" | "completed" | "failed" | "cancelled";
  stage: string;
  bytes_processed: number;
  total_bytes: number | null;
  attempts: number;
  error_code: string;
  can_retry: boolean;
  file_id: string | null;
  created_at: string;
  updated_at: string;
};

export type DatasetPage<T> = {
  items: T[];
  total: number;
  next_cursor: string | null;
  summary: Record<string, number>;
  warning_code?: string;
  unavailable_entries?: number;
};
