"""Personal recovery comparison and real file integrity, not a restore drill."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from apps.common.management.commands.disaster_recovery_manifest import compare_manifests, storage_manifest
from tests.recovery_manifest_guards import RecoveryStorageGuards


class PersonalRecoveryManifestTests(RecoveryStorageGuards, SimpleTestCase):
    def manifest(self):
        return {
            "schema_version": 1,
            "database": {
                "vendor": "postgresql",
                "migration_leaves": ["personal.0001_initial"],
                "tables": {"agents_agentdisplayrun": {"row_count": 2, "content_digest": "abc"}},
                "sequences": {"audit_id_seq": {"last_value": 7}},
            },
            "storage": {"files": {"agents/output.txt": {"size": 4, "sha256": "def"}}},
        }

    def test_matching_manifest_preserves_counts_without_mutating_inputs(self):
        expected = self.manifest()
        current = deepcopy(expected)
        current["captured_at"] = "a later snapshot"
        result = compare_manifests(expected=expected, current=current)
        self.assertTrue(result["passed"])
        self.assertEqual(result["mismatches"], [])
        self.assertEqual((result["table_count"], result["storage_file_count"]), (1, 1))
        self.assertEqual(expected, self.manifest())
        self.assertEqual(current, {**expected, "captured_at": "a later snapshot"})

    def test_each_database_and_storage_difference_is_reported(self):
        expected = self.manifest()
        for path, value, message in (
            (("schema_version",), 2, "manifest schema version differs"),
            (("database", "vendor"), "different", "database vendor differs"),
            (("database", "migration_leaves"), [], "migration leaf set differs"),
            (("database", "tables", "agents_agentdisplayrun"), {"row_count": 1},
             "changed database table: agents_agentdisplayrun"),
            (("database", "sequences", "audit_id_seq"), {"last_value": 8},
             "changed database sequence: audit_id_seq"),
            (("storage", "files", "agents/output.txt"), {"size": 4, "sha256": "changed"},
             "changed storage file: agents/output.txt"),
        ):
            with self.subTest(path=path):
                current = deepcopy(expected)
                target = current
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                result = compare_manifests(expected=expected, current=current)
                self.assertFalse(result["passed"])
                self.assertEqual(result["mismatches"], [message])
                self.assertEqual(result["mismatch_count"], 1)

    def test_missing_and_unexpected_entries_are_not_silently_ignored(self):
        for section, field, label in (("database", "tables", "database table"),
                                      ("database", "sequences", "database sequence"),
                                      ("storage", "files", "storage file")):
            with self.subTest(section=section, field=field):
                expected = self.manifest()
                current = deepcopy(expected)
                key, value = current[section][field].popitem()
                current[section][field]["extra"] = value
                result = compare_manifests(expected=expected, current=current)
                self.assertFalse(result["passed"])
                self.assertCountEqual(result["mismatches"], [f"missing {label}: {key}", f"unexpected {label}: extra"])

    def test_real_same_size_file_change_and_deletion_fail_comparison(self):
        with TemporaryDirectory(prefix="nexus-recovery-guard-") as directory:
            root = Path(directory)
            artifact = root / "result.txt"
            artifact.write_bytes(b"private-a")
            expected = {**self.manifest(), "storage": {"files": storage_manifest(root)}}
            artifact.write_bytes(b"private-b")
            current = {**self.manifest(), "storage": {"files": storage_manifest(root)}}
            result = compare_manifests(expected=expected, current=current)
            self.assertEqual(result["mismatches"], ["changed storage file: result.txt"])
            self.assertFalse(result["passed"])
            self.assertNotIn("private-a", str(expected))
            self.assertNotIn("private-b", str(current))
            artifact.unlink()
            current["storage"]["files"] = storage_manifest(root)
            self.assertEqual(compare_manifests(expected=expected, current=current)["mismatches"],
                             ["missing storage file: result.txt"])
