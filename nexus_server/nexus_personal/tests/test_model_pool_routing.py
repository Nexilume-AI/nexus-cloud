"""Original Model Pool guards through Personal owner CSRF and real upstream discovery."""
from django.test import TestCase, override_settings
from tests.model_pool_routing_guards import ModelPoolRoutingGuards
from .deployment_guard_fixture import PersonalDeploymentGuardFixture


@override_settings(ROOT_URLCONF="nexus_personal.urls", NEXUS_PROVIDER_ALLOW_HTTP=True,
                   NEXUS_PROVIDER_ALLOWED_PRIVATE_HOSTS="127.0.0.1")
class PersonalModelPoolRoutingTests(ModelPoolRoutingGuards, PersonalDeploymentGuardFixture, TestCase):
    pass
