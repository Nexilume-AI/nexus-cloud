"""Explicit Community production host; never inherits Cloud or test settings."""
import os
from pathlib import Path

from .composition import *
from .host_config import load_config

_host = load_config(os.environ)
BASE_DIR = Path(__file__).resolve().parents[1]
SECRET_KEY = _host["secret_key"]
NEXUS_SECRET_ENCRYPTION_KEYS = _host["encryption_key"]
DEBUG = False
NEXUS_PRODUCTION = True
NEXUS_ENVIRONMENT = "production"
NEXUS_PROCESS_ROLE = os.environ.get("NEXUS_PROCESS_ROLE", "web")
NEXUS_PUBLIC_BASE_URL = _host["public_origin"]
ALLOWED_HOSTS = [_host["allowed_host"]]
CSRF_TRUSTED_ORIGINS = [NEXUS_PUBLIC_BASE_URL]
_local_http_port = os.environ.get("NEXUS_PERSONAL_LOCAL_HTTP_PORT", "")
_local_http = False
if _local_http_port:
    from urllib.parse import urlsplit as _urlsplit
    try:
        _local_port = int(_local_http_port)
        _configured_origin = _urlsplit(_host["public_origin"])
        if (not 1024 <= _local_port <= 65535 or _configured_origin.hostname not in {"localhost", "127.0.0.1", "::1"}
                or (_configured_origin.port or 443) != _local_port):
            raise ValueError()
    except (TypeError, ValueError):
        raise RuntimeError("PERSONAL_LOCAL_HTTP_INVALID: Use only the configured loopback port.") from None
    _local_http = True
    NEXUS_PUBLIC_BASE_URL = f"http://127.0.0.1:{_local_port}"
    ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
    CSRF_TRUSTED_ORIGINS = [NEXUS_PUBLIC_BASE_URL, f"http://localhost:{_local_port}"]
ROOT_URLCONF = "nexus_personal.urls"
ASGI_APPLICATION = "nexus_personal.asgi.application"
USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "nexus_personal.middleware.PersonalRequestIDMiddleware",
    "nexus_personal.frontend.PersonalFrontendMiddleware",
    "django.middleware.common.CommonMiddleware",
    "nexus_personal.middleware.PersonalContextMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
SESSION_COOKIE_NAME = "nexus_personal_session"
CSRF_COOKIE_NAME = "csrftoken"  # Existing browser transport reads this cookie.
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = not _local_http
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = CSRF_COOKIE_SAMESITE = "Lax"
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SECURE_SSL_REDIRECT = not _local_http
SECURE_HSTS_SECONDS = 0 if _local_http else 31536000
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
# The ASGI listener must remain loopback-only behind a proxy which replaces
# forwarded headers. Never trust arbitrary Internet-supplied forwarding headers.
SECURE_PROXY_SSL_HEADER = None if _local_http else ("HTTP_X_FORWARDED_PROTO", "https")
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "nexus_personal.exceptions.personal_exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "nexus_personal.authentication.PersonalBearerAuthentication",
        "nexus_personal.authentication.PersonalSessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["nexus_personal.renderers.PersonalJSONRenderer"],
}
NEXUS_JWT_ALGORITHM = "HS256"
NEXUS_JWT_ISSUER = f"nexus-personal:{_host['instance_id']}"
NEXUS_JWT_AUDIENCE = "nexus-personal-owner"
NEXUS_JWT_LEEWAY_SECONDS = 30
NEXUS_TENANT_HEADER = "X-Nexus-Tenant"
NEXUS_PROJECT_HEADER = "X-Nexus-Project"
NEXUS_REQUEST_ID_HEADER = "X-Request-ID"

_db = _host["database"]
DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql", "NAME": _db["name"],
    "HOST": _db["host"], "PORT": _db["port"], "USER": _db["user"], "PASSWORD": _db["password"],
    "CONN_MAX_AGE": 0, "CONN_HEALTH_CHECKS": True,
    "OPTIONS": {"sslmode": _db["sslmode"], "connect_timeout": 5,
        "application_name": "nexus-personal",
        "options": "-c lock_timeout=5000 -c statement_timeout=60000 -c idle_in_transaction_session_timeout=120000"},
}}
REDIS_URL = _host["redis_url"]
_namespace = f"nexus-personal:{_host['instance_id']}:"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache",
    "LOCATION": REDIS_URL, "KEY_PREFIX": _namespace, "TIMEOUT": 300}}
CELERY_BROKER_URL = CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ALWAYS_EAGER = False
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = CELERY_RESULT_SERIALIZER = "json"
CELERY_BROKER_TRANSPORT_OPTIONS = {"global_keyprefix": _namespace}
CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = {"global_keyprefix": _namespace}
CELERY_TASK_DEFAULT_QUEUE = "personal-default"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 3600
from .worker_composition import BEAT_SCHEDULE
CELERY_BEAT_SCHEDULE = BEAT_SCHEDULE
CELERY_BEAT_SCHEDULE_FILENAME = str(_host["state_dir"] / "run" / "personal-beat")
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
NEXUS_WEBSOCKET_ROUTING_MODE = "redis"

NEXUS_SHARED_STORAGE_ROOT = str(_host["state_dir"] / "storage")
NEXUS_DATASET_STORAGE_ROOT = str(_host["state_dir"] / "storage" / "datasets")
NEXUS_MEDIA_STORAGE_ROOT = str(_host["state_dir"] / "storage" / "media")
MEDIA_ROOT = NEXUS_MEDIA_STORAGE_ROOT
NEXUS_AGENT_STORAGE_ROOT = str(_host["state_dir"] / "storage" / "agents")
NEXUS_ROUTER_STORAGE_ROOT = str(_host["state_dir"] / "storage" / "routers")
NEXUS_PROVIDER_RUNTIME_STORAGE_ROOT = str(_host["state_dir"] / "storage" / "provider-runtimes")
NEXUS_DATASET_SPOOL_MIN_FREE_BYTES = 512 * 1024**2
NEXUS_AGENT_RUNTIME_RUNNER = NEXUS_PROVIDER_RUNTIME_RUNNER = "controller"
NEXUS_AGENT_RUNTIME_CONTROLLER_SOCKET = str(_host["state_dir"] / "run" / "agent-controller.sock")
NEXUS_PROVIDER_RUNTIME_CONTROLLER_SOCKET = str(_host["state_dir"] / "run" / "provider-controller.sock")
NEXUS_AGENT_RUNTIME_CONTROLLER_TOKEN = NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN = ""
NEXUS_PERSONAL_CONFIGURED_CONTROLLERS = tuple(sorted(_host["controllers"]))
NEXUS_AGENT_IMAGE_ADMISSION_COMMAND = []
NEXUS_AGENT_RUNTIME_EGRESS_POLICY_READY = False
NEXUS_AGENT_NETWORK_POLICY_FACTORY = ''
NEXUS_PERSONAL_NETWORK_POLICY = None
NEXUS_PROVIDER_RUNTIME_RELEASE_DIR = ""
NEXUS_PROVIDER_RUNTIME_REQUIRE_VERIFIED_RELEASE = True
for _kind, _controller in _host["controllers"].items():
    _prefix = f"NEXUS_{_kind.upper()}_RUNTIME_CONTROLLER_"
    globals()[_prefix + "SOCKET"] = _controller["socket"]
    globals()[_prefix + "TOKEN"] = _controller["token"]
    if _kind == "agent":
        NEXUS_AGENT_IMAGE_ADMISSION_COMMAND = _controller["image_admission_command"]
        NEXUS_AGENT_RUNTIME_EGRESS_POLICY_READY = _controller["egress_policy_ready"]
        if 'network_policy' in _controller:
            NEXUS_PERSONAL_NETWORK_POLICY = _controller['network_policy']
            NEXUS_AGENT_NETWORK_POLICY_FACTORY = 'nexus_personal.docker_network_policy.from_settings'
    else:
        NEXUS_PROVIDER_RUNTIME_RELEASE_DIR = _controller["release_dir"]
if _host["controllers"]:
    del _kind, _controller, _prefix
NEXUS_AGENT_RUNTIME_HOST_ID = _host["instance_id"]
NEXUS_MONITOR_SMTP_CONFIGURED = _host["monitoring"]["smtp"] is not None
NEXUS_MONITOR_RETENTION_ENABLED = _host["monitoring"]["retention_enabled"]
NEXUS_MONITOR_RETENTION_DAYS = _host["monitoring"]["retention_days"]
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_TIMEOUT = 15
if NEXUS_MONITOR_SMTP_CONFIGURED:
    _smtp = _host["monitoring"]["smtp"]
    EMAIL_HOST, EMAIL_PORT = _smtp["host"], _smtp["port"]
    EMAIL_HOST_USER, EMAIL_HOST_PASSWORD = _smtp["username"], _smtp["password"]
    EMAIL_USE_TLS, EMAIL_USE_SSL = _smtp["tls"] == "starttls", _smtp["tls"] == "tls"
    DEFAULT_FROM_EMAIL = _smtp["from_email"]
    del _smtp
NEXUS_AGENT_RUNTIME_RECONCILE_SECONDS = 60
NEXUS_EDGE_PRESENCE_STATUS_FILE = str(_host["state_dir"] / "run" / "presence-sweeper.json")
NEXUS_AGENT_PYTHON_BUILDS_ENABLED = _host["python_builder"]["enabled"]
NEXUS_AGENT_PYTHON_BASE_IMAGE = _host["python_builder"]["base_image"]
NEXUS_AGENT_PYTHON_ISOLATION_READY = _host["python_builder"]["isolation_ready"]
NEXUS_AGENT_PYTHON_DEPENDENCY_NETWORK = _host["python_builder"]["dependency_network"]
NEXUS_AGENT_BUILDER_STATUS_GENERATION = _host["instance_id"]
NEXUS_ROUTER_RUNTIME_RUNNER = "nsjail"
NEXUS_LEGACY_SSH_ENABLED = False
NEXUS_WORKSPACE_SSH_RUNNER = "disabled"
NEXUS_PROVIDER_ALLOW_HTTP = False
NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS = ""
NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL = NEXUS_PUBLIC_BASE_URL
NEXUS_AGENT_WORKSPACE_API_BASE_URL = NEXUS_PUBLIC_BASE_URL
# Edge identities belong to this installation, never the formal Cloud profile.
# The installer must provision these protected keys/CAs and the verified-mTLS
# proxy. Missing material stays unavailable; no self-signed fallback is issued.
NEXUS_EDGE_REQUIRE_MTLS_HEADER = True
NEXUS_EDGE_JWT_ISSUER = NEXUS_PUBLIC_BASE_URL + "/edge"
NEXUS_EDGE_JWT_KEY_ID = "personal-edge-" + _host["instance_id"]
NEXUS_EDGE_JWT_PRIVATE_KEY_FILE = str(_host["state_dir"] / "keys" / "edge-signing.pem")
NEXUS_EDGE_DEVICE_CA_CERT_FILE = str(_host["state_dir"] / "keys" / "edge-device-ca.pem")
NEXUS_EDGE_DEVICE_CA_KEY_FILE = str(_host["state_dir"] / "keys" / "edge-device-ca-key.pem")
NEXUS_EDGE_CA_FILE = str(_host["state_dir"] / "keys" / "edge-ingress-ca.pem")
NEXUS_EDGE_CA_KEY_FILE = str(_host["state_dir"] / "keys" / "edge-ingress-ca-key.pem")
# No Enterprise frontend is accidentally served from the mixed source tree.
NEXUS_WEB_INDEX_PATH = str(_host["state_dir"] / "web" / "index.html")

# Request bodies, device tickets and credential-bearing URLs are never access logs.
LOGGING = {"version": 1, "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}, "null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {"django.request": {"handlers": ["null"], "propagate": False},
                "django.server": {"handlers": ["null"], "propagate": False}}}
from .relay import settings_for as _relay_settings
globals().update(_relay_settings(_host))
del _relay_settings
del _db, _host
