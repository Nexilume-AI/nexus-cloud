"""Core policy-port tests: no wallets, plans or database access."""
from unittest.mock import Mock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from apps.common import invocation_lifecycle as lifecycle
from apps.common import resource_limits


class ResourceOnlyBackend:
    def enforce_resource(self, **kwargs):
        return None


class IncompleteLifecycle:
    def begin(self, **kwargs):
        return None


class InvocationLifecycleTests(SimpleTestCase):
    def setUp(self):
        lifecycle._backend.cache_clear()
        self.addCleanup(lifecycle._backend.cache_clear)

    def test_absent_invalid_and_incomplete_backend_reject_all_operations(self):
        for path in ("", "missing.Policy", "tests.test_invocation_lifecycle.IncompleteLifecycle"):
            for method in (lifecycle.begin_runtime_invocation, lifecycle.finalize_runtime_invocation,
                           lifecycle.report_invocation_billing, lifecycle.renew_invocation_lease,
                           lifecycle.release_stale_invocation_reservations):
                with self.subTest(path=path, method=method.__name__), override_settings(NEXUS_INVOCATION_LIFECYCLE_BACKEND=path):
                    with self.assertRaises(ImproperlyConfigured):
                        method()

    def test_each_operation_forwards_exact_arguments_and_result(self):
        operations = {
            "agent_pricing_snapshot": "pricing_snapshot", "calculate_agent_cost": "estimate",
            "invocation_preflight": "preflight", "begin_runtime_invocation": "begin",
            "finalize_runtime_invocation": "finalize", "report_invocation_billing": "report",
            "record_invocation": "record", "release_stale_invocation_reservations": "release_stale",
            "renew_invocation_lease": "renew_lease",
        }
        for name, backend_method in operations.items():
            with self.subTest(name=name):
                backend = Mock()
                result = object()
                arguments = {"request": object(), "turn_index": 7, "run": object()}
                getattr(backend, backend_method).return_value = result
                with patch.object(lifecycle, "_current", return_value=backend):
                    self.assertIs(getattr(lifecycle, name)(**arguments), result)
                getattr(backend, backend_method).assert_called_once_with(**arguments)

    def test_failure_is_not_retried_or_converted_to_free_invocation(self):
        backend = Mock()
        error = RuntimeError("unavailable")
        backend.begin.side_effect = error
        with patch.object(lifecycle, "_current", return_value=backend):
            with self.assertRaises(RuntimeError) as raised:
                lifecycle.begin_runtime_invocation(turn_index=2)
        self.assertIs(raised.exception, error)
        backend.begin.assert_called_once_with(turn_index=2)
        backend.finalize.assert_not_called()

    @override_settings(NEXUS_INVOCATION_LIFECYCLE_BACKEND="configured.Backend")
    def test_backend_is_loaded_once_without_importing_it_at_port_import(self):
        backend = Mock()
        with patch.object(lifecycle, "import_string", return_value=lambda: backend) as load:
            lifecycle.agent_pricing_snapshot(agent=object(), tool_name="echo")
            lifecycle.invocation_preflight(tenant=object(), agent=object())
            load.assert_called_once_with("configured.Backend")

    @override_settings(NEXUS_RESOURCE_ADMISSION_BACKEND="tests.test_invocation_lifecycle.ResourceOnlyBackend")
    def test_missing_capacity_support_does_not_grant_unlimited_runs(self):
        with self.assertRaises(ImproperlyConfigured):
            resource_limits.reserve_capability(tenant=object(), code="agents.concurrent_runs", idempotency_key="run:1")
