from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from apps.agents.models import AgentRunInteraction
from apps.agents.runtime_services import pending_mcp_elicitation_chunks


class CredentialStreamTests(SimpleTestCase):
    def test_credential_and_regular_form_events_can_share_a_stream(self):
        request=SimpleNamespace(user=SimpleNamespace(pk=1,is_authenticated=True),tenant_id='tenant',headers={})
        run=SimpleNamespace(interactions=AgentRunInteraction.objects.none())
        session=SimpleNamespace(client_capabilities_json={'elicitation':True,'elicitation_url':True})
        seen={'12345678-1234-1234-1234-123456789abc'}
        def credential_chunks(request,run,session,seen):
            seen.update({'credential:12345678-1234-1234-1234-123456789abc',
                         'credential:12345678-1234-1234-1234-123456789abc:done'})
            return iter([b'credential-event'])
        with patch('apps.agents.credential_services.chunks',side_effect=credential_chunks):
            self.assertEqual(list(pending_mcp_elicitation_chunks(request=request,run=run,session=session,seen=seen)),[b'credential-event'])
