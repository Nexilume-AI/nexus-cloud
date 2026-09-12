"""Explicit real-Docker build acceptance; not part of the ordinary DB suite.

Use the existing private-import-denied test host. No database, Cloud process,
source secrets, financial fixture or fake builder is involved.
"""
import shutil
import tempfile
import json
from uuid import uuid4
from django.test import SimpleTestCase
from tests.python_docker_guards import PythonDockerGuards
from tests.python_build_guards import SOURCE
from apps.agents.python_builder import docker, sandbox, remove_own_container


CALL_PROBE = r'''
import json, os, socket, subprocess, sys, time
sys.path.insert(0, '/opt/nexus-python')
from nexus_boot import rpc
assert os.getuid() == 65532
assert {name for _,name in socket.if_nameindex()} == {'lo'}
try:
    with open('/opt/nexus-python/unexpected-write', 'x'):
        pass
except (PermissionError, OSError):
    pass
else:
    raise AssertionError('Expected read-only application filesystem')
process = subprocess.Popen([sys.executable, '/opt/nexus-python/nexus_boot.py', 'serve', 'server'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
headers = {'MCP-Protocol-Version': '2025-06-18'}
try:
    deadline = time.monotonic() + 30
    while True:
        assert process.poll() is None, 'Agent exited during initialization'
        try:
            response = rpc('initialize', {'protocolVersion':'2025-06-18', 'capabilities':{},
                'clientInfo':{'name':'personal-live-call','version':'1'}}, headers)
            assert 'result' in response
            break
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                raise AssertionError('Agent did not initialize') from None
            time.sleep(.1)
    tools = rpc('tools/list', {}, headers)['result']['tools']
    assert [tool['name'] for tool in tools] == ['hello']
    def call(arguments):
        return rpc('tools/call', {'name':'hello','arguments':arguments}, headers)
    def text(response):
        assert 'error' not in response
        result = response['result']
        assert not result.get('isError', False)
        return ''.join(item['text'] for item in result['content'] if item['type']=='text')
    assert text(call({'message':'你好，container'})) == 'hello 你好，container'
    for arguments in ({}, {'message':'reject'}):
        response = call(arguments)
        assert 'error' in response or response['result'].get('isError') is True
    assert text(call({'message':'after-error'})) == 'hello after-error'
finally:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
assert process.poll() is not None
print(json.dumps({'calls':4,'negative_calls':2,'recovered':True,'offline':True,'nonroot':True}))
'''


class PersonalPythonDockerTests(PythonDockerGuards, SimpleTestCase):
    def setUp(self):
        executable = shutil.which('docker')
        self.assertIsNotNone(executable, 'An explicitly available Docker CLI is required')
        storage = tempfile.TemporaryDirectory(prefix='nexus-personal-python-artifacts-')
        self.addCleanup(storage.cleanup)
        configured = self.settings(NEXUS_AGENT_PYTHON_DOCKER=executable,
                                   NEXUS_AGENT_STORAGE_ROOT=storage.name)
        configured.enable()
        self.addCleanup(configured.disable)

    def test_built_agent_serves_real_mcp_and_remains_usable_after_errors(self):
        # Build verification remains side-effect free; calls happen only in this
        # separate owned container, with no host port or external network.
        source = SOURCE.replace('return "hello " + message',
            'if message == "reject":\n        raise ValueError("Expected fixture rejection")\n    return "hello " + message')
        result = self.build(source)
        name = 'nexus-python-' + uuid4().hex
        self.addCleanup(remove_own_container, name)
        report = json.loads(docker(sandbox(name, result['digest'], ['-I', '-c', CALL_PROBE]), timeout=90))
        self.assertEqual(report, {'calls':4, 'negative_calls':2, 'recovered':True, 'offline':True, 'nonroot':True})
