import { Suspense, type ReactNode } from "react";
import { useApplicationDistribution } from "../app/distribution";
import type { ShareResourceModalProps } from "../app/resourceSharing";
export type { ShareResourceTarget } from "../app/resourceSharing";

/** Optional extension only: absent distributions mount no directory queries or mutations. */
export function ShareResourceModal(props: ShareResourceModalProps) {
  const Dialog = useApplicationDistribution().resourceSharing?.Dialog;
  return Dialog && props.target ? <Suspense fallback={<p role="status">Loading sharing tools…</p>}><Dialog {...props} /></Suspense> : null;
}

/** Not an authorization check; server permissions still govern all sharing. */
export function ResourceSharingAction({ children }: { children: ReactNode }) {
  return useApplicationDistribution().resourceSharing ? <>{children}</> : null;
}
