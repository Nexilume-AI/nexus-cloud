"""Initial-install optimization preserves the entire pre-release schema contract."""
import json
from pathlib import Path
from uuid import uuid4

from django.apps import apps
from django.db import connection, IntegrityError, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations.models import AddConstraint, AddIndex, CreateModel
from django.db.migrations.state import ProjectState
from django.test import SimpleTestCase, TestCase
from django.utils import timezone


def original_migrations():
    result = {}
    labels = {"agent_migrations": "agents", "dataset_migrations": "datasets", "router_migrations": "routers",
              "deployment_migrations": "deployments", "provider_migrations": "providers",
              "metrics_migrations": "metrics", "migrations": "personal",
              "workspace_migrations": "workspaces", "mobile_migrations": "mobile", "job_migrations": "jobs"}
    for filename in ("agent_initial_migrations_before_optimization.json",
                     "catalog_initial_migrations_before_optimization.json",
                     "deployment_initial_migrations_before_optimization.json",
                     "operational_initial_migrations_before_optimization.json",
                     "personal_gateway_migration_before_optimization.json",
                     "device_initial_migrations_before_optimization.json",
                     "job_initial_migration_before_optimization.json"):
        path = Path(__file__).parent / "fixtures" / filename
        for name, source in json.loads(path.read_text(encoding="utf-8")).items():
            namespace = {"__name__": name[:-3].replace("/", ".")}
            exec(compile(source, name, "exec"), namespace)
            key = (labels[Path(name).parent.name], Path(name).stem)
            result[key] = namespace["Migration"](key[1], key[0])
    return result


class InitialMigrationStateTests(SimpleTestCase):
    def test_reduces_initial_operations_without_changing_dependency_graph(self):
        originals = original_migrations()
        loader = MigrationLoader(None)
        for app_label in ("agents", "datasets", "routers", "deployments", "providers", "metrics", "personal", "workspaces", "mobile", "jobs"):
            with self.subTest(app=app_label):
                selected = [key for key in originals if key[0] == app_label]
                self.assertLess(sum(len(loader.disk_migrations[key].operations) for key in selected),
                                sum(len(originals[key].operations) for key in selected))
        for key, original in originals.items():
            migration = loader.disk_migrations[key]
            self.assertEqual(migration.dependencies, original.dependencies)
            self.assertEqual(migration.initial, original.initial)

    def test_operational_initial_indexes_do_not_reload_existing_model_states(self):
        loader = MigrationLoader(None)
        for key in (("providers", "0001_personal_models"), ("metrics", "0001_initial"),
                    ("workspaces", "0001_personal_runtime"), ("mobile", "0001_personal_runtime"),
                    ("jobs", "0001_personal_jobs")):
            with self.subTest(migration=key):
                self.assertFalse(any(isinstance(operation, (AddIndex, AddConstraint))
                                     for operation in loader.disk_migrations[key].operations))

    def test_dependency_ready_agent_and_request_indexes_are_part_of_table_creation(self):
        loader = MigrationLoader(None)
        for key in (("agents", "0001_personal_runtime"), ("personal", "0005_gateway_requests")):
            operations = loader.disk_migrations[key].operations
            fields = {operation.name.lower(): {name for name, _ in operation.fields}
                      for operation in operations if isinstance(operation, CreateModel)}
            delayed = set()
            for operation in operations:
                if isinstance(operation, (AddIndex, AddConstraint)):
                    item = operation.index if isinstance(operation, AddIndex) else operation.constraint
                    ready = all(name.lstrip("-") in fields[operation.model_name] for name in item.fields)
                    category = (operation.model_name, type(operation))
                    # Preserve exact option order after a dependency-delayed
                    # predecessor, even if this later index could be created.
                    self.assertFalse(ready and category not in delayed, (key, item.name))
                    delayed.add(category)

    def test_all_final_model_fields_indexes_constraints_and_options_are_identical(self):
        loader = MigrationLoader(None)
        plan = []
        for node in loader.graph.leaf_nodes():
            for key in loader.graph.forwards_plan(node):
                if key not in plan:
                    plan.append(key)
        originals = original_migrations()
        before, after = ProjectState(), ProjectState()
        for key in plan:
            migration = loader.disk_migrations[key]
            before = originals.get(key, migration).mutate_state(before, preserve=False)
            after = migration.mutate_state(after, preserve=False)
        self.assertEqual(before.models, after.models)
        self.assertEqual(before.real_apps, after.real_apps)
        self.assertEqual({m._meta.label for m in before.apps.get_models()},
                         {m._meta.label for m in after.apps.get_models()})


class InitialMigrationDatabaseTests(TestCase):
    def test_operational_unique_and_conditional_constraints_reject_real_duplicates(self):
        from apps.deployments.models import CanonicalModel
        from apps.metrics.models import AlertEvent, AlertRule, MonitoringHeartbeat
        from apps.providers.models import (
            Provider, ProviderAccount, ProviderImportBatch, ProviderRuntimeAccount, ProviderRuntimeModelOffer,
        )
        from nexus_personal.services import provision_owner
        from .test_installation import PASSWORD

        owner = provision_owner(email='migration-owner@example.test', password=PASSWORD)

        def rejected(model, **values):
            with self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.create(**values)

        account_values = {'tenant': owner.tenant, 'provider': Provider.objects.create(name='migration-provider'),
                          'account_id': 'account'}
        account = ProviderAccount.objects.create(**account_values)
        rejected(ProviderAccount, **account_values)
        batch_values = {'tenant': owner.tenant, 'owner_subject_hash': 'owner', 'request_key': uuid4(),
                        'expires_at': timezone.now()}
        ProviderImportBatch.objects.create(**batch_values)
        rejected(ProviderImportBatch, **batch_values)

        runtime_values = {'tenant': owner.tenant, 'name': 'runtime', 'source_provider_account': account,
                          'runtime_type': 'direct_api', 'status': 'active'}
        runtime = ProviderRuntimeAccount.objects.create(**runtime_values)
        rejected(ProviderRuntimeAccount, **runtime_values)
        runtime.status = 'deleted'
        runtime.save(update_fields=['status'])
        active = ProviderRuntimeAccount.objects.create(**runtime_values)
        canonical = CanonicalModel.objects.create(key='migration-model', display_name='Migration model')
        offer_values = {'runtime_account': active, 'upstream_model_id': 'upstream',
                        'canonical_model': canonical, 'status': 'confirmed'}
        ProviderRuntimeModelOffer.objects.create(**offer_values)
        rejected(ProviderRuntimeModelOffer, **{**offer_values, 'upstream_model_id': 'different'})
        # A detected mapping is outside the conditional canonical constraint,
        # but upstream identity is unique regardless of status.
        ProviderRuntimeModelOffer.objects.create(**{**offer_values, 'upstream_model_id': 'detected', 'status': 'detected'})
        rejected(ProviderRuntimeModelOffer, **{**offer_values, 'status': 'detected'})

        rule = AlertRule.objects.create(tenant=owner.tenant, metric='system.quality.error_rate', threshold='1')
        event_values = {'tenant': owner.tenant, 'rule': rule, 'metric': rule.metric,
                        'value': 2, 'triggered_at': timezone.now(), 'status': 'firing', 'is_test': False}
        AlertEvent.objects.create(**event_values)
        rejected(AlertEvent, **event_values)
        AlertEvent.objects.create(**{**event_values, 'is_test': True})
        AlertEvent.objects.create(**{**event_values, 'status': 'resolved'})
        heartbeat_values = {'tenant': owner.tenant, 'component': 'collector'}
        MonitoringHeartbeat.objects.create(**heartbeat_values)
        rejected(MonitoringHeartbeat, **heartbeat_values)

        from nexus_personal.models import PersonalGatewayRequest, PersonalGatewayAttempt
        request_values = {'tenant': owner.tenant, 'project': owner.project, 'actor': owner.owner,
                          'request_id': str(uuid4()), 'request_digest': 'd' * 64,
                          'requested_model': 'migration-model', 'expires_at': timezone.now()}
        gateway_request = PersonalGatewayRequest.objects.create(**request_values)
        rejected(PersonalGatewayRequest, **request_values)
        attempt_values = {'request': gateway_request, 'source_id': uuid4()}
        PersonalGatewayAttempt.objects.create(**attempt_values)
        rejected(PersonalGatewayAttempt, **attempt_values)

    def test_every_named_index_constraint_and_foreign_key_is_really_installed(self):
        # These are actual tables created by the full migration graph, not a
        # comparison of two Python declarations that could both be wrong.
        with connection.cursor() as cursor:
            models = [model for app_label in ("agents", "datasets", "routers", "deployments", "providers", "metrics", "personal", "workspaces", "mobile", "jobs")
                      for model in apps.get_app_config(app_label).get_models()]
            for model in models:
                physical = connection.introspection.get_constraints(cursor, model._meta.db_table)
                for index in model._meta.indexes:
                    with self.subTest(model=model._meta.label, index=index.name):
                        self.assertIn(index.name, physical)
                        self.assertTrue(physical[index.name]["index"])
                        if index.fields:
                            self.assertEqual(physical[index.name]["columns"],
                                [model._meta.get_field(field.lstrip("-")).column for field in index.fields])
                for constraint in model._meta.constraints:
                    with self.subTest(model=model._meta.label, constraint=constraint.name):
                        self.assertIn(constraint.name, physical)
                        if getattr(constraint, "fields", ()):
                            self.assertTrue(physical[constraint.name]["unique"])
                            self.assertEqual(physical[constraint.name]["columns"],
                                [model._meta.get_field(field).column for field in constraint.fields])
                for field in model._meta.local_fields:
                    if (field.many_to_one or field.one_to_one) and field.db_constraint:
                        expected = (field.remote_field.model._meta.db_table, field.target_field.column)
                        with self.subTest(model=model._meta.label, foreign_key=field.name):
                            self.assertTrue(any(value["columns"] == [field.column]
                                                and value["foreign_key"] == expected
                                                for value in physical.values()))
