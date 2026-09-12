"""Owner-local Agent catalog. No prices, publication or Marketplace query."""
from types import SimpleNamespace
from rest_framework import exceptions
from apps.common.request_context import get_tenant_from_request
from .resource_catalog import PersonalResourceCatalog


class PersonalAgentPolicy:
    def expire_stale_non_invocation_runs(self, *, now=None):
        # Personal has no executable legacy or public Demo lifecycle.
        return 0

    def interaction_run_kinds(self):
        from apps.agents.models import AgentDisplayRun
        return [AgentDisplayRun.KIND_INVOCATION]

    def validate_run_interaction(self, *, run):
        from apps.agents.runtime_services import AgentRuntimeError, AgentRuntimeNotFound
        if run.run_kind not in self.interaction_run_kinds():
            raise AgentRuntimeNotFound("Agent run not found.")
        if run.interaction_mode not in {"stream", "task"}:
            exc = AgentRuntimeError("Interactive Chat requires Streamable HTTP/SSE or an MCP Task.")
            exc.default_code = "INTERACTIVE_TRANSPORT_REQUIRED"
            raise exc

    def run_display_asset_url(self, *, run, asset):
        from apps.agents.runtime_services import AgentRuntimeNotFound
        if run.run_kind not in self.interaction_run_kinds():
            raise AgentRuntimeNotFound("Agent run not found.")
        return f"/api/v1/agent-runs/{run.id}/display-assets/{asset.id}/"

    def create_agent_key(self, *, request, agent_id):
        raise exceptions.NotFound('Legacy Agent keys are unavailable. Use Personal Tool Setup credentials.')

    def deploy_agent(self, **kwargs):
        raise exceptions.NotFound('Legacy simulated deployment is unavailable. Use a real Agent runtime.')

    def _get_bindable_agent(self, *, request, agent_id):
        from apps.agents.services import get_agent
        return get_agent(request=request, agent_id=agent_id)

    def validate_agent_update(self, *, request, data):
        get_tenant_from_request(request)
        if {"project_id", "team_id"}.intersection(data) and (
            str(data.get("project_id") or "") != str(request.project_id) or data.get("team_id")
        ):
            raise exceptions.ValidationError({"project_id": "Keep the Agent in this personal instance's Project."})

    def visible_agents(self, *, user, tenant):
        from apps.agents.models import Agent
        request = SimpleNamespace(user=user, META={}, query_params={})
        return PersonalResourceCatalog().discoverable_resource_queryset(Agent.objects.all(),
            request=request, tenant=tenant, resource_type="agent")

    def list_agents(self, *, request):
        tenant = get_tenant_from_request(request)
        return self.visible_agents(user=request.user, tenant=tenant).select_related(
            "tenant", "project", "team", "edge_registration", "current_image",
            "current_image__version", "resource_config").prefetch_related(
                "versions", "deployments", "runtime_deployments",
                "runtime_deployments__edge_registration__node", "runtime_images", "runtime_images__version"
            ).order_by("-created_at")

    def set_visibility(self, *, request, agent_id, visibility, project_id=None):
        if visibility != "private" or project_id not in (None, "", str(getattr(request, "project_id", ""))):
            raise exceptions.NotFound("Agent publishing is not available in this distribution.")
        from apps.agents.services import get_mutable_agent, log_write
        agent = get_mutable_agent(request=request, agent_id=agent_id)
        agent.visibility, agent.publication_status = "private", "unpublished"
        agent.save(update_fields=["visibility", "publication_status", "updated_at"])
        log_write(request=request, action="agents.visibility.set", agent=agent, metadata={"visibility": "private"})
        return agent

    def clone_commercial_configuration(self, *, source, clone):
        # Personal clones have no commercial configuration to copy. The actual
        # Agent, metadata and resource configuration are cloned by shared code.
        return None

    def _unavailable(self, **kwargs):
        raise exceptions.NotFound("Agent pricing and Marketplace are not available in this distribution.")

    set_publication = _unavailable
    set_pricing = _unavailable
    validate_agent_publication = _unavailable
    list_marketplace_agents = _unavailable
    get_marketplace_agent = _unavailable
    get_public_agent_display = _unavailable
    get_display_agent = _unavailable
    public_agent_summary = _unavailable
    submit_public_display_message = _unavailable
    public_demo_cookie_name = _unavailable
    get_public_display_run = _unavailable
    get_public_display_asset = _unavailable
    start_public_agent_demo = _unavailable
    _execute_public_demo = _unavailable
