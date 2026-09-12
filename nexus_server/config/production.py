from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured


PRODUCTION_ENVIRONMENTS = {"prod", "production"}
PROCESS_ROLES = {
    "web",
    "worker",
    "beat",
    "management",
    "provider-controller",
    "agent-controller",
    "agent-builder",
}
REDIS_SCHEMES = {"redis", "rediss"}


@dataclass(frozen=True)
class ProductionRuntimeConfig:
    environment: str
    debug: bool
    database_configured: bool
    database_url: str
    database_engine: str
    redis_configured: bool
    redis_url: str
    celery_broker_url: str
    celery_result_backend: str
    celery_always_eager: bool
    celery_beat_scheduler: str
    process_role: str
    shared_storage_configured: bool
    shared_storage_root: str
    websocket_routing_mode: str
    secret_encryption_keys_configured: bool
    provider_runtime_runner: str
    provider_runtime_controller_socket: str
    provider_runtime_controller_token_configured: bool
    agent_runtime_runner: str
    agent_runtime_controller_socket: str
    agent_runtime_controller_token_configured: bool
    agent_runtime_host_id: str
    agent_runtime_egress_policy_ready: bool
    agent_image_admission_configured: bool
    agent_python_builds_enabled: bool
    agent_python_isolation_ready: bool
    agent_python_base_image: str


def is_production_environment(*, environment: str, debug: bool) -> bool:
    """Treat DEBUG=0 as production even when an environment label was omitted."""

    return environment.strip().lower() in PRODUCTION_ENVIRONMENTS or not debug


def production_configuration_errors(config: ProductionRuntimeConfig) -> list[str]:
    errors: list[str] = []
    if config.debug:
        errors.append("DJANGO_DEBUG must be 0")
    if not config.database_configured:
        errors.append("DATABASE_URL or POSTGRES_DB must be explicitly configured")
    if config.database_url and urlparse(config.database_url).scheme.lower() not in {"postgres", "postgresql"}:
        errors.append("DATABASE_URL must use postgres:// or postgresql://")
    if config.database_engine != "django.db.backends.postgresql":
        errors.append("the default database must use PostgreSQL")
    if not config.redis_configured:
        errors.append("REDIS_URL must be explicitly configured")
    if not _uses_redis(config.redis_url):
        errors.append("REDIS_URL must use redis:// or rediss://")
    if not _uses_redis(config.celery_broker_url):
        errors.append("CELERY_BROKER_URL must use Redis")
    if not _uses_redis(config.celery_result_backend):
        errors.append("CELERY_RESULT_BACKEND must use Redis")
    if config.celery_always_eager:
        errors.append("CELERY_TASK_ALWAYS_EAGER must be 0")
    if config.celery_beat_scheduler != "config.beat.SingletonRedisScheduler":
        errors.append("CELERY_BEAT_SCHEDULER must use config.beat.SingletonRedisScheduler")
    if config.process_role not in PROCESS_ROLES:
        errors.append(
            "NEXUS_PROCESS_ROLE must be one of web, worker, beat, management, provider-controller, agent-controller, or agent-builder; "
            "combined web/worker/beat processes are not supported"
        )
    if not config.shared_storage_configured or not config.shared_storage_root.strip():
        errors.append("NEXUS_SHARED_STORAGE_ROOT must name a shared persistent filesystem")
    if config.websocket_routing_mode != "sticky":
        errors.append(
            "NEXUS_WEBSOCKET_ROUTING_MODE must be sticky until Computer and Terminal sockets "
            "use a cross-instance channel broker"
        )
    if not config.secret_encryption_keys_configured:
        errors.append(
            "NEXUS_SECRET_ENCRYPTION_KEYS must provide a dedicated Fernet keyring; "
            "Provider credentials must not rely on DJANGO_SECRET_KEY"
        )
    expected_provider_runner = "docker" if config.process_role == "provider-controller" else "controller"
    if config.provider_runtime_runner.strip().lower() != expected_provider_runner:
        errors.append(
            f"NEXUS_PROVIDER_RUNTIME_RUNNER must be {expected_provider_runner} for the {config.process_role} process"
        )
    if not config.provider_runtime_controller_socket.strip():
        errors.append("NEXUS_PROVIDER_RUNTIME_CONTROLLER_SOCKET must be configured")
    elif not config.provider_runtime_controller_socket.strip().startswith("/"):
        errors.append("NEXUS_PROVIDER_RUNTIME_CONTROLLER_SOCKET must be an absolute Unix socket in production")
    if not config.provider_runtime_controller_token_configured:
        errors.append("NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN must be configured")
    expected_agent_runner = "docker" if config.process_role == "agent-controller" else "controller"
    if config.agent_runtime_runner.strip().lower() != expected_agent_runner:
        errors.append(
            f"NEXUS_AGENT_RUNTIME_RUNNER must be {expected_agent_runner} for the {config.process_role} process"
        )
    if not config.agent_runtime_controller_socket.strip():
        errors.append("NEXUS_AGENT_RUNTIME_CONTROLLER_SOCKET must be configured")
    elif not config.agent_runtime_controller_socket.strip().startswith("/"):
        errors.append("NEXUS_AGENT_RUNTIME_CONTROLLER_SOCKET must be an absolute Unix socket in production")
    if not config.agent_runtime_controller_token_configured:
        errors.append("NEXUS_AGENT_RUNTIME_CONTROLLER_TOKEN must be configured")
    if not config.agent_runtime_host_id.strip() or config.agent_runtime_host_id.strip() == "local":
        errors.append("NEXUS_AGENT_RUNTIME_HOST_ID must identify a stable Docker worker")
    if config.process_role == "agent-controller":
        if not config.agent_runtime_egress_policy_ready:
            errors.append("NEXUS_AGENT_RUNTIME_EGRESS_POLICY_READY must be 1 for the Agent Controller")
        if not config.agent_image_admission_configured:
            errors.append("NEXUS_AGENT_IMAGE_ADMISSION_COMMAND must be configured for the Agent Controller")
    if config.process_role == "agent-builder":
        if not config.agent_python_builds_enabled:
            errors.append("NEXUS_AGENT_PYTHON_BUILDS_ENABLED must be 1 for the Agent Python Builder")
        if not config.agent_python_isolation_ready:
            errors.append("NEXUS_AGENT_PYTHON_ISOLATION_READY must be 1 for the Agent Python Builder")
        if not re.fullmatch(
            r"(?:[a-zA-Z0-9./:_-]+@)?sha256:[a-f0-9]{64}",
            config.agent_python_base_image,
        ):
            errors.append(
                "NEXUS_AGENT_PYTHON_BASE_IMAGE must be an immutable SHA-256 image for the Agent Python Builder"
            )
    return errors


def enforce_production_configuration(config: ProductionRuntimeConfig) -> None:
    errors = production_configuration_errors(config)
    if errors:
        detail = "\n - ".join(errors)
        raise ImproperlyConfigured(f"Invalid Nexus production runtime configuration:\n - {detail}")


def _uses_redis(value: str) -> bool:
    try:
        return urlparse(value).scheme.lower() in REDIS_SCHEMES
    except (TypeError, ValueError):
        return False
