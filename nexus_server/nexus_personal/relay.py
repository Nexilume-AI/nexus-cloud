"""Installation-owned Relay provisioning and process entrypoint."""
import argparse
import ipaddress
import http.client
import ssl
import time
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .host_config import load_config, read_protected_json
from .install import _path, _private_directory, _write, _json
from . import relay_credentials as credentials


def prepare(host, address=None, listen=None):
    """Create once; never rotate an existing installation on ordinary restart."""
    state = host['state_dir']
    root = state / 'relay'
    _path(root, exists=False)
    if not root.exists():
        _private_directory(root, create=True)
    else:
        _private_directory(root)
    receipt = root / 'settings.json'
    if receipt.exists():
        saved = read_protected_json(str(receipt))
        if saved['instance_id'] != host['instance_id'] or (address and address != saved['address']) or (listen and listen != saved['listen']):
            raise ValueError('RELAY_IDENTITY_CHANGED: restore the original address and installation')
        # Validate referenced material without silently generating replacement keys.
        for name in saved['files']:
            _path(Path(name))
        return saved
    if any(root.iterdir()):
        raise ValueError('RELAY_INITIALIZATION_INCOMPLETE: inspect the retained relay directory')
    address = str(ipaddress.ip_address(address or '127.0.0.1'))
    if ipaddress.ip_address(address).is_unspecified or ipaddress.ip_address(address).is_multicast:
        raise ValueError('RELAY_ADDRESS_INVALID: supply a reachable unicast IP')
    listen = str(ipaddress.ip_address(listen or address))
    keys = state / 'keys'
    _path(keys)
    _private_directory(keys)
    cert_path, key_path = keys / 'edge-device-ca.pem', keys / 'edge-device-ca-key.pem'
    if cert_path.exists() != key_path.exists():
        raise ValueError('RELAY_DEVICE_CA_INCOMPLETE')
    if not cert_path.exists():
        key, cert = credentials._ca('Nexus Community Device CA ' + host['instance_id'])
        _write(key_path, credentials._private_bytes(key))
        _write(cert_path, cert.public_bytes(credentials.serialization.Encoding.PEM))
    else:
        _path(cert_path)
        _path(key_path)
        cert = credentials.x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = credentials.serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        if cert.public_key().public_numbers() != key.public_key().public_numbers():
            raise ValueError('RELAY_DEVICE_CA_MISMATCH')
    signing = keys / 'edge-signing.pem'
    if not signing.exists():
        _write(signing, credentials._private_bytes(credentials.rsa.generate_private_key(public_exponent=65537, key_size=3072)))
    else:
        _path(signing)
    # The shared issuer helper takes a device-ca.pem input. Only the public CA is copied.
    _write(root / 'device-ca.pem', cert_path.read_bytes())
    manifest = credentials.ensure_credentials(root / 'credentials', edge_credential_dir=root,
        host_address=address, tunnel_port=27444, cloud_port=27445)
    directory = Path(manifest['credential_dir'])
    path = lambda name: str(directory / name)
    config = dict(listen=listen, port=27444,
        tls=dict(key=path('relay-tunnel.key'), cert=path('relay-tunnel.pem'), ca=str(cert_path)),
        relayId=manifest['relay_id'], relayRouterId=manifest['relay_router_id'], relayDomainId=manifest['relay_domain_id'],
        heartbeatMs=1000, maxNodes=256, maxPairs=512, maxQueuedBytes=1048576,
        ticketMaxLifetimeSeconds=300, ticketClockSkewSeconds=5, maxUsedTickets=4096,
        maxCapabilityRoutes=4096, ticketKeys=manifest['ticket_keys'],
        cloudIngress=dict(enabled=True, listen='127.0.0.1', port=27445, relayId=manifest['relay_id'],
            sourceRouterId='nexus-cloud', clientDns=manifest['relay_client_dns'], maxInflight=128,
            maxBodyBytes=1048576, timeoutMs=30000,
            tls=dict(key=path('relay-cloud.key'), cert=path('relay-cloud.pem'), ca=path('server-client-ca.pem')),
            jwt=dict(issuer=manifest['relay_issuer'], keyId=manifest['relay_key_id'],
                publicKey=path('relay-jwt-public.pem'), maxReplay=4096, clockSkewSeconds=5)),
        federation=dict(enabled=False, peers={}), capture=str(root / 'events.jsonl'))
    _write(root / 'relay.config.json', _json(config))
    _write(root / 'control.json', _json(dict(version=1, enabled=True, source='community_startup')))
    authority = '[' + address + ']' if ':' in address else address
    settings = dict(NEXUS_RELAY_ENDPOINTS={manifest['relay_id']:dict(router_id=manifest['relay_router_id'],
        domain_id=manifest['relay_domain_id'], assignment_endpoint=f'https://{authority}:27444/arpx/v1',
        connect_ipv4=address if ipaddress.ip_address(address).version == 4 else '',
        invoke_endpoint='https://127.0.0.1:27445/cloud/invoke/v1', ca_file=path('relay-server-ca.pem'),
        router_ca_file=path('relay-server-ca.pem'))},
        NEXUS_RELAY_TICKET_KEYS=manifest['ticket_keys'], NEXUS_RELAY_TICKET_ACTIVE_KEY_ID=manifest['ticket_key_id'],
        NEXUS_RELAY_CA_FILE=path('relay-server-ca.pem'), NEXUS_RELAY_CLIENT_CERT_FILE=path('server-relay-client.pem'),
        NEXUS_RELAY_CLIENT_KEY_FILE=path('server-relay-client.key'), NEXUS_RELAY_JWT_PRIVATE_KEY_FILE=path('relay-jwt.key'),
        NEXUS_RELAY_JWT_KEY_ID=manifest['relay_key_id'], NEXUS_RELAY_JWT_ISSUER=manifest['relay_issuer'],
        NEXUS_RELAY_FORWARDING_PRIVATE_KEY_FILE=path('forwarding.key'),
        NEXUS_RELAY_FORWARDING_KEY_ID=manifest['forwarding_key_id'], NEXUS_RELAY_FORWARDING_ISSUER=manifest['forwarding_issuer'],
        NEXUS_RELAY_SOURCE_ROUTER_ID='nexus-cloud', NEXUS_RELAY_CONTROL_FILE=str(root / 'control.json'),
        NEXUS_RELAY_RUNTIME_PROBE=True)
    saved = dict(instance_id=host['instance_id'], address=address, listen=listen, settings=settings,
        files=[str(root / 'relay.config.json'), str(cert_path), str(key_path), str(signing)] + [path(name) for name in credentials.REQUIRED_FILES])
    _write(receipt, _json(saved))
    return saved


def settings_for(host):
    receipt = host['state_dir'] / 'relay' / 'settings.json'
    if not receipt.exists():
        return {}
    value = read_protected_json(str(receipt))
    if value['instance_id'] != host['instance_id'] or any(not key.startswith('NEXUS_RELAY_') for key in value['settings']):
        raise ValueError('RELAY_INSTALLATION_MISMATCH')
    # Older receipts omitted the native Directory contract's required address.
    # Derive it only from this installation's already pinned IPv4 identity.
    address = ipaddress.ip_address(value['address'])
    if address.version == 4:
        for endpoint in value['settings'].get('NEXUS_RELAY_ENDPOINTS', {}).values():
            endpoint.setdefault('connect_ipv4', str(address))
    return value['settings']


def wait_ready(host, timeout=30):
    values = settings_for(host)
    context = ssl.create_default_context(cafile=values['NEXUS_RELAY_CA_FILE'])
    context.load_cert_chain(values['NEXUS_RELAY_CLIENT_CERT_FILE'], values['NEXUS_RELAY_CLIENT_KEY_FILE'])
    deadline = time.monotonic() + timeout
    while True:
        connection = http.client.HTTPSConnection('127.0.0.1', 27445, context=context, timeout=2)
        try:
            connection.request('GET', '/')
            response = connection.getresponse()
            response.read()
            if response.status in (403, 404):
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        if time.monotonic() >= deadline:
            raise ValueError('RELAY_START_TIMEOUT')
        time.sleep(.2)


def run():
    host = load_config(os.environ)
    if not settings_for(host):
        raise ValueError('RELAY_NOT_PREPARED')
    node = shutil.which('node')
    if not node:
        raise ValueError('RELAY_NODE_REQUIRED: install Node.js before starting Community')
    command = [node, str(Path(__file__).with_name('relay_runtime') / 'nexus-relayd.js'),
        '--config', str(host['state_dir'] / 'relay' / 'relay.config.json')]
    if os.name != 'nt':
        os.execv(node, command)
    # Windows launcher owns this process tree and stops the child with its wrapper.
    return subprocess.call(command)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installation', required=True, type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--address')
    parser.add_argument('--listen')
    args = parser.parse_args()
    if not shutil.which('node'):
        parser.error('Node.js is required for the bundled Relay')
    host = load_config({'NEXUS_PERSONAL_CONFIG': str(args.installation / 'host.json')})
    if args.check:
        wait_ready(host)
        print('Relay TLS listener ready')
        return
    value = prepare(host, args.address, args.listen)
    print(json.dumps({'relay_address': value['address'], 'tunnel_port': 27444, 'configured': True}))


if __name__ == '__main__':
    main()
