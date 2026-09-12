"""Real personal migrations and HTTP authentication with private imports denied."""
from pathlib import Path
import subprocess
import sys
import time
import unittest


PROBE = '''
import importlib.abc, os, sys
class BlockPrivate(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in
               ("nexus_enterprise", "apps.iam", "apps.billing", "apps.tokenbank",
                "apps.marketplace", "apps.api_keys")):
            raise ModuleNotFoundError("Forbidden personal dependency: " + fullname)
sys.meta_path.insert(0, BlockPrivate())
os.environ["DJANGO_SETTINGS_MODULE"] = "nexus_personal.tests.settings"
sys.argv = ["manage.py", "test", "nexus_personal.tests", "--noinput"]
from django.core.management import execute_from_command_line
execute_from_command_line(sys.argv)
'''


def database_test_shards(labels):
    """Bound each fresh-database process without dropping any test module."""
    # Fixed group counts grow without limit as new modules arrive. The prior
    # three-group run exhausted 240s with 39 modules in one process. Bound the
    # batch size instead; retain round-robin order, fresh DBs and the deadline.
    # A single slow module still fails the deadline rather than being skipped.
    count = (len(labels) + 23) // 24
    return [labels[index::count] for index in range(count)]


class PersonalImportBoundaryTests(unittest.TestCase):
    def test_provider_parser_contracts_run_with_private_imports_blocked(self):
        probe = PROBE.replace('"nexus_personal.tests", "--noinput"',
                              '"tests.test_provider_import_tables", "--noinput"')
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", probe], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding="utf-8", timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 5 tests", result.stderr)

    def test_legacy_http_host_is_not_implicitly_enabled_in_personal(self):
        probe = PROBE.split("from django.core.management import execute_from_command_line")[0] + '''
import django, importlib
django.setup()
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from apps.agents.http_host import configured_views, configured_urls, configured_runtime_views, configured_runtime_urls
for load in (configured_views, configured_urls, configured_runtime_views, configured_runtime_urls):
    try:
        load()
    except ImproperlyConfigured:
        pass
    else:
        raise AssertionError("Implicit Agent HTTP host enabled")
for name in ("apps.agents.views", "apps.agents.urls", "apps.agents.runtime_views", "apps.agents.runtime_urls"):
    try:
        importlib.import_module(name)
    except ImproperlyConfigured:
        pass
    else:
        raise AssertionError("Legacy mixed HTTP path loaded")
# Shared operational classes remain usable independently of a legacy host.
from apps.agents import catalog_views, operations_views, device_views
assert callable(catalog_views.AgentListCreateView.as_view)
assert callable(operations_views.AgentDisplayRunEventsView.as_view)
assert callable(device_views.PrivateAgentRunComputerView.as_view)
assert not any(name == "nexus_enterprise" or name.startswith("nexus_enterprise.") for name in sys.modules)
print("personal-agent-http-host-isolated")
'''
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", probe],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding="utf-8", timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "personal-agent-http-host-isolated")

    def test_application_factory_boots_from_cold_registry_without_private_imports(self):
        probe = PROBE.split("from django.core.management import execute_from_command_line")[0] + '''
from django.conf import settings
from django.apps import apps
settings.ROOT_URLCONF = "nexus_personal.urls"
assert not apps.ready
from nexus_personal.application import create_application
assert not apps.ready
application = create_application()
assert apps.ready
from django.core.handlers.asgi import ASGIHandler
from apps.workspaces.asgi import WorkspaceASGIProxy
assert isinstance(application, WorkspaceASGIProxy)
assert isinstance(application.django_app, ASGIHandler)
print("personal-cold-asgi-ok")
'''
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", probe], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding="utf-8", timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "personal-cold-asgi-ok")

    def test_run_admission_races_with_private_imports_blocked(self):
        probe = PROBE.replace('"nexus_personal.tests.settings"', '"nexus_personal.tests.runtime_settings"')
        probe = probe.replace('"nexus_personal.tests", "--noinput"', '"nexus_personal.tests.run_admission_probe", "--noinput"')
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", probe], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 4 tests", result.stderr)

    def test_computer_switch_race_with_private_imports_blocked(self):
        probe = PROBE.replace('"nexus_personal.tests.settings"', '"nexus_personal.tests.runtime_settings"')
        probe = probe.replace('"nexus_personal.tests", "--noinput"',
                              '"nexus_personal.tests.computer_switch_probe", "--noinput"')
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", probe], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 1 test", result.stderr)

    def test_file_flow_sdk_and_chunk_race_with_private_imports_blocked(self):
        probe = PROBE.replace('"nexus_personal.tests.settings"', '"nexus_personal.tests.runtime_settings"')
        probe = probe.replace('"nexus_personal.tests", "--noinput"',
                              '"nexus_personal.tests.agent_file_flow_probe", "--noinput"')
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", probe], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 2 tests", result.stderr)

    def test_follow_up_sdk_and_queue_races_with_private_imports_blocked(self):
        probe = PROBE.replace('"nexus_personal.tests.settings"', '"nexus_personal.tests.runtime_settings"')
        probe = probe.replace('"nexus_personal.tests", "--noinput"',
                              '"nexus_personal.tests.follow_up_flow_probe", "--noinput"')
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", probe], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 4 tests", result.stderr)

    def test_durable_task_claim_race_with_private_imports_blocked(self):
        probe = PROBE.replace('"nexus_personal.tests.settings"', '"nexus_personal.tests.runtime_settings"')
        probe = probe.replace('"nexus_personal.tests", "--noinput"',
                              '"nexus_personal.tests.durable_task_probe", "--noinput"')
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", probe], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 1 test", result.stderr)

    def test_real_runtime_bridge_with_private_imports_blocked(self):
        probe = PROBE.replace('"nexus_personal.tests.settings"', '"nexus_personal.tests.runtime_settings"')
        probe = probe.replace('"nexus_personal.tests", "--noinput"', '"nexus_personal.tests.runtime_bridge_probe", "--noinput"')
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", probe], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 3 tests", result.stderr)
        self.assertNotIn("skipped=", result.stderr)

    def test_agent_model_declarations_load_without_private_schema_or_imports(self):
        # This probe checks imports. The full suite below separately applies
        # fresh Agent/Workspace/Mobile migrations and tests real records.
        setup = PROBE.split("from django.core.management import execute_from_command_line")[0]
        probe = setup + '''
from django.conf import settings
import django
django.setup()
from django.apps import apps
from apps.agents import models
from django.core.exceptions import FieldDoesNotExist
assert len(list(apps.get_app_config("agents").get_models())) == 39
for name in ("AgentPricing", "AgentToolPricingLimit", "APIKey"):
    try:
        getattr(models, name)
    except AttributeError:
        pass
    else:
        raise AssertionError("Private Agent schema exported")
for model, names in (
    (models.AgentDisplayRun, ("billing_token_hash", "billing_token_expires_at")),
    (models.AgentRuntimeInvocation, ("api_key", "pricing_type_snapshot", "authorized_cost",
     "wallet_reserved_amount", "reported_cost", "cost", "currency", "billing_status",
     "billing_report", "billing_report_hash", "billing_idempotency_key", "reported_at", "settled_at")),
):
    for name in names:
        try:
            model._meta.get_field(name)
        except FieldDoesNotExist:
            pass
        else:
            raise AssertionError("Private Agent field remained: " + name)
assert not any("api_key" in index.fields for index in models.AgentRuntimeInvocation._meta.indexes)
assert not hasattr(models.AgentRuntimeInvocation, "BILLING_CAPTURED")
for name in ("Agent", "AgentOutputArtifact", "AgentDisplayEvent", "AgentModelUsage", "AgentExecutionTask"):
    assert apps.get_model("agents", name) is getattr(models, name)
print("agent-model-import-ok")
'''
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", probe], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding="utf-8", timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "agent-model-import-ok")

    def test_dataset_storage_services_import_without_private_modules(self):
        # Import/presentation evidence complements the real personal Dataset
        # table/storage tests below; it is still not a runnable Cloud/E2E host.
        setup = PROBE.split("from django.core.management import execute_from_command_line")[0]
        probe = setup + '''
from django.conf import settings
# The personal test host now installs the actual core Dataset schema.
import django
django.setup()
from apps.datasets import services, transfers, policy, serializers, presentation, media_services, operations, urls
from apps.datasets.models import Dataset
from nexus_personal.dataset_policy import PersonalDatasetPolicy
assert all(callable(getattr(PersonalDatasetPolicy(), name)) for name in policy.OPERATIONS)
assert serializers.DatasetSerializer.__module__ == "nexus_personal.dataset_serializers"
assert "pricing" not in serializers.DatasetSerializer.Meta.fields
assert "publication_readiness" not in serializers.DatasetSerializer.Meta.fields
serializer = serializers.DatasetSerializer()
assert serializer.get_lifecycle_status(Dataset(file_count=0)) == "draft"
assert serializer.get_lifecycle_status(Dataset(file_count=1)) == "assets_added"
assert serializer.get_lifecycle_status(Dataset(file_count=1, current_version="v1")) == "versioned"
assert serializer.get_allowed_actions(Dataset()) == []
assert len(urls.urlpatterns) == 24
for name in ("MarketplaceDatasetSerializer", "DatasetPricingSetSerializer", "DatasetAcquisitionCreateSerializer"):
    try:
        presentation.serializer_export(name)
    except AttributeError:
        pass
    else:
        raise AssertionError("Commercial serializer loaded in personal edition")
assert not any(name == prefix or name.startswith(prefix + ".")
               for name in sys.modules for prefix in
               ("apps.iam", "apps.billing", "apps.marketplace", "apps.tokenbank", "nexus_enterprise", "apps.api_keys"))
print("dataset-core-import-ok")
'''
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", probe], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding="utf-8", timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "dataset-core-import-ok")

    def test_personal_database_and_request_suite_with_private_apps_blocked(self):
        from tests.bounded_process import run_bounded
        root = Path(__file__).resolve().parents[1]
        # Keep the existing per-process bound. The growing database/request
        # suite is split into size-bounded fresh-database shards, with the same import
        # denylist in each. Inventory every test module; none is waived/dropped.
        directory = root / 'nexus_personal' / 'tests'
        labels = sorted('nexus_personal.tests.' + '.'.join(path.relative_to(directory).with_suffix('').parts)
                        for path in directory.rglob('test_*.py'))
        self.assertTrue(labels)
        shards = database_test_shards(labels)
        self.assertEqual(sorted(label for shard in shards for label in shard), labels)
        for index, shard in enumerate(shards):
            with self.subTest(database_shard=index + 1):
                probe = PROBE.replace('"nexus_personal.tests", "--noinput"',
                    ', '.join(repr(label) for label in shard) + ', "--noinput"')
                probe = probe.replace('"--noinput"', '"--noinput", "--verbosity=2"')
                print(f"Personal database shard {index + 1}/{len(shards)}: {len(shard)} modules", flush=True)
                started = time.monotonic()
                try:
                    result = run_bounded([sys.executable, "-c", probe], cwd=root, timeout=240)
                except subprocess.TimeoutExpired as error:
                    diagnostic = (error.stderr or "")[-8000:]
                    print(f"Personal database shard {index + 1} timed out:\n{diagnostic}", file=sys.stderr, flush=True)
                    self.fail(f"Database shard {index + 1} exceeded 240 seconds; owned process tree stopped")
                print(f"Personal database shard {index + 1}: exit {result.returncode}, "
                      f"{time.monotonic() - started:.1f}s", flush=True)
                if result.returncode:
                    print(result.stderr[-8000:], file=sys.stderr, flush=True)
                self.assertEqual(result.returncode, 0, result.stderr)
