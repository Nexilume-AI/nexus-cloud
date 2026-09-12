import { Bot, FolderTree, MonitorUp, ShieldCheck, Terminal } from "lucide-react";
import { Link } from "react-router-dom";

import { useAuth } from "../app/AuthContext";
import { useAuthPresentation } from './AuthPresentation';

const accessReason = "Sign in to open Computer and project-scoped execution sessions.";

const capabilities = (terminalDescription: string) => [
  {
    icon: Terminal,
    label: "Remote terminal",
    description: terminalDescription
  },
  {
    icon: MonitorUp,
    label: "Live execution",
    description: "Follow connection health and session state in real time."
  },
  {
    icon: FolderTree,
    label: "Workspace context",
    description: "Keep targets, runtime setup, and recent activity together."
  },
  {
    icon: Bot,
    label: "Agent tooling",
    description: "Configure Codex, provider APIs, and MCP from one workbench."
  }
];

export function RemoteWorkspacePreview() {
  const auth = useAuth();
  const copy = useAuthPresentation();

  return (
    <section className="mx-auto w-full max-w-5xl py-4 sm:py-8" aria-labelledby="remote-workspace-preview-title">
      <div className="overflow-hidden rounded-2xl border border-line bg-white shadow-panel">
        <div className="grid lg:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
          <div className="p-6 sm:p-8 lg:p-10">
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-brand">Computer</div>
            <h1 id="remote-workspace-preview-title" className="mt-3 max-w-2xl text-3xl font-semibold tracking-[-0.04em] text-ink sm:text-4xl">
              Operate remote computers without losing session context.
            </h1>
            <p className="mt-4 max-w-xl text-base leading-7 text-muted">
              {copy.computerIntroduction}
            </p>

            <div className="mt-7 flex flex-wrap items-center gap-3">
              <button className="btn btn-primary" type="button" onClick={() => auth.requestLogin(accessReason)}>
                <ShieldCheck size={16} />
                Sign in to open Computer
              </button>
              <Link className="btn" to="/">
                Back to overview
              </Link>
            </div>

            <div className="mt-7 flex items-start gap-2 border-t border-line pt-5 text-sm leading-6 text-muted">
              <ShieldCheck size={16} className="mt-1 shrink-0 text-success" aria-hidden="true" />
              <span>{copy.computerPrivacy}</span>
            </div>
          </div>

          <div className="border-t border-line bg-slate-50/80 p-6 sm:p-8 lg:border-l lg:border-t-0">
            <h2 className="text-sm font-semibold text-ink">Available after sign-in</h2>
            <div className="mt-4 divide-y divide-line">
              {capabilities(copy.computerTerminal).map((capability) => {
                const Icon = capability.icon;
                return (
                  <div key={capability.label} className="flex gap-3 py-4 first:pt-0 last:pb-0">
                    <Icon size={18} className="mt-0.5 shrink-0 text-slate-600" aria-hidden="true" />
                    <div>
                      <div className="text-sm font-semibold text-ink">{capability.label}</div>
                      <div className="mt-1 text-sm leading-6 text-muted">{capability.description}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
