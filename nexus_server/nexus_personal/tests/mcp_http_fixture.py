"""Actual SDK MCP Python tools on loopback, not a Cloud server or fake runner.

MCP fixture exercises the real Docker runner's HTTP transport. The TLS helper
hosts the existing integration ASGI application for actual SDK callbacks.
Container build/start and attached-device execution remain separate acceptance.
"""
from pathlib import Path
from contextlib import contextmanager
import os
import socket
import sys
import threading
import time


# Executed by the existing Docker network acceptance with a read-only SDK mount.
# Standard input contains only that test's ephemeral context, never argv/env.
# Keep this dependency-free so it runs in the same pinned Python probe image.
CLOUD_CALLBACK_SDK_PROBE = r'''
import hashlib,json,os,pathlib,sys,tempfile
sys.path.insert(0,'/nexus-test-sdk')
from nexus_agent.reporting import NexusRunContext,NexusReportingConfig,NexusRunContextExchangeError
from nexus_agent.hosted_trust import hosted_cloud_opener,HostedCloudTrustError
phase='input'
sdk=None
try:
    assert os.getuid()==65534
    body=sys.stdin.buffer.read(65537)
    assert len(body)<=65536
    request=json.loads(body)
    assert set(request)=={'exchange_url','exchange_token','trust','run_id'}
    opener=hosted_cloud_opener({'NEXUS_HOSTED_CLOUD_TRUST':json.dumps(request['trust'])})
    phase='tls-negative'
    untrusted=hosted_cloud_opener({'NEXUS_HOSTED_CLOUD_TRUST':json.dumps({
        'schema_version':1,'mode':'system','origins':request['trust']['origins']})})
    try:
        NexusRunContext.from_exchange(request['exchange_url'],request['exchange_token'],cloud_opener=untrusted)
    except HostedCloudTrustError as error:
        assert error.code=='RUN_CONTEXT_TLS_FAILED'
    else:
        raise AssertionError('Untrusted test CA accepted')
    phase='exchange'
    sdk=NexusRunContext.from_exchange(request['exchange_url'],request['exchange_token'],cloud_opener=opener,
        config=NexusReportingConfig(request_timeout=8,flush_timeout=10,max_retries=0))
    assert sdk.run_id==request['run_id']
    try:
        NexusRunContext.from_exchange(request['exchange_url'],request['exchange_token'],cloud_opener=opener)
    except NexusRunContextExchangeError:
        pass
    else:
        raise AssertionError('One-time context replay accepted')
    phase='plan-chat'
    assert sdk.plan.set([{'title':'Verify container callbacks','status':'completed'}])
    assert sdk.chat.say('容器内 HTTPS 回调已连接')
    report=sdk.flush(timeout=10)
    assert (report.failed,report.pending,report.dropped)==(0,0,0)
    phase='interaction'
    answer=sdk.chat.ask('继续容器验收？',key='container-https-confirm',kind='confirm',timeout=30,
        choices=[{'value':'yes','label':'继续'},{'value':'no','label':'取消'}])
    assert answer.value=='yes'
    phase='output'
    payload='真实容器 HTTPS 文件产物\n'.encode('utf-8')
    with tempfile.TemporaryDirectory(prefix='nexus-callback-output-') as directory:
        source=pathlib.Path(directory)/'container-https-result.txt'
        source.write_bytes(payload)
        output=sdk.output.upload_file(source,content_type='text/plain',timeout=30)
    assert output['state']=='ready'
    phase='history'
    messages=[item['content'] for item in sdk.run.messages(refresh=True)]
    assert messages==['容器内 HTTPS 回调已连接\n\n继续容器验收？','yes']
    result={'container_sdk_callbacks':True,'run_id':sdk.run_id,'artifact_id':output['artifact_id'],
        'sha256':hashlib.sha256(payload).hexdigest(),'uid':os.getuid(),
        'tls_rejected':True,'replay_rejected':True,'interaction_answered':True}
except Exception as error:
    # Do not print exception text, headers, input payloads or traceback locals.
    print(json.dumps({'container_sdk_callbacks':False,'phase':phase,'error_type':type(error).__name__}),flush=True)
    raise SystemExit(1) from None
finally:
    if sdk is not None:
        sdk.close()
print(json.dumps(result),flush=True)
'''


@contextmanager
def cloud_https_fixture(application, *, bind_address='127.0.0.1', isolated_callback=False):
    """One ephemeral loopback TLS listener for the existing integration host.

    Real ASGI application, certificate and sockets; no alternate Cloud settings,
    persistent service or transport mock. Server private key is test-only.
    """
    from datetime import datetime, timedelta, timezone
    import hashlib
    import ipaddress
    import tempfile
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from nexus_personal.install import _private_directory
    import uvicorn

    address = ipaddress.ip_address(bind_address)
    private_networks = tuple(ipaddress.ip_network(item) for item in
                             ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
    if address.version != 4 or (bind_address != '127.0.0.1' and
            (not isolated_callback or not any(address in item for item in private_networks))):
        raise ValueError('Callback fixture requires loopback or an explicit private test interface')
    # The kernel fixture owns this synthetic address in an anonymous namespace.
    # This is not a production trust origin or a configurable forwarding target.
    names = [x509.IPAddress(address)]
    if isolated_callback:
        names.append(x509.IPAddress(ipaddress.ip_address('10.0.0.2')))
    with tempfile.TemporaryDirectory(prefix='nexus-callback-tls-') as directory:
        root = Path(directory) / 'tls'
        _private_directory(root, create=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Isolated callback test')])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName(names), False)
            .sign(key, hashes.SHA256()))
        pem = certificate.public_bytes(serialization.Encoding.PEM)
        cert_path, key_path = root / 'server.pem', root / 'key.pem'
        cert_path.write_bytes(pem)
        with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
        sock = socket.socket()
        sock.bind((bind_address, 0))
        origin = f'https://{bind_address}:{sock.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(application, lifespan='off', log_level='critical',
            access_log=False, ssl_certfile=str(cert_path), ssl_keyfile=str(key_path)))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        try:
            thread.start()
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.01)
            if not server.started:
                raise AssertionError('Real callback TLS listener did not start')
            origins = [origin, 'https://10.0.0.2:9443'] if isolated_callback else [origin]
            yield origin, {'schema_version': 1, 'mode': 'pinned-pem', 'origins': origins,
                           'ca_pem': pem.decode(), 'sha256': hashlib.sha256(pem).hexdigest()}
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()
            if thread.is_alive():
                raise AssertionError('Real callback TLS listener did not stop')


def use_sdk(cleanup):
    """Use an explicitly installed SDK for artifact acceptance, with no fallback."""
    target = os.environ.get('NEXUS_TEST_INSTALLED_SDK_ROOT')
    sdk = Path(target) if target else Path(__file__).resolve().parents[3] / 'nexus_openwrt/sdk/nexus-agent-sdk-python/src'
    if not sdk.is_absolute() or not (sdk/'nexus_agent/__init__.py').is_file():
        raise AssertionError('SDK fixture source is unavailable; fallback is forbidden')
    sdk = sdk.resolve()
    sys.path.insert(0,str(sdk))
    cleanup(lambda: sys.path.remove(str(sdk)))
    import nexus_agent
    if not Path(nexus_agent.__file__).resolve().is_relative_to(sdk/'nexus_agent'):
        raise AssertionError('SDK fixture imported a different installation')


class SDKMCPFixture:
    @classmethod
    def setUpClass(cls):
        use_sdk(cls.addClassCleanup)
        from nexus_agent.fastmcp import NexusMCPServer
        from fastmcp.server.dependencies import get_http_headers
        import uvicorn
        cls.tool_calls = []
        server = NexusMCPServer('Personal MCP boundary',legacy_sse=False)

        @server.tool(name='echo')
        def echo(value: str) -> dict:
            headers = get_http_headers()
            cls.tool_calls.append({'value':value,'received_credential':any(
                name.lower() in {'authorization','x-api-key','cookie'} for name in headers)})
            return {'echo':value}

        app = server.fastmcp.http_app(path='/mcp',stateless_http=True,json_response=True)
        sock = socket.socket()
        sock.bind(('127.0.0.1',0))
        cls.mcp_url = f'http://127.0.0.1:{sock.getsockname()[1]}/mcp'
        cls.mcp_server = uvicorn.Server(uvicorn.Config(app=app,log_level='critical',access_log=False))
        cls.mcp_thread = threading.Thread(target=cls.mcp_server.run,kwargs={'sockets':[sock]},daemon=True)
        def cleanup():
            cls.mcp_server.should_exit = True
            cls.mcp_thread.join(timeout=10)
            sock.close()
            if cls.mcp_thread.is_alive():
                raise AssertionError('SDK MCP fixture did not stop')
        cls.addClassCleanup(cleanup)
        cls.mcp_thread.start()
        deadline = time.monotonic()+15
        while not cls.mcp_server.started and cls.mcp_thread.is_alive() and time.monotonic()<deadline:
            time.sleep(.01)
        if not cls.mcp_server.started:
            raise AssertionError('SDK MCP fixture could not start')
        # Django calls setUpTestData here; it needs the actual bound endpoint.
        super().setUpClass()
