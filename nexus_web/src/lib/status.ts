import { CheckCircle2, CircleDashed, CircleOff, Clock, ShieldAlert, TriangleAlert } from "lucide-react";

export function statusTone(status: string | null | undefined) {
  const normalized = (status ?? "").toLowerCase();
  if (["active", "healthy", "available", "succeeded", "success", "enabled"].includes(normalized)) return "success";
  if (["pending", "login_required", "limited", "starting", "queued"].includes(normalized)) return "warn";
  if (["failed", "disabled", "revoked", "unhealthy", "exhausted", "error"].includes(normalized)) return "danger";
  return "muted";
}

export function statusIcon(status: string | null | undefined) {
  const tone = statusTone(status);
  if (tone === "success") return CheckCircle2;
  if (tone === "warn") return TriangleAlert;
  if (tone === "danger") return CircleOff;
  if ((status ?? "").toLowerCase().includes("pending")) return Clock;
  if ((status ?? "").toLowerCase().includes("policy")) return ShieldAlert;
  return CircleDashed;
}
