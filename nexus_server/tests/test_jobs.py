from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.jobs.models import Job
from apps.jobs.services import create_job, fail_job, start_job, succeed_job
from apps.tenancy.models import Membership, Tenant


class JobAPITests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="jobs_owner",
            email="jobs-owner@example.com",
            password="password",
        )
        self.other_owner = user_model.objects.create_user(
            username="jobs_other_owner",
            email="jobs-other-owner@example.com",
            password="password",
        )
        self.tenant = Tenant.objects.create(name="Jobs Tenant", slug="jobs-tenant")
        self.other_tenant = Tenant.objects.create(name="Other Jobs Tenant", slug="other-jobs-tenant")
        Membership.objects.create(tenant=self.tenant, user=self.owner, role=Membership.ROLE_OWNER)
        Membership.objects.create(tenant=self.other_tenant, user=self.other_owner, role=Membership.ROLE_OWNER)
        self.client = APIClient()

    def test_job_api_uses_envelope_and_redacts_payloads(self) -> None:
        request = self._request(self.owner, self.tenant)
        job = create_job(
            request=request,
            job_type="providers.runtime.login",
            resource_type="provider_account",
            resource_id="acct_1",
            input_json={
                "username": "alice",
                "password": "plain-password",
                "api_key": "sk-plaintext",
                "key_prefix": "sk-visible-prefix",
            },
        )
        start_job(job_id=job.id)
        succeed_job(job_id=job.id, result_json={"access_token": "secret-token", "key_prefix": "safe-prefix"})
        self.client.force_authenticate(self.owner)

        response = self.client.get(f"/api/v1/jobs/{job.id}/", HTTP_X_NEXUS_TENANT=str(self.tenant.id))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIs(payload["ok"], True)
        self.assertEqual(payload["data"]["status"], Job.STATUS_SUCCEEDED)
        self.assertEqual(payload["data"]["input_json"]["password"], "***REDACTED***")
        self.assertEqual(payload["data"]["input_json"]["api_key"], "***REDACTED***")
        self.assertEqual(payload["data"]["input_json"]["key_prefix"], "sk-visible-prefix")
        self.assertEqual(payload["data"]["result_json"]["access_token"], "***REDACTED***")
        self.assertTrue(AuditLog.objects.filter(action="jobs.create", tenant_id=str(self.tenant.id)).exists())
        self.assertTrue(AuditLog.objects.filter(action="jobs.succeed", tenant_id=str(self.tenant.id)).exists())

    def test_job_events_are_tenant_scoped(self) -> None:
        job = create_job(
            request=self._request(self.owner, self.tenant),
            job_type="agents.runtime.deploy",
            resource_type="agent_runtime_deployment",
            resource_id="runtime_1",
            input_json={"secret": "hidden"},
        )
        other_job = create_job(
            request=self._request(self.other_owner, self.other_tenant),
            job_type="agents.runtime.deploy",
            resource_type="agent_runtime_deployment",
            resource_id="runtime_2",
        )
        fail_job(job_id=job.id, error_code="FAILED", error_message="boom")
        self.client.force_authenticate(self.owner)

        list_response = self.client.get("/api/v1/jobs/", HTTP_X_NEXUS_TENANT=str(self.tenant.id))
        other_response = self.client.get(f"/api/v1/jobs/{other_job.id}/", HTTP_X_NEXUS_TENANT=str(self.tenant.id))
        events_response = self.client.get(f"/api/v1/jobs/{job.id}/events/", HTTP_X_NEXUS_TENANT=str(self.tenant.id))

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.json()["data"]), 1)
        self.assertEqual(list_response.json()["data"][0]["id"], str(job.id))
        self.assertEqual(other_response.status_code, 404)
        self.assertEqual(events_response.status_code, 200)
        self.assertGreaterEqual(len(events_response.json()["data"]), 2)

    def _request(self, user, tenant):
        request = type("Request", (), {})()
        request.user = user
        request.tenant_id = str(tenant.id)
        request.project_id = ""
        request.request_id = "req_jobs_test"
        request.META = {}
        return request
