"""Owner-only Agent runtime access, without pricing or machine credentials."""
import hmac
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from apps.common.execution_request import ExecutionRequest
from apps.common.subjects import RequestSubject, subject_digest
from rest_framework import exceptions
from apps.common.request_context import get_tenant_from_request
from apps.agents.models import Agent
from .authentication import validate_owner
from .services import installation_context


class PersonalAgentRuntimePolicy:
    def restore_request(self, *, payload):
        try:
            owner_id, tenant_id, project_id = installation_context()
            subject = RequestSubject(**payload["subject"])
            if (subject.principal_type != "user" or subject.external is not False
                    or subject.principal_id != str(owner_id)
                    or payload["tenant_id"] != tenant_id or payload["project_id"] != project_id
                    or payload.get("service_token_id")
                    or not hmac.compare_digest(subject.subject_hash, subject_digest(f"{tenant_id}|user|{owner_id}"))):
                raise ValueError("Invalid task authority")
            owner = get_user_model().objects.get(pk=owner_id, is_active=True)
            request = ExecutionRequest(user=owner, execution_subject=subject,
                tenant_id=tenant_id, project_id=project_id, base_url=payload["base_url"],
                request_id=payload["request_id"], headers={}, META={}, query_params={},
                method="POST", path="/api/v1/agents/tasks/", auth=None, api_key=None, GET={})
            validate_owner(request, owner)
            from .agent_credentials import restore_invocation_authority
            restore_invocation_authority(request, payload)
        except (KeyError, TypeError, ValueError, get_user_model().DoesNotExist, ImproperlyConfigured):
            # A queued caller losing its installation/active owner is an
            # authority loss, not a retryable worker crash. Keep other setup
            # paths fail-closed with their existing configuration diagnostics.
            raise exceptions.PermissionDenied("Task authority is no longer active.") from None
        return request

    def runtime_actor_user(self, *, request, api_key):
        self.enforce_api_key_agent_policy(api_key=api_key, agent=None)
        from .agent_credentials import current_credential
        current_credential(request)
        return validate_owner(request, request.user)[0]

    def enforce_api_key_agent_policy(self, *, api_key, agent):
        if api_key is not None:
            raise exceptions.AuthenticationFailed("Use personal owner credentials for Agent calls.")

    def get_runtime_use_agent(self, *, request, tenant, agent_id):
        from apps.agents.services import get_agent
        from apps.agents.runtime_services import AgentRuntimeNotAvailable
        if str(get_tenant_from_request(request).pk) != str(tenant.pk):
            raise exceptions.NotFound("Agent not found.")
        self.enforce_api_key_agent_policy(api_key=getattr(request, "api_key", None), agent=None)
        from .agent_credentials import enforce_agent
        enforce_agent(request, agent_id)
        agent = get_agent(request=request, agent_id=agent_id)
        if agent.status in {Agent.STATUS_DISABLED, Agent.STATUS_ARCHIVED}:
            raise AgentRuntimeNotAvailable("Agent is disabled or archived.")
        return agent


class PersonalRunContextExtension:
    def renewed_model_values(self, *, run, expires_at):
        # Core delegates are renewed by the worker. This host has no extra
        # credentials or additional expiry columns to renew.
        return {}

    def cleared_model_values(self):
        return {}

    def close_legacy_browser(self, *, run_id):
        from apps.agents.models import AgentBrowserSession
        from django.utils import timezone
        # Personal execution never starts SSH browsers. Retain an explicit
        # disabled record if an unsupported historical session is encountered.
        AgentBrowserSession.objects.filter(run_id=run_id).exclude(connection__connection_type="runtime").update(
            status=AgentBrowserSession.STATUS_FAILED, closed_at=timezone.now(),
            last_error="LEGACY_SSH_DISABLED: pair Nexus Computer Runtime to use Attached Browser.")

    def interactor_fields(self, *, agent):
        return {}

    def annotate_invocations(self, *, queryset, latest, summary):
        # The common optimizer retains operational latency/tool/turn fields.
        # Do not query financial columns or add fake zero-cost annotations.
        return queryset

    def presentation_fields(self, *, invocation):
        # No financial schema or synthetic zero-cost statement in personal mode.
        return {}

    def expired_model_values(self, now):
        # Cleanup must still revoke core delegates if the owner is disabled.
        # There are no additional financial columns to expire in this schema.
        return {}

    def issue(self, *, agent, tool_name, now):
        owner_id, tenant_id, project_id = installation_context()
        owner = get_user_model().objects.get(pk=owner_id, is_active=True)
        validate_owner(SimpleNamespace(user=owner, META={}, headers={}), owner)
        if not Agent.objects.filter(pk=agent.pk, tenant_id=tenant_id, project_id=project_id,
                                    created_by_id=owner_id, status=Agent.STATUS_ACTIVE).exists():
            raise exceptions.NotFound("Agent context is unavailable.")
        # No financial model fields exist in personal mode. This is absence
        # of billing, not a synthetic free pricing snapshot or wallet.
        return self

    @property
    def model_values(self):
        return {}

    def display_values(self, internal_root):
        return {}
