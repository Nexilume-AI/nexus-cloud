import { t, useLocale } from "../localization";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import type { AgentRunFollowUp, PrivateAgentRunDisplay } from "../lib/types";

export function RunQueueActions({ run, item, token, onRemove, removing, onCorrect, onEdit, editing }: {
  run: PrivateAgentRunDisplay; item: AgentRunFollowUp; token: string; onRemove: () => void; removing: boolean; onCorrect?: () => void; onEdit: (item: AgentRunFollowUp) => void; editing: boolean;
}) {
  useLocale();
  const { apiContext } = useAuth();
  const queries = useQueryClient();
  const queue = run.follow_up?.items.filter(row => row.mode === "queue" && row.status === "pending") || [];
  const index = queue.findIndex(row => row.id === item.id);
  const steer = item.mode === "steer";
  const revision = (steer ? item.edit_revision : run.follow_up?.queue_revision) || "";
  const editable = Boolean(item.editable && revision);
  const change = useMutation({
    mutationFn: (input: { content?: string; ids?: string[]; expected_revision: string }) =>
      api.editAgentRunQueue(apiContext, run.id, token, input, input.content === undefined ? undefined : item.id),
    onSuccess: data => {
      queries.setQueriesData<PrivateAgentRunDisplay>({ queryKey: ["private-agent-run", apiContext, run.id] }, current => current ? { ...current, follow_up: data } : current);
      void queries.invalidateQueries({ queryKey: ["private-agent-run", apiContext, run.id] });
    },
    onError: () => { void queries.invalidateQueries({ queryKey: ["private-agent-run", apiContext, run.id] }); },
  });
  function move(offset: number) {
    const ids = queue.map(row => row.id);
    [ids[index], ids[index + offset]] = [ids[index + offset], ids[index]];
    change.mutate({ ids, expected_revision: revision });
  }
  const received = steer && item.status === "received";
  if (!["pending", "blocked"].includes(item.status) && !received) return null;
  return <div className="shrink-0 text-xs">
    {received ? <div className="max-w-44 text-muted"><p>{t("Already received. Editing or removing cannot undo it.")}</p>{onCorrect ? <button type="button" className="min-h-11 underline" onClick={onCorrect}>{t("Draft a correction")}</button> : null}</div> : null}
    {["pending", "blocked"].includes(item.status) ? <select aria-label={t(steer ? "Actions for current-turn instruction: {{0}}" : "Actions for queued message: {{0}}", { 0: item.content })} value="" disabled={change.isPending || removing || editing} className="min-h-11 max-w-32 rounded-md border border-black/15 bg-white px-2" onChange={event => {
      change.reset();
      if (event.target.value === "edit") onEdit(item);
      else if (event.target.value === "up") move(-1);
      else if (event.target.value === "down") move(1);
      else if (event.target.value === "remove") onRemove();
    }}>
      <option value="">{change.isPending ? t("Saving…") : t("Actions")}</option>
      {editable ? <option value="edit">{t("Edit message")}</option> : null}
      {editable && !steer ? <option value="up" disabled={index <= 0 || queue.some(row => !row.editable)}>{t("Move earlier")}</option> : null}
      {editable && !steer ? <option value="down" disabled={index < 0 || index === queue.length - 1 || queue.some(row => !row.editable)}>{t("Move later")}</option> : null}
      <option value="remove">{t("Remove message")}</option>
    </select> : null}
    {change.isError ? <p role="alert" className="max-w-44 text-red-800">{t("Change not confirmed. Review the refreshed queue before trying again.")}</p> : null}

  </div>;
}
