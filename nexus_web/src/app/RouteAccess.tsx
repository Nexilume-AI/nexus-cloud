import { t, useLocale } from "../localization";
import { useEffect, type ReactNode } from "react";
import { Loader2, LockKeyhole, ShieldCheck } from "lucide-react";
import { useAuth } from "./AuthContext";
import { useAuthPresentation } from '../components/AuthPresentation';

export function PageLoader() {
  useLocale();
  return (
    <div className="flex min-h-[45vh] items-center justify-center">
      <div className="flex items-center gap-3 rounded-xl border border-line bg-white px-4 py-3 text-sm font-medium text-muted shadow-control">
        <Loader2 size={17} className="animate-spin text-accent" />{t("Loading workspace")}</div>
    </div>
  );
}

export function ProtectedRoute({
  reason,
  children,
  anonymousFallback
}: {
  reason: string;
  children: ReactNode;
  anonymousFallback?: ReactNode;
}) {
  useLocale();
  const auth = useAuth();
  const copy = useAuthPresentation();

  useEffect(() => {
    if (auth.status === "anonymous") auth.requestLogin(reason);
  }, [auth.requestLogin, auth.status, reason]);

  if (auth.status === "checking") return <PageLoader />;
  if (auth.isAuthenticated) return children;
  if (anonymousFallback) return anonymousFallback;

  return (
    <section className="mx-auto flex min-h-[62vh] max-w-2xl items-center justify-center px-4 py-12">
      <div className="w-full rounded-2xl border border-line bg-white p-7 text-center shadow-panel sm:p-10">
        <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-lume/20 text-accent">
          <LockKeyhole size={25} />
        </span>
        <div className="mt-5 text-xs font-semibold uppercase tracking-[0.14em] text-brand">{t("Protected workspace data")}</div>
        <h1 className="mt-2 text-2xl font-semibold tracking-[-0.03em] text-ink">{t("Sign in to continue")}</h1>
        <p className="mx-auto mt-3 max-w-lg text-sm leading-6 text-muted">{copy.reason(reason)}</p>
        <button className="btn btn-primary mt-6" onClick={() => auth.requestLogin(reason)} type="button">
          <LockKeyhole size={16} />{" "}{t("Sign in securely")}</button>
        <div className="mt-5 flex items-center justify-center gap-2 text-xs text-muted">
          <ShieldCheck size={14} className="text-success" />
          {copy.protectedPrivacy}
        </div>
      </div>
    </section>
  );
}
