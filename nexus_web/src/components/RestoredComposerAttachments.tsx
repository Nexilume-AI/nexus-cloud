import { useQueries } from "@tanstack/react-query";
import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import type { DraftAsset } from "../lib/composerDrafts";
import type { AgentInteractionTool } from "../lib/types";
import { AttachmentImagePreview } from "./RunImageAttachments";

function RestoredImagePreview({ assetId, name }: { assetId: string; name: string }) {
  const { apiContext } = useAuth();
  const preview = useQueries({ queries: [{
    queryKey: ["composer-image-preview", apiContext, assetId],
    queryFn: () => api.mediaSignedUrl(apiContext, assetId, { expires_in: 300 }),
    staleTime: 240_000, retry: false,
  }] })[0];
  return preview.data?.url ? <AttachmentImagePreview src={preview.data.url} name={name} />
    : <div aria-label={`Preview unavailable for ${name}`} className="flex h-14 w-14 shrink-0 items-center justify-center rounded-md bg-black/5 text-center text-[10px] text-muted">{preview.isError ? "Preview unavailable" : "Loading preview"}</div>;
}

export function useRestoredAttachments(key: string, assets: DraftAsset[], tool: AgentInteractionTool | null, run?: { id: string; token: string }) {
  const { apiContext } = useAuth();
  const checks = useQueries({ queries: assets.map(asset => ({
    queryKey: ["composer-asset-check", apiContext, key, asset.kind, asset.id, asset.kind.startsWith("run_") ? run?.token : ""],
    queryFn: async () => {
      if (asset.kind === "run_file" || asset.kind === "run_image") {
        if (!run?.token || asset.runId !== run.id) return "This reference belongs to another Run or access is being renewed.";
        await api.runFileReference(apiContext, run.id, run.token, asset.kind === "run_image" ? "image" : "input", asset.id, true);
      } else if (asset.kind === "image") {
        const row = await api.mediaAsset(apiContext, asset.id);
        if (row.status !== "active" || (row.expires_at && Date.parse(row.expires_at) <= Date.now()) || !["chat_input", "agent_attachment"].includes(row.purpose)) return "Image expired or unavailable. Remove it and choose the image again.";
      } else {
        const row = await api.agentFileStatus(apiContext, asset.id);
        if (row.turn_index != null) return "This file was already sent. Remove it and attach a new copy.";
        if (row.state !== "ready") return "Upload is incomplete or verification failed. Remove it and choose the file again; no upload is restarted automatically.";
      }
      return "";
    }, retry: false, staleTime: 0, refetchInterval: 60_000,
  })) });
  const rows = assets.map((asset, index) => {
    const check = checks[index];
    const compatible = tool && (asset.kind === "image" || asset.kind === "run_image" || asset.kind === "audio" ? tool.input_modalities?.includes(asset.kind === "run_image" ? "image" : asset.kind) : tool.accepts_files);
    const reason = !compatible ? "This tool does not accept this attachment. Choose a compatible tool or remove it." : check.isError ? "Cannot verify access to this attachment. Check again or remove it." : check.data || "";
    return { asset, reason, checking: check.isPending, ready: compatible && !check.isPending && !check.isError && check.data === "", retry: () => void check.refetch() };
  });
  return { rows, blocked: rows.some(row => !row.ready) };
}

export function RestoredComposerAttachments({ state, onRemove, disabled }: {
  state: ReturnType<typeof useRestoredAttachments>; onRemove: (asset: DraftAsset) => void; disabled: boolean;
}) {
  if (!state.rows.length) return null;
  return <section aria-label="Restored attachments" className="mb-2 rounded-md border border-black/10 bg-white p-3 text-sm">
    <p className="mb-2 text-xs text-muted">Saved attachments · checked with Cloud before use</p>
    {state.rows.map(({ asset, reason, checking, ready, retry }) => <div key={`${asset.kind}:${asset.id}`} className="flex items-start justify-between gap-2 border-t border-black/5 py-2">
      {asset.kind === "image" && ready ? <RestoredImagePreview assetId={asset.id} name={asset.name} /> : null}
      <div className="min-w-0 flex-1"><p className="break-words">{asset.name}</p><p className="text-xs text-muted">{asset.kind === "computer" ? "Computer file snapshot" : asset.kind} · {Math.ceil(asset.size / 1024)} KiB</p>
        <p role="status" className={`mt-1 text-xs ${reason ? "text-amber-800" : "text-muted"}`}>{checking ? "Checking attachment…" : ready ? "Ready to send" : reason}</p></div>
      <div className="shrink-0"><select value="" aria-label={`Saved attachment actions: ${asset.name}`} disabled={disabled} className="min-h-11 max-w-28 rounded-md border border-black/15 bg-white px-2 text-xs" onChange={event => event.target.value === "remove" ? onRemove(asset) : retry()}><option value="">Actions</option><option value="check">Check again</option><option value="remove">Remove</option></select></div>
    </div>)}
  </section>;
}
