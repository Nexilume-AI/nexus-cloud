import type { Dataset } from "../lib/types";
import { useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Download, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import type { useAuth } from "../app/AuthContext";
import { api, type ApiContext } from "../lib/api";
import { DatasetImagePreview } from "./DatasetImagePreview";
import { StatusBadge } from "./Badge";

export type DatasetDownloadFile = { file_name: string; content_type?: string; size_bytes: number; status: string; download_url: string };

export function shortList(values: string[] | undefined, fallback: string) {
  if (!values?.length) return fallback;
  const display = values.slice(0, 2).map(humanizeLifecycle).join(", ");
  return values.length > 2 ? `${display} +${values.length - 2}` : display;
}

export function humanizeLifecycle(status: string | undefined) {
  const labels: Record<string, string> = {
    draft: "Draft",
    assets_added: "Assets added",
    commercial_setup: "Commercial setup",
    ready_to_publish: "Ready to publish",
    published: "Published",
  };
  if (!status) return "Not reported";
  return (
    labels[status] ??
    status
      .replaceAll("_", " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase())
  );
}

export function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line px-3 py-2">
      <div className="label">{label}</div>
      <div className="mt-1 break-all text-sm font-medium text-ink">{value}</div>
    </div>
  );
}

export function formatBytes(value: number | string | null | undefined) {
  const bytes = Number(value ?? 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export async function downloadApiFile(
  path: string,
  fileName: string,
  ctx: ReturnType<typeof useAuth>["apiContext"],
) {
  try {
    const prepared = await api.prepareDatasetDownload(ctx, path);
    // The HttpOnly, path-scoped download cookie lets the browser stream directly
    // to disk. Never put bearer tokens in URLs or materialize the file as a Blob.
    const anchor = document.createElement("a");
    anchor.href = prepared.url;
    anchor.download = prepared.file_name || fileName || "download";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    toast.success("Download started. Progress and resume are available in your browser's downloads.");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "Could not start download. Please retry.");
  }
}

export function useDatasetDownloadColumns(apiContext: ApiContext) {
  return useMemo<
    Array<ColumnDef<DatasetDownloadFile, unknown>>
  >(
    () => [
      {
        header: "File",
        cell: ({ row }) => (
          <div className="font-medium text-ink">{row.original.file_name}</div>
        ),
      },
      { header: "Type", cell: ({ row }) => row.original.content_type || "-" },
      {
        header: "Size",
        cell: ({ row }) => formatBytes(row.original.size_bytes),
      },
      {
        header: "Status",
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
      {
        id: "download",
        header: "Actions",
        cell: ({ row }) => (
          <div className="flex flex-wrap gap-2">
          <DatasetImagePreview context={apiContext} file={row.original} />
          <button
            className="btn min-h-11 px-2"
            aria-label={`Download ${row.original.file_name}`}
            onClick={() =>
              void downloadApiFile(
                row.original.download_url,
                row.original.file_name,
                apiContext,
              )
            }
          >
            <Download size={14} aria-hidden="true" />
          </button>
          </div>
        ),
      },
    ],
    [apiContext],
  );
}

export function datasetOwnershipLabel(dataset: Dataset) {
  if (dataset.ownership?.label) return dataset.ownership.label;
  return dataset.project_id ? "Project resource" : "Organization shared";
}

export function LoadingState({ label }: { label: string }) {
  return (
    <div className="flex min-h-32 items-center justify-center gap-2 rounded-lg border border-dashed border-line text-sm text-muted">
      <Loader2 size={18} className="animate-spin" />
      {label}
    </div>
  );
}

export function ErrorState({
  label,
  onRetry,
}: {
  label: string;
  onRetry: () => void;
}) {
  return (
    <div className="grid min-h-32 place-items-center gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-center">
      <div className="text-sm text-red-900">{label}</div>
      <button type="button" className="btn min-h-11" onClick={onRetry}>
        <RefreshCw size={15} />
        Try again
      </button>
    </div>
  );
}
