"""Shared process-health checks on the Personal host, without a fake runner."""
from django.test import SimpleTestCase

from tests.runtime_health_guards import BuilderHeartbeatGuards, ImageSweeperGuards


class PersonalBuilderHeartbeatTests(BuilderHeartbeatGuards, SimpleTestCase):
    pass


class PersonalImageSweeperTests(ImageSweeperGuards, SimpleTestCase):
    pass
