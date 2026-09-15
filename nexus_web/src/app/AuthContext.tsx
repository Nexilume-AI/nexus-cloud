import { t, useLocale, getLocale } from "../localization";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError, api, type ApiContext } from "../lib/api";
import type { Project, Tenant, UserProfile } from "../lib/types";
import { clearRunDrafts } from "../lib/runDrafts";
import { disableCurrentPushDevice } from "../lib/pushNotifications";
import { requireContextDirectory, type ContextDirectory } from "./contextDirectory";

type AuthStatus = "checking" | "anonymous" | "authenticated";

type AuthState = {
  token: string | null;
  refreshToken: string | null;
  tenantId: string | null;
  projectId: string | null;
};

type AuthContextValue = AuthState & {
  user?: UserProfile;
  tenants: Tenant[];
  projects: Project[];
  apiContext: ApiContext;
  status: AuthStatus;
  isAuthenticated: boolean;
  isContextReady: boolean;
  isLoginOpen: boolean;
  loginReason: string;
  googleLoginEnabled: boolean;
  githubLoginEnabled: boolean;
  login: (email: string, password: string) => Promise<void>;
  loginWithGoogle: (next?: string) => void;
  loginWithGitHub: (next?: string) => void;
  logout: () => Promise<void>;
  requestLogin: (reason?: string) => void;
  closeLogin: () => void;
  setTenantId: (tenantId: string) => void;
  setProjectId: (projectId: string | null) => void;
};

const STORAGE_KEY = "nexus.console.context";
const LEGACY_STORAGE_KEY = "nexus.console.auth";

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

function loadInitialState(): AuthState {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") as Partial<AuthState>;
    const legacy = JSON.parse(localStorage.getItem(LEGACY_STORAGE_KEY) ?? "{}") as Partial<AuthState>;
    localStorage.removeItem(LEGACY_STORAGE_KEY);
    return {
      // A legacy access token may finish the current tab session, but it is
      // removed from persistent storage immediately and is never persisted again.
      token: legacy.token ?? null,
      refreshToken: null,
      tenantId: saved.tenantId ?? legacy.tenantId ?? null,
      projectId: saved.projectId ?? legacy.projectId ?? null
    };
  } catch {
    localStorage.removeItem(LEGACY_STORAGE_KEY);
    return { token: null, refreshToken: null, tenantId: null, projectId: null };
  }
}

function persistContext(state: AuthState) {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      tenantId: state.tenantId,
      projectId: state.projectId
    })
  );
}

export function AuthProvider({ children, contextDirectory }: { children: ReactNode; contextDirectory: ContextDirectory }) {
  useLocale();
  const directory = requireContextDirectory(contextDirectory);
  const queryClient = useQueryClient();
  const [state, setState] = useState<AuthState>(loadInitialState);
  const [status, setStatus] = useState<AuthStatus>("checking");
  const [isLoginOpen, setLoginOpen] = useState(false);
  const [loginReason, setLoginReason] = useState("Sign in to access your Nexilume AI workspace.");
  const baseCtx = useMemo(() => ({ token: state.token, tenantId: state.tenantId, projectId: state.projectId }), [state]);

  const bootstrapQuery = useQuery({
    queryKey: ["public", "bootstrap"],
    queryFn: api.publicBootstrap,
    staleTime: 5 * 60_000,
    retry: 1
  });

  const userQuery = useQuery({
    queryKey: ["private", "whoami", state.token, state.tenantId, state.projectId],
    queryFn: () => api.whoami(baseCtx),
    enabled: Boolean(state.token || bootstrapQuery.data?.session_authenticated),
    retry: false
  });

  useEffect(() => {
    if (userQuery.data) {
      setStatus("authenticated");
      return;
    }
    if (userQuery.isError) {
      const error = userQuery.error;
      if (error instanceof ApiError && [401, 403].includes(error.status)) {
        setStatus("anonymous");
        if (state.token) {
          setState((current) => ({ ...current, token: null, refreshToken: null }));
        }
      }
    }
    if (bootstrapQuery.isSuccess && !bootstrapQuery.data.session_authenticated && !state.token) {
      setStatus("anonymous");
    }
  }, [bootstrapQuery.data, bootstrapQuery.isSuccess, state.token, userQuery.data, userQuery.error, userQuery.isError]);

  const tenantsQuery = useQuery({
    queryKey: ["private", "tenants", userQuery.data?.user_id],
    queryFn: () => directory.tenants(baseCtx),
    enabled: status === "authenticated"
  });

  const projectsQuery = useQuery({
    queryKey: ["private", "projects", userQuery.data?.user_id, state.tenantId],
    queryFn: () => directory.projects(baseCtx),
    enabled: status === "authenticated" && Boolean(state.tenantId)
  });

  const updateState = useCallback((next: AuthState) => {
    setState(next);
    persistContext(next);
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      // Ensure the CSRF cookie exists before a browser-originated login.
      await bootstrapQuery.refetch();
      const response = await api.login(email, password);
      const next = {
        token: null,
        refreshToken: null,
        tenantId: response.tenant_id || state.tenantId || null,
        projectId: null
      };
      updateState(next);
      await queryClient.removeQueries({ queryKey: ["private"] });
      const currentUser = await api.whoami({ tenantId: next.tenantId });
      queryClient.setQueryData(["private", "whoami", null, next.tenantId, null], currentUser);
      queryClient.setQueryData(["public", "bootstrap"], {
        ...bootstrapQuery.data,
        session_authenticated: true
      });
      setStatus("authenticated");
      setLoginOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["private"] });
      toast.success(t("Signed in"));
    },
    [bootstrapQuery, queryClient, state.tenantId, updateState, getLocale()]
  );

  const loginWithGoogle = useCallback((next?: string) => {
    const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    const destination = next || (window.location.pathname === "/login" ? "/" : currentPath);
    window.location.assign(`/api/v1/auth/google/start/?next=${encodeURIComponent(destination)}`);
  }, []);

  const loginWithGitHub = useCallback((next?: string) => {
    const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    const destination = next || (window.location.pathname === "/login" ? "/" : currentPath);
    window.location.assign(`/api/v1/auth/github/start/?next=${encodeURIComponent(destination)}`);
  }, []);

  const logout = useCallback(async () => {
    clearRunDrafts();
    if (status === "authenticated") {
      if (userQuery.data?.user_id) await disableCurrentPushDevice(baseCtx, userQuery.data.user_id);
      await api.logout(baseCtx).catch(() => undefined);
    }
    updateState({ token: null, refreshToken: null, tenantId: null, projectId: null });
    queryClient.removeQueries({ queryKey: ["private"] });
    queryClient.setQueryData(["public", "bootstrap"], (current: Record<string, unknown> | undefined) =>
      current ? { ...current, session_authenticated: false } : current
    );
    setStatus("anonymous");
    toast.success(t("Signed out"));
  }, [baseCtx, queryClient, status, updateState, userQuery.data?.user_id, getLocale()]);

  const requestLogin = useCallback((reason = "Sign in to access your Nexilume AI workspace.") => {
    if (status === "authenticated") return;
    setLoginReason(reason);
    setLoginOpen(true);
  }, [status]);

  const closeLogin = useCallback(() => setLoginOpen(false), []);

  const setTenantId = useCallback(
    (tenantId: string) => {
      queryClient.removeQueries({ queryKey: ["private"] });
      updateState({ ...state, tenantId, projectId: null });
    },
    [queryClient, state, updateState]
  );

  const setProjectId = useCallback(
    (projectId: string | null) => {
      updateState({ ...state, projectId });
    },
    [state, updateState]
  );

  const tenants = tenantsQuery.data ?? [];
  const projects = projectsQuery.data ?? [];

  useEffect(() => {
    if (status === "authenticated" && !state.tenantId && tenants.length > 0) {
      updateState({ ...state, tenantId: tenants[0].id });
    }
  }, [state, status, tenants, updateState]);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      user: userQuery.data,
      tenants,
      projects,
      apiContext: baseCtx,
      status,
      isAuthenticated: status === "authenticated",
      isContextReady: status === "authenticated" && Boolean(state.tenantId),
      isLoginOpen,
      loginReason,
      googleLoginEnabled: Boolean(bootstrapQuery.data?.authentication?.google?.enabled),
      githubLoginEnabled: Boolean(bootstrapQuery.data?.authentication?.github?.enabled),
      login,
      loginWithGoogle,
      loginWithGitHub,
      logout,
      requestLogin,
      closeLogin,
      setTenantId,
      setProjectId
    }),
    [baseCtx, closeLogin, isLoginOpen, login, loginWithGoogle, loginWithGitHub, loginReason, logout, projects, requestLogin, setProjectId, setTenantId, state, status, tenants, userQuery.data]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
