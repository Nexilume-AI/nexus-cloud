"""Run shared notification assertions through the actual Personal Inbox."""
from types import SimpleNamespace

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.agents.models import Agent, AgentDisplayRun
from apps.common.subjects import request_subject
from nexus_personal.services import provision_owner
from tests.notification_source_guards import NotificationSourceGuards
from .test_installation import PASSWORD


@override_settings(ROOT_URLCONF="nexus_personal.urls")
class PersonalNotificationSourceTests(NotificationSourceGuards, TestCase):
    notification_deployment_failure_kind = "job_failed"

    def setUp(self):
        row = provision_owner(email="source-owner@example.test", password=PASSWORD)
        self.user, self.tenant, self.project = row.owner, row.tenant, row.project
        self.agent = Agent.objects.create(tenant=self.tenant, project=self.project,
            name="Notifications", created_by=self.user, status="active")
        self.client = APIClient()
        token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token.key)
        self.headers = {"HTTP_X_NEXUS_TENANT": str(self.tenant.pk),
                        "HTTP_X_NEXUS_PROJECT": str(self.project.pk)}

    def new_run(self, **values):
        subject = request_subject(SimpleNamespace(user=self.user, tenant_id=str(self.tenant.pk),
            project_id=str(self.project.pk), headers={}, META={}))
        defaults = dict(tenant=self.tenant, consumer_tenant=self.tenant, consumer_project=self.project,
            agent=self.agent, run_kind="invocation", caller_principal_type="user",
            caller_principal_id=str(self.user.pk), caller_subject_hash=subject.subject_hash,
            write_token="NEVER_RETURN_TOKEN", status="completed", completed_at=timezone.now())
        return AgentDisplayRun.objects.create(**(defaults | values))

    def listing(self):
        response = self.client.get("/api/v1/inbox/items/", **self.headers)
        summary = self.client.get("/api/v1/inbox/summary/", **self.headers)
        for result in (response, summary):
            self.assertEqual(result.status_code, 200, result.content)
            self.assertEqual(result["Cache-Control"], "private, no-store")
        counts = summary.json()
        # Legacy notices counted failed work as needing attention; Inbox exposes
        # failed and needs_action separately. Normalize only this presentation.
        return {"results": response.json()["results"], "counts": {
            "unread": counts["unread"], "total": counts["total"],
            "needs_attention": counts["needs_attention"] + counts["failed"],
        }}
