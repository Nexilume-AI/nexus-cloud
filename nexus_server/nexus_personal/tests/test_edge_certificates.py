"""Personal Edge certificate HTTP contracts; no live TLS or Router is claimed.

Use generated temporary CAs and the real owner/CSRF enrollment path. Certificate
headers represent the ingress verification boundary, not a bypass of device auth.
"""
import json
import tempfile
from datetime import timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.agents.models import EdgeNode, EdgePairingCode
from apps.audit.models import AuditLog
from . import test_edge_http as edge_fixture


@override_settings(ROOT_URLCONF='nexus_personal.urls', NEXUS_EDGE_REQUIRE_MTLS_HEADER=True,
    NEXUS_PUBLIC_BASE_URL='https://personal.example', NEXUS_RELAY_ENDPOINTS={})
class PersonalEdgeCertificateTests(TestCase):
    setUpTestData = classmethod(edge_fixture.PersonalEdgeHTTPTests.setUpTestData.__func__)
    pairing = edge_fixture.PersonalEdgeHTTPTests.pairing
    enroll = edge_fixture.PersonalEdgeHTTPTests.enroll

    def setUp(self):
        edge_fixture.PersonalEdgeHTTPTests.setUp(self)
        directory = tempfile.TemporaryDirectory(prefix='personal-edge-ca-')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.ca_key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Isolated test CA')])
        now = timezone.now()
        self.ca = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(self.ca_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(self.ca_key, hashes.SHA256()))
        certificate, key = root / 'ca.pem', root / 'key.pem'
        certificate.write_bytes(self.ca.public_bytes(serialization.Encoding.PEM))
        key.write_bytes(self.ca_key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        key.chmod(0o600)
        settings = override_settings(NEXUS_EDGE_DEVICE_CA_CERT_FILE=str(certificate),
            NEXUS_EDGE_DEVICE_CA_KEY_FILE=str(key), NEXUS_EDGE_CA_FILE=str(certificate),
            NEXUS_EDGE_CA_KEY_FILE=str(key), NEXUS_EDGE_INGRESS_CA_BUNDLE_ID='isolated-test-ca')
        settings.enable()
        self.addCleanup(settings.disable)

    def csr(self):
        key = ec.generate_private_key(ec.SECP256R1())
        csr = (x509.CertificateSigningRequestBuilder().subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, 'untrusted-client-name')]))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('forged.example')]), False)
            .sign(key, hashes.SHA256()))
        return key, csr.public_bytes(serialization.Encoding.PEM).decode('ascii')

    def assert_identity(self, identity, key, purpose, name):
        certificate = x509.load_pem_x509_certificate(identity['certificate'].encode())
        certificate.verify_directly_issued_by(self.ca)
        self.assertEqual(certificate.public_key().public_numbers(), key.public_key().public_numbers())
        self.assertEqual(certificate.fingerprint(hashes.SHA256()).hex(), identity['sha256'])
        self.assertEqual(certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value, name)
        self.assertEqual(list(certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value), [purpose])
        self.assertFalse(certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        self.assertLessEqual(certificate.not_valid_after_utc, self.ca.not_valid_after_utc)
        self.assertEqual(identity['certificate_chain'], identity['certificate'] + identity['ca_certificate'])
        self.assertNotIn('PRIVATE KEY', json.dumps(identity))
        return certificate

    def test_enrollment_rotation_confirm_and_old_certificate_rejection(self):
        key, csr = self.csr()
        response = self.device.post('/api/v1/edge/nodes/enroll/', {
            'pairing_code': self.pairing(), 'router_id': 'personal-cert-router',
            'domain_id': 'personal.example', 'device_csr': csr}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        identity = response.data['device_identity']
        certificate = self.assert_identity(identity, key, ExtendedKeyUsageOID.CLIENT_AUTH, 'personal-cert-router')
        self.assertEqual(certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            .value.get_values_for_type(x509.DNSName), ['personal-cert-router.personal.example'])
        token, old = response.data['device_token'], identity['sha256']
        self.device.credentials(HTTP_AUTHORIZATION='Edge ' + token, HTTP_X_NEXUS_CLIENT_CERT_SHA256=old)
        new_key, new_csr = self.csr()
        renewed = self.device.post('/api/v1/edge/v1/device-certificate/renew/',
            {'device_csr': new_csr}, format='json')
        self.assertEqual(renewed.status_code, 200, renewed.content)
        new_identity = renewed.data['device_identity']
        self.assert_identity(new_identity, new_key, ExtendedKeyUsageOID.CLIENT_AUTH, 'personal-cert-router')
        node = EdgeNode.objects.get(pk=response.data['id'])
        self.assertEqual(node.device_cert_thumbprint, old)
        self.assertEqual(node.pending_device_cert_thumbprint, new_identity['sha256'])
        self.assertNotEqual(old, new_identity['sha256'])
        self.device.credentials(HTTP_AUTHORIZATION='Edge ' + token,
            HTTP_X_NEXUS_CLIENT_CERT_SHA256=new_identity['sha256'])
        confirmed = self.device.post('/api/v1/edge/v1/device-certificate/confirm/', {}, format='json')
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        node.refresh_from_db()
        self.assertEqual(node.device_cert_thumbprint, new_identity['sha256'])
        self.assertEqual(node.pending_device_cert_thumbprint, '')
        self.assertIsNone(node.pending_device_cert_expires_at)
        for thumbprint in (old, '', 'b' * 64):
            self.device.credentials(HTTP_AUTHORIZATION='Edge ' + token, HTTP_X_NEXUS_CLIENT_CERT_SHA256=thumbprint)
            self.assertIn(self.device.post('/api/v1/edge/v1/device-certificate/confirm/', {}, format='json').status_code, (401, 403))
        inventory = self.client.get('/api/v1/edge/nodes/')
        self.assertNotIn(token, json.dumps(inventory.data))
        self.assertNotIn(token, str(list(AuditLog.objects.values('metadata'))))

    def test_ingress_identity_is_bound_to_authenticated_node_not_requested_san(self):
        node, _ = self.enroll()
        key, csr = self.csr()
        url = '/api/v1/edge/v1/ingress-certificate/renew/'
        self.assertIn(APIClient().post(url, {'ingress_csr': csr}, format='json').status_code, (401, 403))
        response = self.device.post(url, {'ingress_csr': csr}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        identity = response.data['ingress_identity']
        expected = 'edge-' + node['id'].replace('-', '') + '.router.nexus'
        certificate = self.assert_identity(identity, key, ExtendedKeyUsageOID.SERVER_AUTH, expected)
        self.assertEqual(identity['tls_server_name'], expected)
        self.assertEqual(identity['ca_bundle_id'], 'isolated-test-ca')
        self.assertEqual(certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            .value.get_values_for_type(x509.DNSName), [expected])
        invalid = self.device.post(url, {'ingress_csr': 'invalid'}, format='json')
        self.assertEqual(invalid.status_code, 400, invalid.content)
        self.assertNotIn('PRIVATE KEY', invalid.content.decode())

    def test_invalid_csr_does_not_consume_pairing_and_expired_rotation_is_rejected(self):
        code = self.pairing()
        payload = {'pairing_code': code, 'router_id': 'invalid-csr',
            'domain_id': 'personal.example', 'device_csr': 'invalid'}
        response = self.device.post('/api/v1/edge/nodes/enroll/', payload, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('device_csr', response.content.decode())
        self.assertFalse(EdgeNode.objects.exists())
        self.assertIsNone(EdgePairingCode.objects.get().used_at)
        _, payload['device_csr'] = self.csr()
        enrolled = self.device.post('/api/v1/edge/nodes/enroll/', payload, format='json')
        self.assertEqual(enrolled.status_code, 201, enrolled.content)
        EdgeNode.objects.filter(pk=enrolled.data['id']).update(pending_device_cert_thumbprint='c' * 64,
            pending_device_cert_expires_at=timezone.now() - timedelta(seconds=1))
        self.device.credentials(HTTP_AUTHORIZATION='Edge ' + enrolled.data['device_token'],
            HTTP_X_NEXUS_CLIENT_CERT_SHA256='c' * 64)
        rejected = self.device.post('/api/v1/edge/v1/device-certificate/confirm/', {}, format='json')
        self.assertIn(rejected.status_code, (401, 403))
        node = EdgeNode.objects.get(pk=enrolled.data['id'])
        self.assertEqual(node.device_cert_thumbprint, enrolled.data['device_identity']['sha256'])
