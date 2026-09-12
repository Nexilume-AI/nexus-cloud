"""Personal archives require the current installation owner and owned sources."""
from django.db.models import Q
from rest_framework import exceptions

from apps.agents.models import Agent, AgentDisplayRun, AgentOutputArtifact
from apps.common.request_context import get_tenant_from_request
from apps.common.resource_catalog import resource_access_payload
from apps.common.subjects import request_subject


class PersonalAgentAssetPolicy:
    def get_mutable_agent(self, *, request, agent_id):
        tenant = get_tenant_from_request(request)
        agent = Agent.objects.filter(pk=agent_id, tenant=tenant).exclude(status="deleted").first()
        if agent is None:
            raise exceptions.NotFound("Agent not found.")
        resource_access_payload(request=request, resource_type="agent", obj=agent)
        return agent

    def _owned_runs(self, *, request, agent):
        subject = request_subject(request)
        return AgentDisplayRun.objects.filter(agent=agent, tenant_id=agent.tenant_id,
            caller_subject_hash=subject.subject_hash, caller_principal_type="user",
            caller_principal_id=str(request.user.pk)).filter(
                Q(consumer_tenant_id=agent.tenant_id) | Q(consumer_tenant__isnull=True)
            ).filter(Q(consumer_project_id=request.project_id) | Q(consumer_project__isnull=True))

    def check_export_source(self, *, request, agent, run=None, artifact=None, memory_items=None):
        # Revalidate current ownership even if the object/request was cached.
        agent = self.get_mutable_agent(request=request, agent_id=str(agent.pk))
        runs = self._owned_runs(request=request, agent=agent)
        if run is not None and not runs.filter(pk=run.pk).exists():
            raise exceptions.NotFound("Agent display run not found.")
        if artifact is not None and not AgentOutputArtifact.objects.filter(
            pk=artifact.pk, agent=agent, tenant_id=agent.tenant_id, run__in=runs,
        ).exclude(status="deleted").exists():
            raise exceptions.NotFound("Agent output artifact not found.")
        if memory_items is not None:
            # Agent-owned standalone memory remains supported; a linked Run
            # must also belong to the owner, not merely share an Agent ID.
            if memory_items.exclude(tenant_id=agent.tenant_id, agent_id=agent.pk).exists() or memory_items.filter(
                source_run__isnull=False).exclude(source_run__in=runs).exists():
                raise exceptions.NotFound("Agent memory item not found.")
