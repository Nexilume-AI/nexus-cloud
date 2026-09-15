import { t, useLocale } from "../localization";
import { AlertTriangle } from 'lucide-react';
import { useApplicationDistribution } from '../app/distribution';
import { formatDate } from '../lib/format';
import type { EdgeNode } from '../lib/types';

export function RouterRegistrationDiagnostic({ node }: { node: EdgeNode }) {
  useLocale();
  const HostDiagnostic = useApplicationDistribution().edgeRouterPresentation?.RegistrationDiagnostic;
  if (HostDiagnostic) return <HostDiagnostic node={node} />;
  const diagnostic = node.registration_diagnostic;
  return (
    <div className="edge-router-presence-warning" role="status">
      <AlertTriangle size={18} />
      <div className="min-w-0 space-y-2">
        <strong>{diagnostic ? t("Agent registration rejected") : t("Connected · Sync needs attention")}</strong>
        <p>{diagnostic
          ? t("Router heartbeat is current, but this instance rejected an Agent registration. Review the registration settings and the Connector diagnostics.")
          : t("Router heartbeat is current, but the Connector reported an incomplete sync. No specific failure has been recorded yet; inspect the Connector status for the affected Agent or transport.")}</p>
        {diagnostic && <p className="text-xs break-words">{t("Last rejected attempt:")}{" "}{formatDate(diagnostic.occurred_at)} · {diagnostic.code}{" "}{t("· HTTP")}{" "}{diagnostic.http_status}</p>}
      </div>
    </div>
  );
}

export function useRouterStatusLabel() {
  return useApplicationDistribution().edgeRouterPresentation?.statusLabel ?? personalRouterStatusLabel;
}

function personalRouterStatusLabel(node: EdgeNode) {
  if (node.connection_status_reason === 'firmware_upgrade_required') return t("Offline · Upgrade required");
  if (node.connection_status === 'degraded') return t("Degraded · Sync needs attention");
  return node.connection_status.replace(/[_-]+/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase());
}
