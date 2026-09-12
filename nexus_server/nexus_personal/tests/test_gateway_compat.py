"""Real owner-scoped catalogs and compatibility requests over the existing HTTP fixture."""
from asgiref.sync import async_to_sync
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from apps.deployments.models import Deployment, ModelGroup
from apps.routers import services as routers
from apps.routers.models import Router
from nexus_personal.models import PersonalGatewayRequest
from .provider_http_fixture import ProviderHTTPFixture
from . import test_router_runtime as runtime_tests


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalGatewayCompatibilityTests(ProviderHTTPFixture, TestCase):
    source = runtime_tests.PersonalRouterRuntimeTests.source
    post = runtime_tests.PersonalRouterRuntimeTests.post
    runtime = runtime_tests.PersonalRouterRuntimeTests.runtime
    setUp = runtime_tests.PersonalRouterRuntimeTests.setUp

    def catalog(self, family="openai", **params):
        return self.client.get(f"/api/v1/{family}/v1/models", params)

    def send(self, family, key, *, stream=False, model=None):
        common = {"model": model or self.model, "router_id": str(self.router.pk), "stream": stream}
        if family == "openai":
            path = "/api/v1/openai/v1/responses"
            body = {**common, "input": "compatibility-input"}
        else:
            path = "/api/v1/claude/v1/messages"
            body = {**common, "messages": [{"role": "user", "content": "compatibility-input"}], "max_tokens": 64}
        return self.client.post(path, body, format="json", HTTP_X_REQUEST_ID=key, **self.headers)

    def stream_body(self, response):
        if response.is_async:
            async def read():
                return b"".join([part async for part in response.streaming_content]).decode()
            return async_to_sync(read)()
        return b"".join(response.streaming_content).decode()

    def test_catalogs_only_return_current_owned_available_models(self):
        for family in ("openai", "claude"):
            response = self.catalog(family)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual([row["id"] for row in response.data["data"]], [self.group.name])
        ModelGroup.objects.filter(pk=self.group.pk).update(created_by=self.other)
        for family in ("openai", "claude"):
            self.assertEqual(self.catalog(family).data["data"], [])
        ModelGroup.objects.filter(pk=self.group.pk).update(created_by=self.installation.owner)
        Deployment.objects.filter(pk=self.source_row.pk).update(health_status="unhealthy")
        for family in ("openai", "claude"):
            self.assertEqual(self.catalog(family).data["data"], [])
        Deployment.objects.filter(pk=self.source_row.pk).update(health_status="healthy")
        self.assertEqual(len(self.catalog().data["data"]), 1)
        self.assertEqual(self.calls, [])
        self.assertNotIn("local-provider-test-key", str(self.catalog().data))
        self.assertNotIn("endpoint", str(self.catalog().data))

    def test_execution_and_aggregation_catalogs_use_real_outputs(self):
        aggregate = routers.create_router(request=self.request(), name="compat-aggregate", router_type="aggregation")
        routers.add_router_child_binding(request=self.request(), router_id=str(aggregate.pk), data={
            "child_output_id": self.router.outputs.get().pk, "exposed_model_name": "downstream-model"})
        routers.deploy_router(request=self.request(), router_id=str(aggregate.pk))
        for family in ("openai", "claude"):
            response = self.catalog(family, router_id=str(aggregate.pk))
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual([row["id"] for row in response.data["data"]], ["downstream-model"])
        Router.objects.filter(pk=self.router.pk).update(status="disabled")
        self.assertEqual(self.catalog(router_id=str(aggregate.pk)).data["data"], [])
        Router.objects.filter(pk=self.router.pk).update(status="deployed", created_by=self.other)
        self.assertEqual(self.catalog(router_id=str(aggregate.pk)).status_code, 404)
        self.assertEqual(self.calls, [])

    def test_responses_and_claude_nonstream_run_full_lifecycle(self):
        response = self.send("openai", "responses-complete")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["output_text"], "healthy")
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["usage"]["total_tokens"], 12)
        self.assertEqual(PersonalGatewayRequest.objects.get(request_id="responses-complete").result_log.operation, "responses")
        response = self.send("claude", "claude-complete")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["content"][0]["text"], "healthy")
        self.assertEqual(response.data["type"], "message")
        self.assertEqual(set(PersonalGatewayRequest.objects.values_list("state", flat=True)), {"completed"})
        self.assertEqual(self.calls, [("POST", "/v1/chat/completions", "personal-model")] * 2)

    def test_replay_is_refused_across_compatibility_surfaces(self):
        self.assertEqual(self.send("openai", "same-invocation").status_code, 200)
        self.assertEqual(self.send("claude", "same-invocation").status_code, 409)
        self.assertEqual(self.send("openai", "same-invocation").status_code, 409)
        self.assertEqual(len(self.calls), 1)

    def test_complete_streams_record_usage_and_emit_protocol_completion(self):
        for family, marker in (("openai", "response.completed"), ("claude", "message_stop")):
            response = self.send(family, family + "-stream", stream=True)
            body = self.stream_body(response)
            self.assertIn("hello ", body)
            self.assertIn("world", body)
            self.assertIn(marker, body)
            row = PersonalGatewayRequest.objects.get(request_id=family + "-stream")
            self.assertEqual(row.state, "completed")
            self.assertEqual(row.result_log.total_tokens, 12)
            self.assertEqual(row.result_log.operation, "responses" if family == "openai" else "chat.completions")

    def test_truncated_stream_and_replay_emit_failure_not_completion(self):
        type(self).gateway_stream_mode = "truncated"
        for family, error, completed in (("openai", "response.failed", "response.completed"), ("claude", "event: error", "message_stop")):
            Deployment.objects.filter(pk=self.source_row.pk).update(health_status="healthy")
            key = family + "-truncated"
            body = self.stream_body(self.send(family, key, stream=True))
            self.assertIn(error, body)
            self.assertNotIn(completed, body)
            self.assertEqual(PersonalGatewayRequest.objects.get(request_id=key).state, "interrupted")
            from apps.gateway.models import GatewayRequestLog
            self.assertEqual(GatewayRequestLog.objects.get(request_id=key).operation,
                             "responses" if family == "openai" else "chat.completions")
            Deployment.objects.filter(pk=self.source_row.pk).update(health_status="healthy")
            before = len(self.calls)
            replay = self.stream_body(self.send(family, key, stream=True))
            self.assertIn(error, replay)
            self.assertNotIn(completed, replay)
            self.assertEqual(len(self.calls), before)

    def test_authentication_context_and_csrf_are_not_bypassed(self):
        foreign = APIClient(enforce_csrf_checks=True)
        foreign.force_login(self.other)
        for family in ("openai", "claude"):
            self.assertIn(foreign.get(f"/api/v1/{family}/v1/models").status_code, (401, 403))
            self.assertEqual(self.client.get(f"/api/v1/{family}/v1/models", HTTP_X_NEXUS_PROJECT="wrong-project").status_code, 403)
        self.assertEqual(self.catalog(router_id="not-a-router").status_code, 404)
        without_csrf = APIClient(enforce_csrf_checks=True)
        without_csrf.force_login(self.installation.owner)
        self.assertEqual(without_csrf.post("/api/v1/openai/v1/responses", {"model": self.model, "input": "x"}, format="json").status_code, 403)
        self.client.logout()
        self.assertIn(self.catalog().status_code, (401, 403))
        self.assertEqual(self.calls, [])
