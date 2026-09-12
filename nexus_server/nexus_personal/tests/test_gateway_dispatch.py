"""Real authenticated HTTP Gateway lifecycle with the existing upstream fixture."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.test import APIClient
from rest_framework.authtoken.models import Token
from apps.deployments.models import Deployment, ModelGroup
from apps.gateway.models import GatewayRequestLog
from apps.gateway import services as gateway
from apps.routers import services as routers
from nexus_personal import gateway_lifecycle as lifecycle
from nexus_personal.models import PersonalGatewayRequest, PersonalGatewayAttempt
from .provider_http_fixture import ProviderHTTPFixture
from . import test_deployment_http as source_http


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalGatewayDispatchTests(ProviderHTTPFixture, TestCase):
    source = source_http.PersonalDeploymentHTTPTests.source
    post = source_http.PersonalDeploymentHTTPTests.post
    runtime = source_http.PersonalDeploymentHTTPTests.runtime

    def setUp(self):
        super().setUp()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)
        self.client.get("/api/v1/public/bootstrap/")
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value, "HTTP_ORIGIN": "http://testserver"}
        self.source_row = Deployment.objects.get(pk=self.source()["id"])
        self.group = ModelGroup.objects.get()
        self.router = routers.create_router(request=self.request(), name="actual-dispatch", model_group_ids=[self.group.pk])
        routers.deploy_router(request=self.request(), router_id=str(self.router.pk))
        self.model = self.router.outputs.get().model_name
        self.path = "/api/v1/openai/v1/chat/completions"
        type(self).calls.clear()

    def payload(self, **changes):
        return {"model": self.model, "router_id": str(self.router.pk),
                "messages": [{"role": "user", "content": "do-not-persist-this-prompt"}], **changes}

    def invoke(self, *, key=None, **changes):
        headers = {**self.headers}
        if key is not None:
            headers["HTTP_X_REQUEST_ID"] = key
        return self.client.post(self.path, self.payload(**changes), format="json", **headers)

    def test_real_session_request_receipt_usage_audit_and_trace(self):
        response = self.invoke(key="completion-1")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["choices"][0]["message"]["content"], "healthy")
        self.assertEqual(response["X-Request-ID"], "completion-1")
        row = PersonalGatewayRequest.objects.get()
        self.assertEqual(row.state, "completed")
        self.assertEqual(row.attempts.get().state, "completed")
        self.assertEqual(row.result_log.total_tokens, 12)
        self.assertEqual(row.result_log.actor_id, self.installation.owner.pk)
        self.assertEqual(self.group.deployment_links.get().selection_count, 1)
        trace = self.client.get(f"/api/v1/routers/{self.router.pk}/traces/")
        self.assertEqual(trace.status_code, 200)
        self.assertEqual(trace.data["items"][0]["request_id"], "completion-1")
        self.assertEqual(self.calls, [("POST", "/v1/chat/completions", "personal-model")])
        persisted = str(list(PersonalGatewayRequest.objects.values())) + str(list(GatewayRequestLog.objects.values()))
        self.assertNotIn("do-not-persist-this-prompt", persisted)
        self.assertNotIn("local-provider-test-key", persisted)
        self.assertNotIn("encrypted_key", persisted)

    def test_replay_changed_payload_and_deleted_log_never_dispatch_again(self):
        self.assertEqual(self.invoke(key="one-request").status_code, 200)
        for changes in ({}, {"messages": [{"role": "user", "content": "changed"}]}):
            response = self.invoke(key="one-request", **changes)
            self.assertEqual(response.status_code, 409, response.data)
        GatewayRequestLog.objects.all().delete()
        self.assertEqual(self.invoke(key="one-request").status_code, 409)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(PersonalGatewayRequest.objects.count(), 1)
        self.assertEqual(self.invoke(key="deliberate-next-request").status_code, 200)
        self.assertEqual(len(self.calls), 2)

    def test_owner_bearer_csrf_and_context_are_enforced_before_upstream(self):
        bad = APIClient(enforce_csrf_checks=True)
        bad.force_login(self.installation.owner)
        self.assertEqual(bad.post(self.path, self.payload(), format="json").status_code, 403)
        self.client.logout()
        self.assertIn(self.invoke().status_code, (401, 403))
        credential = Token.objects.create(user=self.installation.owner)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + credential.key)
        response = self.invoke(key="bearer-owner")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(self.calls), 1)
        denied = self.client.post(self.path, self.payload(), format="json", HTTP_X_NEXUS_PROJECT=str(uuid4()))
        self.assertEqual(denied.status_code, 403)
        credential.delete()
        self.assertIn(self.invoke().status_code, (401, 403))
        self.assertEqual(len(self.calls), 1)

    def test_stream_completion_and_truncation_are_distinct_and_not_replayed(self):
        response = self.invoke(key="stream-complete", stream=True)
        body = b"".join(response.streaming_content).decode()
        self.assertIn("hello ", body)
        self.assertIn("world", body)
        self.assertIn("[DONE]", body)
        self.assertEqual(PersonalGatewayRequest.objects.get(request_id="stream-complete").state, "completed")
        type(self).gateway_stream_mode = "truncated"
        response = self.invoke(key="stream-truncated", stream=True)
        body = b"".join(response.streaming_content).decode()
        self.assertIn("PROVIDER_STREAM_INCOMPLETE", body)
        row = PersonalGatewayRequest.objects.get(request_id="stream-truncated")
        self.assertEqual(row.state, "interrupted")
        self.assertIsNone(row.result_log_id)
        self.assertEqual(row.attempts.get().state, "interrupted")
        before = len(self.calls)
        response = self.invoke(key="stream-truncated", stream=True)
        self.assertIn("GATEWAY_REQUEST_REPLAY", b"".join(response.streaming_content).decode())
        self.assertEqual(len(self.calls), before)

    def test_known_upstream_failure_and_source_fallback_are_recorded(self):
        runtime, offer = self.runtime("fallback-upstream")
        response = self.post("/api/v1/deployments/", {"source_type": "provider_runtime", "provider_runtime_id": str(runtime.pk),
            "model_offer_id": str(offer.pk), "deployment_id": "fallback-source", "model_group_id": str(self.group.pk)})
        self.assertEqual(response.status_code, 201, response.data)
        self.group.deployment_links.filter(deployment=self.source_row).update(priority=1, fallback_order=1)
        self.group.deployment_links.exclude(deployment=self.source_row).update(priority=2, fallback_order=2)
        type(self).calls.clear()
        type(self).gateway_failures_remaining = 1
        response = self.invoke(key="fallback-request")
        self.assertEqual(response.status_code, 200, response.data)
        row = PersonalGatewayRequest.objects.get()
        self.assertEqual(row.state, "completed")
        self.assertEqual(set(row.attempts.values_list("state", flat=True)), {"failed", "completed"})
        self.assertEqual(row.result_log.fallback_count, 1)
        self.assertEqual(GatewayRequestLog.objects.filter(request_id=row.request_id).count(), 2)
        self.assertEqual(len(self.calls), 2)

    def test_http_health_failure_skips_source_and_recovery_restores_dispatch(self):
        runtime, offer = self.runtime("health-fallback")
        response = self.post("/api/v1/deployments/", {
            "source_type": "provider_runtime", "provider_runtime_id": str(runtime.pk),
            "model_offer_id": str(offer.pk), "deployment_id": "health-fallback-source",
            "model_group_id": str(self.group.pk),
        })
        self.assertEqual(response.status_code, 201, response.data)
        second = Deployment.objects.get(pk=response.data["id"])
        self.group.deployment_links.filter(deployment=self.source_row).update(priority=1, fallback_order=1)
        self.group.deployment_links.filter(deployment=second).update(priority=2, fallback_order=2)
        path = f"/api/v1/deployments/{self.source_row.pk}/health-check/"
        type(self).catalog_status = 503
        health = self.post(path)
        self.assertEqual(health.status_code, 200, health.data)
        self.assertEqual(health.data["health_status"], "unhealthy")
        type(self).catalog_status = 200
        type(self).calls.clear()
        reply = self.invoke(key="health-skipped")
        self.assertEqual(reply.status_code, 200, reply.data)
        receipt = PersonalGatewayRequest.objects.get(request_id="health-skipped")
        self.assertEqual(receipt.attempts.count(), 1)
        self.assertEqual(receipt.result_log.deployment_id, second.pk)
        self.assertEqual(self.calls, [("POST", "/v1/chat/completions", "personal-model")])
        health = self.post(path)
        self.assertEqual(health.status_code, 200, health.data)
        self.assertEqual(health.data["health_status"], "healthy")
        type(self).calls.clear()
        reply = self.invoke(key="health-recovered")
        self.assertEqual(reply.status_code, 200, reply.data)
        receipt = PersonalGatewayRequest.objects.get(request_id="health-recovered")
        self.assertEqual(receipt.result_log.deployment_id, self.source_row.pk)
        self.assertEqual(receipt.attempts.count(), 1)
        self.assertEqual(self.calls, [("POST", "/v1/chat/completions", "personal-model")])

    def test_expired_request_survives_restart_and_duplicate_attempt_is_rejected(self):
        request = self.request()
        request.request_id = "interrupted-request"
        payload = self.payload()
        candidates = gateway.resolve_deployment_candidates(request=request, tenant=self.installation.tenant, payload=payload)
        handle = lifecycle.reserve(request=request, tenant=self.installation.tenant, deployments=candidates, provider_payload=payload)
        attempt = lifecycle.prepare_attempt(request=request, deployment=candidates[0], provider_payload=payload)
        with self.assertRaises(exceptions.APIException):
            lifecycle.prepare_attempt(request=request, deployment=candidates[0], provider_payload=payload)
        PersonalGatewayRequest.objects.filter(pk=handle.id).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(lifecycle.expire_requests(tenant_id=self.installation.tenant.pk), 1)
        self.assertEqual(PersonalGatewayAttempt.objects.get(pk=attempt.id).state, "interrupted")
        # A new request object represents a restarted worker, with no cached handles.
        response = self.invoke(key="interrupted-request")
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.invoke(key="fresh-request").status_code, 200)

    def test_request_id_validation_and_stale_source_prevent_dispatch(self):
        for key in ("x" * 65, "contains space", "秘密"):
            self.assertEqual(self.invoke(key=key).status_code, 400)
        request = self.request()
        request.request_id = "stale-source"
        candidate = gateway.resolve_deployment_candidates(request=request, tenant=self.installation.tenant, payload=self.payload())[0]
        type(candidate.provider_account).objects.filter(pk=candidate.provider_account_id).update(url="https://changed.invalid")
        with self.assertRaises(exceptions.APIException):
            lifecycle.reserve(request=request, tenant=self.installation.tenant, deployments=[candidate], provider_payload=self.payload())
        self.assertEqual(PersonalGatewayRequest.objects.count(), 0)
        self.assertEqual(self.calls, [])

    def test_client_closing_stream_leaves_an_interrupted_receipt(self):
        response = self.invoke(key="closed-stream", stream=True)
        chunks = iter(response.streaming_content)
        self.assertIn(b"hello", next(chunks))
        response.close()
        row = PersonalGatewayRequest.objects.get(request_id="closed-stream")
        self.assertEqual(row.state, "interrupted")
        self.assertEqual(row.attempts.get().state, "interrupted")
        log = GatewayRequestLog.objects.get(request_id="closed-stream")
        self.assertEqual(log.error_code, "GATEWAY_REQUEST_INTERRUPTED")
        self.assertEqual(log.router_id, self.router.pk)
        self.assertEqual(log.model, self.model)
        before = len(self.calls)
        retry = self.invoke(key="closed-stream", stream=True)
        self.assertIn(b"GATEWAY_REQUEST_REPLAY", b"".join(retry.streaming_content))
        self.assertEqual(len(self.calls), before)

    def test_stream_error_and_empty_completion_are_not_success(self):
        for mode, code in (("error", "PROVIDER_STREAM_FAILED"), ("empty", "PROVIDER_STREAM_INCOMPLETE")):
            type(self).gateway_stream_mode = mode
            response = self.invoke(key="stream-" + mode, stream=True)
            body = b"".join(response.streaming_content).decode()
            self.assertIn(code, body)
            self.assertNotIn("do-not-leak-stream-error", body)
            row = PersonalGatewayRequest.objects.get(request_id="stream-" + mode)
            self.assertEqual(row.state, "interrupted")
            self.assertIsNone(row.result_log_id)
            # These failures correctly mark their Source unhealthy. Restore
            # the fixture's known healthy Source for the next independent case.
            Deployment.objects.filter(pk=self.source_row.pk).update(health_status="healthy")
