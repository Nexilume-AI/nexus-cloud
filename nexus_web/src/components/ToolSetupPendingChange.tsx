import { t, useLocale } from "../localization";
import { useState } from "react";
import type { WorkspaceToolConfig } from "../lib/types";

export type ToolRecoveryAction = "recover" | "restore_previous" | "keep_local";

const choices: Array<{ value: ToolRecoveryAction; label: string; detail: string }> = [
  { value: "recover", label: "Check saved result", detail: "Check the Computer's saved receipt without repeating the original change." },
  { value: "restore_previous", label: "Restore previous settings", detail: "Restore the settings saved before this change. Newer local edits and invalid credentials prevent restoration; they will not be overwritten." },
  { value: "keep_local", label: "Keep current settings", detail: "Keep the current Computer configuration and cancel the unconfirmed change. Credentials may still need repair." },
];

/** Optional protocol capability, not an edition flag or a permanent recovery tab. */
export function ToolSetupPendingChange({ config, busy, error, onRecover, onReload }: {
  config: WorkspaceToolConfig;
  busy: boolean;
  error: string;
  onRecover: (action: ToolRecoveryAction) => void;
  onReload: () => void;
}) {
  useLocale();
  const [action, setAction] = useState<ToolRecoveryAction>("recover");
  const [confirmed, setConfirmed] = useState(false);
  if (config.write_availability?.available !== false) return null;
  const recovery = config.recovery;
  const options = choices.filter((item) => recovery.actions?.includes(item.value));
  const selected = options.find((item) => item.value === action);
  const canRecover = recovery.available && recovery.operation_id && options.length > 0;
  return <section className="m-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950 sm:mx-6" aria-label={t("Tool setup needs attention")}>
    <h3 className="font-semibold">{recovery.operation_id ? t("A previous change needs checking") : t("Tool setup is read-only")}</h3>
    <p className="mt-2 leading-6">{config.write_availability.message}</p>
    {recovery.message && recovery.message !== config.write_availability.message && <p className="mt-1 leading-6">{recovery.message}</p>}
    {canRecover && <>
      <label className="mt-3 block font-medium" htmlFor="tool-recovery-action">{t("Next action")}</label>
      <select id="tool-recovery-action" className="input mt-1 min-h-11 w-full" disabled={busy} value={selected ? action : ""} onChange={(event) => { setAction(event.target.value as ToolRecoveryAction); setConfirmed(false); }}>
        {!selected && <option value="" disabled>{t("Choose an action")}</option>}
        {options.map((item) => <option key={item.value} value={item.value}>{t(item.label)}</option>)}
      </select>
      {selected && <p className="mt-2 leading-6">{t(selected.detail)}</p>}
      {selected && action !== "recover" && <label className="mt-2 flex min-h-11 items-center gap-3">
        <input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} />{t("I understand and want to")}{" "}{action === "keep_local" ? t("keep current settings") : t("restore previous settings")}.
      </label>}
    </>}
    {error && <p className="mt-3 text-rose-800" role="alert">{error}</p>}
    <div className="mt-3 flex flex-wrap gap-2">
      {canRecover && <button className="btn min-h-11 min-w-36" type="button" disabled={busy || !selected || (action !== "recover" && !confirmed)} onClick={() => onRecover(action)}>{busy ? t("Checking…") : selected?.label || t("Choose an action")}</button>}
      <button className="btn min-h-11" type="button" disabled={busy} onClick={onReload}>{t("Reload status")}</button>
    </div>
  </section>;
}
