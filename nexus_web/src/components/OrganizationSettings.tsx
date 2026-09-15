import { t, useLocale } from "../localization";
import { Suspense } from "react";
import { useApplicationDistribution } from "../app/distribution";

/** Missing extensions mount no organization or permission queries. */
export function OrganizationSettings() {
  useLocale();
  const Panel = useApplicationDistribution().organizationSettings?.Panel;
  return Panel ? <Suspense fallback={<p role="status">{t("Loading organization settings…")}</p>}><Panel /></Suspense> : null;
}
