"""Shared streaming unit guards under the real Personal component composition."""
from django.test import SimpleTestCase
from tests.dataset_streaming_guards import DatasetStreamingGuards


class PersonalDatasetStreamingTests(DatasetStreamingGuards, SimpleTestCase):
    pass
