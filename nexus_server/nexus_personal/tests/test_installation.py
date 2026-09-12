from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase, override_settings
from rest_framework import exceptions

from apps.accounts.identity import _backend, password_context
from apps.accounts.models import AccountProfile
from apps.accounts.services import login_user
from apps.audit.models import AuditLog
from apps.tenancy.models import Membership, Project, Team, Tenant
from nexus_personal.models import PersonalInstallation
from nexus_personal.services import PersonalSetupError, installation_context, provision_owner


PASSWORD = "fixture-local-owner-strong-7409!"


class PersonalInstallationTests(TestCase):
    def setUp(self):
        _backend.cache_clear()
        self.addCleanup(_backend.cache_clear)

    def provision(self, **overrides):
        return provision_owner(**{"email": "Owner@example.test", "password": PASSWORD,
                                   "display_name": "Local Owner", **overrides})

    def assert_empty(self):
        for model in (PersonalInstallation, get_user_model(), Tenant, Project, AccountProfile):
            self.assertEqual(model.objects.count(), 0, model.__name__)

    def request(self, **context):
        request = RequestFactory().post("/api/v1/auth/login/")
        request.session = SessionStore()
        request.user = AnonymousUser()
        for key, value in context.items():
            setattr(request, key, value)
        return request

    def test_fresh_database_creates_exactly_one_nonadmin_owner_and_real_context(self):
        row = self.provision()
        row.refresh_from_db()
        user = get_user_model().objects.get(pk=row.owner_id)
        self.assertEqual(user.email, "owner@example.test")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        profile = AccountProfile.objects.get(user=user)
        self.assertEqual((profile.tenant_id, profile.project_id), (str(row.tenant_id), str(row.project_id)))
        self.assertEqual(row.project.tenant_id, row.tenant_id)
        self.assertEqual(installation_context(), (str(user.pk), str(row.tenant_id), str(row.project_id)))
        self.assertEqual(Membership.objects.count(), 0)
        self.assertEqual(Team.objects.count(), 0)
        for private in ("iam", "billing", "tokenbank", "marketplace"):
            self.assertFalse(any(app.label == private for app in apps.get_app_configs()))

    def test_repeated_setup_never_replaces_owner_or_rotates_password(self):
        original = self.provision()
        old_hash = original.owner.password
        with self.assertRaisesMessage(PersonalSetupError, "PERSONAL_ALREADY_CONFIGURED"):
            self.provision(email="replacement@example.test", password="replacement-strong-password-9823!")
        original.owner.refresh_from_db()
        self.assertEqual(original.owner.password, old_hash)
        self.assertEqual(PersonalInstallation.objects.count(), 1)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_writes_rollback_as_one_transaction(self):
        with patch("nexus_personal.services.AccountProfile.objects.create", side_effect=RuntimeError("fixture failure")):
            with self.assertRaises(RuntimeError):
                self.provision()
        self.assert_empty()

    def test_integrity_conflict_rolls_back_and_returns_sanitized_message(self):
        with patch("nexus_personal.services.PersonalInstallation.objects.create", side_effect=IntegrityError("SECRET database details")):
            with self.assertRaises(PersonalSetupError) as raised:
                self.provision()
        self.assertNotIn("SECRET", str(raised.exception))
        self.assertIn("PERSONAL_SETUP_CONFLICT", str(raised.exception))
        self.assert_empty()

    def test_existing_accounts_or_organizations_are_not_adopted(self):
        user = get_user_model().objects.create_user(username="existing", email="existing@example.test")
        with self.assertRaisesMessage(PersonalSetupError, "PERSONAL_DATABASE_NOT_EMPTY"):
            self.provision()
        user.delete()
        tenant = Tenant.objects.create(name="Existing Organization", slug="existing")
        with self.assertRaisesMessage(PersonalSetupError, "PERSONAL_DATABASE_NOT_EMPTY"):
            self.provision()
        self.assertTrue(Tenant.objects.filter(pk=tenant.pk).exists())
        self.assertFalse(PersonalInstallation.objects.exists())

    def test_invalid_credentials_are_rejected_before_any_rows_exist(self):
        for data in ({"email": "invalid"}, {"password": "short"}, {"password": "0" * 20},
                     {"display_name": "x" * 256}, {"password": "x" * 4097}):
            with self.subTest(data=tuple(data)), self.assertRaises(PersonalSetupError) as raised:
                self.provision(**data)
            self.assertNotIn(PASSWORD, str(raised.exception))
            self.assert_empty()

    @override_settings(NEXUS_DISTRIBUTION="enterprise")
    def test_commercial_distribution_cannot_invoke_personal_setup(self):
        with self.assertRaises(ImproperlyConfigured):
            self.provision()
        self.assert_empty()

    def test_missing_installation_fails_closed_without_settings_fallback(self):
        with override_settings(NEXUS_PERSONAL_OWNER_ID="1", NEXUS_PERSONAL_TENANT_ID="invented"):
            with self.assertRaises(ImproperlyConfigured):
                password_context(request=SimpleNamespace(), user=SimpleNamespace(pk=1, is_active=True))
        self.assert_empty()

    def test_database_binding_overrides_legacy_owner_settings(self):
        row = self.provision()
        with override_settings(NEXUS_PERSONAL_OWNER_ID="other", NEXUS_PERSONAL_TENANT_ID="invented"):
            self.assertEqual(password_context(request=SimpleNamespace(), user=row.owner),
                             (str(row.tenant_id), str(row.project_id)))

    def test_wrong_owner_and_cross_context_are_denied(self):
        row = self.provision()
        other = get_user_model().objects.create_user(username="unexpected", email="unexpected@example.test", password=PASSWORD)
        with self.assertRaises(exceptions.AuthenticationFailed):
            password_context(request=SimpleNamespace(), user=other)
        for request in (SimpleNamespace(tenant_id="other"), SimpleNamespace(project_id="other")):
            with self.assertRaises(exceptions.PermissionDenied):
                password_context(request=request, user=row.owner)

    def test_disabled_owner_is_checked_from_current_database_state(self):
        row = self.provision()
        stale_user = row.owner
        get_user_model().objects.filter(pk=stale_user.pk).update(is_active=False)
        with self.assertRaises(ImproperlyConfigured):
            password_context(request=SimpleNamespace(), user=stale_user)

    def test_inconsistent_or_disabled_context_requires_repair(self):
        row = self.provision()
        for target in (row.tenant, row.project):
            target.status = "disabled"
            target.save()
            with self.assertRaises(ImproperlyConfigured):
                installation_context()
            target.status = "active"
            target.save()
        other = Tenant.objects.create(name="Other", slug="other")
        Project.objects.filter(pk=row.project_id).update(tenant=other)
        with self.assertRaises(ImproperlyConfigured):
            installation_context()

    def test_singleton_and_owner_protection_are_database_constraints(self):
        row = self.provision()
        other = get_user_model().objects.create_user(username="second-owner")
        tenant = Tenant.objects.create(name="Other", slug="other")
        project = Project.objects.create(tenant=tenant, name="Other")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PersonalInstallation.objects.create(slot=2, owner=other, tenant=tenant, project=project)
        with self.assertRaises(ProtectedError):
            row.owner.delete()
        self.assertTrue(PersonalInstallation.objects.filter(pk=1).exists())

    @patch("apps.accounts.services.issue_token_pair", return_value={"access_token": "fixture", "refresh_token": "fixture-refresh"})
    def test_shared_password_login_uses_saved_personal_context_and_real_session(self, issue):
        row = self.provision()
        request = self.request()
        result = login_user(request=request, email="owner@example.test", password=PASSWORD)
        self.assertEqual(result["tenant_id"], str(row.tenant_id))
        self.assertEqual(request.session["_auth_user_id"], str(row.owner_id))
        self.assertEqual(issue.call_args.kwargs["tenant_id"], str(row.tenant_id))
        self.assertEqual(issue.call_args.kwargs["project_id"], str(row.project_id))
        self.assertTrue(AuditLog.objects.filter(action="accounts.login", actor_id=row.owner_id).exists())

    @patch("apps.accounts.services.issue_token_pair")
    def test_wrong_password_or_other_user_creates_no_session_or_tokens(self, issue):
        self.provision()
        get_user_model().objects.create_user(username="unexpected", email="unexpected@example.test", password=PASSWORD)
        for email, password in (("owner@example.test", "wrong"), ("unexpected@example.test", PASSWORD)):
            request = self.request()
            with self.assertRaises(exceptions.AuthenticationFailed):
                login_user(request=request, email=email, password=password)
            self.assertNotIn("_auth_user_id", request.session)
        issue.assert_not_called()

    def test_cli_password_input_is_explicit_and_never_echoed(self):
        output = StringIO()
        with patch("sys.stdin", StringIO(PASSWORD + "\n")):
            call_command("setup_personal", email="owner@example.test", password_stdin=True, stdout=output)
        self.assertIn("initialized", output.getvalue())
        self.assertNotIn(PASSWORD, output.getvalue())
        self.assertEqual(PersonalInstallation.objects.count(), 1)

    def test_cli_never_falls_back_to_visible_password_entry(self):
        with patch("sys.stdin", StringIO(PASSWORD + "\n")), patch("getpass.getpass") as prompt:
            with self.assertRaises(CommandError):
                call_command("setup_personal", email="owner@example.test")
        prompt.assert_not_called()
        self.assert_empty()

    def test_interactive_cli_requires_matching_confirmation(self):
        with patch("sys.stdin.isatty", return_value=True), patch("getpass.getpass", side_effect=[PASSWORD, "different"]):
            with self.assertRaisesMessage(CommandError, "Passwords do not match"):
                call_command("setup_personal", email="owner@example.test")
        self.assert_empty()
