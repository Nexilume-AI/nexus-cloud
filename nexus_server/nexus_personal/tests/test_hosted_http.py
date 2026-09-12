"""Real owned HTTP/DB management, not Docker execution acceptance.

Images represent previously admitted artifacts. Only queue transport is replaced
in dispatch tests; no fake runtime runner, container or health result is used.
"""
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from kombu.exceptions import OperationalError
from rest_framework.test import APIClient

from apps.agents.models import Agent, AgentRuntimeImage, AgentRuntimeDeployment, AgentPythonBuild
from apps.jobs.models import Job, JobEvent
from apps.tenancy.models import Project
from nexus_personal.services import provision_owner
from .test_installation import PASSWORD


@override_settings(ROOT_URLCONF='nexus_personal.urls', NEXUS_AGENT_PYTHON_BUILDS_ENABLED=False,
                   NEXUS_AGENT_RUNTIME_RUNNER='controller', NEXUS_AGENT_RUNTIME_HOST_ID='personal-http-test')
class PersonalHostedHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.installation = provision_owner(email='hosted-owner@example.test', password=PASSWORD)
        cls.other = get_user_model().objects.create_user(username='other-hosted')

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.installation.owner)
        self.client.get('/api/v1/public/bootstrap/')
        self.csrf = {'HTTP_X_CSRFTOKEN': self.client.cookies['csrftoken'].value}
        response = self.client.post('/api/v1/agents/', {'name': 'my-hosted-agent'}, format='json', **self.csrf)
        self.assertEqual(response.status_code, 201, response.content)
        self.agent = Agent.objects.get(pk=response.data['id'])
        self.base = f'/api/v1/agents/{self.agent.pk}/runtime/'

    def image(self, suffix='one', agent=None):
        agent = agent or self.agent
        return AgentRuntimeImage.objects.create(agent=agent, tenant=agent.tenant,
            project=agent.project, created_by=self.installation.owner,
            image_ref=f'example.test/personal/{suffix}:latest', image_digest='sha256:' + 'a' * 64,
            registry_secret_ref='not-a-real-secret-reference')

    def runtime(self, image):
        return AgentRuntimeDeployment.objects.create(agent=self.agent, tenant=self.agent.tenant,
            project=self.agent.project, image=image, env='prod', runtime_kind='docker',
            status='active', health_status='healthy', deployed_by=self.installation.owner,
            docker_lifecycle={'host_id': 'personal-http-test', 'desired': 'running', 'generation': 'original'})

    def post(self, path, body=None):
        return self.client.post(self.base + path, body or {}, format='json', **self.csrf)

    def test_image_inventory_default_selection_and_safe_removal(self):
        first, second = self.image(), self.image('two')
        response = self.client.get(self.base + 'images/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual({str(row['id']) for row in response.data}, {str(first.pk), str(second.pk)})
        self.assertNotIn('not-a-real-secret-reference', response.content.decode())
        self.assertEqual(self.post(f'images/{second.pk}/set-current/').status_code, 200)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.current_image_id, second.pk)
        response = self.client.delete(self.base + f'images/{first.pk}/', **self.csrf)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.data['artifacts_retained'])
        first.refresh_from_db()
        self.assertEqual(first.status, 'deleted')
        status = self.client.get(self.base + 'status/')
        self.assertEqual(status.status_code, 200, status.content)
        self.assertEqual(len(status.data['images']), 1)
        self.assertEqual(status.data['deployments'], [])

    def test_deployed_image_cannot_be_removed_and_status_is_actual(self):
        image = self.image()
        runtime = self.runtime(image)
        response = self.client.delete(self.base + f'images/{image.pk}/', **self.csrf)
        self.assertEqual(response.status_code, 409, response.content)
        image.refresh_from_db()
        self.assertEqual(image.status, 'active')
        status = self.client.get(self.base + 'status/')
        self.assertEqual(str(status.data['deployments'][0]['id']), str(runtime.pk))
        self.assertIn('DEPLOYED', status.data['images'][0]['usage']['blocking_reasons'])

    def test_invalid_or_foreign_image_cannot_dispatch_deployment(self):
        with patch('apps.agents.tasks.deploy_runtime_job.delay') as dispatch:
            response = self.post('deployments/', {'image_id': 'not-a-uuid'})
            self.assertEqual(response.status_code, 400, response.content)
            response = self.post('deployments/', {'image_id': str(uuid4())})
            self.assertEqual(response.status_code, 404, response.content)
            dispatch.assert_not_called()
        self.assertFalse(Job.objects.exists())
        self.assertFalse(AgentRuntimeDeployment.objects.exists())

    def test_health_request_queues_existing_job_without_inventing_health(self):
        runtime = self.runtime(self.image())
        with patch('apps.agents.tasks.runtime_health_check_job.delay', return_value=SimpleNamespace(id='queued-health')) as dispatch:
            response = self.post('health-check/')
        self.assertEqual(response.status_code, 200, response.content)
        job = Job.objects.get(pk=response.data['job_id'])
        self.assertEqual(job.job_type, 'agents.runtime.health_check')
        self.assertEqual(job.project_id, self.installation.project_id)
        status = self.client.get(f'/api/v1/jobs/{job.pk}/')
        self.assertEqual(status.status_code, 200, status.content)
        self.assertEqual(status.data['status'], 'queued')
        dispatch.assert_called_once_with(str(job.pk), str(runtime.pk))
        runtime.refresh_from_db()
        self.assertEqual(runtime.health_status, 'healthy')

    def test_stop_keeps_recoverable_desired_state_when_queue_is_offline(self):
        runtime = self.runtime(self.image())
        with patch('apps.agents.tasks.stop_runtime_job.delay', side_effect=OperationalError('queue down')):
            response = self.post('deployments/stop/')
        self.assertEqual(response.status_code, 200, response.content)
        runtime.refresh_from_db()
        self.assertEqual(runtime.docker_lifecycle['desired'], 'stopped')
        self.assertEqual(runtime.docker_lifecycle['generation'], 'original')
        job = Job.objects.get(pk=response.data['job_id'])
        self.assertEqual(job.status, 'queued')
        self.assertTrue(JobEvent.objects.filter(job=job, event_type='dispatch_deferred').exists())
        events = self.client.get(f'/api/v1/jobs/{job.pk}/events/')
        self.assertEqual(events.status_code, 200, events.content)
        self.assertIn('dispatch_deferred', [event['event_type'] for event in events.data])
        self.assertNotEqual(runtime.status, 'stopped')  # No container stop has been observed.
        self.assertEqual(self.post('deployments/stop/').status_code, 409)
        self.assertEqual(Job.objects.count(), 1)

    @override_settings(NEXUS_AGENT_RUNTIME_RUNNER='docker', NEXUS_AGENT_RUNTIME_VERIFY_IMAGE_ON_REGISTER=False)
    def test_deploy_preparation_persists_job_and_generation_when_dispatch_is_offline(self):
        # An already admitted image; Docker execution stays queued, not faked.
        image = self.image()
        with patch('apps.agents.tasks.deploy_runtime_job.delay', side_effect=OperationalError('queue down')):
            response = self.post('deployments/', {'image_id': str(image.pk)})
        self.assertEqual(response.status_code, 201, response.content)
        runtime = AgentRuntimeDeployment.objects.get(pk=response.data['id'])
        self.assertEqual(runtime.status, 'deploying')
        self.assertEqual(runtime.container_id, '')
        self.assertEqual(runtime.docker_lifecycle['desired'], 'running')
        job = Job.objects.get(pk=response.data['job_id'])
        self.assertEqual(job.input_json['docker_generation'], runtime.docker_lifecycle['generation'])
        self.assertEqual(job.project_id, self.installation.project_id)
        self.assertTrue(JobEvent.objects.filter(job=job, event_type='dispatch_deferred').exists())
        self.assertEqual(self.post('deployments/', {'image_id': str(image.pk)}).status_code, 409)
        self.assertEqual(Job.objects.count(), 1)

    def test_python_builder_is_explicitly_unavailable_and_failed_source_is_erased(self):
        response = self.client.get(self.base + 'python-builds/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.data['configuration']['enabled'])
        self.assertEqual(self.post('python-builds/').status_code, 400)
        build = AgentPythonBuild.objects.create(agent=self.agent, status='failed', stage='image',
            source='private source fixture', encrypted_secrets='private ciphertext fixture', filename='agent.py')
        response = self.client.get(self.base + 'python-builds/')
        self.assertNotIn('private source fixture', response.content.decode())
        self.assertNotIn('private ciphertext fixture', response.content.decode())
        response = self.post('python-builds/delete-failed/', {'build_ids': [str(build.pk)]})
        self.assertEqual(response.status_code, 200, response.content)
        build.refresh_from_db()
        self.assertEqual((build.status, build.source, build.encrypted_secrets), ('deleted', '', ''))

    def test_authentication_csrf_and_owner_context_isolation(self):
        image = self.image()
        anonymous = APIClient()
        self.assertIn(anonymous.get(self.base + 'status/').status_code, (401, 403))
        response = self.client.post(self.base + f'images/{image.pk}/set-current/', {}, format='json')
        self.assertEqual(response.status_code, 403)
        self.agent.created_by = self.other
        self.agent.save(update_fields=['created_by'])
        for endpoint in ('status/', 'images/', 'python-builds/', 'mcp/export/'):
            self.assertEqual(self.client.get(self.base + endpoint).status_code, 404, endpoint)
        self.agent.created_by = self.installation.owner
        self.agent.project = Project.objects.create(tenant=self.installation.tenant, name='Foreign')
        self.agent.save(update_fields=['created_by', 'project'])
        self.assertEqual(self.client.get(self.base + 'status/').status_code, 404)

    def test_job_reads_reject_other_owner_and_project_without_admin_escalation(self):
        from apps.common.authorization import has_nexus_permission
        self.assertFalse(has_nexus_permission(self.installation.owner, self.installation.tenant, 'tenant.admin'))
        own = Job.objects.create(tenant=self.installation.tenant, project=self.installation.project,
            created_by=self.installation.owner, job_type='agents.runtime.health_check', resource_type='agent')
        foreign = Job.objects.create(tenant=self.installation.tenant, project=self.installation.project,
            created_by=self.other, job_type='agents.runtime.health_check', resource_type='agent')
        elsewhere = Job.objects.create(tenant=self.installation.tenant,
            project=Project.objects.create(tenant=self.installation.tenant, name='Other project'),
            created_by=self.installation.owner, job_type='agents.runtime.health_check', resource_type='agent')
        response = self.client.get('/api/v1/jobs/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([str(job['id']) for job in response.data], [str(own.pk)])
        for job in (foreign, elsewhere):
            self.assertEqual(self.client.get(f'/api/v1/jobs/{job.pk}/').status_code, 404)
            self.assertEqual(self.client.get(f'/api/v1/jobs/{job.pk}/events/').status_code, 404)
        self.client.force_login(self.other)
        self.assertIn(self.client.get('/api/v1/jobs/').status_code, (401, 403))
