import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

type AppErrorBoundaryProps = {
  children: ReactNode;
};

type AppErrorBoundaryState = {
  error: Error | null;
};

export class AppErrorBoundary extends Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): AppErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Nexilume AI console render error", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div className="mx-auto grid max-w-2xl gap-5 px-2 py-10 sm:px-6 sm:py-16">
        <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-red-100 bg-red-50 text-danger">
          <AlertTriangle size={22} />
        </div>
        <div>
          <h1 className="text-2xl font-semibold tracking-[-0.025em] text-ink">This page could not render</h1>
          <p className="mt-2 text-sm leading-6 text-muted">
            Your session is still active. Try the page again, or reload Nexilume AI if the problem continues.
          </p>
        </div>
        <details className="rounded-xl border border-line bg-white">
          <summary className="cursor-pointer px-4 py-3 text-sm font-semibold text-ink">Technical details</summary>
          <pre className="max-h-56 overflow-auto border-t border-line bg-slate-950 p-4 font-mono text-xs text-slate-100">
          {this.state.error.message}
          </pre>
        </details>
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-primary" onClick={() => window.location.reload()}>
            <RefreshCw size={16} />
            Reload
          </button>
          <button className="btn" onClick={() => this.setState({ error: null })}>
            Try again
          </button>
        </div>
      </div>
    );
  }
}
