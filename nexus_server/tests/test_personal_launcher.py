"""The public launcher must select only the independently configured host."""
from pathlib import Path
import os
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "start-nexus-community.ps1"


class CommunityLauncherTests(unittest.TestCase):
    def test_launcher_has_no_enterprise_or_separately_released_runtime_dependency(self):
        source = LAUNCHER.read_text(encoding="utf-8-sig")
        self.assertIn("nexus_personal.install check", source)
        self.assertIn("nexus_personal.processes", source)
        self.assertIn("nexus_personal.settings", source)
        self.assertIn("if ([bool]$check.python_builder_enabled) { $services.Add('python-builder') }", source)
        self.assertIn("Push-Location -LiteralPath $serverRoot", source)
        self.assertIn("Pop-Location", source)
        self.assertIn("if ($CheckOnly -and $null -ne $existing) {\n    foreach ($entry in @($existing.processes))", source)
        self.assertIn("if (-not $CheckOnly) {\n    $listener = Get-NetTCPConnection", source)
        self.assertIn("if ($SkipMigrations -or $CheckOnly)", source)
        self.assertNotIn("config.asgi", source)
        self.assertNotIn("nexus_enterprise", source)
        self.assertNotIn("nexus_openwrt", source)
        self.assertNotIn("vite", source.casefold())
        self.assertNotIn("docker", source.casefold())

    def test_launcher_is_valid_powershell_and_missing_installation_starts_nothing(self):
        parser = ("$tokens=$null;$errors=$null;"
                  "[void][System.Management.Automation.Language.Parser]::ParseFile("
                  "$env:NEXUS_LAUNCHER_TEST_PATH,[ref]$tokens,[ref]$errors);"
                  "if($errors.Count){exit 1}")
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", parser],
                       env={**os.environ, "NEXUS_LAUNCHER_TEST_PATH": str(LAUNCHER)},
                       check=True, capture_output=True, timeout=15)
        missing = ROOT / ".local" / "community-launcher-missing-fixture"
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(LAUNCHER),
                                 "-InstallationDirectory", str(missing)], capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"COMMUNITY_INSTALLATION_REQUIRED", result.stderr + result.stdout)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
