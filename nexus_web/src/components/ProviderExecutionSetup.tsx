import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";
import { t } from "../localization";

export function useProviderExecutionSetup(engine: string, enabled = true) {
  const { apiContext, isContextReady } = useAuth();
  const query = useQuery({
    queryKey: ["provider-execution-setup", apiContext],
    queryFn: ({ signal }) => api.providerExecutionSetup(apiContext, signal),
    enabled: enabled && isContextReady && engine !== "direct_api",
    staleTime: 10_000,
    retry: false,
  });
  return { ...query, blocked: engine !== "direct_api" && (query.isError || query.data?.engines?.[engine]?.available !== true) };
}

export function ProviderExecutionSetup({ engine, status }: {
  engine: string;
  status: ReturnType<typeof useProviderExecutionSetup>;
}) {
  if (engine === "direct_api" || !status.blocked) return null;
  const availability = status.data?.engines?.[engine];
  return <section role="status" className="rounded-xl border border-line bg-paper p-4 text-sm">
    <p className="font-semibold">{status.isFetching ? t("Checking Provider execution environment…") : t("Provider execution setup required")}</p>
    <p className="mt-2 text-muted">{status.isError
      ? t("Execution setup could not be checked. Retry before starting a Provider.")
      : availability?.message ? t(availability.message) : t("Codex Proxy and CLIProxyAPI need the optional Docker execution environment. Direct API connections do not.")}</p>
    <div className="mt-3 flex flex-wrap gap-3">
      <a className="btn min-h-11" href="https://github.com/Nexilume-AI/nexus-cloud/blob/main/deploy/community/PROVIDERS.md" target="_blank" rel="noreferrer">{t("Provider setup guide")}</a>
      <button type="button" className="btn min-h-11" onClick={() => void status.refetch()} disabled={status.isFetching}>{t("Check again")}</button>
    </div>
  </section>;
}
