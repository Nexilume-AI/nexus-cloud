import { t, useLocale } from "../localization";
import type { ReactNode } from "react";

export function Field({
  label,
  hint,
  children
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  useLocale();
  return (
    <label className="grid gap-1.5">
      <span className="text-sm font-medium text-ink">{t(label)}</span>
      {children}
      {hint && <span className="text-xs text-muted">{t(hint)}</span>}
    </label>
  );
}
