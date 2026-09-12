"""Actual owner HTTP actions and correlation, never importing commercial reports."""
from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.agents.models import Agent, AgentRuntimeInvocation
from apps.audit.models import AuditLog
from apps.gateway.models import GatewayRequestLog
from apps.metrics.models import AlertRule, AlertEvent, AlertNotification
from apps.tenancy.models import Tenant, Project
from nexus_personal.services import provision_owner


@override_settings(ROOT_URLCONF="nexus_personal.urls")
class PersonalMonitoringActionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        instance = provision_owner(email="actions-owner@example.test", password="fixture-actions-owner-74829!")
        cls.owner, cls.tenant, cls.project = instance.owner, instance.tenant, instance.project
        cls.other = get_user_model().objects.create_user(username="actions-other")
        cls.foreign = Tenant.objects.create(name="Other installation")
        cls.foreign_project = Project.objects.create(tenant=cls.foreign, name="Other")
        cls.other_project = Project.objects.create(tenant=cls.tenant, name="Not installation project")
        cls.rule = AlertRule.objects.create(tenant=cls.tenant, project=cls.project, created_by=cls.owner,
            metric="system.counts.agents", threshold="1", threshold_value=1)
        cls.event = AlertEvent.objects.create(tenant=cls.tenant, rule=cls.rule, metric=cls.rule.metric,
            value=1, triggered_at=timezone.now())
        cls.notice = AlertNotification.objects.create(tenant=cls.tenant, event=cls.event,
            target="owner@example.test", delivery_status="failed", delivery_key="actions-fixture",
            attempts=5, error_message="old error", lease_id="expired-lease",
            lease_until=timezone.now()-timedelta(minutes=1))
        cls.agent = Agent.objects.create(tenant=cls.tenant, project=cls.project, name="Observed")

    def setUp(self):
        self.client = APIClient()
        self.client.force_login(self.owner)

    def action(self, kind, row, action, **headers):
        return self.client.post(f"/api/v1/metrics/actions/{kind}/{row.pk}/",
            {"action": action}, format="json", **headers)

    def test_pause_resume_mute_unmute_and_project_audit(self):
        for action, state in (("pause", "disabled"), ("resume", "active"), ("mute", "active"), ("unmute", "active")):
            response = self.action("rules", self.rule, action)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response["Cache-Control"], "private, no-store")
            self.rule.refresh_from_db()
            self.assertEqual(self.rule.status, state)
            if action == "mute":
                self.assertGreater(self.rule.muted_until, timezone.now()+timedelta(minutes=59))
            if action == "unmute":
                self.assertIsNone(self.rule.muted_until)
        logs = AuditLog.objects.filter(action__startswith="monitoring.rules.")
        self.assertEqual(logs.count(), 4)
        self.assertEqual(set(logs.values_list("project_id", flat=True)), {str(self.project.pk)})

    def test_acknowledge_is_stable_and_manual_test_cannot_be_incident(self):
        self.assertEqual(self.action("incidents", self.event, "acknowledge").status_code, 200)
        self.event.refresh_from_db()
        previous = self.event.acknowledged_at
        self.assertEqual(self.event.acknowledged_by_id, self.owner.pk)
        self.assertEqual(self.action("incidents", self.event, "acknowledge").status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.acknowledged_at, previous)
        self.event.is_test = True
        self.event.save(update_fields=["is_test"])
        self.assertEqual(self.action("incidents", self.event, "acknowledge").status_code, 400)

    def test_durable_retry_resets_claim_and_rejects_second_retry_or_revoked_rule(self):
        response = self.action("notifications", self.notice, "retry")
        self.assertEqual(response.status_code, 200, response.data)
        self.notice.refresh_from_db()
        self.assertEqual((self.notice.delivery_status, self.notice.attempts, self.notice.lease_id, self.notice.error_message),
            ("pending", 0, "", ""))
        self.assertIsNone(self.notice.lease_until)
        self.assertIsNotNone(self.notice.next_attempt_at)
        self.assertEqual(self.action("notifications", self.notice, "retry").status_code, 400)
        for state in ("disabled", "deleted"):
            AlertRule.objects.filter(pk=self.rule.pk).update(status=state)
            AlertNotification.objects.filter(pk=self.notice.pk).update(delivery_status="failed")
            self.assertEqual(self.action("notifications", self.notice, "retry").status_code, 404)
            self.notice.refresh_from_db()
            self.assertEqual(self.notice.delivery_status, "failed")

    def test_missing_durable_key_and_unsupported_actions_do_not_mutate(self):
        AlertNotification.objects.filter(pk=self.notice.pk).update(delivery_key=None)
        self.assertEqual(self.action("notifications", self.notice, "retry").status_code, 400)
        for kind, row, action in (("rules", self.rule, "delete"), ("incidents", self.event, "retry"),
                                  ("rules", self.rule, ""), ("notifications", self.notice, "pause")):
            self.assertEqual(self.action(kind, row, action).status_code, 400)
        for kind in ("deliveries", "reports", "anything"):
            self.assertEqual(self.action(kind, self.notice, "retry").status_code, 404)

    def test_audit_failure_rolls_back_actual_mutation(self):
        self.client.raise_request_exception = False
        with patch("apps.metrics.operational_actions.write_audit_log", side_effect=RuntimeError("audit unavailable")):
            response = self.action("rules", self.rule, "pause")
        self.assertEqual(response.status_code, 500)
        self.rule.refresh_from_db()
        self.assertEqual(self.rule.status, "active")

    def test_session_mutation_still_requires_csrf(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.owner)
        response = client.post(f"/api/v1/metrics/actions/rules/{self.rule.pk}/", {"action": "pause"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.rule.refresh_from_db()
        self.assertEqual(self.rule.status, "active")

    def test_wrong_scope_owner_and_forged_context_cannot_act(self):
        for attrs in ({"tenant": self.foreign, "project": self.foreign_project},
                      {"tenant": self.tenant, "project": self.other_project},
                      {"tenant": self.tenant, "project": self.project, "created_by": self.other}):
            rule = AlertRule.objects.create(**attrs, metric=self.rule.metric, threshold="1", threshold_value=1)
            self.assertEqual(self.action("rules", rule, "pause").status_code, 404)
        for headers in ({"HTTP_X_NEXUS_TENANT": str(self.foreign.pk)},
                        {"HTTP_X_NEXUS_PROJECT": str(self.other_project.pk)}):
            self.assertIn(self.action("rules", self.rule, "pause", **headers).status_code, (401, 403))
        self.client.force_login(self.other)
        self.assertIn(self.action("rules", self.rule, "pause").status_code, (401, 403))
        self.client.logout()
        self.assertIn(self.action("rules", self.rule, "pause").status_code, (401, 403))
        self.rule.refresh_from_db()
        self.assertEqual(self.rule.status, "active")

    def test_request_links_are_real_bounded_and_not_raw_payloads(self):
        GatewayRequestLog.objects.bulk_create([GatewayRequestLog(tenant=self.tenant,
            project_id=str(self.project.pk), request_id="correlation-owner", model="private-model-marker") for _ in range(105)])
        AgentRuntimeInvocation.objects.create(tenant=self.tenant, project=self.project, agent=self.agent,
            request_id="correlation-owner", status="success")
        GatewayRequestLog.objects.create(tenant=self.foreign, project_id=str(self.foreign_project.pk),
            request_id="correlation-owner", model="foreign-marker")
        GatewayRequestLog.objects.create(tenant=self.foreign, project_id=str(self.foreign_project.pk),
            request_id="foreign-only", model="foreign-marker")
        response = self.client.get("/api/v1/metrics/requests/correlation-owner/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["items"]), 101)
        self.assertEqual(response.data["limit_per_surface"], 100)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual({row["kind"] for row in response.data["items"]}, {"gateway", "agent"})
        for marker in ("private-model-marker", "foreign-marker", "prompt", "transcript"):
            self.assertNotIn(marker, str(response.data["items"]))
        for identifier in ("not-retained", "foreign-only", "x"*65):
            self.assertEqual(self.client.get(f"/api/v1/metrics/requests/{identifier}/").status_code, 404)
        self.client.force_login(self.other)
        self.assertIn(self.client.get("/api/v1/metrics/requests/correlation-owner/").status_code, (401, 403))
