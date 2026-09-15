import { t, useLocale } from "../localization";
import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Boxes, Trash2 } from "lucide-react";
import { api, type ApiContext } from "../lib/api";
import type { AgentRuntimeImage } from "../lib/types";
import { compactId, formatDate } from "../lib/format";
import { NexilumeDialog } from "./NexilumeControls";
import "./agent-runtime-images.css";

const reasons: Record<string, string> = {
  DEPLOYED: "Used by a deployment",
  DEPLOYING: "Deployment in progress",
  RECOVERY_REFERENCE: "Required for runtime recovery. Stop the runtime first.",
  CONTAINER_REFERENCE: "A container still references this image. Stop the runtime first.",
  RUNTIME_OPERATION: "Runtime operation in progress",
  RUN_REFERENCE: "Required by an unfinished Run",
  ROLLBACK_REFERENCE: "Retained for deployment rollback",
  RETIREMENT_PENDING: "Waiting for the previous container to retire",
  DEPLOYMENT_QUEUED: "Deployment is queued",
  REMOVED: "Image removed",
};

export function AgentRuntimeImages({ agentId, apiContext, images, currentImageId, canManage, onChanged }: {
  agentId: string; apiContext: ApiContext; images: AgentRuntimeImage[]; currentImageId?: string | null;
  canManage: boolean; onChanged: () => Promise<unknown>;
}) {
  useLocale();
  const [selected, setSelected] = useState<AgentRuntimeImage | null>(null);
  const [notice, setNotice] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const queries = useQueryClient();
  const remove = useMutation({
    mutationFn: (image: AgentRuntimeImage) => api.deleteAgentRuntimeImage(apiContext, agentId, image.id),
    onSuccess: async () => {
      setSelected(null);
      setNotice("Image removed from this Agent. Deployment and build history are unchanged.");
      await Promise.all([onChanged(), queries.invalidateQueries({ queryKey: ["agent-python-builds"] })]);
      window.requestAnimationFrame(() => listRef.current?.focus());
    },
  });
  const choose = useMutation({
    mutationFn: (image: AgentRuntimeImage) => api.setCurrentAgentRuntimeImage(apiContext, agentId, image.id),
    onSuccess: async () => { setNotice("Default image selected. The running deployment has not changed."); await onChanged(); },
  });
  const busy = remove.isPending || choose.isPending;
  return <div className="runtime-image-library" ref={listRef} tabIndex={-1} aria-label={t("Runtime image library")}>
    <p className="text-sm text-muted">{t("Default selection is used for the next deployment. Deployed shows what is running now.")}</p>
    {notice && <p role="status">{notice}</p>}
    {choose.isError && <p role="alert">{choose.error.message}</p>}
    <div className="agent-object-list">{images.map(image => {
      const usage = image.usage;
      const isDefault = usage?.is_default ?? image.id === currentImageId;
      const deployed = usage?.deployed_environments ?? [];
      return <div className="agent-object-row runtime-image-row" key={image.id}>
        <span className="agent-runtime-capsule" aria-hidden="true"><Boxes size={15} /></span>
        <div className="runtime-image-copy">
          <strong>{image.image_ref}</strong>
          <small>{image.version || t("Unversioned")} · {compactId(image.image_digest || image.id)} · {formatDate(image.created_at)}</small>
          <div className="runtime-image-status">
            {deployed.length > 0 && <span className="runtime-image-deployed">{t("Deployed ·")}{" "}{deployed.join(", ")}</span>}
            {isDefault && <span>{t("Default selection")}</span>}
            {usage?.can_delete && <span>{t("Not deployed")}</span>}
            {!usage && <span>{t("Usage unavailable · Refresh to check")}</span>}
          </div>
          {usage && !usage.can_delete && <small>{usage.blocking_reasons.map(reason => reasons[reason] || "Image is still referenced").join(" · ")}</small>}
        </div>
        {canManage && <div className="runtime-image-actions">
          <button className="btn" disabled={busy || isDefault || image.status !== "active"}
            onClick={() => choose.mutate(image)}>{t("Set default")}</button>
          <button className="btn" aria-label={t("Delete image {{0}}", { 0: image.image_ref })} disabled={busy || !usage?.can_delete}
            onClick={() => { remove.reset(); setNotice(""); setSelected(image); }}><Trash2 size={15} />{t("Delete image")}</button>
        </div>}
      </div>;
    })}</div>
    <NexilumeDialog open={Boolean(selected)} title={t("Delete unused image?")} busy={remove.isPending}
      description={t("Remove this image from the Agent’s available versions. This does not stop or redeploy the Agent.")}
      onClose={() => setSelected(null)} footer={<>
        <button className="btn" disabled={remove.isPending} onClick={() => setSelected(null)}>{t("Cancel")}</button>
        <button className="btn btn-danger" disabled={remove.isPending} onClick={() => selected && remove.mutate(selected)}>{remove.isPending ? "Deleting…" : "Delete image"}</button>
      </>}>
      <p className="runtime-image-reference">{selected?.image_ref}</p>
      <p>{t("Build, deployment and audit history are retained. Registry images, stored archives and host Docker caches are not erased.")}</p>
      {(selected?.usage?.is_default ?? selected?.id === currentImageId) && <p>{t("The default selection will move to the existing production deployment, or be cleared if none exists. No new deployment will start.")}</p>}
      {remove.isError && <p role="alert">{remove.error.message}</p>}
    </NexilumeDialog>
  </div>;
}
