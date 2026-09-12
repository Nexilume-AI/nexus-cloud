"""Core identity imports must work with organizational implementations absent."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


PROBE = '''
import importlib.abc, json, sys
from django.conf import settings
class BlockPrivate(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in
               ("nexus_enterprise", "apps.iam", "apps.billing", "apps.tokenbank",
                "apps.marketplace", "apps.tenancy", "apps.api_keys")):
            raise ModuleNotFoundError("Forbidden identity dependency: " + fullname)
sys.meta_path.insert(0, BlockPrivate())
settings.configure(
    SECRET_KEY="identity-import-test-only",
    INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes",
                    "apps.accounts", "apps.audit", "rest_framework.authtoken"],
    NEXUS_IDENTITY_BACKEND="apps.accounts.personal_identity.PersonalIdentityBackend",
    NEXUS_AUDIT_ACTION_ALIASES={},
    NEXUS_PERSONAL_OWNER_ID="1", NEXUS_PERSONAL_TENANT_ID="local",
)
import django
django.setup()
from apps.accounts import google_oauth, github_oauth, services
from apps.common.authentication import NexusBearerAuthentication
from apps.accounts.identity import identity_backend
from types import SimpleNamespace
result = identity_backend().password_context(request=SimpleNamespace(), user=SimpleNamespace(pk=1,is_active=True))
from rest_framework.exceptions import AuthenticationFailed
from django.test import RequestFactory
for headers in ({"HTTP_X_API_KEY": "sk-nexus-test-only"},
                {"HTTP_AUTHORIZATION": "Bearer sk-nexus-test-only"}):
    try:
        NexusBearerAuthentication().authenticate(RequestFactory().get('/', **headers))
    except AuthenticationFailed as error:
        assert str(error.detail) == "Legacy API keys are not supported by this personal instance."
    else:
        raise AssertionError("Personal authentication accepted a legacy API key")
print(json.dumps({"context": result, "private_loaded": [name for name in sys.modules if name.startswith(("nexus_enterprise", "apps.iam", "apps.billing", "apps.tenancy", "apps.marketplace", "apps.tokenbank", "apps.api_keys"))]}))
'''


class IdentityImportBoundaryTests(unittest.TestCase):
    def test_shared_extension_suites_execute_without_private_modules_or_attempts(self):
        probe = PROBE.rsplit('print(', 1)[0]
        probe = probe.replace('class BlockPrivate(', 'blocked_attempts = []\nclass BlockPrivate(')
        probe = probe.replace('            raise ModuleNotFoundError(',
                              '            blocked_attempts.append(fullname)\n            raise ModuleNotFoundError(')
        probe += '''
import unittest
suite = unittest.defaultTestLoader.loadTestsFromNames([
    "tests.test_core_extension_points", "tests.test_identity_extension",
])
result = unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful(), "Shared extension tests failed"
assert result.testsRun == 20, result.testsRun
assert not blocked_attempts, blocked_attempts
print(json.dumps({"tests": result.testsRun, "private_attempts": blocked_attempts}))
'''
        result = subprocess.run([sys.executable, "-c", probe], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"tests": 20, "private_attempts": []})

    def test_core_identity_protocols_import_with_private_apps_blocked(self):
        result = subprocess.run([sys.executable, "-c", PROBE], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"context": ["local", ""], "private_loaded": []})
