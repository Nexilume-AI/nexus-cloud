/** Optional build-selected run decorations; execution remains shared. */
import type { ComponentType } from 'react';
import type { AgentDisplayRun, PrivateAgentRunDisplay } from '../lib/types';

export type RunObservabilityRecord = AgentDisplayRun & { turn_index?: number | null };
export type RunObservabilityPresentation = { turn: string; details: Array<{ label: string; value: string }> };

export type RunPresentationSummaryProps = { run: PrivateAgentRunDisplay | undefined };
export type RunCapacityCopy = {
  usageSuffix: string;
  reviewDescription: string;
  reviewedScope: string;
  emptyDescription: string;
};
export type RunPresentationExtension = {
  Summary: ComponentType<RunPresentationSummaryProps>;
  historyRemovalDescription: string;
  queuedTurnDescription: (run: PrivateAgentRunDisplay | undefined) => string;
  followUpReason: (code: string) => string | undefined;
  capacityRecovery: RunCapacityCopy;
  observability: (run: RunObservabilityRecord) => RunObservabilityPresentation;
};
