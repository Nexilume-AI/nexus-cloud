import { Loader2 } from "lucide-react";

export function ReadOnlyValue({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-semibold uppercase tracking-wide text-muted">
        {label}
      </dt>
      <dd
        className={`mt-1 break-words text-ink ${mono ? "font-mono text-xs" : ""}`}
      >
        {value || "—"}
      </dd>
    </div>
  );
}

export function LoadingState({
  label,
  compact = false,
}: {
  label: string;
  compact?: boolean;
}) {
  return (
    <div
      className={`flex items-center gap-2 text-sm text-muted ${compact ? "py-4" : "panel p-6"}`}
      role="status"
    >
      <Loader2 size={16} className="animate-spin" />
      {label}
    </div>
  );
}

export function ErrorState({
  title,
  error,
  onRetry,
}: {
  title: string;
  error: Error;
  onRetry: () => void;
}) {
  return (
    <div
      className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-950"
      role="alert"
    >
      <div className="font-semibold">{title}</div>
      <div className="mt-1">{error.message}</div>
      <button className="btn mt-3 min-h-10" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

export function InlineIssue({
  title,
  error,
  onRetry,
  detail,
}: {
  title: string;
  error: Error;
  onRetry: () => void;
  detail?: string;
}) {
  return (
    <div
      className="flex flex-col gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950 sm:flex-row sm:items-center sm:justify-between"
      role="status"
    >
      <div>
        <div className="font-semibold">{title}</div>
        <div className="mt-1">{detail || error.message}</div>
      </div>
      <button className="btn min-h-10 shrink-0" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}
