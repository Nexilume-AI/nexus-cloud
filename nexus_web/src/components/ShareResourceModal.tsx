import { t, useLocale } from "../localization";
import { Suspense, type ReactNode } from "react";
import { useApplicationDistribution } from "../app/distribution";
import type { ShareResourceModalProps } from "../app/resourceSharing";
export type { ShareResourceTarget } from "../app/resourceSharing";

/** Optional extension only: absent distributions mount no directory queries or mutations. */
export function ShareResourceModal(props: ShareResourceModalProps) {
  useLocale();
  const Dialog = useApplicationDistribution().resourceSharing?.Dialog;
  return Dialog && props.target ? <Suspense fallback={<p role="status">{t("Loading sharing tools…")}</p>}><Dialog {...props} /></Suspense> : null;
}

/** Not an authorization check; server permissions still govern all sharing. */
export function ResourceSharingAction({ children }: { children: ReactNode }) {
  useLocale();
  return useApplicationDistribution().resourceSharing ? <>{children}</> : null;
}
