/** One polling policy for the display. Keep recovery live without polling every
 * secondary surface at interactive speed. React Query pauses hidden tabs. */
export function runDisplayPolling({ status, shell, files, attached, ready, pendingOutputs }: {
  status?: string; shell: boolean; files: boolean; attached: boolean; ready?: boolean; pendingOutputs?: boolean;
}) {
  const done = ["completed", "failed", "cancelled", "expired"].includes(status || "");
  return {
    run: done ? (attached ? 15_000 : false) : 5_000,
    readiness: !status || status === "failed" || ready === false ? 5_000 : 30_000,
    history: 30_000,
    terminal: !attached || !shell ? false : done ? false : 3_000,
    outputs: done ? (pendingOutputs ? 5_000 : false) : files ? 5_000 : 30_000,
  } as const;
}
