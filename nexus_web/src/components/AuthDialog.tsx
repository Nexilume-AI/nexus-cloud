import { useEffect, useRef, useState } from "react";
import { ArrowRight, Loader2, LockKeyhole, ShieldCheck, X } from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "../app/AuthContext";
import { useAuthPresentation } from './AuthPresentation';
import { Field } from "./Form";
import { NexilumeBrand } from "./NexilumeBrand";
import { GitHubSignInButton, GoogleSignInButton, SignInDivider } from "./GoogleSignInButton";

export function AuthDialog() {
  const auth = useAuth();
  const copy = useAuthPresentation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const emailRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const loadingRef = useRef(false);

  useEffect(() => {
    loadingRef.current = loading;
  }, [loading]);

  useEffect(() => {
    if (!auth.isLoginOpen) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const timer = window.setTimeout(() => {
      // Initial focus must never steal a field the user has already selected.
      if (!dialogRef.current || dialogRef.current.contains(document.activeElement)) return;
      if (window.matchMedia("(min-width: 640px)").matches) emailRef.current?.focus();
      else dialogRef.current?.focus();
    }, 30);
    function handleDialogKeyboard(event: KeyboardEvent) {
      if (event.key === "Escape" && !loadingRef.current) {
        auth.closeLogin();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    window.addEventListener("keydown", handleDialogKeyboard);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", handleDialogKeyboard);
      previouslyFocused?.focus();
    };
  }, [auth.closeLogin, auth.isLoginOpen]);

  if (!auth.isLoginOpen || auth.isAuthenticated) return null;

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    try {
      await auth.login(email, password);
      setPassword("");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Sign in failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/50 p-4 backdrop-blur-sm"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !loading) auth.closeLogin();
      }}
    >
      <section
        ref={dialogRef}
        className="relative grid w-full max-w-4xl overflow-hidden rounded-2xl border border-white/60 bg-white shadow-overlay md:grid-cols-[0.9fr_1.1fr]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="auth-dialog-title"
        aria-describedby="auth-dialog-description"
        tabIndex={-1}
      >
        <div className="hidden bg-ink p-8 text-white md:block">
          <NexilumeBrand dark subtitle="Secure workspace access" />
          <div className="mt-14 flex h-12 w-12 items-center justify-center rounded-2xl bg-lume/15 text-lume shadow-glow">
            <LockKeyhole size={23} />
          </div>
          <h2 className="mt-5 text-2xl font-semibold tracking-[-0.03em]">Your workspace data stays private.</h2>
          <p className="mt-3 text-sm leading-6 text-slate-300">
            {copy.introduction}
          </p>
          <div className="mt-8 grid gap-3 text-sm text-slate-300">
            <div className="flex items-center gap-2"><ShieldCheck size={16} className="text-lume" /> {copy.assurances[0]}</div>
            <div className="flex items-center gap-2"><ShieldCheck size={16} className="text-lume" /> {copy.assurances[1]}</div>
            <div className="flex items-center gap-2"><ShieldCheck size={16} className="text-lume" /> {copy.assurances[2]}</div>
          </div>
        </div>

        <div className="p-6 sm:p-8">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.14em] text-brand">Sign in when needed</div>
              <h1 id="auth-dialog-title" className="mt-2 text-2xl font-semibold tracking-[-0.03em] text-ink">Access your workspace</h1>
              <p id="auth-dialog-description" className="mt-2 text-sm leading-6 text-muted">{copy.reason(auth.loginReason)}</p>
            </div>
            <button className="rounded-lg p-2 text-muted transition hover:bg-slate-100 hover:text-ink" onClick={auth.closeLogin} disabled={loading} aria-label="Close sign in" type="button">
              <X size={18} />
            </button>
          </div>

          {auth.googleLoginEnabled || auth.githubLoginEnabled ? (
            <div className="mt-7">
              <div className="grid gap-3">
                {auth.googleLoginEnabled ? <GoogleSignInButton onClick={() => auth.loginWithGoogle()} disabled={loading} /> : null}
                {auth.githubLoginEnabled ? <GitHubSignInButton onClick={() => auth.loginWithGitHub()} disabled={loading} /> : null}
              </div>
              <p className="mt-3 text-center text-xs leading-5 text-muted">{copy.oauthDescription}</p>
              <SignInDivider />
            </div>
          ) : null}

          <form className={auth.googleLoginEnabled || auth.githubLoginEnabled ? "grid gap-5" : "mt-7 grid gap-5"} onSubmit={(event) => void onSubmit(event)}>
            <Field label="Email">
              <input
                ref={emailRef}
                className="input"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@company.com"
                required
              />
            </Field>
            <Field label="Password">
              <input
                className="input"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="Enter your password"
                required
              />
            </Field>
            <button className="btn btn-primary mt-1 w-full" disabled={loading}>
              {loading ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}
              {loading ? "Signing in…" : "Sign in securely"}
            </button>
          </form>

          <button className="mt-5 w-full text-center text-sm font-semibold text-muted transition hover:text-ink" onClick={auth.closeLogin} disabled={loading} type="button">
            {copy.dismissLabel}
          </button>
        </div>
      </section>
    </div>
  );
}
