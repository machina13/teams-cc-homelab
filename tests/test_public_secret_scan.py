"""Regression tests for the public-source credential scanner."""
import unittest
from scripts import check_public_secrets as scanner


class TestPublicSecretScanner(unittest.TestCase):
    def test_dynamic_dns_hostname_detected(self):
        hostname = "test-host" + ".duckdns" + ".org"
        self.assertIsNotNone(scanner.RULES["Dynamic DNS hostname"].search(hostname.encode()))

    def test_private_lan_host_port_detected(self):
        for prefix in ("192" + ".168", "10" + ".254", "172" + ".31"):
            endpoint = "http://" + prefix + ".254.253:54321/"
            with self.subTest(endpoint=prefix):
                self.assertIsNotNone(
                    scanner.RULES["Embedded LAN host:port"].search(endpoint.encode())
                )

    def test_hardcoded_private_host_without_port_is_detected(self):
        for prefix in ("192" + ".168", "10" + ".254", "172" + ".31"):
            private_ip = prefix + ".254.253"
            self.assertIsNotNone(
                scanner.RULES["Hardcoded private IPv4 host"].search(private_ip.encode())
            )

    def test_private_deployment_name_is_rejected(self):
        identity = "Docker" + " Labs"
        self.assertIsNotNone(
            scanner.RULES["Internal deployment name"].search(identity.encode())
        )

    def test_expected_safe_urls_and_loopback_not_blocked(self):
        for endpoint in ("https://example.test", "http://127.0.0.1:8765"):
            self.assertIsNone(
                scanner.RULES["Embedded LAN host:port"].search(endpoint.encode())
            )


if __name__ == "__main__":
    unittest.main()
