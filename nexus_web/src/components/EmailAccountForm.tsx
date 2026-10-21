import { useState } from "react";
import { Loader2, Mail } from "lucide-react";
import { api } from "../lib/api";
import { t } from "../localization";
import { Field } from "./Form";

export type EmailAccountMode = "register" | "reset" | "verify" | "reset-confirm";

export function EmailAccountForm({ mode, token, onComplete, onBack }: {
  mode: EmailAccountMode; token: string; onComplete: (email: string) => void; onBack: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const confirming = mode === "verify" || mode === "reset-confirm";
  const resetting = mode === "reset" || mode === "reset-confirm";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError("");
    if (confirming && password !== confirmation) { setError(t("Passwords do not match.")); return; }
    setBusy(true);
    try {
      if (confirming) {
        const result = await api.confirmEmailAccount(resetting ? "reset" : "register", token, password, confirmation);
        setPassword(""); setConfirmation(""); onComplete(result.email);
      } else {
        await api.requestEmailAccount(resetting ? "reset" : "register", email.trim());
        setSent(true);
      }
    } catch (reason) {
      setError(reason instanceof Error ? t(reason.message) : t("Email request failed. Please try again."));
    } finally { setBusy(false); }
  }

  return <div className="mt-6">
    <p className="mb-4 text-sm text-muted">{t(confirming
      ? "Choose a strong password with at least 8 characters. Your email is your username."
      : "Your email is your username. We will email you a one-time link, valid for 30 minutes.")}</p>
    {sent ? <div role="status" className="mb-4 rounded-xl border border-brand/20 bg-brand/5 p-4 text-sm">
      {t("If this address is eligible, an email will arrive shortly. Check your inbox and spam folder.")}
      <p className="mt-2">{t("Already registered? Sign in or use Forgot password.")}</p>
    </div> : null}
    <form className="grid gap-4" onSubmit={event => void submit(event)}>
      {!confirming ? <Field label={t("Email (your username)")}><input className="input" type="email" autoComplete="email" maxLength={150} value={email} onChange={event => { setEmail(event.target.value); setSent(false); }} required disabled={busy} /></Field> : <>
        <Field label={t("New password")}><input className="input" type="password" autoComplete="new-password" minLength={8} maxLength={128} value={password} onChange={event => setPassword(event.target.value)} required disabled={busy} /></Field>
        <Field label={t("Confirm new password")}><input className="input" type="password" autoComplete="new-password" minLength={8} maxLength={128} value={confirmation} onChange={event => setConfirmation(event.target.value)} required disabled={busy} /></Field>
      </>}
      {error ? <p role="alert" className="text-sm text-red-700">{error}</p> : null}
      <button className="btn btn-primary w-full" disabled={busy || sent} type="submit">{busy ? <Loader2 size={16} className="animate-spin" /> : <Mail size={16} />}
        {t(busy ? "Please wait…" : confirming ? (resetting ? "Save new password" : "Verify email and create account") : "Send verification email")}
      </button>
    </form>
    <button className="mt-4 text-sm font-semibold text-brand" type="button" disabled={busy} onClick={onBack}>{t("Back to sign in")}</button>
  </div>;
}
