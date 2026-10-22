import { useEffect, useId, useState } from "react";
import { t } from "../localization";
import type { AgentMcpExport } from "../lib/types";
import { hasDeclaredCodexHooks, serializeCodexHook } from "../lib/codexHookConfig";

export function CodexHookSetup({ config, onReload }: { config: AgentMcpExport; onReload: () => void }) {
  const id = useId();
  const [reviewed, setReviewed] = useState(false);
  const [copied, setCopied] = useState("");
  const [copyError, setCopyError] = useState(false);
  useEffect(() => { setReviewed(false); setCopied(""); setCopyError(false); }, [config]);
  if (!hasDeclaredCodexHooks(config)) return null;
  let content = "", error = "";
  try { content = serializeCodexHook(config); }
  catch (cause) { error = cause instanceof Error ? cause.message : "Hook configuration is unavailable."; }
  async function copy() {
    if (!content || !reviewed) return;
    try { await navigator.clipboard.writeText(content); setCopied(content); setCopyError(false); }
    catch { setCopyError(true); setCopied(""); }
  }
  return <div className="grid min-w-0 gap-4" aria-label={t("Codex Hook setup")}>
    <p className="text-sm text-muted">{t("The Agent author defines these hooks in the SDK. Review and copy the configuration; no event or argument setup is needed.")}</p>
    {error ? <div className="grid gap-2" role="status">
      <p>{t(error)}</p>
      <button className="btn min-h-11 justify-self-start" onClick={onReload}>{t("Retry configuration")}</button>
    </div> : <>
      <ul className="min-w-0 divide-y divide-line rounded-lg border border-line" aria-label={t("Published SDK hooks")}>
        {config.tools?.flatMap(tool => (tool.codex_hooks ?? []).map((hook, index) =>
          <li className="min-w-0 p-3" key={tool.name + index}>
            <div className="flex flex-wrap items-center gap-2 text-sm"><strong>{hook.event}</strong><span className="break-all text-muted">{tool.name}</span></div>
            <details className="min-w-0 text-sm">
              <summary className="min-h-11 cursor-pointer py-3">{t("View trigger and transmitted data")}</summary>
              <pre className="max-h-48 overflow-auto text-xs" tabIndex={0}>{JSON.stringify(hook, null, 2)}</pre>
            </details>
          </li>))}
      </ul>
      <div className="min-w-0 overflow-hidden rounded-lg border border-line">
        <div className="border-b border-line bg-paper p-3 text-sm"><code>~/.codex/config.toml</code> · TOML</div>
        <pre className="max-h-64 max-w-full overflow-auto p-4 font-mono text-xs" aria-label={t("Generated Codex Hook configuration")} tabIndex={0}>{content}</pre>
      </div>
      <label className="flex min-h-11 items-start gap-2 text-sm" htmlFor={id + "-review"}>
        <input id={id + "-review"} className="mt-1" type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} />
        {t("I reviewed the transmitted data. These hooks run automatically and may incur Agent charges.")}
      </label>
      <button className="btn btn-primary min-h-11 justify-self-start" disabled={!reviewed} onClick={() => void copy()}>
        {t(copied === content ? "Hook configuration copied" : "Copy Hook configuration")}
      </button>
      {copyError && <p role="alert" className="text-sm text-danger">{t("Copy failed. Select the configuration above and copy it manually.")}</p>}
    </>}
    <div className="grid gap-2 border-t border-line pt-3 text-sm text-muted">
      <p>{t("Merge, do not overwrite, your existing MCP servers and hooks. Set {{0}} before starting Codex, connect the server, then review and trust the Hook in Codex.", { 0: config.credential.environment_variable })}</p>
      <p>{t("Hooks wait for the MCP result. Missing connections, errors or timeouts do not reliably block an operation. This is not a security boundary; Nexus permissions remain enforced.")}</p>
      <p>{t("SessionEnd is not supported by Codex MCP hooks. SessionStart may run before the MCP connection is ready.")}</p>
      <a className="underline underline-offset-4" href="https://learn.chatgpt.com/docs/hooks" target="_blank" rel="noreferrer">{t("Codex Hook documentation")}</a>
      <p role="status">{t("Configuration prepared · Client connection not verified")}</p>
    </div>
  </div>;
}
