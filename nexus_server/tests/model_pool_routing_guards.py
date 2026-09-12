"""Portable Model Pool routing assertions; each host supplies its real HTTP context."""
from apps.deployments.models import ModelGroupDeployment, ModelGroupRoutingRevision


class ModelPoolRoutingGuards:
    def test_update_model_group_and_source_routing(self) -> None:
        deployment = self.routing_deployment()
        group = self.routing_pool(deployment)
        link = ModelGroupDeployment.objects.get(model_group=group, deployment=deployment)
        self.authorize_routing()

        routing_response = self.routing_request("patch",
            f"/api/v1/models/{group.id}/routing/",
            {
                "expected_revision": group.routing_revision,
                "routing_strategy": "lowest_cost",
                "routing_config": {},
                "sources": [{"id": str(link.id), "enabled": True, "priority": 100, "weight": 100, "fallback_order": 100}],
            },
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        source_response = self.routing_request("patch",
            f"/api/v1/models/{group.id}/sources/{link.id}/",
            {"expected_revision": self.routing_payload(routing_response)["routing_revision"], "priority": 5, "weight": 20, "fallback_order": 3},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(routing_response.status_code, 200, routing_response.content)
        self.assertEqual(self.routing_payload(routing_response)["routing_strategy"], "lowest_cost")
        self.assertEqual(source_response.status_code, 200, source_response.content)
        link.refresh_from_db()
        group.refresh_from_db()
        self.assertEqual(group.routing_strategy, "lowest_cost")
        self.assertTrue(link.enabled)
        self.assertEqual(link.priority, 5)
        self.assertEqual(link.weight, 20)
        self.assertEqual(link.fallback_order, 3)

    def test_model_pool_policy_preview_revision_fence_and_history(self) -> None:
        deployment = self.routing_deployment()
        group = self.routing_pool(deployment)
        link = ModelGroupDeployment.objects.get(model_group=group, deployment=deployment)
        self.authorize_routing()
        draft = {
            "expected_revision": group.routing_revision,
            "routing_strategy": "weighted",
            "routing_config": {},
            "sources": [{"id": str(link.id), "enabled": True, "priority": 100, "weight": 25, "fallback_order": 1}],
        }

        preview = self.routing_request("post",
            f"/api/v1/models/{group.id}/routing/preview/",
            draft,
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        applied = self.routing_request("patch",
            f"/api/v1/models/{group.id}/routing/",
            draft,
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        stale = self.routing_request("patch",
            f"/api/v1/models/{group.id}/routing/",
            draft,
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        history = self.routing_request("get",
            f"/api/v1/models/{group.id}/routing/history/",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(self.routing_payload(preview)["candidates"][0]["traffic_share_percent"], 100.0)
        self.assertIn("SINGLE_SOURCE", [item["code"] for item in self.routing_payload(preview)["risks"]])
        self.assertEqual(applied.status_code, 200, applied.content)
        self.assertEqual(self.routing_payload(applied)["routing_revision"], group.routing_revision + 1)
        self.assertEqual(stale.status_code, 409, stale.content)
        self.assertEqual(history.status_code, 200, history.content)
        self.assertGreaterEqual(len(self.routing_payload(history)), 2)
        self.assertTrue(ModelGroupRoutingRevision.objects.filter(model_group=group, routing_strategy="weighted").exists())

    def test_model_pool_policy_rejects_zero_capacity_drafts(self) -> None:
        deployment = self.routing_deployment()
        group = self.routing_pool(deployment)
        link = ModelGroupDeployment.objects.get(model_group=group, deployment=deployment)
        self.authorize_routing()

        disabled = self.routing_request("post",
            f"/api/v1/models/{group.id}/routing/preview/",
            {"expected_revision": group.routing_revision, "routing_strategy": "fallback", "sources": [{"id": str(link.id), "enabled": False, "priority": 1, "weight": 100, "fallback_order": 1}]},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )
        zero_weight = self.routing_request("post",
            f"/api/v1/models/{group.id}/routing/preview/",
            {"expected_revision": group.routing_revision, "routing_strategy": "weighted", "sources": [{"id": str(link.id), "enabled": True, "priority": 1, "weight": 0, "fallback_order": 1}]},
            format="json",
            HTTP_X_NEXUS_TENANT=str(self.tenant.id),
        )

        self.assertEqual(disabled.status_code, 400, disabled.content)
        self.assertEqual(zero_weight.status_code, 400, zero_weight.content)
