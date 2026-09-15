import { t, useLocale } from "../localization";
import type { ReactNode } from "react";
import { statusTone } from "../lib/status";

type BadgeProps = {
  children: ReactNode;
  tone?: "success" | "warn" | "danger" | "muted" | "info";
};

const tones = {
  success: "border-green-200 bg-green-50 text-success",
  warn: "border-amber-200 bg-amber-50 text-warn",
  danger: "border-red-200 bg-red-50 text-danger",
  muted: "border-slate-200 bg-slate-50 text-muted",
  info: "border-blue-200 bg-blue-50 text-accent"
};

export function Badge({ children, tone = "muted" }: BadgeProps) {
  useLocale();
  return (
    <span className={`inline-flex h-6 items-center gap-1 rounded-full border px-2.5 text-xs font-semibold ${tones[tone]}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-70" aria-hidden="true" />
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  useLocale();
  return <Badge tone={statusTone(status)}>{t(status || "unknown")}</Badge>;
}
