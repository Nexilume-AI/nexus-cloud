import type {
  AlertEvent,
  AlertRule,
  AccountProfile,
  Agent,
  AgentComputerBinding,
  AgentPrivateRun,
  AgentPrivateRunPage,
  AgentDeployment,
  AgentDisplayRun,
  AgentInteractor,
  AgentLog,
  AgentMobileBinding,
  AgentMobileGrant,
  AgentMemoryItem,
  AgentMcpExport,
  AgentOutputArtifact,
  AgentRunTerminal,
  AgentRunFollowUp,
  AgentResourceConfig,
  AgentRuntimeDeployment,
  AgentRuntimeImage,
  AgentRuntimeStatus,
  AgentStatus,
  AgentVersion,
  AgentWorkspaceGrant,
  EdgeAgentRegistration,
  EdgeNode,
  EdgeNodeDetail,
  EdgeRouterCapabilities,
  EdgeRelayServiceStatus,
  AuditLog,
  ApiKeyWithPlaintext,
  ChangePasswordResult,
  ChatCompletionRequest,
  CanonicalModel,
  Dataset,
  DatasetCapabilities,
  DatasetContentSearchResult,
  DatasetFile,
  DatasetPullResult,
  DatasetQuota,
  DatasetSearchAllResult,
  DatasetVersion,
  Deployment,
  Envelope,
  Job,
  JobEvent,
  MediaAsset,
  MediaSignedUrl,
  ModelGroup,
  MobileCommand,
  MobileAggregateStatus,
  MobileDevice,
  MobileDeviceWithPairingToken,
  MobileVideoSession,
  PasswordResetResult,
  Provider,
  ProviderAccount,
  ProviderConnection,
  ProviderConnectionDeletionImpact,
  ProviderRuntimeAccount,
  ProviderRuntimeCredentials,
  ProviderRuntimeLoginInfo,
  ProviderRuntimeModelOffer,
  PrivateAgentRunDisplay,
  DisplayStreamEvent,
  ReportDelivery,
  ReportSchedule,
  ResourceMetrics,
  Router,
  ResourceOwnershipInput,
  RouterAggregationCandidate,
  RouterChildBinding,
  RouterCredentials,
  RouterDeployment,
  RouterModelGroupBinding,
  RouterOutput,
  RouterProviderPreference,
  RouterSource,
  RouterVersion,
  RouterTestResult,
  RouterTracePage,
  TopologyPayload,
  SystemMetrics,
  UserProfile,
  WorkspaceConnection,
  WorkspaceConnectionTestResult,
  WorkspaceToolConfig,
  WorkspaceToolConfigChange,
  WorkspaceToolConfigOptions,
  WorkspaceToolConfigPreview,
  WorkspaceTerminalSession,
} from "./types";

export type ApiContext = {
  token?: string | null;
  tenantId?: string | null;
  projectId?: string | null;
};

export type PublicBootstrap = {
  product_name: string;
  anonymous_access: boolean;
  authentication_mode: "on_demand";
  session_authenticated: boolean;
  authentication: {
    google: {
      enabled: boolean;
      signup_enabled: boolean;
    };
    github: {
      enabled: boolean;
      signup_enabled: boolean;
    };
  };
  capabilities: Array<{
    id: string;
    label: string;
    description: string;
  }>;
};

type WebLoginResponse = {
  tenant_id: string;
  session_authenticated: true;
};

const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  Accept: "application/json",
};

export type AgentFileTransfer = { file_id: string; name: string; content_type: string; size_bytes: number;
  received_bytes: number; sha256: string; state: string; chunk_bytes: number; error_code: string; artifact_id?: string;
  source_kind: "upload" | "computer" | "audio" | string; source_label: string; turn_index?: number | null };

export class ApiError extends Error {
  code: string;
  requestId: string;
  status: number;

  constructor(
    message: string,
    options: { code?: string; requestId?: string; status?: number } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.code = options.code ?? "API_ERROR";
    this.requestId = options.requestId ?? "";
    this.status = options.status ?? 0;
  }
}

function cookieValue(name: string) {
  if (typeof document === "undefined") return "";
  const prefix = `${name}=`;
  const cookie = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith(prefix));
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : "";
}

function contextHeaders(
  ctx: ApiContext = {},
  method = "GET",
): Record<string, string> {
  const result: Record<string, string> = {};
  if (ctx.token) result.Authorization = `Bearer ${ctx.token}`;
  if (ctx.tenantId) result["X-Nexus-Tenant"] = ctx.tenantId;
  if (ctx.projectId) result["X-Nexus-Project"] = ctx.projectId;
  if (!["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase())) {
    const csrfToken = cookieValue("csrftoken");
    if (csrfToken) result["X-CSRFToken"] = csrfToken;
  }
  return result;
}

function headers(
  ctx: ApiContext = {},
  extra?: HeadersInit,
  method = "GET",
): HeadersInit {
  return {
    ...JSON_HEADERS,
    ...contextHeaders(ctx, method),
    ...(extra as Record<string, string> | undefined),
  };
}

export function multipartHeaders(ctx: ApiContext = {}, method = "POST"): HeadersInit {
  return {
    ...contextHeaders(ctx, method),
    Accept: "application/json",
  };
}


export async function parseEnvelope<T>(response: Response): Promise<T> {
  const payload = (await response
    .json()
    .catch(() => null)) as Envelope<T> | null;
  if (!payload || typeof payload !== "object" || !("ok" in payload)) {
    if (!response.ok) {
      throw new ApiError(response.statusText || "Request failed", {
        status: response.status,
      });
    }
    return payload as T;
  }

  if (!response.ok || !payload.ok) {
    throw new ApiError(payload.error?.message ?? response.statusText, {
      code: payload.error?.code,
      requestId: payload.request_id,
      status: response.status,
    });
  }

  return payload.data as T;
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
  ctx: ApiContext = {},
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: headers(ctx, init.headers, init.method ?? "GET"),
  });
  return parseEnvelope<T>(response);
}

export async function requestBlob(
  path: string,
  ctx: ApiContext = {},
  extraHeaders: HeadersInit = {},
): Promise<Blob> {
  return (await requestBlobResponse(path, ctx, extraHeaders)).blob();
}

async function requestBlobResponse(path: string, ctx: ApiContext, extraHeaders: HeadersInit = {}): Promise<Response> {
  const response = await fetch(path, {
    credentials: "same-origin",
    // DRF negotiates the request before this endpoint returns its raw image
    // response. Accepting any representation avoids a JSON-renderer 406 while
    // the response's Content-Type still identifies the actual image format.
    headers: headers(ctx, { Accept: "*/*", ...extraHeaders }, "GET"),
  });
  if (!response.ok) {
    await parseEnvelope<never>(response);
    throw new ApiError(response.statusText || "Request failed", {
      status: response.status,
    });
  }
  return response;
}

export const api = {
  runFileReference: (ctx: ApiContext, runId: string, token: string, kind: "input" | "output" | "image", id: string, check = false) =>
    request<import("./composerDrafts").DraftAsset>(`/api/v1/agent-runs/${runId}/file-reference/${check ? `?${new URLSearchParams({ kind, id })}` : ""}`,
      { method: check ? "GET" : "POST", ...(!check ? { body: JSON.stringify({ kind, id }) } : {}), headers: { "X-Nexus-Agent-Display-Token": token } }, ctx),
  runFilePage: async <T>(ctx: ApiContext, runId: string, token: string, kind: "files" | "outputs", filters: { cursor: string; q: string; turn: string }, signal?: AbortSignal) => {
    const params = new URLSearchParams({ paged: "1", limit: "50", ...filters });
    const value = await request<T[] | { items: T[]; next_cursor: string | null }>(`/api/v1/agent-runs/${runId}/${kind}/?${params}`, { signal, headers: { "X-Nexus-Agent-Display-Token": token } }, ctx);
    return Array.isArray(value) ? { items: value, next_cursor: null, legacy: true } : { ...value, legacy: false };
  },
  previewRunFile: async (ctx: ApiContext, runId: string, path: string, token: string, limit: number, signal: AbortSignal) => {
    const base = `/api/v1/agent-runs/${runId}/`;
    const suffix = path.startsWith(base) ? path.slice(base.length) : "";
    if (!/^(?:(?:files|outputs)\/[a-zA-Z0-9-]+\/download|display-assets\/[a-zA-Z0-9-]+)\/$/.test(suffix)) throw new Error("Untrusted file reference.");
    const response = await fetch(path, { signal, credentials: "same-origin", redirect: "error",
      headers: headers(ctx, { Accept: "*/*", "X-Nexus-Agent-Display-Token": token, Range: `bytes=0-${limit}` }, "GET") });
    if (!response.ok) { await parseEnvelope<never>(response); throw new Error("Preview unavailable."); }
    const total = response.headers.get("Content-Range")?.split("/").at(-1) || response.headers.get("Content-Length");
    if (total && Number(total) > limit) { await response.body?.cancel(); throw new Error("This file is too large to preview. Download it instead."); }
    const reader = response.body?.getReader();
    if (!reader) throw new Error("Preview unavailable.");
    const chunks: Uint8Array<ArrayBuffer>[] = []; let size = 0;
    try {
      while (true) {
        const next = await reader.read(); if (next.done) break;
        size += next.value.byteLength;
        if (size > limit) throw new Error("This file is too large to preview. Download it instead.");
        chunks.push(new Uint8Array(next.value));
      }
    } finally { await reader.cancel(); reader.releaseLock(); }
    return new Blob(chunks, { type: response.headers.get("Content-Type") || "application/octet-stream" });
  },
  inboxSummary: (ctx: ApiContext, signal?: AbortSignal) => request<import("./inboxTypes").InboxSummary>("/api/v1/inbox/summary/", { signal }, ctx),
  inboxItems: (ctx: ApiContext, filters: Record<string, string | null | undefined>, signal?: AbortSignal) => {
    const params = new URLSearchParams({ include_counts: "0" });
    Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, value); });
    return request<import("./inboxTypes").InboxPage>(`/api/v1/inbox/items/?${params}`, { signal }, ctx);
  },
  inboxItem: (ctx: ApiContext, id: string, signal?: AbortSignal) => request<import("./inboxTypes").InboxItem>(`/api/v1/inbox/items/${encodeURIComponent(id)}/`, { signal }, ctx),
  readInboxItem: (ctx: ApiContext, id: string) => request(`/api/v1/inbox/items/${encodeURIComponent(id)}/read/`, { method: "POST", body: "{}" }, ctx),
  readAllInboxItems: async (ctx: ApiContext) => {
    let cursor: string | null = null;
    let marked_read = 0;
    do {
      const page: { marked_read: number; next_cursor: string | null } = await request("/api/v1/inbox/read-all/", { method: "POST", body: JSON.stringify({ cursor }) }, ctx);
      marked_read += page.marked_read;
      cursor = page.next_cursor;
    } while (cursor);
    return { marked_read };
  },
  snoozeInboxItem: (ctx: ApiContext, id: string, until: string) => request(`/api/v1/inbox/items/${encodeURIComponent(id)}/snooze/`, { method: "POST", body: JSON.stringify({ until }) }, ctx),
  archiveInboxItem: (ctx: ApiContext, id: string) => request(`/api/v1/inbox/items/${encodeURIComponent(id)}/archive/`, { method: "POST", body: "{}" }, ctx),
  openInboxItem: (ctx: ApiContext, id: string) => request<{ url: string; tenant_id: string; project_id: string | null }>(`/api/v1/inbox/items/${encodeURIComponent(id)}/open/`, { method: "POST", body: "{}" }, ctx),
  inboxPreferences: (ctx: ApiContext) => request<import("./inboxTypes").InboxPreferences>("/api/v1/inbox/preferences/", {}, ctx),
  updateInboxPreferences: (ctx: ApiContext, data: import("./inboxTypes").InboxPreferences) => request<import("./inboxTypes").InboxPreferences>("/api/v1/inbox/preferences/", { method: "PUT", body: JSON.stringify(data) }, ctx),
  pushDevices: (ctx: ApiContext) => request<{ results: import("./inboxTypes").PushDevice[] }>("/api/v1/inbox/push-subscriptions/", {}, ctx),
  subscribePush: (ctx: ApiContext, data: { endpoint: string; keys: { p256dh: string; auth: string }; device_name: string }) => request<import("./inboxTypes").PushDevice>("/api/v1/inbox/push-subscriptions/", { method: "POST", body: JSON.stringify(data) }, ctx),
  unsubscribePush: (ctx: ApiContext, id: string) => request("/api/v1/inbox/push-subscriptions/", { method: "DELETE", body: JSON.stringify({ id }) }, ctx),
  testPush: (ctx: ApiContext, id: string) => request("/api/v1/inbox/push-subscriptions/test/", { method: "POST", body: JSON.stringify({ id }) }, ctx),
  streamInbox: async (ctx: ApiContext, onInvalidate: () => void, signal: AbortSignal) => {
    const response = await fetch("/api/v1/inbox/stream/", { credentials: "same-origin", headers: headers(ctx, { Accept: "text/event-stream" }, "GET"), signal });
    if (!response.ok || !response.body) throw new Error("Inbox live updates are unavailable.");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = "";
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true });
      const frames = pending.split("\n\n");
      pending = frames.pop() ?? "";
      frames.forEach(frame => { if (frame.includes("event: invalidate")) onInvalidate(); });
    }
  },
  notificationSummary: (ctx: ApiContext, signal?: AbortSignal) => request<import("./notificationTypes").NotificationCounts>("/api/v1/notifications/summary/", { signal }, ctx),
  notifications: (ctx: ApiContext, filter: import("./notificationTypes").NotificationFilter, cursor: string | null, signal?: AbortSignal) =>
    request<import("./notificationTypes").NotificationPage>(`/api/v1/notifications/?${new URLSearchParams({ filter, ...(cursor ? { cursor } : {}) })}`, { signal }, ctx),
  readNotification: (ctx: ApiContext, id: string) => request(`/api/v1/notifications/${encodeURIComponent(id)}/read/`, { method: "POST", body: "{}" }, ctx),
  readNotificationBatch: (ctx: ApiContext, snapshot: string) => request("/api/v1/notifications/read-all/", { method: "POST", body: JSON.stringify({ snapshot }) }, ctx),
  openNotification: (ctx: ApiContext, id: string) => request<{ url: string; tenant_id: string; project_id: string | null }>(`/api/v1/notifications/${encodeURIComponent(id)}/open/`, { method: "POST", body: "{}" }, ctx),
  providerImportCapabilities: (ctx: ApiContext) => request<{ can_import: boolean; can_update_existing: boolean }>("/api/v1/provider-connections/imports/capabilities/", {}, ctx),
  providerImports: (ctx: ApiContext) => request<Array<{ id: string; created_at: string; status: string; rows: number }>>("/api/v1/provider-connections/imports/", {}, ctx),
  providerImportTemplate: (ctx: ApiContext, format: "xlsx" | "apis_csv") => requestBlob(`/api/v1/provider-connections/imports/template/?file_format=${format}`, ctx),
  previewProviderImport: async (ctx: ApiContext, body: FormData) => {
    const response = await fetch("/api/v1/provider-connections/imports/preview/", { method: "POST", credentials: "same-origin", headers: multipartHeaders(ctx), body });
    return parseEnvelope<import("./providerImportTypes").ProviderImportBatch>(response);
  },
  providerImport: (ctx: ApiContext, id: string) => request<import("./providerImportTypes").ProviderImportBatch>(`/api/v1/provider-connections/imports/${encodeURIComponent(id)}/`, {}, ctx),
  commitProviderImport: (ctx: ApiContext, id: string, body: { confirm_updates: boolean; allow_partial: boolean; retry_failed: boolean }) => request<import("./providerImportTypes").ProviderImportBatch>(`/api/v1/provider-connections/imports/${encodeURIComponent(id)}/commit/`, { method: "POST", body: JSON.stringify(body) }, ctx),
  discardProviderImport: (ctx: ApiContext, id: string) => request<import("./providerImportTypes").ProviderImportBatch>(`/api/v1/provider-connections/imports/${encodeURIComponent(id)}/`, { method: "DELETE" }, ctx),
  providerImportReport: (ctx: ApiContext, id: string) => requestBlob(`/api/v1/provider-connections/imports/${encodeURIComponent(id)}/report/`, ctx),



  publicBootstrap: () => request<PublicBootstrap>("/api/v1/public/bootstrap/"),

  login: (email: string, password: string) =>
    request<WebLoginResponse>("/api/v1/auth/login/", {
      method: "POST",
      headers: { "X-Nexus-Client": "web" },
      body: JSON.stringify({ email, password }),
    }),

  logout: (ctx: ApiContext) =>
    request<Record<string, unknown>>(
      "/api/v1/auth/logout/",
      { method: "POST" },
      ctx,
    ),

  whoami: (ctx: ApiContext) =>
    request<UserProfile>("/api/v1/auth/whoami/", {}, ctx),

  accountProfile: (ctx: ApiContext) =>
    request<AccountProfile>("/api/v1/account/me/", {}, ctx),

  updateAccountProfile: (
    ctx: ApiContext,
    body: { display_name?: string; phone?: string; company?: string },
  ) =>
    request<AccountProfile>(
      "/api/v1/account/me/",
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  changePassword: (
    ctx: ApiContext,
    body: {
      current_password: string;
      new_password: string;
      new_password_confirm: string;
    },
  ) =>
    request<ChangePasswordResult>(
      "/api/v1/account/change-password/",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  requestPasswordReset: (email: string) =>
    request<PasswordResetResult>("/api/v1/account/password-reset/", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),


  providers: (ctx: ApiContext) =>
    request<Provider[]>("/api/v1/providers/", {}, ctx),

  providerExecutionSetup: (ctx: ApiContext, signal?: AbortSignal) =>
    request<{ engines: Record<string, { available: boolean; code: string; message: string }> }>(
      "/api/v1/provider-connections/execution-setup/", { signal }, ctx),

  providerConnections: (ctx: ApiContext) =>
    request<ProviderConnection[]>(
      `/api/v1/provider-connections/?view_scope=${ctx.projectId ? "current" : "all"}`,
      {},
      ctx,
    ),

  providerConnection: (ctx: ApiContext, id: string, signal?: AbortSignal) => request<ProviderConnection>(`/api/v1/provider-connections/${id}/`, { signal }, ctx),

  providerConnectionsPage: async (ctx: ApiContext, query: Record<string, string>, signal?: AbortSignal) => {
    const result = await request<import("./useCursorPage").CursorPage<ProviderConnection> | ProviderConnection[]>(`/api/v1/provider-connections/?${new URLSearchParams({ ...query, limit: "25", projection: "summary", view_scope: ctx.projectId ? "current" : "all" })}`, { signal }, ctx);
    return Array.isArray(result) ? { items: result, next_cursor: null, has_more: false, limit: 25 } : result;
  },

  legacyProviderRuntimesPage: async (ctx: ApiContext, cursor: string, signal?: AbortSignal) => {
    const result = await request<import("./useCursorPage").CursorPage<ProviderRuntimeAccount> | ProviderRuntimeAccount[]>(`/api/v1/provider-runtimes/?${new URLSearchParams({ unlinked: "true", limit: "25", cursor })}`, { signal }, ctx);
    return Array.isArray(result) ? { items: result, next_cursor: null, has_more: false, limit: 25 } : result;
  },

  createProviderConnection: (
    ctx: ApiContext,
    body: {
      name: string;
      ownership: ResourceOwnershipInput;
      account_id?: string;
      engine: "direct_api" | "codex_proxy" | "cliproxyapi";
      upstream_provider?: string;
      url?: string;
      key?: string;
    },
  ) =>
    request<ProviderConnection>(
      "/api/v1/provider-connections/",
      { method: "POST", body: JSON.stringify(body) },
      ctx,
    ),

  updateProviderConnection: (
    ctx: ApiContext,
    providerId: string,
    body: {
      name?: string;
      engine?: "direct_api" | "codex_proxy" | "cliproxyapi";
      upstream_provider?: string;
      url?: string;
      key?: string;
    },
  ) =>
    request<ProviderConnection>(
      `/api/v1/provider-connections/${providerId}/`,
      { method: "PATCH", body: JSON.stringify(body) },
      ctx,
    ),

  providerConnectionAction: (
    ctx: ApiContext,
    providerId: string,
    action:
      "start" | "stop" | "health" | "repair" | "models/refresh" | "unshare",
  ) =>
    request<ProviderConnection>(
      `/api/v1/provider-connections/${providerId}/${action}/`,
      { method: "POST", body: JSON.stringify({}) },
      ctx,
    ),

  providerConnectionLoginInfo: (ctx: ApiContext, providerId: string) =>
    request<ProviderRuntimeLoginInfo>(
      `/api/v1/provider-connections/${providerId}/login/`,
      {},
      ctx,
    ),

  refreshProviderConnectionModel: (
    ctx: ApiContext,
    providerId: string,
    offerId: string,
  ) =>
    request<ProviderConnection>(
      `/api/v1/provider-connections/${providerId}/models/${offerId}/refresh/`,
      { method: "POST", body: JSON.stringify({}) },
      ctx,
    ),




  providerConnectionDeletionImpact: (ctx: ApiContext, providerId: string) =>
    request<ProviderConnectionDeletionImpact>(
      `/api/v1/provider-connections/${providerId}/deletion-impact/`,
      {},
      ctx,
    ),

  removeProviderConnection: (
    ctx: ApiContext,
    providerId: string,
    confirmationName: string,
  ) =>
    request<{ id: string; status: string }>(
      `/api/v1/provider-connections/${providerId}/remove/`,
      {
        method: "POST",
        body: JSON.stringify({ confirmation_name: confirmationName }),
      },
      ctx,
    ),

  providerAccounts: (ctx: ApiContext) =>
    request<ProviderAccount[]>("/api/v1/provider-accounts/status/", {}, ctx),

  providerRuntimes: (ctx: ApiContext) =>
    request<ProviderRuntimeAccount[]>("/api/v1/provider-runtimes/", {}, ctx),

  createProviderRuntime: (
    ctx: ApiContext,
    body: {
      name: string;
      runtime_type: string;
      share_mode?: string;
      source_provider_account_id?: string | null;
    },
  ) =>
    request<ProviderRuntimeAccount>(
      "/api/v1/provider-runtimes/",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  updateProviderRuntime: (
    ctx: ApiContext,
    runtimeId: string,
    body: {
      name?: string;
      runtime_type?: string;
      source_provider_account_id?: string | null;
    },
  ) =>
    request<ProviderRuntimeAccount>(
      `/api/v1/provider-runtimes/${runtimeId}/`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  deleteProviderRuntime: (ctx: ApiContext, runtimeId: string) =>
    request<ProviderRuntimeAccount>(
      `/api/v1/provider-runtimes/${runtimeId}/`,
      { method: "DELETE" },
      ctx,
    ),

  startProviderRuntime: (ctx: ApiContext, runtimeId: string) =>
    request<ProviderRuntimeAccount>(
      `/api/v1/provider-runtimes/${runtimeId}/start/`,
      { method: "POST" },
      ctx,
    ),

  stopProviderRuntime: (ctx: ApiContext, runtimeId: string) =>
    request<ProviderRuntimeAccount>(
      `/api/v1/provider-runtimes/${runtimeId}/stop/`,
      { method: "POST" },
      ctx,
    ),

  healthCheckProviderRuntime: (ctx: ApiContext, runtimeId: string) =>
    request<ProviderRuntimeAccount>(
      `/api/v1/provider-runtimes/${runtimeId}/health/`,
      { method: "POST" },
      ctx,
    ),

  providerRuntimeLoginInfo: (ctx: ApiContext, runtimeId: string) =>
    request<ProviderRuntimeLoginInfo>(
      `/api/v1/provider-runtimes/${runtimeId}/login/`,
      {},
      ctx,
    ),

  exportProviderRuntimeCredentials: (ctx: ApiContext, runtimeId: string) =>
    request<ProviderRuntimeCredentials>(
      `/api/v1/provider-runtimes/${runtimeId}/export-credentials/`,
      { method: "POST" },
      ctx,
    ),



  providerRuntimeModels: (ctx: ApiContext, runtimeId: string) =>
    request<ProviderRuntimeModelOffer[]>(
      `/api/v1/provider-runtimes/${runtimeId}/models/`,
      {},
      ctx,
    ),

  refreshProviderRuntimeModels: (ctx: ApiContext, runtimeId: string) =>
    request<ProviderRuntimeModelOffer[]>(
      `/api/v1/provider-runtimes/${runtimeId}/models/refresh/`,
      {
        method: "POST",
        body: JSON.stringify({}),
      },
      ctx,
    ),

  refreshProviderRuntimeModel: (
    ctx: ApiContext,
    runtimeId: string,
    offerId: string,
  ) =>
    request<ProviderRuntimeModelOffer>(
      `/api/v1/provider-runtimes/${runtimeId}/models/${offerId}/refresh/`,
      {
        method: "POST",
        body: JSON.stringify({}),
      },
      ctx,
    ),

  configureProviderRuntimeModel: async (
    ctx: ApiContext, runtimeId: string, offerId: string,
    body: Partial<Pick<ProviderRuntimeModelOffer, "status" | "capabilities">> & { canonical_model_id?: string | null },
  ) => {
    const allowed = new Set(["status", "capabilities", "canonical_model_id"]);
    if (!body || typeof body !== "object" || Array.isArray(body) || Object.keys(body).some(key => !allowed.has(key))) {
      throw new ApiError("Only model status, capabilities and canonical mapping may be configured here.", { code: "PROVIDER_MODEL_CONFIGURATION_INVALID" });
    }
    return request<ProviderRuntimeModelOffer>(
      `/api/v1/provider-runtimes/${runtimeId}/models/${offerId}/`,
      { method: "PATCH", body: JSON.stringify(Object.fromEntries(Object.entries(body))) }, ctx,
    );
  },

  canonicalModels: (ctx: ApiContext) =>
    request<CanonicalModel[]>("/api/v1/canonical-models/", {}, ctx),







  createProviderAccount: (
    ctx: ApiContext,
    body: {
      provider?: string;
      name: string;
      account_id?: string;
      url: string;
      key?: string;
      username?: string;
      password?: string;
      auth_mode: string;
      preferred_runtime_type?: string;
    },
  ) =>
    request<ProviderAccount>(
      "/api/v1/provider-accounts/",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  deleteProviderAccount: (ctx: ApiContext, accountId: string) =>
    request<ProviderAccount>(
      `/api/v1/provider-accounts/${accountId}/`,
      { method: "DELETE" },
      ctx,
    ),

  updateProviderAccount: (
    ctx: ApiContext,
    accountId: string,
    body: {
      url?: string;
      pricing_rate?: string;
      auth_mode?: string;
      preferred_runtime_type?: string;
      key?: string;
      username?: string;
      password?: string;
    },
  ) =>
    request<ProviderAccount>(
      `/api/v1/provider-accounts/${accountId}/`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  deployments: (ctx: ApiContext) =>
    request<Deployment[]>(
      `/api/v1/deployments/?view_scope=${ctx.projectId ? "current" : "all"}`,
      {},
      ctx,
    ),

  deploymentStatus: (ctx: ApiContext) =>
    request<Deployment[]>(
      `/api/v1/deployments/status/?view_scope=${ctx.projectId ? "current" : "all"}`,
      {},
      ctx,
    ),

  createModelSourcesBatch: async (
    ctx: ApiContext,
    body: {
      origin: {
        type: "provider_runtime";
        provider_runtime_id: string;
      };
      sources: Array<{
        model_offer_id: string;
        source_id: string;
        visibility?: string;
        model_group_id?: string;
        new_pool?: { name: string; visibility?: string };
      }>;
    },
  ) => {
    if (body?.origin?.type !== "provider_runtime" || typeof body.origin.provider_runtime_id !== "string" || !body.origin.provider_runtime_id) {
      throw new ApiError("Unsupported Source origin.", { code: "MODEL_SOURCE_ORIGIN_INVALID" });
    }
    return request<Deployment[]>(
      "/api/v1/model-sources/batch/",
      { method: "POST", body: JSON.stringify({ origin: { type: "provider_runtime", provider_runtime_id: body.origin.provider_runtime_id }, sources: body.sources }) },
      ctx,
    );
  },

  deleteDeployment: (ctx: ApiContext, deploymentId: string) =>
    request<Deployment>(
      `/api/v1/deployments/${deploymentId}/`,
      { method: "DELETE" },
      ctx,
    ),

  healthCheckDeployment: (ctx: ApiContext, deploymentId: string) =>
    request<Deployment>(
      `/api/v1/deployments/${deploymentId}/health-check/`,
      { method: "POST" },
      ctx,
    ),

  disableDeployment: (ctx: ApiContext, deploymentId: string) =>
    request<Deployment>(
      `/api/v1/deployments/${deploymentId}/disable/`,
      { method: "POST" },
      ctx,
    ),

  models: (ctx: ApiContext) =>
    request<ModelGroup[]>(
      `/api/v1/models/?view_scope=${ctx.projectId ? "current" : "all"}`,
      {},
      ctx,
    ),

  topology: (ctx: ApiContext) =>
    request<TopologyPayload>(
      `/api/v1/topology/?view_scope=${ctx.projectId ? "current" : "all"}`,
      {},
      ctx,
    ),

  updateModelRouting: (
    ctx: ApiContext,
    modelGroupId: string,
    body: {
      expected_revision: number;
      routing_strategy: string;
      routing_config?: Record<string, unknown>;
      sources?: import("./types").ModelGroupRoutingDraftSource[];
    },
  ) =>
    request<ModelGroup>(
      `/api/v1/models/${modelGroupId}/routing/`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  previewModelRouting: (
    ctx: ApiContext,
    modelGroupId: string,
    body: {
      expected_revision: number;
      routing_strategy: string;
      routing_config?: Record<string, unknown>;
      sources?: import("./types").ModelGroupRoutingDraftSource[];
    },
  ) => request<import("./types").ModelGroupRoutingPreview>(
    `/api/v1/models/${modelGroupId}/routing/preview/`,
    { method: "POST", body: JSON.stringify(body) },
    ctx,
  ),

  modelRoutingHistory: (ctx: ApiContext, modelGroupId: string) =>
    request<import("./types").ModelGroupRoutingRevision[]>(
      `/api/v1/models/${modelGroupId}/routing/history/`,
      {},
      ctx,
    ),

  rollbackModelRouting: (ctx: ApiContext, modelGroupId: string, revision: number, expectedRevision: number) =>
    request<ModelGroup>(
      `/api/v1/models/${modelGroupId}/routing/history/${revision}/rollback/`,
      { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision }) },
      ctx,
    ),

  updateModelSourceRouting: (
    ctx: ApiContext,
    modelGroupId: string,
    sourceId: string,
    body: {
      expected_revision: number;
      enabled?: boolean;
      priority?: number;
      weight?: number;
      fallback_order?: number;
    },
  ) =>
    request<ModelGroup["deployments"][number]>(
      `/api/v1/models/${modelGroupId}/sources/${sourceId}/`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  routers: (ctx: ApiContext) =>
    request<Router[]>(
      `/api/v1/routers/?view_scope=${ctx.projectId ? "current" : "all"}`,
      {},
      ctx,
    ),

  routersPage: async (ctx: ApiContext, query: Record<string, string>, signal?: AbortSignal) => {
    const result = await request<import("./useCursorPage").CursorPage<Router> | Router[]>(`/api/v1/routers/?${new URLSearchParams({ ...query, limit: "50", view_scope: ctx.projectId ? "current" : "all" })}`, { signal }, ctx);
    return Array.isArray(result) ? { items: result, next_cursor: null, has_more: false, limit: 50 } : result;
  },

  createRouter: (
    ctx: ApiContext,
    body: {
      name: string;
      router_type: "execution" | "aggregation";
      ownership: ResourceOwnershipInput;
      strategy?: string;
      model_group_ids?: string[];
    },
  ) =>
    request<Router>(
      "/api/v1/routers/",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  router: (ctx: ApiContext, routerId: string) =>
    request<Router>(`/api/v1/routers/${routerId}/`, {}, ctx),

  routerSource: (ctx: ApiContext, routerId: string) =>
    request<RouterSource>(`/api/v1/routers/${routerId}/source/`, {}, ctx),

  updateRouter: (
    ctx: ApiContext,
    routerId: string,
    body: { name?: string; strategy?: string; model_group_ids?: string[] },
  ) =>
    request<Router>(
      `/api/v1/routers/${routerId}/`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  deleteRouter: (ctx: ApiContext, routerId: string) =>
    request<void>(`/api/v1/routers/${routerId}/`, { method: "DELETE" }, ctx),

  setRouterPolicy: (ctx: ApiContext, routerId: string, strategy: string) =>
    request<Router>(
      `/api/v1/routers/${routerId}/policy/`,
      {
        method: "POST",
        body: JSON.stringify({ strategy }),
      },
      ctx,
    ),

  bindRouterModelGroups: (
    ctx: ApiContext,
    routerId: string,
    body: { providers?: string[]; model_group_ids?: string[] },
  ) =>
    request<RouterModelGroupBinding[]>(
      `/api/v1/routers/${routerId}/model-groups/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  updateRouterOutput: (
    ctx: ApiContext,
    routerId: string,
    outputId: string,
    body: {
      model_name?: string;
      description?: string;
      enabled?: boolean;
      is_default?: boolean;
    },
  ) =>
    request<RouterOutput>(
      `/api/v1/routers/${routerId}/outputs/${outputId}/`,
      { method: "PATCH", body: JSON.stringify(body) },
      ctx,
    ),

  routerAggregationCandidates: (ctx: ApiContext, routerId: string) =>
    request<RouterAggregationCandidate[]>(
      `/api/v1/routers/${routerId}/aggregation-candidates/`,
      {},
      ctx,
    ),

  addRouterChildBinding: (
    ctx: ApiContext,
    routerId: string,
    body: {
      exposed_model_name: string;
      child_output_id: string;
      priority?: number;
      weight?: number;
    },
  ) =>
    request<RouterChildBinding>(
      `/api/v1/routers/${routerId}/child-bindings/`,
      { method: "POST", body: JSON.stringify(body) },
      ctx,
    ),

  updateRouterChildBinding: (
    ctx: ApiContext,
    routerId: string,
    bindingId: string,
    body: {
      exposed_model_name?: string;
      enabled?: boolean;
      priority?: number;
      weight?: number;
    },
  ) =>
    request<RouterChildBinding>(
      `/api/v1/routers/${routerId}/child-bindings/${bindingId}/`,
      { method: "PATCH", body: JSON.stringify(body) },
      ctx,
    ),

  removeRouterChildBinding: (
    ctx: ApiContext,
    routerId: string,
    bindingId: string,
  ) =>
    request<void>(
      `/api/v1/routers/${routerId}/child-bindings/${bindingId}/`,
      { method: "DELETE" },
      ctx,
    ),

  uploadRouterFile: async (ctx: ApiContext, routerId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file, "router.py");
    const response = await fetch(`/api/v1/routers/${routerId}/upload/`, {
      method: "POST",
      credentials: "same-origin",
      headers: multipartHeaders(ctx),
      body: formData,
    });
    return parseEnvelope<RouterVersion>(response);
  },

  deployRouter: (ctx: ApiContext, routerId: string) =>
    request<RouterDeployment>(
      `/api/v1/routers/${routerId}/deploy/`,
      { method: "POST" },
      ctx,
    ),

  exportRouterCredentials: (ctx: ApiContext, routerId: string) =>
    request<RouterCredentials>(
      `/api/v1/routers/${routerId}/export-credentials/`,
      { method: "POST" },
      ctx,
    ),

  testRouter: (
    ctx: ApiContext,
    routerId: string,
    body: {
      model: string;
      messages: Array<{ role: string; content: string }>;
      stream?: boolean;
    },
  ) =>
    request<RouterTestResult>(
      `/api/v1/routers/${routerId}/test/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  routerTraces: (
    ctx: ApiContext,
    routerId: string,
    limit = 20,
    cursor = "",
  ) => {
    const query = new URLSearchParams({ limit: String(limit) });
    if (cursor) query.set("cursor", cursor);
    return request<RouterTracePage>(
      `/api/v1/routers/${routerId}/traces/?${query.toString()}`,
      {},
      ctx,
    );
  },

  routerPreferences: (ctx: ApiContext, routerId: string) =>
    request<RouterProviderPreference[]>(
      `/api/v1/routers/${routerId}/provider-preferences/`,
      {},
      ctx,
    ),

  addRouterPreference: (
    ctx: ApiContext,
    routerId: string,
    body: {
      preference_type: string;
      provider_account_id?: string;
      pool_contribution_id?: string;
      priority?: number;
      weight?: number;
    },
  ) =>
    request<RouterProviderPreference>(
      `/api/v1/routers/${routerId}/provider-preferences/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  deleteRouterPreference: (
    ctx: ApiContext,
    routerId: string,
    preferenceId: string,
  ) =>
    request<RouterProviderPreference>(
      `/api/v1/routers/${routerId}/provider-preferences/${preferenceId}/`,
      { method: "DELETE" },
      ctx,
    ),

  systemMetrics: (ctx: ApiContext, seconds = 86400) =>
    request<SystemMetrics>(`/api/v1/metrics/system/?window_seconds=${seconds}`, {}, ctx),

  monitoringCapabilities: (ctx: ApiContext) => request<import("./monitoring").MonitoringCapabilities>("/api/v1/metrics/capabilities/", {}, ctx),
  monitoringPage: <T,>(ctx: ApiContext, path: string, query: Record<string, string>, signal?: AbortSignal) =>
    request<import("./useCursorPage").CursorPage<T>>(`/api/v1/${path}/?${new URLSearchParams({ limit: "50", ...query })}`, { signal }, ctx),
  monitoringDetail: <T,>(ctx: ApiContext, path: string, signal?: AbortSignal) => request<T>(`/api/v1/${path}/`, { signal }, ctx),
  monitoringAction: (ctx: ApiContext, kind: string, id: string, action: string) =>
    request(`/api/v1/metrics/actions/${kind}/${encodeURIComponent(id)}/`, { method: "POST", body: JSON.stringify({ action }) }, ctx),

  resourceMetrics: (
    ctx: ApiContext,
    resourceType: string,
    resourceId: string,
  ) =>
    request<ResourceMetrics>(
      `/api/v1/metrics/?resource=${encodeURIComponent(resourceType)}&id=${encodeURIComponent(resourceId)}`,
      {},
      ctx,
    ),

  alerts: (ctx: ApiContext) => request<AlertRule[]>("/api/v1/alerts/", {}, ctx),

  createAlert: (
    ctx: ApiContext,
    body: {
      metric: string;
      threshold: string;
      operator?: string;
      resource_type?: string;
      resource_id?: string;
      window_seconds?: number;
      cooldown_seconds?: number;
      notification_channels?: Record<string, unknown>;
    },
  ) =>
    request<AlertRule>(
      "/api/v1/alerts/",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  deleteAlert: (ctx: ApiContext, alertId: string) =>
    request<AlertRule>(`/api/v1/alerts/${alertId}/`, { method: "DELETE" }, ctx),

  testAlert: (ctx: ApiContext, alertId: string) =>
    request<AlertEvent>(
      `/api/v1/alerts/${alertId}/test/`,
      { method: "POST" },
      ctx,
    ),

  alertEvents: (ctx: ApiContext) =>
    request<AlertEvent[]>("/api/v1/alerts/events/", {}, ctx),

  reportSchedules: (ctx: ApiContext) =>
    request<ReportSchedule[]>("/api/v1/reports/schedules/", {}, ctx),

  createReportSchedule: (
    ctx: ApiContext,
    body: { email: string; interval: string },
  ) =>
    request<ReportSchedule>(
      "/api/v1/reports/schedules/",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  deleteReportSchedule: (ctx: ApiContext, scheduleId: string) =>
    request<ReportSchedule>(
      `/api/v1/reports/schedules/${scheduleId}/`,
      { method: "DELETE" },
      ctx,
    ),

  sendReportNow: (ctx: ApiContext, scheduleId: string) =>
    request<ReportDelivery>(
      `/api/v1/reports/schedules/${scheduleId}/send-now/`,
      { method: "POST" },
      ctx,
    ),

  reportDeliveries: (ctx: ApiContext) =>
    request<ReportDelivery[]>("/api/v1/reports/deliveries/", {}, ctx),

  auditLogs: (ctx: ApiContext) =>
    request<AuditLog[]>("/api/v1/audit/logs/", {}, ctx),

  auditLog: (ctx: ApiContext, logId: string) =>
    request<AuditLog>(`/api/v1/audit/logs/${logId}/`, {}, ctx),

  agents: (ctx: ApiContext) =>
    request<Agent[]>(
      `/api/v1/agents/?view_scope=${ctx.projectId ? "current" : "all"}`,
      {},
      ctx,
    ),

  agentsPage: async (ctx: ApiContext, query: Record<string, string>, signal?: AbortSignal) => {
    const result = await request<import("./useCursorPage").CursorPage<Agent> | Agent[]>(`/api/v1/agents/?${new URLSearchParams({ limit: "50", ...query, view_scope: ctx.projectId ? "current" : "all" })}`, { signal }, ctx);
    return Array.isArray(result) ? { items: result, next_cursor: null, has_more: false, limit: 50 } : result;
  },

  agentCapabilities: (ctx: ApiContext) =>
    request<{ view: boolean; create: boolean; manage: boolean }>(
      "/api/v1/agents/capabilities/",
      {},
      ctx,
    ),





  issueAgentRunDisplayToken: (ctx: ApiContext, runId: string) =>
    request<{ run_id: string; display_token: string; expires_at: string }>(
      `/api/v1/agent-runs/${runId}/display-token/`,
      { method: "POST" },
      ctx,
    ),

  privateAgentRunDisplay: (
    ctx: ApiContext,
    runId: string,
    displayToken: string,
  ) =>
    request<PrivateAgentRunDisplay>(
      `/api/v1/agent-runs/${runId}/display/`,
      { headers: { "X-Nexus-Agent-Display-Token": displayToken } },
      ctx,
    ),

  privateAgentRunEvents: (
    ctx: ApiContext,
    runId: string,
    displayToken: string,
    cursor = 0,
  ) =>
    request<DisplayStreamEvent[]>(
      `/api/v1/agent-runs/${runId}/events/?cursor=${Math.max(0, cursor)}`,
      { headers: { "X-Nexus-Agent-Display-Token": displayToken } },
      ctx,
    ),

  privateAgentRunOutputs: (
    ctx: ApiContext,
    runId: string,
    displayToken: string,
  ) =>
    request<AgentOutputArtifact[]>(
      `/api/v1/agent-runs/${runId}/outputs/`,
      { headers: { "X-Nexus-Agent-Display-Token": displayToken } },
      ctx,
    ),

  privateAgentRunTerminal: (
    ctx: ApiContext,
    runId: string,
    displayToken: string,
    cursor = 0,
  ) =>
    request<AgentRunTerminal>(
      `/api/v1/agent-runs/${runId}/terminal/?cursor=${cursor}`,
      { headers: { "X-Nexus-Agent-Display-Token": displayToken } },
      ctx,
    ),

  privateAgentRunTerminalTicket: (
    ctx: ApiContext,
    runId: string,
    displayToken: string,
  ) =>
    request<{ ticket: string; expires_in: number; websocket_url: string }>(
      `/api/v1/agent-runs/${runId}/terminal-ticket/`,
      {
        method: "POST",
        headers: { "X-Nexus-Agent-Display-Token": displayToken },
      },
      ctx,
    ),

  privateAgentRunComputerDirectories: (
    ctx: ApiContext,
    runId: string,
    displayToken: string,
    path = "",
  ) =>
    request<import("./types").AgentRunComputerDirectoryListing>(
      `/api/v1/agent-runs/${runId}/computer/${path ? `?path=${encodeURIComponent(path)}` : ""}`,
      { headers: { "X-Nexus-Agent-Display-Token": displayToken } },
      ctx,
    ),

  switchPrivateAgentRunComputer: (ctx: ApiContext, runId: string, displayToken: string, connectionId: string, revision: number) =>
    request<import("./types").PrivateAgentRunDisplay>(`/api/v1/agent-runs/${runId}/computer/`, {
      method: "POST", headers: { "X-Nexus-Agent-Display-Token": displayToken },
      body: JSON.stringify({ connection_id: connectionId, expected_revision: revision }),
    }, ctx),

  updatePrivateAgentRunWorkspaceCwd: (
    ctx: ApiContext,
    runId: string,
    displayToken: string,
    path: string,
  ) =>
    request<import("./types").AgentRunComputerDirectoryListing>(
      `/api/v1/agent-runs/${runId}/computer/`,
      {
        method: "PATCH",
        headers: { "X-Nexus-Agent-Display-Token": displayToken },
        body: JSON.stringify({ path }),
      },
      ctx,
    ),

  answerPrivateAgentRunInteraction: (
    ctx: ApiContext,
    runId: string,
    interactionId: string,
    displayToken: string,
    body: { text: string; value?: string },
  ) =>
    request<import("./types").AgentRunInteraction>(
      `/api/v1/agent-runs/${runId}/interactions/${interactionId}/reply/`,
      {
        method: "POST",
        headers: { "X-Nexus-Agent-Display-Token": displayToken },
        body: JSON.stringify(body),
      },
      ctx,
    ),

  privateAgentRunDisplayAsset: (
    ctx: ApiContext,
    assetPath: string,
    displayToken: string,
  ) =>
    requestBlob(assetPath, ctx, {
      "X-Nexus-Agent-Display-Token": displayToken,
    }),

  startAgentInvocation: (
    ctx: ApiContext,
    agentId: string,
    body: { tool: string; arguments: Record<string, unknown> },
  ) =>
    request<{ run_id: string; display_url: string; status: string }>(
      `/api/v1/agents/${agentId}/invocations/`,
      { method: "POST", body: JSON.stringify(body) },
      ctx,
    ),

  privateAgentRunOutputDownload: (
    ctx: ApiContext,
    runId: string,
    artifactId: string,
    displayToken: string,
  ) =>
    requestBlob(
      `/api/v1/agent-runs/${runId}/outputs/${artifactId}/download/`,
      ctx,
      { "X-Nexus-Agent-Display-Token": displayToken },
    ),

  agentInteractor: (ctx: ApiContext, agentId: string) =>
    request<AgentInteractor>(`/api/v1/agents/${agentId}/interactor/`, {}, ctx),

  agentProjectContextGrant: (ctx: ApiContext, agentId: string) =>
    request<AgentInteractor["project_context"]>(`/api/v1/agents/${agentId}/project-context-grant/`, {}, ctx),

  authorizeAgentProjectContext: (ctx: ApiContext, agentId: string) =>
    request<AgentInteractor["project_context"]>(`/api/v1/agents/${agentId}/project-context-grant/`, { method: "PUT", body: JSON.stringify({}) }, ctx),

  revokeAgentProjectContext: (ctx: ApiContext, agentId: string) =>
    request<void>(`/api/v1/agents/${agentId}/project-context-grant/`, { method: "DELETE" }, ctx),

  createPrivateAgentRun: async (
    ctx: ApiContext,
    agentId: string,
    input: { content?: string; arguments?: Record<string, unknown>; attachments?: Array<{ asset_id: string }>; files?: string[]; audio?: string[]; execution_profile_id?: string; reasoning_effort?: string },
    toolName = "",
  ) => {
    type StartResponse = {
      run: AgentPrivateRun;
      run_id: string;
      display_url: string;
      status: string;
    };
    const response = await request<StartResponse>(
      `/api/v1/agents/${agentId}/private-runs/`,
      {
        method: "POST",
        body: JSON.stringify({ ...input, tool_name: toolName }),
      },
      ctx,
    );
    if (!response?.run?.id || !response.run_id || !response.display_url)
      throw new ApiError(
        "The Nexus server is not upgraded for Run-only Private Display.",
        { code: "PRIVATE_RUN_SERVER_UPGRADE_REQUIRED" },
      );
    return response;
  },

  resumePrivateAgentRun: async (
    ctx: ApiContext,
    runId: string,
    displayToken: string,
    input: { content?: string; arguments?: Record<string, unknown>; attachments?: Array<{ asset_id: string }>; files?: string[]; audio?: string[]; execution_profile_id?: string; reasoning_effort?: string },
  ) => {
    type ResumeResponse = {
      run: AgentPrivateRun;
      run_id: string;
      display_url: string;
      status: string;
      resumed: boolean;
    };
    const response = await request<ResumeResponse>(
      `/api/v1/agent-runs/${runId}/resume/`,
      {
        method: "POST",
        headers: { "X-Nexus-Agent-Display-Token": displayToken },
        body: JSON.stringify(input),
      },
      ctx,
    );
    if (!response?.run?.id || response.run_id !== runId || !response.resumed)
      throw new ApiError("Nexus could not resume the selected Run.", {
        code: "PRIVATE_RUN_RESUME_FAILED",
      });
    return response;
  },

  submitAgentRunFollowUp: (ctx: ApiContext, runId: string, displayToken: string,
    input: { mode: "queue" | "steer"; content: string; turn_index: number; idempotency_key: string; attachments?: { asset_id: string }[]; files?: string[] }) =>
    request(`/api/v1/agent-runs/${runId}/follow-ups/`, {
      method: "POST", headers: { "X-Nexus-Agent-Display-Token": displayToken }, body: JSON.stringify(input),
    }, ctx),
  agentRunFollowUpDelivery: async (ctx: ApiContext, runId: string, displayToken: string, key: string, signal?: AbortSignal) => {
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) abort();
    signal?.addEventListener("abort", abort, { once: true });
    const timeout = setTimeout(abort, 5000);
    try {
      const result = await request<{ submission: AgentRunFollowUp | null }>(
        `/api/v1/agent-runs/${runId}/follow-ups/?idempotency_key=${encodeURIComponent(key)}`,
        { signal: controller.signal, headers: { "X-Nexus-Agent-Display-Token": displayToken } }, ctx);
      // Older servers return the list here. That is not proof of delivery.
      if (!result || !("submission" in result)) throw new Error("Delivery lookup is unavailable.");
      return result.submission;
    } finally {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", abort);
    }
  },
  cancelAgentRunFollowUp: (ctx: ApiContext, runId: string, displayToken: string, id: string) =>
    request(`/api/v1/agent-runs/${runId}/follow-ups/${id}/`, {
      method: "DELETE", headers: { "X-Nexus-Agent-Display-Token": displayToken },
    }, ctx),

  editAgentRunQueue: (ctx: ApiContext, runId: string, displayToken: string,
    change: { expected_revision: string; content?: string; ids?: string[] }, messageId?: string) =>
    request<NonNullable<PrivateAgentRunDisplay["follow_up"]>>(`/api/v1/agent-runs/${runId}/follow-ups/${messageId ? `${messageId}/` : ""}`, {
      method: "PATCH", headers: { "X-Nexus-Agent-Display-Token": displayToken }, body: JSON.stringify(change),
    }, ctx),

  privateAgentRuns: (
    ctx: ApiContext,
    agentId: string,
    query = "",
    cursor = "",
    attention = "all",
    limit = 30,
  ) =>
    request<AgentPrivateRunPage>(
      `/api/v1/agents/${agentId}/private-runs/?limit=${encodeURIComponent(String(limit))}&attention=${encodeURIComponent(attention)}${query ? `&q=${encodeURIComponent(query)}` : ""}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
      {},
      ctx,
    ),

  markPrivateRunRead: (ctx: ApiContext, runId: string, displayToken: string, completedAt: string) =>
    request<{ run_id: string; completion_unread: boolean }>(`/api/v1/agent-runs/${runId}/read/`, {
      method: "POST", headers: { "X-Nexus-Agent-Display-Token": displayToken }, body: JSON.stringify({ completed_at: completedAt }),
    }, ctx),

  privateRunCapacityRecovery: (ctx: ApiContext, agentId: string, excludeRunId: string) =>
    request<{ capacity: { used: string; limit: string | null; remaining: string | null; state: string }; eligible_count: number;
      batch_count: number; has_more: boolean; preview_token: string;
      runs: Array<{ id: string; title: string; agent_name: string; hidden: boolean }> }>(
      `/api/v1/agents/${agentId}/private-runs/capacity-recovery/${excludeRunId ? `?exclude_run_id=${encodeURIComponent(excludeRunId)}` : ""}`, {}, ctx),

  stopOtherPrivateRuns: (ctx: ApiContext, agentId: string, excludeRunId: string, previewToken: string) =>
    request<{ stopped: number; pending: number; failed: number; skipped: number; remaining: number;
      capacity: { used: string; limit: string | null; remaining: string | null; state: string } }>(
      `/api/v1/agents/${agentId}/private-runs/capacity-recovery/`, {
        method: "POST", body: JSON.stringify({ exclude_run_id: excludeRunId || null, preview_token: previewToken, confirmation: true }),
      }, ctx),

  renamePrivateAgentRun: async (
    ctx: ApiContext,
    runId: string,
    title: string,
    displayToken = "",
  ) => {
    const token =
      displayToken ||
      (await api.issueAgentRunDisplayToken(ctx, runId)).display_token;
    return request<AgentPrivateRun>(
      `/api/v1/agent-runs/${runId}/display/`,
      {
        method: "PATCH",
        headers: { "X-Nexus-Agent-Display-Token": token },
        body: JSON.stringify({ title }),
      },
      ctx,
    );
  },

  deletePrivateAgentRun: async (
    ctx: ApiContext,
    runId: string,
    displayToken = "",
  ) => {
    const token =
      displayToken ||
      (await api.issueAgentRunDisplayToken(ctx, runId)).display_token;
    return request<void>(
      `/api/v1/agent-runs/${runId}/display/`,
      { method: "DELETE", headers: { "X-Nexus-Agent-Display-Token": token } },
      ctx,
    );
  },

  cancelPrivateAgentRun: (
    ctx: ApiContext,
    runId: string,
    displayToken: string,
  ) =>
    request<AgentPrivateRun>(
      `/api/v1/agent-runs/${runId}/cancel/`,
      {
        method: "POST",
        headers: { "X-Nexus-Agent-Display-Token": displayToken },
      },
      ctx,
    ),

  decidePrivateAgentRecovery: (
    ctx: ApiContext,
    runId: string,
    displayToken: string,
    action: "check_status" | "retry" | "cancel",
  ) => request<{ state: string; attempt?: number; unknown_operations?: number }>(
    `/api/v1/agent-runs/${runId}/recovery/`,
    {
      method: "POST",
      headers: { "X-Nexus-Agent-Display-Token": displayToken },
      body: JSON.stringify({ action }),
    },
    ctx,
  ),


  createAgent: (
    ctx: ApiContext,
    body: { name: string; ownership: ResourceOwnershipInput },
  ) =>
    request<Agent>(
      "/api/v1/agents/",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  agent: (ctx: ApiContext, agentId: string) =>
    request<Agent>(`/api/v1/agents/${agentId}/`, {}, ctx),

  updateAgent: (
    ctx: ApiContext,
    agentId: string,
    body: {
      name?: string;
      status?: string;
      project_id?: string | null;
      team_id?: string | null;
      computer_requirement?: "required" | "optional" | "disabled";
      workspace_capabilities?: string[];
      mobile_requirement?: "required" | "optional" | "disabled";
      mobile_capabilities?: string[];
      mobile_policy_source?: "sdk" | "cloud";
    },
  ) =>
    request<Agent>(
      `/api/v1/agents/${agentId}/`,
      { method: "PATCH", body: JSON.stringify(body) },
      ctx,
    ),

  deleteAgent: (ctx: ApiContext, agentId: string) =>
    request<void>(`/api/v1/agents/${agentId}/`, { method: "DELETE" }, ctx),

  agentWorkspaceGrant: (ctx: ApiContext, agentId: string) =>
    request<AgentWorkspaceGrant>(
      `/api/v1/agents/${agentId}/workspace-grant/`,
      {},
      ctx,
    ),

  setAgentWorkspaceGrant: (
    ctx: ApiContext,
    agentId: string,
    scopes: string[],
  ) =>
    request<AgentWorkspaceGrant>(
      `/api/v1/agents/${agentId}/workspace-grant/`,
      { method: "PUT", body: JSON.stringify({ scopes }) },
      ctx,
    ),

  agentComputerBindings: (ctx: ApiContext, agentId: string) =>
    request<AgentComputerBinding[]>(
      `/api/v1/agents/${agentId}/computer-bindings/`,
      {},
      ctx,
    ),

  createAgentComputerBinding: (
    ctx: ApiContext,
    agentId: string,
    connectionId: string,
    isDefault = true,
  ) =>
    request<AgentComputerBinding>(
      `/api/v1/agents/${agentId}/computer-bindings/`,
      {
        method: "POST",
        body: JSON.stringify({
          connection_id: connectionId,
          is_default: isDefault,
        }),
      },
      ctx,
    ),

  deleteAgentComputerBinding: (
    ctx: ApiContext,
    agentId: string,
    bindingId: string,
  ) =>
    request<void>(
      `/api/v1/agents/${agentId}/computer-bindings/${bindingId}/`,
      { method: "DELETE" },
      ctx,
    ),

  revokeAgentWorkspaceGrant: (ctx: ApiContext, agentId: string) =>
    request<void>(
      `/api/v1/agents/${agentId}/workspace-grant/`,
      { method: "DELETE" },
      ctx,
    ),

  cloneAgent: (
    ctx: ApiContext,
    agentId: string,
    body: { name?: string } = {},
  ) =>
    request<Agent>(
      `/api/v1/agents/${agentId}/clone/`,
      { method: "POST", body: JSON.stringify(body) },
      ctx,
    ),


  initAgentRepo: (ctx: ApiContext, agentId: string) =>
    request<Agent>(
      `/api/v1/agents/${agentId}/repo/init/`,
      { method: "POST" },
      ctx,
    ),

  pushAgentRepo: async (
    ctx: ApiContext,
    agentId: string,
    file: File,
    commitId: string,
  ) => {
    const formData = new FormData();
    formData.append("file", file, file.name);
    formData.append("commit_id", commitId);
    const response = await fetch(`/api/v1/agents/${agentId}/repo/push/`, {
      method: "POST",
      headers: {
        ...(ctx.token ? { Authorization: `Bearer ${ctx.token}` } : {}),
        ...(ctx.tenantId ? { "X-Nexus-Tenant": ctx.tenantId } : {}),
        ...(ctx.projectId ? { "X-Nexus-Project": ctx.projectId } : {}),
        Accept: "application/json",
      },
      body: formData,
    });
    return parseEnvelope<Agent>(response);
  },

  agentVersions: (ctx: ApiContext, agentId: string) =>
    request<AgentVersion[]>(`/api/v1/agents/${agentId}/versions/`, {}, ctx),

  publishAgentVersion: (ctx: ApiContext, agentId: string, releaseNotes = "") =>
    request<AgentVersion>(
      `/api/v1/agents/${agentId}/versions/publish/`,
      { method: "POST", body: JSON.stringify({ release_notes: releaseNotes }) },
      ctx,
    ),

  rollbackAgentVersion: (ctx: ApiContext, agentId: string, version: string) =>
    request<AgentVersion>(
      `/api/v1/agents/${agentId}/versions/${encodeURIComponent(version)}/rollback/`,
      { method: "POST" },
      ctx,
    ),

  deployAgent: (ctx: ApiContext, agentId: string, body: { env: string }) =>
    request<AgentDeployment>(
      `/api/v1/agents/${agentId}/deployments/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  stopAgent: (ctx: ApiContext, agentId: string, body: { env: string }) =>
    request<AgentDeployment>(
      `/api/v1/agents/${agentId}/deployments/stop/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  agentLogs: (ctx: ApiContext, agentId: string, tail = 100) =>
    request<AgentLog[]>(
      `/api/v1/agents/${agentId}/logs/?tail=${tail}`,
      {},
      ctx,
    ),

  agentStatus: (ctx: ApiContext, agentId: string) =>
    request<AgentStatus>(`/api/v1/agents/${agentId}/status/`, {}, ctx),

  agentMemory: (ctx: ApiContext, agentId: string) =>
    request<AgentMemoryItem[]>(`/api/v1/agents/${agentId}/memory/`, {}, ctx),

  agentObservabilityPage: <T>(ctx: ApiContext, agentId: string, path: string, params: Record<string, string>) =>
    request<{ items: T[]; next_cursor: string | null }>(
      `/api/v1/agents/${agentId}/observability/${path}/?${new URLSearchParams(params)}`, {}, ctx,
    ),

  agentDisplayRuns: (ctx: ApiContext, agentId: string) =>
    request<AgentDisplayRun[]>(
      `/api/v1/agents/${agentId}/display-runs/`,
      {},
      ctx,
    ),

  agentDisplayRunEvents: (
    ctx: ApiContext,
    agentId: string,
    runId: string,
    cursor = 0,
    limit = 200,
  ) =>
    request<import("./types").AgentDisplayEvent[]>(
      `/api/v1/agents/${agentId}/display-runs/${runId}/events/?cursor=${cursor}&limit=${limit}`,
      {},
      ctx,
    ),

  redactAgentDisplayRun: (ctx: ApiContext, agentId: string, runId: string) =>
    request<AgentDisplayRun>(
      `/api/v1/agents/${agentId}/display-runs/${runId}/redact/`,
      { method: "POST" },
      ctx,
    ),

  agentOutputArtifacts: (ctx: ApiContext, agentId: string, runId: string) =>
    request<AgentOutputArtifact[]>(
      `/api/v1/agents/${agentId}/display-runs/${runId}/outputs/`,
      {},
      ctx,
    ),

  scanAgentOutputArtifact: (
    ctx: ApiContext,
    agentId: string,
    runId: string,
    artifactId: string,
  ) =>
    request<AgentOutputArtifact>(
      `/api/v1/agents/${agentId}/display-runs/${runId}/outputs/${artifactId}/scan/`,
      { method: "POST" },
      ctx,
    ),

  createAgentMemory: (
    ctx: ApiContext,
    agentId: string,
    body: {
      memory_type: string;
      content_text?: string;
      content_json?: Record<string, unknown>;
      source_run_id?: string | null;
      source_event_ids?: string[];
      confidence?: string;
      sensitivity_level?: string;
      consent_status?: string;
      license_status?: string;
    },
  ) =>
    request<AgentMemoryItem>(
      `/api/v1/agents/${agentId}/memory/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  exportAgentMcp: (ctx: ApiContext, agentId: string) =>
    request<AgentMcpExport>(`/api/v1/agents/${agentId}/mcp/export/`, {}, ctx),

  createAgentKey: (ctx: ApiContext, agentId: string) =>
    request<ApiKeyWithPlaintext>(
      `/api/v1/agents/${agentId}/keys/`,
      { method: "POST" },
      ctx,
    ),

  agentMobileBindings: (ctx: ApiContext, agentId: string) =>
    request<AgentMobileBinding[]>(
      `/api/v1/agents/${agentId}/mobile-bindings/`,
      {},
      ctx,
    ),

  createAgentMobileBinding: (
    ctx: ApiContext,
    agentId: string,
    body: { device_id: string; is_default?: boolean },
  ) =>
    request<AgentMobileBinding>(
      `/api/v1/agents/${agentId}/mobile-bindings/`,
      { method: "POST", body: JSON.stringify(body) },
      ctx,
    ),

  updateAgentMobileBinding: (
    ctx: ApiContext,
    agentId: string,
    bindingId: string,
    body: { is_default: boolean },
  ) =>
    request<AgentMobileBinding>(
      `/api/v1/agents/${agentId}/mobile-bindings/${bindingId}/`,
      { method: "PATCH", body: JSON.stringify(body) },
      ctx,
    ),

  deleteAgentMobileBinding: (
    ctx: ApiContext,
    agentId: string,
    bindingId: string,
  ) =>
    request<void>(
      `/api/v1/agents/${agentId}/mobile-bindings/${bindingId}/`,
      { method: "DELETE" },
      ctx,
    ),

  agentMobileGrant: (ctx: ApiContext, agentId: string) =>
    request<AgentMobileGrant>(
      `/api/v1/agents/${agentId}/mobile-grant/`,
      {},
      ctx,
    ),

  setAgentMobileGrant: (ctx: ApiContext, agentId: string, scopes: string[]) =>
    request<AgentMobileGrant>(
      `/api/v1/agents/${agentId}/mobile-grant/`,
      { method: "PUT", body: JSON.stringify({ scopes }) },
      ctx,
    ),

  revokeAgentMobileGrant: (ctx: ApiContext, agentId: string) =>
    request<void>(
      `/api/v1/agents/${agentId}/mobile-grant/`,
      { method: "DELETE" },
      ctx,
    ),



  setAgentResources: (
    ctx: ApiContext,
    agentId: string,
    body: { cpu: string; memory: string },
  ) =>
    request<AgentResourceConfig>(
      `/api/v1/agents/${agentId}/resources/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  agentRuntimeImages: (ctx: ApiContext, agentId: string) =>
    request<AgentRuntimeImage[]>(
      `/api/v1/agents/${agentId}/runtime/images/`,
      {},
      ctx,
    ),

  agentPythonBuilds: async (ctx: ApiContext, agentId: string) => {
    const result = await request<import("./types").AgentPythonBuildList>(`/api/v1/agents/${agentId}/runtime/python-builds/`, {}, ctx);
    if (!result?.configuration || !Array.isArray(result.results)) throw new ApiError("Python build service is unavailable.");
    return result;
  },

  deleteAgentPythonBuilds: (ctx: ApiContext, agentId: string, buildIds: string[]) =>
    request<{ deleted_ids: string[]; cleanup_pending: boolean }>(
      buildIds.length === 1
        ? `/api/v1/agents/${agentId}/runtime/python-builds/${buildIds[0]}/`
        : `/api/v1/agents/${agentId}/runtime/python-builds/delete-failed/`,
      buildIds.length === 1 ? { method: "DELETE" } : { method: "POST", body: JSON.stringify({ build_ids: buildIds }) }, ctx,
    ),

  uploadAgentPython: async (ctx: ApiContext, agentId: string, file: File, requirements: File | null, entrypoint: string, secrets: Record<string, string>) => {
    const form = new FormData();
    form.append("file", file);
    if (requirements) form.append("requirements", requirements);
    if (entrypoint.trim()) form.append("entrypoint", entrypoint.trim());
    form.append("secrets", JSON.stringify(secrets));
    const response = await fetch(`/api/v1/agents/${agentId}/runtime/python-builds/`, {
      method: "POST", credentials: "same-origin", headers: { ...contextHeaders(ctx, "POST"), Accept: "application/json" }, body: form,
    });
    return parseEnvelope<import("./types").AgentPythonBuild>(response);
  },

  registerAgentRuntimeImage: (
    ctx: ApiContext,
    agentId: string,
    body: {
      image_ref?: string;
      image_digest?: string;
      version?: string;
      registry_secret_ref?: string;
    },
  ) =>
    request<AgentRuntimeImage>(
      `/api/v1/agents/${agentId}/runtime/images/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  uploadAgentRuntimeImage: async (
    ctx: ApiContext,
    agentId: string,
    file: File,
    imageRef?: string,
  ) => {
    const formData = new FormData();
    formData.append("file", file, file.name);
    if (imageRef?.trim()) formData.append("image_ref", imageRef.trim());
    const response = await fetch(`/api/v1/agents/${agentId}/runtime/images/`, {
      method: "POST",
      headers: {
        ...(ctx.token ? { Authorization: `Bearer ${ctx.token}` } : {}),
        ...(ctx.tenantId ? { "X-Nexus-Tenant": ctx.tenantId } : {}),
        ...(ctx.projectId ? { "X-Nexus-Project": ctx.projectId } : {}),
        Accept: "application/json",
      },
      body: formData,
    });
    return parseEnvelope<AgentRuntimeImage>(response);
  },

  setCurrentAgentRuntimeImage: (
    ctx: ApiContext,
    agentId: string,
    imageId: string,
  ) =>
    request<AgentRuntimeImage>(
      `/api/v1/agents/${agentId}/runtime/images/${imageId}/set-current/`,
      {
        method: "POST",
      },
      ctx,
    ),

  deleteAgentRuntimeImage: (ctx: ApiContext, agentId: string, imageId: string) =>
    request<{ deleted_id: string; default_image_id: string | null; artifacts_retained: boolean }>(
      `/api/v1/agents/${agentId}/runtime/images/${imageId}/`, { method: "DELETE" }, ctx,
    ),

  deployAgentRuntime: (
    ctx: ApiContext,
    agentId: string,
    body: {
      env: string;
      image_id?: string;
      workspace_connection_id?: string | null;
      workspace_root?: string;
      workspace_access_mode?: "read_only" | "read_write";
    },
  ) =>
    request<AgentRuntimeDeployment>(
      `/api/v1/agents/${agentId}/runtime/deployments/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  stopAgentRuntime: (ctx: ApiContext, agentId: string, body: { env: string }) =>
    request<AgentRuntimeDeployment>(
      `/api/v1/agents/${agentId}/runtime/deployments/stop/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  agentRuntimeStatus: (ctx: ApiContext, agentId: string) =>
    request<AgentRuntimeStatus>(
      `/api/v1/agents/${agentId}/runtime/status/`,
      {},
      ctx,
    ),

  healthCheckAgentRuntime: (
    ctx: ApiContext,
    agentId: string,
    body: { env: string },
  ) =>
    request<AgentRuntimeDeployment>(
      `/api/v1/agents/${agentId}/runtime/health-check/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  exportAgentRuntimeMcp: (ctx: ApiContext, agentId: string) =>
    request<Record<string, unknown>>(
      `/api/v1/agents/${agentId}/runtime/mcp/export/`,
      {},
      ctx,
    ),

  edgeRouterCapabilities: (ctx: ApiContext) =>
    request<EdgeRouterCapabilities>("/api/v1/edge/capabilities/", {}, ctx),

  edgeRelayService: (ctx: ApiContext) =>
    request<EdgeRelayServiceStatus>("/api/v1/edge/relay-service/", {}, ctx),

  setEdgeRelayService: (ctx: ApiContext, enabled: boolean) =>
    request<EdgeRelayServiceStatus>(
      "/api/v1/edge/relay-service/",
      { method: "POST", body: JSON.stringify({ enabled }) },
      ctx,
    ),

  edgeNodes: (
    ctx: ApiContext,
    filters: { scope?: "own" | "admin"; projectId?: string } = {},
  ) => {
    const params = new URLSearchParams();
    params.set("view_scope", ctx.projectId ? "current" : "all");
    if (filters.scope === "admin") params.set("scope", "admin");
    if (filters.projectId) params.set("project_id", filters.projectId);
    const suffix = params.toString();
    return request<EdgeNode[]>(
      `/api/v1/edge/nodes/${suffix ? `?${suffix}` : ""}`,
      {},
      ctx,
    );
  },

  edgeNode: (ctx: ApiContext, nodeId: string, scope: "own" | "admin" = "own") =>
    request<EdgeNodeDetail>(
      `/api/v1/edge/nodes/${nodeId}/${scope === "admin" ? "?scope=admin" : ""}`,
      {},
      ctx,
    ),

  updateEdgeNode: (
    ctx: ApiContext,
    nodeId: string,
    body: { display_name?: string; project_id?: string | null },
  ) =>
    request<EdgeNodeDetail>(
      `/api/v1/edge/nodes/${nodeId}/`,
      { method: "PATCH", body: JSON.stringify(body) },
      ctx,
    ),

  revokeEdgeNode: (
    ctx: ApiContext,
    nodeId: string,
    scope: "own" | "admin" = "own",
  ) =>
    request<null>(
      `/api/v1/edge/nodes/${nodeId}/revoke/${scope === "admin" ? "?scope=admin" : ""}`,
      { method: "POST" },
      ctx,
    ),

  availableEdgeAgents: (ctx: ApiContext) =>
    request<EdgeAgentRegistration[]>(
      "/api/v1/edge/agent-registrations/?available=1",
      {},
      ctx,
    ),

  resumeManagedEdgeAgent: (ctx: ApiContext, registrationId: string) =>
    request<EdgeAgentRegistration>(
      `/api/v1/edge/agent-registrations/${registrationId}/resume-managed/`,
      { method: "POST", body: JSON.stringify({}) },
      ctx,
    ),

  createEdgePairingCode: (
    ctx: ApiContext,
    body: {
      expires_in_seconds?: number;
      project_id?: string | null;
      ownership?: ResourceOwnershipInput;
    } = {},
  ) =>
    request<{ pairing_code: string; pairing_url?: string; expires_at: string; project_id: string }>(
      "/api/v1/edge/pairing-codes/",
      {
        method: "POST",
        body: JSON.stringify({ expires_in_seconds: 600, include_pairing_link: true, ...body }),
      },
      ctx,
    ),

  bindAgentEdgeRuntime: (
    ctx: ApiContext,
    agentId: string,
    registrationId: string,
    env = "prod",
  ) =>
    request<AgentRuntimeDeployment>(
      `/api/v1/agents/${agentId}/runtime/edge-binding/`,
      {
        method: "POST",
        body: JSON.stringify({ registration_id: registrationId, env }),
      },
      ctx,
    ),

  disconnectAgentEdgeRuntime: (
    ctx: ApiContext,
    agentId: string,
    env = "prod",
  ) =>
    request<AgentRuntimeDeployment>(
      `/api/v1/agents/${agentId}/runtime/edge-binding/?env=${encodeURIComponent(env)}`,
      { method: "DELETE" },
      ctx,
    ),

  datasetImports: (ctx: ApiContext, datasetId: string, cursor = "") =>
    request<import("./datasetJobs").DatasetPage<import("./datasetJobs").DatasetImportJob>>(
      `/api/v1/datasets/${datasetId}/imports/?limit=20&cursor=${encodeURIComponent(cursor)}`, {}, ctx,
    ),
  createDatasetImport: (ctx: ApiContext, datasetId: string, body: {
    kind: "trace" | "memory" | "artifact"; inputs: Record<string, unknown>; request_key: string;
  }) => request<import("./datasetJobs").DatasetImportJob>(`/api/v1/datasets/${datasetId}/imports/`,
    { method: "POST", body: JSON.stringify(body) }, ctx),
  changeDatasetImport: (ctx: ApiContext, datasetId: string, jobId: string, action: "cancel" | "retry") =>
    request<import("./datasetJobs").DatasetImportJob>(`/api/v1/datasets/${datasetId}/imports/${jobId}/`,
      { method: "POST", body: JSON.stringify({ action }) }, ctx),
  datasetPage: (ctx: ApiContext, cursor = "", q = "") =>
    request<import("./datasetJobs").DatasetPage<Dataset>>(
      `/api/v1/datasets/?view_scope=${ctx.projectId ? "current" : "all"}&limit=30&cursor=${encodeURIComponent(cursor)}&q=${encodeURIComponent(q)}`, {}, ctx,
    ),
  datasetFilePage: (ctx: ApiContext, datasetId: string, cursor = "") =>
    request<DatasetPullResult & { total: number; next_cursor: string | null; summary: Record<string, number> }>(
      `/api/v1/datasets/${datasetId}/pull/?limit=50&cursor=${encodeURIComponent(cursor)}`, {}, ctx,
    ),
  datasets: (ctx: ApiContext) =>
    request<Dataset[]>(
      `/api/v1/datasets/?view_scope=${ctx.projectId ? "current" : "all"}`,
      {},
      ctx,
    ),

  datasetCapabilities: (ctx: ApiContext) =>
    request<DatasetCapabilities>("/api/v1/datasets/capabilities/", {}, ctx),








  createDataset: (
    ctx: ApiContext,
    body: { name: string; ownership: ResourceOwnershipInput },
  ) =>
    request<Dataset>(
      "/api/v1/datasets/",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  dataset: (ctx: ApiContext, datasetId: string) =>
    request<Dataset>(`/api/v1/datasets/${datasetId}/`, {}, ctx),

  deleteDataset: (ctx: ApiContext, datasetId: string) =>
    request<Dataset>(
      `/api/v1/datasets/${datasetId}/`,
      { method: "DELETE" },
      ctx,
    ),

  renameDataset: (ctx: ApiContext, datasetId: string, body: { name: string }) =>
    request<Dataset>(
      `/api/v1/datasets/${datasetId}/rename/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),


  pullDataset: (ctx: ApiContext, datasetId: string) =>
    request<DatasetPullResult>(`/api/v1/datasets/${datasetId}/pull/`, {}, ctx),

  prepareDatasetDownload: (ctx: ApiContext, path: string) =>
    request<{ url: string; file_name: string; size_bytes: number; expires_in: number }>(
      path, { method: "POST", body: "{}" }, ctx,
    ),

  agentFileLimits: (ctx: ApiContext) => request<{ max_file_bytes: number; chunk_bytes: number; max_files: number }>("/api/v1/agent-files/", {}, ctx),
  createAgentFile: (ctx: ApiContext, agentId: string, file: File, sourceKind: "upload" | "audio" = "upload") => request<AgentFileTransfer>("/api/v1/agent-files/", {
    method: "POST", body: JSON.stringify({ agent_id: agentId, name: file.name, size_bytes: file.size, content_type: file.type || "application/octet-stream", source_kind: sourceKind }) }, ctx),
  agentComputerFiles: (ctx: ApiContext, agentId: string, query = "", path = ".") =>
    request<{ path: string; items: Array<{ name: string; path: string; type: string; size_bytes: number; modified_at?: string | null }> }>(
      `/api/v1/agents/${agentId}/computer-files/?q=${encodeURIComponent(query)}&path=${encodeURIComponent(path)}`, {}, ctx),
  importAgentComputerFile: (ctx: ApiContext, agentId: string, path: string, expectedSizeBytes?: number) =>
    request<AgentFileTransfer>(`/api/v1/agents/${agentId}/input-files/import/`, {
      method: "POST", body: JSON.stringify({ path, expected_size_bytes: expectedSizeBytes }),
    }, ctx),
  agentFileStatus: (ctx: ApiContext, id: string) => request<AgentFileTransfer>(`/api/v1/agent-files/${id}/`, {}, ctx),
  putAgentFileChunk: (ctx: ApiContext, id: string, offset: number, bytes: Blob, sha256: string, signal?: AbortSignal) =>
    request<AgentFileTransfer>(`/api/v1/agent-files/${id}/`, { method: "PUT", body: bytes, signal,
      headers: { "Content-Type": "application/octet-stream", "X-Nexus-Upload-Offset": String(offset), "X-Nexus-Chunk-SHA256": sha256 } }, ctx),
  completeAgentFile: (ctx: ApiContext, id: string) => request<AgentFileTransfer>(`/api/v1/agent-files/${id}/`, { method: "POST", body: "{}" }, ctx),
  cancelAgentFile: (ctx: ApiContext, id: string) => request<null>(`/api/v1/agent-files/${id}/`, { method: "DELETE" }, ctx),
  privateRunFiles: (ctx: ApiContext, runId: string, token: string) => request<AgentFileTransfer[]>(`/api/v1/agent-runs/${runId}/files/`, { headers: { "X-Nexus-Agent-Display-Token": token } }, ctx),
  prepareAgentDownload: (ctx: ApiContext, path: string, token: string) => request<{ url: string; file_name: string }>(path,
    { method: "POST", body: "{}", headers: { "X-Nexus-Agent-Display-Token": token } }, ctx),

  previewDatasetImage: (ctx: ApiContext, path: string) => {
    if (!/^\/api\/v1\/datasets\/[a-zA-Z0-9/-]+\/download\/$/.test(path)) {
      throw new Error("Invalid image preview address.");
    }
    return requestBlob(`${path}?preview=1`, ctx);
  },

  pushDatasetFile: async (ctx: ApiContext, datasetId: string, file: File, options: { rightsConfirmed: boolean; requestKey: string }) => {
    const formData = new FormData();
    formData.append("file", file, file.name);
    formData.append("rights_confirmed", String(options.rightsConfirmed));
    const response = await fetch(`/api/v1/datasets/${datasetId}/push/`, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        ...multipartHeaders(ctx),
        "Idempotency-Key": options.requestKey,
      },
      body: formData,
    });
    return parseEnvelope<DatasetFile>(response);
  },

  exportAgentTraceToDataset: (
    ctx: ApiContext,
    datasetId: string,
    body: { agent_id: string; run_id: string },
  ) =>
    request<DatasetFile>(
      `/api/v1/datasets/${datasetId}/agent-assets/trace/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  exportAgentMemoryToDataset: (
    ctx: ApiContext,
    datasetId: string,
    body: { agent_id: string; memory_item_ids?: string[] },
  ) =>
    request<DatasetFile>(
      `/api/v1/datasets/${datasetId}/agent-assets/memory/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  captureAgentArtifactToDataset: (
    ctx: ApiContext,
    datasetId: string,
    body: {
      agent_id: string;
      artifact_id: string;
    },
  ) =>
    request<DatasetFile>(
      `/api/v1/datasets/${datasetId}/agent-assets/artifact/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  datasetVersions: (ctx: ApiContext, datasetId: string) =>
    request<DatasetVersion[]>(
      `/api/v1/datasets/${datasetId}/versions/`,
      {},
      ctx,
    ),
  datasetVersionPage: (ctx: ApiContext, datasetId: string, cursor = "") =>
    request<import("./datasetJobs").DatasetPage<DatasetVersion>>(
      `/api/v1/datasets/${datasetId}/versions/?limit=20&cursor=${encodeURIComponent(cursor)}`, {}, ctx),
  datasetVersionFilePage: (ctx: ApiContext, datasetId: string, versionId: string, cursor = "") =>
    request<import("./datasetJobs").DatasetPage<import("./types").DatasetVersionFileSnapshot>>(
      `/api/v1/datasets/${datasetId}/versions/${versionId}/files/?limit=50&cursor=${encodeURIComponent(cursor)}`, {}, ctx),

  createDatasetVersion: (
    ctx: ApiContext,
    datasetId: string,
    releaseNotes = "",
  ) =>
    request<DatasetVersion>(
      `/api/v1/datasets/${datasetId}/versions/?limit=20`,
      { method: "POST", body: JSON.stringify({ release_notes: releaseNotes }) },
      ctx,
    ),


  setDatasetQuota: (
    ctx: ApiContext,
    datasetId: string,
    body: { max_size: string },
  ) =>
    request<DatasetQuota>(
      `/api/v1/datasets/${datasetId}/quota/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  searchDatasets: (
    ctx: ApiContext,
    keyword: string,
    scope: "metadata" | "content" | "all",
  ) =>
    request<Dataset[] | DatasetContentSearchResult[] | DatasetSearchAllResult>(
      `/api/v1/datasets/search/?q=${encodeURIComponent(keyword)}&scope=${encodeURIComponent(scope)}`,
      {},
      ctx,
    ),

  searchDatasetContent: (ctx: ApiContext, keyword: string) =>
    request<DatasetContentSearchResult[]>(
      `/api/v1/datasets/content-search/?q=${encodeURIComponent(keyword)}`,
      {},
      ctx,
    ),

  mediaAssets: (ctx: ApiContext) =>
    request<MediaAsset[]>("/api/v1/media/assets/", {}, ctx),

  uploadMediaAsset: async (
    ctx: ApiContext,
    file: File,
    body: { purpose: string; expires_at?: string },
    signal?: AbortSignal,
  ) => {
    const formData = new FormData();
    formData.append("file", file, file.name);
    formData.append("purpose", body.purpose);
    if (body.expires_at) formData.append("expires_at", body.expires_at);
    const response = await fetch("/api/v1/media/assets/", {
      method: "POST",
      headers: { ...contextHeaders(ctx, "POST"), Accept: "application/json" },
      credentials: "same-origin",
      body: formData,
      signal,
    });
    return parseEnvelope<MediaAsset>(response);
  },

  mediaAsset: (ctx: ApiContext, assetId: string) =>
    request<MediaAsset>(`/api/v1/media/assets/${assetId}/`, {}, ctx),

  deleteMediaAsset: (ctx: ApiContext, assetId: string) =>
    request<MediaAsset>(
      `/api/v1/media/assets/${assetId}/`,
      { method: "DELETE" },
      ctx,
    ),

  mediaSignedUrl: (
    ctx: ApiContext,
    assetId: string,
    body: { expires_in?: number },
  ) =>
    request<MediaSignedUrl>(
      `/api/v1/media/assets/${assetId}/signed-url/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  jobs: (
    ctx: ApiContext,
    filters: {
      job_type?: string;
      status?: string;
      resource_type?: string;
      resource_id?: string;
    } = {},
  ) => {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    const suffix = params.toString();
    return request<Job[]>(
      `/api/v1/jobs/${suffix ? `?${suffix}` : ""}`,
      {},
      ctx,
    );
  },

  job: (ctx: ApiContext, jobId: string) =>
    request<Job>(`/api/v1/jobs/${jobId}/`, {}, ctx),

  jobEvents: (ctx: ApiContext, jobId: string) =>
    request<JobEvent[]>(`/api/v1/jobs/${jobId}/events/`, {}, ctx),

  workspaceConnections: (ctx: ApiContext) =>
    request<WorkspaceConnection[]>("/api/v1/workspace-connections/", {}, ctx),

  computerRuntimePairingCode: (
    ctx: ApiContext,
    body: {
      name?: string;
      project_id?: string | null;
      workspace_root?: string;
    } = {},
  ) =>
    request<import("./types").ComputerRuntimePairing>(
      "/api/v1/computers/pairing-codes/",
      { method: "POST", body: JSON.stringify(body) },
      ctx,
    ),

  revokeComputerRuntime: (ctx: ApiContext, connectionId: string) =>
    request<WorkspaceConnection>(
      `/api/v1/computers/${connectionId}/revoke/`,
      { method: "POST", body: JSON.stringify({}) },
      ctx,
    ),

  createWorkspaceConnection: (
    ctx: ApiContext,
    body: {
      name: string;
      ssh_host: string;
      ssh_port?: number;
      ssh_user: string;
      auth_mode: string;
      private_key?: string;
      password?: string;
      project_id?: string | null;
      workspace_root?: string;
      metadata?: Record<string, unknown>;
    },
  ) =>
    request<WorkspaceConnection>(
      "/api/v1/workspace-connections/",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  validateWorkspaceConnection: (
    ctx: ApiContext,
    body: {
      connection_id?: string;
      name: string;
      ssh_host: string;
      ssh_port?: number;
      ssh_user: string;
      auth_mode: string;
      private_key?: string;
      password?: string;
      project_id?: string | null;
    },
  ) =>
    request<WorkspaceConnectionTestResult>(
      "/api/v1/workspace-connections/validate/",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  updateWorkspaceConnection: (
    ctx: ApiContext,
    connectionId: string,
    body: {
      name?: string;
      ssh_host?: string;
      ssh_port?: number;
      ssh_user?: string;
      auth_mode?: string;
      private_key?: string;
      password?: string;
      metadata?: Record<string, unknown>;
    },
  ) =>
    request<WorkspaceConnection>(
      `/api/v1/workspace-connections/${connectionId}/`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  testWorkspaceConnection: (ctx: ApiContext, connectionId: string) =>
    request<WorkspaceConnectionTestResult>(
      `/api/v1/workspace-connections/${connectionId}/test/`,
      { method: "POST" },
      ctx,
    ),

  deleteWorkspaceConnection: (ctx: ApiContext, connectionId: string) =>
    request<WorkspaceConnection>(
      `/api/v1/workspace-connections/${connectionId}/`,
      { method: "DELETE" },
      ctx,
    ),

  workspaceTerminalSessions: (ctx: ApiContext) =>
    request<WorkspaceTerminalSession[]>(
      "/api/v1/workspace-terminal-sessions/",
      {},
      ctx,
    ),

  createWorkspaceTerminalSession: (
    ctx: ApiContext,
    body: {
      connection_id: string;
      shell?: string;
      cols?: number;
      rows?: number;
      metadata?: Record<string, unknown>;
    },
  ) =>
    request<WorkspaceTerminalSession>(
      "/api/v1/workspace-terminal-sessions/",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  closeWorkspaceTerminalSession: (ctx: ApiContext, sessionId: string) =>
    request<WorkspaceTerminalSession>(
      `/api/v1/workspace-terminal-sessions/${sessionId}/close/`,
      { method: "POST" },
      ctx,
    ),

  workspaceTerminalTicket: (ctx: ApiContext, sessionId: string) =>
    request<{ ticket: string; expires_in: number; websocket_url: string }>(
      `/api/v1/workspace-terminal-sessions/${sessionId}/ticket/`,
      { method: "POST" },
      ctx,
    ),

  detectWorkspaceTerminalTools: (ctx: ApiContext, sessionId: string) =>
    request<WorkspaceTerminalSession>(
      `/api/v1/workspace-terminal-sessions/${sessionId}/tool-status/`,
      { method: "POST" },
      ctx,
    ),

  workspaceToolConfig: (ctx: ApiContext, sessionId: string, tool = "codex") =>
    request<WorkspaceToolConfig>(
      `/api/v1/workspace-terminal-sessions/${sessionId}/tool-config/?tool=${encodeURIComponent(tool)}`,
      {},
      ctx,
    ),

  workspaceToolConfigOptions: (
    ctx: ApiContext,
    sessionId: string,
    tool = "codex",
  ) =>
    request<WorkspaceToolConfigOptions>(
      `/api/v1/workspace-terminal-sessions/${sessionId}/tool-config/options/?tool=${encodeURIComponent(tool)}`,
      {},
      ctx,
    ),

  previewWorkspaceToolConfig: (
    ctx: ApiContext,
    sessionId: string,
    body: WorkspaceToolConfigChange,
  ) =>
    request<WorkspaceToolConfigPreview>(
      `/api/v1/workspace-terminal-sessions/${sessionId}/tool-config/preview/`,
      { method: "POST", body: JSON.stringify(body) },
      ctx,
    ),

  applyWorkspaceToolConfigV2: (
    ctx: ApiContext,
    sessionId: string,
    body: WorkspaceToolConfigChange,
  ) =>
    request<WorkspaceToolConfig>(
      `/api/v1/workspace-terminal-sessions/${sessionId}/tool-config/apply/`,
      { method: "POST", body: JSON.stringify(body) },
      ctx,
    ),

  recoverWorkspaceToolConfig: (
    ctx: ApiContext,
    sessionId: string,
    body: {
      operation_id: string;
      expected_revision: string;
      action: "recover" | "restore_previous" | "keep_local";
    },
  ) =>
    request<WorkspaceToolConfig>(
      `/api/v1/workspace-terminal-sessions/${sessionId}/tool-config/recovery/`,
      { method: "POST", body: JSON.stringify(body) },
      ctx,
    ),

  applyWorkspaceToolConfig: (
    ctx: ApiContext,
    sessionId: string,
    body: {
      tool?: "codex";
      operation:
        | "api_only"
        | "mcp_add"
        | "mcp_remove"
        | "mcp_replace"
        | "mcp_clear"
        | "full_profile";
      provider_runtime_id?: string;
      agent_id?: string;
      mcp_server_name?: string;
    },
  ) =>
    request<WorkspaceToolConfig>(
      `/api/v1/workspace-terminal-sessions/${sessionId}/tool-config/apply/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  rollbackWorkspaceToolConfig: (
    ctx: ApiContext,
    sessionId: string,
    tool = "codex",
  ) =>
    request<WorkspaceToolConfig>(
      `/api/v1/workspace-terminal-sessions/${sessionId}/tool-config/rollback/`,
      {
        method: "POST",
        body: JSON.stringify({ tool }),
      },
      ctx,
    ),

  mobileDevices: (ctx: ApiContext) =>
    request<MobileDevice[]>("/api/v1/mobile-devices/", {}, ctx),

  startMobileVideo: (ctx: ApiContext, deviceId: string) =>
    request<MobileVideoSession>(`/api/v1/mobile-devices/${deviceId}/video/`, { method: "POST", body: "{}" }, ctx),
  mobileVideo: (ctx: ApiContext, sessionId: string, after: number) =>
    request<MobileVideoSession>(`/api/v1/mobile-video/${sessionId}/?after=${after}`, {}, ctx),
  signalMobileVideo: (ctx: ApiContext, sessionId: string, signal: Record<string, unknown>) =>
    request<MobileVideoSession>(`/api/v1/mobile-video/${sessionId}/`, {
      method: "POST", body: JSON.stringify({ ...signal, message_id: crypto.randomUUID() })
    }, ctx),

  mobileAggregateStatus: (ctx: ApiContext) =>
    request<MobileAggregateStatus>(
      "/api/v1/mobile-devices/aggregate-status/",
      {},
      ctx,
    ),

  createMobileDevice: (
    ctx: ApiContext,
    body: {
      name: string;
      platform?: "android";
      approval_mode?: "manual" | "confirm_high_risk" | "auto";
      device_identifier?: string;
      capabilities?: Record<string, unknown>;
      metadata?: Record<string, unknown>;
      project_id?: string | null;
    },
  ) =>
    request<MobileDeviceWithPairingToken>(
      "/api/v1/mobile-devices/",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  updateMobileDevice: (
    ctx: ApiContext,
    deviceId: string,
    body: {
      name?: string;
      approval_mode?: "manual" | "confirm_high_risk" | "auto";
      capabilities?: Record<string, unknown>;
      metadata?: Record<string, unknown>;
      status?: string;
    },
  ) =>
    request<MobileDevice>(
      `/api/v1/mobile-devices/${deviceId}/`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  deleteMobileDevice: (ctx: ApiContext, deviceId: string) =>
    request<MobileDevice>(
      `/api/v1/mobile-devices/${deviceId}/`,
      { method: "DELETE" },
      ctx,
    ),

  rotateMobileDeviceToken: (ctx: ApiContext, deviceId: string) =>
    request<MobileDeviceWithPairingToken>(
      `/api/v1/mobile-devices/${deviceId}/rotate-token/`,
      { method: "POST" },
      ctx,
    ),

  mobileCommands: (ctx: ApiContext, deviceId: string) =>
    request<MobileCommand[]>(
      `/api/v1/mobile-devices/${deviceId}/commands/`,
      {},
      ctx,
    ),

  mobileScreenshot: (ctx: ApiContext, deviceId: string) =>
    requestBlob(`/api/v1/mobile-devices/${deviceId}/screenshot/`, ctx),
  mobileScreenImage: async (ctx: ApiContext, deviceId: string) => {
    const response = await requestBlobResponse(`/api/v1/mobile-devices/${deviceId}/screenshot/`, ctx);
    return { blob: await response.blob(), frameId: response.headers.get("X-Nexus-Mobile-Screen-Frame") || "" };
  },

  createMobileCommand: (
    ctx: ApiContext,
    deviceId: string,
    body: {
      action:
        | "observe"
        | "capture_screen"
        | "press_home"
        | "press_recents"
        | "long_press"
        | "tap_text"
        | "tap_coordinates"
        | "type_text"
        | "swipe"
        | "press_back"
        | "open_app"
        | "wait_for_state";
      arguments?: Record<string, unknown>;
      risk_level?: "low" | "medium" | "high";
      requires_approval?: boolean;
      ttl_seconds?: number;
    },
  ) =>
    request<MobileCommand>(
      `/api/v1/mobile-devices/${deviceId}/commands/`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

  approveMobileCommand: (ctx: ApiContext, commandId: string) =>
    request<MobileCommand>(
      `/api/v1/mobile-commands/${commandId}/approve/`,
      { method: "POST" },
      ctx,
    ),

  rejectMobileCommand: (ctx: ApiContext, commandId: string) =>
    request<MobileCommand>(
      `/api/v1/mobile-commands/${commandId}/reject/`,
      { method: "POST" },
      ctx,
    ),

  cancelMobileCommand: (ctx: ApiContext, commandId: string) =>
    request<MobileCommand>(
      `/api/v1/mobile-commands/${commandId}/cancel/`,
      { method: "POST" },
      ctx,
    ),

  deleteMobileCommand: (ctx: ApiContext, commandId: string) =>
    request<MobileCommand>(
      `/api/v1/mobile-commands/${commandId}/`,
      { method: "DELETE" },
      ctx,
    ),

  exportMobileMcp: (ctx: ApiContext, deviceId: string) =>
    request<Record<string, unknown>>(
      `/api/v1/mobile-devices/${deviceId}/mcp/export/`,
      {},
      ctx,
    ),

  chatCompletion: (ctx: ApiContext, body: ChatCompletionRequest) =>
    request<Record<string, unknown>>(
      "/api/v1/chat/completions/",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      ctx,
    ),

};

export function workspaceTerminalWebSocketUrl(
  ctx: ApiContext,
  sessionId: string,
) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const params = new URLSearchParams();
  if (ctx.token) params.set("token", ctx.token);
  if (ctx.tenantId) params.set("tenant_id", ctx.tenantId);
  if (ctx.projectId) params.set("project_id", ctx.projectId);
  return `${protocol}//${window.location.host}/ws/workspace-terminals/${sessionId}/?${params.toString()}`;
}

export async function streamChatCompletion(
  ctx: ApiContext,
  body: ChatCompletionRequest,
  onEvent: (event: unknown) => void,
) {
  const response = await fetch("/api/v1/chat/completions/", {
    method: "POST",
    headers: headers(ctx),
    body: JSON.stringify({
      ...body,
      stream: true,
      stream_options: { include_usage: true },
    }),
  });

  if (!response.ok || !response.body) {
    throw new ApiError(`Streaming request failed with ${response.status}`, {
      status: response.status,
    });
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const line = chunk
        .split("\n")
        .map((item) => item.trim())
        .find((item) => item.startsWith("data:"));
      if (!line) continue;
      const data = line.slice(5).trim();
      if (data === "[DONE]") return;
      onEvent(JSON.parse(data));
    }
  }
}
