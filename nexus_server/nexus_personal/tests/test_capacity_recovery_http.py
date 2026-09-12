"""Real personal admission and cancellation; no Agent/Computer runner fake."""
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core import signing
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.agents.models import AgentDisplayRun, AgentTaskExecution
from apps.agents.runtime_services import cancel_private_run
from . import test_conversation_http as conversation


class PersonalCapacityRecoveryHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        conversation.PersonalConversationHTTPTests.setUpTestData.__func__(cls)

    setUp=conversation.PersonalConversationHTTPTests.setUp
    start=conversation.PersonalConversationHTTPTests.start
    envelope=conversation.PersonalConversationHTTPTests.envelope

    def recovery(self,exclude=''):
        return self.client.get(self.agent_base+'private-runs/capacity-recovery/',{'exclude_run_id':exclude})

    def stop(self,preview,exclude=''):
        return self.client.post(self.agent_base+'private-runs/capacity-recovery/',
            {'exclude_run_id':exclude or None,'preview_token':preview,'confirmation':True},format='json')

    def test_real_admission_recovery_cancels_only_reviewed_other_run_and_allows_new_turn(self):
        limits={**settings.NEXUS_PERSONAL_AGENT_LIMITS,'agents.concurrent_runs':2}
        rest={**settings.REST_FRAMEWORK,'EXCEPTION_HANDLER':'apps.common.exceptions.unified_exception_handler'}
        with override_settings(NEXUS_PERSONAL_AGENT_LIMITS=limits,REST_FRAMEWORK=rest):
            self.start()
            first=self.run
            self.start()
            current=self.run
            denied=self.client.post(self.agent_base+'private-runs/',{'message':'No slot'},format='json')
            self.assertEqual(denied.status_code,409,denied.data)
            self.assertEqual(denied.data['error']['code'],'PERSONAL_RUN_CAPACITY_EXCEEDED')
            preview=self.recovery(str(current.pk))
            self.assertEqual(preview.status_code,200,getattr(preview,'data',None))
            self.assertEqual([run['id'] for run in preview.data['runs']],[str(first.pk)])
            self.assertEqual(int(preview.data['capacity']['used']),2)
            result=self.stop(preview.data['preview_token'],str(current.pk))
            self.assertEqual(result.status_code,200,result.data)
            self.assertEqual(result.data['stopped'],1,result.data)
            current.refresh_from_db()
            first.refresh_from_db()
            self.assertEqual(current.status,'running')
            self.assertNotEqual(first.status,'running')
            self.assertTrue(first.messages.exists())
            replay=self.stop(preview.data['preview_token'],str(current.pk))
            self.assertEqual(replay.status_code,200,replay.data)
            self.assertEqual(replay.data['stopped'],0)
            self.start()
            self.assertEqual(self.run.status,'running')
            self.assertEqual(AgentDisplayRun.objects.filter(status='running',run_kind='invocation').count(),2)

    def test_preview_does_not_stop_new_runs_or_rotated_turns(self):
        self.start()
        first=self.run
        preview=self.recovery()
        self.assertEqual(preview.status_code,200,getattr(preview,'data',None))
        self.start()
        later=self.run
        AgentDisplayRun.objects.filter(pk=first.pk).update(write_token='different-turn-token')
        result=self.stop(preview.data['preview_token'])
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.data['stopped'],0)
        self.assertEqual(result.data['skipped'],1)
        for run in (first,later):
            run.refresh_from_db()
            self.assertEqual(run.status,'running')

    def test_forged_preview_and_foreign_owner_never_cancel(self):
        self.start()
        preview=self.recovery()
        self.assertEqual(preview.status_code,200,getattr(preview,'data',None))
        self.assertEqual(self.stop(preview.data['preview_token']+'tampered').status_code,400)
        stranger=get_user_model().objects.create_user(username='not-the-owner')
        client=APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer '+Token.objects.create(user=stranger).key)
        self.assertIn(client.get(self.agent_base+'private-runs/capacity-recovery/').status_code,(401,403))
        response=client.post(self.agent_base+'private-runs/capacity-recovery/',
            {'confirmation':True,'preview_token':preview.data['preview_token']},format='json')
        self.assertIn(response.status_code,(401,403))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status,'running')

    def test_session_writes_require_csrf_and_preview_exclusion_is_bound(self):
        self.start()
        current=self.run
        self.start()
        preview=self.recovery(str(current.pk))
        self.assertEqual(preview.status_code,200,getattr(preview,'data',None))
        # Removing the excluded Run from a previously approved review changes
        # its identity and must not expand the cancellation target set.
        self.assertEqual(self.stop(preview.data['preview_token']).status_code,400)
        session=APIClient(enforce_csrf_checks=True)
        session.force_login(self.row.owner)
        response=session.post(self.agent_base+'private-runs/capacity-recovery/',
            {'confirmation':True,'exclude_run_id':str(current.pk),'preview_token':preview.data['preview_token']},format='json')
        self.assertEqual(response.status_code,403)
        self.assertEqual(AgentDisplayRun.objects.filter(status='running',run_kind='invocation').count(),2)

    def test_other_personal_limits_keep_the_generic_error_code(self):
        from nexus_personal.resource_limits import PersonalResourceAdmission, PersonalCapacityExceeded
        limits={**settings.NEXUS_PERSONAL_AGENT_LIMITS,'agents.agents':0}
        with override_settings(NEXUS_PERSONAL_AGENT_LIMITS=limits),self.assertRaises(PersonalCapacityExceeded) as denied:
            PersonalResourceAdmission().enforce_capability(tenant=self.row.tenant,code='agents.agents')
        self.assertEqual(denied.exception.default_code,'PERSONAL_CAPACITY_EXCEEDED')

    def test_running_run_cannot_be_hidden_and_expired_review_cannot_cancel(self):
        self.start()
        response = self.client.delete(self.base + 'display/', **self.headers)
        self.assertEqual(response.status_code, 409, response.data)
        preview = self.recovery()
        self.assertEqual(preview.status_code, 200, preview.data)
        with patch('apps.agents.run_capacity.signing.loads', side_effect=signing.SignatureExpired):
            response = self.stop(preview.data['preview_token'])
        self.assertEqual(response.status_code, 400, response.data)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, 'running')
        self.assertIsNone(self.run.caller_hidden_at)
        self.assertEqual(AgentTaskExecution.objects.get(task__run=self.run).state, 'queued')

    def test_live_worker_cancellation_keeps_capacity_until_worker_finishes(self):
        self.start()
        execution = AgentTaskExecution.objects.get(task__run=self.run)
        execution.state = 'running'
        execution.lease_expires_at = timezone.now() + timedelta(seconds=90)
        execution.save(update_fields=['state', 'lease_expires_at'])
        preview = self.recovery()
        self.assertEqual(preview.status_code, 200, preview.data)
        response = self.stop(preview.data['preview_token'])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data['pending'], response.data['stopped']), (1, 0))
        self.assertEqual(int(response.data['capacity']['used']), 1)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, 'running')
        self.assertIsNone(self.run.caller_hidden_at)

    def test_partial_cancellation_failure_keeps_history_and_allows_safe_retry(self):
        self.start()
        first = self.run
        self.start()
        second = self.run
        preview = self.recovery()
        self.assertEqual(preview.status_code, 200, preview.data)

        def cancel(**kwargs):
            if kwargs['run_id'] == str(first.pk):
                raise RuntimeError('sensitive upstream credential')
            return cancel_private_run(**kwargs)

        with patch('apps.agents.runtime_services.cancel_private_run', side_effect=cancel):
            response = self.stop(preview.data['preview_token'])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data['failed'], response.data['stopped']), (1, 1))
        self.assertNotIn('sensitive upstream credential', response.content.decode())
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, 'running')
        self.assertEqual(second.status, 'failed')
        self.assertTrue(first.messages.exists())
        self.assertTrue(second.messages.exists())
        retry = self.stop(preview.data['preview_token'])
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual((retry.data['stopped'], retry.data['skipped'], retry.data['failed']), (1, 1, 0))
        self.assertEqual(AgentDisplayRun.objects.count(), 2)

    def test_resume_at_capacity_preserves_finished_turn_and_delegates(self):
        self.start()
        finished = self.run
        conversation.PersonalConversationHTTPTests.settle_success_fixture(self)
        old_token = finished.write_token
        old_messages = list(finished.messages.values_list('id', 'content'))
        old_invocations = list(finished.runtime_invocations.values_list('id', 'turn_index', 'status'))
        finished_base, finished_headers = self.base, self.headers
        self.start()
        limits = {**settings.NEXUS_PERSONAL_AGENT_LIMITS, 'agents.concurrent_runs': 1}
        rest = {**settings.REST_FRAMEWORK, 'EXCEPTION_HANDLER': 'apps.common.exceptions.unified_exception_handler'}
        with override_settings(NEXUS_PERSONAL_AGENT_LIMITS=limits, REST_FRAMEWORK=rest):
            response = self.client.post(finished_base + 'resume/', {'message': 'Wait for a slot'},
                                        format='json', **finished_headers)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['error']['code'], 'PERSONAL_RUN_CAPACITY_EXCEEDED')
        finished.refresh_from_db()
        self.assertEqual((finished.status, finished.write_token), ('completed', old_token))
        self.assertEqual(list(finished.messages.values_list('id', 'content')), old_messages)
        self.assertEqual(list(finished.runtime_invocations.values_list('id', 'turn_index', 'status')), old_invocations)

    def test_over_limit_recovery_preserves_52_real_queued_runs_and_ignores_non_invocations(self):
        limits = {**settings.NEXUS_PERSONAL_AGENT_LIMITS, 'agents.concurrent_runs': 60}
        with override_settings(NEXUS_PERSONAL_AGENT_LIMITS=limits):
            for index in range(52):
                self.start()
                if index % 2:
                    AgentDisplayRun.objects.filter(pk=self.run.pk).update(caller_hidden_at=timezone.now())
        current = self.run
        for kind in ('deployment', 'demo', 'legacy'):
            AgentDisplayRun.objects.create(tenant=self.row.tenant, agent=self.agent,
                consumer_tenant=self.row.tenant, consumer_project=self.row.project,
                caller_subject_hash=current.caller_subject_hash, run_kind=kind,
                status='running', write_token='non-invocation-test-only')
        limits['agents.concurrent_runs'] = 5
        with override_settings(NEXUS_PERSONAL_AGENT_LIMITS=limits):
            preview = self.recovery(str(current.pk))
            self.assertEqual(preview.status_code, 200, preview.data)
            self.assertEqual(int(preview.data['capacity']['used']), 52)
            self.assertEqual(preview.data['eligible_count'], 51)
            self.assertTrue(any(run['hidden'] for run in preview.data['runs']))
            self.assertNotIn('write_token', preview.content.decode())
            response = self.stop(preview.data['preview_token'], str(current.pk))
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual((response.data['stopped'], response.data['failed']), (51, 0))
            self.assertEqual(int(response.data['capacity']['used']), 1)
            self.assertEqual(int(response.data['capacity']['remaining']), 4)
            replay = self.stop(preview.data['preview_token'], str(current.pk))
            self.assertEqual(replay.status_code, 200, replay.data)
            self.assertEqual((replay.data['stopped'], replay.data['skipped']), (0, 51))
        self.assertEqual(AgentDisplayRun.objects.count(), 55)
        self.assertEqual(AgentDisplayRun.objects.filter(run_kind='invocation', messages__isnull=False).distinct().count(), 52)
        self.assertEqual(AgentDisplayRun.objects.exclude(run_kind='invocation').filter(status='running').count(), 3)
        current.refresh_from_db()
        self.assertEqual(current.status, 'running')
