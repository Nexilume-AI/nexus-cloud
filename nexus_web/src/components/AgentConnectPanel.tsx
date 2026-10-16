import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { AgentMcpExport } from "../lib/types";
import { t } from "../localization";
import { Link } from "react-router-dom";
import { AgentMcpSetup } from "./AgentMcpSetup";

type Props = {
  agentId: string;
  apiContext: Parameters<typeof api.exportAgentMcp>[0];
  workspaceName?: string;
  projectName?: string;
};

export function AgentConnectPanel(props: Props) {
  // Unmount secrets and pending requests whenever caller or scope changes.
  return <ConnectPanel key={`${props.agentId}:${props.apiContext.token}:${props.apiContext.tenantId}:${props.apiContext.projectId}`} {...props} />;
}

function ConnectPanel({ agentId, apiContext, workspaceName, projectName }: Props) {
  const [config, setConfig] = useState<AgentMcpExport | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => setAttempt(value => value + 1), []);
  useEffect(() => {
    let current = true;
    setLoading(true); setError("");
    void api.exportAgentMcp(apiContext, agentId).then(value => {
      if (current) setConfig(value);
    }).catch(() => {
      if (current) { setConfig(null); setError(t("Could not prepare the connection. Try again.")); }
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [agentId, apiContext.token, apiContext.tenantId, apiContext.projectId, attempt]);
  return <AgentMcpSetup config={config} loading={loading} error={error}
    onLoad={retry} workspaceName={workspaceName} projectName={projectName}
    credentials={<p className="text-sm text-muted">{t("Use your Personal Tool Setup credential for this Agent. This page does not create or rotate credentials.")}{" "}<Link className="underline underline-offset-4" to="/remote-workspaces">{t("Open Computer Tool Setup")}</Link></p>} />;
}
