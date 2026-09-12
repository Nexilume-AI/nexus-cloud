from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import yaml

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.common.runtime_health import (
    AGENT_BUILDER_HEARTBEAT_CACHE_KEY,
    AGENT_RECONCILER_HEARTBEAT_CACHE_KEY,
    RUNTIME_HEARTBEAT_CACHE_KEY,
    agent_builder_readiness,
    agent_reconciler_readiness,
    record_agent_builder_heartbeat,
    record_agent_reconciler_heartbeat,
    record_runtime_heartbeat,
)
from config.production import ProductionRuntimeConfig, production_configuration_errors


def valid_production_config(**overrides) -> ProductionRuntimeConfig:
    values = {
        "environment": "production",
        "debug": False,
        "database_configured": True,
        "database_url": "postgresql://nexus:secret@postgres:5432/nexus",
        "database_engine": "django.db.backends.postgresql",
        "redis_configured": True,
        "redis_url": "redis://redis:6379/0",
        "celery_broker_url": "redis://redis:6379/0",
        "celery_result_backend": "redis://redis:6379/0",
        "celery_always_eager": False,
        "celery_beat_scheduler": "config.beat.SingletonRedisScheduler",
        "process_role": "web",
        "shared_storage_configured": True,
        "shared_storage_root": "/var/lib/nexus",
        "websocket_routing_mode": "sticky",
        "secret_encryption_keys_configured": True,
        "provider_runtime_runner": "controller",
        "provider_runtime_controller_socket": "/run/nexus-provider/controller.sock",
        "provider_runtime_controller_token_configured": True,
        "agent_runtime_runner": "controller",
        "agent_runtime_controller_socket": "/run/nexus-agent/controller.sock",
        "agent_runtime_controller_token_configured": True,
        "agent_runtime_host_id": "agent-worker-1",
        "agent_runtime_egress_policy_ready": False,
        "agent_image_admission_configured": False,
        "agent_python_builds_enabled": True,
        "agent_python_isolation_ready": True,
        "agent_python_base_image": "sha256:" + "a" * 64,
    }
    values.update(overrides)
    return ProductionRuntimeConfig(**values)


class ProductionConfigurationTests(SimpleTestCase):
    @override_settings(NEXUS_PRODUCTION=True, NEXUS_AGENT_RUNTIME_RUNNER="fake")
    def test_production_cannot_silently_use_fake_agent_execution(self):
        from django.core.exceptions import ImproperlyConfigured
        from apps.agents.runtime_runner import get_runtime_runner
        with self.assertRaises(ImproperlyConfigured):
            get_runtime_runner()

    @override_settings(NEXUS_PRODUCTION=True, NEXUS_PROVIDER_RUNTIME_RUNNER="fake")
    def test_production_cannot_silently_use_fake_provider_execution(self):
        from django.core.exceptions import ImproperlyConfigured
        from apps.providers.runtime_runner import get_provider_runtime_runner
        with self.assertRaises(ImproperlyConfigured):
            get_provider_runtime_runner()

    def test_valid_production_topology_has_no_errors(self) -> None:
        self.assertEqual(production_configuration_errors(valid_production_config()), [])

    def test_unsafe_single_process_defaults_are_rejected(self) -> None:
        errors = production_configuration_errors(
            valid_production_config(
                database_configured=False,
                database_url="sqlite:///db.sqlite3",
                database_engine="django.db.backends.sqlite3",
                redis_configured=False,
                redis_url="locmem://",
                celery_broker_url="memory://",
                celery_result_backend="cache+memory://",
                celery_always_eager=True,
                celery_beat_scheduler="celery.beat.PersistentScheduler",
                process_role="all",
                shared_storage_configured=False,
                shared_storage_root="",
                websocket_routing_mode="",
                secret_encryption_keys_configured=False,
                provider_runtime_runner="fake",
                provider_runtime_controller_socket="",
                provider_runtime_controller_token_configured=False,
                agent_runtime_runner="fake",
                agent_runtime_controller_socket="",
                agent_runtime_controller_token_configured=False,
                agent_runtime_host_id="local",
                agent_runtime_egress_policy_ready=False,
                agent_image_admission_configured=False,
            )
        )

        joined = "\n".join(errors)
        self.assertIn("PostgreSQL", joined)
        self.assertIn("REDIS_URL", joined)
        self.assertIn("CELERY_TASK_ALWAYS_EAGER", joined)
        self.assertIn("CELERY_BEAT_SCHEDULER", joined)
        self.assertIn("NEXUS_PROCESS_ROLE", joined)
        self.assertIn("NEXUS_SHARED_STORAGE_ROOT", joined)
        self.assertIn("NEXUS_WEBSOCKET_ROUTING_MODE", joined)
        self.assertIn("NEXUS_SECRET_ENCRYPTION_KEYS", joined)
        self.assertIn("NEXUS_PROVIDER_RUNTIME_RUNNER", joined)
        self.assertIn("NEXUS_PROVIDER_RUNTIME_CONTROLLER_SOCKET", joined)
        self.assertIn("NEXUS_PROVIDER_RUNTIME_CONTROLLER_TOKEN", joined)
        self.assertIn("NEXUS_AGENT_RUNTIME_RUNNER", joined)
        self.assertIn("NEXUS_AGENT_RUNTIME_CONTROLLER_SOCKET", joined)
        self.assertIn("NEXUS_AGENT_RUNTIME_CONTROLLER_TOKEN", joined)
        self.assertIn("NEXUS_AGENT_RUNTIME_HOST_ID", joined)

    def test_provider_controller_process_must_own_the_docker_runner(self) -> None:
        self.assertEqual(
            production_configuration_errors(
                valid_production_config(process_role="provider-controller", provider_runtime_runner="docker")
            ),
            [],
        )

    def test_agent_controller_process_must_own_the_docker_runner_and_policy(self) -> None:
        self.assertEqual(
            production_configuration_errors(
                valid_production_config(
                    process_role="agent-controller",
                    agent_runtime_runner="docker",
                    agent_runtime_egress_policy_ready=True,
                    agent_image_admission_configured=True,
                )
            ),
            [],
        )

    def test_general_worker_cannot_own_agent_docker(self) -> None:
        errors = production_configuration_errors(
            valid_production_config(process_role="worker", agent_runtime_runner="docker")
        )
        self.assertTrue(any("NEXUS_AGENT_RUNTIME_RUNNER" in error for error in errors))

    def test_agent_builder_requires_enabled_isolated_immutable_profile(self) -> None:
        self.assertEqual(
            production_configuration_errors(valid_production_config(process_role="agent-builder")),
            [],
        )
        errors = production_configuration_errors(
            valid_production_config(
                process_role="agent-builder",
                agent_python_builds_enabled=False,
                agent_python_isolation_ready=False,
                agent_python_base_image="python:latest",
            )
        )
        self.assertTrue(any("NEXUS_AGENT_PYTHON_BUILDS_ENABLED" in error for error in errors))
        self.assertTrue(any("NEXUS_AGENT_PYTHON_ISOLATION_READY" in error for error in errors))
        self.assertTrue(any("NEXUS_AGENT_PYTHON_BASE_IMAGE" in error for error in errors))


class ProductionComposeIsolationTests(SimpleTestCase):
    def test_only_isolated_control_processes_receive_docker_socket(self) -> None:
        compose_path = Path(__file__).resolve().parents[1] / "infra" / "production" / "compose.yaml"
        services = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"]
        docker_services = {
            name
            for name, service in services.items()
            if any("/var/run/docker.sock" in str(volume) for volume in service.get("volumes", []))
        }
        self.assertEqual(
            docker_services,
            {"provider-runtime-controller", "agent-runtime-controller", "agent-python-builder"},
        )
        for name in {"web", "worker", "dataset-worker", "agent-worker", "beat"}:
            self.assertFalse(
                any("/var/run/docker.sock" in str(volume) for volume in services[name].get("volumes", [])),
                name,
            )


class RuntimeHeartbeatTests(SimpleTestCase):
    @override_settings(NEXUS_PRODUCTION=True, NEXUS_PROCESS_ROLE="worker")
    def test_agent_worker_has_an_independent_health_gate(self):
        from apps.common.runtime_health import AGENT_WORKER_HEARTBEAT_CACHE_KEY, agent_worker_readiness, record_agent_worker_heartbeat
        cache.delete(AGENT_WORKER_HEARTBEAT_CACHE_KEY)
        self.assertFalse(agent_worker_readiness()["ok"])
        record_agent_worker_heartbeat()
        self.assertTrue(agent_worker_readiness()["ok"])
        cache.set(AGENT_WORKER_HEARTBEAT_CACHE_KEY, time.time() - 46, timeout=90)
        self.assertFalse(agent_worker_readiness()["ok"])
        cache.delete(AGENT_WORKER_HEARTBEAT_CACHE_KEY)

    def tearDown(self) -> None:
        cache.delete(RUNTIME_HEARTBEAT_CACHE_KEY)
        cache.delete(AGENT_RECONCILER_HEARTBEAT_CACHE_KEY)
        cache.delete(AGENT_BUILDER_HEARTBEAT_CACHE_KEY)

    @override_settings(
        NEXUS_PRODUCTION=True,
        NEXUS_PROCESS_ROLE="worker",
        NEXUS_RUNTIME_HEARTBEAT_STALE_SECONDS=45,
    )
    def test_only_worker_records_production_heartbeat(self) -> None:
        payload = record_runtime_heartbeat()

        self.assertEqual(payload["role"], "worker")
        self.assertLess(time.time() - payload["recorded_at"], 2)
        self.assertEqual(cache.get(RUNTIME_HEARTBEAT_CACHE_KEY)["role"], "worker")

    @override_settings(NEXUS_PRODUCTION=True, NEXUS_PROCESS_ROLE="web")
    def test_web_process_cannot_forge_worker_heartbeat(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "worker process"):
            record_runtime_heartbeat()

    @override_settings(NEXUS_PRODUCTION=True, NEXUS_AGENT_RECONCILER_STALE_SECONDS=45)
    def test_agent_reconciler_failure_and_staleness_are_not_reported_ready(self) -> None:
        cache.delete(AGENT_RECONCILER_HEARTBEAT_CACHE_KEY)
        self.assertEqual(agent_reconciler_readiness()["code"], "AGENT_RECONCILER_HEARTBEAT_MISSING")
        record_agent_reconciler_heartbeat(ok=False, error_code="AGENT_RECONCILER_FAILED")
        self.assertEqual(agent_reconciler_readiness()["code"], "AGENT_RECONCILER_FAILED")
        record_agent_reconciler_heartbeat(ok=True)
        self.assertTrue(agent_reconciler_readiness()["ok"])
        cache.set(
            AGENT_RECONCILER_HEARTBEAT_CACHE_KEY,
            {"recorded_at": time.time() - 46, "ok": True},
            timeout=90,
        )
        self.assertEqual(agent_reconciler_readiness()["code"], "AGENT_RECONCILER_HEARTBEAT_STALE")

    @override_settings(
        NEXUS_PRODUCTION=True,
        NEXUS_PROCESS_ROLE="agent-builder",
        NEXUS_AGENT_PYTHON_BUILDS_ENABLED=True,
        NEXUS_AGENT_BUILDER_STALE_SECONDS=30,
    )
    def test_python_builder_has_an_independent_health_gate(self) -> None:
        self.assertEqual(agent_builder_readiness()["code"], "AGENT_BUILDER_HEARTBEAT_MISSING")
        record_agent_builder_heartbeat()
        self.assertTrue(agent_builder_readiness()["ok"])
        cache.set(
            AGENT_BUILDER_HEARTBEAT_CACHE_KEY,
            {"recorded_at": time.time() - 31, "role": "agent-builder"},
            timeout=60,
        )
        self.assertEqual(agent_builder_readiness()["code"], "AGENT_BUILDER_HEARTBEAT_STALE")


class RuntimeReadinessAPITests(TestCase):
    def test_development_readiness_checks_database_and_cache(self) -> None:
        response = APIClient().get("/api/v1/health/readiness/")

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["status"], "ready")
        self.assertTrue(data["checks"]["database"]["ok"])
        self.assertTrue(data["checks"]["redis"]["ok"])

    @patch(
        "apps.common.views.runtime_readiness",
        return_value=(False, {"status": "not_ready", "checks": {"background_tasks": {"ok": False}}}),
    )
    def test_failed_runtime_dependency_returns_service_unavailable(self, _readiness) -> None:
        response = APIClient().get("/api/v1/health/readiness/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["data"]["status"], "not_ready")
