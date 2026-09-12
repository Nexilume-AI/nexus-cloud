from __future__ import annotations

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.agents.models import (
    Agent,
    AgentDisplayRun,
    AgentRunContextGrant,
    AgentRuntimeDeployment,
    EdgeAgentRegistration,
    EdgeNode,
)
from apps.agents.runtime_context import prepare_openwrt_run_context_headers
from apps.tenancy.models import Tenant


@override_settings(NEXUS_PUBLIC_BASE_URL="https://nexus.example.test")
class AgentRunContextGrantTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Context Tenant", slug="context-tenant")
        self.agent = Agent.objects.create(tenant=self.tenant, name="Context Agent")
        self.node = EdgeNode.objects.create(
            tenant=self.tenant,
            router_id="context-router",
            domain_id="context.example.test",
            display_name="Context Router",
            device_token_hash="a" * 64,
            capabilities={
                "runtime_context_v1": True,
                "invoke_interactions_v1": True,
                "mcp_stream_v1": True,
                "mcp_tasks_v1": True,
            },
        )
        self.registration = EdgeAgentRegistration.objects.create(
            node=self.node,
            agent=self.agent,
            origin="agent://context/edge",
            route_id="context-route",
            protocols=["mcp"],
            capabilities=["context.inspect"],
            ipv6_address="2001:db8::42",
            port=7443,
            path="/mcp/agent%3A%2F%2Fcontext%2Fedge",
            scheme="https",
            tls_server_name="context.example.test",
            ca_bundle_id="context-ca",
            lease_expires_at=timezone.now() + timedelta(minutes=5),
            last_renewed_at=timezone.now(),
        )
        self.runtime = AgentRuntimeDeployment.objects.create(
            tenant=self.tenant,
            agent=self.agent,
            runtime_kind=AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6,
            edge_registration=self.registration,
            status=AgentRuntimeDeployment.STATUS_ACTIVE,
        )
        self.run = AgentDisplayRun.objects.create(
            tenant=self.tenant,
            agent=self.agent,
            runtime=self.runtime,
            run_kind=AgentDisplayRun.KIND_INVOCATION,
            write_token="write-secret",
        )
        self.client = APIClient()

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "X-Nexus-AGUI-Run-Id": str(self.run.id),
            "X-Nexus-AGUI-Events-Url": "https://nexus.example.test/events",
            "X-Nexus-AGUI-Token": "agui-secret",
            "X-Nexus-Interaction-Url": "https://nexus.example.test/interactions",
            "X-Nexus-Interaction-Token": "interaction-secret",
            "X-Nexus-Interaction-Mode": "stream",
            "X-Nexus-Memory-Url": "https://nexus.example.test/memory",
            "X-Nexus-Context-Url": "https://nexus.example.test/context",
            "X-Nexus-Context-Token": "context-secret",
            "X-Nexus-Run-Turn": "2",
        }

    def test_exchange_is_single_use_and_full_context_never_crosses_edge_header(self):
        outbound = prepare_openwrt_run_context_headers(
            deployment=self.runtime,
            headers=self._headers(),
        )
        self.assertEqual(outbound["Content-Type"], "application/json")
        self.assertNotIn("X-Nexus-AGUI-Token", outbound)
        self.assertNotIn("X-Nexus-Interaction-Token", outbound)
        self.assertNotIn("X-Nexus-Context-Token", outbound)
        self.assertIn("X-Nexus-Run-Context-Url", outbound)
        token = outbound["X-Nexus-Run-Context-Token"]
        grant = AgentRunContextGrant.objects.get(run=self.run)
        self.assertNotEqual(grant.token_hash, token)
        self.assertNotIn("agui-secret", grant.encrypted_context)

        path = f"/api/v1/internal/agent-run-contexts/{grant.id}/exchange/"
        response = self.client.post(
            path,
            {},
            format="json",
            HTTP_X_NEXUS_RUN_CONTEXT_TOKEN=token,
        )
        self.assertEqual(response.status_code, 200, response.content)
        context = response.json()["data"]["context"]
        self.assertEqual(context["X-Nexus-AGUI-Run-Id"], str(self.run.id))
        self.assertEqual(context["X-Nexus-AGUI-Token"], "agui-secret")
        self.assertEqual(context["X-Nexus-Context-Url"], "https://nexus.example.test/context")
        self.assertEqual(context["X-Nexus-Context-Token"], "context-secret")
        self.assertEqual(context["X-Nexus-Run-Turn"], "2")
        replay = self.client.post(
            path,
            {},
            format="json",
            HTTP_X_NEXUS_RUN_CONTEXT_TOKEN=token,
        )
        self.assertEqual(replay.status_code, 404)

    def test_wrong_or_expired_token_returns_same_404(self):
        outbound = prepare_openwrt_run_context_headers(
            deployment=self.runtime,
            headers=self._headers(),
        )
        grant = AgentRunContextGrant.objects.get(run=self.run)
        path = f"/api/v1/internal/agent-run-contexts/{grant.id}/exchange/"
        wrong = self.client.post(
            path,
            {},
            format="json",
            HTTP_X_NEXUS_RUN_CONTEXT_TOKEN="wrong",
        )
        grant.expires_at = timezone.now() - timedelta(seconds=1)
        grant.save(update_fields=["expires_at"])
        expired = self.client.post(
            path,
            {},
            format="json",
            HTTP_X_NEXUS_RUN_CONTEXT_TOKEN=outbound["X-Nexus-Run-Context-Token"],
        )
        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(expired.status_code, 404)
        wrong_body = wrong.json()
        expired_body = expired.json()
        wrong_body.pop("request_id", None)
        expired_body.pop("request_id", None)
        self.assertEqual(wrong_body, expired_body)
        self.assertNotContains(wrong, "wrong", status_code=404)
        self.assertNotContains(
            expired,
            outbound["X-Nexus-Run-Context-Token"],
            status_code=404,
        )
