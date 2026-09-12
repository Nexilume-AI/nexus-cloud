import { toast } from "sonner";

export function ProviderFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-l border-border px-3 py-2">
      <dt className="font-mono text-[11px] uppercase tracking-wider text-muted">
        {label}
      </dt>
      <dd className="mt-1 break-words text-sm font-medium">
        {value || "Not available"}
      </dd>
    </div>
  );
}

import type { ProviderConnection } from "../lib/types";

export function DialogActions({
  onClose,
  onSubmit,
  label,
  disabled,
}: {
  onClose: () => void;
  onSubmit: () => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <div className="flex justify-end gap-2">
      <button className="btn" onClick={onClose}>
        Cancel
      </button>
      <button
        className="btn btn-primary"
        disabled={disabled}
        onClick={onSubmit}
      >
        {label}
      </button>
    </div>
  );
}

export function providerRuntimeId(provider: ProviderConnection): string {
  return provider.technical_details?.runtime_id ?? "";
}

export function errorToast(fallback: string) {
  return (error: unknown) =>
    toast.error(error instanceof Error ? error.message : fallback);
}
