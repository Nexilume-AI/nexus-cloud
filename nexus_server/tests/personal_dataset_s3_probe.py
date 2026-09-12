"""Explicit real local S3 acceptance using the existing Personal installation.

Starts no Cloud, database or storage service. Supply a disposable local MinIO
endpoint with the existing shared test credentials. Only its random test bucket
is created and removed; a production endpoint is never accepted.
"""
import argparse
from contextlib import redirect_stdout
import json
import os
import re
import sys
import unittest

from django.conf import settings


def validate_endpoint(endpoint):
    match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", endpoint)
    if match is None or int(match[1]) > 65535:
        raise ValueError("Explicit disposable loopback S3 endpoint required")
    return endpoint


def test_case_type():
    # Model-bearing shared imports must follow main()'s django.setup(). Help
    # and malformed endpoints need neither a host config nor a database.
    from django.test import SimpleTestCase
    from tests.dataset_s3_guards import DatasetS3Guards

    class PersonalDatasetS3Tests(DatasetS3Guards, SimpleTestCase):
        def s3_endpoint(self):
            return validate_endpoint(self.endpoint)

    return PersonalDatasetS3Tests


def run(endpoint):
    endpoint = validate_endpoint(endpoint)
    if settings.NEXUS_DISTRIBUTION != "community":
        raise AssertionError("Existing Community host settings required")
    case = test_case_type()("test_real_128_mib_multipart_roundtrip_and_range")
    case.endpoint = endpoint
    suite = unittest.TestSuite([case])
    with redirect_stdout(sys.stderr):
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    assert result.testsRun == 1 and result.wasSuccessful() and not result.skipped
    return {"scope": "personal-dataset-s3-guards", "tests_run": result.testsRun,
            "skipped": len(result.skipped), "payload_bytes": 128 * 1024 * 1024}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    args = parser.parse_args()
    endpoint = validate_endpoint(args.endpoint)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nexus_personal.settings")
    import django
    django.setup()
    print(json.dumps(run(endpoint)))


if __name__ == "__main__":
    main()
