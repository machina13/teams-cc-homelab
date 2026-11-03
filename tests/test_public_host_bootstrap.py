"""Tests for standalone public GitHub updater migration without credentials."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import upgrade_noauth_host as bootstrap


class TestPublicHostBootstrap(unittest.TestCase):
    def test_preserves_extension_and_native_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            cfgdir = home / ".config" / "teamscc-updater"
            cfgdir.mkdir(parents=True)
            ext = home / "Downloads" / "teams-captions-extension (3)"
            ext.mkdir(parents=True)
            manifest = {"name": bootstrap.EXTENSION_NAME,
                        "permissions": ["nativeMessaging"], "version": "2.2.1"}
            (ext / "manifest.json").write_text(json.dumps(manifest))
            ext_id = "a" * 32
            config = {"extension_id": ext_id, "branch": "main",
                      "extension_dir": str(ext), "installed_commit": "b"*40}
            (cfgdir / "config.json").write_text(json.dumps(config))
            root = home / ".local" / "share" / "teamscc-updater"
            root.mkdir(parents=True)
            (root / "native_host").write_text("#!/bin/sh\necho old-host\n")
            native = home / ".config" / "google-chrome" / "NativeMessagingHosts"
            native.mkdir(parents=True)
            registered = native / (bootstrap.HOST_NAME + ".json")
            registered.write_text(json.dumps({
                "path": str(root / "native_host"),
                "allowed_origins": ["chrome-extension://" + ext_id + "/"]}))
            host_bytes = b'PUBLIC_API = "https://api.github.com"\ndef public_extension(): pass\n'
            with patch.object(bootstrap.os,"geteuid",return_value=1000):
                result = bootstrap.install(home=home, download=lambda:host_bytes)
            self.assertEqual(result,ext_id)
            self.assertEqual((root / "native_update_host.py").read_bytes(),host_bytes)
            self.assertIn("native_update_host.py",(root / "native_host").read_text())
            self.assertEqual((root / "native_host.pre-noauth.bak").read_text(),
                             "#!/bin/sh\necho old-host\n")
            self.assertEqual(json.loads((cfgdir / "config.json").read_text()),config)
            self.assertEqual(json.loads((ext / "manifest.json").read_text()),manifest)
            self.assertEqual(json.loads(registered.read_text())["allowed_origins"],
                             ["chrome-extension://" + ext_id + "/"])

    def test_migrates_legacy_branch_to_public_main_and_preserves_config(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            cfgdir = home / ".config" / "teamscc-updater"
            cfgdir.mkdir(parents=True)
            ext = home / "Downloads" / "teams-captions-extension (3)"
            ext.mkdir(parents=True)
            manifest = {"name": bootstrap.EXTENSION_NAME,
                        "permissions": ["nativeMessaging"], "version": "2.2.1"}
            (ext / "manifest.json").write_text(json.dumps(manifest))
            ext_id = "a" * 32
            old_cfg = {"extension_id": ext_id, "branch": "legacy/private-branch",
                       "extension_dir": str(ext), "repo_dir": str(home/"old-repo"),
                       "installed_commit": "b"*40}
            config = cfgdir / "config.json"
            config.write_text(json.dumps(old_cfg))
            root = home / ".local" / "share" / "teamscc-updater"
            root.mkdir(parents=True)
            (root / "native_host").write_text("#!/bin/sh\necho old-host\n")
            native = home / ".config" / "google-chrome" / "NativeMessagingHosts"
            native.mkdir(parents=True)
            registered = native / (bootstrap.HOST_NAME + ".json")
            registered.write_text(json.dumps({
                "path": str(root / "native_host"),
                "allowed_origins": ["chrome-extension://" + ext_id + "/"]}))
            fake_host = b'PUBLIC_API = "https://api.github.com"\ndef public_extension(): pass\n'
            with patch.object(bootstrap.os, "geteuid", return_value=1000):
                result = bootstrap.install(home=home, download=lambda: fake_host)
            self.assertEqual(result, ext_id)
            after = json.loads(config.read_text())
            self.assertEqual(after["branch"], "main")
            for key in ("extension_id", "extension_dir", "repo_dir", "installed_commit"):
                self.assertEqual(after[key], old_cfg[key])
            saved = json.loads((cfgdir/"config.pre-noauth.json").read_text())
            self.assertEqual(saved, old_cfg)
            self.assertEqual(json.loads((ext/"manifest.json").read_text()), manifest)
            self.assertEqual(
                json.loads(registered.read_text())["allowed_origins"],
                ["chrome-extension://" + ext_id + "/"]
            )
            self.assertEqual((root/"native_update_host.py").read_bytes(), fake_host)

            # Safe to run the repair more than once; original backup is retained.
            with patch.object(bootstrap.os, "geteuid", return_value=1000):
                bootstrap.install(home=home, download=lambda: fake_host)
            self.assertEqual(
                json.loads((cfgdir/"config.pre-noauth.json").read_text()), old_cfg
            )

    def test_rejects_root_execution(self):
        with patch.object(bootstrap.os,"geteuid",return_value=0):
            with self.assertRaisesRegex(ValueError,"sin sudo"):
                bootstrap.install(home=Path("/unused"), download=lambda:b"")


if __name__ == "__main__":
    unittest.main()
