"""Actual owner HTTP and database transitions for shared Inbox guarantees."""
from django.test import TestCase

from tests.work_inbox_guards import WorkInboxGuards
from . import test_notification_sources as source_tests


class PersonalWorkInboxTests(WorkInboxGuards, TestCase):
    # The personal schema contains Runtime Computers, not legacy SSH credentials.
    # This is an operational-notice fixture, not a Computer execution test.
    work_inbox_connection_fields = {"connection_type": "runtime"}
    setUp = source_tests.PersonalNotificationSourceTests.setUp

    def new_run(self, *, status="completed"):
        values = {"status": status, "write_token": "DO_NOT_RETURN"}
        if status not in {"completed", "failed"}:
            values["completed_at"] = None
        return source_tests.PersonalNotificationSourceTests.new_run(self, **values)

    def get(self, path, data=None):
        return self.client.get(f"/api/v1/inbox/{path}", data or {}, **self.headers)

    def post(self, path, data=None):
        return self.client.post(f"/api/v1/inbox/{path}", data or {}, format="json", **self.headers)

    @staticmethod
    def inbox_payload(response):
        # Both hosts read the real response; only Enterprise uses a data envelope.
        return response.json()
