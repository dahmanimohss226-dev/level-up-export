"""Tests for health endpoint and version reporting."""

import unittest
from alephgeo import __version__
from alephgeo.dashboard import Dashboard


class HealthEndpointTests(unittest.TestCase):
    def test_health_endpoint_reports_ready_and_version(self):
        server = Dashboard.__new__(Dashboard)
        for path in (["health"], ["api", "health"]):
            with self.subTest(path=path):
                response = server.handle("GET", path, {}, None)
                self.assertIsInstance(response, dict)
                self.assertEqual(response.get("status"), "ready")
                self.assertEqual(response.get("version"), __version__)


if __name__ == "__main__":
    unittest.main()
