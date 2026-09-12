"""Real build-lease recovery in the existing installed PostgreSQL fixture."""
import re
import unittest
from uuid import uuid4

from django.conf import settings
from django.db import connection, transaction

from apps.agents.models import Agent, AgentPythonBuild
from apps.notifications.models import UserNotification
from apps.jobs.models import Job
from apps.notifications.models import InboxItem, InboxWorkerCursor, PushDelivery, WebPushSubscription
from django.utils import timezone
from nexus_personal.models import PersonalInstallation
from tests.notification_source_guards import NotificationBuildWorkerGuards
from tests.work_inbox_guards import WorkInboxPostgresGuards
from tests.work_inbox_scale_guards import InboxConcurrentPushGuards


def require_fixture(expected_database):
    if (settings.NEXUS_DISTRIBUTION != "community"
            or not re.fullmatch(r"nexus_personal_[0-9a-f]{32}", expected_database)
            or connection.vendor != "postgresql"
            or connection.settings_dict["NAME"] != expected_database
            or connection.in_atomic_block or not connection.get_autocommit()):
        raise AssertionError("Explicit committed fixture-owned PostgreSQL database required")


class PersonalNotificationBuildWorkerTests(NotificationBuildWorkerGuards, unittest.TestCase):
    def setUp(self):
        require_fixture(connection.settings_dict["NAME"])
        # claim_build has a global scheduling lock. Never apply it to a populated
        # database or narrow the original exactly-one-notification assertion.
        self.assertFalse(AgentPythonBuild.objects.exists())
        self.assertFalse(UserNotification.objects.exists())
        row = PersonalInstallation.objects.select_related("owner", "tenant", "project").get(slot=1)
        self.assertTrue(row.owner.is_active)
        self.assertFalse(row.owner.is_superuser)
        self.user = row.owner
        self.agent = Agent.objects.create(tenant=row.tenant, project=row.project,
            name="notification-guard-" + uuid4().hex, created_by=self.user)
        self.addCleanup(Agent.objects.filter(pk=self.agent.pk, tenant=row.tenant,
            project=row.project, created_by=self.user).delete)


class PersonalInboxReconcilerTests(WorkInboxPostgresGuards, unittest.TestCase):
    def setUp(self):
        require_fixture(connection.settings_dict["NAME"])
        # The original assertion invokes the actual session-level advisory lock.
        # Never run maintenance against an operator database or bypass the lock.
        self.assertFalse(Job.objects.exists())
        row = PersonalInstallation.objects.select_related("owner", "tenant", "project").get(slot=1)
        self.assertTrue(row.owner.is_active)
        self.assertFalse(row.owner.is_superuser)
        self.user, self.tenant, self.project = row.owner, row.tenant, row.project
        started = timezone.now()
        cursors_before = set(InboxWorkerCursor.objects.values_list("pk", flat=True))

        def cleanup():
            require_fixture(connection.settings_dict["NAME"])
            jobs = Job.objects.filter(tenant=self.tenant, project=self.project,
                created_by=self.user, job_type="datasets.reconcile-me", created_at__gte=started)
            ids = list(jobs.values_list("pk", flat=True))
            InboxItem.objects.filter(tenant=self.tenant, recipient=self.user,
                source_type="job", source_id__in=[str(pk) for pk in ids]).delete()
            jobs.filter(pk__in=ids).delete()
            added = set(InboxWorkerCursor.objects.values_list("pk", flat=True)) - cursors_before
            InboxWorkerCursor.objects.filter(pk__in=added).delete()

        self.addCleanup(cleanup)


class PersonalInboxConcurrentPushTests(InboxConcurrentPushGuards, unittest.TestCase):
    def setUp(self):
        require_fixture(connection.settings_dict["NAME"])
        self.assertFalse(PushDelivery.objects.exists())
        self.assertFalse(WebPushSubscription.objects.exists())
        row = PersonalInstallation.objects.select_related("owner", "tenant", "project").get(slot=1)
        self.assertTrue(row.owner.is_active)
        self.assertFalse(row.owner.is_superuser)
        self.row = row
        self.job_ids, self.item_ids, self.subscription_ids = [], [], []

        def cleanup():
            require_fixture(connection.settings_dict["NAME"])
            WebPushSubscription.objects.filter(pk__in=self.subscription_ids, user=row.owner).delete()
            InboxItem.objects.filter(pk__in=self.item_ids, tenant=row.tenant, recipient=row.owner).delete()
            Job.objects.filter(pk__in=self.job_ids, tenant=row.tenant,
                project=row.project, created_by=row.owner).delete()

        self.addCleanup(cleanup)

    def concurrent_push_fixture(self):
        row, recipients = self.row, {}
        # Personal has exactly one human owner. Two owned browser endpoints and
        # two explicit queued deliveries preserve the worker overlap invariant.
        # Signal fan-out is tested separately; real source checks remain enabled.
        with transaction.atomic():
            for index in range(2):
                marker = "personal-parallel-" + uuid4().hex
                subscription = WebPushSubscription.objects.create(user=row.owner, endpoint_hash=marker,
                    endpoint_encrypted="encrypted", p256dh_encrypted="encrypted", auth_encrypted="encrypted",
                    last_seen_at=timezone.now())
                self.subscription_ids.append(subscription.pk)
                job = Job.objects.bulk_create([Job(tenant=row.tenant, project=row.project,
                    created_by=row.owner, job_type="audit.parallel", status="succeeded",
                    completed_at=timezone.now())])[0]
                self.job_ids.append(job.pk)
                item = InboxItem.objects.create(tenant=row.tenant, project=row.project, recipient=row.owner,
                    category="background", kind="job_succeeded", state="completed",
                    audience_key=f"user:{row.owner_id}", source_type="job", source_id=str(job.pk),
                    event_key=marker, navigation_key="job", title_key="job_succeeded",
                    occurred_at=job.completed_at, resolved_at=job.completed_at)
                self.item_ids.append(item.pk)
                PushDelivery.objects.create(item=item, subscription=subscription)
                recipients[str(item.pk)] = subscription.pk
        self.assertEqual(PushDelivery.objects.count(), 2)
        return recipients


def run(expected_database):
    require_fixture(expected_database)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PersonalNotificationBuildWorkerTests)
    assert suite.countTestCases() == 1
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(PersonalInboxReconcilerTests))
    assert suite.countTestCases() == 2
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(PersonalInboxConcurrentPushTests))
    assert suite.countTestCases() == 3
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    assert result.wasSuccessful() and not result.skipped
    return {"scope": "personal-notification-postgres-guards", "tests_run": result.testsRun,
            "skipped": len(result.skipped), "vendor": connection.vendor}
