import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import type { AgentRunFollowUp, PrivateAgentRunDisplay } from "../lib/types";
import { NexilumeDialog } from "./NexilumeControls";

export function RunQueueActions({ run, item, token, onRemove, removing }: {
  run: PrivateAgentRunDisplay; item: AgentRunFollowUp; token: string; onRemove: () => void; removing: boolean;
}) {
  const { apiContext } = useAuth();
  const queries = useQueryClient();
  const [editor, setEditor] = useState<{ text: string; revision: string } | null>(null);
  const queue = run.follow_up?.items.filter(row => row.mode === "queue" && row.status === "pending") || [];
  const index = queue.findIndex(row => row.id === item.id);
  const revision = run.follow_up?.queue_revision || "";
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
  if (!["pending", "blocked"].includes(item.status) && !editor) return null;
  return <div className="shrink-0 text-xs">
    {["pending", "blocked"].includes(item.status) ? <select aria-label={`Actions for queued message: ${item.content}`} value="" disabled={change.isPending || removing} className="min-h-11 max-w-32 rounded-md border border-black/15 bg-white px-2" onChange={event => {
      change.reset();
      if (event.target.value === "edit") setEditor({ text: item.content, revision });
      else if (event.target.value === "up") move(-1);
      else if (event.target.value === "down") move(1);
      else if (event.target.value === "remove") onRemove();
    }}>
      <option value="">{change.isPending ? "Saving…" : "Actions"}</option>
      {editable ? <option value="edit">Edit message</option> : null}
      {editable ? <option value="up" disabled={index <= 0 || queue.some(row => !row.editable)}>Move earlier</option> : null}
      {editable ? <option value="down" disabled={index < 0 || index === queue.length - 1 || queue.some(row => !row.editable)}>Move later</option> : null}
      <option value="remove">Remove message</option>
    </select> : null}
    {change.isError && !editor ? <p role="alert" className="max-w-44 text-red-800">Change not confirmed. Review the refreshed queue before trying again.</p> : null}
    <NexilumeDialog open={Boolean(editor)} onClose={() => setEditor(null)} busy={change.isPending} title="Edit queued message" description="Only messages that have not started can be changed. Nothing is sent as a new turn." footer={<>
      <button type="button" className="btn min-h-11" disabled={change.isPending} onClick={() => setEditor(null)}>Cancel</button>
      <button type="button" className="btn btn-primary min-h-11 min-w-32" disabled={change.isPending || invalid || changed || !editable} onClick={() => editor && change.mutate({ content: editor.text.trim(), expected_revision: editor.revision })}>{change.isPending ? "Saving…" : "Save changes"}</button>
    </>}>
      <label className="grid gap-2 text-sm">Queued message<textarea rows={6} value={editor?.text || ""} className="w-full resize-y rounded-md border border-black/20 p-3" onChange={event => setEditor(current => current ? { ...current, text: event.target.value } : null)} /></label>
      <p className="mt-2 text-xs text-muted">{Array.from(editor?.text.trim() || "").length.toLocaleString()} / 8,000 characters</p>
      {changed || !editable || change.isError ? <div role="alert" className="mt-3 text-sm text-amber-800">
        <p>{!editable ? "This message can no longer be edited. Your changes are kept here for review." : "The queue changed or saving could not be confirmed. Your changes are kept; review the current message before saving again."}</p>
        <p className="mt-2 whitespace-pre-wrap break-words">Current message: {item.content}</p>
        {editable ? <button type="button" className="min-h-11 underline" disabled={change.isPending} onClick={() => { change.reset(); setEditor(current => current ? { ...current, revision } : null); }}>I reviewed the current queue</button> : null}
      </div> : null}
    </NexilumeDialog>
  </div>;
}
