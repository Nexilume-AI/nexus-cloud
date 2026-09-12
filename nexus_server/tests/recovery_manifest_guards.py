"""Portable storage integrity assertions shared by both distribution hosts."""
import tempfile
from pathlib import Path

from apps.common.management.commands.disaster_recovery_manifest import storage_manifest


class RecoveryStorageGuards:
    def test_storage_manifest_hashes_content_without_exposing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "agents" / "run-output.txt"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("private recovery canary", encoding="utf-8")
            (root / ".nexus-readiness-transient").write_text("ignore", encoding="utf-8")

            result = storage_manifest(root)

        self.assertEqual(set(result), {"agents/run-output.txt"})
        self.assertEqual(result["agents/run-output.txt"]["size"], len("private recovery canary"))
        self.assertNotIn("private recovery canary", str(result))
