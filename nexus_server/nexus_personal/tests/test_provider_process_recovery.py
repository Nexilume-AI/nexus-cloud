"""Run the original process-recovery unit guards without a financial fixture."""
from django.test import SimpleTestCase
from tests.provider_process_recovery_guards import ProviderProcessRecoveryGuards


class PersonalProviderProcessRecoveryTests(ProviderProcessRecoveryGuards, SimpleTestCase):
    pass
