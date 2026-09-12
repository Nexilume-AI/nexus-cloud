export type Envelope<T> = {
  ok: boolean;
  data: T | null;
  error: null | {
    code: string;
    message: string;
  };
  request_id: string;
};


export type Tenant = {
  id: string;
  name: string;
  slug: string;
  status: string;
};

export type Project = {
  id: string;
  tenant_id: string;
  team_id: string | null;
  name: string;
  instructions_markdown: string;
  instructions_revision: number;
  instructions_updated_at: string | null;
  status: string;
  created_at?: string;
  updated_at?: string;
};

export type ResourceOwnershipInput = {
  scope: "organization" | "project";
  project_id: string | null;
};

export type ResourceOwnership = {
  scope: "organization" | "project" | "unknown";
  project_id: string | null;
  project_name: string | null;
  label: string;
  repair_required?: boolean;
};

export type ResourceAccess = {
  can_discover: boolean;
  can_read: boolean;
  can_use: boolean;
  can_edit: boolean;
  can_manage: boolean;
  sources: Array<
    | "project_membership"
    | "organization_role"
    | "project_role"
    | "resource_share"
    | "creator"
    | string
  >;
};

export type IdentityDisplay = {
  type: string;
  id: string;
  name: string;
  display_name: string;
  email: string;
  avatar_url: string;
  status: string;
  key_prefix?: string;
};

export type UserProfile = {
  user_id: string;
  email: string;
  display_name: string;
  is_superuser: boolean;
  current_tenant: string;
  tenant_id: string;
  roles: string[];
};

export type AccountProfile = {
  user_id: string;
  email: string;
  username: string;
  display_name: string;
  phone: string;
  company: string;
  status: string;
  tenant_id: string;
  project_id: string;
  last_login_at: string | null;
};

export type PasswordResetResult = {
  status: string;
  reset_token_id: string;
  expires_at: string;
};

export type ChangePasswordResult = {
  status: "password_changed" | string;
};


export type Provider = {
  id: string;
  name: string;
  display_name: string;
  status: string;
};

export type ProviderAccount = {
  id: string;
  tenant_id: string;
  provider: string;
  name: string;
  account_id: string;
  url: string;
  auth_mode: string;
  preferred_runtime_type: "codex_proxy" | "cliproxyapi" | string;
  login_status: string;
  last_login_error: string;
  pricing_rate: string;
  status: string;
  created_at: string;
  updated_at: string;
  last_checked_at: string | null;
  quota_status: string;
  quota_reset_at: string | null;
  quota_remaining_tokens: number | null;
  quota_remaining_requests: number | null;
};

export type ProviderRuntimeAccount = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  provider_account_id: string | null;
  source_provider_account_id: string | null;
  name: string;
  runtime_type: "direct_api" | "codex_proxy" | "cliproxyapi" | string;
  share_mode: "private" | "shared_pool" | string;
  status: string;
  public_login_path: string;
  model_offers: ProviderRuntimeModelOffer[];
  last_error: string;
  last_health_check_at: string | null;
  quota_snapshot: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ProviderConnection = {
  id: string;
  name: string;
  account_identity: string;
  upstream_provider: string;
  url: string;
  engine: "direct_api" | "codex_proxy" | "cliproxyapi" | string;
  auth_mode: string;
  status: string;
  status_message: string;
  login_status: string;
  last_error: string;
  models: ProviderRuntimeModelOffer[];
  model_count?: number;
  models_loaded?: boolean;
  quota: {
    status: string;
    remaining_tokens: number | null;
    remaining_requests: number | null;
    reset_at: string | null;
  };
  available_actions: string[];
  ownership?: ResourceOwnership;
  access?: ResourceAccess;
  ownership_repair_required?: boolean;
  last_checked_at: string | null;
  health_history: Array<{
    status: string;
    reason: string;
    latency_ms: number;
    checked_at: string;
  }>;
  created_at: string;
  updated_at: string;
  technical_details?: {
    runtime_id: string;
    runtime_name: string;
    runtime_status: string;
    router_id: string;
    router_name: string;
    router_status: string;
    runtime_type: string;
    internal_api_url: string;
  };
};

export type ProviderConnectionDeletionImpact = {
  provider_id: string;
  provider_name: string;
  runtime_status: string;
  model_count: number;
  published_count: number;
  source_count: number;
  requires_name_confirmation: boolean;
  preserved_history: string[];
};

export type ModelContract = {
  operations: string[];
  input_modalities: string[];
  output_modalities: string[];
};
export type ProviderRuntimeModelOffer = {
  model_contract?: ModelContract;
  id: string;
  canonical_model_id: string | null;
  canonical_model_key: string | null;
  upstream_model_id: string;
  status: "detected" | "confirmed" | "unavailable" | "disabled" | string;
  daily_limit: number;
  monthly_limit: number;
  capacity: number;
  capabilities: string[];
  health_status: string;
  health_reason: string;
  last_discovered_at: string | null;
  last_health_check_at: string | null;
  confirmed_at: string | null;
  source_ids: string[];
};

export type CanonicalModel = {
  input_modalities?: string[];
  output_modalities?: string[];
  operations?: string[];
  id: string;
  key: string;
  display_name: string;
  family: string;
  modalities: string[];
  capabilities: string[];
  context_window: number | null;
  status: string;
};

export type ProviderRuntimeLoginInfo = {
  runtime_id: string;
  runtime_type: string;
  login_url: string;
  public_login_path: string;
  status: string;
};

export type ProviderRuntimeCredentials = {
  runtime_id: string;
  runtime_name: string;
  runtime_type: string;
  status: string;
  gateway_api_base_url: string;
  gateway_chat_completions_url: string;
  gateway_models_url: string;
  gateway_api_key: string;
  gateway_api_key_id: string;
  gateway_authorization_header: string;
  runtime_api_base_url: string;
  runtime_api_key: string;
  runtime_authorization_header: string;
  recommended_model: string;
  canonical_model: string;
  upstream_model_id: string;
  env: string;
  curl: string;
};






export type Deployment = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  deployment_id: string;
  provider: string;
  provider_account_id: string | null;
  source_type: "manual" | "provider_runtime" | string;
  canonical_model_id: string;
  canonical_model_key: string;
  upstream_model_id: string;
  provider_runtime_id: string | null;
  runtime_model_offer_id: string | null;
  endpoint: string;
  visibility: string;
  pricing_rate: string;
  status: string;
  health_status: string;
  health_reason: string;
  last_latency_ms: number | null;
  consecutive_failures: number;
  last_checked_at: string | null;
  created_at: string;
  updated_at: string;
  ownership?: ResourceOwnership;
  access?: ResourceAccess;
};

export type ModelGroup = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  name: string;
  display_name: string;
  canonical_model_id: string;
  canonical_model_key: string;
  visibility: string;
  routing_strategy: string;
  routing_config: Record<string, unknown>;
  routing_revision: number;
  status: string;
  ownership?: ResourceOwnership;
  access?: ResourceAccess;
  deployments: ModelGroupSource[];
};

export type ModelGroupSource = {
  id: string;
  deployment: Deployment;
  enabled: boolean;
  priority: number;
  weight: number;
  fallback_order: number;
  last_selected_at: string | null;
  selection_count: number;
  status: string;
};

export type ModelGroupRoutingDraftSource = {
  id: string;
  enabled: boolean;
  priority: number;
  weight: number;
  fallback_order: number;
};

export type ModelGroupRoutingPreview = {
  valid: boolean;
  expected_revision: number;
  next_revision: number;
  routing_strategy: string;
  candidate_count: number;
  healthy_candidate_count: number;
  affected_router_count: number;
  risks: Array<{ code: string; severity: "critical" | "warning" | "info" | string; message: string }>;
  candidates: Array<{
    source_id: string;
    source: string;
    provider: string;
    rank: number;
    health_status: string;
    last_checked_at: string | null;
    latency_ms: number | null;
    price_per_1k_tokens: string;
    traffic_share_percent: number | null;
  }>;
};

export type ModelGroupRoutingRevision = {
  revision: number;
  routing_strategy: string;
  routing_config: Record<string, unknown>;
  sources: ModelGroupRoutingDraftSource[];
  created_at: string;
  created_by: string;
  is_current: boolean;
};

export type Agent = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  team_id: string | null;
  name: string;
  status: string;
  visibility: string;
  publication_status: string;
  computer_requirement: "required" | "optional" | "disabled" | string;
  workspace_capabilities: string[];
  computer_declared_by_sdk: boolean;
  mobile_requirement: "required" | "optional" | "disabled" | string;
  mobile_capabilities: string[];
  mobile_policy_source: "sdk" | "cloud" | string;
  mobile_sdk_requirement: "required" | "optional" | "disabled" | string | null;
  mobile_sdk_capabilities: string[];
  mobile_can_restore_sdk: boolean;
  repo_metadata: Record<string, unknown>;
  current_version: string;
  version_count: number;
  latest_version_status: string;
  deployment_count: number;
  active_deployment_count: number;
  runtime_deployment_count: number;
  active_runtime_deployment_count: number;
  runtime_status: string;
  runtime_health_status: string;
  runtime_kind: "docker" | "openwrt_ipv6" | "openwrt_relay" | string;
  edge_router_id: string;
  edge_binding_mode: "manual" | "managed" | "suppressed" | string;
  edge_endpoint_url: string;
  current_image_id: string;
  current_image_ref: string;
  current_image_digest: string;
  current_image_version: string;
  deployed_image_id: string;
  deployed_image_ref: string;
  configuration_drift: boolean;
  lifecycle_status: string;
  publication_readiness: {
    ready: boolean;
    checks: Array<{ key: string; label: string; ok: boolean }>;
  };
  allowed_actions: string[];
  ownership?: ResourceOwnership;
  access?: ResourceAccess;
  created_at: string;
  updated_at: string;
};


export type AgentVersion = {
  id: string;
  version: string;
  commit_id: string;
  artifact_metadata: Record<string, unknown>;
  workspace_capabilities: string[];
  status: string;
  created_at: string;
  updated_at: string;
};

export type AgentWorkspaceGrant = {
  id?: string;
  agent_id: string;
  tenant_id?: string;
  project_id?: string | null;
  declared_scopes: string[];
  scopes: string[];
  status: "active" | "not_granted" | "deleted" | string;
  granted_at?: string | null;
  revoked_at?: string | null;
  updated_at?: string;
};

export type AgentComputerBinding = {
  id: string;
  agent_id: string;
  connection_id: string;
  connection_name: string;
  is_default: boolean;
  status: string;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
};

export type AgentDeployment = {
  id: string;
  agent_id: string;
  version: string | null;
  env: string;
  status: string;
  endpoint_url: string;
  created_at: string;
  updated_at: string;
};

export type AgentLog = {
  id: string;
  level: string;
  message: string;
  created_at: string;
};

export type AgentMemoryItem = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  agent_id: string;
  memory_type: string;
  scope: "caller" | "agent_global" | "developer_only" | string;
  content_text: string;
  content_json: Record<string, unknown>;
  source_run_id: string | null;
  source_event_ids: string[];
  confidence: string;
  sensitivity_level: string;
  consent_status: string;
  license_status: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type AgentDisplayRun = {
  tool_name?: string;
  error_code?: string;
  latency_ms?: number | null;
  id: string;
  agent_id: string;
  runtime_id: string | null;
  run_kind: "invocation" | "demo" | "deployment" | "legacy" | string;
  status: string;
  redaction_status: string;
  redaction_metadata: Record<string, unknown>;
  title: string;
  started_at: string;
  completed_at: string | null;
  latest_seq: number;
  caller: string;
  computer_status?: string;
  mobile_status?: string;
  created_at: string;
  updated_at: string;
};

export type AgentDisplayEvent = {
  id: string;
  agent_id: string;
  run_id: string;
  seq: number;
  type?: string;
  event_type?: string;
  message?: string;
  stepName?: string;
  toolCallId?: string;
  created_at: string;
  [key: string]: unknown;
};

export type AgentOutputArtifact = {
  computer_revision?: number;
  turn_index?: number | null;
  id: string;
  agent_id: string;
  run_id: string;
  runtime_id: string | null;
  source_event_id: string | null;
  workspace_path: string;
  original_file_name: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  producer_step: string;
  license_status: "unknown" | "internal" | "approved" | string;
  scan_status: "pending" | "passed" | "failed" | string;
  policy_status: "pending" | "approved" | "blocked" | string;
  scan_metadata: Record<string, unknown>;
  snapshot_status: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type AgentRunFollowUp = {
  attachments?: { asset_id: string; name?: string }[];
  files?: { file_id: string; name: string }[];
  id: string;
  mode: "queue" | "steer";
  turn_index: number;
  dispatched_turn: number | null;
  content: string;
  status: "pending" | "received" | "applied" | "rejected" | "not_applied" | "dispatched" | "blocked" | "cancelled" | "expired";
  code: string;
  position?: number;
  editable?: boolean;
};

export type RunFailure = {
  domain: "agent" | "cloud" | "computer" | "browser" | "business" | "mobile" | "unknown";
  code: string;
  title: string;
  message: string;
  recovery_hint: string;
  actions: Array<"check_status" | "retry_operation" | "cancel_turn" | "manage_computer" | "manage_mobile" | "view_browser" | "continue_chat" | "new_run">;
  outcome_unknown: boolean;
  automatic_retry: false;
};

export type PrivateAgentRunDisplay = {
  completion_unread?: boolean;
  failure?: RunFailure | null;
  follow_up?: { mode: "none" | "queue" | "steer_and_queue"; turn_index: number; items: AgentRunFollowUp[]; queue_revision?: string; attachment_protocol?: { queue: number; steer: number } };
  id: string;
  agent_id: string;
  agent_name: string;
  display_title: string;
  display_title_source: "pending" | "agent" | "derived" | "user" | string;
  tool_name: string;
  turn_index: number;
  display_mode: "chat" | "task" | "interactive" | "tool";
  tool_policy: Partial<{
    task: boolean;
    continuable: boolean;
    demo: boolean;
    chat: boolean;
    interactive: boolean;
  }>;
  status: string;
  execution: { profile_id: string; model: string; reasoning_effort: string };
  project_context: {
    project_id: string;
    project_name: string;
    instructions_markdown: string;
    revision: number;
    captured_at: string | null;
  };
  usage: {
    reported: boolean;
    current: null | { turn_index: number; profile_id: string; model: string; input_tokens: number; output_tokens: number; cached_input_tokens: number; reasoning_tokens: number; context_window: number; source: string };
    totals: { input_tokens: number; output_tokens: number; cached_input_tokens: number; reasoning_tokens: number };
  };
  title: string;
  started_at: string;
  completed_at: string | null;
  latest_seq: number;
  computer: {
    attached: boolean;
    terminal_status: string;
    viewer_mode: "read_only";
    workspace_cwd: string;
    revision?: number;
    name?: string;
    online?: boolean;
    platform?: string;
    browser_available?: boolean;
    event_cursor?: number;
    history?: Array<{ revision: number; computer_name: string }>;
  };
  mobile: {
    attached: boolean;
    name: string;
    status: string;
    capabilities: string[];
    commands: Array<{
      id: string;
      action: string;
      status: string;
      risk_level: string;
      requires_approval: boolean;
      created_at: string;
      completed_at: string | null;
    }>;
  };
  interactions: AgentRunInteraction[];
  messages: AgentRunMessage[];
  execution_task: {
    status: string;
    execution_state?: string;
    continuable: boolean;
    recovery?: {
      managed: boolean;
      protocol: number;
      attempt: number;
      state: string;
      last_committed_operation: number | null;
    };
    retry_count: number;
    error_code: string;
    result: Record<string, unknown>;
    expires_at: string;
  } | null;
};

export type AgentRunComputerDirectoryListing = {
  cwd: string;
  path: string;
  parent: string | null;
  directories: Array<{ name: string; path: string }>;
};

export type AgentMcpServerConfig = {
  type: "streamable-http" | string;
  agent_id: string;
  name: string;
  version: string;
  tenant_id?: string;
  url: string;
  headers: Record<string, string>;
};

export type AgentMcpExport = {
  agent_id: string;
  server_name: string;
  version: string;
  transport: "streamable-http" | string;
  scope: {
    kind: "workspace" | "project" | "template" | string;
    tenant_id: string | null;
    project_id: string | null;
  };
  credential: {
    mode: "bearer_env" | string;
    environment_variable: string;
    placeholder: string;
    one_time_inline_supported: boolean;
  };
  template: boolean;
  mcpServers: Record<string, AgentMcpServerConfig>;
};

export type AgentRunMessage = {
  id: string;
  sequence: number;
  turn_index: number | null;
  role: "user" | "assistant";
  content: string;
  content_blocks: AgentChatContentBlock[];
  created_at: string;
};

export type AgentPrivateRun = {
  attention_state?: RunHistoryFilter | string;
  completion_unread?: boolean;
  id: string;
  agent_id: string;
  title: string;
  title_source: "pending" | "agent" | "derived" | "user" | string;
  tool_name: string;
  preview: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  updated_at: string;
  display_url: string;
  messages: AgentRunMessage[];
};

export type AgentChatContentBlock =
  | { type: "markdown"; text: string }
  | { type: "image"; url: string; alt?: string; title?: string }
  | {
      type: "file";
      url: string;
      artifact_id?: string;
      name: string;
      content_type?: string;
      size_bytes?: number;
      status?: string;
    }
  | { type: "fields"; items: Array<{ label: string; value: string }> };

export type RunHistoryFilter = "all" | "running" | "input_required" | "completed_unread" | "failed";
export type AgentPrivateRunPage = {
  counts?: Record<RunHistoryFilter, number>;
  results: AgentPrivateRun[];
  next_cursor: string;
};

export type AgentRunInteraction = {
  id: string;
  key: string;
  kind: "text" | "confirm" | "select" | string;
  prompt: string;
  choices: Array<{ value: string; label: string }>;
  response: { value?: string; text?: string };
  status: "pending" | "answered" | "expired" | "cancelled" | string;
  expires_at: string;
  answered_at: string | null;
};

export type AgentRunTerminal = {
  status: string;
  viewer_mode: "read_only";
  latest_seq: number;
  next_cursor: number | null;
  has_more: boolean;
  truncated: boolean;
  computer_name: string;
  shell: string;
  workspace_cwd: string;
  last_error: string;
  started_at: string | null;
  ended_at: string | null;
  events: Array<{
    seq: number;
    kind: "command" | "stdout" | "stderr" | "system" | "exit" | string;
    command_id: string;
    data: string;
    exit_code: number | null;
    created_at: string;
  }>;
};


export type AgentResourceConfig = {
  id: string;
  cpu: string;
  memory: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type AgentStatus = {
  agent: Agent;
  deployments: AgentDeployment[];
};

export type AgentRuntimeImage = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  agent_id: string;
  version: string | null;
  image_ref: string;
  image_digest: string;
  artifact_path: string;
  status: string;
  created_at: string;
  updated_at: string;
  usage?: {
    is_default: boolean;
    deployed_environments: string[];
    can_delete: boolean;
    blocking_reasons: string[];
  };
};

export type AgentPythonBuild = {
  id: string;
  filename: string;
  source_sha256: string;
  entrypoint: string;
  framework: string;
  status: "queued" | "running" | "succeeded" | "failed";
  stage: string;
  error_code: string;
  error_message: string;
  diagnostics: string[];
  dependency_lock: string[];
  tool_count: number;
  image_id: string | null;
  image_removed?: boolean;
  created_at: string;
  completed_at: string | null;
};

export type AgentPythonBuildList = {
  configuration: { enabled: boolean; profile: string; max_source_bytes: number };
  results: AgentPythonBuild[];
  failed_builds?: Array<Pick<AgentPythonBuild, "id" | "filename">>;
};

export type AgentRuntimeDeployment = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  agent_id: string;
  runtime_kind: "docker" | "openwrt_ipv6" | "openwrt_relay" | string;
  image_id: string | null;
  image_ref: string | null;
  edge_registration_id: string | null;
  edge_router_id: string | null;
  edge_endpoint_url: string;
  edge_transport: "direct_ipv6" | "relay" | string | null;
  edge_relay_id: string | null;
  edge_relay_router_id: string | null;
  edge_relay_assignment_id: string | null;
  edge_lease_expires_at: string | null;
  env: string;
  status: string;
  container_id: string;
  internal_mcp_url: string;
  health_status: string;
  last_error: string;
  workspace_connection_id: string | null;
  workspace_root: string;
  workspace_access_mode: "read_only" | "read_write";
  created_at: string;
  updated_at: string;
  job_id?: string;
};

export type AgentRuntimeStatus = {
  images: AgentRuntimeImage[];
  deployments: AgentRuntimeDeployment[];
};

export type EdgeNode = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  router_id: string;
  domain_id: string;
  display_name: string;
  connectivity_mode: "direct_ipv6" | "relay" | string;
  relay_id?: string;
  relay_assignment_id?: string;
  relay_lease_expires_at?: string | null;
  connection_status:
    "pending" | "online" | "degraded" | "offline" | "revoked" | string;
  connection_status_reason:
    | "pending_heartbeat"
    | "connected"
    | "degraded"
    | "heartbeat_expired"
    | "graceful_stop"
    | "firmware_upgrade_required"
    | "revoked"
    | string;
  presence_supported: boolean;
  registration_diagnostic?: {
    code: string;
    http_status: number;
    occurred_at: string;
    capacity?: { code: string; used: string; limit: string };
  } | null;
  presence_expires_at: string | null;
  last_presence_at: string | null;
  ipv6_mode: "routed_prefix" | "upstream_relay" | "agent_owned" | string;
  software_version: string;
  capabilities: Record<string, unknown>;
  enrollment_generation: number;
  last_seen_at: string | null;
  registration_count: number;
  ownership: ResourceOwnership;
  access?: ResourceAccess;
  owner_relation: "own" | "other" | "legacy";
  owner_label: string;
  is_legacy: boolean;
  allowed_actions: Array<"update" | "revoke" | "use" | string>;
  status: string;
  created_at: string;
  updated_at: string;
};

export type EdgeRouterCapabilities = {
  create_own: boolean;
  read_own: boolean;
  update_own: boolean;
  revoke_own: boolean;
  use_own: boolean;
  audit: boolean;
  revoke_any: boolean;
  relay_available: boolean;
};

export type EdgeRelayEndpoint = {
  scheme: string;
  host: string;
  port: number;
  path: string;
  scope: "router" | "cloud_internal" | string;
};

export type EdgeRelayServiceStatus = {
  configured: boolean;
  running: boolean;
  enabled: boolean;
  available: boolean;
  reason:
    | "ready"
    | "operator_disabled"
    | "relay_process_unavailable"
    | "configuration_incomplete"
    | string;
  can_manage: boolean;
  relay_id: string;
  router_listener_healthy: boolean;
  cloud_listener_healthy: boolean;
  router_endpoint: EdgeRelayEndpoint | null;
  cloud_invoke_endpoint: EdgeRelayEndpoint | null;
  presence_sweeper: {
    automatic: boolean;
    running: boolean;
    reason:
      | "ready"
      | "starting"
      | "heartbeat_stale"
      | "configuration_incomplete"
      | string;
    interval_seconds: number;
    last_sweep_at: string | null;
    expired_last_run: number;
  };
};

export type EdgeRouterRegistrationSummary = {
  id: string;
  origin: string;
  protocols: string[];
  capabilities: string[];
  transport: "direct_ipv6" | "relay" | string;
  binding_mode: "manual" | "managed" | "suppressed" | string;
  managed_agent_name: string;
  manifest_digest: string;
  provisioning_state: "pending" | "ready" | "suppressed" | "rejected" | string;
  runtime_id: string | null;
  mcp_url: string | null;
  health_status: string;
  lease_active: boolean;
  lease_expires_at: string;
  agent: { id: string; name: string } | null;
};

export type EdgeNodeDetail = EdgeNode & {
  registrations: EdgeRouterRegistrationSummary[];
};

export type EdgeAgentRegistration = {
  id: string;
  node_id: string;
  router_id: string;
  agent_id: string | null;
  origin: string;
  route_id: string;
  protocols: string[];
  capabilities: string[];
  transport: "direct_ipv6" | "relay" | string;
  mcp_tools: Array<{
    name: string;
    title?: string;
    description?: string;
    input_schema?: Record<string, unknown>;
    intent?: string;
    intent_version?: string;
  }>;
  binding_mode: "manual" | "managed" | "suppressed" | string;
  managed_agent_name: string;
  manifest_digest: string;
  provisioning_state: "pending" | "ready" | "suppressed" | "rejected" | string;
  runtime_id: string | null;
  mcp_url: string | null;
  ipv6_address: string;
  port: number;
  path: string;
  scheme: string;
  tls_server_name: string;
  ca_bundle_id: string;
  relay_id: string;
  relay_router_id: string;
  relay_assignment_id: string;
  endpoint_url: string;
  generation: number;
  lease_expires_at: string;
  last_renewed_at: string;
  last_probe_at: string | null;
  health_status: string;
  lease_active: boolean;
  status: string;
  created_at: string;
  updated_at: string;
};

export type AgentInteractionTool = {
  input_modalities?: Array<"text" | "image" | "audio">;
  accepts_files?: boolean;
  slash_command?: string;
  slash_description?: string;
  execution_profiles?: Array<{
    id: string;
    label: string;
    model: string;
    is_default?: boolean;
    reasoning_efforts: string[];
    default_reasoning_effort: string;
    context_window: number | null;
  }>;
  name: string;
  title: string;
  description: string;
  input_schema: Record<string, unknown>;
  policy: {
    task: boolean;
    continuable: boolean;
    demo: boolean;
    chat: boolean;
    interactive: boolean;
  };
  display_mode: "chat" | "task" | "interactive" | "tool";
  availability: {
    can_invoke: boolean;
    code: string;
    message: string;
  };
};

export type AgentInteractor = {
  agent: {
    id: string;
    name: string;
    status: string;
    visibility: string;
    publication_status: string;
  };
  runtime: {
    id: string;
    status: string;
    kind: string;
    health_status: string;
  } | null;
  pricing: {
    type: string;
    price: string;
    max_price: string;
    currency: string;
  } | null;
  tools: AgentInteractionTool[];
  default_tool_name: string;
  private_display?: {
    available: boolean;
    tool_name: string;
    code: string;
    message: string;
  };
  project_context: {
    available: boolean;
    authorized: boolean;
    requires_authorization: boolean;
    external_agent: boolean;
    project_id: string;
    project_name: string;
    instructions_markdown: string;
    revision: number;
    updated_at: string | null;
  };
  computer: {
    requirement: "required" | "optional" | "disabled" | string;
    declared_scopes: string[];
    granted_scopes: string[];
    missing_scopes: string[];
    attached: boolean;
    online: boolean;
    device_name?: string;
    platform: "windows" | "linux" | "macos" | "unknown" | string;
    browser_available: boolean | null;
    browser_name: string;
    ready: boolean;
    code: string;
    message: string;
  };
  mobile: {
    requirement: "required" | "optional" | "disabled" | string;
    declared_scopes: string[];
    granted_scopes: string[];
    missing_scopes: string[];
    attached: boolean;
    binding_id: string;
    device_name: string;
    device_status: string;
    ready: boolean;
    code: string;
    message: string;
  };
};


export type DisplayStreamEvent = {
  id: string;
  agent_id: string;
  run_id: string;
  seq: number;
  type: string;
  name?: string;
  value?: unknown;
  content?: Record<string, unknown>;
  patch?: Array<Record<string, unknown>>;
  snapshot?: Record<string, unknown>;
  messages?: Array<Record<string, unknown>>;
  activityType?: string;
  messageId?: string;
  role?: string;
  delta?: string;
  toolCallId?: string;
  toolCallName?: string;
  parentMessageId?: string;
  stepName?: string;
  result?: unknown;
  payload?: Record<string, unknown>;
  created_at: string;
};

export type Dataset = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  name: string;
  visibility: "public" | "private" | string;
  status: string;
  size_bytes: number;
  file_count: number;
  current_version: string;
  lifecycle_status:
    | "draft"
    | "assets_added"
    | "commercial_setup"
    | "ready_to_publish"
    | "published"
    | string;
  publication_readiness: {
    ready: boolean;
    checks: Array<{ key: string; label: string; ok: boolean }>;
  };
  quota: {
    max_size: string;
    max_size_bytes: number | null;
  };
  allowed_actions: string[];
  ownership?: ResourceOwnership;
  access?: ResourceAccess;
  created_at: string;
  updated_at: string;
};

export type DatasetCapabilities = {
  can_create: boolean;
  can_search: boolean;
  can_pull: boolean;
  file_import?: { extensions: string[]; max_bytes: number; image_max_bytes: number; scan_description: string };
};










export type DatasetFile = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  dataset_id: string;
  file_name: string;
  size_bytes: number;
  content_type: string;
  sha256: string;
  storage_backend: string;
  metadata_json: Record<string, unknown>;
  download_url: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type DatasetVersionFileSnapshot = {
  file_id: string;
  file_name: string;
  size_bytes: number;
  sha256: string;
  storage_backend: string;
  metadata_json: Record<string, unknown>;
  download_url: string;
};

export type DatasetVersion = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  version: string;
  file_count: number;
  size_bytes: number;
  snapshot_json: {
    files: DatasetVersionFileSnapshot[];
    paginated?: boolean;
  };
  status: string;
  created_at: string;
  updated_at: string;
};

export type DatasetPullResult = {
  dataset: Dataset;
  files: DatasetFile[];
};


export type DatasetQuota = {
  id: string;
  max_size: string;
  max_size_bytes: number;
  status: string;
  created_at: string;
  updated_at: string;
};

export type DatasetContentSearchResult = {
  dataset: Dataset;
  file: DatasetFile;
  matches: string[];
};

export type DatasetSearchAllResult = {
  datasets: Dataset[];
  content: DatasetContentSearchResult[];
};

export type MediaAsset = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  purpose: "chat_input" | "agent_attachment" | "dataset_file" | string;
  file_name: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  uri: string;
  status: string;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
};

export type MediaSignedUrl = {
  url: string;
};

export type Job = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  job_type: string;
  resource_type: string;
  resource_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "canceled" | string;
  celery_task_id: string;
  input_json: Record<string, unknown>;
  result_json: Record<string, unknown>;
  error_code: string;
  error_message: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type JobEvent = {
  id: string;
  tenant_id: string;
  job_id: string;
  event_type: string;
  message: string;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type WorkspaceConnection = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  name: string;
  connection_type: "runtime" | "ssh" | string;
  ssh_host: string;
  ssh_port: number | null;
  ssh_user: string;
  auth_mode: "private_key" | "password" | string;
  workspace_root: string;
  status: string;
  last_test_status: string;
  last_test_error: string;
  last_test_at: string | null;
  metadata: Record<string, unknown>;
  runtime: {
    device_id: string;
    online: boolean;
    platform: "windows" | "linux" | "macos" | string;
    protocol_version: number;
    capabilities: Record<string, number>;
    browser_available: boolean | null;
    browser_name: string;
    last_seen_at: string | null;
    revoked: boolean;
  } | null;
  availability: {
    available: boolean;
    code: string;
    message: string;
  };
  created_by: number | null;
  created_at: string;
  updated_at: string;
};

export type ComputerRuntimePairing = {
  connection_id: string;
  pairing_url: string;
  setup_command: string;
  expires_at: string;
};

export type WorkspaceConnectionTestResult = {
  status: string;
  facts: Record<string, unknown>;
  checks: Array<{ name: string; ok: boolean; detail: string }>;
  error: string;
};

export type WorkspaceTerminalSession = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  connection_id: string;
  connection_name: string;
  shell: "auto" | "powershell" | "sh" | "bash" | string;
  cols: number;
  rows: number;
  status: string;
  last_error: string;
  started_at: string | null;
  ended_at: string | null;
  metadata: Record<string, unknown>;
  created_by: number | null;
  created_at: string;
  updated_at: string;
};

export type WorkspaceToolConfig = {
  tool: "codex" | string;
  profile: string;
  path: string;
  exists: boolean;
  launch_command: string;
  model: string;
  model_provider: string;
  api_configured: boolean;
  base_url: string;
  wire_api: string;
  has_bearer_token: boolean;
  mcp_servers: Array<{ name: string; url: string; type: string }>;
  content?: string;
  redacted_content: string;
  revision: string;
  overview: {
    target_name: string;
    session_status: string;
    api_status: "ready" | "needs_repair" | "not_configured" | string;
    connected_agent_count: number;
    external_mcp_count: number;
    last_applied_at: string;
  };
  api: {
    status: "ready" | "needs_repair" | "not_configured" | string;
    runtime_id: string;
    runtime_name: string;
    runtime_status: string;
    router_id: string;
    router_name: string;
    router_status: string;
    model: string;
    models?: string[];
    base_url: string;
    credential_managed: boolean;
    message: string;
  };
  agents: {
    credential_status: string;
    managed: Array<{
      server_name: string;
      agent_id: string;
      name: string;
      url: string;
      available: boolean;
      code: string;
      message: string;
    }>;
    external: Array<{ server_name: string; url: string; type: string }>;
  };
  recovery: {
    available?: boolean;
    actions?: Array<"recover" | "restore_previous" | "keep_local">;
    operation_id?: string;
    state?: string;
    code?: string;
    message?: string;
    backup_path?: string;
    backup_created_at?: string;
    rollback_available?: boolean;
  };
  write_availability?: {
    available: boolean;
    code: string;
    message: string;
    operation_id?: string;
    state?: string;
  };
  technical: {
    path: string;
    launch_command: string;
    redacted_content: string;
  };
  backup_path?: string;
  backup_created_at?: string;
  rollback_available?: boolean;
};

export type WorkspaceToolConfigOption = {
  id: string;
  name: string;
  status: string;
  available: boolean;
  code: string;
  message: string;
};

export type WorkspaceToolConfigOptions = {
  tool: "codex";
  routers: Array<
    WorkspaceToolConfigOption & {
      model: string;
      models?: string[];
      strategy: string;
      router_type?: string;
    }
  >;
  runtimes: Array<
    WorkspaceToolConfigOption & { model: string; runtime_type: string }
  >;
  agents: Array<
    WorkspaceToolConfigOption & { visibility: string; version: string }
  >;
};

export type WorkspaceToolConfigChange = {
  tool?: "codex";
  section: "api" | "agents" | "advanced";
  action:
    | "set_runtime"
    | "set_router"
    | "rotate_api_credential"
    | "add_agent"
    | "remove_agent"
    | "rotate_agent_credential"
    | "replace_all_mcp"
    | "clear_all_mcp";
  provider_runtime_id?: string;
  router_id?: string;
  agent_id?: string;
  mcp_server_name?: string;
  expected_revision?: string;
  confirm_destructive?: boolean;
};

export type WorkspaceToolConfigPreview = {
  tool: "codex";
  section: "api" | "agents" | "advanced";
  action: WorkspaceToolConfigChange["action"];
  revision: string;
  changes: Array<{
    kind: string;
    label: string;
    before: string;
    after: string;
  }>;
  warnings: Array<{ code: string; message: string }>;
  credential_action:
    "none" | "reuse" | "create" | "repair" | "rotate" | "revoke" | string;
  destructive: boolean;
  can_apply: boolean;
};

export type MobileDevice = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  name: string;
  platform: "android" | string;
  device_identifier: string;
  token_prefix: string;
  approval_mode: "manual" | "confirm_high_risk" | "auto" | string;
  online_status: "unknown" | "online" | "offline" | string;
  lifecycle_status:
    | "awaiting_pairing"
    | "setup_required"
    | "online"
    | "offline"
    | "token_expired"
    | "disabled"
    | string;
  lifecycle_detail: string;
  recommended_action:
    | "continue_pairing"
    | "complete_setup"
    | "open_control"
    | "troubleshoot"
    | "regenerate_pairing"
    | "enable_device"
    | string;
  paired_at: string | null;
  pairing_expires_at: string | null;
  capabilities: Record<string, unknown>;
  metadata: Record<string, unknown>;
  current_package: string;
  current_activity: string;
  last_observation: Record<string, unknown>;
  screenshot_available: boolean;
  screenshot_captured_at: string | null;
  screenshot_expires_at: string | null;
  last_seen_at: string | null;
  status: string;
  created_by: number | null;
  created_at: string;
  updated_at: string;
};

export type MobileDeviceWithPairingToken = MobileDevice & {
  pairing_token: string;
};

export type MobileAggregateStatus = {
  scope: "workspace" | string;
  total: number;
  online: number;
  offline: number;
  busy: number;
  failed: number;
  re_pair_required: number;
};

export type AgentMobileBinding = {
  id: string;
  agent_id: string;
  device_id: string;
  device_name: string;
  device_status: string;
  is_default: boolean;
  status: string;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
};

export type AgentMobileGrant = {
  id?: string;
  agent_id: string;
  tenant_id?: string;
  project_id?: string | null;
  declared_scopes: string[];
  scopes: string[];
  status: string;
  granted_at?: string | null;
  revoked_at?: string | null;
  updated_at?: string;
};

export type MobileCommand = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  device_id: string;
  action:
    | "observe"
    | "capture_screen"
    | "tap_text"
    | "tap_coordinates"
    | "type_text"
    | "swipe"
    | "press_back"
    | "open_app"
    | "wait_for_state"
    | string;
  arguments: Record<string, unknown>;
  status:
    | "pending_approval"
    | "queued"
    | "running"
    | "succeeded"
    | "failed"
    | "rejected"
    | "canceled"
    | string;
  risk_level: "low" | "medium" | "high" | string;
  requires_approval: boolean;
  result: Record<string, unknown>;
  error: string;
  approved_at: string | null;
  dispatched_at: string | null;
  completed_at: string | null;
  expires_at: string | null;
  created_by: number | null;
  approved_by: number | null;
  created_at: string;
  updated_at: string;
};

export type RouterOutput = {
  id: string;
  router_id: string;
  router_name: string;
  model_name: string;
  model_group_id: string | null;
  model_group_name: string;
  description: string;
  enabled: boolean;
  is_default: boolean;
  status: string;
  created_at: string;
  updated_at: string;
};

export type RouterChildBinding = {
  id: string;
  router_id: string;
  exposed_model_name: string;
  child_output: RouterOutput;
  enabled: boolean;
  priority: number;
  weight: number;
  status: string;
  created_at: string;
  updated_at: string;
};

export type RouterAggregationCandidateModel = {
  output_id: string;
  model_name: string;
  model_group_name: string;
  available: boolean;
  code: string;
  message: string;
  already_bound: boolean;
};

export type RouterAggregationCandidate = {
  router_id: string;
  name: string;
  status: string;
  available: boolean;
  code: string;
  message: string;
  output_id: string;
  model_name: string;
  already_bound: boolean;
  bound_model_name: string;
  models: RouterAggregationCandidateModel[];
};

export type Router = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  name: string;
  strategy: string;
  status: string;
  current_version: string;
  model_group_ids: string[];
  router_type: "execution" | "aggregation" | string;
  output_models: string[];
  outputs: RouterOutput[];
  child_bindings: RouterChildBinding[];
  ownership?: ResourceOwnership;
  access?: ResourceAccess;
  created_at: string;
  updated_at: string;
};

export type RouterVersion = {
  id: string;
  version: string;
  router_file_path: string;
  file_name: string;
  file_size: number;
  status: string;
  created_at: string;
  updated_at: string;
};

export type RouterDeployment = {
  id: string;
  router_id: string;
  version: string;
  status: string;
  endpoint_url: string;
  created_at: string;
  updated_at: string;
};

export type RouterSource = {
  router_id: string;
  has_source: boolean;
  source: string;
  version: string;
  version_status: string;
  is_deployed: boolean;
  is_runtime_source: boolean;
  source_kind: string;
  file_name: string;
  file_size: number;
};

export type RouterModelGroupBinding = {
  id: string;
  model_group_id: string | null;
  model_group_display_name: string;
  provider_name: string;
  model_group_name: string;
  enabled: boolean;
  priority: number;
  weight: number;
  routing_hint: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type RouterProviderPreference = {
  id: string;
  preference_type: string;
  provider_account_id: string | null;
  pool_contribution_id: string | null;
  priority: number;
  weight: number;
  status: string;
  created_at: string;
  updated_at: string;
};

export type RouterCredentials = {
  router_id: string;
  router_name: string;
  router_strategy: string;
  gateway_api_base_url: string;
  gateway_chat_completions_url: string;
  gateway_models_url: string;
  router_invoke_url: string;
  gateway_api_key: string;
  gateway_api_key_id: string;
  gateway_authorization_header: string;
  recommended_model: string;
  available_models: string[];
  env: string;
  curl: string;
  invoke_curl: string;
};

export type RoutingSourceCandidate = {
  source_id: string;
  source: string;
  provider: string;
  model: string;
  source_type: string;
  rank: number | null;
  selected: boolean;
  enabled: boolean;
  priority: number;
  weight: number;
  fallback_order: number;
  health_status: string;
  quota_available: boolean;
  price_per_1k_tokens: string;
  latency_ms: number;
  exclusion_reason: string;
};

export type RoutingTrace = {
  version: number;
  path?: Array<{
    router_id: string;
    router: string;
    type: "aggregation" | "execution" | string;
    model: string;
  }>;
  aggregation?: {
    model: string;
    selected_binding_id: string;
    selected_child_router_id: string;
    selected_child_output_id: string;
  };
  router: {
    strategy: string;
    candidates: Array<{
      pool_id: string;
      pool: string;
      model: string;
      rank: number | null;
      selected: boolean;
      priority: number;
      weight: number;
      routing_hint: string;
      source_count: number;
      lowest_price_per_1k_tokens: string | null;
      lowest_latency_ms: number | null;
      best_health_status: string;
      exclusion_reason: string;
    }>;
    selected_pool_id: string;
    reason: string;
  };
  pool: {
    pool_id?: string;
    pool?: string;
    strategy: string;
    candidates: RoutingSourceCandidate[];
    selected_source_id: string;
  };
  child_router?: RoutingTrace;
};

export type RouterTestResult = {
  router_id: string;
  strategy: string;
  requested_model: string;
  selected_model_group_id: string;
  selected_model_group: string;
  selected_source_id: string;
  selected_source: string;
  selected_provider: string;
  selected_model: string;
  selected_provider_deployment_id: string;
  selected_provider_deployment: string;
  reason: string;
  trace: RoutingTrace;
};

export type RouterTraceRecord = {
  id: string;
  request_id: string;
  status: string;
  error_code: string;
  model: string;
  latency_ms: number;
  cost: string;
  currency: string;
  fallback_count: number;
  selected_pool_id: string | null;
  selected_pool: string | null;
  selected_source_id: string | null;
  selected_source: string | null;
  provider: string;
  provider_deployment: string | null;
  trace: RoutingTrace | null;
  created_at: string;
};

export type RouterTracePage = {
  items: RouterTraceRecord[];
  next_cursor: string | null;
};

export type TopologyModelOffer = {
  id: string;
  canonical_model_key: string | null;
  upstream_model_id: string;
  status: string;
  health_status: string;
  source_ids: string[];
};

export type TopologyRuntime = {
  id: string;
  name: string;
  runtime_type: string;
  status: string;
  ownership: "owned" | string;
  resource_ownership?: ResourceOwnership;
  publisher: string;
  model_offers: TopologyModelOffer[];
};

export type TopologySource = {
  id: string;
  name: string;
  provider: string;
  canonical_model_key: string;
  upstream_model_id: string;
  model_offer_id: string | null;
  source_type: string;
  runtime_id: string | null;
  publisher: string;
  price_per_1k_tokens: string;
  latency_ms: number;
  health_status: string;
  health_reason: string;
  status: string;
  resource_ownership?: ResourceOwnership;
  pool_ids: string[];
};

export type TopologyPool = {
  id: string;
  name: string;
  display_name: string;
  canonical_model_key: string;
  routing_strategy: string;
  routing_config: Record<string, unknown>;
  visibility: string;
  status: string;
  source_count: number;
  enabled_source_count: number;
  lowest_price_per_1k_tokens: string | null;
  lowest_latency_ms: number | null;
  health_status: string;
  resource_ownership?: ResourceOwnership;
  sources: Array<{
    link_id: string;
    source_id: string;
    source: string;
    provider: string;
    enabled: boolean;
    priority: number;
    weight: number;
    fallback_order: number;
    health_status: string;
    price_per_1k_tokens: string;
    latency_ms: number;
  }>;
};

export type TopologyRouter = {
  id: string;
  name: string;
  router_type: "execution" | "aggregation" | string;
  strategy: string;
  status: string;
  resource_ownership?: ResourceOwnership;
  current_version: string;
  pool_count: number;
  pool_ids: string[];
  pools: Array<{
    binding_id: string;
    pool_id: string;
    pool: string;
    canonical_model_key: string;
    priority: number;
    weight: number;
    routing_hint: string;
  }>;
  api_models: Array<{
    binding_id: string;
    model_name: string;
    available: boolean;
    code: string;
    message: string;
    execution_router_id: string;
    execution_router_name: string;
    execution_router_status: string;
    execution_model_name: string;
  }>;
  execution_router_count: number;
  request_count: number;
  average_latency_ms: number;
  total_cost?: string;
  last_request_at: string | null;
  last_selected_pool_id: string | null;
  last_selected_source_id: string | null;
};

export type TopologyPayload = {
  summary: {
    runtime_count: number;
    source_count: number;
    pool_count: number;
    router_count: number;
    health: Record<string, number>;
  };
  runtimes: TopologyRuntime[];
  sources: TopologySource[];
  pools: TopologyPool[];
  routers: TopologyRouter[];
};

export type ApiKeyPolicy = {
  allowed_operations?: string[];
  monthly_budget: string | null;
  rpm: number | null;
  tpm: number | null;
  allowed_models: string[];
  allowed_agents: string[];
  allowed_routers: string[];
  no_store_prompt: boolean;
  no_store_response: boolean;
  no_overseas_model: boolean;
};

export type ApiKey = {
  id: string;
  tenant_id: string;
  project_id: string | null;
  name: string;
  scope: string;
  key_prefix: string;
  status: string;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  policy: ApiKeyPolicy;
};

export type ApiKeyWithPlaintext = ApiKey & {
  plaintext_key: string;
};



export type AlertEvent = {
  is_test?: boolean;
  acknowledged_at?: string | null;
  id: string;
  rule: string;
  metric: string;
  resource_type: string;
  resource_id: string;
  value: string;
  threshold_value: string;
  threshold: string;
  status: string;
  triggered_at: string;
  resolved_at: string | null;
  metadata_json: Record<string, unknown>;
  notifications: Array<{
    id: string;
    channel: string;
    target: string;
    delivery_status: string;
    sent_at: string | null;
    error_message: string;
  }>;
  created_at: string;
  updated_at: string;
};

export type SystemMetrics = Record<string, unknown>;

export type ResourceMetrics = Record<string, unknown>;

export type AlertRule = {
  last_error_code?: string;
  muted_until?: string | null;
  project_id?: string | null;
  id: string;
  tenant_id: string;
  metric: string;
  threshold: string;
  threshold_value: string | null;
  threshold_unit: string;
  operator: "gt" | "gte" | "lt" | "lte" | "eq";
  resource_type: string;
  resource_id: string;
  window_seconds: number;
  cooldown_seconds: number;
  notification_channels: Record<string, unknown>;
  last_evaluated_at: string | null;
  last_triggered_at: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

export type ReportSchedule = {
  id: string;
  tenant_id: string;
  email: string;
  interval: "daily" | "weekly" | "monthly";
  last_sent_at: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

export type ReportDelivery = {
  id: string;
  tenant_id: string;
  schedule: string;
  email: string;
  interval: string;
  period_start: string;
  period_end: string;
  delivery_status: string;
  sent_at: string | null;
  error_message: string;
  summary_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type AuditLog = {
  id: string;
  tenant_id: string;
  project_id: string;
  actor_id: string;
  actor_email: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  before_snapshot: Record<string, unknown>;
  after_snapshot: Record<string, unknown>;
  ip_address: string | null;
  user_agent: string;
  request_id: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ChatMessage = {
  role: "system" | "user" | "assistant";
  content: string;
};

export type ChatCompletionRequest = {
  model: string;
  router_id?: string;
  messages: ChatMessage[];
  temperature?: number;
  max_tokens?: number;
  stream?: boolean;
  stream_options?: Record<string, unknown>;
};

export type LoginResponse = {
  access_token: string;
  refresh_token: string;
  tenant_id: string;
};
