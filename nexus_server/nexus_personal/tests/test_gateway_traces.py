"""Real migrated logs/trace HTTP, not a claim that inference is composed yet."""
from uuid import uuid4
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db import connection, IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.test import APIClient
from apps.gateway.models import GatewayRequestLog, GatewayImageOperation
from apps.routers.models import Router
from apps.deployments.models import Deployment, ModelGroup
from .provider_http_fixture import ProviderHTTPFixture
from . import test_deployment_http as source_http
from nexus_personal.router_traces import serialize_router_trace


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalGatewayTraceTests(ProviderHTTPFixture, TestCase):
    source = source_http.PersonalDeploymentHTTPTests.source
    post = source_http.PersonalDeploymentHTTPTests.post
    runtime = source_http.PersonalDeploymentHTTPTests.runtime

    def setUp(self):
        super().setUp()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)
        self.client.get("/api/v1/public/bootstrap/")
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value, "HTTP_ORIGIN": "http://testserver"}
        self.router = Router.objects.create(tenant=self.installation.tenant, project=self.installation.project,
            created_by=self.installation.owner, name="local-router")
        self.path = f"/api/v1/routers/{self.router.pk}/traces/"

    def log(self, **changes):
        return GatewayRequestLog.objects.create(**{
            "tenant": self.installation.tenant, "project_id": str(self.installation.project.pk),
            "actor": self.installation.owner, "router": self.router, "model": "personal-model",
            "status": "success", "request_tokens": 5, "response_tokens": 7, "total_tokens": 12,
            **changes})

    def test_real_tables_have_no_commercial_fields_and_image_dedup_is_durable(self):
        self.assertEqual(len(list(apps.get_app_config("gateway").get_models())), 2)
        for model, names in ((GatewayRequestLog, ("api_key", "cost", "currency")),
                             (GatewayImageOperation, ("pricing_snapshot",))):
            self.assertIn(model._meta.db_table, connection.introspection.table_names())
            with connection.cursor() as cursor:
                columns = {col.name for col in connection.introspection.get_table_description(cursor, model._meta.db_table)}
            for name in names:
                with self.assertRaises(FieldDoesNotExist):
                    model._meta.get_field(name)
                self.assertNotIn(name, columns)
                self.assertNotIn(name + "_id", columns)
        log = self.log()
        data = dict(tenant=log.tenant, principal="local-owner", key_digest="digest", request_digest="request",
                    operation="images.generations", gateway_log=log)
        operation = GatewayImageOperation.objects.create(**data)
        with self.assertRaises(IntegrityError), transaction.atomic():
            GatewayImageOperation.objects.create(**data)
        log.delete()
        operation.refresh_from_db()
        self.assertIsNone(operation.gateway_log_id)

    def test_http_preserves_operational_facts_not_billing_or_raw_payload(self):
        source = Deployment.objects.get(pk=self.source()["id"])
        group = ModelGroup.objects.get()
        trace = {"version": 1, "Authorization": "never-return-secret", "router": {
            "strategy": "manual_priority", "reason": "never-return-secret", "selected_pool_id": str(group.pk),
            "candidates": [{"pool_id": str(group.pk), "pool": "stale-name", "rank": 1, "priority": 100,
                            "lowest_price_per_1k_tokens": "never-return-secret"}]},
            "pool": {"strategy": "fallback", "selected_source_id": str(source.pk),
                     "candidates": [{"source_id": str(source.pk), "source": "stale-name", "latency_ms": 31}]}}
        self.log(consumer_source=source, deployment=source, selected_model_group=group, routing_trace=trace,
                 status="failed", error_code="UPSTREAM_UNAVAILABLE", fallback_count=2)
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data["items"][0]
        self.assertEqual(row["total_tokens"], 12)
        self.assertEqual(row["error_code"], "UPSTREAM_UNAVAILABLE")
        self.assertEqual(row["trace"]["router"]["candidates"][0]["pool"], group.display_name or group.name)
        self.assertEqual(row["trace"]["pool"]["candidates"][0]["latency_ms"], 31)
        self.assertFalse({"cost", "currency", "api_key"} & row.keys())
        self.assertNotIn("never-return-secret", str(response.data))
        self.assertNotIn("stale-name", str(response.data))
        self.assertEqual(self.post(self.path).status_code, 405)

    def test_equal_timestamps_keyset_and_router_bound_cursors(self):
        logs = [self.log(request_id=str(i)) for i in range(3)]
        GatewayRequestLog.objects.filter(pk__in=[l.pk for l in logs]).update(created_at=timezone.now())
        seen = []
        query = {"limit": 1}
        for _ in range(3):
            response = self.client.get(self.path, query)
            self.assertEqual(response.status_code, 200, response.data)
            seen.append(response.data["items"][0]["id"])
            cursor = response.data["next_cursor"]
            if cursor:
                other = Router.objects.create(tenant=self.router.tenant, created_by=self.installation.owner, name="other")
                self.assertEqual(self.client.get(f"/api/v1/routers/{other.pk}/traces/", {"limit": 1, "cursor": cursor}).status_code, 400)
                query["cursor"] = cursor
        self.assertEqual(set(seen), {str(l.pk) for l in logs})
        self.assertIsNone(cursor)
        for params in ({"cursor": "forged"}, {"limit": "bad"}, {"limit": 101}, {"limit": 0}):
            self.assertEqual(self.client.get(self.path, params).status_code, 400)

    def test_current_owner_actor_and_context_are_required(self):
        self.log(request_id="mine")
        self.log(actor=self.other, request_id="other-actor")
        self.log(project_id=str(uuid4()), request_id="other-project")
        rows = self.client.get(self.path).data["items"]
        self.assertEqual([r["request_id"] for r in rows], ["mine"])
        self.assertIn(self.client.get(self.path, HTTP_X_NEXUS_PROJECT=str(uuid4())).status_code, (403, 404))
        self.client.force_login(self.other)
        self.assertIn(self.client.get(self.path).status_code, (401, 403))
        self.client.force_login(self.installation.owner)
        Router.objects.filter(pk=self.router.pk).update(created_by=self.other)
        self.assertIn(self.client.get(self.path).status_code, (403, 404))
        self.client.logout()
        self.assertIn(self.client.get(self.path).status_code, (401, 403))

    def test_related_resources_rechecked_and_malformed_history_is_bounded(self):
        source = Deployment.objects.get(pk=self.source()["id"])
        group = ModelGroup.objects.get()
        trace = {"router": {"strategy": [], "candidates": [{"pool_id": str(group.pk)}, {"pool_id": "bad-id"}, None]},
                 "pool": {"strategy": "fallback", "selected_source_id": str(source.pk),
                          "candidates": [{"source_id": str(source.pk), "source": "foreign-secret"}]}}
        self.log(consumer_source=source, deployment=source, selected_model_group=group, routing_trace=trace)
        ModelGroup.objects.filter(pk=group.pk).update(project_id=None, created_by=self.other)
        Deployment.objects.filter(pk=source.pk).update(created_by=self.other)
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data["items"][0]
        self.assertIsNone(row["selected_pool_id"])
        self.assertIsNone(row["selected_source_id"])
        self.assertEqual(row["trace"]["pool"]["candidates"], [])
        self.assertNotIn(str(source.pk), str(response.data))
        self.assertNotIn("foreign-secret", str(response.data))
        for invalid in ([], "malformed", {"pool": {"candidates": "not-list"}}):
            GatewayRequestLog.objects.all().update(routing_trace=invalid)
            self.assertEqual(self.client.get(self.path).status_code, 200)

    def test_no_context_cannot_serialize_stale_log(self):
        log = self.log()
        with self.assertRaises(exceptions.NotAuthenticated):
            serialize_router_trace(log=log)
