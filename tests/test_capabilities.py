from pathlib import Path
import unittest
from unittest.mock import patch

from sentinel.platform.capabilities import CapabilityState, detect_capabilities
from sentinel.platform.detection import PlatformInfo

class CapabilityTests(unittest.TestCase):
    def test_journal_capability_tracks_journalctl_availability(self) -> None:
        info = PlatformInfo(True, "Linux", "kernel", "x86_64", True, True)
        with patch("sentinel.platform.capabilities.which", side_effect=lambda name: "/bin/tool" if name == "journalctl" else None):
            capabilities = {item.name: item.state for item in detect_capabilities(info, Path("/missing-proc"))}
        self.assertEqual(capabilities["journal"], CapabilityState.SUPPORTED)
