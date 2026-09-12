"""Installed PostgreSQL worker-loss/offline-controller probe, not hosted deployment.

Run only in a freshly initialized, owned fixture database. No test settings,
financial adapters, controller readiness bypass or fake execution runner.
"""
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def run(password, expected_database):
    import django
    django.setup()
    from django.conf import settings
    from django.db import connection
    from django.utils import timezone
    from rest_framework.test import APIClient
    from apps.agents import task_execution as worker
    from apps.agents.models import Agent, AgentVersion, AgentRuntimeImage, AgentRuntimeDeployment, AgentExecutionTask, AgentTaskExecution
    from nexus_personal.models import PersonalInstallation, PersonalInvocationUsage

    assert connection.vendor == 'postgresql'
    assert re.fullmatch(r'nexus_personal_[0-9a-f]{32}', expected_database)
    assert connection.settings_dict['NAME'] == expected_database
    assert settings.NEXUS_PRODUCTION is True
    assert settings.NEXUS_AGENT_RUNTIME_RUNNER == 'controller'
    assert not settings.NEXUS_AGENT_RUNTIME_EGRESS_POLICY_READY
    assert not settings.NEXUS_PERSONAL_CONFIGURED_CONTROLLERS
    import nexus_personal
    server = Path(nexus_personal.__file__).resolve().parents[1]
    # The parent verifies the actual installed artifact or a filtered source
    # tree. Fixtures live separately for installed acceptance; __file__ here
    # must not select the fixture directory as the product runtime. Children
    # retain the same runtime/CWD rather than importing the mixed checkout.
    assert Path.cwd().resolve() == server
    assert Path(os.environ['PYTHONPATH'].split(os.pathsep)[0]).resolve() == server
    row = PersonalInstallation.objects.select_related('owner', 'tenant', 'project').get()
    agent = Agent.objects.create(tenant=row.tenant, project=row.project, created_by=row.owner,
        name='Worker recovery protocol fixture', status='active', current_version='v1',
        repo_metadata={'tools': [{'name': 'inspect', 'input_schema': {'type': 'object',
            'properties': {'message': {'type': 'string'}}, 'required': ['message']}}]})
    version = AgentVersion.objects.create(agent=agent, version='v1', created_by=row.owner,
        tool_runtime_policy={'inspect': {'task': True, 'chat': True, 'interactive': True,
            'continuable': True, 'recovery_protocol': 1}})
    image = AgentRuntimeImage.objects.create(tenant=row.tenant, project=row.project, agent=agent,
        version=version, created_by=row.owner, image_ref='fixture.invalid/not-deployed:v1')
    # Metadata admits a durable Task; the absent real Controller must prevent
    # execution. This is deliberately NOT a successful Docker/MCP acceptance.
    AgentRuntimeDeployment.objects.create(tenant=row.tenant, project=row.project, agent=agent,
        image=image, runtime_kind='docker', status='active', health_status='healthy',
        container_id='not-deployed', internal_mcp_url='http://127.0.0.1:1/mcp')
    client = APIClient(enforce_csrf_checks=True)
    headers = {'HTTP_HOST': 'personal.example:9443', 'HTTP_ORIGIN': 'https://personal.example:9443'}
    assert client.get('/api/v1/public/bootstrap/', secure=True, **headers).status_code == 200
    headers['HTTP_X_CSRFTOKEN'] = client.cookies['csrftoken'].value
    response = client.post('/api/v1/auth/login/', {'email': row.owner.email, 'password': password},
        format='json', secure=True, HTTP_X_NEXUS_CLIENT='web', **headers)
    assert response.status_code == 200, 'Owner session login failed'
    headers['HTTP_X_CSRFTOKEN'] = client.cookies['csrftoken'].value
    response = client.post(f'/api/v1/agents/{agent.pk}/private-runs/',
        {'content': 'durable recovery probe'}, format='json', secure=True, **headers)
    assert response.status_code == 202, 'Task admission failed: ' + str(response.status_code)
    task = AgentExecutionTask.objects.get(run_id=response.json()['run_id'])
    assert task.execution.encrypted_payload and task.execution.state == 'queued'

    environment = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'nexus_personal.settings'}
    child = subprocess.Popen([sys.executable, '-c',
        'import django,time; django.setup(); from apps.agents.task_execution import claim; '
        'assert claim() is not None; time.sleep(60)'], cwd=server, env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            task.refresh_from_db()
            if task.execution.state == 'running':
                break
            assert child.poll() is None, 'Owned claim process exited before claiming'
            time.sleep(0.1)
        assert task.execution.state == 'running', 'Claim process deadline exceeded'
        lease = str(task.execution.lease_id)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
    AgentTaskExecution.objects.filter(task=task).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    assert worker.sweep() >= 1
    task.refresh_from_db()
    assert task.status == AgentExecutionTask.STATUS_RECOVERING
    assert task.execution.encrypted_payload
    # An expired worker cannot finalize after another process takes ownership.
    worker.execute(str(task.pk), lease)
    task.refresh_from_db()
    assert task.execution.state == 'queued'

    process = subprocess.Popen([sys.executable, '-m', 'nexus_personal.processes',
        'agent-worker', '--once', '--concurrency', '1'], cwd=server, env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        assert process.wait(timeout=60) == 0, 'Formal worker exited unsuccessfully'
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
    task.refresh_from_db()
    assert task.status == AgentExecutionTask.STATUS_WAITING_FOR_RUNTIME, task.status
    assert task.execution.state == 'waiting_for_runtime'
    assert task.execution.encrypted_payload
    receipt = PersonalInvocationUsage.objects.get(run_id=task.run_id)
    assert receipt.finished_at is None
    assert receipt.invocation.status == 'pending'
    # No orphan pending invocation survives an owner cancellation.
    worker.request_cancel(task)
    task.refresh_from_db()
    receipt.refresh_from_db()
    assert task.status == AgentExecutionTask.STATUS_CANCELLED
    assert not task.execution.encrypted_payload
    assert receipt.finished_at is not None
    assert receipt.invocation.status == 'failed'
    print(json.dumps({'installed_postgres': True, 'owner_session_csrf': True,
        'killed_claim_fenced': True, 'formal_worker_spawn': True,
        'missing_controller_retains_task': True, 'cancel_finalizes_receipt': True,
        'hosted_deployment_verified': False}), flush=True)
