import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ApiContext } from "../lib/api";
import { formatDate } from "../lib/format";

const errors: Record<string, string> = {
  SOURCE_NOT_READY: "The source did not pass import checks. Complete the run and review scan, consent, license and quota before retrying.",
  ACCESS_REVOKED_OR_SOURCE_UNAVAILABLE: "Access changed or the source is no longer available.",
  WORKER_RETRY_LIMIT: "Worker recovery reached its retry limit. Review the source and start a new import.",
  IMPORT_FAILED: "Import failed. Check storage and Worker health, then retry. No file was committed by this attempt.",
};

export function DatasetPageControls({ total, next, loading, previous, onFirst, onNext }: {
  total: number; next?: string | null; loading: boolean; previous: boolean;
  onFirst: () => void; onNext: (cursor: string) => void;
}) {
  return <nav aria-label="Results pagination" className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
    <span className="mr-auto text-sm text-muted">{total.toLocaleString()} records · server paginated</span>
    <button className="btn min-h-11" disabled={!previous || loading} onClick={onFirst}>First page</button>
    <button className="btn min-h-11" disabled={!next || loading} onClick={() => next && onNext(next)}>Next page</button>
  </nav>;
}

export function DatasetImports({ ctx, datasetId }: { ctx: ApiContext; datasetId: string }) {
  const [cursor, setCursor] = useState("");
  const client = useQueryClient();
  const previousStates = useRef(new Map<string, string>());
  const jobs = useQuery({
    queryKey: ["dataset-imports", ctx, datasetId, cursor],
    queryFn: () => api.datasetImports(ctx, datasetId, cursor),
    refetchInterval: 3000,
    refetchIntervalInBackground: false,
  });
  useEffect(() => {
    const rows = jobs.data?.items ?? [];
    let completed = false;
    for (const row of rows) {
      if (row.state === "completed" && previousStates.current.get(row.id) !== "completed") completed = true;
      previousStates.current.set(row.id, row.state);
    }
    if (completed) {
      for (const key of ["datasets", "dataset-pull", "dataset-detail"]) void client.invalidateQueries({ queryKey: [key] });
    }
  }, [jobs.data, client]);
  const action = useMutation({
    mutationFn: ({ id, type }: { id: string; type: "cancel" | "retry" }) => api.changeDatasetImport(ctx, datasetId, id, type),
    onSuccess: () => client.invalidateQueries({ queryKey: ["dataset-imports"] }),
  });
  return <section aria-label="Import activity" className="grid gap-3 border-y border-line py-4">
    <div><h3 className="font-semibold text-ink">Import activity</h3>
      <p className="text-sm text-muted">Imports continue in the background. Refreshing this page does not cancel them.</p></div>
    {jobs.isPending && <p role="status">Loading imports…</p>}
    {jobs.isError && <div role="alert">Import activity unavailable. <button className="btn min-h-11" onClick={() => void jobs.refetch()}>Retry</button></div>}
    {action.isError && <p role="alert">{action.error.message}</p>}
    {!jobs.isPending && !jobs.isError && !jobs.data?.items.length && <p className="text-sm text-muted">No imports submitted by you for this collection.</p>}
    <ul className="grid gap-3" aria-label="Import jobs">
      {(jobs.data?.items ?? []).map(job => <li key={job.id} className="border-l-2 border-line pl-3">
        <div className="flex flex-wrap items-center gap-2"><strong className="capitalize">{job.kind} · {job.state}</strong>
          <span className="text-sm text-muted">{formatDate(job.created_at)}</span>
          {["queued", "running"].includes(job.state) && <button className="btn min-h-11 ml-auto" disabled={action.isPending} onClick={() => action.mutate({ id: job.id, type: "cancel" })}>Cancel import</button>}
          {job.can_retry && <button className="btn min-h-11 ml-auto" disabled={action.isPending} onClick={() => action.mutate({ id: job.id, type: "retry" })}>Retry import</button>}
        </div>
        {job.state === "queued" && <p className="text-sm">Waiting for an import Worker.</p>}
        {job.state === "running" && <div className="grid gap-1"><span className="text-sm">{job.stage} · {(job.bytes_processed / 1048576).toFixed(1)} MiB</span>
          <progress aria-label={`${job.kind} import progress`} className="w-full" max={job.total_bytes || undefined} value={job.total_bytes ? job.bytes_processed : undefined} /></div>}
        {job.error_code && <p className="text-sm text-amber-900">{errors[job.error_code] || "Import could not finish."}</p>}
        <span className="break-all font-mono text-xs text-muted">{job.id}</span>
      </li>)}
    </ul>
    {!!jobs.data?.total && <DatasetPageControls total={jobs.data.total} next={jobs.data.next_cursor} loading={jobs.isFetching} previous={!!cursor} onFirst={() => setCursor("")} onNext={setCursor} />}
  </section>;
}
