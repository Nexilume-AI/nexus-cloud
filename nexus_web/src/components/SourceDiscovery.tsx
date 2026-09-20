import { t, useLocale } from "../localization";
import { Link } from 'react-router-dom';
import { useApplicationDistribution } from '../app/distribution';

export function useSourceDiscovery() {
  return useApplicationDistribution().sourceDiscovery ?? {
    emptyDescription: t("Create Sources from your Provider models. Compatible Sources will form a Pool boundary."),
    sourceDescription: t("Created from your Providers."),
  };
}

export function ProviderEntryActions() {
  useLocale();
  const discovery=useApplicationDistribution().sourceDiscovery;
  return <div className="flex flex-wrap justify-center gap-2">
    <Link className="btn btn-primary" to="/providers">{t("Open Providers")}</Link>
    {discovery ? <Link className="btn" to={discovery.href}>{discovery.label}</Link> : null}
  </div>;
}
