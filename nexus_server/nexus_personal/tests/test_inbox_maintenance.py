"""Real personal DB repair; PostgreSQL lock/network push need live acceptance."""
import inspect
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.utils import timezone
from apps.agents.models import AgentDisplayRun, AgentRuntimeDeployment, AgentPythonBuild, AgentRunInteraction, EdgeNode
from apps.datasets.models import Dataset, DatasetImportJob
from apps.jobs.models import Job
from apps.notifications import tasks
from apps.notifications.inbox import upsert_item
from apps.notifications.models import InboxItem, InboxWorkerCursor, UserNotification
from apps.providers.models import ProviderRuntimeAccount
from apps.tenancy.models import Project, Tenant
from apps.workspaces.models import WorkspaceConnection
from apps.mobile.models import MobileDevice
from nexus_personal.notifications import PersonalNotificationBackend
from . import test_agent_file_http as file_http


class PersonalInboxMaintenanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        file_http.PersonalAgentFileHTTPTests.setUpTestData.__func__(cls)

    def setUp(self):
        file_http.PersonalAgentFileHTTPTests.setUp(self)

    def repair(self):
        # Only unwrap the PostgreSQL advisory lock. All queries, callbacks,
        # cursor advancement, notifications and cleanup use the actual DB.
        return inspect.unwrap(tasks.reconcile_work_inbox.run)()

    def test_missed_run_signal_is_repaired_and_replay_is_idempotent(self):
        AgentDisplayRun.objects.filter(pk=self.run.pk).update(status='completed', completed_at=timezone.now())
        self.assertFalse(InboxItem.objects.exists())
        self.repair()
        self.repair()
        self.assertEqual(InboxItem.objects.filter(source_type='run').count(), 1)
        self.assertEqual(UserNotification.objects.filter(run=self.run).count(), 1)
        self.assertFalse(InboxWorkerCursor.objects.filter(name__in=['approvals', 'workflows', 'alerts']).exists())

    def test_operational_issues_are_owner_not_role_notices_and_recover(self):
        context = {'tenant': self.row.tenant, 'project': self.row.project}
        provider = ProviderRuntimeAccount.objects.create(**context, name='Local provider', owner=self.row.owner,
            runtime_type='direct_api', status='unhealthy', last_error='sensitive-detail-not-for-inbox')
        computer = WorkspaceConnection.objects.create(**context, name='Local computer', created_by=self.row.owner,
            connection_type='runtime', last_test_status='failed')
        phone = MobileDevice.objects.create(**context, name='Local phone', created_by=self.row.owner,
            token_hash='d' * 64, last_seen_at=timezone.now(), online_status='offline')
        AgentRuntimeDeployment.objects.filter(pk=self.runtime.pk).update(health_status='unhealthy')
        self.repair()
        notices = InboxItem.objects.filter(category='operations')
        self.assertEqual(notices.count(), 4)
        for item in notices:
            self.assertEqual(str(item.recipient_id), str(self.row.owner_id))
            self.assertEqual(item.audience_type, 'personal')
            self.assertEqual(item.required_permission, '')
            self.assertNotIn('sensitive-detail', str(item.safe_context))
        response = self.client.get('/api/v1/inbox/items/?state=needs_action')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 4)
        ProviderRuntimeAccount.objects.filter(pk=provider.pk).update(status='active')
        WorkspaceConnection.objects.filter(pk=computer.pk).update(last_test_status=WorkspaceConnection.TEST_SUCCEEDED)
        MobileDevice.objects.filter(pk=phone.pk).update(online_status='online')
        AgentRuntimeDeployment.objects.filter(pk=self.runtime.pk).update(health_status='healthy')
        # Live listing is read-only; reconciliation subsequently persists it.
        self.assertEqual(self.client.get('/api/v1/inbox/items/?state=needs_action').data['results'], [])
        self.repair()
        self.assertEqual(set(notices.values_list('state', flat=True)), {'resolved'})

    def test_build_and_import_repair_follow_parent_ownership(self):
        build = AgentPythonBuild(agent=self.agent, created_by=self.row.owner, filename='example.py',
            source='private-source-not-for-inbox', status='failed', completed_at=timezone.now())
        AgentPythonBuild.objects.bulk_create([build])
        dataset = Dataset.objects.create(tenant=self.row.tenant, project=self.row.project,
            created_by=self.row.owner, name='Local files')
        imported = DatasetImportJob(dataset=dataset, tenant=self.row.tenant, requested_by=self.row.owner,
            state='failed', context_project_id=str(self.row.project_id), request_key='import-1',
            inputs={'private': 'not-for-inbox'})
        DatasetImportJob.objects.bulk_create([imported])
        self.repair()
        self.assertEqual(InboxItem.objects.filter(source_type__in=['build', 'dataset_import']).count(), 2)
        self.assertEqual(UserNotification.objects.filter(build=build).count(), 1)
        for item in InboxItem.objects.all():
            self.assertEqual(item.project_id, self.row.project_id)
            self.assertNotIn('not-for-inbox', str(item.safe_context))

    def test_pending_question_is_repaired_then_expiry_resolves_it(self):
        AgentDisplayRun.objects.filter(pk=self.run.pk).update(status='input_required')
        question = AgentRunInteraction(run=self.run, key='missed-question', kind='text',
            prompt='private question content', expires_at=timezone.now() + timedelta(minutes=5))
        AgentRunInteraction.objects.bulk_create([question])
        self.repair()
        item = InboxItem.objects.get(kind='input_required')
        self.assertEqual(item.state, 'needs_action')
        self.assertNotIn('private question', str(item.safe_context))
        AgentRunInteraction.objects.filter(pk=question.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.repair()
        item.refresh_from_db()
        self.assertEqual(item.state, 'resolved')

    def test_real_node_presence_facts_drive_failure_and_recovery_notice(self):
        node = EdgeNode.objects.create(tenant=self.row.tenant, project=self.row.project,
            registered_by=self.row.owner, router_id='local-router', domain_id='fixture.invalid',
            display_name='Local router', device_token_hash='b' * 64, presence_protocol_version=1,
            last_presence_at=timezone.now() - timedelta(minutes=5),
            presence_expires_at=timezone.now() - timedelta(minutes=1), connection_status='online')
        self.repair()
        item = InboxItem.objects.get(source_type='edge_router_issue')
        self.assertEqual(item.state, 'needs_action')
        EdgeNode.objects.filter(pk=node.pk).update(last_presence_at=timezone.now(),
            presence_expires_at=timezone.now() + timedelta(minutes=5), connection_status='online')
        self.repair()
        item.refresh_from_db()
        self.assertEqual(item.state, 'resolved')

    def test_foreign_scope_and_user_work_are_not_reconciled(self):
        other = get_user_model().objects.create_user(username='foreign-inbox-owner')
        other_project = Project.objects.create(tenant=self.row.tenant, name='Other project')
        foreign = Tenant.objects.create(name='Foreign', slug='foreign-inbox')
        for index, fields in enumerate(({'owner': other, 'project': self.row.project, 'tenant': self.row.tenant},
            {'owner': self.row.owner, 'project': other_project, 'tenant': self.row.tenant},
            {'owner': self.row.owner, 'project': None, 'tenant': foreign})):
            ProviderRuntimeAccount.objects.create(**fields, name=f'Foreign {index}', status='unhealthy', runtime_type='direct_api')
        AgentDisplayRun.objects.filter(pk=self.run.pk).update(caller_principal_id=str(other.pk), status='completed', completed_at=timezone.now())
        self.repair()
        self.assertFalse(InboxItem.objects.exists())
        self.assertFalse(UserNotification.objects.exists())

    @override_settings(NEXUS_INBOX_RECONCILE_BATCH_SIZE=2)
    def test_bounded_cursor_replay_does_not_skip_interrupted_batch(self):
        jobs = [Job(tenant=self.row.tenant, project=self.row.project, created_by=self.row.owner,
                    job_type='local', resource_type='agent') for _ in range(5)]
        Job.objects.bulk_create(jobs)
        queryset = Job.objects.all()
        stream = tasks._batch('jobs', queryset)
        first = next(stream)
        stream.close()
        self.assertEqual(InboxWorkerCursor.objects.get(name='jobs').position, {})
        batches = [list(tasks._batch('jobs', queryset)) for _ in range(3)]
        self.assertEqual([len(rows) for rows in batches], [2, 2, 1])
        self.assertEqual(batches[0][0].pk, first.pk)
        self.assertEqual({row.pk for rows in batches for row in rows}, {row.pk for row in jobs})
        self.repair()
        self.assertTrue(InboxItem.objects.filter(source_type='job').exists())

    def test_retention_does_not_delete_foreign_or_unsupported_items(self):
        other = get_user_model().objects.create_user(username='retention-other')
        rows = []
        for index, changes in enumerate(({}, {'recipient_id': other.pk}, {'source_type': 'unavailable_product'},
                                        {'audience_type': 'role', 'recipient_id': None, 'required_permission': 'admin'})):
            rows.append(upsert_item(**{**dict(tenant_id=self.row.tenant_id, project_id=self.row.project_id,
                recipient_id=self.row.owner_id, category='agent', kind='run_completed', state='completed',
                priority=1, source_type='run', source_id=str(self.run.pk), event_key=f'old:{index}', navigation_key='agent_run',
                resolved_at=timezone.now() - timedelta(days=100)), **changes}))
        self.repair()
        self.assertFalse(InboxItem.objects.filter(pk=rows[0].pk).exists())
        self.assertEqual(InboxItem.objects.filter(pk__in=[row.pk for row in rows[1:]]).count(), 3)

    def test_unknown_maintenance_source_fails_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            PersonalNotificationBackend().maintenance_queryset(name='unreviewed-source', queryset=Job.objects.all())
