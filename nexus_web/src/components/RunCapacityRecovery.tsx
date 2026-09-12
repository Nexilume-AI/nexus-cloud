import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { createPortal } from "react-dom";
import { api, ApiError, type ApiContext } from "../lib/api";
import { NexilumeDialog } from "./NexilumeControls";
import { useApplicationDistribution } from "../app/distribution";
import type { RunCapacityCopy } from "../app/runPresentation";

const personalCapacityCopy: RunCapacityCopy = {
  usageSuffix: "personal instance slots in use.",
  reviewDescription: "Only your active Runs in this personal instance are included. The current Run, history and files are kept.",
  reviewedScope: "other active Runs in this review belong to this personal instance.",
  emptyDescription: "No other Runs can be stopped here. The current Run or pending reservations may still occupy capacity. Wait for work to finish, or ask the operator to review local limits.",
};

export function isConcurrentRunQuota(error: unknown) {
  return error instanceof ApiError && (error.code === "PERSONAL_RUN_CAPACITY_EXCEEDED"
    || (error.code === "PLAN_QUOTA_EXCEEDED" && /Concurrent Runs quota exceeded/i.test(error.message)));
}

export function RunCapacityRecovery({ context, agentId, runId, message, onChanged }: {
  context: ApiContext; agentId: string; runId: string; message: string; onChanged: () => void;
}) {
  const presentation = useApplicationDistribution().runPresentation;
  const copy = presentation ? presentation.capacityRecovery : personalCapacityCopy;
  if (!copy || ![copy.usageSuffix,copy.reviewDescription,copy.reviewedScope,copy.emptyDescription].every(value => typeof value === 'string' && value.trim())) {
    throw new Error('Run capacity presentation is incomplete.');
  }
  const [open, setOpen] = useState(false);
  const preview = useQuery({
    queryKey: ["private-run-capacity", context, agentId, runId],
    queryFn: () => api.privateRunCapacityRecovery(context, agentId, runId),
    // Recheck capacity without resubmitting the user's message or cancelling.
    refetchInterval: 5000,
    refetchOnWindowFocus: "always",
  });
  const [reviewToken, setReviewToken] = useState("");
  const [reviewed, setReviewed] = useState<Awaited<ReturnType<typeof api.privateRunCapacityRecovery>> | null>(null);
  const stop = useMutation({
    mutationFn: () => api.stopOtherPrivateRuns(context, agentId, runId, reviewToken),
    onSuccess: () => { setReviewToken(""); onChanged(); void preview.refetch(); },
  });
  const free = preview.data?.capacity.remaining === null || Number(preview.data?.capacity.remaining ?? 0) > 0;
  const buttonClass = "min-h-11 rounded-md border border-black/15 bg-white px-3 text-sm text-[#34322d] disabled:opacity-50";
  async function review() {
    setOpen(true); setReviewToken(""); setReviewed(null); stop.reset();
    const result = await preview.refetch();
    if (result.data && !result.isError) { setReviewToken(result.data.preview_token); setReviewed(result.data); }
  }
  return <>
    <div role="alert" className="mx-3 mb-2 flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-[#535350]">
      <div className="min-w-0 flex-1"><p className="font-semibold">{free ? "Run capacity available" : "Concurrent Run limit reached"}</p>
        <p>{preview.data ? `${preview.data.capacity.used} / ${preview.data.capacity.limit ?? "Unlimited"} ${copy.usageSuffix}` : message}</p>
        <p>{free ? "Your draft is kept. Send it when you are ready; it was not retried automatically." : "Stop your other active Runs to free capacity. Deleting completed history does not free Run slots."}</p>
      </div>
      <button type="button" className={buttonClass} onClick={() => void review()}>Manage active runs</button>
    </div>
    {createPortal(<NexilumeDialog open={open} busy={stop.isPending} onClose={() => { if (!stop.isPending) setOpen(false); }} title="Free Run capacity" description={copy.reviewDescription}>
      <div className="grid gap-3 text-sm text-[#535350]">
        {preview.isLoading ? <p role="status">Loading active Runs…</p> : null}
        {preview.isError ? <p role="alert">Unable to load active Runs. Nothing was stopped.<button className={`${buttonClass} ml-2`} onClick={() => void review()}>Retry review</button></p> : null}
        {reviewed ? <>
          <p>{reviewed.eligible_count} {copy.reviewedScope}{reviewed.has_more ? ` The next batch contains ${reviewed.batch_count} Runs.` : ""}</p>
          {reviewed.runs.length ? <ul aria-label="Runs to stop" className="max-h-52 overflow-y-auto rounded-md border border-black/10 divide-y divide-black/10">
            {reviewed.runs.map(item => <li key={item.id} className="break-words p-2"><span className="font-medium">{item.title}</span><p className="text-xs">{item.agent_name}{item.hidden ? " · Previously removed from history" : ""}</p></li>)}
          </ul> : <p>{copy.emptyDescription}</p>}
          <p>Stopping requests cancellation. Running work may take time to acknowledge; capacity is only released once the Run actually ends.</p>
        </> : null}
        {stop.isError ? <p role="alert">{stop.error instanceof Error ? stop.error.message : "Stopping failed."} Some requests may have completed. Review the current state before retrying.<button className={`${buttonClass} ml-2`} onClick={() => void review()}>Review again</button></p> : null}
        {stop.data ? <p role="status">{stop.data.stopped} stopped · {stop.data.pending} awaiting cancellation · {stop.data.failed} failed · {stop.data.skipped} already ended or changed. History and files were not deleted.</p> : null}
        <div className="flex flex-wrap justify-end gap-2">
          <button className={buttonClass} disabled={stop.isPending} onClick={() => setOpen(false)}>Close</button>
          {stop.data ? <button className={buttonClass} onClick={() => void review()}>Review remaining runs</button> : null}
          <button className="min-h-11 min-w-40 rounded-md bg-[#9b2c2c] px-3 text-sm font-semibold text-white disabled:opacity-50" disabled={stop.isPending || stop.isError || preview.isError || !reviewToken || !reviewed?.batch_count} onClick={() => stop.mutate()}>{stop.isPending ? "Requesting stop…" : "Stop other runs"}</button>
        </div>
      </div>
    </NexilumeDialog>, document.body)}
  </>;
}
