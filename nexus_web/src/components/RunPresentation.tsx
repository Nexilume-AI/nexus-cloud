import { t, useLocale } from "../localization";
import { useApplicationDistribution } from '../app/distribution';
import type { RunPresentationSummaryProps, RunObservabilityRecord, RunObservabilityPresentation } from '../app/runPresentation';

export function useRunObservabilityPresentation(run: RunObservabilityRecord): RunObservabilityPresentation {
  const presentation = useApplicationDistribution().runPresentation;
  if (presentation) {
    if (typeof presentation.observability !== 'function') throw new Error('Run observability presentation is incomplete.');
    return presentation.observability(run);
  }
  return { turn: String(run.turn_index || '—'), details: [] };
}

export function RunPresentationSummary(props: RunPresentationSummaryProps) {
  useLocale();
  const Summary = useApplicationDistribution().runPresentation?.Summary;
  return Summary ? <Summary {...props} /> : null;
}

export function useRunHistoryRemovalDescription() {
  return useApplicationDistribution().runPresentation?.historyRemovalDescription
    ?? t("Trace, Outputs and security audit records remain available under their retention policies.");
}

const followUpReasons: Record<string, string> = {
  PREVIOUS_TURN_FAILED: "The previous turn did not succeed. Review its result before sending again.",
  FOLLOW_UP_CONTEXT_CHANGED: "The Agent or Computer changed. Review the context before sending again.",
  FOLLOW_UP_RECHECK_REQUIRED: "Access or device readiness could not be confirmed. Restore access and send again.",
  FOLLOW_UP_UNSUPPORTED: "This Agent does not currently accept follow-up messages.",
  FOLLOW_UP_QUEUE_PAUSED: "An earlier queued message could not start. Review the queue before sending again.",
  FOLLOW_UP_ATTACHMENTS_UNAVAILABLE: "An attachment is no longer available. Reattach it and send a new message.",
};

/** Only explanatory copy varies. Delivery/admission remain in their own services. */
export function useRunFollowUpPresentation() {
  const presentation = useApplicationDistribution().runPresentation;
  return {
    reason: (code: string) => presentation?.followUpReason(code) ?? followUpReasons[code] ?? '',
    queuedTurnDescription: presentation?.queuedTurnDescription
      ?? (() => 'Starts only after this turn succeeds; access and device readiness are checked again.'),
  };
}
