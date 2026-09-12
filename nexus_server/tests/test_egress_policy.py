"""Pure renderer regressions plus explicitly authorized Linux kernel acceptance."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import hashlib
import select
import subprocess
import sys
import time
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("egress_policy", ROOT / "nexus_personal/egress_policy.py")
policy_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy_module)


def fixture_policy():
    return {"schema_version": 1, "interfaces": ["agentA", "agentB"],
        "tcp_endpoints": [{"address": "10.0.0.2", "port": 8443},
                          {"address": "fd00:3::2", "port": 8443}], "dns_servers": []}


class EgressPolicyTests(unittest.TestCase):
    def test_exact_ipv4_ipv6_pairs_and_peer_denial_precede_outgoing_allow(self):
        result = policy_module.render_policy(fixture_policy())
        self.assertIn("ip daddr 10.0.0.2 tcp dport 8443", result)
        self.assertIn("ip6 daddr fd00:3::2 tcp dport 8443", result)
        self.assertLess(result.index('comment "agent-peer-denied"'), result.index('forward iifname { "agentA", "agentB" } jump endpoints'))
        self.assertNotIn("flush ruleset", result)
        self.assertNotIn("endpoints ct state established", result)

    def test_interfaces_cannot_inject_nft_syntax_or_include_loopback(self):
        for names in ([], ["lo"], ["a", "a"], ["x" * 16], ['x"; flush ruleset'], ["agent*"], [None]):
            policy = fixture_policy()
            policy["interfaces"] = names
            with self.subTest(names=names), self.assertRaises(ValueError):
                policy_module.render_policy(policy)

    def test_addresses_and_ports_fail_closed(self):
        for address in ("cloud.example", "127.0.0.1", "::1", "169.254.169.254", "fe80::1", "::ffff:10.0.0.2", "0.0.0.0", "ff02::1", "10.0.0.2;accept"):
            policy = fixture_policy()
            policy["tcp_endpoints"][0]["address"] = address
            with self.subTest(address=address), self.assertRaises(ValueError):
                policy_module.render_policy(policy)
        for port in (0, 65536, True, "443", 443.0):
            policy = fixture_policy()
            policy["tcp_endpoints"][0]["port"] = port
            with self.subTest(port=port), self.assertRaises(ValueError):
                policy_module.render_policy(policy)

    def test_empty_destinations_deny_all_and_dns_is_explicit_port53_only(self):
        policy = fixture_policy()
        policy["tcp_endpoints"] = []
        empty = policy_module.render_policy(policy)
        self.assertNotIn("endpoints ip daddr", empty)
        self.assertIn("unapproved-egress", empty)
        policy["dns_servers"] = ["10.0.0.53", "fd00:3::53"]
        result = policy_module.render_policy(policy)
        self.assertIn("ip daddr 10.0.0.53 meta l4proto { tcp, udp } th dport 53", result)
        self.assertIn("ip6 daddr fd00:3::53 meta l4proto { tcp, udp } th dport 53", result)

    def test_canonical_order_and_strict_capacity_schema(self):
        policy = fixture_policy()
        other = copy.deepcopy(policy)
        other["interfaces"].reverse()
        other["tcp_endpoints"].reverse()
        self.assertEqual(policy_module.render_policy(policy), policy_module.render_policy(other))
        for key, value in (("schema_version", True), ("interfaces", ["a"] * 65),
                           ("tcp_endpoints", [{}] * 257), ("dns_servers", ["10.0.0.53"] * 9), ("unknown", 1)):
            invalid = {**policy, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                policy_module.render_policy(invalid)

    def test_only_reserved_worker_prefixes_and_tables_are_accepted(self):
        policy = fixture_policy()
        policy['interfaces'] = ['nxabc123*']
        result = policy_module.render_policy(policy, table='nexus_agent_egress_' + 'a' * 16)
        self.assertIn('oifname { "nx*" }', result)
        self.assertIn('ct direction reply ct state established,related counter accept', result)
        for interface in ('*', 'eth*', 'nx*', 'nxabcdefg*'):
            with self.subTest(interface=interface), self.assertRaises(ValueError):
                policy_module.render_policy({**policy, 'interfaces': [interface]})
        for table in ('filter', 'nexus_agent_egress;flush ruleset', 'nexus_agent_egress_' + 'a' * 17):
            with self.subTest(table=table), self.assertRaises(ValueError):
                policy_module.render_policy(policy, table=table)


# A test-only byte forwarder, not another Cloud application. Its listening socket
# belongs to the synthetic external namespace; only new upstream sockets belong
# to the original namespace. TLS stays end-to-end with the existing ASGI fixture.
# The parent supplies a single owned fixture endpoint over stdin, never a secret
# in argv. This helper is only started by the explicit combined acceptance.
_CLOUD_FORWARDER = r'''
import ipaddress,json,os,select,socket,sys,threading,time
config=json.loads(sys.stdin.readline())
assert set(config)=={'address','port'}
address=ipaddress.ip_address(config['address'])
assert address.version==4 and any(address in ipaddress.ip_network(n) for n in
    ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))
assert type(config['port']) is int and 1024<=config['port']<=65535
listener=socket.socket()
listener.bind(('10.0.0.2',9443))
listener.listen(8)
os.setns(int(sys.argv[1]),os.CLONE_NEWNET)
slots=threading.BoundedSemaphore(8)
def relay(client):
    try:
        with client, socket.create_connection((str(address),config['port']),timeout=8) as upstream:
            client.settimeout(8)
            upstream.settimeout(8)
            peers={client:upstream,upstream:client}
            deadline=time.monotonic()+90
            while peers and time.monotonic()<deadline:
                ready,_,_=select.select(list(peers),[],[],1)
                for source in ready:
                    data=source.recv(16384)
                    target=peers[source]
                    if data:
                        target.sendall(data)
                    else:
                        target.shutdown(socket.SHUT_WR)
                        del peers[source]
    except OSError:
        pass
    finally:
        client.close()
        slots.release()
print('cloud-forwarder-ready',flush=True)
while True:
    client,_=listener.accept()
    if not slots.acquire(blocking=False):
        client.close()
        continue
    threading.Thread(target=relay,args=(client,),daemon=True).start()
'''


_NODE = r'''
import json, os, socket, sys, threading
if sys.argv[1] == 'new':
    os.unshare(os.CLONE_NEWNET)
print('namespace-ready', flush=True)
if sys.stdin.readline() != 'serve\n':
    sys.exit(2)
def connection_handler(connection):
    with connection:
        connection.settimeout(15)
        try:
            while connection.recv(64):
                connection.sendall(b'nexus-egress-probe')
        except OSError:
            pass
def serve(listener):
    while True:
        connection, _ = listener.accept()
        threading.Thread(target=connection_handler, args=(connection,), daemon=True).start()
def dns(listener):
    while True:
        body, source = listener.recvfrom(64)
        listener.sendto(body, source)
listeners = []
for family, address in ((socket.AF_INET, '0.0.0.0'), (socket.AF_INET6, '::')):
    for port in (8080, 8443, 53):
        sock = socket.socket(family, socket.SOCK_STREAM)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((address, port))
        sock.listen(16)
        listeners.append(sock)
        threading.Thread(target=serve, args=(sock,), daemon=True).start()
for address in json.loads(sys.argv[2]):
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_DGRAM)
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    sock.bind((address, 53))
    listeners.append(sock)
    threading.Thread(target=dns, args=(sock,), daemon=True).start()
    threading.Thread(target=dns, args=(sock,), daemon=True).start()
print('listeners-ready', flush=True)
sys.stdin.read()
'''

_PROBE = r'''
import json, os, socket, sys, time
os.setgroups([])
os.setgid(65534)
os.setuid(65534)
address, port, protocol = sys.argv[1], int(sys.argv[2]), sys.argv[3]
family = socket.AF_INET6 if ':' in address else socket.AF_INET
sock = socket.socket(family, socket.SOCK_DGRAM if protocol == 'udp' else socket.SOCK_STREAM)
sock.settimeout(.45)
started = time.monotonic()
error = None
try:
    sock.connect((address, port))
    sock.sendall(b'nexus-egress-probe')
    allowed = sock.recv(64) == b'nexus-egress-probe'
except OSError as exc:
    allowed = False
    error = {'type': type(exc).__name__, 'errno': exc.errno}
finally:
    sock.close()
print(json.dumps({'allowed': allowed, 'uid': os.getuid(), 'error': error, 'elapsed': time.monotonic()-started}))
'''


@unittest.skipUnless(sys.platform == 'linux' and os.environ.get('NEXUS_RUN_EGRESS_NAMESPACE_TESTS') == '1',
                     'Explicitly authorized Linux network namespace acceptance')
class EgressKernelTests(unittest.TestCase):
    @classmethod
    def command(cls, args, *, node=None, body=None, check=True):
        if node is not None:
            args = ['/usr/bin/nsenter', '-t', str(node.pid), '-n', '--', *args]
        return subprocess.run(args, input=body, text=True, capture_output=True,
                              timeout=10, check=check, env=cls.environment)

    @classmethod
    def ready(cls, node, expected):
        if not select.select([node.stdout], [], [], 10)[0] or node.stdout.readline().strip() != expected:
            raise RuntimeError('Isolated network fixture did not become ready')

    @classmethod
    def node(cls, *, new=True, dns_addresses=()):
        node = subprocess.Popen([sys.executable, '-I', '-c', _NODE, 'new' if new else 'current', json.dumps(dns_addresses)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        cls.nodes.append(node)
        cls.ready(node, 'namespace-ready')
        return node

    @classmethod
    def start(cls, node):
        node.stdin.write('serve\n')
        node.stdin.flush()
        cls.ready(node, 'listeners-ready')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if os.geteuid() != 0:
            raise RuntimeError('Authorized namespace test requires namespace administration privileges')
        tools = Path(os.environ['NEXUS_EGRESS_TEST_TOOLS']).resolve(strict=True)
        if not str(tools).startswith('/tmp/nexus-egress-tools-'):
            raise RuntimeError('Use the exact dedicated test tools directory')
        cls.ip = str(tools / 'root/bin/ip')
        cls.nft = str(tools / 'root/usr/sbin/nft')
        cls.environment = {**os.environ, 'LD_LIBRARY_PATH': str(tools / 'root/usr/lib/x86_64-linux-gnu')}
        cls.nodes = []
        cls.original_namespace = os.readlink('/proc/self/ns/net')
        cls.original = os.open('/proc/self/ns/net', os.O_RDONLY)
        cls.original_rules = cls.command([cls.nft, '-j', 'list', 'ruleset']).stdout
        cls.addClassCleanup(cls.cleanup)
        # Every subsequent interface, sysctl and rule change is in this newly
        # created anonymous namespace. There is no link to any host interface.
        os.unshare(os.CLONE_NEWNET)
        cls.test_namespace = os.readlink('/proc/self/ns/net')
        if cls.original_namespace == cls.test_namespace:
            raise RuntimeError('Network namespace isolation did not occur')
        links = json.loads(cls.command([cls.ip, '-j', 'link', 'show']).stdout)
        tables = json.loads(cls.command([cls.nft, '-j', 'list', 'tables']).stdout).get('nftables', [])
        if [row['ifname'] for row in links] != ['lo'] or any('table' in row for row in tables):
            raise RuntimeError('Network namespace was not empty')
        cls.command([cls.ip, 'link', 'set', 'lo', 'up'])
        Path('/proc/sys/net/ipv4/ip_forward').write_text('1\n')
        Path('/proc/sys/net/ipv6/conf/all/forwarding').write_text('1\n')
        cls.clients = []
        for name, subnet, v4 in [('agentA', 1, '198.18.0'), ('agentB', 2, '198.18.1'), ('uplink', 3, '198.19.0')]:
            node = cls.node(dns_addresses=['10.0.0.53', 'fd00:3::53'] if name == 'uplink' else [])
            cls.command([cls.ip, 'link', 'add', name, 'type', 'veth', 'peer', 'name', name + 'p'])
            cls.command([cls.ip, 'link', 'set', name + 'p', 'netns', str(node.pid)])
            for host, interface, suffix in [(None, name, 1), (node, name + 'p', 2)]:
                cls.command([cls.ip, 'addr', 'add', f'{v4}.{suffix}/24', 'dev', interface], node=host)
                cls.command([cls.ip, '-6', 'addr', 'add', f'fd00:{subnet}::{suffix}/64', 'dev', interface, 'nodad'], node=host)
                cls.command([cls.ip, 'link', 'set', interface, 'up'], node=host)
                cls.command([cls.ip, 'link', 'set', 'lo', 'up'], node=host)
            cls.command([cls.ip, 'route', 'add', 'default', 'via', f'{v4}.1'], node=node)
            cls.command([cls.ip, '-6', 'route', 'add', 'default', 'via', f'fd00:{subnet}::1'], node=node)
            cls.clients.append(node)
        cls.a, cls.b, cls.server = cls.clients
        for address in ['10.0.0.2/32', '10.0.0.53/32', '169.254.169.254/32']:
            cls.command([cls.ip, 'addr', 'add', address, 'dev', 'uplinkp'], node=cls.server)
            cls.command([cls.ip, 'route', 'add', address, 'via', '198.19.0.2'])
        for address in ['fd00:3::53/128', 'fd00:ec2::254/128']:
            cls.command([cls.ip, '-6', 'addr', 'add', address, 'dev', 'uplinkp', 'nodad'], node=cls.server)
            cls.command([cls.ip, '-6', 'route', 'add', address, 'via', 'fd00:3::2'])
        # Even explicit global addresses marked nodad have automatically
        # generated link-local neighbors whose DAD must finish first.
        deadline = time.monotonic() + 5
        for node in [None, *cls.clients]:
            while True:
                addresses = json.loads(cls.command([cls.ip, '-j', '-6', 'addr', 'show'], node=node).stdout)
                entries = [entry for link in addresses for entry in link.get('addr_info', [])]
                if any(entry.get('dadfailed') or 'dadfailed' in entry.get('flags', []) for entry in entries):
                    raise RuntimeError('Isolated IPv6 address failed readiness')
                if not any(entry.get('tentative') or 'tentative' in entry.get('flags', []) for entry in entries):
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError('Isolated IPv6 address readiness timed out')
                time.sleep(.05)
        for node in cls.clients:
            cls.start(node)
        cls.host = cls.node(new=False)
        cls.start(cls.host)
        cls.command([cls.nft, '-f', '-'], body='add table inet unrelated_sentinel\n')

    @classmethod
    def cleanup(cls):
        for node in cls.nodes:
            if node.poll() is None:
                node.kill()
            node.wait(timeout=10)
            node.stdin.close()
            node.stdout.close()
        if os.readlink('/proc/self/ns/net') != cls.original_namespace:
            os.setns(cls.original, os.CLONE_NEWNET)
        os.close(cls.original)
        after = cls.command([cls.nft, '-j', 'list', 'ruleset']).stdout
        if after != cls.original_rules:
            raise RuntimeError('Original namespace rules changed during acceptance')
        print(json.dumps({'egress_namespace_cleanup': True, 'owned_processes_reaped': len(cls.nodes),
                          'original_rules_sha256': hashlib.sha256(after.encode()).hexdigest()}))

    def allowed(self, address, port=8443, protocol='tcp', node=None):
        result = json.loads(self.command([sys.executable, '-I', '-c', _PROBE, address, str(port), protocol],
                                        node=node or self.a).stdout)
        self.assertEqual(result['uid'], 65534)
        if os.environ.get('NEXUS_EGRESS_TEST_DIAGNOSTICS') == '1':
            print(json.dumps({'probe': address, 'port': port, 'protocol': protocol, **result}))
        return result['allowed']

    def apply(self, policy):
        self.command([self.nft, '-f', '-'], body=policy_module.render_policy(policy))

    def persistent_connection(self, address):
        source = r'''
import os, socket, sys
os.setgroups([])
os.setgid(65534)
os.setuid(65534)
sock = socket.create_connection((sys.argv[1], 8443), timeout=.45)
print('connected', flush=True)
for line in sys.stdin:
    if line != 'probe\n':
        break
    try:
        sock.sendall(b'nexus-egress-probe')
        print('allowed' if sock.recv(64) == b'nexus-egress-probe' else 'blocked', flush=True)
    except OSError:
        print('blocked', flush=True)
        break
sock.close()
'''
        node = subprocess.Popen(['/usr/bin/nsenter', '-t', str(self.a.pid), '-n', '--',
            sys.executable, '-I', '-c', source, address], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.nodes.append(node)
        self.ready(node, 'connected')
        self.probe_persistent(node, 'allowed')
        return node

    def probe_persistent(self, node, expected):
        node.stdin.write('probe\n')
        node.stdin.flush()
        self.ready(node, expected)

    @unittest.skipUnless(os.environ.get('NEXUS_EGRESS_DOCKER_TESTS') == '1',
                         'Dedicated PID/mount namespace and offline Docker engine required')
    def test_real_docker_bridge_filtering_and_daemon_recovery(self):
        # PID 1 must be this test, under unshare --mount --pid --fork --mount-proc.
        # On exit the kernel tears down every remaining descendant. No host
        # Docker socket, system daemon, service configuration or image is used.
        self.assertEqual(os.getpid(), 1, 'Run only inside a dedicated PID namespace')
        self.assertEqual(os.readlink('/proc/self/ns/pid'), os.readlink('/proc/1/ns/pid'))
        callback = None
        if os.environ.get('NEXUS_EGRESS_CLOUD_CALLBACKS') == '1':
            # Same original test application; all ephemeral grants travel on
            # stdin. Keep the network-isolated outer namespace. The caller
            # passes one pre-opened namespace fd solely for the forwarder's
            # connection to the owned HTTPS fixture, never to the Agent.
            raw = sys.stdin.buffer.read(65537)
            self.assertLessEqual(len(raw), 65536)
            callback = json.loads(raw)
            self.assertEqual(set(callback), {'upstream', 'sdk_request'})
            self.assertEqual(set(callback['upstream']), {'address', 'port'})
            self.assertEqual(set(callback['sdk_request']), {'exchange_url', 'exchange_token', 'trust', 'run_id'})
            self.assertTrue(callback['sdk_request']['exchange_url'].startswith('https://10.0.0.2:9443/'))
            sdk_root = Path(os.environ['NEXUS_EGRESS_CALLBACK_SDK']).resolve(strict=True)
            self.assertTrue((sdk_root / 'nexus_agent/__init__.py').is_file())
            self.assertFalse((sdk_root / '.git').exists())
            upstream_namespace_fd = int(os.environ['NEXUS_EGRESS_CALLBACK_NAMESPACE_FD'])
            self.assertGreaterEqual(upstream_namespace_fd, 3)
            upstream_namespace = os.readlink(f'/proc/self/fd/{upstream_namespace_fd}')
            self.assertRegex(upstream_namespace, r'^net:\[[0-9]+\]$')
            self.assertNotIn(upstream_namespace, (self.original_namespace, self.test_namespace))
        tools = Path(os.environ['NEXUS_EGRESS_TEST_TOOLS']).resolve(strict=True) / 'root'
        archive = Path(os.environ['NEXUS_EGRESS_DOCKER_IMAGE']).resolve(strict=True)
        expected_archive = os.environ['NEXUS_EGRESS_DOCKER_IMAGE_SHA256']
        with archive.open('rb') as source:
            self.assertEqual(hashlib.file_digest(source, 'sha256').hexdigest(), expected_archive)
        # This daemon uses the classic image store. The pinned archive's
        # manifest.json identifies this config blob; its OCI index digest is
        # not a runnable image ID in that store. Never resolve a mutable tag.
        image = 'sha256:05084ba2bb2ab80af5c9479ab90d886b84cfac00465c31200280fa6ac736871e'
        self.command(['/usr/bin/mount', '--make-rprivate', '/'])
        # Docker's network namespace bind mounts must not touch shared /run.
        self.command(['/usr/bin/mount', '-t', 'tmpfs', '-o', 'mode=0755,nosuid,nodev', 'tmpfs', '/run'])
        policy_root = Path(os.environ['NEXUS_EGRESS_CALLBACK_SERVER_ROOT']).resolve(strict=True) if callback else ROOT
        sys.path.insert(0, str(policy_root))
        try:
            from nexus_personal.docker_network_policy import PersonalNetworkPolicy
            import nexus_personal.docker_network_policy as installed_policy
            self.assertTrue(Path(installed_policy.__file__).resolve().is_relative_to(policy_root))
        finally:
            sys.path.pop(0)
        environment = {'PATH': ':'.join(str(tools / part) for part in ('usr/sbin', 'usr/bin', 'sbin', 'bin'))
                       + ':/usr/sbin:/usr/bin:/bin', 'LD_LIBRARY_PATH': self.environment['LD_LIBRARY_PATH'],
                       'XTABLES_LIBDIR': str(tools / 'usr/lib/x86_64-linux-gnu/xtables')}
        daemon = None
        containers = []
        networks = []
        with tempfile.TemporaryDirectory(prefix='nexus-egress-docker-', dir='/tmp') as directory:
            root = Path(directory)
            socket_path = root / 'docker.sock'
            (root / 'client-config').mkdir(mode=0o700)
            docker = ['/usr/bin/docker', '--config', str(root / 'client-config'), '--host', 'unix://' + str(socket_path)]
            binaries = root / 'bin'
            binaries.mkdir()
            for command in ('iptables', 'ip6tables', 'iptables-save', 'ip6tables-save', 'iptables-restore', 'ip6tables-restore'):
                (binaries / command).symlink_to(tools / 'usr/sbin/xtables-nft-multi')
            environment['PATH'] = str(binaries) + ':' + environment['PATH']
            config = {'data-root': str(root / 'data'), 'exec-root': str(root / 'exec'),
                      'pidfile': str(root / 'daemon.pid'), 'hosts': ['unix://' + str(socket_path)],
                      'storage-driver': 'vfs', 'features': {'containerd-snapshotter': False},
                      'bridge': 'none', 'ip-forward': False,
                      'exec-opts': ['native.cgroupdriver=cgroupfs'],
                      'cgroup-parent': '/' + root.name}
            (root / 'daemon.json').write_text(json.dumps(config))
            def cli(*args, check=True, body=None):
                result = subprocess.run([*docker, *args], input=body, capture_output=True, text=True,
                                        timeout=90 if body is not None else 60, check=False, env=environment)
                if check and result.returncode:
                    # This empty, offline daemon receives synthetic inputs only.
                    # Never include argv (which contains probe source) in errors.
                    raise RuntimeError('Isolated Docker ' + args[0] + ' failed: ' + result.stderr[-2500:])
                return result
            def launch():
                nonlocal daemon
                daemon = subprocess.Popen([str(tools / 'usr/bin/dockerd'), '--config-file', str(root / 'daemon.json')],
                    stdout=log, stderr=log, stdin=subprocess.DEVNULL, env=environment)
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if daemon.poll() is not None:
                        raise RuntimeError('Dedicated Docker daemon exited: ' + (root / 'daemon.log').read_text()[-5000:])
                    if cli('info', '--format', '{{.ID}}', check=False).returncode == 0:
                        return
                    time.sleep(.1)
                raise RuntimeError('Dedicated Docker readiness deadline exceeded')
            def stop_daemon():
                if daemon is not None and daemon.poll() is None:
                    daemon.terminate()
                    daemon.wait(timeout=25)
            def probe(name, address, port=8443, protocol='tcp'):
                source = _PROBE.replace('os.setgroups([])\nos.setgid(65534)\nos.setuid(65534)',
                                        'assert os.getuid() == 65534')
                result = json.loads(cli('exec', name, 'python', '-I', '-c', source, address, str(port), protocol).stdout)
                self.assertEqual(result['uid'], 65534)
                return result['allowed']
            configuration = {'nft_executable': self.nft, 'tcp_endpoints': fixture_policy()['tcp_endpoints'], 'dns_servers': []}
            backend = PersonalNetworkPolicy(host_id='isolated-docker-worker', configuration=configuration)
            with (root / 'daemon.log').open('w+') as log, patch.dict(os.environ, environment, clear=True):
                try:
                    launch()
                    self.assertEqual(json.loads(cli('image', 'ls', '--format', 'json').stdout or '[]'), [])
                    cli('load', '--input', str(archive))
                    self.assertEqual(cli('image', 'inspect', image, '--format', '{{.Id}}').stdout.strip(), image)
                    # Real remote callbacks terminate at the synthetic external
                    # namespace. Baselines below prove denied services are live.
                    for number in (10, 11):
                        runtime = 'runtime' + str(number)
                        name = f'nexus-agent-{runtime}-g1'
                        network = name + '-net'
                        labels = {'nexus.managed': 'agent', 'nexus.agent.host': backend.host_id,
                                  'nexus.agent.runtime': runtime}
                        options = backend.prepare(network, labels)
                        self.assertIn(backend.table, self.command([self.nft, '-j', 'list', 'tables']).stdout)
                        args = ['network', 'create', '--driver', 'bridge', '--ipv6', '--subnet', f'198.18.{number}.0/24',
                                '--subnet', f'fd00:{number}::/64', '--opt', 'com.docker.network.bridge.enable_icc=false', *options]
                        for key, value in labels.items():
                            args += ['--label', key + '=' + value]
                        cli(*args, network)
                        networks.append(network)
                        backend.validate_network(json.loads(cli('network', 'inspect', network).stdout)[0], network, labels)
                        code = _NODE.replace("print('namespace-ready', flush=True)", '')
                        code = code.replace("if sys.stdin.readline() != 'serve\\n':\n    sys.exit(2)", '')
                        code = code.replace('sys.stdin.read()', 'threading.Event().wait()')
                        args = ['run', '-d', '--name', name, '--network', network, '--restart', 'no',
                                '--user', '65534:65534', '--read-only', '--cap-drop', 'ALL',
                                '--security-opt', 'no-new-privileges', '--memory', '96m', '--memory-swap', '96m',
                                '--pids-limit', '32', '-p', '127.0.0.1::8443']
                        if callback is not None:
                            args += ['--mount', f'type=bind,src={sdk_root},dst=/nexus-test-sdk,readonly',
                                     '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=1048576']
                        for key, value in labels.items():
                            args += ['--label', key + '=' + value]
                        cli(*args, image, 'python', '-I', '-c', code, 'current', '[]')
                        containers.append(name)
                        backend.validate_container(json.loads(cli('inspect', name).stdout)[0], network)
                    first, second = containers
                    info = json.loads(cli('inspect', second).stdout)[0]
                    peer = info['NetworkSettings']['Networks'][networks[1]]['IPAddress']
                    peer6 = info['NetworkSettings']['Networks'][networks[1]]['GlobalIPv6Address']
                    self.assertTrue(self.allowed(peer, node=self.host), 'Peer listener must be healthy')
                    self.assertTrue(self.allowed(peer6, node=self.host), 'IPv6 peer listener must be healthy')
                    # Docker also isolates bridges. In this no-uplink fixture,
                    # explicitly allow only these two test peers through its
                    # raw direct-access guard and DOCKER-USER chain so the
                    # negative assertion tests Nexus, not an unreachable peer.
                    # Nexus must still reject before Docker's filter accepts.
                    first_network = json.loads(cli('inspect', first).stdout)[0]['NetworkSettings']['Networks'][networks[0]]
                    for binary in ('iptables', 'ip6tables'):
                        address_key = 'IPAddress' if binary == 'iptables' else 'GlobalIPv6Address'
                        pair = ((networks[0], first_network[address_key]),
                                (networks[1], info['NetworkSettings']['Networks'][networks[1]][address_key]))
                        for (incoming, source), (_, destination) in (pair, tuple(reversed(pair))):
                            subprocess.run([str(binaries / binary), '-t', 'raw', '-I', 'PREROUTING', '1',
                                '-i', backend.bridge_name(incoming), '-s', source, '-d', destination,
                                '-p', 'tcp', '-j', 'ACCEPT'],
                                capture_output=True, check=True, timeout=10, env=environment)
                        for incoming, outgoing in ((networks[0], networks[1]), (networks[1], networks[0])):
                            subprocess.run([str(binaries / binary), '-I', 'DOCKER-USER', '1',
                                '-i', backend.bridge_name(incoming), '-o', backend.bridge_name(outgoing),
                                '-j', 'ACCEPT'], capture_output=True, check=True, timeout=10, env=environment)
                    cases = [('10.0.0.2', 8443), ('fd00:3::2', 8443), ('10.0.0.2', 8080),
                             ('169.254.169.254', 8443), ('fd00:ec2::254', 8443),
                             ('198.18.10.1', 8443), (peer, 8443), (peer6, 8443)]
                    # Temporarily remove ONLY this test worker's table. The
                    # isolated engine has no physical/uplink route to real hosts.
                    self.command([self.nft, 'delete', 'table', 'inet', backend.table])
                    for address, port in cases:
                        reachable = probe(first, address, port)
                        if not reachable:
                            diagnostic = subprocess.run([str(binaries / 'iptables-save'), '-c'],
                                capture_output=True, text=True, timeout=10, env=environment)
                            print('isolated-baseline-filter: ' + diagnostic.stdout[-12000:], flush=True)
                        self.assertTrue(reachable, ('baseline', address, port))
                    reverse_peers = (first_network['IPAddress'], first_network['GlobalIPv6Address'])
                    for address in reverse_peers:
                        self.assertTrue(probe(second, address), ('reverse-baseline', address))
                    dns_addresses = ('10.0.0.53', 'fd00:3::53')
                    for address in dns_addresses:
                        for protocol in ('tcp', 'udp'):
                            self.assertTrue(probe(first, address, 53, protocol), ('dns-baseline', address, protocol))
                    backend.prepare(network, labels)
                    for index, (address, port) in enumerate(cases):
                        self.assertEqual(probe(first, address, port), index < 2, ('filtered', address, port))
                    for address in reverse_peers:
                        self.assertFalse(probe(second, address), ('reverse-filtered', address))
                    for address in dns_addresses:
                        for protocol in ('tcp', 'udp'):
                            self.assertFalse(probe(first, address, 53, protocol), ('dns-denied', address, protocol))
                    backend = PersonalNetworkPolicy(host_id=backend.host_id,
                        configuration={**configuration, 'dns_servers': list(dns_addresses)})
                    backend.prepare(network, labels)
                    for address in dns_addresses:
                        for protocol in ('tcp', 'udp'):
                            self.assertTrue(probe(first, address, 53, protocol), ('dns-permitted', address, protocol))
                        self.assertFalse(probe(first, address, 8443), ('dns-other-port', address))
                    first_info = json.loads(cli('inspect', first).stdout)[0]
                    mcp_port = first_info['NetworkSettings']['Ports']['8443/tcp'][0]['HostPort']
                    self.assertTrue(self.allowed('127.0.0.1', int(mcp_port), node=self.host))
                    if callback is not None:
                        import ast
                        forwarder = subprocess.Popen(['/usr/bin/nsenter', '-t', str(self.server.pid), '-n', '--',
                            sys.executable, '-I', '-c', _CLOUD_FORWARDER, str(upstream_namespace_fd)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            pass_fds=(upstream_namespace_fd,), text=True, env=self.environment)
                        self.nodes.append(forwarder)
                        forwarder.stdin.write(json.dumps(callback['upstream']) + '\n')
                        forwarder.stdin.flush()
                        self.ready(forwarder, 'cloud-forwarder-ready')
                        backend = PersonalNetworkPolicy(host_id=backend.host_id, configuration={
                            **configuration, 'dns_servers': list(dns_addresses),
                            'tcp_endpoints': [*configuration['tcp_endpoints'], {'address': '10.0.0.2', 'port': 9443}]})
                        backend.prepare(network, labels)
                        # Extract only the reviewed constant. Do not import a
                        # Django host into this dependency-free kernel harness.
                        fixture = ast.parse((ROOT / 'nexus_personal/tests/mcp_http_fixture.py').read_text(encoding='utf-8'))
                        probe_code = next(ast.literal_eval(item.value) for item in fixture.body
                            if isinstance(item, ast.Assign) and any(isinstance(target, ast.Name)
                                and target.id == 'CLOUD_CALLBACK_SDK_PROBE' for target in item.targets))
                        result = cli('exec', '-i', first, 'python', '-I', '-c', probe_code,
                                     body=json.dumps(callback['sdk_request']))
                        report = json.loads(result.stdout)
                        self.assertTrue(report['container_sdk_callbacks'])
                        self.assertEqual(report['run_id'], callback['sdk_request']['run_id'])
                        self.assertEqual(report['uid'], 65534)
                        # A successful callback must not widen peer/metadata access.
                        self.assertFalse(probe(first, peer))
                        self.assertFalse(probe(first, '169.254.169.254'))
                        print(json.dumps(report), flush=True)
                    for name in containers:
                        cli('stop', '--time', '3', name)
                    stop_daemon()
                    self.command([self.nft, 'delete', 'table', 'inet', backend.table])
                    launch()
                    for name in containers:
                        self.assertEqual(cli('inspect', name, '--format', '{{.State.Running}}').stdout.strip(), 'false')
                    backend.prepare(network, labels)
                    cli('start', first)
                    self.assertTrue(probe(first, '10.0.0.2'))
                    self.assertTrue(probe(first, 'fd00:3::2'))
                    self.assertFalse(probe(first, '169.254.169.254'))
                    self.assertFalse(probe(first, 'fd00:ec2::254'))
                    self.assertTrue(probe(first, '10.0.0.53', 53, 'udp'))
                    self.assertTrue(probe(first, 'fd00:3::53', 53, 'udp'))
                    print(json.dumps({'isolated_docker_egress': True, 'containers': 2,
                                      'ipv4_ipv6': True, 'restart_policy': 'no', 'recovery': True,
                                      'explicit_dns_tcp_udp': True, 'bidirectional_peer_denial': True}))
                finally:
                    if daemon is not None and daemon.poll() is None:
                        for name in containers:
                            cli('rm', '--force', name, check=False)
                        for network in networks:
                            cli('network', 'rm', network, check=False)
                    stop_daemon()

    def test_real_ipv4_ipv6_allow_deny_atomic_reload_and_revocation(self):
        cases = [('10.0.0.2', 8443), ('fd00:3::2', 8443), ('10.0.0.2', 8080),
                 ('198.19.0.2', 8443), ('169.254.169.254', 8443), ('fd00:ec2::254', 8443),
                 ('198.18.0.1', 8443), ('fd00:1::1', 8443),
                 ('198.18.1.2', 8443), ('fd00:2::2', 8443)]
        # Every denied destination is first proven genuinely reachable; a dead
        # server or absent IPv6 path cannot produce a false security success.
        for address, port in cases:
            with self.subTest(stage='baseline', address=address):
                self.assertTrue(self.allowed(address, port))
        for address in ['10.0.0.53', 'fd00:3::53']:
            for protocol in ['udp', 'tcp']:
                with self.subTest(stage='dns-baseline', address=address, protocol=protocol):
                    self.assertTrue(self.allowed(address, 53, protocol))
        self.assertTrue(self.allowed('198.18.0.2', node=self.server))
        policy = fixture_policy()
        self.apply(policy)
        for index, (address, port) in enumerate(cases):
            with self.subTest(stage='filtered', address=address):
                self.assertEqual(self.allowed(address, port), index < 2)
        self.assertTrue(self.allowed('10.0.0.2', node=self.b))
        self.assertFalse(self.allowed('198.18.0.2', node=self.b))
        self.assertFalse(self.allowed('198.18.0.2', node=self.server))
        self.assertTrue(self.allowed('198.19.0.1', node=self.server), 'Unmanaged traffic must be preserved')
        self.assertTrue(self.allowed('198.18.0.2', node=self.host), 'Worker-initiated MCP must receive replies')
        persistent = [self.persistent_connection(address) for address in ['10.0.0.2', 'fd00:3::2']]
        self.assertFalse(self.allowed('10.0.0.53', 53, 'udp'))
        policy['dns_servers'] = ['10.0.0.53', 'fd00:3::53']
        self.apply(policy)
        for address in policy['dns_servers']:
            self.assertTrue(self.allowed(address, 53, 'udp'))
            self.assertTrue(self.allowed(address, 53, 'tcp'))
            self.assertFalse(self.allowed(address, 8443))
        # A failed replacement must preserve the installed rules, not flush
        # everything and briefly allow traffic while a process retries.
        invalid = policy_module.render_policy(policy) + 'add rule inet nexus_agent_egress forward jump missing_chain\n'
        self.assertNotEqual(self.command([self.nft, '-f', '-'], body=invalid, check=False).returncode, 0)
        self.assertTrue(self.allowed('10.0.0.2'))
        self.assertFalse(self.allowed('169.254.169.254'))
        policy['tcp_endpoints'].append({'address': '198.18.1.2', 'port': 8443})
        self.apply(policy)
        self.assertFalse(self.allowed('198.18.1.2'), 'A peer is not an allowed egress endpoint')
        policy['tcp_endpoints'] = []
        self.apply(policy)
        for connection in persistent:
            self.probe_persistent(connection, 'blocked')
        self.assertFalse(self.allowed('10.0.0.2'))
        self.assertFalse(self.allowed('fd00:3::2'))
        policy = fixture_policy()
        self.apply(policy)
        self.assertTrue(self.allowed('10.0.0.2'))
        self.assertTrue(self.allowed('fd00:3::2'))
        self.assertFalse(self.allowed('169.254.169.254'))
        tables = self.command([self.nft, '-j', 'list', 'tables']).stdout
        self.assertIn('unrelated_sentinel', tables)
        rules = self.command([self.nft, '-j', 'list', 'table', 'inet', policy_module.TABLE]).stdout
        denied = [row['rule'] for row in json.loads(rules)['nftables']
                  if row.get('rule', {}).get('comment') == 'unapproved-egress']
        self.assertEqual(len(denied), 1)
        self.assertTrue(any(expr.get('counter', {}).get('packets', 0) > 0 for expr in denied[0]['expr']))

        # Exercise the real Personal backend: install future-bridge coverage
        # before creating the managed interface name, then recover a lost table.
        # These are real Linux links, not a claim of real Docker daemon coverage.
        sys.path.insert(0, str(ROOT))
        try:
            from nexus_personal.docker_network_policy import PersonalNetworkPolicy
        finally:
            sys.path.pop(0)
        configuration = {'nft_executable': self.nft, 'tcp_endpoints': policy['tcp_endpoints'], 'dns_servers': []}
        backend = PersonalNetworkPolicy(host_id='namespace-test-worker', configuration=configuration)
        self.command([self.nft, 'delete', 'table', 'inet', policy_module.TABLE])
        with patch.dict(os.environ, {'LD_LIBRARY_PATH': self.environment['LD_LIBRARY_PATH']}):
            for interface, runtime in [('agentA', 'runtimeA'), ('agentB', 'runtimeB')]:
                network = f'nexus-agent-{runtime}-g1-net'
                labels = {'nexus.managed': 'agent', 'nexus.agent.host': backend.host_id, 'nexus.agent.runtime': runtime}
                backend.prepare(network, labels)
                self.assertIn(backend.table, self.command([self.nft, '-j', 'list', 'tables']).stdout)
                bridge = backend.bridge_name(network)
                links = json.loads(self.command([self.ip, '-j', 'link', 'show']).stdout)
                self.assertNotIn(bridge, [link['ifname'] for link in links])
                self.command([self.ip, 'link', 'set', interface, 'name', bridge])
            self.assertTrue(self.allowed('10.0.0.2'))
            self.assertTrue(self.allowed('fd00:3::2'))
            self.assertFalse(self.allowed('169.254.169.254'))
            self.assertFalse(self.allowed('198.18.1.2'))
            self.assertTrue(self.allowed('198.18.0.2', node=self.host))
            self.command([self.nft, 'delete', 'table', 'inet', backend.table])
            backend.prepare(network, labels)
            self.assertTrue(self.allowed('10.0.0.2'))
            self.assertFalse(self.allowed('169.254.169.254'))


if __name__ == "__main__":
    unittest.main()
