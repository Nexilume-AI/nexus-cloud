type GoogleSignInButtonProps = {
  onClick: () => void;
  disabled?: boolean;
};

export function GoogleSignInButton({ onClick, disabled = false }: GoogleSignInButtonProps) {
  return (
    <button className="btn min-h-11 w-full border-line bg-white text-ink hover:bg-slate-50" onClick={onClick} disabled={disabled} type="button">
      <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4">
        <path fill="#4285F4" d="M21.6 12.23c0-.71-.06-1.39-.18-2.04H12v3.86h5.38a4.6 4.6 0 0 1-2 3.02v2.5h3.24c1.9-1.75 2.98-4.33 2.98-7.34Z" />
        <path fill="#34A853" d="M12 22c2.7 0 4.98-.9 6.64-2.43l-3.24-2.5c-.9.6-2.05.96-3.4.96-2.61 0-4.82-1.76-5.61-4.13H3.04v2.58A10 10 0 0 0 12 22Z" />
        <path fill="#FBBC05" d="M6.39 13.9A6 6 0 0 1 6.08 12c0-.66.11-1.3.31-1.9V7.52H3.04A10 10 0 0 0 2 12c0 1.61.39 3.14 1.04 4.48l3.35-2.58Z" />
        <path fill="#EA4335" d="M12 5.97c1.47 0 2.79.51 3.83 1.5l2.88-2.88A9.65 9.65 0 0 0 12 2a10 10 0 0 0-8.96 5.52l3.35 2.58C7.18 7.73 9.39 5.97 12 5.97Z" />
      </svg>
      Continue with Google
    </button>
  );
}

export function GitHubSignInButton({ onClick, disabled = false }: GoogleSignInButtonProps) {
  return (
    <button className="btn min-h-11 w-full border-line bg-white text-ink hover:bg-slate-50" onClick={onClick} disabled={disabled} type="button">
      <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4 fill-current">
        <path d="M12 .7a11.5 11.5 0 0 0-3.64 22.41c.58.11.79-.25.79-.56v-2.23c-3.23.7-3.91-1.37-3.91-1.37-.53-1.34-1.29-1.7-1.29-1.7-1.05-.72.08-.71.08-.71 1.17.08 1.78 1.2 1.78 1.2 1.04 1.78 2.72 1.27 3.39.97.1-.75.4-1.27.74-1.56-2.58-.29-5.29-1.29-5.29-5.69 0-1.26.45-2.29 1.19-3.09-.12-.29-.52-1.47.11-3.05 0 0 .97-.31 3.16 1.18a10.9 10.9 0 0 1 5.76 0c2.2-1.49 3.16-1.18 3.16-1.18.63 1.58.23 2.76.11 3.05.74.8 1.19 1.83 1.19 3.09 0 4.42-2.72 5.39-5.31 5.68.42.36.79 1.07.79 2.16v3.2c0 .31.21.68.8.56A11.5 11.5 0 0 0 12 .7Z" />
      </svg>
      Continue with GitHub
    </button>
  );
}

export function SignInDivider() {
  return (
    <div className="my-5 flex items-center gap-3 text-xs text-muted" aria-hidden="true">
      <span className="h-px flex-1 bg-line" />
      <span>or use email</span>
      <span className="h-px flex-1 bg-line" />
    </div>
  );
}
