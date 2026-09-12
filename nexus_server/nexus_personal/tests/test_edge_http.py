"""Actual Edge HTTP lifecycle and cryptography, with commercial imports denied.

Certificate headers model a verified ingress; these tests are not live TLS or
OpenWrt transport acceptance and never invoke an untrusted Internet endpoint.
"""
import base64
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.agents.models import Agent, AgentRuntimeDeployment, EdgeNode, EdgeAgentRegistration, EdgePairingCode
from apps.agents.edge_auth import issue_edge_access_token
from apps.agents.edge_services import expire_edge_node_presence
from apps.audit.models import AuditLog
from apps.tenancy.models import Project
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


@override_settings(ROOT_URLCONF='nexus_personal.urls', NEXUS_EDGE_REQUIRE_MTLS_HEADER=True,
    NEXUS_PUBLIC_BASE_URL='https://personal.example', NEXUS_EDGE_INGRESS_BASE_URL='https://edge.personal.example',
    NEXUS_EDGE_JWT_ISSUER='https://personal.example/edge', NEXUS_RELAY_ENDPOINTS={})
class PersonalEdgeHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.installation = provision_owner(email='edge-owner@example.test', password=PASSWORD)

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)
        self.client.get('/api/v1/public/bootstrap/')
        self.csrf = {'HTTP_X_CSRFTOKEN': self.client.cookies['csrftoken'].value}
        self.device = APIClient()

    def pairing(self):
        response = self.client.post('/api/v1/edge/pairing-codes/', {}, format='json', **self.csrf)
        self.assertEqual(response.status_code, 201, response.content)
        return response.data['pairing_code']

    def enroll(self, name='my-router'):
        code = self.pairing()
        response = self.device.post('/api/v1/edge/nodes/enroll/', {
            'pairing_code': code, 'router_id': name, 'domain_id': 'personal.example',
            'device_cert_thumbprint': 'a' * 64, 'capabilities': {'router_presence_v1': True}}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.device.credentials(HTTP_AUTHORIZATION='Edge ' + response.data['device_token'],
            HTTP_X_NEXUS_CLIENT_CERT_SHA256='a' * 64)
        return response.data, code

    def presence(self):
        response = self.device.post('/api/v1/edge/v1/presence/', {
            'state': 'online', 'connectivity_mode': 'direct_ipv6', 'capabilities': {}}, format='json')
        self.assertEqual(response.status_code, 200, response.content)

    def payload(self, **overrides):
        origin = 'agent://local-personal/echo'
        return {'origin': origin, 'route_id': 'route-echo', 'protocols': ['mcp'], 'capabilities': ['demo.echo'],
            'mcp_tools': [{'name': 'demo.echo', 'description': 'Echo', 'input_schema': {'type': 'object'},
                           'intent': 'demo.echo', 'intent_version': 1}],
            'provisioning': {'mode': 'managed', 'agent_name': 'My Echo', 'manifest_digest': 'c' * 64},
            'ipv6_address': '2606:4700:1234::10', 'port': 7443, 'path': '/mcp/' + quote(origin, safe=''),
            'scheme': 'https', 'tls_server_name': 'router.personal.example', 'ca_bundle_id': 'personal-ca',
            'generation': 1, 'lease_seconds': 300, **overrides}

    def register(self, **overrides):
        response = self.device.post('/api/v1/edge/v1/agent-registrations/', self.payload(**overrides), format='json')
        self.assertEqual(response.status_code, 201, response.content)
        return response.data

    def test_pairing_inventory_rename_revoke_and_credential_erasure(self):
        caps = self.client.get('/api/v1/edge/capabilities/')
        self.assertEqual(caps.status_code, 200, caps.content)
        self.assertTrue(caps.data['create_own'])
        self.assertFalse(caps.data['audit'])
        self.assertFalse(caps.data['relay_available'])
        node, code = self.enroll()
        self.assertFalse(node['access']['can_manage'])
        self.assertEqual(node['project_id'], str(self.installation.project_id))
        inventory = self.client.get('/api/v1/edge/nodes/')
        self.assertEqual(inventory.status_code, 200, inventory.content)
        self.assertEqual([item['id'] for item in inventory.data], [node['id']])
        for secret in [code, node['device_token'], EdgeNode.objects.get(pk=node['id']).device_token_hash]:
            self.assertNotIn(secret, json.dumps(inventory.data))
            self.assertNotIn(secret, str(list(AuditLog.objects.values('metadata'))))
        changed = self.client.patch(f"/api/v1/edge/nodes/{node['id']}/", {'display_name': 'Renamed'}, format='json', **self.csrf)
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(changed.data['display_name'], 'Renamed')
        self.assertEqual(self.client.post(f"/api/v1/edge/nodes/{node['id']}/revoke/", {}, format='json', **self.csrf).status_code, 204)
        self.assertIn(self.device.post('/api/v1/edge/v1/presence/', {'state': 'online', 'connectivity_mode': 'direct_ipv6'}, format='json').status_code, (401, 403))

    def test_managed_identity_renewal_and_withdrawal_reuse_same_agent(self):
        self.enroll()
        self.presence()
        first = self.register(computer={'requirement': 'required', 'workspace_capabilities': ['browser.control']})
        agent = Agent.objects.get(pk=first['agent_id'])
        self.assertEqual(agent.created_by_id, self.installation.owner_id)
        self.assertEqual(agent.project_id, self.installation.project_id)
        self.assertEqual(agent.visibility, 'private')
        self.assertEqual(agent.workspace_capabilities, ['browser.control'])
        second = self.register(generation=2, route_id='new-route', ipv6_address='2606:4700:1234::11')
        self.assertEqual(second['agent_id'], first['agent_id'])
        self.assertEqual(second['id'], first['id'])
        result = self.device.delete(f"/api/v1/edge/v1/agent-registrations/{first['id']}/")
        self.assertEqual(result.status_code, 204, result.content)
        runtime = AgentRuntimeDeployment.objects.get(edge_registration_id=first['id'])
        self.assertEqual(runtime.status, 'failed')
        recovered = self.register(generation=3)
        self.assertEqual(recovered['agent_id'], first['agent_id'])
        self.assertEqual(Agent.objects.count(), 1)

    def test_foreign_user_project_scope_and_csrf_are_rejected(self):
        self.assertEqual(APIClient().get('/api/v1/edge/nodes/').status_code, 401)
        self.assertEqual(self.client.post('/api/v1/edge/pairing-codes/', {}, format='json').status_code, 403)
        self.assertEqual(self.client.get('/api/v1/edge/nodes/?scope=admin').status_code, 403)
        other = Project.objects.create(tenant=self.installation.tenant, name='Not local')
        result = self.client.post('/api/v1/edge/pairing-codes/', {'project_id': str(other.pk)}, format='json', **self.csrf)
        self.assertEqual(result.status_code, 404, result.content)
        user = get_user_model().objects.create_user(username='other', password=PASSWORD)
        foreign = APIClient()
        foreign.force_login(user)
        self.assertEqual(foreign.get('/api/v1/edge/nodes/').status_code, 401)

    def test_single_use_expired_pairing_wrong_certificate_and_changed_owner(self):
        expired = self.pairing()
        EdgePairingCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.device.post('/api/v1/edge/nodes/enroll/', {'pairing_code': expired, 'router_id': 'expired',
            'domain_id': 'personal.example', 'device_cert_thumbprint': 'a' * 64}, format='json')
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(EdgeNode.objects.count(), 0)
        node, code = self.enroll()
        replay = self.device.post('/api/v1/edge/nodes/enroll/', {'pairing_code': code, 'router_id': 'new-router',
            'domain_id': 'personal.example', 'device_cert_thumbprint': 'a' * 64}, format='json')
        self.assertIn(replay.status_code, (401, 403))
        self.device.credentials(HTTP_AUTHORIZATION='Edge ' + node['device_token'], HTTP_X_NEXUS_CLIENT_CERT_SHA256='b' * 64)
        denied = self.device.post('/api/v1/edge/v1/presence/', {'state': 'online', 'connectivity_mode': 'direct_ipv6'}, format='json')
        self.assertIn(denied.status_code, (401, 403))
        self.device.credentials(HTTP_AUTHORIZATION='Edge ' + node['device_token'], HTTP_X_NEXUS_CLIENT_CERT_SHA256='a' * 64)
        user = get_user_model().objects.create_user(username='not-owner')
        EdgeNode.objects.filter(pk=node['id']).update(registered_by=user)
        denied = self.device.post('/api/v1/edge/v1/presence/', {'state': 'online', 'connectivity_mode': 'direct_ipv6'}, format='json')
        self.assertIn(denied.status_code, (401, 403))

    def test_capacity_failure_rolls_back_and_retry_creates_one_agent(self):
        self.enroll()
        self.presence()
        with override_settings(NEXUS_PERSONAL_AGENT_LIMITS={'agents.agents': 0}):
            result = self.device.post('/api/v1/edge/v1/agent-registrations/', self.payload(), format='json')
        self.assertEqual(result.status_code, 409, result.content)
        self.assertEqual(Agent.objects.count(), 0)
        self.assertEqual(EdgeAgentRegistration.objects.count(), 0)
        node = EdgeNode.objects.get()
        self.assertEqual(node.registration_diagnostic['code'], 'PERSONAL_CAPACITY_EXCEEDED')
        self.assertNotIn('PLAN_', json.dumps(node.registration_diagnostic))
        self.register()
        self.presence()
        node.refresh_from_db()
        self.assertEqual(node.registration_diagnostic, {})
        self.assertEqual(Agent.objects.count(), 1)

    def test_missing_relay_infrastructure_fails_instead_of_inventing_assignment(self):
        response = self.client.get('/api/v1/edge/relay-service/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.data['configured'])
        code = self.pairing()
        response = self.device.post('/api/v1/edge/nodes/enroll/', {'pairing_code': code, 'router_id': 'relay-router',
            'domain_id': 'personal.example', 'device_cert_thumbprint': 'a' * 64, 'connectivity_mode': 'relay'}, format='json')
        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(EdgeNode.objects.count(), 0)
        self.assertFalse(EdgePairingCode.objects.filter(used_at__isnull=False).exists())

    def test_personal_owner_controls_relay_without_enterprise_superuser(self):
        self.assertFalse(self.installation.owner.is_superuser)
        with tempfile.TemporaryDirectory(prefix='personal-relay-control-') as directory:
            control = Path(directory) / 'relay.json'
            with override_settings(NEXUS_RELAY_CONTROL_FILE=str(control)):
                response = self.client.get('/api/v1/edge/relay-service/')
                self.assertEqual(response.status_code, 200, response.content)
                self.assertTrue(response.data['can_manage'])
                denied = self.client.post('/api/v1/edge/relay-service/', {'enabled': False}, format='json')
                self.assertEqual(denied.status_code, 403)
                self.assertFalse(control.exists())
                response = self.client.post('/api/v1/edge/relay-service/', {'enabled': False}, format='json', **self.csrf)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertFalse(json.loads(control.read_text())['enabled'])
                response = self.client.post('/api/v1/edge/relay-service/', {'enabled': True}, format='json', **self.csrf)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertFalse(json.loads(control.read_text())['enabled'])
                # Owning an installation never makes unavailable infrastructure healthy.
                self.assertFalse(self.client.get('/api/v1/edge/relay-service/').data['available'])

    def test_foreign_superuser_and_device_cannot_control_personal_relay(self):
        from apps.agents.edge_policy import edge_policy
        from types import SimpleNamespace
        other = get_user_model().objects.create_superuser(username='foreign-relay-admin', password=PASSWORD)
        self.assertFalse(edge_policy().can_manage_relay(SimpleNamespace(user=other)))
        foreign = APIClient()
        foreign.force_login(other)
        with tempfile.TemporaryDirectory(prefix='personal-relay-denial-') as directory:
            control = Path(directory) / 'relay.json'
            with override_settings(NEXUS_RELAY_CONTROL_FILE=str(control)):
                for client in (foreign, self.device):
                    response = client.post('/api/v1/edge/relay-service/', {'enabled': False}, format='json')
                    self.assertIn(response.status_code, (401, 403))
                self.assertFalse(control.exists())

    def test_reenrollment_rotates_device_credential_and_preserves_managed_identity(self):
        node, _ = self.enroll()
        self.presence()
        first = self.register()
        again, _ = self.enroll()
        self.assertEqual(again['id'], node['id'])
        self.assertNotEqual(again['device_token'], node['device_token'])
        self.assertGreater(again['enrollment_generation'], node['enrollment_generation'])
        old = APIClient()
        old.credentials(HTTP_AUTHORIZATION='Edge ' + node['device_token'], HTTP_X_NEXUS_CLIENT_CERT_SHA256='a' * 64)
        denied = old.post('/api/v1/edge/v1/presence/', {'state': 'online', 'connectivity_mode': 'direct_ipv6'}, format='json')
        self.assertIn(denied.status_code, (401, 403))
        self.presence()
        restored = self.register(generation=2)
        self.assertEqual(restored['agent_id'], first['agent_id'])
        self.assertEqual(Agent.objects.count(), 1)

    def test_real_jwks_and_router_scoped_signature_use_only_personal_key(self):
        node, _ = self.enroll()
        self.presence()
        registered = self.register()
        deployment = AgentRuntimeDeployment.objects.get(edge_registration_id=registered['id'])
        with tempfile.TemporaryDirectory(prefix='personal-edge-signing-') as directory:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            filename = Path(directory) / 'key.pem'
            filename.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            filename.chmod(0o600)
            with override_settings(NEXUS_EDGE_JWT_PRIVATE_KEY_FILE=str(filename)):
                jwks = self.device.get('/api/v1/edge/.well-known/jwks.json')
                self.assertEqual(jwks.status_code, 200, jwks.content)
                token = issue_edge_access_token(deployment=deployment)
            header, payload, signature = token.split('.')
            decode = lambda value: base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
            public = jwks.data['keys'][0]
            published_key = rsa.RSAPublicNumbers(int.from_bytes(decode(public['e']), 'big'), int.from_bytes(decode(public['n']), 'big')).public_key()
            published_key.verify(decode(signature), (header + '.' + payload).encode(), padding.PKCS1v15(), hashes.SHA256())
            claims = json.loads(decode(payload))
            self.assertEqual(claims['tenant'], str(self.installation.tenant_id))
            self.assertEqual(claims['target_agent'], self.payload()['origin'])
            self.assertEqual(claims['aud'], 'urn:nexus:router:' + node['router_id'])
            self.assertEqual(claims['iss'], 'https://personal.example/edge')
            self.assertEqual(set(claims['scope'].split()), {'agent.route', 'agent.invoke'})
            self.assertNotIn('d', public)

    def test_generation_scope_and_presence_failure_preserve_recoverable_registration(self):
        node, _ = self.enroll()
        self.presence()
        first = self.register(generation=3)
        for changes in [{'generation': 2}, {'ipv6_address': '::1'}, {'mcp_tools': [{'name': 'invalid'}]}]:
            result = self.device.post('/api/v1/edge/v1/agent-registrations/', self.payload(**changes), format='json')
            self.assertEqual(result.status_code, 400, result.content)
        registration = EdgeAgentRegistration.objects.get(pk=first['id'])
        self.assertEqual(registration.generation, 3)
        original_device = self.device
        self.device = APIClient()
        self.enroll('another-router')
        rejected = self.device.delete(f"/api/v1/edge/v1/agent-registrations/{first['id']}/")
        self.assertEqual(rejected.status_code, 404, rejected.content)
        self.device = original_device
        EdgeNode.objects.filter(pk=node['id']).update(presence_expires_at=timezone.now() - timedelta(seconds=1))
        expire_edge_node_presence()
        runtime = AgentRuntimeDeployment.objects.get(edge_registration=registration)
        self.assertEqual(runtime.status, 'failed')
        self.presence()
        restored = self.register(generation=4)
        self.assertEqual(restored['agent_id'], first['agent_id'])
