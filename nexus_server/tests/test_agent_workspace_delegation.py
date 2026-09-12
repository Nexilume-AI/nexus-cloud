from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from apps.agents.models import (
    Agent,
    AgentDisplayRun,
    AgentMCPSession,
    AgentRuntimeDeployment,
    AgentRuntimeImage,
    AgentWorkspaceGrant,
)
from apps.agents.runtime_services import (
    AgentComputerRequired,
    AgentRuntimeNotFound,
    create_invocation_display_context,
    finalize_mcp_session_response,
    get_workspace_delegate_run,
    prepare_mcp_session_request,
    prepare_legacy_sse_message_path,
    rewrite_legacy_sse_line,
)
from apps.agents.runtime_views import workspace_token
from apps.agents.workspace_grants import revoke_workspace_grant, set_workspace_grant
from apps.common.subjects import hash_token, request_subject
from apps.tenancy.models import Membership, Project, Tenant


class AgentWorkspaceDelegationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.caller_a = user_model.objects.create_user(username="workspace-caller-a")
        self.caller_b = user_model.objects.create_user(username="workspace-caller-b")
        self.developer = user_model.objects.create_user(username="workspace-developer")
        self.consumer = Tenant.objects.create(name="Workspace Consumer", slug="workspace-consumer")
        self.producer = Tenant.objects.create(name="Workspace Producer", slug="workspace-producer")
        self.project = Project.objects.create(tenant=self.consumer, name="Workspace Project")
        Membership.objects.create(tenant=self.consumer, project=self.project, user=self.caller_a, role=Membership.ROLE_OWNER)
        Membership.objects.create(tenant=self.consumer, project=self.project, user=self.caller_b, role=Membership.ROLE_OWNER)
        Membership.objects.create(tenant=self.producer, user=self.developer, role=Membership.ROLE_OWNER)
        self.agent = Agent.objects.create(
            tenant=self.producer,
            name="Delegated Workspace Agent",
            visibility=Agent.VISIBILITY_PUBLIC,
            publication_status=Agent.PUBLICATION_PUBLISHED,
            status=Agent.STATUS_ACTIVE,
            computer_requirement=Agent.COMPUTER_REQUIRED,
            workspace_capabilities=[
                "connection.list",
                "connection.create",
                "connection.test",
                "connection.bind",
                "files.read",
                "command.execute",
            ],
            created_by=self.developer,
        )
        image = AgentRuntimeImage.objects.create(
            tenant=self.producer,
            agent=self.agent,
            image_ref="registry.example.invalid/workspace-agent:v1",
            created_by=self.developer,
        )
        self.runtime = AgentRuntimeDeployment.objects.create(
            tenant=self.producer,
            agent=self.agent,
            image=image,
            runtime_kind=AgentRuntimeDeployment.RUNTIME_DOCKER,
            env="prod",
            status=AgentRuntimeDeployment.STATUS_ACTIVE,
            internal_mcp_url="http://127.0.0.1:8000/mcp",
        )
        self.factory = APIRequestFactory()

    def request(self, user, *, project=True, method="post"):
        request = getattr(self.factory, method)("/", {}, format="json")
        request.user = user
        request.tenant_id = str(self.consumer.id)
        request.project_id = str(self.project.id) if project else ""
        return request

    def grant(self, request, scopes=None):
        return set_workspace_grant(
            request=request,
            agent=self.agent,
            scopes=scopes or self.agent.workspace_capabilities,
        )

    def test_internal_workspace_token_accepts_edge_and_authorization_fallbacks(self):
        primary = self.factory.get(
            "/",
            HTTP_X_NEXUS_WORKSPACE_TOKEN="primary-token",
            HTTP_X_NEXUS_WORKSPACE_DELEGATE_TOKEN="delegate-token",
            HTTP_AUTHORIZATION="Bearer bearer-token",
        )
        self.assertEqual(workspace_token(primary), "primary-token")

        delegate = self.factory.get(
            "/",
            HTTP_X_NEXUS_WORKSPACE_DELEGATE_TOKEN="delegate-token",
            HTTP_AUTHORIZATION="Bearer bearer-token",
        )
        self.assertEqual(workspace_token(delegate), "delegate-token")

        bearer = self.factory.get("/", HTTP_AUTHORIZATION="Bearer bearer-token")
        self.assertEqual(workspace_token(bearer), "bearer-token")

    def test_required_agent_allows_setup_run_then_live_revocation_denies_delegate(self):
        request = self.request(self.caller_a)
        self.grant(request)

        before = AgentDisplayRun.objects.count()
        with self.assertRaises(AgentComputerRequired):
            create_invocation_display_context(
                runtime=self.runtime,
                tool_name="research",
                request=request,
            )
        self.assertEqual(AgentDisplayRun.objects.count(), before)

        run, context = create_invocation_display_context(
            runtime=self.runtime,
            tool_name="nexus_workspace_connection_create",
            request=request,
        )
        self.assertIsNone(run.computer_binding)
        self.assertNotEqual(run.write_token, context.workspace_delegate_token)
        self.assertEqual(
            run.workspace_delegate_token_hash,
            hash_token(context.workspace_delegate_token),
        )
        self.assertEqual(
            run.workspace_capabilities_snapshot,
            self.agent.workspace_capabilities,
        )
        self.assertEqual(
            get_workspace_delegate_run(
                run_id=str(run.id),
                token=context.workspace_delegate_token,
                scope="connection.create",
            ).id,
            run.id,
        )

        revoke_workspace_grant(request=request, agent=self.agent)
        run.refresh_from_db()
        self.assertEqual(run.workspace_capabilities_snapshot, self.agent.workspace_capabilities)
        with self.assertRaises(AgentRuntimeNotFound):
            get_workspace_delegate_run(
                run_id=str(run.id),
                token=context.workspace_delegate_token,
                scope="connection.create",
            )

    def test_grant_is_caller_and_project_scoped(self):
        request_a = self.request(self.caller_a)
        self.grant(request_a, ["connection.list", "files.read"])
        subject_a = request_subject(request_a)
        grant = AgentWorkspaceGrant.objects.get(
            tenant=self.consumer,
            project=self.project,
            agent=self.agent,
            caller_subject_hash=subject_a.subject_hash,
        )
        self.assertEqual(grant.scopes, ["connection.list", "files.read"])

        request_b = self.request(self.caller_b)
        subject_b = request_subject(request_b)
        self.assertNotEqual(subject_a.subject_hash, subject_b.subject_hash)
        self.assertFalse(
            AgentWorkspaceGrant.objects.filter(
                agent=self.agent,
                caller_subject_hash=subject_b.subject_hash,
            ).exists()
        )

        tenant_request = self.request(self.caller_a, project=False)
        self.grant(tenant_request, ["connection.list"])
        self.assertEqual(
            AgentWorkspaceGrant.objects.filter(
                tenant=self.consumer,
                project__isnull=True,
                agent=self.agent,
                caller_subject_hash=subject_a.subject_hash,
            ).count(),
            1,
        )
        # Repeated writes update the one tenant-level grant instead of creating
        # duplicates through SQL NULL uniqueness semantics.
        self.grant(tenant_request, ["connection.list"])
        self.assertEqual(
            AgentWorkspaceGrant.objects.filter(
                tenant=self.consumer,
                project__isnull=True,
                agent=self.agent,
                caller_subject_hash=subject_a.subject_hash,
            ).count(),
            1,
        )

    def test_caller_can_review_grant_subset_and_revoke_through_api(self):
        client = APIClient()
        client.force_authenticate(self.caller_a)
        path = f"/api/v1/agents/{self.agent.id}/workspace-grant/"
        headers = {
            "HTTP_X_NEXUS_TENANT": str(self.consumer.id),
            "HTTP_X_NEXUS_PROJECT": str(self.project.id),
        }
        initial = client.get(path, **headers)
        self.assertEqual(initial.status_code, 200, initial.content)
        initial_payload = initial.json().get("data", initial.json())
        self.assertEqual(initial_payload["scopes"], [])
        self.assertEqual(
            initial_payload["declared_scopes"],
            self.agent.workspace_capabilities,
        )

        granted = client.put(
            path,
            {"scopes": ["connection.list", "files.read"]},
            format="json",
            **headers,
        )
        self.assertEqual(granted.status_code, 200, granted.content)
        granted_payload = granted.json().get("data", granted.json())
        self.assertEqual(granted_payload["scopes"], ["connection.list", "files.read"])

        revoked = client.delete(path, **headers)
        self.assertEqual(revoked.status_code, 204, revoked.content)
        after = client.get(path, **headers)
        self.assertEqual(after.status_code, 200, after.content)
        after_payload = after.json().get("data", after.json())
        self.assertEqual(after_payload["scopes"], [])

    def test_external_mcp_session_is_opaque_caller_bound_and_deleted(self):
        request_a = self.request(self.caller_a)
        headers, session, external = prepare_mcp_session_request(
            request=request_a,
            tenant=self.consumer,
            agent=self.agent,
            runtime=self.runtime,
            headers={},
        )
        self.assertEqual(headers, {})
        self.assertIsNone(session)
        self.assertEqual(external, "")

        response_headers = {"Mcp-Session-Id": "container-session-secret"}
        finalize_mcp_session_response(
            request=request_a,
            tenant=self.consumer,
            agent=self.agent,
            runtime=self.runtime,
            method="POST",
            status_code=200,
            response_headers=response_headers,
            session=None,
            external_session_id="",
        )
        external_id = response_headers["Mcp-Session-Id"]
        self.assertNotEqual(external_id, "container-session-secret")
        stored = AgentMCPSession.objects.get()
        self.assertEqual(stored.external_session_hash, hash_token(external_id))
        self.assertEqual(stored.internal_session_id, "container-session-secret")

        prepared, session, returned_external = prepare_mcp_session_request(
            request=request_a,
            tenant=self.consumer,
            agent=self.agent,
            runtime=self.runtime,
            headers={"Mcp-Session-Id": external_id, "Last-Event-ID": "event-7"},
        )
        self.assertEqual(prepared["Mcp-Session-Id"], "container-session-secret")
        self.assertEqual(prepared["Last-Event-ID"], "event-7")
        self.assertEqual(returned_external, external_id)

        with self.assertRaises(AgentRuntimeNotFound):
            prepare_mcp_session_request(
                request=self.request(self.caller_b),
                tenant=self.consumer,
                agent=self.agent,
                runtime=self.runtime,
                headers={"Mcp-Session-Id": external_id},
            )

        finalize_mcp_session_response(
            request=request_a,
            tenant=self.consumer,
            agent=self.agent,
            runtime=self.runtime,
            method="DELETE",
            status_code=204,
            response_headers={},
            session=session,
            external_session_id=external_id,
        )
        self.assertFalse(AgentMCPSession.objects.exists())

    def test_legacy_sse_endpoint_is_rewritten_to_caller_bound_session(self):
        request_a = self.request(self.caller_a, method="get")
        line = rewrite_legacy_sse_line(
            b"data: /messages/?session_id=container-legacy-secret\r\n",
            request=request_a,
            tenant=self.consumer,
            agent=self.agent,
            runtime=self.runtime,
        )
        endpoint = line.decode().strip().removeprefix("data: ")
        self.assertIn(f"/api/v1/agents/{self.agent.id}/messages/?session_id=", endpoint)
        self.assertNotIn("container-legacy-secret", endpoint)
        external_id = endpoint.rsplit("session_id=", 1)[-1]

        inbound_a = self.factory.post(f"/?session_id={external_id}", {}, format="json")
        inbound_a.user = self.caller_a
        inbound_a.tenant_id = str(self.consumer.id)
        inbound_a.project_id = str(self.project.id)
        self.assertEqual(
            prepare_legacy_sse_message_path(
                request=inbound_a,
                tenant=self.consumer,
                agent=self.agent,
                runtime=self.runtime,
            ),
            "/messages/?session_id=container-legacy-secret",
        )

        inbound_b = self.factory.post(f"/?session_id={external_id}", {}, format="json")
        inbound_b.user = self.caller_b
        inbound_b.tenant_id = str(self.consumer.id)
        inbound_b.project_id = str(self.project.id)
        with self.assertRaises(AgentRuntimeNotFound):
            prepare_legacy_sse_message_path(
                request=inbound_b,
                tenant=self.consumer,
                agent=self.agent,
                runtime=self.runtime,
            )
