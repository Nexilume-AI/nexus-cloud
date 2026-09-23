from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse


# This is an input to a distribution, not a deployable edition. In particular,
# do not allow a deployment typo to start Django without its policy adapters.
if os.environ.get("DJANGO_SETTINGS_MODULE") == "config.base_settings":
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured("BASE_SETTINGS_NOT_RUNNABLE: select a complete distribution settings module.")

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-nexus-secret-key")
NEXUS_SECRET_ENCRYPTION_KEYS = os.environ.get("NEXUS_SECRET_ENCRYPTION_KEYS", "")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
NEXUS_ENVIRONMENT = os.environ.get("NEXUS_ENVIRONMENT", "development").strip().lower()
NEXUS_PRODUCTION = NEXUS_ENVIRONMENT in {"prod", "production"} or not DEBUG
NEXUS_PROCESS_ROLE = os.environ.get("NEXUS_PROCESS_ROLE", "").strip().lower()
NEXUS_WEBSOCKET_ROUTING_MODE = os.environ.get("NEXUS_WEBSOCKET_ROUTING_MODE", "").strip().lower()
# Composition input only: the distribution must supply authorization and
# resource-admission backends before Django starts. No personal fallback.
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

from config.api_routes import CORE_API_ROUTES as _core_api_routes
NEXUS_API_ROUTES = _core_api_routes

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "apps.common.apps.CommonConfig",
    "apps.accounts",
    "apps.notifications",
    "apps.tenancy",
    "apps.jobs",
    "apps.providers",
    "apps.deployments",
    "apps.routers",
    "apps.gateway",
    "apps.agents",
    "apps.datasets",
    "apps.metrics.apps.MetricsConfig",
    "apps.audit",
    "apps.workspaces.apps.WorkspacesConfig",
    "apps.mobile.apps.MobileConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.common.middleware.RequestIDMiddleware",
    "apps.common.middleware.TenantContextMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASE_URL = os.environ.get("DATABASE_URL")
from config.database import database_config
DATABASES = {"default": database_config(os.environ, BASE_DIR)}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
NEXUS_WEB_INDEX_PATH = os.environ.get("NEXUS_WEB_INDEX_PATH", str(BASE_DIR / "static" / "web" / "index.html"))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
NEXUS_SHARED_STORAGE_ROOT = os.environ.get(
    "NEXUS_SHARED_STORAGE_ROOT",
    str(BASE_DIR / "storage"),
)
NEXUS_DATASET_STORAGE_ROOT = os.environ.get(
    "NEXUS_DATASET_STORAGE_ROOT",
    str(Path(NEXUS_SHARED_STORAGE_ROOT) / "datasets"),
)
NEXUS_DATASET_STORAGE_BACKEND = os.environ.get("NEXUS_DATASET_STORAGE_BACKEND", "local")
NEXUS_DATASET_S3_ENDPOINT_URL = os.environ.get("NEXUS_DATASET_S3_ENDPOINT_URL", "")
NEXUS_DATASET_S3_BUCKET = os.environ.get("NEXUS_DATASET_S3_BUCKET", "")
NEXUS_DATASET_S3_REGION = os.environ.get("NEXUS_DATASET_S3_REGION", "")
NEXUS_DATASET_S3_ACCESS_KEY_ID = os.environ.get("NEXUS_DATASET_S3_ACCESS_KEY_ID", "")
NEXUS_DATASET_S3_SECRET_ACCESS_KEY = os.environ.get("NEXUS_DATASET_S3_SECRET_ACCESS_KEY", "")
NEXUS_DATASET_IMPORT_MAX_BYTES = int(os.environ.get("NEXUS_DATASET_IMPORT_MAX_BYTES", str(10 * 1024 ** 2)))
NEXUS_DATASET_INDEX_MAX_BYTES = int(os.environ.get("NEXUS_DATASET_INDEX_MAX_BYTES", "1048576"))
NEXUS_DATASET_INDEX_SYNC = os.environ.get("NEXUS_DATASET_INDEX_SYNC", "1") == "1"
NEXUS_DATASET_EXPORT_SPOOL_MAX_BYTES = int(os.environ.get("NEXUS_DATASET_EXPORT_SPOOL_MAX_BYTES", str(10 * 1024 ** 3)))
NEXUS_DATASET_IMPORT_PENDING_LIMIT = int(os.environ.get("NEXUS_DATASET_IMPORT_PENDING_LIMIT", "100"))
NEXUS_DATASET_SPOOL_MIN_FREE_BYTES = int(os.environ.get("NEXUS_DATASET_SPOOL_MIN_FREE_BYTES", str(512 * 1024 ** 2)))
NEXUS_DATASET_SEARCH_BACKEND = os.environ.get("NEXUS_DATASET_SEARCH_BACKEND", "database")
NEXUS_OPENSEARCH_URL = os.environ.get("NEXUS_OPENSEARCH_URL", "http://127.0.0.1:9200")
NEXUS_OPENSEARCH_INDEX = os.environ.get("NEXUS_OPENSEARCH_INDEX", "nexus-dataset-files")
NEXUS_OPENSEARCH_USERNAME = os.environ.get("NEXUS_OPENSEARCH_USERNAME", "")
NEXUS_OPENSEARCH_PASSWORD = os.environ.get("NEXUS_OPENSEARCH_PASSWORD", "")
NEXUS_OPENSEARCH_TIMEOUT_SECONDS = float(os.environ.get("NEXUS_OPENSEARCH_TIMEOUT_SECONDS", "10"))
NEXUS_MEDIA_STORAGE_ROOT = os.environ.get(
    "NEXUS_MEDIA_STORAGE_ROOT",
    str(Path(NEXUS_SHARED_STORAGE_ROOT) / "media"),
)
NEXUS_MEDIA_SIGNED_URL_TTL_SECONDS = int(os.environ.get("NEXUS_MEDIA_SIGNED_URL_TTL_SECONDS", "300"))
NEXUS_MEDIA_MAX_UPLOAD_BYTES = int(os.environ.get("NEXUS_MEDIA_MAX_UPLOAD_BYTES", "25000000"))
NEXUS_MEDIA_ALLOWED_CONTENT_TYPES = os.environ.get(
    "NEXUS_MEDIA_ALLOWED_CONTENT_TYPES",
    "image/png,image/jpeg,image/webp,image/gif,application/pdf,audio/mpeg,audio/wav,video/mp4,text/plain,application/json",
).split(",")
NEXUS_ROUTER_STORAGE_ROOT = os.environ.get(
    "NEXUS_ROUTER_STORAGE_ROOT",
    str(Path(NEXUS_SHARED_STORAGE_ROOT) / "routers"),
)
NEXUS_ROUTER_RUNTIME_RUNNER = os.environ.get("NEXUS_ROUTER_RUNTIME_RUNNER", "nsjail")
NEXUS_NSJAIL_BIN = os.environ.get("NEXUS_NSJAIL_BIN", "nsjail")
NEXUS_ROUTER_RUNTIME_TIMEOUT_SECONDS = float(os.environ.get("NEXUS_ROUTER_RUNTIME_TIMEOUT_SECONDS", "2"))
NEXUS_ROUTER_RUNTIME_CPU_SECONDS = int(os.environ.get("NEXUS_ROUTER_RUNTIME_CPU_SECONDS", "1"))
NEXUS_ROUTER_RUNTIME_MEMORY_MB = int(os.environ.get("NEXUS_ROUTER_RUNTIME_MEMORY_MB", "128"))
NEXUS_ROUTER_RUNTIME_PIDS_LIMIT = int(os.environ.get("NEXUS_ROUTER_RUNTIME_PIDS_LIMIT", "32"))
NEXUS_ROUTER_RUNTIME_PYTHON = os.environ.get("NEXUS_ROUTER_RUNTIME_PYTHON", "/usr/bin/python3")
NEXUS_ROUTER_RUNTIME_DOCKER_NSJAIL_IMAGE = os.environ.get("NEXUS_ROUTER_RUNTIME_DOCKER_NSJAIL_IMAGE", "nsjail-test")
NEXUS_ROUTER_RUNTIME_READONLY_BINDS = os.environ.get(
    "NEXUS_ROUTER_RUNTIME_READONLY_BINDS",
    "/usr,/lib,/lib64,/bin,/etc/ld.so.cache,/etc/alternatives",
)
NEXUS_AGENT_STORAGE_ROOT = os.environ.get(
    "NEXUS_AGENT_STORAGE_ROOT",
    str(Path(NEXUS_SHARED_STORAGE_ROOT) / "agents"),
)
NEXUS_GATEWAY_MOCK_HOSTS = {"mock.local", "mock.provider.local"}
NEXUS_GATEWAY_FAIL_HOSTS = {"fail.local", "fail.provider.local"}
NEXUS_PROVIDER_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("NEXUS_PROVIDER_REQUEST_TIMEOUT_SECONDS", "60"))
NEXUS_PROVIDER_ALLOW_HTTP = os.environ.get("NEXUS_PROVIDER_ALLOW_HTTP", "0") == "1"
# Optional exact-host DNS recovery; endpoint validation and TLS remain mandatory.
NEXUS_PROVIDER_DOH_HOSTS = os.environ.get("NEXUS_PROVIDER_DOH_HOSTS", "")
NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS = os.environ.get("NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS", "")
NEXUS_MULTIMODAL_MAX_CONTENT_PARTS = int(os.environ.get("NEXUS_MULTIMODAL_MAX_CONTENT_PARTS", "64"))
NEXUS_MULTIMODAL_MAX_IMAGE_PARTS = int(os.environ.get("NEXUS_MULTIMODAL_MAX_IMAGE_PARTS", "8"))
NEXUS_MULTIMODAL_DATA_URL_MAX_BYTES = int(os.environ.get("NEXUS_MULTIMODAL_DATA_URL_MAX_BYTES", "2000000"))
NEXUS_PROVIDER_RUNTIME_RUNNER = os.environ.get("NEXUS_PROVIDER_RUNTIME_RUNNER", "docker")
NEXUS_PROVIDER_RUNTIME_CONTROLLER_SOCKET = os.environ.get(
    "NEXUS_PROVIDER_RUNTIME_CONTROLLER_SOCKET", "/run/nexus-provider/controller.sock"
)
NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN = os.environ.get("NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN", "")
NEXUS_PROVIDER_RUNTIME_CONTROLLER_TIMEOUT_SECONDS = float(
    os.environ.get("NEXUS_PROVIDER_RUNTIME_CONTROLLER_TIMEOUT_SECONDS", "1900")
)
NEXUS_PROVIDER_RUNTIME_CONTROLLER_WORKERS = int(
    os.environ.get("NEXUS_PROVIDER_RUNTIME_CONTROLLER_WORKERS", "8")
)
NEXUS_CODEX_PROXY_IMAGE = os.environ.get("NEXUS_CODEX_PROXY_IMAGE", "codex-proxy:latest")
NEXUS_CLIPROXYAPI_IMAGE = os.environ.get("NEXUS_CLIPROXYAPI_IMAGE", "cliproxyapi:latest")
NEXUS_CODEX_PROXY_SOURCE_DIR = os.environ.get("NEXUS_CODEX_PROXY_SOURCE_DIR", str(BASE_DIR.parent / "codex-proxy"))
NEXUS_CLIPROXYAPI_SOURCE_DIR = os.environ.get("NEXUS_CLIPROXYAPI_SOURCE_DIR", str(BASE_DIR.parent / "CLIProxyAPI"))
NEXUS_CODEX_PROXY_IMAGE_PREFIX = os.environ.get("NEXUS_CODEX_PROXY_IMAGE_PREFIX", "nexus-codex-proxy")
NEXUS_CLIPROXYAPI_IMAGE_PREFIX = os.environ.get("NEXUS_CLIPROXYAPI_IMAGE_PREFIX", "nexus-cliproxyapi")
NEXUS_PROVIDER_RUNTIME_BUILD_IMAGES = os.environ.get("NEXUS_PROVIDER_RUNTIME_BUILD_IMAGES", "1") == "1"
NEXUS_PROVIDER_RUNTIME_REUSE_BUILT_IMAGES = os.environ.get("NEXUS_PROVIDER_RUNTIME_REUSE_BUILT_IMAGES", "1") == "1"
NEXUS_PROVIDER_RUNTIME_SHARED_IMAGE_TAG = os.environ.get("NEXUS_PROVIDER_RUNTIME_SHARED_IMAGE_TAG", "runtime")
# Operator-trusted receipts from tools/provider_releases/release.py. Production
# always requires verification; the flag also enables it in development.
NEXUS_PROVIDER_RUNTIME_RELEASE_DIR = os.environ.get("NEXUS_PROVIDER_RUNTIME_RELEASE_DIR", "")
NEXUS_PROVIDER_RUNTIME_REQUIRE_VERIFIED_RELEASE = os.environ.get(
    "NEXUS_PROVIDER_RUNTIME_REQUIRE_VERIFIED_RELEASE", "1" if NEXUS_PRODUCTION else "0"
) == "1"
NEXUS_PROVIDER_RUNTIME_BUILD_TIMEOUT_SECONDS = int(os.environ.get("NEXUS_PROVIDER_RUNTIME_BUILD_TIMEOUT_SECONDS", "1800"))
NEXUS_PROVIDER_RUNTIME_START_ATTEMPTS = int(os.environ.get("NEXUS_PROVIDER_RUNTIME_START_ATTEMPTS", "60"))
NEXUS_PROVIDER_RUNTIME_START_DELAY_SECONDS = float(os.environ.get("NEXUS_PROVIDER_RUNTIME_START_DELAY_SECONDS", "0.5"))
NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS = int(os.environ.get("NEXUS_PROVIDER_LIFECYCLE_STALE_SECONDS", "180"))
NEXUS_PROVIDER_HEALTH_INTERVAL_SECONDS = int(os.environ.get("NEXUS_PROVIDER_HEALTH_INTERVAL_SECONDS", "60"))
NEXUS_PROVIDER_HEALTH_CONFIRM_TIMEOUT_SECONDS = float(
    os.environ.get("NEXUS_PROVIDER_HEALTH_CONFIRM_TIMEOUT_SECONDS", "15")
)
NEXUS_PROVIDER_MODEL_DISCOVERY_INTERVAL_SECONDS = int(
    os.environ.get("NEXUS_PROVIDER_MODEL_DISCOVERY_INTERVAL_SECONDS", "1800")
)
# Optional host-scoped inference-probe budget. Catalog discovery remains complete;
# models outside a configured host's list are explicitly unverified, not healthy.
NEXUS_PROVIDER_MODEL_PROBE_ALLOWLIST = json.loads(os.environ.get("NEXUS_PROVIDER_MODEL_PROBE_ALLOWLIST", "{}"))
if not isinstance(NEXUS_PROVIDER_MODEL_PROBE_ALLOWLIST, dict) or any(
    not isinstance(host, str) or not isinstance(models, list) or any(not isinstance(model, str) for model in models)
    for host, models in NEXUS_PROVIDER_MODEL_PROBE_ALLOWLIST.items()
):
    raise ValueError("NEXUS_PROVIDER_MODEL_PROBE_ALLOWLIST must map hosts to lists of model IDs")
NEXUS_PROVIDER_RUNTIME_STORAGE_ROOT = os.environ.get(
    "NEXUS_PROVIDER_RUNTIME_STORAGE_ROOT",
    str(Path(NEXUS_SHARED_STORAGE_ROOT) / "provider-runtimes"),
)
NEXUS_PROVIDER_RUNTIME_MEMORY_LIMIT = os.environ.get("NEXUS_PROVIDER_RUNTIME_MEMORY_LIMIT", "1024m")
NEXUS_PROVIDER_RUNTIME_DOCKER_NETWORK = os.environ.get("NEXUS_PROVIDER_RUNTIME_DOCKER_NETWORK", "bridge")
NEXUS_PROVIDER_RUNTIME_NETWORK_ENDPOINTS = os.environ.get("NEXUS_PROVIDER_RUNTIME_NETWORK_ENDPOINTS", "0") == "1"
NEXUS_PROVIDER_RUNTIME_HOST_STORAGE_ROOT = os.environ.get("NEXUS_PROVIDER_RUNTIME_HOST_STORAGE_ROOT", "")
NEXUS_PROVIDER_RUNTIME_PIDS_LIMIT = int(os.environ.get("NEXUS_PROVIDER_RUNTIME_PIDS_LIMIT", "512"))
NEXUS_PROVIDER_RUNTIME_LOGIN_PORT = int(os.environ.get("NEXUS_PROVIDER_RUNTIME_LOGIN_PORT", "8080"))
NEXUS_CLAUDE_OAUTH_CALLBACK_PORT = int(os.environ.get("NEXUS_CLAUDE_OAUTH_CALLBACK_PORT", "54545"))
NEXUS_PROVIDER_RUNTIME_API_PORT = int(os.environ.get("NEXUS_PROVIDER_RUNTIME_API_PORT", "8080"))
NEXUS_WORKSPACE_SSH_RUNNER = os.environ.get("NEXUS_WORKSPACE_SSH_RUNNER", "fake")
NEXUS_WORKSPACE_SESSION_TTL_SECONDS = int(os.environ.get("NEXUS_WORKSPACE_SESSION_TTL_SECONDS", "14400"))
NEXUS_WORKSPACE_IDLE_TIMEOUT_SECONDS = int(os.environ.get("NEXUS_WORKSPACE_IDLE_TIMEOUT_SECONDS", "1800"))
NEXUS_WORKSPACE_SSH_TIMEOUT_SECONDS = int(os.environ.get("NEXUS_WORKSPACE_SSH_TIMEOUT_SECONDS", "20"))
NEXUS_LEGACY_SSH_ENABLED = os.environ.get("NEXUS_LEGACY_SSH_ENABLED", "0") == "1"
NEXUS_COMPUTER_PAIRING_TTL_SECONDS = int(os.environ.get("NEXUS_COMPUTER_PAIRING_TTL_SECONDS", "600"))
NEXUS_COMPUTER_RUNTIME_CA_FILE = os.environ.get("NEXUS_COMPUTER_RUNTIME_CA_FILE", "")
NEXUS_COMPUTER_RUNTIME_HEARTBEAT_SECONDS = int(os.environ.get("NEXUS_COMPUTER_RUNTIME_HEARTBEAT_SECONDS", "15"))
NEXUS_COMPUTER_RUNTIME_STALE_SECONDS = int(os.environ.get("NEXUS_COMPUTER_RUNTIME_STALE_SECONDS", "45"))
NEXUS_COMPUTER_RUNTIME_UPLOAD_MAX_BYTES = int(os.environ.get("NEXUS_COMPUTER_RUNTIME_UPLOAD_MAX_BYTES", str(8 * 1024 * 1024)))
NEXUS_LOCAL_TENANT_SLUG = os.environ.get("NEXUS_LOCAL_TENANT_SLUG", "nexus-local")
NEXUS_LOCAL_REMOTE_WORKSPACE_LIMIT = int(
    os.environ.get("NEXUS_LOCAL_REMOTE_WORKSPACE_LIMIT", "10" if DEBUG else "0")
)
NEXUS_LOCAL_MOBILE_DEVICE_LIMIT = int(
    os.environ.get("NEXUS_LOCAL_MOBILE_DEVICE_LIMIT", "10" if DEBUG else "0")
)
NEXUS_MOBILE_PAIRING_TTL_SECONDS = int(os.environ.get("NEXUS_MOBILE_PAIRING_TTL_SECONDS", "600"))
NEXUS_MOBILE_ONLINE_STALE_SECONDS = int(os.environ.get("NEXUS_MOBILE_ONLINE_STALE_SECONDS", "90"))
NEXUS_MOBILE_SCREENSHOT_TTL_SECONDS = int(os.environ.get("NEXUS_MOBILE_SCREENSHOT_TTL_SECONDS", "300"))
NEXUS_MOBILE_SCREENSHOT_MAX_BYTES = int(os.environ.get("NEXUS_MOBILE_SCREENSHOT_MAX_BYTES", "1048576"))
NEXUS_AGENT_RUNTIME_RUNNER = os.environ.get("NEXUS_AGENT_RUNTIME_RUNNER", "controller" if NEXUS_PRODUCTION else "fake")
NEXUS_AGENT_RUNTIME_CONTROLLER_SOCKET = os.environ.get(
    "NEXUS_AGENT_RUNTIME_CONTROLLER_SOCKET", "/run/nexus-agent/controller.sock"
)
NEXUS_AGENT_RUNTIME_CONTROLLER_TOKEN = os.environ.get("NEXUS_AGENT_RUNTIME_CONTROLLER_TOKEN", "")
NEXUS_AGENT_RUNTIME_CONTROLLER_TIMEOUT_SECONDS = float(
    os.environ.get("NEXUS_AGENT_RUNTIME_CONTROLLER_TIMEOUT_SECONDS", "1900")
)
NEXUS_AGENT_RUNTIME_CONTROLLER_WORKERS = int(
    os.environ.get("NEXUS_AGENT_RUNTIME_CONTROLLER_WORKERS", "8")
)
NEXUS_AGENT_RUNTIME_CONTROLLER_MAX_FRAME_BYTES = int(
    os.environ.get("NEXUS_AGENT_RUNTIME_CONTROLLER_MAX_FRAME_BYTES", str(16 * 1024 * 1024))
)
NEXUS_AGENT_RUNTIME_CONTROLLER_CONTAINER = os.environ.get("NEXUS_AGENT_RUNTIME_CONTROLLER_CONTAINER", "")
NEXUS_AGENT_PYTHON_BUILDS_ENABLED = os.environ.get("NEXUS_AGENT_PYTHON_BUILDS_ENABLED", "0") == "1"
# Single-host launcher heartbeat transport. Production always uses shared Redis.
NEXUS_AGENT_BUILDER_STATUS_FILE = os.environ.get("NEXUS_AGENT_BUILDER_STATUS_FILE", "")
NEXUS_AGENT_BUILDER_STATUS_GENERATION = os.environ.get("NEXUS_AGENT_BUILDER_STATUS_GENERATION", "")
NEXUS_AGENT_PYTHON_BASE_IMAGE = os.environ.get("NEXUS_AGENT_PYTHON_BASE_IMAGE", "")
NEXUS_AGENT_PYTHON_DOCKER = os.environ.get("NEXUS_AGENT_PYTHON_DOCKER", "docker")
NEXUS_AGENT_PYTHON_DEPENDENCY_NETWORK = os.environ.get("NEXUS_AGENT_PYTHON_DEPENDENCY_NETWORK", "bridge")
NEXUS_AGENT_PYTHON_ISOLATION_READY = os.environ.get("NEXUS_AGENT_PYTHON_ISOLATION_READY", "0") == "1"
NEXUS_AGENT_PYTHON_IMAGE_ARTIFACT_MAX_BYTES = int(
    os.environ.get("NEXUS_AGENT_PYTHON_IMAGE_ARTIFACT_MAX_BYTES", str(2 * 1024 * 1024 * 1024))
)
NEXUS_AGENT_BUILDER_STALE_SECONDS = int(os.environ.get("NEXUS_AGENT_BUILDER_STALE_SECONDS", "30"))
NEXUS_AGENT_RUNTIME_MEMORY_LIMIT = os.environ.get("NEXUS_AGENT_RUNTIME_MEMORY_LIMIT", "512m")
NEXUS_AGENT_RUNTIME_CPU_LIMIT = os.environ.get("NEXUS_AGENT_RUNTIME_CPU_LIMIT", "1")
NEXUS_AGENT_RUNTIME_HOST_ID = os.environ.get("NEXUS_AGENT_RUNTIME_HOST_ID", "local")
NEXUS_AGENT_RUNTIME_EGRESS_POLICY_READY = os.environ.get("NEXUS_AGENT_RUNTIME_EGRESS_POLICY_READY", "0") == "1"
NEXUS_AGENT_IMAGE_ADMISSION_COMMAND = json.loads(os.environ.get("NEXUS_AGENT_IMAGE_ADMISSION_COMMAND", "[]"))
NEXUS_AGENT_RUNTIME_RECONCILE_SECONDS = int(os.environ.get("NEXUS_AGENT_RUNTIME_RECONCILE_SECONDS", "30"))
NEXUS_AGENT_RUNTIME_LEASE_SECONDS = int(os.environ.get("NEXUS_AGENT_RUNTIME_LEASE_SECONDS", "60"))
NEXUS_AGENT_RUNTIME_RECONCILE_LOCK_SECONDS = int(
    os.environ.get("NEXUS_AGENT_RUNTIME_RECONCILE_LOCK_SECONDS", "300")
)
NEXUS_AGENT_RUNTIME_RECONCILE_BATCH_SIZE = int(
    os.environ.get("NEXUS_AGENT_RUNTIME_RECONCILE_BATCH_SIZE", "100")
)
NEXUS_AGENT_RUNTIME_ORPHAN_GRACE_SECONDS = int(
    os.environ.get("NEXUS_AGENT_RUNTIME_ORPHAN_GRACE_SECONDS", "3600")
)
NEXUS_AGENT_RECONCILER_STALE_SECONDS = int(
    os.environ.get("NEXUS_AGENT_RECONCILER_STALE_SECONDS", "90")
)
NEXUS_AGENT_RUNTIME_DOCKER_NETWORK = os.environ.get("NEXUS_AGENT_RUNTIME_DOCKER_NETWORK", "bridge")
NEXUS_AGENT_RUNTIME_PIDS_LIMIT = int(os.environ.get("NEXUS_AGENT_RUNTIME_PIDS_LIMIT", "256"))
NEXUS_AGENT_RUNTIME_CONTAINER_PORT = int(os.environ.get("NEXUS_AGENT_RUNTIME_CONTAINER_PORT", "8000"))
NEXUS_AGENT_RUNTIME_USER = os.environ.get("NEXUS_AGENT_RUNTIME_USER", "65532:65532")
NEXUS_AGENT_RUNTIME_TMPFS_SPEC = os.environ.get("NEXUS_AGENT_RUNTIME_TMPFS_SPEC", "/tmp:rw,nosuid,nodev,size=256m")
NEXUS_AGENT_RUNTIME_SHM_SIZE = os.environ.get("NEXUS_AGENT_RUNTIME_SHM_SIZE", "256m")
NEXUS_AGENT_BROWSER_CAPTURE_INTERVAL_SECONDS = os.environ.get("NEXUS_AGENT_BROWSER_CAPTURE_INTERVAL_SECONDS", "1.0")
NEXUS_AGENT_BROWSER_CAPTURE_MAX_IMAGE_BYTES = int(os.environ.get("NEXUS_AGENT_BROWSER_CAPTURE_MAX_IMAGE_BYTES", "900000"))
NEXUS_ATTACHED_BROWSER_MAX_PER_COMPUTER = int(os.environ.get("NEXUS_ATTACHED_BROWSER_MAX_PER_COMPUTER", "2"))
NEXUS_ATTACHED_BROWSER_IDLE_SECONDS = int(os.environ.get("NEXUS_ATTACHED_BROWSER_IDLE_SECONDS", "900"))
NEXUS_AGENT_RUNTIME_START_ATTEMPTS = int(os.environ.get("NEXUS_AGENT_RUNTIME_START_ATTEMPTS", "30"))
NEXUS_AGENT_RUNTIME_START_DELAY_SECONDS = float(os.environ.get("NEXUS_AGENT_RUNTIME_START_DELAY_SECONDS", "0.25"))
NEXUS_AGENT_RUNTIME_VERIFY_IMAGE_ON_REGISTER = os.environ.get("NEXUS_AGENT_RUNTIME_VERIFY_IMAGE_ON_REGISTER", "1") == "1"
NEXUS_EDGE_REQUIRE_MTLS_HEADER = os.environ.get("NEXUS_EDGE_REQUIRE_MTLS_HEADER", "0" if DEBUG else "1") == "1"
NEXUS_EDGE_ALLOW_WITHOUT_MTLS = os.environ.get("NEXUS_EDGE_ALLOW_WITHOUT_MTLS", "1" if DEBUG else "0") == "1"
NEXUS_EDGE_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("NEXUS_EDGE_REQUEST_TIMEOUT_SECONDS", "30"))
NEXUS_EDGE_IPV6_SOURCE_ADDRESS = os.environ.get("NEXUS_EDGE_IPV6_SOURCE_ADDRESS", "").strip()
NEXUS_EDGE_CLIENT_CERT_FILE = os.environ.get("NEXUS_EDGE_CLIENT_CERT_FILE", "")
NEXUS_EDGE_CLIENT_KEY_FILE = os.environ.get("NEXUS_EDGE_CLIENT_KEY_FILE", "")
NEXUS_EDGE_CA_FILE = os.environ.get("NEXUS_EDGE_CA_FILE", "")
NEXUS_EDGE_CA_KEY_FILE = os.environ.get("NEXUS_EDGE_CA_KEY_FILE", "")
NEXUS_EDGE_CA_BUNDLES = json.loads(os.environ.get("NEXUS_EDGE_CA_BUNDLES", "{}"))
NEXUS_EDGE_INGRESS_CA_BUNDLE_ID = os.environ.get("NEXUS_EDGE_INGRESS_CA_BUNDLE_ID", "edge-local-ca")
NEXUS_EDGE_INGRESS_CERT_DAYS = int(os.environ.get("NEXUS_EDGE_INGRESS_CERT_DAYS", "90"))
NEXUS_EDGE_DEVICE_CA_CERT_FILE = os.environ.get("NEXUS_EDGE_DEVICE_CA_CERT_FILE", "")
NEXUS_EDGE_DEVICE_CA_KEY_FILE = os.environ.get("NEXUS_EDGE_DEVICE_CA_KEY_FILE", "")
NEXUS_EDGE_DEVICE_CERT_DAYS = int(os.environ.get("NEXUS_EDGE_DEVICE_CERT_DAYS", "90"))
NEXUS_EDGE_JWT_PRIVATE_KEY_FILE = os.environ.get("NEXUS_EDGE_JWT_PRIVATE_KEY_FILE", "")
NEXUS_EDGE_JWT_KEY_ID = os.environ.get("NEXUS_EDGE_JWT_KEY_ID", "edge-rs256-1")
NEXUS_EDGE_JWT_ISSUER = os.environ.get("NEXUS_EDGE_JWT_ISSUER", "https://nexus.local/edge")
NEXUS_EDGE_JWT_TTL_SECONDS = int(os.environ.get("NEXUS_EDGE_JWT_TTL_SECONDS", "120"))
NEXUS_RELAY_ENDPOINTS = json.loads(os.environ.get("NEXUS_RELAY_ENDPOINTS", "{}"))
NEXUS_RELAY_TICKET_KEYS = json.loads(os.environ.get("NEXUS_RELAY_TICKET_KEYS", "{}"))
NEXUS_RELAY_TICKET_ACTIVE_KEY_ID = os.environ.get("NEXUS_RELAY_TICKET_ACTIVE_KEY_ID", "relay-ticket-1")
NEXUS_RELAY_TICKET_TTL_SECONDS = int(os.environ.get("NEXUS_RELAY_TICKET_TTL_SECONDS", "120"))
NEXUS_RELAY_CA_FILE = os.environ.get("NEXUS_RELAY_CA_FILE", "")
NEXUS_RELAY_CLIENT_CERT_FILE = os.environ.get("NEXUS_RELAY_CLIENT_CERT_FILE", "")
NEXUS_RELAY_CLIENT_KEY_FILE = os.environ.get("NEXUS_RELAY_CLIENT_KEY_FILE", "")
NEXUS_RELAY_JWT_PRIVATE_KEY_FILE = os.environ.get("NEXUS_RELAY_JWT_PRIVATE_KEY_FILE", "")
NEXUS_RELAY_JWT_KEY_ID = os.environ.get("NEXUS_RELAY_JWT_KEY_ID", "relay-rs256-1")
NEXUS_RELAY_JWT_ISSUER = os.environ.get("NEXUS_RELAY_JWT_ISSUER", "https://nexus.local/relay")
NEXUS_RELAY_JWT_TTL_SECONDS = int(os.environ.get("NEXUS_RELAY_JWT_TTL_SECONDS", "60"))
NEXUS_RELAY_FORWARDING_PRIVATE_KEY_FILE = os.environ.get("NEXUS_RELAY_FORWARDING_PRIVATE_KEY_FILE", "")
NEXUS_RELAY_FORWARDING_KEY_ID = os.environ.get("NEXUS_RELAY_FORWARDING_KEY_ID", "nexus-cloud-1")
NEXUS_RELAY_FORWARDING_ISSUER = os.environ.get("NEXUS_RELAY_FORWARDING_ISSUER", "nexus-cloud")
NEXUS_RELAY_FORWARDING_TTL_SECONDS = int(os.environ.get("NEXUS_RELAY_FORWARDING_TTL_SECONDS", "30"))
NEXUS_RELAY_SOURCE_ROUTER_ID = os.environ.get("NEXUS_RELAY_SOURCE_ROUTER_ID", "nexus-cloud")
NEXUS_RELAY_CONTROL_FILE = os.environ.get("NEXUS_RELAY_CONTROL_FILE", "")
NEXUS_EDGE_PRESENCE_STATUS_FILE = os.environ.get("NEXUS_EDGE_PRESENCE_STATUS_FILE", "")
NEXUS_RELAY_RUNTIME_PROBE = os.environ.get("NEXUS_RELAY_RUNTIME_PROBE", "0") == "1"
NEXUS_RELAY_RUNTIME_PROBE_TIMEOUT_SECONDS = float(
    os.environ.get("NEXUS_RELAY_RUNTIME_PROBE_TIMEOUT_SECONDS", "0.35")
)
NEXUS_PUBLIC_BASE_URL = os.environ.get("NEXUS_PUBLIC_BASE_URL", "http://localhost:8000")
# Public browser/OAuth traffic and authenticated Edge traffic intentionally use
# different TLS listeners. The Edge origin is returned during the one-time
# public pairing exchange so the connector can move to a CERT_REQUIRED ingress
# without exposing a TLS CertificateRequest to ordinary browsers.
NEXUS_EDGE_INGRESS_BASE_URL = os.environ.get(
    "NEXUS_EDGE_INGRESS_BASE_URL",
    NEXUS_PUBLIC_BASE_URL,
).rstrip("/")
NEXUS_GOOGLE_OAUTH_CLIENT_SECRET_FILE = os.environ.get(
    "NEXUS_GOOGLE_OAUTH_CLIENT_SECRET_FILE",
    str(BASE_DIR.parent / "googleoauthsec.json"),
)
NEXUS_GOOGLE_OAUTH_REDIRECT_URI = os.environ.get(
    "NEXUS_GOOGLE_OAUTH_REDIRECT_URI",
    f"{NEXUS_PUBLIC_BASE_URL.rstrip('/')}/api/v1/auth/google/callback/",
)
NEXUS_GOOGLE_OAUTH_SIGNUP_MODE = os.environ.get("NEXUS_GOOGLE_OAUTH_SIGNUP_MODE", "open").strip().lower()
NEXUS_GOOGLE_OAUTH_ALLOWED_DOMAINS = tuple(
    value.strip().lower()
    for value in os.environ.get("NEXUS_GOOGLE_OAUTH_ALLOWED_DOMAINS", "").split(",")
    if value.strip()
)
NEXUS_GOOGLE_OAUTH_STATE_TTL_SECONDS = int(os.environ.get("NEXUS_GOOGLE_OAUTH_STATE_TTL_SECONDS", "600"))
NEXUS_GITHUB_OAUTH_CLIENT_SECRET_FILE = os.environ.get(
    "NEXUS_GITHUB_OAUTH_CLIENT_SECRET_FILE",
    str(BASE_DIR.parent / "githuboauthsec.json"),
)
NEXUS_GITHUB_OAUTH_CLIENT_ID = os.environ.get("NEXUS_GITHUB_OAUTH_CLIENT_ID", "").strip()
NEXUS_GITHUB_OAUTH_CLIENT_SECRET = os.environ.get("NEXUS_GITHUB_OAUTH_CLIENT_SECRET", "").strip()
NEXUS_GITHUB_OAUTH_REDIRECT_URI = os.environ.get(
    "NEXUS_GITHUB_OAUTH_REDIRECT_URI",
    f"{NEXUS_PUBLIC_BASE_URL.rstrip('/')}/api/v1/auth/github/callback/",
)
NEXUS_GITHUB_OAUTH_SIGNUP_MODE = os.environ.get("NEXUS_GITHUB_OAUTH_SIGNUP_MODE", "open").strip().lower()
NEXUS_GITHUB_OAUTH_ALLOWED_DOMAINS = tuple(
    value.strip().lower()
    for value in os.environ.get("NEXUS_GITHUB_OAUTH_ALLOWED_DOMAINS", "").split(",")
    if value.strip()
)
NEXUS_GITHUB_OAUTH_STATE_TTL_SECONDS = int(os.environ.get("NEXUS_GITHUB_OAUTH_STATE_TTL_SECONDS", "600"))
NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL = os.environ.get("NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL", NEXUS_PUBLIC_BASE_URL)
NEXUS_AGENT_RUNTIME_CLOUD_CA_FILE = os.environ.get("NEXUS_AGENT_RUNTIME_CLOUD_CA_FILE", "")
NEXUS_AGENT_WORKSPACE_API_BASE_URL = os.environ.get("NEXUS_AGENT_WORKSPACE_API_BASE_URL", NEXUS_PUBLIC_BASE_URL)
NEXUS_AGENT_WORKSPACE_MAX_FILE_BYTES = int(os.environ.get("NEXUS_AGENT_WORKSPACE_MAX_FILE_BYTES", "1048576"))
NEXUS_STRIPE_SECRET_KEY = os.environ.get("NEXUS_STRIPE_SECRET_KEY", "")
NEXUS_STRIPE_CONNECT_ENABLED = os.environ.get("NEXUS_STRIPE_CONNECT_ENABLED", "0") == "1"
NEXUS_STRIPE_CONNECT_MODE = os.environ.get("NEXUS_STRIPE_CONNECT_MODE", "test")
NEXUS_STRIPE_CONNECT_LIVE_ENABLED = os.environ.get("NEXUS_STRIPE_CONNECT_LIVE_ENABLED", "0") == "1"
NEXUS_STRIPE_CONNECT_SECRET_KEY = os.environ.get("NEXUS_STRIPE_CONNECT_SECRET_KEY", "")
NEXUS_STRIPE_CONNECT_WEBHOOK_SECRET = os.environ.get("NEXUS_STRIPE_CONNECT_WEBHOOK_SECRET", "")
NEXUS_STRIPE_WEBHOOK_SECRET = os.environ.get("NEXUS_STRIPE_WEBHOOK_SECRET", "")
NEXUS_STRIPE_WEBHOOK_TOLERANCE_SECONDS = int(os.environ.get("NEXUS_STRIPE_WEBHOOK_TOLERANCE_SECONDS", "300"))
NEXUS_ALIPAY_APP_ID = os.environ.get("NEXUS_ALIPAY_APP_ID", "")
NEXUS_ALIPAY_PRIVATE_KEY = os.environ.get("NEXUS_ALIPAY_PRIVATE_KEY", "")
NEXUS_ALIPAY_PUBLIC_KEY = os.environ.get("NEXUS_ALIPAY_PUBLIC_KEY", "")
NEXUS_ALIPAY_GATEWAY_URL = os.environ.get("NEXUS_ALIPAY_GATEWAY_URL", "https://openapi.alipay.com/gateway.do")
NEXUS_WECHAT_PAY_APPID = os.environ.get("NEXUS_WECHAT_PAY_APPID", "")
NEXUS_WECHAT_PAY_MCHID = os.environ.get("NEXUS_WECHAT_PAY_MCHID", "")
NEXUS_WECHAT_PAY_MERCHANT_SERIAL_NO = os.environ.get("NEXUS_WECHAT_PAY_MERCHANT_SERIAL_NO", "")
NEXUS_WECHAT_PAY_PRIVATE_KEY = os.environ.get("NEXUS_WECHAT_PAY_PRIVATE_KEY", "")
NEXUS_WECHAT_PAY_API_V3_KEY = os.environ.get("NEXUS_WECHAT_PAY_API_V3_KEY", "")
NEXUS_WECHAT_PAY_PLATFORM_CERTIFICATE = os.environ.get("NEXUS_WECHAT_PAY_PLATFORM_CERTIFICATE", "")
NEXUS_WECHAT_PAY_GATEWAY_URL = os.environ.get("NEXUS_WECHAT_PAY_GATEWAY_URL", "https://api.mch.weixin.qq.com")
NEXUS_PROMETHEUS_ENABLED = os.environ.get("NEXUS_PROMETHEUS_ENABLED", "1") == "1"
NEXUS_OTEL_ENABLED = os.environ.get("NEXUS_OTEL_ENABLED", "0") == "1"
NEXUS_OTEL_SERVICE_NAME = os.environ.get("NEXUS_OTEL_SERVICE_NAME", "nexus-server")
NEXUS_OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get("NEXUS_OTEL_EXPORTER_OTLP_ENDPOINT", "")
NEXUS_ALERT_EVALUATION_INTERVAL_SECONDS = int(os.environ.get("NEXUS_ALERT_EVALUATION_INTERVAL_SECONDS", "60"))
NEXUS_METRIC_SNAPSHOT_INTERVAL_SECONDS = int(os.environ.get("NEXUS_METRIC_SNAPSHOT_INTERVAL_SECONDS", "300"))
NEXUS_REPORT_SCHEDULE_INTERVAL_SECONDS = int(os.environ.get("NEXUS_REPORT_SCHEDULE_INTERVAL_SECONDS", "300"))
NEXUS_MONITOR_COLLECTOR_SECONDS = NEXUS_METRIC_SNAPSHOT_INTERVAL_SECONDS
NEXUS_MONITOR_EVALUATOR_SECONDS = NEXUS_ALERT_EVALUATION_INTERVAL_SECONDS
NEXUS_MONITOR_REPORTS_SECONDS = NEXUS_REPORT_SCHEDULE_INTERVAL_SECONDS
NEXUS_MONITOR_DELIVERY_SECONDS = 30
NEXUS_MONITOR_DELIVERY_MAX_ATTEMPTS = 5
# Preserve existing data until an operator explicitly opts into bounded retention.
NEXUS_MONITOR_RETENTION_ENABLED = os.environ.get("NEXUS_MONITOR_RETENTION_ENABLED", "0") == "1"
NEXUS_MONITOR_RETENTION_DAYS = int(os.environ.get("NEXUS_MONITOR_RETENTION_DAYS", "90"))
EMAIL_TIMEOUT = int(os.environ.get("NEXUS_EMAIL_TIMEOUT", "10"))
NEXUS_REPORT_EMAIL_FROM = os.environ.get("NEXUS_REPORT_EMAIL_FROM", "noreply@nexus.local")
EMAIL_BACKEND = os.environ.get("NEXUS_EMAIL_BACKEND", "django.core.mail.backends.locmem.EmailBackend")
EMAIL_HOST = os.environ.get("NEXUS_EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("NEXUS_EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.environ.get("NEXUS_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("NEXUS_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("NEXUS_EMAIL_USE_TLS", "0") == "1"
DEFAULT_FROM_EMAIL = os.environ.get("NEXUS_EMAIL_FROM", NEXUS_REPORT_EMAIL_FROM)

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.common.authentication.NexusBearerAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "apps.common.renderers.UnifiedJSONRenderer",
    ],
    "DEFAULT_SCHEMA_CLASS": "apps.common.openapi.NexusAutoSchema",
    "EXCEPTION_HANDLER": "apps.common.exceptions.unified_exception_handler",
}

# Browser sessions are used by the web console. Bearer tokens, API keys, and
# service-account tokens remain available for non-browser clients.
NEXUS_SECURE_COOKIES = os.environ.get("NEXUS_SECURE_COOKIES", "0" if DEBUG else "1") == "1"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = NEXUS_SECURE_COOKIES
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = int(os.environ.get("NEXUS_SESSION_COOKIE_AGE", "28800"))
SESSION_SAVE_EVERY_REQUEST = True
CSRF_COOKIE_SECURE = NEXUS_SECURE_COOKIES
CSRF_COOKIE_SAMESITE = "Lax"
NEXUS_CONFIGURED_CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("NEXUS_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
NEXUS_DEVELOPMENT_CSRF_TRUSTED_ORIGINS = (
    [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://127.0.0.1:5175",
        "http://localhost:5175",
    ]
    if DEBUG
    else []
)
CSRF_TRUSTED_ORIGINS = list(
    dict.fromkeys(
        [
            *NEXUS_DEVELOPMENT_CSRF_TRUSTED_ORIGINS,
            *NEXUS_CONFIGURED_CSRF_TRUSTED_ORIGINS,
        ]
    )
)

SPECTACULAR_SETTINGS = {
    "TITLE": "Nexus API",
    "DESCRIPTION": "Multi-tenant AI Gateway, Agent Hosting, Dataset Hosting, Billing, and Access Control API.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    "POSTPROCESSING_HOOKS": [
        "apps.common.openapi.postprocess_schema",
    ],
}

NEXUS_API_PREFIX = "/api/v1/"
NEXUS_REQUEST_ID_HEADER = "HTTP_X_REQUEST_ID"
NEXUS_TENANT_HEADER = "HTTP_X_NEXUS_TENANT"
NEXUS_PROJECT_HEADER = "HTTP_X_NEXUS_PROJECT"
NEXUS_JWT_ALGORITHM = os.environ.get("NEXUS_JWT_ALGORITHM", "HS256")
NEXUS_JWT_ISSUER = os.environ.get("NEXUS_JWT_ISSUER", "nexus-server")
NEXUS_JWT_AUDIENCE = os.environ.get("NEXUS_JWT_AUDIENCE", "nexus-client-cli")
NEXUS_JWT_LEEWAY_SECONDS = int(os.environ.get("NEXUS_JWT_LEEWAY_SECONDS", "30"))
NEXUS_WEB_PUSH_ENABLED = os.environ.get("NEXUS_WEB_PUSH_ENABLED", "0") == "1"
NEXUS_VAPID_PUBLIC_KEY = os.environ.get("NEXUS_VAPID_PUBLIC_KEY", "")
NEXUS_VAPID_PRIVATE_KEY = os.environ.get("NEXUS_VAPID_PRIVATE_KEY", "")
NEXUS_VAPID_SUBJECT = os.environ.get("NEXUS_VAPID_SUBJECT", "")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = int(os.environ.get("CELERY_TASK_TIME_LIMIT", "300"))
CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_TASK_ALWAYS_EAGER", "1") == "1"
CELERY_BEAT_SCHEDULER = os.environ.get(
    "CELERY_BEAT_SCHEDULER",
    "config.beat.SingletonRedisScheduler" if NEXUS_PRODUCTION else "celery.beat.PersistentScheduler",
)
NEXUS_RUNTIME_HEARTBEAT_INTERVAL_SECONDS = int(
    os.environ.get("NEXUS_RUNTIME_HEARTBEAT_INTERVAL_SECONDS", "15")
)
NEXUS_RUNTIME_HEARTBEAT_STALE_SECONDS = int(
    os.environ.get("NEXUS_RUNTIME_HEARTBEAT_STALE_SECONDS", "45")
)
NEXUS_DEPLOYMENT_HEALTH_TIMEOUT = int(os.environ.get("NEXUS_DEPLOYMENT_HEALTH_TIMEOUT", "5"))
NEXUS_SLA_PROBE_ALLOW_HTTP_DIRECT_API = os.environ.get("NEXUS_SLA_PROBE_ALLOW_HTTP_DIRECT_API", "0") == "1"
NEXUS_SLA_PROBE_ALLOWED_PRIVATE_HOSTS = os.environ.get("NEXUS_SLA_PROBE_ALLOWED_PRIVATE_HOSTS", "")
NEXUS_INBOX_RECONCILE_BATCH_SIZE = int(os.environ.get("NEXUS_INBOX_RECONCILE_BATCH_SIZE", "100"))
NEXUS_INBOX_PUSH_CONCURRENCY = int(os.environ.get("NEXUS_INBOX_PUSH_CONCURRENCY", "4"))
CELERY_BEAT_SCHEDULE = {
    "reconcile-work-inbox": {
        "task": "apps.notifications.tasks.reconcile_work_inbox",
        "schedule": 60,
    },
    "deliver-work-inbox-push": {
        "task": "apps.notifications.tasks.deliver_work_inbox_push",
        "schedule": 15,
    },
    "process-agent-file-transfers": {"task": "apps.datasets.tasks.process_agent_file_transfers", "schedule": 5.0,
        "options": {"queue": "dataset-imports"}},
    "dispatch-dataset-imports": {
        "task": "apps.datasets.tasks.dispatch_dataset_imports",
        "schedule": 10.0,
    },
    "cleanup-dataset-transfers": {
        "task": "apps.datasets.tasks.cleanup_dataset_transfers",
        "schedule": 300.0,
    },
    "provider-import-credential-cleanup": {
        "task": "apps.providers.tasks.clear_expired_provider_imports",
        "schedule": 60.0,
    },
    "expire-image-operations": {
        "task": "apps.gateway.tasks.expire_image_operations",
        "schedule": 60,
    },
    "nexus-production-runtime-heartbeat": {
        "task": "config.tasks.record_production_runtime_heartbeat",
        "schedule": NEXUS_RUNTIME_HEARTBEAT_INTERVAL_SECONDS,
    },
    "refresh-provider-runtime-models": {
        "task": "apps.providers.tasks.refresh_active_provider_runtime_models",
        # Poll due retries promptly; successful catalogs retain their normal
        # discovery interval inside the task and are not probed every minute.
        "schedule": min(NEXUS_PROVIDER_HEALTH_INTERVAL_SECONDS, NEXUS_PROVIDER_MODEL_DISCOVERY_INTERVAL_SECONDS),
    },
    "reconcile-provider-runtime-health": {
        "task": "apps.providers.tasks.reconcile_provider_runtime_health_task",
        "schedule": NEXUS_PROVIDER_HEALTH_INTERVAL_SECONDS,
    },
    "expire-edge-router-presence": {
        "task": "apps.agents.tasks.expire_edge_node_presence_task",
        "schedule": 60,
    },
    "reconcile-docker-agent-runtimes": {
        "task": "apps.agents.tasks.reconcile_docker_agent_runtimes",
        "schedule": NEXUS_AGENT_RUNTIME_RECONCILE_SECONDS,
    },
    "check-active-deployment-health": {
        "task": "apps.deployments.tasks.check_all_active_deployments",
        "schedule": int(os.environ.get("NEXUS_DEPLOYMENT_HEALTH_INTERVAL_SECONDS", "300")),
    },
    "capture-metric-snapshots": {
        "task": "apps.metrics.tasks.capture_metric_snapshots_task",
        "schedule": NEXUS_METRIC_SNAPSHOT_INTERVAL_SECONDS,
    },
    "evaluate-alert-rules": {
        "task": "apps.metrics.tasks.evaluate_alert_rules_task",
        "schedule": NEXUS_ALERT_EVALUATION_INTERVAL_SECONDS,
    },
    "send-scheduled-reports": {
        "task": "apps.metrics.tasks.send_scheduled_reports_task",
        "schedule": NEXUS_REPORT_SCHEDULE_INTERVAL_SECONDS,
    },
    "deliver-monitoring-notifications": {
        "task": "apps.metrics.tasks.deliver_monitoring_notifications_task", "schedule": 30,
    },
    "cleanup-monitoring-history": {
        "task": "apps.metrics.tasks.cleanup_monitoring_task", "schedule": 3600,
    },
}

NEXUS_AGENT_FILE_MAX_BYTES = int(os.environ.get("NEXUS_AGENT_FILE_MAX_BYTES", str(5 * 1024**3)))
NEXUS_AGENT_FILE_TENANT_BYTES = int(os.environ.get("NEXUS_AGENT_FILE_TENANT_BYTES", str(50 * 1024**3)))

if NEXUS_PRODUCTION:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": "nexus",
            "TIMEOUT": 300,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "nexus-server",
        }
    }


if NEXUS_PRODUCTION:
    from config.production import ProductionRuntimeConfig, enforce_production_configuration

    enforce_production_configuration(
        ProductionRuntimeConfig(
            environment=NEXUS_ENVIRONMENT,
            debug=DEBUG,
            database_configured=bool(DATABASE_URL or os.environ.get("POSTGRES_DB")),
            database_url=DATABASE_URL or "",
            database_engine=str(DATABASES["default"].get("ENGINE", "")),
            redis_configured=bool(os.environ.get("REDIS_URL", "").strip()),
            redis_url=REDIS_URL,
            celery_broker_url=CELERY_BROKER_URL,
            celery_result_backend=CELERY_RESULT_BACKEND,
            celery_always_eager=CELERY_TASK_ALWAYS_EAGER,
            celery_beat_scheduler=CELERY_BEAT_SCHEDULER,
            process_role=NEXUS_PROCESS_ROLE,
            shared_storage_configured=bool(os.environ.get("NEXUS_SHARED_STORAGE_ROOT", "").strip()),
            shared_storage_root=NEXUS_SHARED_STORAGE_ROOT,
            websocket_routing_mode=NEXUS_WEBSOCKET_ROUTING_MODE,
            secret_encryption_keys_configured=bool(NEXUS_SECRET_ENCRYPTION_KEYS.strip()),
            provider_runtime_runner=NEXUS_PROVIDER_RUNTIME_RUNNER,
            provider_runtime_controller_socket=NEXUS_PROVIDER_RUNTIME_CONTROLLER_SOCKET,
            provider_runtime_controller_token_configured=bool(NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN.strip()),
            agent_runtime_runner=NEXUS_AGENT_RUNTIME_RUNNER,
            agent_runtime_controller_socket=NEXUS_AGENT_RUNTIME_CONTROLLER_SOCKET,
            agent_runtime_controller_token_configured=bool(NEXUS_AGENT_RUNTIME_CONTROLLER_TOKEN.strip()),
            agent_runtime_host_id=NEXUS_AGENT_RUNTIME_HOST_ID,
            agent_runtime_egress_policy_ready=NEXUS_AGENT_RUNTIME_EGRESS_POLICY_READY,
            agent_image_admission_configured=bool(NEXUS_AGENT_IMAGE_ADMISSION_COMMAND),
            agent_python_builds_enabled=NEXUS_AGENT_PYTHON_BUILDS_ENABLED,
            agent_python_isolation_ready=NEXUS_AGENT_PYTHON_ISOLATION_READY,
            agent_python_base_image=NEXUS_AGENT_PYTHON_BASE_IMAGE,
        )
    )
