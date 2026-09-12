"""Shared Execution/Aggregation HTTP guards; each edition supplies its own authority."""
from __future__ import annotations
import os
from django.core.files.uploadedfile import SimpleUploadedFile
from apps.routers.models import (Router, RouterChildBinding, RouterDeployment, RouterModelGroupBinding, RouterOutput, RouterVersion)


class RouterHTTPGuards:
    def test_create_router(self) -> None:
        self.authorize_router()

        response = self.router_request("post",
            "/api/v1/routers/",
            {"name": "Cost Router"},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 201)
        payload = self.router_payload(response)
        self.assert_router_success(response)
        self.assertEqual(payload["name"], "Cost Router")
        self.assertEqual(payload["status"], Router.STATUS_DRAFT)
        self.assertEqual(payload["router_type"], Router.TYPE_EXECUTION)

    def test_create_aggregation_router_has_a_separate_configuration_contract(self) -> None:
        group = self._create_model_group(name="chat", display_name="Chat")
        self.authorize_router()

        response = self.router_request("post",
            "/api/v1/routers/",
            {"name": "Downstream API", "router_type": Router.TYPE_AGGREGATION},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 201, response.content)
        router_id = self.router_payload(response)["id"]
        self.assertEqual(self.router_payload(response)["router_type"], Router.TYPE_AGGREGATION)
        local_pool = self.router_request("post",
            f"/api/v1/routers/{router_id}/model-groups/",
            {"model_group_ids": [str(group.id)]},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(local_pool.status_code, 400, local_pool.content)

    def test_update_router_name(self) -> None:
        router = self._create_router(name="Old Router")
        self.authorize_router()

        response = self.router_request("patch",
            f"/api/v1/routers/{router.id}/",
            {"name": "Renamed Router"},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 200, response.content)
        payload = self.router_payload(response)
        self.assert_router_success(response)
        self.assertEqual(payload["name"], "Renamed Router")
        router.refresh_from_db()
        self.assertEqual(router.name, "Renamed Router")

    def test_upload_router_py(self) -> None:
        router = self._create_router()
        self.authorize_router()

        response = self.router_request("post",
            f"/api/v1/routers/{router.id}/upload/",
            {
                "file": SimpleUploadedFile(
                    "router.py",
                    b"def route(request, candidates, context):\n    return {'ordered_pool_ids': [candidates[0]['id']]}\n",
                )
            },
            format="multipart",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 201)
        payload = self.router_payload(response)
        self.assertEqual(payload["version"], "v1")
        self.assertEqual(payload["file_name"], "router.py")
        self.assertTrue(RouterVersion.objects.filter(router=router, version="v1").exists())

    def test_upload_router_py_rejects_aggregation_router(self) -> None:
        router = self._create_router(router_type=Router.TYPE_AGGREGATION)
        self.authorize_router()

        response = self.router_request("post",
            f"/api/v1/routers/{router.id}/upload/",
            {
                "file": SimpleUploadedFile(
                    "router.py",
                    b"def route(request, candidates, context):\n    return {'ordered_pool_ids': []}\n",
                )
            },
            format="multipart",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("Execution Routers", response.json()["error"]["message"])
        self.assertFalse(RouterVersion.objects.filter(router=router).exists())

    def test_get_router_source_without_upload_returns_empty_state(self) -> None:
        router = self._create_router()
        self.authorize_router()

        response = self.router_request("get",
            f"/api/v1/routers/{router.id}/source/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 200, response.content)
        payload = self.router_payload(response)
        self.assertIs(payload["has_source"], False)
        self.assertEqual(payload["source_kind"], "none")
        self.assertEqual(payload["source"], "")

    def test_get_router_source_returns_deployed_router_py(self) -> None:
        router = self._create_router()
        source = "def route(request, candidates, context):\n    return {'ordered_pool_ids': [candidates[0]['id']], 'reason': 'deployed'}\n"
        self._upload_version(router, source=source)
        self.authorize_router()
        self.router_request("post",f"/api/v1/routers/{router.id}/deploy/", HTTP_X_NEXUS_TENANT=str(self.tenant.id))

        response = self.router_request("get",
            f"/api/v1/routers/{router.id}/source/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 200, response.content)
        payload = self.router_payload(response)
        self.assertIs(payload["has_source"], True)
        self.assertIs(payload["is_runtime_source"], True)
        self.assertEqual(payload["source_kind"], "deployed")
        self.assertEqual(payload["version"], "v1")
        self.assertEqual(payload["source"], source)

    def test_bind_model_group_ids(self) -> None:
        router = self._create_router()
        group = self._create_model_group(name="smart-chat", display_name="Smart Chat")
        self.authorize_router()

        response = self.router_request("post",
            f"/api/v1/routers/{router.id}/model-groups/",
            {"model_group_ids": [str(group.id)]},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 200, response.content)
        data = self.router_payload(response)
        self.assertEqual(data[0]["model_group_id"], str(group.id))
        self.assertEqual(data[0]["provider_name"], "*")
        self.assertEqual(data[0]["model_group_name"], "smart-chat")
        self.assertTrue(RouterModelGroupBinding.objects.filter(router=router, model_group=group, enabled=True).exists())
        output = RouterOutput.objects.get(router=router, model_group=group)
        self.assertEqual(output.model_name, "smart-chat")
        self.assertTrue(output.is_default)

    def test_aggregation_router_maps_stable_models_to_deployed_child_outputs(self) -> None:
        group = self._create_model_group(name="smart-chat", display_name="Smart Chat")
        self._make_model_group_available(group)
        child = self._create_router(name="Chat Router", strategy=Router.STRATEGY_MANUAL_PRIORITY)
        RouterModelGroupBinding.objects.create(
            router=child,
            model_group=group,
            provider_name="*",
            model_group_name=group.name,
            priority=1,
        )
        output = RouterOutput.objects.create(
            router=child,
            model_group=group,
            model_name="chat-upstream",
            is_default=True,
        )
        child.status = Router.STATUS_DEPLOYED
        child.save(update_fields=["status", "updated_at"])
        parent = self._create_router(
            name="Computer API Router",
            strategy=Router.STRATEGY_MANUAL_PRIORITY,
            router_type=Router.TYPE_AGGREGATION,
        )
        self.authorize_router()

        created = self.router_request("post",
            f"/api/v1/routers/{parent.id}/child-bindings/",
            {
                "exposed_model_name": "chat",
                "child_output_id": str(output.id),
                "priority": 1,
            },
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(created.status_code, 201, created.content)
        binding = RouterChildBinding.objects.get(router=parent)
        self.assertEqual(binding.exposed_model_name, "chat")
        detail = self.router_request("get",
            f"/api/v1/routers/{parent.id}/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(self.router_payload(detail)["router_type"], "aggregation")
        self.assertEqual(self.router_payload(detail)["output_models"], ["chat"])

        deployed = self.router_request("post",
            f"/api/v1/routers/{parent.id}/deploy/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(deployed.status_code, 201, deployed.content)

    def test_aggregation_candidates_include_available_and_repairable_execution_models(self) -> None:
        available_group = self._create_model_group(name="gpt-5.6-terra", display_name="GPT-5.6 Terra")
        unavailable_group = self._create_model_group(name="gpt-4", display_name="GPT-4")
        self._make_model_group_available(available_group)
        child = self._create_router(name="q")
        child.status = Router.STATUS_DEPLOYED
        child.save(update_fields=["status", "updated_at"])
        available_output = RouterOutput.objects.create(
            router=child,
            model_group=available_group,
            model_name="gpt-5.6-terra",
            is_default=True,
        )
        unavailable_output = RouterOutput.objects.create(
            router=child,
            model_group=unavailable_group,
            model_name="gpt-4",
        )
        non_default_available_output = RouterOutput.objects.create(
            router=child,
            model_group=available_group,
            model_name="terra-shadow",
        )
        draft_child = self._create_router(name="draft-execution")
        RouterOutput.objects.create(
            router=draft_child,
            model_group=available_group,
            model_name="draft-model",
        )
        unresolved_child = self._create_router(name="legacy-execution")
        RouterModelGroupBinding.objects.create(
            router=unresolved_child,
            model_group=None,
            provider_name="legacy-provider",
            model_group_name="legacy-model",
            priority=1,
        )
        unresolved_child.status = Router.STATUS_DEPLOYED
        unresolved_child.save(update_fields=["status", "updated_at"])
        second_child = self._create_router(name="second-execution")
        second_child.status = Router.STATUS_DEPLOYED
        second_child.save(update_fields=["status", "updated_at"])
        second_output = RouterOutput.objects.create(
            router=second_child,
            model_group=available_group,
            model_name="second-model",
            is_default=True,
        )
        offline_child = self._create_router(name="offline-execution")
        offline_child.status = Router.STATUS_DEPLOYED
        offline_child.save(update_fields=["status", "updated_at"])
        offline_output = RouterOutput.objects.create(
            router=offline_child,
            model_group=unavailable_group,
            model_name="offline-model",
            is_default=True,
        )
        repair_child = self._create_router(name="repair-execution")
        repair_child.status = Router.STATUS_DEPLOYED
        repair_child.save(update_fields=["status", "updated_at"])
        RouterOutput.objects.create(
            router=repair_child,
            model_group=unavailable_group,
            model_name="offline-default",
            is_default=True,
        )
        RouterOutput.objects.create(
            router=repair_child,
            model_group=available_group,
            model_name="healthy-alternative",
        )
        parent = self._create_router(name="catalog", router_type=Router.TYPE_AGGREGATION)
        self.authorize_router()

        response = self.router_request("get",
            f"/api/v1/routers/{parent.id}/aggregation-candidates/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 200, response.content)
        candidates = {row["name"]: row for row in self.router_payload(response)}
        self.assertIs(candidates["q"]["available"], True)
        self.assertEqual(candidates["q"]["output_id"], str(available_output.id))
        self.assertEqual(candidates["q"]["model_name"], "gpt-5.6-terra")
        models = {row["model_name"]: row for row in candidates["q"]["models"]}
        self.assertIs(models["gpt-5.6-terra"]["available"], True)
        self.assertEqual(models["gpt-4"]["code"], "EXECUTION_MODEL_UNAVAILABLE")
        self.assertEqual(candidates["draft-execution"]["code"], "EXECUTION_ROUTER_NOT_DEPLOYED")
        self.assertEqual(candidates["legacy-execution"]["code"], "EXECUTION_OUTPUT_UNRESOLVED")
        self.assertEqual(candidates["offline-execution"]["code"], "EXECUTION_MODEL_UNAVAILABLE")
        self.assertEqual(candidates["repair-execution"]["code"], "EXECUTION_MODEL_UNAVAILABLE")
        self.assertIn("Move healthy-alternative to first position", candidates["repair-execution"]["message"])

        rejected = self.router_request("post",
            f"/api/v1/routers/{parent.id}/child-bindings/",
            {"exposed_model_name": "gpt-4", "child_output_id": str(offline_output.id)},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(rejected.status_code, 400, rejected.content)

        forged_output = self.router_request("post",
            f"/api/v1/routers/{parent.id}/child-bindings/",
            {"exposed_model_name": "shadow", "child_output_id": str(non_default_available_output.id)},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(forged_output.status_code, 400, forged_output.content)
        self.assertIn("single default model output", forged_output.json()["error"]["message"])

        created = self.router_request("post",
            f"/api/v1/routers/{parent.id}/child-bindings/",
            {"exposed_model_name": "gpt-5.6-terra", "child_output_id": str(available_output.id)},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(created.status_code, 201, created.content)
        duplicate_model_name = self.router_request("post",
            f"/api/v1/routers/{parent.id}/child-bindings/",
            {"exposed_model_name": "gpt-5.6-terra", "child_output_id": str(second_output.id)},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(duplicate_model_name.status_code, 400, duplicate_model_name.content)
        self.assertIn("model name is already mapped", duplicate_model_name.json()["error"]["message"])
        duplicate_execution_router = self.router_request("post",
            f"/api/v1/routers/{parent.id}/child-bindings/",
            {"exposed_model_name": "terra-alias", "child_output_id": str(available_output.id)},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(duplicate_execution_router.status_code, 400, duplicate_execution_router.content)
        self.assertIn("Execution Router is already mapped", duplicate_execution_router.json()["error"]["message"])
        second_created = self.router_request("post",
            f"/api/v1/routers/{parent.id}/child-bindings/",
            {"exposed_model_name": "second-model", "child_output_id": str(second_output.id)},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(second_created.status_code, 201, second_created.content)
        duplicate_update = self.router_request("patch",
            f"/api/v1/routers/{parent.id}/child-bindings/{self.router_payload(second_created)['id']}/",
            {"exposed_model_name": "gpt-5.6-terra"},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(duplicate_update.status_code, 400, duplicate_update.content)
        self.assertIn("model name is already mapped", duplicate_update.json()["error"]["message"])
        refreshed = self.router_request("get",
            f"/api/v1/routers/{parent.id}/aggregation-candidates/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        refreshed_models = {
            row["model_name"]: row
            for row in next(item for item in self.router_payload(refreshed) if item["name"] == "q")["models"]
        }
        self.assertIs(refreshed_models["gpt-5.6-terra"]["already_bound"], True)
        refreshed_router = next(item for item in self.router_payload(refreshed) if item["name"] == "q")
        self.assertIs(refreshed_router["available"], False)
        self.assertEqual(refreshed_router["code"], "EXECUTION_ROUTER_ALREADY_MAPPED")
        self.assertEqual(refreshed_router["bound_model_name"], "gpt-5.6-terra")

    def test_aggregation_deploy_rejects_legacy_non_one_to_one_mappings(self) -> None:
        group = self._create_model_group(name="legacy-chat", display_name="Legacy Chat")
        self._make_model_group_available(group)
        first = self._create_router(name="Legacy First")
        second = self._create_router(name="Legacy Second")
        for child in (first, second):
            child.status = Router.STATUS_DEPLOYED
            child.save(update_fields=["status", "updated_at"])
        first_output = RouterOutput.objects.create(
            router=first,
            model_group=group,
            model_name="first-chat",
            is_default=True,
        )
        second_output = RouterOutput.objects.create(
            router=second,
            model_group=group,
            model_name="second-chat",
            is_default=True,
        )
        parent = self._create_router(name="Legacy Aggregation", router_type=Router.TYPE_AGGREGATION)
        RouterChildBinding.objects.create(
            router=parent,
            exposed_model_name="chat",
            child_output=first_output,
            priority=1,
        )
        RouterChildBinding.objects.create(
            router=parent,
            exposed_model_name="chat",
            child_output=second_output,
            priority=2,
        )
        self.authorize_router()

        response = self.router_request("post",
            f"/api/v1/routers/{parent.id}/deploy/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("exactly one Execution Router", response.json()["error"]["message"])
        parent.refresh_from_db()
        self.assertEqual(parent.status, Router.STATUS_DRAFT)

    def test_aggregation_router_rejects_local_pools_and_nested_children(self) -> None:
        group = self._create_model_group(name="code", display_name="Code")
        leaf = self._create_router(name="Leaf")
        leaf.status = Router.STATUS_DEPLOYED
        leaf.save(update_fields=["status", "updated_at"])
        leaf_output = RouterOutput.objects.create(router=leaf, model_group=group, model_name="code")
        parent = self._create_router(name="Parent", router_type=Router.TYPE_AGGREGATION)
        RouterChildBinding.objects.create(router=parent, exposed_model_name="code", child_output=leaf_output)
        parent.status = Router.STATUS_DEPLOYED
        parent.save(update_fields=["status", "updated_at"])
        parent_output = RouterOutput.objects.create(router=parent, model_group=group, model_name="invalid-nested-output")
        grandparent = self._create_router(name="Grandparent", router_type=Router.TYPE_AGGREGATION)
        self.authorize_router()

        local_pool = self.router_request("post",
            f"/api/v1/routers/{parent.id}/model-groups/",
            {"model_group_ids": [str(group.id)]},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(local_pool.status_code, 400, local_pool.content)

        nested = self.router_request("post",
            f"/api/v1/routers/{grandparent.id}/child-bindings/",
            {"exposed_model_name": "code", "child_output_id": str(parent_output.id)},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        self.assertEqual(nested.status_code, 400, nested.content)

    def test_create_router_with_initial_policy_and_pool(self) -> None:
        group = self._create_model_group(name="balanced-chat", display_name="Balanced Chat")
        self.authorize_router()

        response = self.router_request("post",
            "/api/v1/routers/",
            {
                "name": "Balanced Router",
                "strategy": Router.STRATEGY_LOWEST_LATENCY_POOL,
                "model_group_ids": [str(group.id)],
            },
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.router_payload(response)["strategy"], Router.STRATEGY_LOWEST_LATENCY_POOL)
        self.assertEqual(self.router_payload(response)["model_group_ids"], [str(group.id)])

    def test_update_router_applies_name_policy_and_pool_order_together(self) -> None:
        router = self._create_router(name="Draft Router")
        first = self._create_model_group(name="first-pool", display_name="First Pool")
        second = self._create_model_group(name="second-pool", display_name="Second Pool")
        self.authorize_router()

        response = self.router_request("patch",
            f"/api/v1/routers/{router.id}/",
            {
                "name": "Production Router",
                "strategy": Router.STRATEGY_BEST_HEALTH_POOL,
                "model_group_ids": [str(second.id), str(first.id)],
            },
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.router_payload(response)["name"], "Production Router")
        self.assertEqual(self.router_payload(response)["strategy"], Router.STRATEGY_BEST_HEALTH_POOL)
        self.assertEqual(self.router_payload(response)["model_group_ids"], [str(second.id), str(first.id)])

    def test_deploy_builtin_policy_without_router_py(self) -> None:
        router = self._create_router(strategy=Router.STRATEGY_MANUAL_PRIORITY)
        group = self._create_model_group(name="builtin-pool", display_name="Built-in Pool")
        RouterModelGroupBinding.objects.create(
            router=router,
            model_group=group,
            provider_name="*",
            model_group_name=group.name,
            priority=1,
        )
        self.authorize_router()

        response = self.router_request("post",
            f"/api/v1/routers/{router.id}/deploy/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertIsNone(self.router_payload(response)["version"])
        router.refresh_from_db()
        self.assertEqual(router.status, Router.STATUS_DEPLOYED)
        self.assertTrue(RouterOutput.objects.filter(router=router, model_group=group, enabled=True).exists())

    def test_archive_router_disables_runtime_and_hides_router(self) -> None:
        router = self._create_router()
        RouterDeployment.objects.create(router=router, status=RouterDeployment.STATUS_ACTIVE)
        self.authorize_router()

        response = self.router_request("delete",
            f"/api/v1/routers/{router.id}/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 204, response.content)
        router.refresh_from_db()
        self.assertEqual(router.status, Router.STATUS_DELETED)
        self.assertFalse(
            RouterDeployment.objects.filter(router=router, status=RouterDeployment.STATUS_ACTIVE).exists()
        )

    def test_set_cost_policy(self) -> None:
        router = self._create_router(strategy=Router.STRATEGY_QUALITY)
        self.authorize_router()

        response = self.router_request("post",
            f"/api/v1/routers/{router.id}/policy/",
            {"strategy": "cost"},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.router_payload(response)["strategy"], Router.STRATEGY_COST)

    def test_upload_router_py_requires_route_contract(self) -> None:
        router = self._create_router()
        self.authorize_router()

        response = self.router_request("post",
            f"/api/v1/routers/{router.id}/upload/",
            {"file": SimpleUploadedFile("router.py", b"def route(request):\n    return request\n")},
            format="multipart",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_set_custom_policy_requires_deployed_router_file(self) -> None:
        router = self._create_router()
        self.authorize_router()

        response = self.router_request("post",
            f"/api/v1/routers/{router.id}/policy/",
            {"strategy": "custom"},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_set_custom_policy_after_deploy(self) -> None:
        router = self._create_router()
        self._upload_version(router)
        self.authorize_router()
        self.router_request("post",f"/api/v1/routers/{router.id}/deploy/", HTTP_X_NEXUS_TENANT=str(self.tenant.id))

        response = self.router_request("post",
            f"/api/v1/routers/{router.id}/policy/",
            {"strategy": "custom"},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.router_payload(response)["strategy"], Router.STRATEGY_CUSTOM)

    def test_deploy_router(self) -> None:
        router = self._create_router()
        self._upload_version(router)
        self.authorize_router()

        response = self.router_request("post",
            f"/api/v1/routers/{router.id}/deploy/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.router_payload(response)["status"], RouterDeployment.STATUS_ACTIVE)
        router.refresh_from_db()
        self.assertEqual(router.status, Router.STATUS_DEPLOYED)


class RouterFileFixture:
    def _upload_version(self, router: Router, source: str | None = None) -> RouterVersion:
        source_text = source or "def route(request, candidates, context):\n    return {'ordered_pool_ids': [candidates[0]['id']]}\n"
        path = f"{self.tenant.id}/{router.id}/v1_router.py"
        storage_path = f"{self.temp_dir.name}/{path}"
        storage_dir = storage_path.rsplit("/", 1)[0]

        os.makedirs(storage_dir, exist_ok=True)
        with open(storage_path, "wb") as handle:
            handle.write(source_text.encode("utf-8"))
        router.current_version = "v1"
        router.save(update_fields=["current_version"])
        return RouterVersion.objects.create(
            router=router,
            version="v1",
            router_file_path=path,
            file_name="router.py",
            file_size=len(source_text.encode("utf-8")),
            created_by=self.owner,
        )
