from datetime import timedelta
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import exceptions
from apps.agents import credential_services as c
from apps.agents.models import AgentCredentialAuthorization
from tests.test_provider_credentials import CredentialsTests


@override_settings(NEXUS_PUBLIC_BASE_URL='https://cloud.example.test')
class RenewalTests(TestCase):
    setUp = CredentialsTests.setUp
    new_run = CredentialsTests.new_run
    begin = CredentialsTests.begin
    approve = CredentialsTests.approve

    def test_expiry_requires_fresh_confirmation_and_clears_secret(self):
        identity=self.begin()['authorization_id'];self.approve(identity)
        row=AgentCredentialAuthorization.objects.get(pk=identity)
        row.expires_at=timezone.now()-timedelta(seconds=1);row.save(update_fields=['expires_at'])
        self.assertTrue(c.browser_details(row)['can_renew'])
        self.req.data={'action':'renew'};result=c.decide(self.req,identity)
        self.assertEqual(result['status'],'pending')
        row.refresh_from_db();self.assertEqual(row.encrypted_secret,'');self.assertIsNotNone(row.requested_at)
        with self.assertRaises(exceptions.PermissionDenied):c.internal_status(self.run.id,'test-callback',identity,True)

    def test_active_authorization_cannot_be_silently_renewed(self):
        identity=self.begin()['authorization_id'];self.approve(identity)
        self.req.data={'action':'renew'}
        with self.assertRaises(exceptions.ValidationError):c.decide(self.req,identity)
