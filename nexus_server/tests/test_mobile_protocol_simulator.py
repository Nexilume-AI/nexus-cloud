from __future__ import annotations

import base64
from unittest import TestCase

from nexus_mobile.tools.mobile_protocol_simulator import MobileProtocolSimulator


class MobileProtocolSimulatorTests(TestCase):
    def setUp(self) -> None:
        self.simulator = MobileProtocolSimulator(
            "https://nexus.example.test",
            "00000000-0000-0000-0000-000000000001",
            "secret-pairing-token",
        )

    def test_deterministic_accessibility_flow_preserves_unicode(self) -> None:
        self.simulator.execute({"action": "open_app", "arguments": {"package": "com.nexus.mobile"}})
        self.simulator.execute({"action": "tap_text", "arguments": {"text": "Open Mobile E2E Surface"}})
        self.simulator.execute({"action": "tap_text", "arguments": {"text": "E2E message"}})
        marker = "中文验证 🧭 · run-001"
        self.simulator.execute({"action": "type_text", "arguments": {"text": marker}})
        self.simulator.execute({"action": "tap_text", "arguments": {"text": "Apply"}})
        result = self.simulator.execute({"action": "observe", "arguments": {}})
        self.assertIn(marker, str(result["nodes"]))

        self.simulator.execute({"action": "swipe", "arguments": {}})
        self.simulator.execute({"action": "wait_for_state", "arguments": {"text": "End of Mobile E2E Surface"}})
        screen = self.simulator.execute({"action": "capture_screen", "arguments": {}})
        self.assertTrue(base64.b64decode(screen["screenshot_base64"]).startswith(b"\x89PNG"))
        self.assertEqual((screen["width"], screen["height"]), (320, 180))

    def test_pairing_token_is_not_exposed_by_object_repr(self) -> None:
        self.assertNotIn("secret-pairing-token", repr(self.simulator))
