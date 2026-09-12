"""Original recovery scenarios through the Personal owner's authenticated API."""
from django.test import TestCase, override_settings
from tests.provider_lifecycle_recovery_guards import ProviderLifecycleRecoveryGuards
from .provider_connection_fixture import PersonalProviderConnectionFixture


@override_settings(
    ROOT_URLCONF="nexus_personal.urls",
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                        "LOCATION": "personal-provider-lifecycle-recovery"}},
)
class PersonalProviderLifecycleRecoveryTests(ProviderLifecycleRecoveryGuards, PersonalProviderConnectionFixture, TestCase):
    pass
