"""Cascaded Routers over real authenticated HTTP and a real fixture upstream."""
from unittest.mock import patch
from rest_framework import exceptions
from rest_framework.test import APIClient
from apps.routers import services
from apps.routers.cascade import sync_outputs, validate_graph
from apps.routers.models import Router, RouterChildBinding
from apps.gateway import services as gateway
from apps.gateway.model_info import router_groups
from apps.gateway.models import GatewayRequestLog
from nexus_personal.models import PersonalGatewayRequest, PersonalGatewayAttempt
from . import test_gateway_routing as fixture


class RouterCascadeTests(fixture.PersonalGatewayRoutingTests):
    def parent(self, child=None, model="my-export", alias="nested"):
        child = child or self.aggregate
        parent = services.create_router(request=self.request(), name="parent", router_type="aggregation")
        services.add_router_child_binding(request=self.request(), router_id=str(parent.pk), data={
            "child_output_id": child.outputs.get(model_name=model).pk, "exposed_model_name": alias})
        services.deploy_router(request=self.request(), router_id=str(parent.pk))
        parent.refresh_from_db()
        return parent

    def route(self, router, name="nested", **payload):
        return gateway.resolve_router_routing(request=self.request(), tenant=self.installation.tenant,
            router_id=str(router.pk), payload={**self.payload(name), **payload})

    def client_for(self, router):
        response = self.post(f"/api/v1/routers/{router.pk}/api-access/", {"action": "ensure"})
        self.assertEqual(response.status_code, 200, response.data)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + response.data["credentials"]["gateway_api_key"])
        return client

    def test_three_and_four_levels_preserve_path_catalog_and_metadata(self):
        parent = self.parent()
        root = self.parent(parent, "nested", "outer")
        result = self.route(root, "outer")
        self.assertEqual([row["router_id"] for row in result.trace["path"]],
                         list(map(lambda r: str(r.pk), [root, parent, self.aggregate, self.execution])))
        self.assertEqual(result.deployments[0].pk, self.source_row.pk)
        self.assertEqual(router_groups(root, "outer"), [self.group])
        catalog = gateway.router_model_catalog(tenant=self.installation.tenant, router=root)
        self.assertTrue(catalog[0]["available"])
        self.assertEqual(self.calls, [])  # Resolution is not another inference request.
        with self.assertRaises(exceptions.ValidationError):
            self.parent(root, "outer", "fifth")

    def test_cycle_and_reenable_cycle_rejected_atomically(self):
        parent = self.parent()
        data = {"child_output_id": parent.outputs.get(model_name="nested").pk, "exposed_model_name": "loop"}
        with self.assertRaises(exceptions.ValidationError):
            services.add_router_child_binding(request=self.request(), router_id=str(self.aggregate.pk), data=data)
        disabled = RouterChildBinding.objects.create(router=self.aggregate, child_output_id=data["child_output_id"],
                                                     exposed_model_name="loop", enabled=False)
        with self.assertRaises(exceptions.ValidationError):
            services.update_router_child_binding(request=self.request(), router_id=str(self.aggregate.pk),
                binding_id=str(disabled.pk), data={"enabled": True})
        disabled.refresh_from_db()
        self.assertFalse(disabled.enabled)

    def test_runtime_guards_corrupt_cycle_and_metadata_returns_empty(self):
        parent = self.parent()
        binding = self.aggregate.child_bindings.get()
        binding.child_output = parent.outputs.get(model_name="nested")
        binding.save()
        with self.assertRaises(exceptions.APIException):
            self.route(parent)
        self.assertEqual(router_groups(parent, "nested"), [])
        self.assertFalse(gateway.router_model_catalog(tenant=self.installation.tenant, router=parent)[0]["available"])
        self.assertEqual(self.calls, [])

    def test_changed_subtree_checks_ancestor_depth(self):
        parent = self.parent()
        self.parent(parent, "nested", "outer")
        independent = self.parent(self.execution, self.output.model_name, "branch")
        with self.assertRaises(exceptions.ValidationError):
            # Replacing the bottom edge would lengthen already-existing parents.
            validate_graph(self.installation.tenant.pk, edge=(self.aggregate.pk, independent.pk))

    def test_each_level_permission_and_disable_rechecked(self):
        parent = self.parent()
        for router in (self.aggregate, self.execution):
            Router.objects.filter(pk=router.pk).update(created_by=self.other)
            with self.assertRaises(exceptions.APIException):
                self.route(parent)
            Router.objects.filter(pk=router.pk).update(created_by=self.installation.owner)
        binding = self.aggregate.child_bindings.get()
        services.update_router_child_binding(request=self.request(), router_id=str(self.aggregate.pk),
            binding_id=str(binding.pk), data={"enabled": False})
        with self.assertRaises(exceptions.APIException):
            self.route(parent)
        self.assertEqual(router_groups(parent, "nested"), [])
        self.assertEqual(self.calls, [])

    def test_auto_delegation_has_one_decision_owner_and_named_bypasses_parent(self):
        services.update_router(request=self.request(), router_id=str(self.aggregate.pk), data={"strategy": "task_type"})
        self.aggregate.refresh_from_db()
        parent = self.parent(self.aggregate, "auto", "delegate")
        services.add_router_child_binding(request=self.request(), router_id=str(parent.pk), data={
            "child_output_id": self.aggregate.outputs.get(model_name="my-export").pk, "exposed_model_name": "fixed"})
        services.update_router(request=self.request(), router_id=str(parent.pk), data={"strategy": "task_type"})
        parent.refresh_from_db()
        from apps.routers.aggregate_auto import rank
        with patch("apps.routers.aggregate_auto.rank", wraps=rank) as ranking, patch("apps.gateway.models_dev.snapshot", return_value={}):
            delegated = self.route(parent, "delegate")
            self.assertEqual(ranking.call_count, 1)
            self.assertEqual(ranking.call_args.args[1].pk, self.aggregate.pk)
            self.assertEqual(delegated.trace["child_router"]["router"]["decision"]["owner_router_id"], str(self.aggregate.pk))
            ranking.reset_mock()
            result = self.route(parent, "auto")
            self.assertEqual(ranking.call_count, 1)
            self.assertEqual(ranking.call_args.args[1].pk, parent.pk)
            self.assertEqual(result.trace["router"]["decision"]["candidate_models"], ["fixed"])
            ranking.reset_mock()
            self.route(parent, "fixed")
            ranking.assert_not_called()
        self.assertEqual(router_groups(parent, "auto"), [self.group])

    def test_exported_credential_actual_http_single_receipt_attempt_and_usage(self):
        parent = self.parent()
        client = self.client_for(parent)
        for stream in (False, True):
            before = (PersonalGatewayRequest.objects.count(), PersonalGatewayAttempt.objects.count(), GatewayRequestLog.objects.count())
            response = client.post("/api/v1/openai/v1/chat/completions", {
                "model": "nested", "messages": [{"role": "user", "content": "Hello"}], "stream": stream}, format="json")
            self.assertEqual(response.status_code, 200, getattr(response, "data", None))
            if stream:
                self.assertIn(b"[DONE]", b"".join(response.streaming_content))
            else:
                self.assertEqual(response.data["choices"][0]["message"]["content"], "healthy")
            after = (PersonalGatewayRequest.objects.count(), PersonalGatewayAttempt.objects.count(), GatewayRequestLog.objects.count())
            self.assertEqual(tuple(b-a for a,b in zip(before,after)), (1,1,1))
        self.assertEqual(len([call for call in self.calls if call[0] == "POST"]), 2)
        # The entry credential cannot directly invoke a child, even when cascades work.
        denied = client.post("/api/v1/openai/v1/chat/completions", {**self.payload(), "router_id": str(self.aggregate.pk)}, format="json")
        self.assertEqual(denied.status_code, 403)

    def test_delegated_native_decision_continuity_and_invalid_choice_fallback(self):
        from .test_system_one_routing import NativeSystemOneRoutingTests
        from apps.deployments.models import ModelGroup
        self.decision_provider = lambda: NativeSystemOneRoutingTests.decision_provider(self)
        NativeSystemOneRoutingTests.configure(self)
        other = services.create_router(request=self.request(), name="other", model_group_ids=[ModelGroup.objects.get(name="second-pool").pk])
        services.deploy_router(request=self.request(), router_id=str(other.pk))
        services.add_router_child_binding(request=self.request(), router_id=str(self.aggregate.pk), data={
            "child_output_id": other.outputs.get().pk, "exposed_model_name": "fast"})
        services.update_router(request=self.request(), router_id=str(self.aggregate.pk), data={
            "strategy": "system_one", "decision_config": self.execution.decision_config})
        parent = self.parent(self.aggregate, "auto", "delegate")
        client = self.client_for(parent)
        type(self).system_one_choice = "model_1"
        messages = [{"role": "user", "content": "Hello"}]
        def invoke():
            response = client.post("/api/v1/openai/v1/chat/completions", {
                "model": "delegate", "messages": messages}, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            return GatewayRequestLog.objects.latest("created_at").routing_trace["child_router"]["router"]["decision"]
        with patch("apps.gateway.models_dev.snapshot", return_value={}):
            self.assertEqual(invoke()["status"], "selected")
            self.assertEqual(len(self.system_one_payloads), 1)
            messages.append({"role": "tool", "tool_call_id": "fixture", "content": "done"})
            self.assertEqual(invoke()["status"], "retained")
            self.assertEqual(len(self.system_one_payloads), 1)
            messages.append({"role": "user", "content": "A new task"})
            type(self).system_one_choice = "not-an-authorized-model"
            self.assertEqual(invoke()["status"], "fallback")
            self.assertEqual(len(self.system_one_payloads), 2)
