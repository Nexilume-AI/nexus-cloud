import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import { t } from "../localization";
import type { AgentRunFollowUp, PrivateAgentRunDisplay } from "../lib/types";

export function useRunQueueEditor(run: PrivateAgentRunDisplay | undefined, token: string) {
  const { apiContext } = useAuth();
  const queries = useQueryClient();
  const [edit, setEdit] = useState<{ id: string; text: string; revision: string; mode: string } | null>(null);
  const item = run?.follow_up?.items.find(row => row.id === edit?.id);
  const revision = (item?.mode === "steer" ? item.edit_revision : run?.follow_up?.queue_revision) || "";
  const editable = Boolean(item?.editable && revision);
  const change = useMutation({
    mutationFn: (input: { id: string; content: string; expected_revision: string }) =>
      api.editAgentRunQueue(apiContext, run!.id, token, { content: input.content, expected_revision: input.expected_revision }, input.id),
    onSuccess: data => {
      queries.setQueriesData<PrivateAgentRunDisplay>({ queryKey: ["private-agent-run", apiContext, run!.id] }, current => current ? { ...current, follow_up: data } : current);
      setEdit(null);
      void queries.invalidateQueries({ queryKey: ["private-agent-run", apiContext, run!.id] });
    },
    onError: () => { void queries.invalidateQueries({ queryKey: ["private-agent-run", apiContext, run!.id] }); },
  });
  const conflicted = Boolean(edit && (edit.revision !== revision || change.isError));
  function begin(row: AgentRunFollowUp) {
    if (edit || change.isPending) return;
    change.reset();
    setEdit({ id: row.id, text: row.content, mode: row.mode, revision: (row.mode === "steer" ? row.edit_revision : run?.follow_up?.queue_revision) || "" });
  }
  const blocked = !editable || conflicted || !token || change.isPending;
  return { edit, begin, blocked, busy: change.isPending,
    text: edit?.text || "",
    setText: (text: string) => setEdit(current => current ? { ...current, text } : null),
    cancel: () => { if (!change.isPending) { setEdit(null); change.reset(); } },
    save: (text: string) => { if (edit && !blocked && text.trim() && Array.from(text.trim()).length <= 8000) change.mutate({ id: edit.id, content: text.trim(), expected_revision: edit.revision }); },
    notice: edit ? <section aria-label={t(edit.mode === "steer" ? "Edit current-turn instruction" : "Edit queued message")} className="mb-2 rounded-lg border border-black/10 bg-[#f8f8f7] px-3 py-2 text-xs">
      <div className="flex items-center justify-between gap-2"><strong>{t(edit.mode === "steer" ? "Edit current-turn instruction" : "Edit queued message")}</strong><button type="button" className="min-h-9 px-2 underline" disabled={change.isPending} onClick={() => { setEdit(null); change.reset(); }}>{t("Cancel")}</button></div>
      <p>{t("Nothing is sent as a new turn.")}</p>
      {!editable || conflicted ? <div role="alert" className="mt-2 text-amber-800"><p>{!editable ? t("This message can no longer be edited. Your changes are kept here for review.") : t("The queue changed or saving could not be confirmed. Your changes are kept; review the current message before saving again.")}</p>
        <p className="mt-1 whitespace-pre-wrap break-words">{t("Current message:")} {item?.content}</p>
        {editable ? <button type="button" disabled={change.isPending} className="min-h-9 underline" onClick={() => { change.reset(); setEdit(current => current ? { ...current, revision } : null); }}>{t("I reviewed the current queue")}</button> : null}
      </div> : null}
    </section> : null,
  };
}
