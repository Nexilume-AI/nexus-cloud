"""Real operational records through the private-import-blocked Personal host."""
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.audit.models import AuditLog
from apps.jobs.models import Job, JobEvent
from apps.tenancy.models import Tenant, Project
from nexus_personal.services import provision_owner


@override_settings(ROOT_URLCONF="nexus_personal.urls")
class PersonalMonitoringCatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.installation = provision_owner(email="monitor-owner@example.test",
            password="fixture-monitoring-owner-4928!")
        cls.owner = cls.installation.owner
        cls.other = get_user_model().objects.create_user(username="monitor-other")
        cls.foreign_tenant = Tenant.objects.create(name="Foreign")
        cls.foreign_project = Project.objects.create(tenant=cls.foreign_tenant, name="Foreign")
        cls.job = Job.objects.create(tenant=cls.installation.tenant, project=cls.installation.project,
            job_type="agent.build", resource_type="agent", status="failed", error_code="BUILD_FAILED",
            input_json={"token": "private-job-secret"}, result_json={"prompt": "private-job-secret"},
            error_message="private-job-secret", celery_task_id="private-job-secret")
        cls.foreign_job = Job.objects.create(tenant=cls.foreign_tenant, project=cls.foreign_project,
            job_type="foreign-task", resource_type="agent")
        cls.event = JobEvent.objects.create(tenant=cls.installation.tenant, job=cls.job,
            event_type="failed", message="private-event-secret", metadata_json={"password": "private-event-secret"})
        cls.audit = AuditLog.objects.create(tenant_id=str(cls.installation.tenant_id),
            project_id=str(cls.installation.project_id), actor=cls.owner, action="agent.update",
            resource_type="agent", resource_id=str(uuid4()), request_id="monitor-request",
            metadata={"token": "private-audit-secret"}, before_snapshot={"token": "private-audit-secret"},
            after_snapshot={"token": "private-audit-secret"}, user_agent="private-audit-secret")
        cls.foreign_audit = AuditLog.objects.create(tenant_id=str(cls.foreign_tenant.pk),
            project_id=str(cls.foreign_project.pk), action="agent.update", resource_type="agent")

    def setUp(self):
        self.client = APIClient()
        self.client.force_login(self.owner)

    def test_real_jobs_details_and_events_are_bounded_projections(self):
        response = self.client.get("/api/v1/metrics/jobs/?limit=1")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["items"][0]["id"], str(self.job.pk))
        self.assertEqual(response.data["items"][0]["error_code"], "BUILD_FAILED")
        for path in ("jobs/?limit=1", f"jobs/{self.job.pk}/", f"jobs/{self.job.pk}/events/"):
            response = self.client.get("/api/v1/metrics/" + path)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response["Cache-Control"], "private, no-store")
            self.assertNotIn("private-job-secret", response.content.decode())
            self.assertNotIn("private-event-secret", response.content.decode())
        self.assertEqual(self.client.get(f"/api/v1/metrics/jobs/{self.job.pk}/events/").data[0]["id"], str(self.event.pk))

    def test_real_audit_filter_detail_and_sensitive_snapshot_exclusion(self):
        response = self.client.get("/api/v1/metrics/audit/?limit=2&resource_type=agent&request_id=monitor-request")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item["id"] for item in response.data["items"]], [str(self.audit.pk)])
        detail = self.client.get(f"/api/v1/metrics/audit/{self.audit.pk}/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["request_id"], "monitor-request")
        for item in (response, detail):
            self.assertNotIn("private-audit-secret", item.content.decode())
        self.assertEqual(self.client.get("/api/v1/metrics/audit/?q=no-match").data, [])

    def test_shared_audit_records_keep_owner_context_and_operational_aliases(self):
        from django.test import RequestFactory
        from apps.audit import services
        from apps.audit.action_names import CORE_ACTION_ALIASES, configured_action_aliases
        self.assertEqual(services.ACTION_ALIASES, CORE_ACTION_ALIASES)
        self.assertEqual(configured_action_aliases(), CORE_ACTION_ALIASES)
        self.assertEqual(set(services.action_filter_values("agent.create")), {"agent.create", "agents.create"})
        request = RequestFactory().post("/api/v1/agents/", HTTP_USER_AGENT="owner-client")
        request.user = self.owner
        request.tenant_id = str(self.installation.tenant_id)
        request.project_id = str(self.installation.project_id)
        request.request_id = "owner-audit-write"
        row = services.log_audit(request=request, actor=self.owner, action="agents.create",
            resource_type="agent", resource_id="example-agent", after={"name": "Example"})
        row.refresh_from_db()
        self.assertEqual(row.tenant_id, str(self.installation.tenant_id))
        self.assertEqual(row.project_id, str(self.installation.project_id))
        self.assertEqual(row.actor, self.owner)
        self.assertEqual(row.request_id, "owner-audit-write")
        self.assertEqual(row.after_snapshot, {"name": "Example"})
        self.assertEqual(row.user_agent, "owner-client")
        canonical = AuditLog.objects.create(tenant_id=row.tenant_id, project_id=row.project_id,
            actor=self.owner, action="agent.create", resource_type="agent")
        AuditLog.objects.create(tenant_id=str(self.foreign_tenant.pk), project_id=str(self.foreign_project.pk),
            action="agents.create", resource_type="agent")
        for action in ("agent.create", "agents.create"):
            response = self.client.get("/api/v1/metrics/audit/", {"action": action})
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual({item["id"] for item in response.data}, {str(row.pk), str(canonical.pk)})
        page = self.client.get("/api/v1/metrics/audit/", {"action": "agent.create", "limit": 1})
        self.assertTrue(page.data["next_cursor"])
        invalid = self.client.get("/api/v1/metrics/audit/", {"action": "agent.stop", "limit": 1,
            "cursor": page.data["next_cursor"]})
        self.assertEqual(invalid.status_code, 400)

    def test_legacy_audit_authority_is_not_implicitly_enabled(self):
        from apps.audit.services import require_audit_permission
        from rest_framework.exceptions import PermissionDenied
        # Personal uses the owner-bound, redacted monitoring endpoint above;
        # the old unscoped audit management interface remains unmounted.
        with self.assertRaises(PermissionDenied):
            require_audit_permission(user=self.owner, tenant=self.installation.tenant)
        self.assertEqual(self.client.get("/api/v1/audit/logs/").status_code, 404)

    def test_foreign_records_owner_and_forged_context_are_denied(self):
        for path in (f"jobs/{self.foreign_job.pk}/", f"jobs/{self.foreign_job.pk}/events/",
                     f"audit/{self.foreign_audit.pk}/", "jobs/not-a-uuid/"):
            self.assertEqual(self.client.get("/api/v1/metrics/" + path).status_code, 404, path)
        for headers in ({"HTTP_X_NEXUS_PROJECT": str(self.foreign_project.pk)},
                        {"HTTP_X_NEXUS_TENANT": str(self.foreign_tenant.pk)}):
            self.assertIn(self.client.get("/api/v1/metrics/jobs/", **headers).status_code, (401, 403))
        self.client.force_login(self.other)
        self.assertIn(self.client.get("/api/v1/metrics/audit/").status_code, (401, 403))
        self.client.logout()
        self.assertIn(self.client.get("/api/v1/metrics/jobs/").status_code, (401, 403))

    def test_pagination_no_truncation_and_cursor_cannot_change_filters(self):
        Job.objects.bulk_create([Job(tenant=self.installation.tenant, project=self.installation.project,
            job_type="bulk-task", resource_type="dataset") for _ in range(105)])
        self.assertEqual(self.client.get("/api/v1/metrics/jobs/").status_code, 400)
        seen, cursor = set(), None
        while True:
            params = {"limit": 40, "job_type": "bulk-task"}
            if cursor:
                params["cursor"] = cursor
            response = self.client.get("/api/v1/metrics/jobs/", params)
            self.assertEqual(response.status_code, 200, response.data)
            ids = {row["id"] for row in response.data["items"]}
            self.assertFalse(seen & ids)
            seen.update(ids)
            cursor = response.data["next_cursor"]
            if not cursor:
                break
            invalid = self.client.get("/api/v1/metrics/jobs/", {"limit": 40, "job_type": "agent.build", "cursor": cursor})
            self.assertEqual(invalid.status_code, 400)
        self.assertEqual(len(seen), 105)
        for query in ("limit=0", "window_seconds=1", "created_from=not-a-date", "q=" + "x" * 201):
            self.assertEqual(self.client.get("/api/v1/metrics/jobs/?" + query).status_code, 400, query)

    def test_policy_is_personal_without_financial_or_unknown_authority(self):
        from django.test import RequestFactory
        from django.core.exceptions import ImproperlyConfigured
        from rest_framework.exceptions import PermissionDenied
        from apps.metrics import policy, policy_host
        from nexus_personal import monitoring_policy
        self.assertIs(policy, monitoring_policy)
        request = RequestFactory().get("/api/v1/metrics/jobs/")
        request.user = self.owner
        self.assertEqual(policy.context(request), (self.installation.tenant, str(self.installation.project_id)))
        self.assertFalse(policy.financial(request, self.installation.tenant))
        self.assertFalse(policy.capabilities(request)["reports"])
        for action in ("metrics.financial.read", "tenant.admin", "unknown"):
            with self.assertRaises(PermissionDenied):
                policy.context(request, action)
        with self.assertRaises(PermissionDenied):
            policy.financial(request, self.foreign_tenant)
        with override_settings(NEXUS_MONITORING_POLICY_MODULE=""), self.assertRaises(ImproperlyConfigured):
            policy_host.configured_policy()
        # The finance/reporting surface is never implicitly mounted here.
        self.assertEqual(self.client.get("/api/v1/reports/deliveries/").status_code, 404)
