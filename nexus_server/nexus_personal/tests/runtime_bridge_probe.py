"""In-process ASGI + durable queue + real SDK filesystem/process integration.

Run explicitly with runtime_settings through test_personal_import_boundary;
this uses committed records, unlike the rollback-based unit TestCase suite.

The queue cases drive ASGI with ApplicationCommunicator. The callback case
exposes this same test application on ephemeral loopback HTTPS, exercising
actual SDK TLS/HTTP without a second Cloud installation or fake Computer runner.
SDK dispatch executes only in temporary directories. Docker-isolated callbacks,
network WSS and browser workloads remain separate release requirements.
"""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from asgiref.sync import async_to_sync, sync_to_async
from asgiref.testing import ApplicationCommunicator
from django.test import TransactionTestCase, override_settings
from django.db import connections
from rest_framework.authtoken.models import Token
from nexus_personal.application import create_application
from apps.workspaces import computer_runtime as runtime
from apps.workspaces.models import WorkspaceConnection
from nexus_personal.services import provision_owner
from . import test_computer_runtime_http as computer_http
from .test_installation import PASSWORD
from .provider_http_fixture import ProviderHTTPFixture
from .mcp_http_fixture import SDKMCPFixture, use_sdk
from .monitoring_smtp_fixture import SMTPFixture
from . import test_deployment_http as deployment_http


@override_settings(NEXUS_PUBLIC_BASE_URL="https://personal.example", REDIS_URL="redis://127.0.0.1:1/0",
                   ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1", NEXUS_AGENT_RUNTIME_RUNNER="docker")
class PersonalRuntimeBridgeTests(SDKMCPFixture, ProviderHTTPFixture, TransactionTestCase):
    pair = computer_http.PersonalComputerRuntimeTests.pair
    ticket = computer_http.PersonalComputerRuntimeTests.ticket
    source = deployment_http.PersonalDeploymentHTTPTests.source
    post = deployment_http.PersonalDeploymentHTTPTests.post

    def setUp(self):
        self.row = provision_owner(email="bridge-owner@example.test", password=PASSWORD)
        self.owner_token = Token.objects.create(user=self.row.owner)
        computer_http.PersonalComputerRuntimeTests.setUp(self)
        ProviderHTTPFixture.setUp(self)
        self.installation = self.row
        self.headers = {}
        self.source()
        from apps.deployments.models import ModelGroup
        from apps.routers import services
        self.tool_router = services.create_router(request=self.request(), name="Tool bridge Router",
            model_group_ids=[ModelGroup.objects.get().pk])
        services.deploy_router(request=self.request(), router_id=str(self.tool_router.pk))

    def test_real_https_sdk_exchange_callbacks_and_caller_display(self):
        from concurrent.futures import ThreadPoolExecutor
        from datetime import timedelta
        from urllib.request import Request
        from urllib.error import HTTPError
        from unittest.mock import patch
        import os
        import time
        from django.utils import timezone
        from apps.agents import runtime_services
        from apps.agents.models import Agent, AgentRuntimeDeployment, EdgeNode, EdgeAgentRegistration, AgentRunContextGrant
        from apps.agents.runtime_context import prepare_openwrt_run_context_headers
        from nexus_agent.reporting import NexusRunContext, NexusReportingConfig, NexusRunContextExchangeError
        from nexus_agent.hosted_trust import hosted_cloud_opener, HostedCloudTrustError
        from .mcp_http_fixture import cloud_https_fixture

        with cloud_https_fixture(create_application()) as (origin, trust), patch.dict(os.environ,
                {'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'}), override_settings(
                ALLOWED_HOSTS=['127.0.0.1', 'localhost', 'testserver'], NEXUS_PUBLIC_BASE_URL=origin,
                NEXUS_AGENT_CONTEXT_EXCHANGE_BASE_URL=origin, NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL=origin,
                NEXUS_DATASET_STORAGE_ROOT=str(Path(self.folder.name) / 'callback-objects')):
            opener = hosted_cloud_opener({'NEXUS_HOSTED_CLOUD_TRUST': json.dumps(trust)})
            def http(path, *, method='GET', body=None, caller=False, display_token=None, status=200):
                headers = {'Content-Type': 'application/json'}
                if caller:
                    headers['Authorization'] = 'Bearer ' + self.owner_token.key
                if display_token:
                    headers['X-Nexus-Agent-Display-Token'] = display_token
                request = Request(origin + path, headers=headers, method=method,
                                  data=None if body is None else json.dumps(body).encode())
                try:
                    with opener(request, timeout=8) as response:
                        self.assertEqual(response.status, status)
                        value = json.loads(response.read())
                    return value.get('data', value) if isinstance(value, dict) else value
                except HTTPError as error:
                    with error:
                        self.assertEqual(error.code, status)
                        return None

            agent = Agent.objects.create(tenant=self.row.tenant, project=self.row.project,
                created_by=self.row.owner, name='TLS callback assistant', status='active')
            node = EdgeNode.objects.create(tenant=self.row.tenant, router_id='tls-fixture',
                domain_id='fixture.invalid', device_token_hash='a' * 64)
            registration = EdgeAgentRegistration.objects.create(node=node, agent=agent,
                origin='agent://fixture/tls', route_id='tls-fixture',
                lease_expires_at=timezone.now() + timedelta(minutes=5), last_renewed_at=timezone.now())
            deployment = AgentRuntimeDeployment.objects.create(tenant=self.row.tenant, project=self.row.project,
                agent=agent, runtime_kind=AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6,
                edge_registration=registration, status='active', health_status='healthy')
            request = SimpleNamespace(user=self.row.owner, tenant_id=str(self.row.tenant_id),
                project_id=str(self.row.project_id), META={}, headers={'Accept': 'text/event-stream'},
                query_params={}, build_absolute_uri=lambda path: origin + path)
            run, context = runtime_services.create_invocation_display_context(
                request=request, runtime=deployment, tool_name='echo')
            outbound = prepare_openwrt_run_context_headers(deployment=deployment,
                headers=runtime_services.runtime_headers_with_agui(headers={}, context=context))
            grant = AgentRunContextGrant.objects.get(run=run)
            self.assertNotIn('X-Nexus-AGUI-Token', outbound)
            exchange_url, token = outbound['X-Nexus-Run-Context-Url'], outbound['X-Nexus-Run-Context-Token']
            untrusted = hosted_cloud_opener({'NEXUS_HOSTED_CLOUD_TRUST': json.dumps(
                {'schema_version': 1, 'mode': 'system', 'origins': [origin]})})
            with self.assertRaises(HostedCloudTrustError) as rejected:
                NexusRunContext.from_exchange(exchange_url, token, cloud_opener=untrusted)
            self.assertEqual(rejected.exception.code, 'RUN_CONTEXT_TLS_FAILED')
            hostname_origin = origin.replace('127.0.0.1', 'localhost')
            hostname_opener = hosted_cloud_opener({'NEXUS_HOSTED_CLOUD_TRUST': json.dumps(
                {**trust, 'origins': [hostname_origin]})})
            with self.assertRaises(HostedCloudTrustError) as rejected:
                NexusRunContext.from_exchange(exchange_url.replace(origin, hostname_origin), token,
                    cloud_opener=hostname_opener)
            self.assertEqual(rejected.exception.code, 'RUN_CONTEXT_TLS_FAILED')
            with self.assertRaises(HostedCloudTrustError) as rejected:
                opener(Request(hostname_origin + '/'), timeout=3)
            self.assertEqual(rejected.exception.code, 'RUN_CONTEXT_ORIGIN_MISMATCH')
            grant.refresh_from_db()
            self.assertIsNone(grant.redeemed_at, 'TLS failure must not consume the one-time grant')
            with self.assertRaises(NexusRunContextExchangeError):
                NexusRunContext.from_exchange(exchange_url, 'invalid', cloud_opener=opener)
            sdk = NexusRunContext.from_exchange(exchange_url, token, cloud_opener=opener,
                config=NexusReportingConfig(request_timeout=8, flush_timeout=10, max_retries=0))
            try:
                grant.refresh_from_db()
                self.assertIsNotNone(grant.redeemed_at)
                self.assertEqual(sdk.run_id, str(run.pk))
                with self.assertRaises(NexusRunContextExchangeError):
                    NexusRunContext.from_exchange(exchange_url, token, cloud_opener=opener)
                self.assertTrue(sdk.plan.set([{'title': 'Verify TLS callbacks', 'status': 'completed'}]))
                self.assertTrue(sdk.chat.say('HTTPS 回调已连接'))
                report = sdk.flush(timeout=10)
                self.assertEqual((report.failed, report.pending, report.dropped), (0, 0, 0))
                base = f'/api/v1/agent-runs/{run.pk}/'
                display_token = http(base + 'display-token/', method='POST', body={}, caller=True)['display_token']
                http(base + 'display/', status=401)
                http(base + 'display/', caller=True, status=404)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    answer = pool.submit(sdk.chat.ask, '继续验收？', key='https-confirm', kind='confirm', timeout=12,
                        choices=[{'value': 'yes', 'label': '继续'}, {'value': 'no', 'label': '取消'}])
                    deadline = time.monotonic() + 10
                    while True:
                        display = http(base + 'display/', caller=True, display_token=display_token)
                        pending = [item for item in display['interactions'] if item['status'] == 'pending']
                        if pending:
                            break
                        if answer.done():
                            answer.result()
                            self.fail('Question returned without a pending interaction')
                        self.assertLess(time.monotonic(), deadline, 'HTTPS question did not reach caller display')
                        time.sleep(.05)
                    reply = base + f"interactions/{pending[0]['id']}/reply/"
                    http(reply, method='POST', body={'value': 'yes'}, caller=True, status=404)
                    http(reply, method='POST', body={'value': 'yes'}, caller=True, display_token=display_token)
                    self.assertEqual(answer.result(timeout=15).value, 'yes')
                events = http(base + 'events/', caller=True, display_token=display_token)
                plans = [item for item in events if item.get('type') == 'ACTIVITY_SNAPSHOT'
                         and item.get('activityType') == 'PLAN']
                self.assertEqual(plans[-1]['content']['steps'],
                    [{'id': 'step-1', 'label': 'Verify TLS callbacks', 'state': 'done'}])
                first = http(base + 'display/', caller=True, display_token=display_token)
                self.assertEqual([item['content'] for item in first['messages']],
                    ['HTTPS 回调已连接\n\n继续验收？', 'yes'])
                self.assertEqual(first['messages'], http(base + 'display/', caller=True,
                    display_token=display_token)['messages'])
                self.assertEqual([item['content'] for item in sdk.run.messages(refresh=True)],
                                 [item['content'] for item in first['messages']])
                from apps.agents import file_transfers
                source_file = Path(self.folder.name) / 'https-result.txt'
                source_file.write_text('真实 HTTPS 文件产物\n', encoding='utf-8')
                with ThreadPoolExecutor(max_workers=1) as pool:
                    upload = pool.submit(sdk.output.upload_file, source_file, content_type='text/plain', timeout=15)
                    deadline = time.monotonic() + 15
                    while not upload.done():
                        # Existing durable finalizer, not a fake scan/runner.
                        file_transfers.process_one()
                        self.assertLess(time.monotonic(), deadline, 'HTTPS output did not finalize')
                        time.sleep(.05)
                    output = upload.result()
                self.assertEqual(output['state'], 'ready')
                download_path = base + f"outputs/{output['artifact_id']}/download/"
                http(download_path, caller=True, status=404)
                with opener(Request(origin + download_path, headers={
                        'Authorization': 'Bearer ' + self.owner_token.key,
                        'X-Nexus-Agent-Display-Token': display_token}), timeout=8) as response:
                    self.assertEqual(response.read(), source_file.read_bytes())
                for secret in (token, context.write_token, context.interaction_token, context.context_token):
                    self.assertNotIn(secret, json.dumps(first))
                runtime_services.finish_invocation_display_run(run=run, succeeded=True)
                self.assertEqual(http(base + 'display/', caller=True, display_token=display_token)['status'], 'completed')
            finally:
                sdk.close()
        if os.environ.get('NEXUS_COMBINED_EGRESS_CALLBACK_ACCEPTANCE') == '1':
            self._container_cloud_callbacks()

    def _container_cloud_callbacks(self):
        """Extend this same test application with the owned WSL kernel fixture."""
        from concurrent.futures import ThreadPoolExecutor
        from datetime import timedelta
        from urllib.request import Request
        from urllib.error import HTTPError
        from urllib.parse import urlsplit
        from unittest.mock import patch
        import hashlib
        import os
        import shlex
        import subprocess
        import time
        import nexus_agent
        import nexus_personal
        from django.utils import timezone
        from apps.agents import runtime_services, file_transfers
        from apps.agents.models import Agent, AgentRuntimeDeployment, EdgeNode, EdgeAgentRegistration, AgentRunContextGrant
        from apps.agents.runtime_context import prepare_openwrt_run_context_headers
        from nexus_agent.hosted_trust import hosted_cloud_opener
        from .mcp_http_fixture import cloud_https_fixture

        self.assertEqual(os.name, 'nt', 'Use the existing Windows/WSL integration host')
        distribution = 'NexusOpenWrtBuilder'
        harness = Path(os.environ['NEXUS_EGRESS_CALLBACK_HARNESS']).resolve(strict=True)
        self.assertEqual(harness.name, 'test_egress_policy.py')
        sdk_root = Path(nexus_agent.__file__).resolve().parents[1]
        bind_address = os.environ['NEXUS_EGRESS_CALLBACK_BIND_ADDRESS']
        child_environment = {key: value for key, value in os.environ.items() if key.upper() in
            {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA'}}
        def linux_path(path):
            result = subprocess.run(['wsl.exe', '-d', distribution, '-u', 'root', '--exec',
                'wslpath', '-u', str(path)], capture_output=True, text=True, encoding='utf-8',
                timeout=15, env=child_environment)
            self.assertEqual(result.returncode, 0, 'Cannot resolve owned WSL fixture path')
            return result.stdout.strip()
        linux_sdk, linux_harness = linux_path(sdk_root), linux_path(harness)
        linux_server = linux_path(Path(nexus_personal.__file__).resolve().parents[1])
        self.assertNotIn(',', linux_sdk)
        command = ['timeout', '-k', '5s', '210s', 'unshare', '--mount', '--propagation', 'private',
            '--pid', '--fork', '--mount-proc', '--kill-child', '--net', 'env', '-i',
            'PATH=/usr/sbin:/usr/bin:/sbin:/bin', 'NEXUS_RUN_EGRESS_NAMESPACE_TESTS=1',
            'NEXUS_EGRESS_DOCKER_TESTS=1', 'NEXUS_EGRESS_CLOUD_CALLBACKS=1',
            'NEXUS_EGRESS_CALLBACK_NAMESPACE_FD=9', 'NEXUS_EGRESS_CALLBACK_SDK=' + linux_sdk,
            'NEXUS_EGRESS_CALLBACK_SERVER_ROOT=' + linux_server]
        for name in ('NEXUS_EGRESS_TEST_TOOLS', 'NEXUS_EGRESS_DOCKER_IMAGE', 'NEXUS_EGRESS_DOCKER_IMAGE_SHA256'):
            command.append(name + '=' + os.environ[name])
        command += ['python3', '-B', linux_harness, '-v']
        shell_command = 'exec ' + shlex.join(command) + ' 9</proc/self/ns/net'
        virtual_origin = 'https://10.0.0.2:9443'
        with cloud_https_fixture(create_application(), bind_address=bind_address, isolated_callback=True) as (origin, trust), \
                patch.dict(os.environ, {'NO_PROXY': bind_address, 'no_proxy': bind_address}), override_settings(
                    ALLOWED_HOSTS=[bind_address, '10.0.0.2', 'testserver'], NEXUS_PUBLIC_BASE_URL=virtual_origin,
                    NEXUS_AGENT_CONTEXT_EXCHANGE_BASE_URL=virtual_origin, NEXUS_AGENT_DISPLAY_EVENTS_BASE_URL=virtual_origin,
                    NEXUS_DATASET_STORAGE_ROOT=str(Path(self.folder.name) / 'container-callback-objects')):
            opener = hosted_cloud_opener({'NEXUS_HOSTED_CLOUD_TRUST': json.dumps(trust)})
            agent = Agent.objects.create(tenant=self.row.tenant, project=self.row.project,
                created_by=self.row.owner, name='Container callback assistant', status='active')
            node = EdgeNode.objects.create(tenant=self.row.tenant, router_id='container-tls-fixture',
                domain_id='fixture.invalid', device_token_hash='b' * 64)
            registration = EdgeAgentRegistration.objects.create(node=node, agent=agent,
                origin='agent://fixture/container-tls', route_id='container-tls-fixture',
                lease_expires_at=timezone.now() + timedelta(minutes=5), last_renewed_at=timezone.now())
            deployment = AgentRuntimeDeployment.objects.create(tenant=self.row.tenant, project=self.row.project,
                agent=agent, runtime_kind=AgentRuntimeDeployment.RUNTIME_OPENWRT_IPV6,
                edge_registration=registration, status='active', health_status='healthy')
            request = SimpleNamespace(user=self.row.owner, tenant_id=str(self.row.tenant_id),
                project_id=str(self.row.project_id), META={}, headers={'Accept': 'text/event-stream'},
                query_params={}, build_absolute_uri=lambda path: virtual_origin + path)
            run, context = runtime_services.create_invocation_display_context(
                request=request, runtime=deployment, tool_name='echo')
            outbound = prepare_openwrt_run_context_headers(deployment=deployment,
                headers=runtime_services.runtime_headers_with_agui(headers={}, context=context))
            packet = {'upstream': {'address': bind_address, 'port': urlsplit(origin).port},
                'sdk_request': {'exchange_url': outbound['X-Nexus-Run-Context-Url'],
                    'exchange_token': outbound['X-Nexus-Run-Context-Token'], 'trust': trust, 'run_id': str(run.pk)}}
            base = f'/api/v1/agent-runs/{run.pk}/'
            display_token = None
            def http(path, *, body=None, protected=True, status=200):
                headers = {'Authorization': 'Bearer ' + self.owner_token.key, 'Content-Type': 'application/json'}
                if protected and display_token:
                    headers['X-Nexus-Agent-Display-Token'] = display_token
                try:
                    with opener(Request(origin + path, headers=headers,
                            data=None if body is None else json.dumps(body).encode()), timeout=8) as response:
                        self.assertEqual(response.status, status)
                        result = json.loads(response.read())
                        return result.get('data', result) if isinstance(result, dict) else result
                except HTTPError as error:
                    with error:
                        self.assertEqual(error.code, status)
                        return None
            display_token = http(base + 'display-token/', body={}, protected=False)['display_token']
            answered = set()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(subprocess.run, ['wsl.exe', '-d', distribution, '-u', 'root', '--exec',
                    'sh', '-c', shell_command], input=json.dumps(packet), env=child_environment,
                    capture_output=True, text=True, encoding='utf-8', timeout=230)
                deadline = time.monotonic() + 220
                while not future.done():
                    display = http(base + 'display/')
                    for question in display['interactions']:
                        if question['status'] == 'pending' and question['id'] not in answered:
                            self.assertEqual(question['kind'], 'confirm')
                            reply = base + f"interactions/{question['id']}/reply/"
                            http(reply, body={'value': 'yes'}, protected=False, status=404)
                            http(reply, body={'value': 'yes'})
                            answered.add(question['id'])
                    file_transfers.process_one()
                    self.assertLess(time.monotonic(), deadline, 'Owned container callback deadline exceeded')
                    time.sleep(.15)
                result = future.result()
            self.assertEqual(result.returncode, 0, 'Owned Docker callback harness failed: ' + result.stderr[-3000:])
            reports = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
            report, = [item for item in reports if item.get('container_sdk_callbacks') is True]
            self.assertTrue(any(item.get('egress_namespace_cleanup') is True for item in reports))
            self.assertTrue(any(item.get('isolated_docker_egress') is True and item.get('recovery') is True for item in reports))
            self.assertEqual(report['run_id'], str(run.pk))
            self.assertEqual(len(answered), 1)
            self.assertIsNotNone(AgentRunContextGrant.objects.get(run=run).redeemed_at)
            display = http(base + 'display/')
            self.assertEqual([item['content'] for item in display['messages']],
                ['容器内 HTTPS 回调已连接\n\n继续容器验收？', 'yes'])
            plans = [item for item in http(base + 'events/') if item.get('type') == 'ACTIVITY_SNAPSHOT'
                and item.get('activityType') == 'PLAN']
            self.assertEqual(plans[-1]['content']['steps'],
                [{'id': 'step-1', 'label': 'Verify container callbacks', 'state': 'done'}])
            download = base + f"outputs/{report['artifact_id']}/download/"
            http(download, protected=False, status=404)
            with opener(Request(origin + download, headers={'Authorization': 'Bearer ' + self.owner_token.key,
                    'X-Nexus-Agent-Display-Token': display_token}), timeout=8) as response:
                content = response.read()
            self.assertEqual(content, '真实容器 HTTPS 文件产物\n'.encode('utf-8'))
            self.assertEqual(hashlib.sha256(content).hexdigest(), report['sha256'])
            runtime_services.finish_invocation_display_run(run=run, succeeded=True)
            self.assertEqual(http(base + 'display/')['status'], 'completed')
            for secret in (packet['sdk_request']['exchange_token'], context.write_token, context.context_token):
                self.assertNotIn(secret, result.stdout + result.stderr + json.dumps(display))
            print('restricted-container-cloud-callbacks-and-cleanup-ok', flush=True)

    def test_authenticated_asgi_monitoring_job_lineage_and_real_smtp_outbox(self):
        # Reuse this committed ASGI host and the existing bounded SMTP peer.
        # This is not an external HTTP listener or browser-rendering acceptance.
        from apps.gateway.models import GatewayRequestLog
        from apps.jobs.services import create_job, start_job, succeed_job
        from apps.metrics.models import AlertEvent, AlertNotification, AlertRule
        from nexus_personal import monitoring_workers

        request = self.request()
        request.tenant_id = str(self.row.tenant_id)
        request.project_id = str(self.row.project_id)
        request.request_id = "monitoring-bridge-seed"
        job = create_job(request=request, project=self.row.project, job_type="agents.runtime.invoke",
            resource_type="agent_runtime_invocation", resource_id="monitoring-bridge",
            input_json={"api_key": "fixture-monitor-secret", "prompt": "fixture-private-prompt"})
        start_job(job_id=job.pk, celery_task_id="fixture-private-worker")
        succeed_job(job_id=job.pk, result_json={"access_token": "fixture-monitor-secret"})
        GatewayRequestLog.objects.create(tenant=self.row.tenant, project_id=str(self.row.project_id),
            model="monitoring-bridge", status="success", total_tokens=64,
            request_id="monitoring-bridge-request")
        application = create_application()

        async def run():
            async def http(method, url, *, body=None, status=200, authenticated=True):
                path, _, query = url.partition("?")
                payload = b"" if body is None else json.dumps(body).encode()
                headers = [(b"host", b"testserver"), (b"content-type", b"application/json"),
                           (b"x-request-id", b"monitoring-bridge-http"),
                           (b"content-length", str(len(payload)).encode())]
                if authenticated:
                    headers.append((b"authorization", ("Bearer " + self.owner_token.key).encode()))
                communicator = ApplicationCommunicator(application, {
                    "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                    "method": method, "path": path, "raw_path": path.encode(),
                    "query_string": query.encode(), "scheme": "http", "headers": headers,
                    "server": ("testserver", 80), "client": ("127.0.0.1", 12345),
                })
                await communicator.send_input({"type": "http.request", "more_body": False,
                    "body": payload})
                start = await communicator.receive_output(timeout=8)
                self.assertEqual(start["type"], "http.response.start")
                self.assertEqual(start["status"], status)
                content = bytearray()
                while True:
                    chunk = await communicator.receive_output(timeout=8)
                    self.assertEqual(chunk["type"], "http.response.body")
                    content.extend(chunk.get("body", b""))
                    self.assertLessEqual(len(content), 262144)
                    if not chunk.get("more_body", False):
                        break
                await communicator.wait(timeout=8)
                for secret in (self.owner_token.key, "fixture-monitor-secret",
                               "fixture-private-prompt", "fixture-private-worker"):
                    self.assertNotIn(secret.encode(), content)
                if status >= 400:
                    return None
                response_headers = {key.lower(): value for key, value in start["headers"]}
                self.assertEqual(response_headers[b"x-request-id"], b"monitoring-bridge-http")
                if path.startswith("/api/v1/metrics/"):
                    self.assertEqual(response_headers[b"cache-control"], b"private, no-store")
                return json.loads(content)

            await http("GET", "/api/v1/metrics/system/", authenticated=False, status=401)
            capabilities = await http("GET", "/api/v1/metrics/capabilities/")
            self.assertFalse(capabilities["financial"])
            self.assertFalse(capabilities["reports"])
            system = await http("GET", "/api/v1/metrics/system/")
            self.assertEqual(system["usage"], {"requests": 1, "tokens": 64})
            self.assertNotIn("amount", system["usage"])
            jobs = await http("GET", "/api/v1/metrics/jobs/?limit=5&resource_id=monitoring-bridge")
            self.assertEqual([row["id"] for row in jobs["items"]], [str(job.pk)])
            detail = await http("GET", f"/api/v1/metrics/jobs/{job.pk}/")
            self.assertEqual(detail["status"], "succeeded")
            self.assertEqual(detail["input_json"], {})
            self.assertEqual(detail["result_json"], {})
            events = await http("GET", f"/api/v1/metrics/jobs/{job.pk}/events/")
            ordered = sorted(events, key=lambda row: row["created_at"])
            self.assertEqual([row["event_type"] for row in ordered], ["queued", "running", "succeeded"])
            self.assertTrue(all(row["metadata_json"] == {} for row in events))
            lineage = await http("GET", "/api/v1/metrics/requests/monitoring-bridge-request/")
            self.assertEqual([row["kind"] for row in lineage["items"]], ["gateway"])
            rule = await http("POST", "/api/v1/alerts/", status=201, body={
                "metric": "system.usage.requests", "threshold": "1", "operator": "gte",
                "window_seconds": 60, "cooldown_seconds": 0,
                "notification_channels": {"emails": ["monitoring-bridge@example.test"]}})
            event = await http("POST", f"/api/v1/alerts/{rule['id']}/test/", status=201)
            self.assertTrue(event["is_test"])
            self.assertEqual(event["notifications"][0]["delivery_status"], "pending")
            self.assertEqual((await sync_to_async(monitoring_workers.deliver_notifications,
                thread_sensitive=True)())["sent"], 1)
            self.assertEqual((await sync_to_async(monitoring_workers.deliver_notifications,
                thread_sensitive=True)())["sent"], 0)
            delivered = await http("GET", f"/api/v1/alerts/events/{event['id']}/")
            self.assertEqual(delivered["notifications"][0]["delivery_status"], "sent")
            audit = await http("GET", "/api/v1/metrics/audit/?limit=10&request_id=monitoring-bridge-http")
            self.assertTrue({"alert.create", "metrics.alert.test"} <= {row["action"] for row in audit["items"]})
            await http("POST", "/api/v1/alerts/", status=400,
                body={"metric": "system.usage.amount", "threshold": "1"})
            await http("GET", "/api/v1/reports/schedules/", status=404)

        with SMTPFixture() as smtp, override_settings(
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="127.0.0.1",
            EMAIL_PORT=smtp.server_address[1], EMAIL_HOST_USER="", EMAIL_HOST_PASSWORD="",
            EMAIL_USE_TLS=False, EMAIL_USE_SSL=False, EMAIL_TIMEOUT=5):
            async_to_sync(run)()
            self.assertEqual(len(smtp.messages), 1)
            self.assertIn(b"monitoring-bridge@example.test", smtp.messages[0])
            self.assertNotIn(b"fixture-monitor-secret", smtp.messages[0])
        self.assertEqual(AlertRule.objects.count(), 1)
        self.assertEqual(AlertEvent.objects.count(), 1)
        notification = AlertNotification.objects.get()
        self.assertEqual(notification.delivery_status, "sent")
        self.assertEqual(notification.attempts, 1)
        self.assertIsNotNone(notification.sent_at)

    def test_authenticated_asgi_queue_executes_real_sdk_files_and_command(self):
        # Same executor for checkout tests and explicitly installed wheel tests.
        use_sdk(self.addCleanup)
        from nexus_agent.computer_runtime import NexusComputerRuntime, RuntimeOperationError
        computer = NexusComputerRuntime(root=Path(self.folder.name) / "runtime-state")
        self.addCleanup(computer.close)
        # Use the actual SDK file implementation, but never touch this user's
        # real ~/.codex. Only the path resolver points into this test fixture.
        from unittest.mock import patch
        codex_path = Path(self.folder.name) / "codex-home" / "nexus.config.toml"
        isolated_codex = patch.object(computer, "_codex_path", return_value=codex_path)
        isolated_codex.start()
        self.addCleanup(isolated_codex.stop)
        root = Path(self.folder.name) / "workspace"
        root.mkdir()
        computer.config = {"workspace_root": str(root)}
        storage = override_settings(NEXUS_DATASET_STORAGE_ROOT=str(Path(self.folder.name) / "objects"))
        storage.enable()
        self.addCleanup(storage.disable)
        device, key = self.pair()
        device.capabilities = {**device.capabilities, "tool_setup.v1": 1, "tool_setup.cas.v1": 1, "tool_setup.cas.v2":1}
        computer.config['device_id'] = str(device.pk)
        device.save(update_fields=["capabilities"])
        device.connection.workspace_root = str(root)
        device.connection.save(update_fields=["workspace_root"])
        ticket = self.ticket(device, key)
        application = create_application()

        async def run():
            async def http_get(path, authenticated=False):
                headers = [(b"host", b"testserver")]
                if authenticated:
                    headers.append((b"authorization", ("Bearer " + self.owner_token.key).encode()))
                http = ApplicationCommunicator(application, {
                    "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                    "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
                    "scheme": "http", "headers": headers, "server": ("testserver", 80),
                    "client": ("127.0.0.1", 12345),
                })
                await http.send_input({"type": "http.request", "body": b"", "more_body": False})
                start = await http.receive_output(timeout=8)
                self.assertEqual(start["type"], "http.response.start")
                self.assertEqual(start["status"], 200)
                chunks = []
                while True:
                    body = await http.receive_output(timeout=8)
                    self.assertEqual(body["type"], "http.response.body")
                    chunks.append(body.get("body", b""))
                    if not body.get("more_body", False):
                        break
                await http.wait(timeout=8)
                return json.loads(b"".join(chunks))

            bootstrap = await http_get("/api/v1/public/bootstrap/")
            self.assertEqual(bootstrap["distribution"], "community")
            self.assertFalse(bootstrap["session_authenticated"])
            account = await http_get("/api/v1/auth/whoami/", authenticated=True)
            self.assertEqual(account["user_id"], str(self.row.owner_id))
            communicator = ApplicationCommunicator(application, {
                "type": "websocket", "path": "/ws/computer-runtime/v1/connect/",
                "headers": [(b"authorization", ("Bearer " + ticket).encode())],
            })

            async def frame_of_type(kind):
                for _ in range(20):
                    event = await communicator.receive_output(timeout=8)
                    self.assertEqual(event["type"], "websocket.send")
                    frame = json.loads(event["text"])
                    self.assertNotEqual(frame.get("type"), "protocol_error", frame.get("code"))
                    if frame.get("type") == kind:
                        return frame
                self.fail("Expected Runtime frame was not received")

            sequence = 1
            async def callback_roundtrip(callback, *, expected_commands, before_dispatch=None, lose_write_receipt=False, **kwargs):
                nonlocal sequence
                def invoke():
                    try:
                        return callback(**kwargs)
                    finally:
                        connections.close_all()
                pending = asyncio.create_task(sync_to_async(invoke, thread_sensitive=False)())
                try:
                    for _ in range(expected_commands):
                        receiving = asyncio.create_task(frame_of_type("command"))
                        done, _ = await asyncio.wait([pending, receiving], return_when=asyncio.FIRST_COMPLETED)
                        if pending in done:
                            receiving.cancel()
                            await asyncio.gather(receiving, return_exceptions=True)
                            pending.result()  # Surface delegate errors, not a socket timeout.
                            self.fail("Callback returned without dispatching its required command")
                        wire = await receiving
                        if before_dispatch:
                            await asyncio.to_thread(before_dispatch, wire)
                        try:
                            result = await asyncio.to_thread(computer.dispatch,
                                wire["operation"], wire["required_scope"], wire["payload"])
                            response = {"type": "result", "result": result}
                            if lose_write_receipt and wire['operation'] == 'tool_setup.write_codex_config_fenced':
                                # Fault injection after actual SDK disk I/O: the
                                # success receipt is lost, not a fake runner.
                                response = {'type':'error', 'code':'COMPUTER_RUNTIME_CONNECTION_RESET',
                                    'message':'The write acknowledgement was lost.'}
                        except RuntimeOperationError as exc:
                            response = {"type": "error", "code": exc.code, "message": str(exc)}
                        sequence += 1
                        await communicator.send_input({"type": "websocket.receive", "text": json.dumps({
                            "version": 1, "sequence": sequence, "command_id": wire["command_id"],
                            **response})})
                        await frame_of_type("frame_ack")
                    return await asyncio.wait_for(asyncio.shield(pending), timeout=40)
                finally:
                    # Sync database work cannot be cancelled by cancelling an
                    # asyncio wrapper. Let it finish and close its connection
                    # before removing the test database or isolated directory.
                    if not pending.done():
                        await asyncio.wait_for(asyncio.shield(pending), timeout=40)

            def prepare_attached_run():
                from apps.agents.models import Agent, AgentRuntimeImage, AgentRuntimeDeployment
                from apps.agents import services, runtime_services, workspace_grants
                request = SimpleNamespace(user=self.row.owner, tenant_id=str(self.row.tenant_id),
                    project_id=str(self.row.project_id), META={}, headers={}, query_params={},
                    build_absolute_uri=lambda path: "https://personal.example" + path)
                scopes = ["files.list", "files.read", "files.write", "command.execute"]
                agent = Agent.objects.create(tenant=self.row.tenant, project=self.row.project,
                    created_by=self.row.owner, name="Attached bridge", status="active",
                    computer_requirement="required", workspace_capabilities=scopes)
                image = AgentRuntimeImage.objects.create(tenant=self.row.tenant, project=self.row.project,
                    agent=agent, image_ref="attached-bridge:unit")
                deployment = AgentRuntimeDeployment.objects.create(tenant=self.row.tenant, project=self.row.project,
                    agent=agent, image=image, status="active", health_status="healthy")
                from rest_framework.test import APIClient
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION="Bearer " + self.owner_token.key)
                granted = client.put(f"/api/v1/agents/{agent.pk}/workspace-grant/", {"scopes": scopes}, format="json")
                self.assertEqual(granted.status_code, 200, granted.data)
                attached = client.post(f"/api/v1/agents/{agent.pk}/computer-bindings/",
                    {"connection_id": str(device.connection_id)}, format="json")
                self.assertEqual(attached.status_code, 201, attached.data)
                run_record, context = runtime_services.create_invocation_display_context(
                    request=request, runtime=deployment, tool_name="echo")
                return run_record, context, request

            async def roundtrip(operation, scope, payload, error_code=None):
                nonlocal sequence
                # Model instances from enrollment cache pre-hello presence.
                # Like a new delegate request, resolve current persisted state.
                connection = await sync_to_async(WorkspaceConnection.objects.get)(pk=device.connection_id)
                command = await sync_to_async(runtime.enqueue_runtime_command)(
                    connection=connection, operation=operation, required_scope=scope,
                    payload={"workspace_root": str(root), **payload}, timeout_seconds=10)
                wire = await frame_of_type("command")
                self.assertEqual(wire["command_id"], str(command.pk))
                self.assertEqual(wire["required_scope"], scope)
                try:
                    result = await asyncio.to_thread(computer.dispatch, wire["operation"], wire["required_scope"], wire["payload"])
                    self.assertIsNone(error_code)
                    response = {"type": "result", "result": result}
                except RuntimeOperationError as exc:
                    self.assertEqual(exc.code, error_code)
                    response = {"type": "error", "code": exc.code, "message": str(exc)}
                sequence += 1
                await communicator.send_input({"type": "websocket.receive", "text": json.dumps({
                    "version": 1, "sequence": sequence, "command_id": str(command.pk), **response})})
                await frame_of_type("frame_ack")
                if error_code:
                    with self.assertRaises(runtime.ComputerRuntimeError) as raised:
                        await sync_to_async(runtime.wait_for_runtime_command)(command, timeout_seconds=1)
                    self.assertEqual(raised.exception.default_code, error_code)
                    return None
                return await sync_to_async(runtime.wait_for_runtime_command)(command, timeout_seconds=1)

            try:
                await communicator.send_input({"type": "websocket.connect"})
                self.assertEqual((await communicator.receive_output(timeout=8))["type"], "websocket.accept")
                await frame_of_type("connected")
                await communicator.send_input({"type": "websocket.receive", "text": json.dumps({
                    "version": 1, "sequence": sequence, "type": "hello", "capabilities": device.capabilities})})
                await frame_of_type("hello_ack")
                text = "真实 SDK 文件 · 中文\n"
                written = await roundtrip("workspace.write_file", "files.write", {"path": "nested/input.txt", "content": text})
                self.assertEqual(written["size_bytes"], len(text.encode()))
                read = await roundtrip("workspace.read_file", "files.read", {"path": "nested/input.txt"})
                self.assertEqual(read["content"], text)
                result = await roundtrip("command.execute", "command.execute", {
                    "cwd": "nested", "command": "echo nexus-personal-bridge-probe", "timeout_seconds": 5})
                self.assertEqual(result["exit_code"], 0)
                self.assertIn("nexus-personal-bridge-probe", result["stdout"])
                await roundtrip("workspace.write_file", "files.read", {"path": "denied.txt", "content": "denied"}, "COMPUTER_SCOPE_MISMATCH")
                await roundtrip("workspace.write_file", "files.write", {"path": "../escape.txt", "content": "denied"}, "WORKSPACE_PATH_OUTSIDE_ROOT")
                self.assertFalse((root / "denied.txt").exists())
                self.assertFalse((root.parent / "escape.txt").exists())
                self.assertEqual((root / "nested/input.txt").read_text(encoding="utf-8"), text)
                from apps.workspaces import tool_codec
                tool_request = SimpleNamespace(build_absolute_uri=lambda path: "https://personal.example" + path)
                original_config = {"mcp_servers": {"external": {"url": "https://external.example/mcp"}}}
                tool_config = tool_codec._configure_router_api_with_token(request=tool_request,
                    config=original_config, router=None, models=["selected-model"], token="codec-redaction-sentinel")
                config_text = tool_codec.dump_toml(tool_config)
                await roundtrip("tool_setup.write_codex_config", "tool.setup", {"content": config_text})
                tool_read = await roundtrip("tool_setup.read_codex_config", "tool.setup", {})
                self.assertEqual(tool_codec.parse_codex_toml(tool_read["content"]), tool_config)
                self.assertEqual(codex_path.read_text(encoding="utf-8"), config_text)
                await roundtrip("tool_setup.backup_codex_config", "tool.setup", {"content": config_text})
                await roundtrip("tool_setup.write_codex_config", "tool.setup", {"content": "model = 'changed'\n"})
                restored = await roundtrip("tool_setup.rollback_codex_config", "tool.setup", {})
                self.assertEqual(tool_codec.parse_codex_toml(restored["content"]), tool_config)
                public_summary = tool_codec.codex_config_summary(path="nexus.config.toml", exists=True,
                    content=restored["content"], parsed=tool_config)
                self.assertNotIn("codec-redaction-sentinel", str(public_summary))
                await roundtrip("tool_setup.write_codex_config", "files.write", {"content": "denied"}, "COMPUTER_SCOPE_MISMATCH")
                self.assertEqual(codex_path.read_text(encoding="utf-8"), config_text)
                # Same real queue/SDK path for opt-in CAS; never call a legacy
                # write with an expected_revision that an old Runtime ignores.
                replacement = config_text + '\n# revision-checked\n'
                await roundtrip('tool_setup.write_codex_config_cas', 'tool.setup', {
                    'content': replacement, 'expected_revision': tool_codec.tool_config_revision(config_text)})
                await roundtrip('tool_setup.write_codex_config_cas', 'tool.setup', {
                    'content': config_text, 'expected_revision': tool_codec.tool_config_revision(config_text)}, 'TOOL_CONFIG_CONFLICT')
                self.assertEqual(codex_path.read_text(encoding='utf-8'), replacement)

                def tool_session():
                    from apps.workspaces.models import WorkspaceTerminalSession
                    return WorkspaceTerminalSession.objects.create(tenant=self.row.tenant,
                        project=self.row.project, created_by=self.row.owner, connection=device.connection, status='active')
                active_tool_session = await sync_to_async(tool_session)()
                def tool_http(method='get', suffix='', data=None, status=200):
                    from rest_framework.test import APIClient
                    client = APIClient()
                    client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.owner_token.key)
                    url = f'/api/v1/workspace-terminal-sessions/{active_tool_session.pk}/tool-config/' + suffix
                    response = getattr(client, method)(url, data or {}, format='json')
                    self.assertEqual(response.status_code, status, response.data)
                    self.assertNotIn('codec-redaction-sentinel', str(response.data))
                    self.assertNotIn('experimental_bearer_token = "codec', str(response.data))
                    return response.data
                inspected = await callback_roundtrip(tool_http, expected_commands=1)
                self.assertEqual(inspected['revision'], tool_codec.tool_config_revision(replacement))
                self.assertNotIn('content', inspected)
                self.assertTrue(inspected['write_availability']['available'])
                self.assertEqual(inspected['mcp_servers'][0]['name'], 'external')
                options = await callback_roundtrip(tool_http, expected_commands=0, suffix='options/')
                self.assertEqual(options['routers'][0]['id'], str(self.tool_router.pk))
                review_data = {'section':'api', 'action':'set_router', 'router_id':str(self.tool_router.pk)}
                review = await callback_roundtrip(tool_http, expected_commands=1, method='post',
                    suffix='preview/', data=review_data)
                self.assertTrue(review['can_apply'])
                self.assertEqual(review['credential_action'], 'create')
                await callback_roundtrip(tool_http, expected_commands=1, method='post', suffix='preview/',
                    data={**review_data, 'expected_revision':'a'*64}, status=409)
                from nexus_personal.models import PersonalRouterCredential
                self.assertFalse(await sync_to_async(PersonalRouterCredential.objects.exists)())
                self.assertEqual(codex_path.read_text(encoding='utf-8'), replacement)
                apply_data = {**review_data, 'expected_revision': review['revision']}
                applied = await callback_roundtrip(tool_http, expected_commands=4, method='post', suffix='apply-v2/', data=apply_data)
                self.assertEqual(applied['api']['status'], 'ready')
                self.assertEqual(applied['operation']['state'], 'applied')
                stored = tool_codec.parse_codex_toml(codex_path.read_text(encoding='utf-8'))
                self.assertEqual(stored['mcp_servers'], original_config['mcp_servers'])
                token = tool_codec._provider_token(stored)
                self.assertNotIn(token, str(applied))
                self.assertTrue(token.startswith('np-router-'))
                def invoke_scoped(value, expected):
                    from rest_framework.test import APIClient
                    client = APIClient()
                    client.credentials(HTTP_AUTHORIZATION='Bearer ' + value)
                    response = client.post('/api/v1/openai/v1/chat/completions', {
                        'model':stored['model'], 'messages':[{'role':'user','content':'Tool Setup verified request'}]}, format='json')
                    self.assertEqual(response.status_code, expected, response.data)
                await sync_to_async(invoke_scoped)(token, 200)
                replayed = await callback_roundtrip(tool_http, expected_commands=1, method='post', suffix='apply-v2/', data=apply_data)
                self.assertEqual(replayed['operation']['id'], applied['operation']['id'])
                self.assertEqual(await sync_to_async(PersonalRouterCredential.objects.count)(), 1)
                reused = await callback_roundtrip(tool_http, expected_commands=4, method='post', suffix='apply-v2/',
                    data={**review_data, 'expected_revision':applied['revision']})
                self.assertEqual(reused['operation']['credential_action'], 'reused')
                self.assertEqual(await sync_to_async(PersonalRouterCredential.objects.count)(), 1)
                rotated = await callback_roundtrip(tool_http, expected_commands=4, method='post', suffix='apply-v2/',
                    data={**review_data, 'action':'rotate_api_credential', 'expected_revision':reused['revision']})
                new_token = tool_codec._provider_token(tool_codec.parse_codex_toml(codex_path.read_text(encoding='utf-8')))
                self.assertNotEqual(new_token, token)
                self.assertNotIn(new_token, str(rotated))
                self.assertEqual(await sync_to_async(PersonalRouterCredential.objects.count)(), 2)
                await sync_to_async(invoke_scoped)(new_token, 200)
                await sync_to_async(invoke_scoped)(token, 401)
                before_conflict = codex_path.read_text(encoding='utf-8')
                def external_editor(wire):
                    if wire['operation'] == 'tool_setup.write_codex_config_fenced':
                        # Actual SDK file write representing a non-cooperating
                        # local editor, not a mocked Computer runner/result.
                        computer.dispatch('tool_setup.write_codex_config','tool.setup',
                            {'content':before_conflict + '\n# local user edit\n'})
                conflict_data = {**review_data,'action':'rotate_api_credential','expected_revision':rotated['revision']}
                await callback_roundtrip(tool_http,expected_commands=2,method='post',suffix='apply-v2/',
                    data=conflict_data,status=409,before_dispatch=external_editor)
                self.assertEqual(codex_path.read_text(encoding='utf-8'),before_conflict+'\n# local user edit\n')
                await sync_to_async(invoke_scoped)(new_token,200)
                count = await sync_to_async(PersonalRouterCredential.objects.count)()
                await callback_roundtrip(tool_http,expected_commands=0,method='post',suffix='apply-v2/',data=conflict_data,status=409)
                self.assertEqual(await sync_to_async(PersonalRouterCredential.objects.count)(),count)
                self.assertEqual(await sync_to_async(PersonalRouterCredential.objects.filter(revoked_at__isnull=True).count)(),1)
                from nexus_personal.models import PersonalToolConfigOperation
                unconfirmed_before = codex_path.read_text(encoding='utf-8')
                await callback_roundtrip(tool_http, expected_commands=2, method='post', suffix='apply-v2/',
                    data={**review_data,'action':'rotate_api_credential','expected_revision':tool_codec.tool_config_revision(unconfirmed_before)},
                    status=409, lose_write_receipt=True)
                pending_op = await sync_to_async(lambda: PersonalToolConfigOperation.objects.get(active=True))()
                inspected = await callback_roundtrip(tool_http, expected_commands=1)
                self.assertTrue(inspected['recovery']['available'])
                self.assertEqual(inspected['recovery']['operation_id'],str(pending_op.pk))
                recovered = await callback_roundtrip(tool_http, expected_commands=3, method='post', suffix='recovery/',
                    data={'operation_id':str(pending_op.pk),'expected_revision':inspected['revision'],'action':'recover'})
                self.assertEqual(recovered['operation']['state'],'applied')
                recovered_token = tool_codec._provider_token(tool_codec.parse_codex_toml(codex_path.read_text(encoding='utf-8')))
                await sync_to_async(invoke_scoped)(recovered_token,200)
                await sync_to_async(invoke_scoped)(new_token,401)
                new_token = recovered_token
                stable_config = codex_path.read_text(encoding='utf-8')
                await callback_roundtrip(tool_http, expected_commands=2, method='post', suffix='apply-v2/',
                    data={**review_data,'action':'rotate_api_credential','expected_revision':tool_codec.tool_config_revision(stable_config)},
                    status=409, lose_write_receipt=True)
                pending_op = await sync_to_async(lambda: PersonalToolConfigOperation.objects.get(active=True))()
                pending_config = codex_path.read_text(encoding='utf-8')
                restored = await callback_roundtrip(tool_http, expected_commands=4, method='post', suffix='recovery/',
                    data={'operation_id':str(pending_op.pk),'expected_revision':tool_codec.tool_config_revision(pending_config),'action':'restore_previous'})
                self.assertEqual(restored['operation']['state'],'rolled_back')
                self.assertEqual(codex_path.read_text(encoding='utf-8'),stable_config)
                self.assertNotIn(new_token,str(restored))
                await sync_to_async(invoke_scoped)(new_token,200)
                self.assertEqual(tool_codec.parse_codex_toml(stable_config)['mcp_servers'],original_config['mcp_servers'])
                from apps.agents import runtime_services as callbacks, workspace_grants
                from apps.agents.models import AgentDisplayRun
                from apps.workspaces.models import ComputerRuntimeCommand, WorkspaceTerminalSession
                from rest_framework import exceptions
                run_record, context, request = await sync_to_async(prepare_attached_run)()
                delegated = {"run_id": str(run_record.pk), "token": context.workspace_delegate_token}
                def delegate_http(*, method, suffix, data, expected_status, token=None):
                    from rest_framework.test import APIClient
                    client = APIClient()
                    headers = {"HTTP_X_NEXUS_WORKSPACE_DELEGATE_TOKEN":
                        context.workspace_delegate_token if token is None else token}
                    url = f"/api/v1/internal/agent-runs/{run_record.pk}/{suffix}/"
                    response = getattr(client, method)(url, data, format="json", **headers)
                    self.assertEqual(response.status_code, expected_status, response.data)
                    return response.data
                written = await callback_roundtrip(delegate_http, expected_commands=1,
                    method="post", suffix="workspace", expected_status=201,
                    data={"path": "nested/input.txt", "content": text, "idempotency_key": "attached-file-1"})
                self.assertEqual(written["size_bytes"], len(text.encode()))
                def computer_http(method="get", path="", expected_status=200):
                    from rest_framework.test import APIClient
                    client = APIClient()
                    client.credentials(HTTP_AUTHORIZATION="Bearer " + self.owner_token.key)
                    base = f"/api/v1/agent-runs/{run_record.pk}/"
                    issued = client.post(base + "display-token/", {}, format="json")
                    self.assertEqual(issued.status_code, 200, issued.data)
                    headers = {"HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN": issued.data["display_token"]}
                    response = getattr(client, method)(base + "computer/", {"path": path}, format="json", **headers)
                    self.assertEqual(response.status_code, expected_status, response.data)
                    return response.data
                folders = await callback_roundtrip(computer_http, expected_commands=1)
                self.assertEqual(folders["cwd"], ".")
                self.assertIn({"name": "nested", "path": "nested"}, folders["directories"])
                switched = await callback_roundtrip(computer_http, expected_commands=2, method="patch", path="nested")
                self.assertEqual(switched["cwd"], "nested")
                self.assertEqual(await sync_to_async(lambda: AgentDisplayRun.objects.get(pk=run_record.pk).status)(), "running")
                await callback_roundtrip(computer_http, expected_commands=0, method="patch", path="../escape", expected_status=400)
                read = await callback_roundtrip(delegate_http, expected_commands=1,
                    method="get", suffix="workspace", expected_status=200,
                    data={"operation": "read", "path": "input.txt"})
                self.assertEqual(read["content"], text)
                command = await callback_roundtrip(delegate_http, expected_commands=2,
                    method="post", suffix="terminal", expected_status=200,
                    data={"command": "echo attached-callback-command", "cwd": ".", "timeout_seconds": 5,
                        "idempotency_key": "attached-command-1"})
                self.assertEqual(command["exit_code"], 0)
                self.assertIn("attached-callback-command", command["stdout"])
                transcript = await sync_to_async(lambda: list(WorkspaceTerminalSession.objects.get(
                    display_run=run_record).transcript.values_list("kind", "data")))()
                self.assertTrue(any("attached-callback-command" in data for _, data in transcript))
                rows = await sync_to_async(lambda: list(ComputerRuntimeCommand.objects.filter(
                    display_run_id=run_record.pk).values("caller_subject_hash", "status")))()
                self.assertEqual(len(rows), 4)
                self.assertTrue(all(row["caller_subject_hash"] == run_record.caller_subject_hash and row["status"] == "succeeded" for row in rows))
                await callback_roundtrip(delegate_http, expected_commands=0,
                    method="get", suffix="workspace", expected_status=400,
                    data={"operation": "read", "path": "../../escape.txt"})
                output = await callback_roundtrip(delegate_http, expected_commands=1,
                    method="post", suffix="workspace", expected_status=201,
                    data={"path": "result.txt", "root": "output", "content": text, "idempotency_key": "attached-output-1"})
                self.assertEqual(output["size_bytes"], len(text.encode()))
                artifact = await sync_to_async(lambda: run_record.output_artifacts.get(workspace_path="result.txt"))()
                self.assertEqual(artifact.snapshot_status, "ready")
                from apps.datasets.storage_backends import get_dataset_storage_backend
                def read_snapshot():
                    with get_dataset_storage_backend(artifact.snapshot_storage_backend).open(object_key=artifact.snapshot_object_key) as stream:
                        return stream.read()
                self.assertEqual(await sync_to_async(read_snapshot)(), text.encode())
                def read_display_http():
                    from rest_framework.test import APIClient
                    client = APIClient()
                    client.credentials(HTTP_AUTHORIZATION="Bearer " + self.owner_token.key)
                    base = f"/api/v1/agent-runs/{run_record.pk}/"
                    response = client.post(base + "display-token/", {}, format="json")
                    self.assertEqual(response.status_code, 200, response.data)
                    headers = {"HTTP_X_NEXUS_AGENT_DISPLAY_TOKEN": response.data["display_token"]}
                    event = APIClient().post(f"/api/v1/internal/display/runs/{run_record.pk}/events/", {
                        "type": "CUSTOM", "name": "nexus.plan", "value": {
                            "steps": [{"title": "Attached Computer", "status": "completed"}]}},
                        format="json", HTTP_X_NEXUS_AGUI_TOKEN=context.write_token)
                    self.assertEqual(event.status_code, 201, event.data)
                    display = client.get(base + "display/", **headers)
                    self.assertEqual(display.status_code, 200, display.data)
                    self.assertEqual(display.data["computer"]["workspace_cwd"], "nested")
                    self.assertTrue(display.data["computer"]["attached"])
                    self.assertNotIn("billing", display.data)
                    terminal = client.get(base + "terminal/", **headers)
                    self.assertEqual(terminal.status_code, 200, terminal.data)
                    self.assertTrue(any("attached-callback-command" in item["data"] for item in terminal.data["events"]))
                    events = client.get(base + "events/", **headers)
                    self.assertEqual(events.status_code, 200, events.data)
                    self.assertTrue(any(item.get("name") == "nexus.plan" for item in events.data))
                    outputs = client.get(base + "outputs/?paged=1", **headers)
                    self.assertEqual(outputs.status_code, 200, outputs.data)
                    self.assertIn(str(artifact.pk), [item["id"] for item in outputs.data["items"]])
                    download = client.get(base + f"outputs/{artifact.pk}/download/", **headers)
                    try:
                        self.assertEqual(download.status_code, 200, getattr(download, "data", None))
                        self.assertEqual(b"".join(download.streaming_content), text.encode())
                    finally:
                        download.close()
                    return client.get(base + "display/", **headers).data["computer"]["workspace_cwd"]
                self.assertEqual(await callback_roundtrip(read_display_http, expected_commands=0), "nested")
                await callback_roundtrip(delegate_http, expected_commands=0,
                    method="get", suffix="workspace", expected_status=404, token="incorrect",
                    data={"operation": "read", "path": "input.txt"})
                await sync_to_async(workspace_grants.revoke_workspace_grant)(request=request, agent=run_record.agent)
                with self.assertRaises(exceptions.APIException):
                    await callback_roundtrip(callbacks.read_invocation_workspace_file, expected_commands=0,
                        **delegated, path="input.txt")
                self.assertEqual(await sync_to_async(ComputerRuntimeCommand.objects.filter(display_run_id=run_record.pk).count)(), 5)
                await sync_to_async(callbacks.finish_invocation_display_run)(run=run_record, succeeded=True)
                await sync_to_async(run_record.refresh_from_db)()
                self.assertEqual(run_record.status, "completed")
                terminal_status = await sync_to_async(lambda: WorkspaceTerminalSession.objects.get(display_run=run_record).status)()
                self.assertEqual(terminal_status, "closed")
                # Same ASGI/real Computer SDK harness; credentials come from
                # actual verified writes, not profile activation fixtures.
                def create_tool_agents():
                    from apps.agents.models import Agent,AgentRuntimeImage,AgentRuntimeDeployment
                    result=[]
                    for name in ('MCP bridge A','MCP bridge B'):
                        agent=Agent.objects.create(tenant=self.row.tenant,project=self.row.project,
                            created_by=self.row.owner,name=name,status='active',repo_metadata={'tools':[
                                {'name':'echo','input_schema':{'type':'object','properties':{'value':{'type':'string'}},'required':['value']}}]})
                        image=AgentRuntimeImage.objects.create(tenant=agent.tenant,project=agent.project,
                            agent=agent,image_ref='sdk-http-not-container-deployment:probe')
                        AgentRuntimeDeployment.objects.create(tenant=agent.tenant,project=agent.project,
                            agent=agent,image=image,status='active',health_status='healthy',internal_mcp_url=self.mcp_url)
                        result.append(agent)
                    return result
                tool_agents=await sync_to_async(create_tool_agents)()
                api_before=tool_codec.parse_codex_toml(codex_path.read_text(encoding='utf-8'))['model_providers']
                current=await callback_roundtrip(tool_http,expected_commands=1)
                from nexus_personal.models import PersonalAgentCredential
                agent_token=None
                for agent in tool_agents:
                    agent_data={'section':'agents','action':'add_agent','agent_id':str(agent.pk),
                        'expected_revision':current['revision']}
                    review=await callback_roundtrip(tool_http,expected_commands=1,method='post',suffix='preview/',data=agent_data)
                    self.assertTrue(review['can_apply'])
                    current=await callback_roundtrip(tool_http,expected_commands=4,method='post',suffix='apply-v2/',data=agent_data)
                    self.assertEqual(current['agents']['status'],'ready')
                    physical=tool_codec.parse_codex_toml(codex_path.read_text(encoding='utf-8'))
                    self.assertEqual(physical['model_providers'],api_before)
                    self.assertEqual(physical['mcp_servers']['external'],original_config['mcp_servers']['external'])
                    fresh_token=tool_codec._mcp_token(physical['mcp_servers']['nexus-agent-'+agent.pk.hex])
                    self.assertNotIn(fresh_token,str(current))
                    if agent_token:
                        self.assertEqual(agent_token,fresh_token)
                    agent_token=fresh_token
                self.assertEqual(await sync_to_async(PersonalAgentCredential.objects.count)(),1)
                def invoke_agent(agent,token,expected=200):
                    from rest_framework.test import APIClient
                    client=APIClient()
                    client.credentials(HTTP_AUTHORIZATION='Bearer '+token)
                    response=client.post(f'/api/v1/agents/{agent.pk}/mcp/',
                        {'jsonrpc':'2.0','id':'bridge','method':'tools/call',
                            'params':{'name':'echo','arguments':{'value':'Computer 配置 echo'}}},format='json',
                        HTTP_ACCEPT='application/json',HTTP_MCP_PROTOCOL_VERSION='2025-06-18')
                    self.assertEqual(response.status_code,expected)
                    if expected==200:
                        payload=json.loads(response.content)
                        self.assertFalse(payload['result'].get('isError',False))
                        self.assertIn('Computer 配置 echo',response.content.decode())
                for agent in tool_agents:
                    await sync_to_async(invoke_agent)(agent,agent_token)
                agents_before=tool_codec.parse_codex_toml(codex_path.read_text(encoding='utf-8'))['mcp_servers']
                current=await callback_roundtrip(tool_http,expected_commands=4,method='post',suffix='apply-v2/',
                    data={**review_data,'expected_revision':current['revision']})
                self.assertEqual(tool_codec.parse_codex_toml(codex_path.read_text(encoding='utf-8'))['mcp_servers'],agents_before)
                # Actual disk write succeeds but its acknowledgement is lost.
                # Explicit restore uses a newer fence and keeps the old key.
                prior_agent_config=codex_path.read_text(encoding='utf-8')
                await callback_roundtrip(tool_http,expected_commands=2,method='post',suffix='apply-v2/',
                    data={'section':'agents','action':'rotate_agent_credential','expected_revision':current['revision']},
                    status=409,lose_write_receipt=True)
                pending_agent_op=await sync_to_async(lambda:PersonalToolConfigOperation.objects.get(active=True,section='agents'))()
                await sync_to_async(invoke_agent)(tool_agents[1],agent_token)
                unconfirmed=codex_path.read_text(encoding='utf-8')
                current=await callback_roundtrip(tool_http,expected_commands=4,method='post',suffix='recovery/',
                    data={'operation_id':str(pending_agent_op.pk),'expected_revision':tool_codec.tool_config_revision(unconfirmed),
                        'action':'restore_previous'})
                self.assertEqual(current['operation']['state'],'rolled_back')
                self.assertEqual(codex_path.read_text(encoding='utf-8'),prior_agent_config)
                self.assertEqual(current['agents']['status'],'ready')
                self.assertEqual(await sync_to_async(PersonalAgentCredential.objects.filter(revoked_at__isnull=True).count)(),1)
                removing={'section':'agents','action':'remove_agent',
                    'mcp_server_name':'nexus-agent-'+tool_agents[0].pk.hex,'expected_revision':current['revision']}
                current=await callback_roundtrip(tool_http,expected_commands=4,method='post',suffix='apply-v2/',data=removing)
                await sync_to_async(invoke_agent)(tool_agents[0],agent_token,404)
                await sync_to_async(invoke_agent)(tool_agents[1],agent_token)
                rotating={'section':'agents','action':'rotate_agent_credential','expected_revision':current['revision']}
                await callback_roundtrip(tool_http,expected_commands=4,method='post',suffix='apply-v2/',data=rotating)
                physical=tool_codec.parse_codex_toml(codex_path.read_text(encoding='utf-8'))
                rotated_agent_token=tool_codec._mcp_token(physical['mcp_servers']['nexus-agent-'+tool_agents[1].pk.hex])
                await sync_to_async(invoke_agent)(tool_agents[1],agent_token,401)
                await sync_to_async(invoke_agent)(tool_agents[1],rotated_agent_token)
                self.assertTrue(all(not row['received_credential'] for row in self.tool_calls))
                def revoke_tool_computer():
                    from rest_framework.test import APIClient
                    client = APIClient()
                    client.credentials(HTTP_AUTHORIZATION='Bearer '+self.owner_token.key)
                    response = client.post(f'/api/v1/computers/{device.connection_id}/revoke/',{},format='json')
                    self.assertEqual(response.status_code,200,response.data)
                    self.assertNotIn(new_token,str(response.data))
                await callback_roundtrip(revoke_tool_computer,expected_commands=0)
                await sync_to_async(invoke_scoped)(new_token,401)
                await sync_to_async(invoke_agent)(tool_agents[1],rotated_agent_token,401)
                self.assertFalse(await sync_to_async(PersonalRouterCredential.objects.filter(
                    issued_for='tool_setup',revoked_at__isnull=True).exists)())
            finally:
                await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
                await communicator.wait(timeout=8)

        async_to_sync(run)()
