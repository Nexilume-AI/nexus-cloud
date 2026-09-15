import { t, useLocale } from "../localization";
import { useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, Loader2 } from "lucide-react";
import type { PrivateAgentRunDisplay, RunFailure } from "../lib/types";

// Old Cloud cannot establish the component at fault from -32004 or prose.
export function currentRunFailure(run?: PrivateAgentRunDisplay): RunFailure | null {
  if (run?.execution_task?.status === "recovery_required") return {
    domain: "cloud", code: run.execution_task.error_code || "RECOVERY_DECISION_REQUIRED",
    title: t("Recovery decision needed"),
    message: t("The Agent process stopped while an external operation may have been in flight."),
    recovery_hint: "Check the external result first. Retry only if you accept the risk that an unconfirmed operation may run twice.",
    actions: ["check_status", "retry_operation", "cancel_turn"], outcome_unknown: true, automatic_retry: false,
  };
  if (run?.status !== "failed") return null;
  if (run.failure) return run.failure;
  const raw = run.execution_task?.error_code || "";
  return { domain: "unknown", code: /^[A-Z][A-Z0-9_]{0,63}$/.test(raw) ? raw : "UNKNOWN_FAILURE",
    title: t("Failure source not confirmed"), message: t("This turn failed without structured failure details."),
    recovery_hint: "Check status and inspect existing results before sending another instruction.",
    actions: ["check_status", "continue_chat"], outcome_unknown: true, automatic_retry: false };
}

const labels: Record<RunFailure["actions"][number], string> = {
  check_status: "Check status", manage_computer: "Review Computer", manage_mobile: "Review Mobile",
  retry_operation: "Retry operation", cancel_turn: "Cancel turn",
  view_browser: "Inspect Browser", continue_chat: "Revise instruction", new_run: "Start a fresh Run",
};
const domains: Record<RunFailure["domain"], string> = {
  agent: "Agent", cloud: "Cloud", computer: "Computer", browser: "Browser", business: "Task", mobile: "Mobile", unknown: "Unconfirmed",
};

export function RunFailureNotice({ failure, runId, ready, canResume, checking, onAction }: {
  failure: RunFailure; runId: string; ready: boolean; canResume: boolean; checking: boolean;
  onAction: (action: RunFailure["actions"][number]) => void;
}) {
  useLocale();
  const [copyState, setCopyState] = useState<"" | "copied" | "failed">("");
  // Allowlist: never copy task.result, messages, endpoints or Tokens.
  const diagnostic = `Run: ${runId}\nSource: ${failure.domain}\nCode: ${failure.code}`;
  async function copy() {
    try { await navigator.clipboard.writeText(diagnostic); setCopyState("copied"); }
    catch { setCopyState("failed"); }
  }
  return <section aria-label={t("Run failure recovery")} className="mx-4 mt-3 max-h-[40vh] shrink-0 overflow-y-auto rounded-md border border-[#e5b4b0] bg-[#fff1ef] px-3 py-3 text-sm text-[#8a2d27]">
    <div role="alert"><div className="flex items-center gap-2 font-semibold"><AlertTriangle size={16} aria-hidden="true" /><span>{domains[failure.domain]} · {failure.title}</span></div>
      <p className="mt-1 leading-5">{failure.message}</p>
      {failure.outcome_unknown ? <p className="mt-1 text-xs leading-5">{t("Outcome unconfirmed. Check results before repeating an action.")}</p> : null}
    </div>
    <details className="mt-1 text-xs leading-5"><summary className="min-h-11 cursor-pointer py-3 font-semibold">{t("Recovery guidance")}</summary><p className="mt-1">{failure.recovery_hint}</p><p>{t("Unknown external effects are never replayed without your decision.")}</p></details>
    <div className="mt-2 flex flex-wrap items-center gap-2">
      {failure.actions.map(action => <button key={action} type="button" className="min-h-11 rounded-md border border-black/15 bg-white px-3 text-xs font-semibold text-[#535350] disabled:opacity-50"
        disabled={(action === "check_status" && checking) || (action === "continue_chat" && (!ready || !canResume))}
        onClick={() => onAction(action)}>{action === "check_status" && checking ? <span className="inline-flex items-center gap-2"><Loader2 size={13} className="animate-spin" />{t("Checking")}</span> : labels[action]}</button>)}
      {!canResume && !failure.actions.includes("new_run") ? <button className="min-h-11 rounded-md border border-black/15 bg-white px-3 text-xs" onClick={() => onAction("new_run")}>{t("Start a fresh Run")}</button> : null}
      <button className="min-h-11 px-2 text-xs underline" onClick={copy}><span className="inline-flex items-center gap-1">{copyState === "copied" ? <CheckCircle2 size={13} /> : <Copy size={13} />}{copyState === "copied" ? t("Copied") : t("Copy failure reference")}</span></button>
    </div>
    {copyState === "failed" ? <div role="status" className="mt-2 text-xs"><p>{t("Copy unavailable. Select this reference manually:")}</p><pre tabIndex={0} className="select-text whitespace-pre-wrap break-all">{diagnostic}</pre></div> : null}
    <div className="mt-2 break-all font-mono text-[10px]">{failure.code}</div>
    {failure.actions.includes("continue_chat") && !ready ? <p role="status" className="mt-1 text-xs">{t("Waiting for Agent and device readiness. Your chat history is preserved.")}</p> : null}
  </section>;
}
