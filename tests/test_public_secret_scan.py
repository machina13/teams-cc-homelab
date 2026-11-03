"""Regression tests for the public-source credential scanner."""
import unittest
from scripts import check_public_secrets as scanner


class TestPublicSecretScanner(unittest.TestCase):
    def test_dynamic_dns_hostname_detected(self):
        hostname = "test-host" + ".duckdns" + ".org"
        self.assertIsNotNone(scanner.RULES["Dynamic DNS hostname"].search(hostname.encode()))

    def test_private_lan_host_port_detected(self):
        for prefix in ("192" + ".168", "10" + ".6", "172" + ".20"):
            endpoint = "http://" + prefix + ".1.200:8123/"
            with self.subTest(endpoint=prefix):
                self.assertIsNotNone(
                    scanner.RULES["Embedded LAN host:port"].search(endpoint.encode())
                )

    def test_expected_safe_urls_and_loopback_not_blocked(self):
        for endpoint in ("https://example.test", "http://127.0.0.1:8765"):
            self.assertIsNone(
                scanner.RULES["Embedded LAN host:port"].search(endpoint.encode())
            )


if __name__ == "__main__":
    unittest.main()
