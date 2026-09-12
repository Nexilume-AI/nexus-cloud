"""Original connection HTTP guards through the Personal owner API."""
from django.test import TestCase, override_settings
from tests.provider_connection_http_guards import ProviderConnectionHTTPGuards
from .provider_connection_fixture import PersonalProviderConnectionFixture


@override_settings(ROOT_URLCONF="nexus_personal.urls")
class PersonalProviderConnectionHTTPTests(ProviderConnectionHTTPGuards, PersonalProviderConnectionFixture, TestCase):
    pass
