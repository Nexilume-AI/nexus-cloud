from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.core.management import call_command, CommandError
from django.utils import timezone
from io import StringIO
import os
import json
import hashlib
import time
from uuid import uuid4
from unittest import skipUnless
from django.core import signing
from rest_framework.test import APIClient

from apps.agents.models import Agent, AgentDisplayRun, AgentDisplayEvent, AgentLog, AgentMemoryItem, AgentRuntimeInvocation
from apps.tenancy.models import Tenant, Membership


class AgentObservabilityScaleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="obs-owner")
        self.tenant = Tenant.objects.create(name="Observability test", slug="observability-scale")
        Membership.objects.create(tenant=self.tenant, user=self.user, role="owner")
        self.agent = Agent.objects.create(tenant=self.tenant, created_by=self.user, name="obs-agent")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.headers = {"HTTP_X_NEXUS_TENANT": str(self.tenant.pk)}
        self.base = f"/api/v1/agents/{self.agent.pk}"
        # IAM bootstraps its built-in role catalog on first use. Measure steady
        # state reads, not that unrelated one-time initialization.
        self.client.get(self.base + "/display-runs/", **self.headers)

    def runs(self, count):
        return AgentDisplayRun.objects.bulk_create([
            AgentDisplayRun(agent=self.agent, tenant=self.tenant, title=f"Run {i}", write_token="test-only")
            for i in range(count)
        ])

    def test_runs_can_page_past_100_without_duplicates(self):
        self.runs(123)
        seen, cursor = [], ""
        while True:
            response = self.client.get(self.base + "/observability/runs/", {"limit": 50, "cursor": cursor}, **self.headers)
            self.assertEqual(response.status_code, 200, response.content)
            data = response.json()["data"]
            self.assertLessEqual(len(data["items"]), 50)
            seen.extend(item["id"] for item in data["items"])
            cursor = data["next_cursor"]
            if not cursor:
                break
        self.assertEqual(len(seen), 123)
        self.assertEqual(len(set(seen)), 123)

    def test_legacy_run_query_count_does_not_grow_per_row(self):
        self.runs(1)
        with CaptureQueriesContext(connection) as small:
            self.client.get(self.base + "/display-runs/", **self.headers)
        self.runs(30)
        with CaptureQueriesContext(connection) as large:
            response = self.client.get(self.base + "/display-runs/", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(large), len(small) + 4)

    def test_output_get_does_not_replay_or_write_events(self):
        run = self.runs(1)[0]
        AgentDisplayEvent.objects.create(tenant=self.tenant, agent=self.agent, run=run, seq=1, event_type="CUSTOM",
            payload_json={"type": "CUSTOM", "name": "nexus.file.created", "value": {"path": "result.txt"}})
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.base + f"/display-runs/{run.pk}/outputs/", **self.headers)
        self.assertEqual(response.status_code, 200)
        writes = [q["sql"] for q in queries if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        self.assertEqual(len(writes), 0, [q.split()[0:3] for q in writes])

    def test_invalid_cursor_and_limit_are_400(self):
        for params in ({"cursor": "forged"}, {"limit": "bad"}, {"limit": 501}):
            response = self.client.get(self.base + "/observability/runs/", params, **self.headers)
            self.assertEqual(response.status_code, 400)

    def test_filter_and_cursor_are_bound_to_query(self):
        runs = self.runs(4)
        AgentDisplayRun.objects.filter(pk__in=[r.pk for r in runs[:3]]).update(status="failed")
        url = self.base + "/observability/runs/"
        response = self.client.get(url, {"status": "failed", "limit": 2}, **self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(len(data["items"]), 2)
        self.assertTrue(all(r["status"] == "failed" for r in data["items"]))
        self.assertEqual(self.client.get(url, {"status": "running", "cursor": data["next_cursor"]}, **self.headers).status_code, 400)

    def test_memory_and_logs_are_bounded_summaries(self):
        AgentMemoryItem.objects.bulk_create([AgentMemoryItem(tenant=self.tenant, agent=self.agent,
            content_text="private memory body", content_json={"secret": True}) for _ in range(55)])
        AgentLog.objects.bulk_create([AgentLog(agent=self.agent, level="info", message="x" * 10000) for _ in range(55)])
        for kind in ("memory", "logs"):
            response = self.client.get(self.base + f"/observability/{kind}/", **self.headers)
            self.assertEqual(response.status_code, 200, response.content)
            data = response.json()["data"]
            self.assertEqual(len(data["items"]), 50)
            self.assertTrue(data["next_cursor"])
            self.assertNotIn("content_json", data["items"][0])
            self.assertNotIn("private memory body", response.content.decode())
            self.assertLess(len(response.content), 80000)

    def test_trace_is_paginated_without_private_payloads(self):
        run = self.runs(1)[0]
        AgentDisplayEvent.objects.bulk_create([AgentDisplayEvent(tenant=self.tenant, agent=self.agent,
            run=run, seq=i, event_type="CUSTOM", payload_json={"name": "nexus.shell", "value": {"secret": "caller-private"}})
            for i in range(1, 61)])
        url = self.base + f"/observability/runs/{run.pk}/events/"
        response = self.client.get(url, **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(len(data["items"]), 50)
        self.assertEqual(data["items"][0]["seq"], 60)
        self.assertNotIn("caller-private", response.content.decode())
        second = self.client.get(url, {"cursor": data["next_cursor"]}, **self.headers).json()["data"]
        self.assertEqual(len(second["items"]), 10)

    def test_cross_tenant_and_other_agent_run_denied(self):
        other = Tenant.objects.create(name="Other", slug="obs-other")
        response = self.client.get(self.base + "/observability/runs/", HTTP_X_NEXUS_TENANT=str(other.pk))
        self.assertIn(response.status_code, (403, 404))
        foreign_agent = Agent.objects.create(tenant=self.tenant, name="other-agent", created_by=self.user)
        run = AgentDisplayRun.objects.create(agent=foreign_agent, tenant=self.tenant, write_token="secret")
        response = self.client.get(self.base + f"/observability/runs/{run.pk}/events/", **self.headers)
        self.assertEqual(response.status_code, 404)

    def test_equal_timestamps_and_inserts_do_not_shift_cursor(self):
        rows = self.runs(65)
        AgentDisplayRun.objects.filter(agent=self.agent).update(created_at=timezone.now())
        url = self.base + "/observability/runs/"
        first = self.client.get(url, **self.headers).json()["data"]
        self.runs(1)
        second = self.client.get(url, {"cursor": first["next_cursor"]}, **self.headers).json()["data"]
        self.assertEqual({r["id"] for r in first["items"] + second["items"]}, {str(r.pk) for r in rows})
        self.assertEqual(len(second["items"]), 15)

    def test_summary_query_count_constant_and_no_secret_fields(self):
        self.runs(1)
        url = self.base + "/observability/runs/"
        with CaptureQueriesContext(connection) as small:
            self.client.get(url, **self.headers)
        self.runs(49)
        with CaptureQueriesContext(connection) as large:
            result = self.client.get(url, **self.headers)
        self.assertLessEqual(len(large), len(small) + 1)
        # Tenant/IAM validation has a fixed query budget of its own; the list
        # must add no per-row queries and must never COUNT/OFFSET history.
        self.assertFalse(any("OFFSET" in q["sql"] for q in large))
        self.assertNotIn("write_token", result.content.decode())
        self.assertNotIn("test-only", result.content.decode())
        self.assertNotIn("redaction_metadata", result.content.decode())

    def test_tool_filter_matches_latest_turn_not_historical_invocations(self):
        old_tool, current_tool, empty = self.runs(3)
        for run, tools in ((old_tool, ("rare", "common")), (current_tool, ("common", "rare"))):
            for turn, tool in enumerate(tools, 1):
                AgentRuntimeInvocation.objects.create(tenant=self.tenant, agent=self.agent,
                    display_run=run, turn_index=turn, tool_name=tool, status="success")
        response = self.client.get(self.base + "/observability/runs/", {"tool": "rare"}, **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["data"]["items"]
        self.assertEqual([row["id"] for row in rows], [str(current_tool.pk)])
        self.assertEqual(rows[0]["tool_name"], "rare")

    def test_cursor_rejected_for_other_resource_kind(self):
        self.runs(3)
        first = self.client.get(self.base + "/observability/runs/", {"limit": 1}, **self.headers).json()["data"]
        response = self.client.get(self.base + "/observability/memory/", {"cursor": first["next_cursor"]}, **self.headers)
        self.assertEqual(response.status_code, 400)

    def test_output_ingestion_materializes_once_and_get_remains_read_only(self):
        from apps.agents.services import append_display_event
        run = self.runs(1)[0]
        append_display_event(run=run, event_type="CUSTOM", payload={"type": "CUSTOM", "name": "nexus.file.created", "value": {"path": "result.txt"}})
        self.assertEqual(run.output_artifacts.count(), 1)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.base + f"/observability/runs/{run.pk}/outputs/", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["data"]["items"]), 1)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))

    def test_legacy_repair_is_explicit_bounded_and_rejects_invocations(self):
        run = self.runs(1)[0]
        run.status = "completed"
        run.save()
        AgentDisplayEvent.objects.create(tenant=self.tenant, agent=self.agent, run=run, seq=1, event_type="CUSTOM",
            payload_json={"type": "CUSTOM", "name": "nexus.file.created", "value": {"path": "result.txt"}})
        call_command("repair_agent_output_metadata", run=str(run.pk), stdout=StringIO())
        self.assertEqual(run.output_artifacts.count(), 0)
        call_command("repair_agent_output_metadata", run=str(run.pk), apply=True, stdout=StringIO())
        self.assertEqual(run.output_artifacts.count(), 1)
        run.run_kind = "invocation"
        run.save()
        with self.assertRaises(CommandError):
            call_command("repair_agent_output_metadata", run=str(run.pk), apply=True, stdout=StringIO())


@skipUnless(os.environ.get("NEXUS_OBSERVABILITY_VOLUME_TEST") == "1", "Opt-in isolated PostgreSQL volume test")
class AgentObservabilityVolumeTests(TestCase):
    """Reuse Django's isolated DB; all synthetic rows roll back after the test.

    No second API server, network Runner, or production data is involved.
    """
    def test_real_keyset_plans_at_volume(self):
        from apps.common.subjects import request_subject
        if connection.vendor != "postgresql" or connection.settings_dict["NAME"] != "test_nexus_agent_observability":
            self.fail("Volume fixture must run only in the authorized isolated PostgreSQL test database.")
        count = min(max(int(os.environ.get("NEXUS_OBSERVABILITY_VOLUME_RUNS", "100000")), 10000), 1000000)
        salt = uuid4().hex
        user = get_user_model().objects.create_user(username="obs-volume")
        tenant = Tenant.objects.create(name="Synthetic volume", slug="obs-volume")
        Membership.objects.create(tenant=tenant, user=user, role="owner")
        agent = Agent.objects.create(tenant=tenant, created_by=user, name="Synthetic volume")
        run_key = "md5(%s || g::text)::uuid"
        # Validate each batch, not a million deferred FK triggers in one
        # teardown statement. Constraints and query timeouts stay enabled.
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

        def seed(prototype, n, expressions):
            fields = [field for field in prototype._meta.local_fields if not field.auto_created]
            columns, values, params = [], [], []
            for field in fields:
                columns.append(connection.ops.quote_name(field.column))
                if field.attname in expressions:
                    expression, arguments = expressions[field.attname]
                    values.append(expression)
                    params.extend(arguments)
                elif getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
                    values.append("transaction_timestamp() - (%s - g) * interval '1 second'")
                    params.append(n)
                else:
                    values.append("%s")
                    params.append(field.get_db_prep_save(getattr(prototype, field.attname), connection))
            with connection.cursor() as cursor:
                for first in range(1, n + 1, 10000):
                    cursor.execute(f"INSERT INTO {connection.ops.quote_name(prototype._meta.db_table)} ({', '.join(columns)}) SELECT {', '.join(values)} FROM generate_series(%s, %s) AS g", [*params, first, min(first + 9999, n)])
                    if min(first + 9999, n) % 100000 == 0:
                        print(f"SYNTHETIC_ROWS {prototype._meta.model_name} {min(first + 9999, n)}/{n}", flush=True)

        start = time.perf_counter()
        seed(AgentDisplayRun(tenant=tenant, agent=agent, status="completed", write_token="synthetic", run_kind="invocation"), count, {
            "id": (run_key, [salt]), "title": ("'Synthetic run ' || g::text", []),
        })
        seed(AgentRuntimeInvocation(tenant=tenant, agent=agent, status="success"), count, {
            "id": (run_key, [salt + "inv"]), "display_run_id": (run_key, [salt]),
            "tool_name": ("CASE WHEN g <= 100 THEN 'rare_tool' ELSE 'common_tool' END", []),
        })
        # Ten events per Run plus a long, hot first Run; all sequences unique.
        seed(AgentDisplayEvent(tenant=tenant, agent=agent, event_type="RUN_STARTED", payload_json={}), count * 10, {
            "id": (run_key, [salt + "event"]), "seq": ("g", []),
            "run_id": ("md5(%s || (CASE WHEN g <= %s THEN 1 ELSE 2 + mod(g, %s - 1) END)::text)::uuid", [salt, count, count]),
        })
        with connection.cursor() as cursor:
            for table in ("agents_agentdisplayrun", "agents_agentruntimeinvocation", "agents_agentdisplayevent"):
                cursor.execute(f'ANALYZE "{table}"')
        client = APIClient()
        client.force_authenticate(user)
        headers = {"HTTP_X_NEXUS_TENANT": str(tenant.pk)}
        url = f"/api/v1/agents/{agent.pk}/observability/runs/"
        warm = client.get(url, **headers)
        self.assertEqual(warm.status_code, 200)
        anchor = AgentDisplayRun.objects.get(pk=hashlib.md5((salt + "100").encode()).hexdigest())
        subject = request_subject(warm.wsgi_request)
        context = [str(tenant.pk), str(agent.pk), "", "runs", subject.subject_hash]
        fingerprint = hashlib.sha256(json.dumps([context, {}], sort_keys=True).encode()).hexdigest()
        deep_cursor = signing.dumps({"scope": fingerprint, "position": anchor.created_at.isoformat(), "id": str(anchor.pk)}, salt="agent-observability-v1")
        hot_run_id = str(AgentDisplayRun.objects.get(pk=hashlib.md5((salt + "1").encode()).hexdigest()).pk)
        trace_url = f"/api/v1/agents/{agent.pk}/observability/runs/{hot_run_id}/events/"
        trace_context = [str(tenant.pk), str(agent.pk), hot_run_id, "events", subject.subject_hash]
        trace_scope = hashlib.sha256(json.dumps([trace_context, {}], sort_keys=True).encode()).hexdigest()
        trace_cursor = signing.dumps({"scope": trace_scope, "position": 100, "id": "unused-for-sequence"}, salt="agent-observability-v1")
        report = {"runs": count, "invocations": count, "events": count * 10, "seed_seconds": round(time.perf_counter() - start, 2), "queries": {}}

        def metrics(plan):
            nodes = [plan]
            removed = 0
            while nodes:
                node = nodes.pop()
                removed += (node.get("Rows Removed by Filter", 0) + node.get("Rows Removed by Join Filter", 0)) * node.get("Actual Loops", 1)
                nodes.extend(node.get("Plans", []))
            return removed

        for name, target, params in (("first", url, {}), ("deep", url, {"cursor": deep_cursor}),
                ("rare_tool", url, {"tool": "rare_tool"}), ("trace_first", trace_url, {}), ("trace_deep", trace_url, {"cursor": trace_cursor})):
            began = time.perf_counter()
            with CaptureQueriesContext(connection) as queries:
                response = client.get(target, params, **headers)
            http_ms = round((time.perf_counter() - began) * 1000, 2)
            self.assertEqual(response.status_code, 200, response.content)
            marker = 'FROM "agents_agentdisplayevent"' if name.startswith("trace_") else 'AS "_obs_latest_seq"'
            sql = next(q["sql"] for q in queries if marker in q["sql"])
            with connection.cursor() as cursor:
                cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql)
                plan = cursor.fetchone()[0][0]
            report["queries"][name] = {"http_ms": http_ms,
                "sql_ms": plan["Execution Time"], "removed_rows": metrics(plan["Plan"]), "sql_count": len(queries),
                "shared_hit_blocks": plan["Plan"].get("Shared Hit Blocks", 0), "returned": len(response.json()["data"]["items"])}
        print("OBSERVABILITY_VOLUME " + json.dumps(report), flush=True)
        self.assertLess(report["queries"]["deep"]["removed_rows"], 1000, "Deep keyset page still scans earlier history")
        self.assertLess(report["queries"]["rare_tool"]["removed_rows"], 1000, "Tool filter still scans unrelated Run history")
        for name in ("trace_first", "trace_deep"):
            self.assertLess(report["queries"][name]["removed_rows"], 1000, "Trace still scans unrelated events")
