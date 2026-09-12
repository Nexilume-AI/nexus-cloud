"""Fail-closed shared extension contracts; no simulated Agent execution."""
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.agents import context_extension
from apps.agents.model_extension import model_extension
from apps.agents.models import Agent, AgentRuntimeInvocation
from apps.agents.task_extension import load_task_extensions
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


class AgentExtensionConfigurationTests(SimpleTestCase):
    def test_model_extension_requires_explicit_valid_configuration(self):
        for value in (None, "", 1, "missing_test_extension", "nexus_personal.models"):
            with self.subTest(value=value), override_settings(NEXUS_AGENT_MODEL_EXTENSION=value):
                with self.assertRaises(ImproperlyConfigured):
                    model_extension()

    @override_settings(NEXUS_AGENT_MODEL_EXTENSION="nexus_personal.agent_models")
    def test_selected_model_extension_does_not_create_optional_pricing_models(self):
        extension = model_extension()
        self.assertEqual(extension.__name__, "nexus_personal.agent_models")
        self.assertIsNone(extension.register_models())
        self.assertFalse(hasattr(extension, "AgentPricing"))
        from apps.agents import models
        with self.assertRaises(AttributeError):
            getattr(models, "AgentPricing")

    def test_task_modules_require_a_tuple_of_explicit_module_names(self):
        for value in (None, "", [], ("",), (None,), ("missing_test_extension",)):
            with self.subTest(value=value), override_settings(NEXUS_AGENT_EXTRA_TASK_MODULES=value):
                with self.assertRaises(ImproperlyConfigured):
                    load_task_extensions()

    def test_empty_tasks_and_explicit_module_load_without_implicit_tasks(self):
        with override_settings(NEXUS_AGENT_EXTRA_TASK_MODULES=()):
            self.assertEqual(load_task_extensions(), ())
        with override_settings(NEXUS_AGENT_EXTRA_TASK_MODULES=("nexus_personal.agent_models",)):
            self.assertEqual([m.__name__ for m in load_task_extensions()], ["nexus_personal.agent_models"])

    def test_context_requires_a_complete_explicit_backend(self):
        for value in (None, "", 1, "missing_test_extension.Backend", "types.SimpleNamespace"):
            with self.subTest(value=value), override_settings(NEXUS_AGENT_CONTEXT_EXTENSION_BACKEND=value):
                with self.assertRaises(ImproperlyConfigured):
                    context_extension.cleared_run_context_fields()

    def test_issued_context_requires_model_values_and_display_callable(self):
        for result in (None, {}, SimpleNamespace(model_values=[]),
                       SimpleNamespace(model_values={}, display_values=None)):
            backend = SimpleNamespace(issue=lambda **kwargs: result)
            with self.subTest(result=type(result).__name__), patch.object(context_extension, "_backend", return_value=backend):
                with self.assertRaises(ImproperlyConfigured):
                    context_extension.issue_run_context_extension(agent=None, tool_name="chat", now=timezone.now())

    def test_all_context_field_adapters_reject_non_mapping_results(self):
        calls = (
            ("expired_model_values", lambda: context_extension.expired_run_context_fields(timezone.now())),
            ("renewed_model_values", lambda: context_extension.renewed_run_context_fields(run=None, expires_at=timezone.now())),
            ("cleared_model_values", context_extension.cleared_run_context_fields),
            ("presentation_fields", lambda: context_extension.run_presentation_fields(invocation=None)),
            ("interactor_fields", lambda: context_extension.interactor_presentation_fields(agent=None)),
        )
        for method, call in calls:
            for value in (None, [], "invalid"):
                backend = SimpleNamespace(**{method: lambda *args, **kwargs: value})
                with self.subTest(method=method, result=type(value).__name__), patch.object(context_extension, "_backend", return_value=backend):
                    with self.assertRaises(ImproperlyConfigured):
                        call()

    def test_annotation_cannot_change_model_or_database_or_return_a_list(self):
        queryset = AgentRuntimeInvocation.objects.none()
        for result in ([], None, Agent.objects.none(), queryset.using("unconfigured-test-alias")):
            backend = SimpleNamespace(annotate_invocations=lambda **kwargs: result)
            with self.subTest(result=type(result).__name__), patch.object(context_extension, "_backend", return_value=backend):
                with self.assertRaises(ImproperlyConfigured):
                    context_extension.annotate_run_invocations(queryset=queryset, latest=False, summary=False)


class PersonalAgentContextAuthorityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.row = provision_owner(email="context-extension@example.test", password=PASSWORD)
        cls.agent = Agent.objects.create(tenant=cls.row.tenant, project=cls.row.project,
            name="Context authority", status=Agent.STATUS_ACTIVE, created_by=cls.row.owner)

    def issue(self):
        return context_extension.issue_run_context_extension(
            agent=self.agent, tool_name="chat", now=timezone.now())

    def test_owner_context_contains_no_synthetic_financial_fields(self):
        extension = self.issue()
        self.assertEqual(extension.model_values, {})
        self.assertEqual(extension.display_values("https://context.invalid"), {})
        self.assertEqual(context_extension.run_presentation_fields(invocation=None), {})
        self.assertEqual(context_extension.interactor_presentation_fields(agent=self.agent), {})
        queryset = AgentRuntimeInvocation.objects.none()
        self.assertIs(context_extension.annotate_run_invocations(queryset=queryset, latest=True, summary=True), queryset)

    def test_inactive_agent_cannot_issue_context_from_stale_instance(self):
        from rest_framework.exceptions import NotFound
        for state in (Agent.STATUS_DISABLED, Agent.STATUS_ARCHIVED, Agent.STATUS_DELETED):
            Agent.objects.filter(pk=self.agent.pk).update(status=state)
            with self.subTest(state=state), self.assertRaises(NotFound):
                self.issue()

    def test_disabled_owner_does_not_prevent_delegate_cleanup(self):
        from django.contrib.auth import get_user_model
        get_user_model().objects.filter(pk=self.row.owner_id).update(is_active=False)
        # The installation guard rejects the disabled owner before user lookup.
        with self.assertRaisesMessage(ImproperlyConfigured, "Personal installation context is unavailable or inconsistent."):
            self.issue()
        self.assertEqual(context_extension.expired_run_context_fields(timezone.now()), {})
        self.assertEqual(context_extension.cleared_run_context_fields(), {})
        self.assertEqual(context_extension.renewed_run_context_fields(run=None, expires_at=timezone.now()), {})
