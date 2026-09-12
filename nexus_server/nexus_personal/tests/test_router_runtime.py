"""Uploaded Python policies run through actual HTTP dispatch, without private apps.

The existing local development runner executes the real uploaded Python. These
tests are not a claim of Docker/nsjail sandbox or production process isolation.
"""
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework import exceptions
from apps.audit.models import AuditLog
from apps.deployments.models import Deployment
from apps.routers.models import Router, RouterVersion, RouterRuntimeInvocation
from apps.routers.runtime_services import log_runtime_invocation
from apps.routers.runtime_runner import RouterRuntimeResult
from nexus_personal.models import PersonalGatewayRequest
from .provider_http_fixture import ProviderHTTPFixture
from . import test_gateway_dispatch as dispatch


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1", NEXUS_ROUTER_RUNTIME_RUNNER="local")
class PersonalRouterRuntimeTests(ProviderHTTPFixture, TestCase):
    source = dispatch.PersonalGatewayDispatchTests.source
    post = dispatch.PersonalGatewayDispatchTests.post
    runtime = dispatch.PersonalGatewayDispatchTests.runtime
    payload = dispatch.PersonalGatewayDispatchTests.payload
    invoke = dispatch.PersonalGatewayDispatchTests.invoke

    def setUp(self):
        # Reuse the authenticated Provider -> Pool -> Router setup without
        # inheriting and rerunning the unrelated dispatch test methods.
        ProviderHTTPFixture.setUp(self)
        from rest_framework.test import APIClient
        from apps.deployments.models import ModelGroup
        from apps.routers import services
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)
        self.client.get("/api/v1/public/bootstrap/")
        self.headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value, "HTTP_ORIGIN": "http://testserver"}
        self.source_row = Deployment.objects.get(pk=self.source()["id"])
        self.group = ModelGroup.objects.get()
        self.router = services.create_router(request=self.request(), name="custom-dispatch", model_group_ids=[self.group.pk])
        services.deploy_router(request=self.request(), router_id=str(self.router.pk))
        self.model = self.router.outputs.get().model_name
        self.path = "/api/v1/openai/v1/chat/completions"
        folder = TemporaryDirectory(prefix="personal-router-policy-")
        self.addCleanup(folder.cleanup)
        setting = override_settings(NEXUS_ROUTER_STORAGE_ROOT=folder.name)
        setting.enable()
        self.addCleanup(setting.disable)
        type(self).calls.clear()

    def upload(self, source):
        base = f"/api/v1/routers/{self.router.pk}/"
        response = self.client.post(base + "upload/", {"file": SimpleUploadedFile("router.py", source.encode())},
                                    format="multipart", **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        version = RouterVersion.objects.get(pk=response.data["id"])
        self.assertEqual(self.post(base + "deploy/").status_code, 201)
        self.assertEqual(self.post(base + "policy/", {"strategy": "custom"}).status_code, 200)
        return version

    def good_policy(self):
        return self.upload('def route(request, candidates, context):\n'
            '    assert request["messages"][0]["content"] == "do-not-persist-this-prompt"\n'
            '    assert context["api_key_id"] == ""\n'
            '    return {"ordered_pool_ids": [candidates[0]["id"]], "reason": "actual-policy"}\n')

    def test_uploaded_policy_success_persists_operational_log_and_calls_upstream(self):
        version = self.good_policy()
        response = self.invoke(key="custom-success")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["choices"][0]["message"]["content"], "healthy")
        receipt = RouterRuntimeInvocation.objects.get()
        self.assertEqual(receipt.status, "success")
        self.assertEqual(receipt.version_id, version.pk)
        self.assertEqual(receipt.selected_deployment_id, self.source_row.pk)
        self.assertEqual(receipt.actor_id, self.installation.owner.pk)
        self.assertEqual(receipt.request_id, "custom-success")
        self.assertNotIn("api_key", {f.name for f in receipt._meta.fields})
        self.assertEqual(PersonalGatewayRequest.objects.get().state, "completed")
        self.assertEqual(self.calls, [("POST", "/v1/chat/completions", "personal-model")])
        self.assertEqual(AuditLog.objects.filter(action="routers.runtime.invoke", request_id="custom-success").count(), 1)

    def test_invalid_policy_does_not_fallback_or_invoke_upstream(self):
        self.upload('def route(request, candidates, context):\n    return {"pool_id": "not-an-admitted-pool"}\n')
        response = self.invoke(key="invalid-policy")
        self.assertEqual(response.status_code, 400, response.data)
        receipt = RouterRuntimeInvocation.objects.get()
        self.assertEqual(receipt.status, "failed")
        self.assertEqual(receipt.error_code, "ROUTER_RUNTIME_INVALID_DECISION")
        self.assertIsNone(receipt.selected_deployment_id)
        self.assertFalse(PersonalGatewayRequest.objects.exists())
        self.assertEqual(self.calls, [])

    def test_python_exception_and_disabled_runner_are_real_failures_not_success(self):
        self.upload('def route(request, candidates, context):\n    raise ValueError(request["messages"][0]["content"])\n')
        self.assertEqual(self.invoke(key="policy-raised").status_code, 400)
        self.assertEqual(RouterRuntimeInvocation.objects.get().error_code, "VALUEERROR")
        persisted = str(list(RouterRuntimeInvocation.objects.values())) + str(list(AuditLog.objects.filter(
            action="routers.runtime.invoke").values()))
        self.assertNotIn("do-not-persist-this-prompt", persisted)
        with override_settings(NEXUS_ROUTER_RUNTIME_RUNNER="disabled"):
            self.assertEqual(self.invoke(key="policy-disabled").status_code, 400)
        self.assertEqual(RouterRuntimeInvocation.objects.get(request_id="policy-disabled").error_code, "ROUTER_RUNTIME_DISABLED")
        self.assertEqual(self.calls, [])

    def test_forged_and_stale_log_context_cannot_create_receipts(self):
        version = self.good_policy()
        kwargs = dict(request=self.request(), tenant=self.installation.tenant, router=self.router,
            version=version, selected_deployment=self.source_row, result=RouterRuntimeResult({}, 1, 0),
            status_value="success", error_code="", error_message="")
        with self.assertRaises(exceptions.APIException):
            log_runtime_invocation(**{**kwargs, "request": self.request(self.other)})
        for model, row in ((Router, self.router), (Deployment, self.source_row)):
            model.objects.filter(pk=row.pk).update(created_by=self.other)
            with self.assertRaises(exceptions.APIException):
                log_runtime_invocation(**kwargs)
            model.objects.filter(pk=row.pk).update(created_by=self.installation.owner)
        RouterVersion.objects.filter(pk=version.pk).update(status="deleted")
        with self.assertRaises(exceptions.APIException):
            log_runtime_invocation(**kwargs)
        self.assertFalse(RouterRuntimeInvocation.objects.exists())
        self.assertEqual(self.calls, [])

    def test_audit_failure_rolls_back_receipt(self):
        version = self.good_policy()
        with patch("nexus_personal.router_runtime.write_audit_log", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaisesMessage(RuntimeError, "audit unavailable"):
                log_runtime_invocation(request=self.request(), tenant=self.installation.tenant, router=self.router,
                    version=version, selected_deployment=self.source_row, result=RouterRuntimeResult({}, 1, 0),
                    status_value="success", error_code="", error_message="")
        self.assertFalse(RouterRuntimeInvocation.objects.exists())
