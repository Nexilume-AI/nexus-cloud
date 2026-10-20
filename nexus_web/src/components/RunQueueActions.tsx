import { t, useLocale, getLocale } from "../localization";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import type { AgentRunFollowUp, PrivateAgentRunDisplay } from "../lib/types";
import { NexilumeDialog } from "./NexilumeControls";

export function RunQueueActions({ run, item, token, onRemove, removing, onCorrect }: {
  run: PrivateAgentRunDisplay; item: AgentRunFollowUp; token: string; onRemove: () => void; removing: boolean; onCorrect?: () => void;
}) {
  useLocale();
  const { apiContext } = useAuth();
  const queries = useQueryClient();
  const [editor, setEditor] = useState<{ text: string; revision: string } | null>(null);
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
      setEditor(null);
      void queries.invalidateQueries({ queryKey: ["private-agent-run", apiContext, run.id] });
    },
    onError: () => { void queries.invalidateQueries({ queryKey: ["private-agent-run", apiContext, run.id] }); },
  });
  function move(offset: number) {
    const ids = queue.map(row => row.id);
    [ids[index], ids[index + offset]] = [ids[index + offset], ids[index]];
    change.mutate({ ids, expected_revision: revision });
  }
  const invalid = !editor?.text.trim() || Array.from(editor.text.trim()).length > 8000;
  const changed = Boolean(editor && editor.revision !== revision);
  const received = steer && item.status === "received";
  if (!["pending", "blocked"].includes(item.status) && !editor && !received) return null;
  return <div className="shrink-0 text-xs">
    {received ? <div className="max-w-44 text-muted"><p>{t("Already received. Editing or removing cannot undo it.")}</p>{onCorrect ? <button type="button" className="min-h-11 underline" onClick={onCorrect}>{t("Draft a correction")}</button> : null}</div> : null}
    {["pending", "blocked"].includes(item.status) ? <select aria-label={t(steer ? "Actions for current-turn instruction: {{0}}" : "Actions for queued message: {{0}}", { 0: item.content })} value="" disabled={change.isPending || removing} className="min-h-11 max-w-32 rounded-md border border-black/15 bg-white px-2" onChange={event => {
      change.reset();
      if (event.target.value === "edit") setEditor({ text: item.content, revision });
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
    {change.isError && !editor ? <p role="alert" className="max-w-44 text-red-800">{t("Change not confirmed. Review the refreshed queue before trying again.")}</p> : null}
    <NexilumeDialog open={Boolean(editor)} onClose={() => setEditor(null)} busy={change.isPending} title={t(steer ? "Edit current-turn instruction" : "Edit queued message")} description={t(steer ? "Only instructions not yet received by the Agent can be changed. This does not start a new turn." : "Only messages that have not started can be changed. Nothing is sent as a new turn.")} footer={<>
      <button type="button" className="btn min-h-11" disabled={change.isPending} onClick={() => setEditor(null)}>{t("Cancel")}</button>
      <button type="button" className="btn btn-primary min-h-11 min-w-32" disabled={change.isPending || invalid || changed || !editable} onClick={() => editor && change.mutate({ content: editor.text.trim(), expected_revision: editor.revision })}>{change.isPending ? t("Saving…") : t("Save changes")}</button>
    </>}>
      <label className="grid gap-2 text-sm">{t(steer ? "Current-turn instruction" : "Queued message")}<textarea rows={6} value={editor?.text || ""} className="w-full resize-y rounded-md border border-black/20 p-3" onChange={event => setEditor(current => current ? { ...current, text: event.target.value } : null)} /></label>
      <p className="mt-2 text-xs text-muted">{Array.from(editor?.text.trim() || "").length.toLocaleString(getLocale())}{" "}{t("/ 8,000 characters")}</p>
      {changed || !editable || change.isError ? <div role="alert" className="mt-3 text-sm text-amber-800">
        <p>{!editable ? t("This message can no longer be edited. Your changes are kept here for review.") : t("The queue changed or saving could not be confirmed. Your changes are kept; review the current message before saving again.")}</p>
        <p className="mt-2 whitespace-pre-wrap break-words">{t("Current message:")}{" "}{item.content}</p>
        {editable ? <button type="button" className="min-h-11 underline" disabled={change.isPending} onClick={() => { change.reset(); setEditor(current => current ? { ...current, revision } : null); }}>{t("I reviewed the current queue")}</button> : null}
      </div> : null}
    </NexilumeDialog>
  </div>;
}
