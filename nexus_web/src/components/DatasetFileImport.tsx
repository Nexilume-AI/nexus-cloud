import { useState } from "react";
import { Loader2, Upload } from "lucide-react";
import { api, type ApiContext } from "../lib/api";
import type { DatasetCapabilities } from "../lib/types";

type Item = {
  file: File;
  key: string;
  state: "pending" | "importing" | "saved" | "failed";
  message: string;
};

export function DatasetFileImport({
  context,
  datasetId,
  policy,
  onComplete,
  onBusy,
}: {
  context: ApiContext;
  datasetId: string;
  policy: DatasetCapabilities["file_import"];
  onComplete: () => Promise<void>;
  onBusy: (busy: boolean) => void;
}) {
  const [items, setItems] = useState<Item[]>([]);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const update = (key: string, changes: Partial<Item>) =>
    setItems((rows) =>
      rows.map((row) => (row.key === key ? { ...row, ...changes } : row)),
    );
  async function submit() {
    if (!consent || busy || !policy) return;
    setBusy(true);
    onBusy(true);
    setError("");
    try {
      for (const item of items.filter((row) => row.state !== "saved")) {
        if (item.file.size <= 0 || item.file.size > policy.max_bytes) {
          update(item.key, {
            state: "failed",
            message: "File is empty or exceeds the import size limit.",
          });
          continue;
        }
        update(item.key, {
          state: "importing",
          message: "Uploading and scanning…",
        });
        try {
          await api.pushDatasetFile(context, datasetId, item.file, {
            rightsConfirmed: consent,
            requestKey: item.key,
          });
          update(item.key, {
            state: "saved",
            message: "Imported · checks passed",
          });
        } catch (reason) {
          update(item.key, {
            state: "failed",
            message:
              reason instanceof Error
                ? reason.message
                : "Import failed. Retry this file.",
          });
        }
      }
      await onComplete();
    } catch {
      setError(
        "Imports finished, but the collection could not refresh. Reload the collection to see saved files.",
      );
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  if (!policy)
    return (
      <p role="status">
        File import is unavailable. Refresh or upgrade the Cloud server.
      </p>
    );
  const pending = items.filter((row) => row.state !== "saved").length;
  return (
    <section aria-label="Import files" className="grid min-w-0 gap-4">
      <p className="text-sm text-muted">
        Import images or text files into this collection. Existing permissions,
        storage limits and release rules still apply.
      </p>
      <label className="grid gap-2 text-sm font-medium">
        Choose files
        <input
          className="input min-h-11 w-full min-w-0"
          type="file"
          multiple
          accept={policy.extensions.join(",")}
          disabled={busy}
          onChange={(event) => {
            setItems(
              Array.from(event.target.files ?? []).map((file) => ({
                file,
                key: crypto.randomUUID(),
                state: "pending",
                message: "Ready to import",
              })),
            );
            setConsent(false);
            setError("");
          }}
        />
      </label>
      <p className="text-xs text-muted">
        {policy.extensions.join(", ")} · Up to{" "}
        {Math.floor(policy.max_bytes / 1024 ** 2)} MiB per file; images up to{" "}
        {Math.floor(policy.image_max_bytes / 1024 ** 2)} MiB.
      </p>
      <p className="text-xs text-muted">
        {policy.scan_description} PDF, Office, archives and executable formats
        require a dedicated scanner and are not accepted yet.
      </p>
      <ul className="grid gap-2" aria-live="polite">
        {items.map((item) => (
          <li
            key={item.key}
            className="min-w-0 rounded-lg border border-line p-3"
          >
            <p className="break-all text-sm font-medium">{item.file.name}</p>
            <p
              className={`mt-1 break-words text-sm ${item.state === "failed" ? "text-red-700" : "text-muted"}`}
              role={item.state === "failed" ? "alert" : undefined}
            >
              {item.message}
            </p>
          </li>
        ))}
      </ul>
      <label className="flex min-h-11 items-start gap-3 text-sm">
        <input
          type="checkbox"
          className="mt-1"
          checked={consent}
          disabled={busy}
          onChange={(event) => setConsent(event.target.checked)}
        />
        <span>
          I have permission to store these files and share them with this
          collection's authorized users. Publication remains a separate action.
        </span>
      </label>
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted">
          {items.filter((row) => row.state === "saved").length} imported ·{" "}
          {pending} remaining
        </p>
        <button
          type="button"
          className="btn btn-primary min-h-11 min-w-40"
          disabled={busy || !consent || !pending}
          onClick={() => void submit()}
        >
          {busy ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Upload size={16} />
          )}
          {busy
            ? "Importing…"
            : items.some((row) => row.state === "failed")
              ? "Retry remaining files"
              : "Import files"}
        </button>
      </div>
    </section>
  );
}
