import { t, useLocale } from "../localization";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useAuth } from "../app/AuthContext";
import { api, ApiError } from "../lib/api";
import { followUpAttachmentReferences, readPendingFollowUp, rememberFollowUp, runDraftKey, settleFollowUp, useRunDraft } from "../lib/runDrafts";
import type { FollowUpAttachments, FollowUpSubmission } from "../lib/runDrafts";
import type { AgentRunFollowUp, PrivateAgentRunDisplay } from "../lib/types";
import { RunQueueActions } from "./RunQueueActions";
import { useRunFollowUpPresentation } from "./RunPresentation";

export const FOLLOW_UP_MAX_CHARACTERS = 8000;

const labels: Record<AgentRunFollowUp["status"], string> = {
  pending: "Saved", received: "Received by Agent · not yet applied", applied: "Applied by Agent",
  rejected: "Declined by Agent", not_applied: "Turn ended before acknowledgement",
  dispatched: "Next turn started", blocked: "Not started · review required", cancelled: "Removed", expired: "Expired",
};
// Dispatch/application receipts belong to conversation history, not the work
// queue. Keep unresolved and unsuccessful delivery states visible for recovery.
const needsQueueDisplay = (item: AgentRunFollowUp) => !["dispatched", "applied", "cancelled"].includes(item.status);

// State is separate from rendering so every Run state can use the same textarea.
// Drafts and uncertain submission envelopes remain isolated by Run and mode.
export type FollowUpAttachmentControl = { value: FollowUpAttachments; blocked: string; onDelivered: (input: FollowUpSubmission) => void };
export function useRunFollowUps(run: PrivateAgentRunDisplay | undefined, onSlashCommand?: (command: string) => boolean, attachments?: FollowUpAttachmentControl) {
  const { apiContext, user } = useAuth();
  const queries = useQueryClient();
  // The host page owns token issuance/renewal and renders only after it loads.
  const displayToken = queries.getQueryData<{ display_token: string }>(["agent-run-display-token", apiContext, run?.id])?.display_token || "";
  const [mode, setMode] = useState<"queue" | "steer">("queue");
  const [commandError, setCommandError] = useState("");
  const [deliveryError, setDeliveryError] = useState<{ key: string; message: string } | null>(null);
  const follow = run?.follow_up;
  const canSteer = follow?.mode === "steer_and_queue";
  const selectedMode = mode === "steer" && canSteer ? "steer" : "queue";
  const draftKey = run ? runDraftKey(user?.user_id, apiContext.tenantId, apiContext.projectId, run.agent_id, run.id, `follow-up:${selectedMode}`) : "";
  const draft = useRunDraft(draftKey);
  const { text, setText } = draft;
  const supportsAttachments = follow?.attachment_protocol?.queue === 1;
  const attachmentValue = supportsAttachments ? attachments?.value || {} : {};
  const hasAttachments = Boolean(attachmentValue.attachments?.length || attachmentValue.files?.length);
  // Older Cloud keeps normal-turn attachments separate from its text-only
  // inbox. Never submit them or clear them after sending an old-protocol input.
  const attachmentError = supportsAttachments ? attachments?.blocked || (hasAttachments && follow?.attachment_protocol?.[selectedMode] !== 1
    ? t("This Agent has not enabled attachments for guidance. Choose Queue next turn, or remove the attachments.") : "") : "";
  function delivered(input: FollowUpSubmission | undefined) { if (input) attachments?.onDelivered(input); }
  const pendingId = draft.pending?.idempotency_key;
  const deliveryKey = JSON.stringify([draftKey, pendingId]);
  const [automatic, setAutomatic] = useState<{ key: string; status: "checking" | "unconfirmed" | "error" } | null>(null);
  const refresh = () => queries.invalidateQueries({ queryKey: ["private-agent-run"] });
  const check = useMutation({
    mutationFn: async (pending: NonNullable<typeof draft.pending>) => ({
      draftKey, key: pending.idempotency_key,
      submission: await api.agentRunFollowUpDelivery(apiContext, run!.id, displayToken, pending.idempotency_key),
    }),
    onSuccess: result => {
      if (result.submission) { delivered(readPendingFollowUp(result.draftKey)); settleFollowUp(result.draftKey, result.key); setCommandError(""); }
      void refresh();
    },
  });
  const send = useMutation({
    mutationFn: async (content: string) => {
      if (!run || !follow) throw new Error(t("No active Run for follow-up messages."));
      // A lost response may already have started the next turn. Retry the exact
      // original envelope, never reinterpret the same input as another turn.
      const pending = readPendingFollowUp(draftKey);
      if (!pending && attachmentError) throw new Error(attachmentError);
      if (pending && pending.content !== content) throw new Error(t("Resolve the previous delivery before sending another message."));
      if (!content.trim() || Array.from(content.trim()).length > FOLLOW_UP_MAX_CHARACTERS)
        throw new Error(t("Provide 1–8,000 characters. Your full draft is kept."));
      const input = pending || {
        mode: selectedMode, content, turn_index: follow!.turn_index, idempotency_key: crypto.randomUUID(),
        ...followUpAttachmentReferences(attachmentValue),
      };
      rememberFollowUp(draftKey, input);
      try {
        await api.submitAgentRunFollowUp(apiContext, run.id, displayToken, input);
        return { draftKey, key: input.idempotency_key, rejection: "" };
      } catch (error) {
        // These explicit rejections occur under the Run lock after the server
        // checks the original key. Unlike a timeout, they prove it was not queued.
        const rejection = error instanceof ApiError && error.status === 400 && error.code === "VALIDATION_ERROR"
          ? error.message || t("This message was not accepted. Edit your draft and try again.")
          : error instanceof ApiError && error.status === 409 && error.code === "FOLLOW_UP_CONFLICT"
          ? ({ "code: TURN_NO_LONGER_ACTIVE": t("This turn has ended. Your message was not queued; continue editing it below."),
            "code: FOLLOW_UP_UNSUPPORTED": t("This Agent no longer accepts queued messages. Your draft is kept."),
            "code: FOLLOW_UP_QUEUE_FULL": t("The message queue is full. Wait for a queued message to finish, then try again."),
            "code: STEER_UNSUPPORTED": t("This Agent cannot accept guidance right now. Your draft is kept."),
            "code: STEER_ATTACHMENTS_UNSUPPORTED": t("The Agent no longer accepts guidance attachments. Choose Queue next turn; your files are kept."),
            "code: FOLLOW_UP_ATTACHMENTS_UNAVAILABLE": t("An attachment expired or is no longer accessible. Reattach or remove it; your message was not queued."),
          } as Record<string, string>)[error.message] : "";
        if (!rejection) throw error;
        return { draftKey, key: input.idempotency_key, rejection };
      }
    },
    onMutate: () => { check.reset(); setDeliveryError(null); setCommandError(""); },
    onSuccess: result => {
      if (!result.rejection) delivered(readPendingFollowUp(result.draftKey));
      settleFollowUp(result.draftKey, result.key, !result.rejection);
      if (result.rejection) setDeliveryError({ key: result.draftKey, message: result.rejection });
      void refresh();
    },
    onError: () => { void refresh(); },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.cancelAgentRunFollowUp(apiContext, run!.id, displayToken, id),
    onSuccess: refresh,
  });
  useEffect(() => {
    setMode("queue"); setCommandError("");
    send.reset(); remove.reset();
  }, [run?.id]);
  useEffect(() => { check.reset(); setCommandError(""); setDeliveryError(null); }, [draftKey]);
  useEffect(() => {
    if (!pendingId || !displayToken || !run?.id || send.isPending) return;
    // Reconcile the exact persisted envelope, never an equal-text list item.
    // One bounded cycle per envelope/context; typing and Run polling do not
    // restart it. A manual retry or renewed Display Token starts a fresh cycle.
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setAutomatic({ key: deliveryKey, status: "checking" });
    async function reconcile(attempt: number) {
      let status: "unconfirmed" | "error" = "unconfirmed";
      try {
        const submission = await api.agentRunFollowUpDelivery(apiContext, run!.id, displayToken, pendingId!, controller.signal);
        if (controller.signal.aborted) return;
        if (submission) {
          delivered(readPendingFollowUp(draftKey));
          settleFollowUp(draftKey, pendingId!);
          setCommandError("");
          void queries.invalidateQueries({ queryKey: ["private-agent-run"] });
          return;
        }
      } catch {
        if (controller.signal.aborted) return;
        status = "error";
      }
      if (attempt < 2) timer = setTimeout(() => void reconcile(attempt + 1), attempt === 0 ? 1000 : 3000);
      else setAutomatic({ key: deliveryKey, status });
    }
    void reconcile(0);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [apiContext, run?.id, draftKey, pendingId, displayToken, send.isPending, deliveryKey, queries]);
  const autoChecking = Boolean(pendingId && displayToken && !send.isPending &&
    (automatic?.key !== deliveryKey || automatic.status === "checking"));
  function submit(value: string) {
    if (check.isPending || send.isPending || autoChecking) return;
    if (!draft.pending && attachmentError) { setCommandError(attachmentError); return; }
    if (Array.from(value.trim()).length > FOLLOW_UP_MAX_CHARACTERS) {
      setCommandError(t("Keep this message within 8,000 characters. Your full draft is kept."));
      return;
    }
    if (draft.pending && draft.pending.content !== value) {
      setCommandError(t("Check the previous message’s delivery before sending a different message."));
      return;
    }
    const slash = value.match(/^\/([a-z0-9-]+)$/i);
    if (slash) {
      const command = slash[1].toLowerCase();
      if (onSlashCommand?.(command)) {
        setCommandError("");
        setText("");
      } else {
        setCommandError(`Unknown command /${command}`);
      }
      return;
    }
    setCommandError("");
    send.mutate(value);
  }
  return { run, follow, draft, text, setText: (value: string) => { setCommandError(""); setDeliveryError(null); setText(value); }, selectedMode, setMode, canSteer, send, remove, submit, displayToken, check, autoChecking,
    automaticStatus: automatic?.key === deliveryKey ? automatic.status : undefined,
    error: commandError || (deliveryError?.key === draftKey ? deliveryError.message : "") || attachmentError, attachmentError,
  };
}

export function RunFollowUps({ controller, active, restoreDisabled = "", appendDraft = false, onRestoreDraft }: {
  controller: ReturnType<typeof useRunFollowUps>; active: boolean;
  restoreDisabled?: string; appendDraft?: boolean; onRestoreDraft: (text: string) => void;
}) {
  useLocale();
  const { run, follow, text, send, remove, draft, check, displayToken, autoChecking, automaticStatus } = controller;
  const { reason } = useRunFollowUpPresentation();
  if (!run) return null;
  const items = follow?.items || [];
  const hasVisibleItems = items.some(needsQueueDisplay);
  const canSend = active && follow?.mode !== "none";
  const uncertain = draft.pending;
  const checked = check.data?.key === uncertain?.idempotency_key && check.data?.submission === null;
  const busy = send.isPending || check.isPending || autoChecking;
  return <section aria-label={t("Run follow-up messages")} className="grid gap-2">
    {active && follow && follow.attachment_protocol?.queue !== 1 ? <p className="text-xs text-muted">{t("Text-only follow-up · saved attachments stay in your next-message draft. Upgrade Cloud to send them while working.")}</p> : null}
    {items.length ? <div className={hasVisibleItems ? "max-h-40 overflow-y-auto rounded-md border border-black/10 bg-white p-2" : "contents"} aria-live="polite">
      {items.map(item => <div key={item.id} className={needsQueueDisplay(item) ? "flex items-start justify-between gap-3 border-b border-black/5 px-2 py-2 last:border-0" : "contents"}>
        {needsQueueDisplay(item) ? <div className="min-w-0"><p className="break-words text-sm">{item.content}</p>
          {[...(item.attachments || []).map(ref => ref.name || "Image"), ...(item.files || []).map(ref => ref.name)].map((name, index) => <p key={index} className="truncate text-xs text-muted" title={name}>{t("Attached ·")}{" "}{name}</p>)}
          <p className="mt-1 text-xs text-[#535350]">{item.mode === "steer" ? t("Current turn") : t("Next turn")} · {t(labels[item.status])}{item.dispatched_turn ? t("· Turn {{0}}", { 0: item.dispatched_turn }) : ""}</p>
          {reason(item.code) ? <p className="mt-1 text-xs text-amber-800">{reason(item.code)}</p> : null}</div> : null}
        {/* Retain the keyed editor if dispatch wins a race with an open edit.
            Its draft stays reviewable, without keeping the receipt row visible. */}
        {item.mode === "queue" ? <RunQueueActions key={`${run.id}:${item.id}`} run={run} item={item} token={displayToken} removing={remove.isPending && remove.variables === item.id} onRemove={() => remove.mutate(item.id)} /> : ["pending", "blocked"].includes(item.status) ? <button type="button" className="min-h-11 shrink-0 px-2 text-xs underline" disabled={remove.isPending && remove.variables === item.id} onClick={() => remove.mutate(item.id)} aria-label={t("Remove follow-up: {{0}}", { 0: item.content })}>{t("Remove")}</button> : null}
      </div>)}
    </div> : null}
    {remove.isError ? <p role="alert" className="text-xs text-red-800">{t("Unable to remove this message. It may already have been received; refresh and try again.")}</p> : null}
    {uncertain ? <section aria-label={t("Message delivery")} className={`min-w-0 rounded-md border p-3 text-sm ${send.isPending || autoChecking ? "border-black/10 bg-white" : "border-amber-200 bg-amber-50/60"}`}>
      <p className="font-medium">{send.isPending ? t("Sending message…") : autoChecking ? t("Checking delivery…") : t("Delivery not confirmed")}</p>
      <p role="status" className="mt-1 text-xs text-[#535350]">{send.isPending ? t("You can keep editing while this message sends.") : autoChecking || check.isPending ? t("Checking saved messages. Nothing is being sent again.") : check.isError || (!check.data && automaticStatus === "error") ? t("Could not check delivery. Your message is still here.") : checked || automaticStatus === "unconfirmed" ? t("Delivery is still unconfirmed. Retry the original message to avoid sending a duplicate.") : t("Your message is kept here. Check its status before retrying.")}</p>
      <p className="mt-2 max-h-24 overflow-y-auto whitespace-pre-wrap break-words" aria-label={t("Original message")}>{uncertain.content}</p>
      {uncertain.attachments?.length || uncertain.files?.length ? <p className="mt-1 text-xs text-muted">{t("Original request ·")}{" "}{uncertain.attachments?.length || 0}{" "}{t("images ·")}{" "}{uncertain.files?.length || 0}{" "}{t("files. Retry keeps these same references.")}</p> : null}
      {!send.isPending && !autoChecking ? <div className="mt-2 flex flex-wrap gap-2">
        <button type="button" className="min-h-11 min-w-28 rounded-md border border-black/15 bg-white px-3 text-xs font-medium disabled:opacity-50" disabled={busy || !displayToken} onClick={() => check.mutate(uncertain)}>{t("Check status")}</button>
        <button type="button" className="min-h-11 px-2 text-xs underline disabled:opacity-50" disabled={busy || !displayToken} onClick={() => send.mutate(uncertain.content)}>{t("Retry original message")}</button>
      </div> : null}
    </section> : !canSend && text && !send.isPending ? <section aria-label={t("Follow-up draft")} className="min-w-0 rounded-md border border-black/10 bg-white p-3 text-sm">
      <p className="font-medium">{t("Unsent draft")}</p>
      {controller.error ? <p role="status" className="mt-1 text-xs text-[#535350]">{controller.error}</p> : null}
      <p className="mt-2 max-h-24 overflow-y-auto whitespace-pre-wrap break-words" aria-label={t("Unsent follow-up draft")}>{text}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        <button type="button" className="min-h-11 rounded-md border border-black/15 px-3 text-xs font-medium disabled:opacity-50" disabled={Boolean(restoreDisabled)} onClick={() => { onRestoreDraft(text); controller.setText(""); }}>{appendDraft ? t("Add to message") : t("Continue editing")}</button>
        <button type="button" className="min-h-11 px-2 text-xs text-[#535350] underline" onClick={() => controller.setText("")}>{t("Discard draft")}</button>
      </div>
      {restoreDisabled ? <p className="mt-1 text-xs text-[#535350]">{restoreDisabled}</p> : null}
    </section> : null}
  </section>;
}

export function RunFollowUpMode({ controller, compact = false }: { controller: ReturnType<typeof useRunFollowUps>; compact?: boolean }) {
  useLocale();
  const { run, selectedMode, setMode, send, canSteer } = controller;
  const { queuedTurnDescription } = useRunFollowUpPresentation();
  const help = selectedMode === "steer" ? t("Applied at the Agent’s next safe step. Commands already running are not undone.")
    : queuedTurnDescription(run);
  return <div className={`flex h-11 min-w-0 items-center gap-2 text-xs ${compact ? "shrink-0" : ""}`}>
    <label htmlFor="run-follow-up-mode" className="sr-only">{t("While Agent is working")}</label>
    <select id="run-follow-up-mode" aria-describedby="run-follow-up-help" title={help} className={`h-11 min-w-0 max-w-full rounded-md border border-black/15 bg-white px-2 ${compact ? "w-[104px]" : ""}`} value={selectedMode} disabled={send.isPending || Boolean(controller.draft.pending)} onChange={event => setMode(event.target.value as "queue" | "steer")}>
      <option value="queue">{compact ? t("Next turn") : t("Queue next turn")}</option>
      {canSteer ? <option value="steer">{compact ? t("Guide turn") : t("Guide current turn")}</option> : null}
    </select>
    <span id="run-follow-up-help" className={compact ? "sr-only" : "min-w-0 truncate text-[#535350]"} title={help}>{help}</span>
  </div>;
}
