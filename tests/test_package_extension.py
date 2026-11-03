"""Public release artifact must contain all referenced extension scripts."""
import json
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestPublicReleasePackage(unittest.TestCase):
    def test_zip_includes_every_manifest_script_without_private_files(self):
        process = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "package_extension.py")],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, check=False
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        package = ROOT / "teams-cc-groq-v2.zip"
        self.assertTrue(package.is_file())
        with zipfile.ZipFile(str(package)) as archive:
            files = set(archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["version"], "2.3.0")
            required = {
                manifest["background"]["service_worker"],
                manifest["action"]["default_popup"],
                manifest["options_ui"]["page"],
            }
            for content_script in manifest["content_scripts"]:
                required.update(content_script["js"])
            self.assertTrue(required.issubset(files), "Missing manifest scripts: " + str(required-files))
            self.assertIn("meeting_prompt.js", files)
            self.assertFalse(any(
                path.startswith(".") or "/secrets/" in path or
                path.endswith((".env", ".key", ".pem", ".sqlite3", ".webm"))
                for path in files
            ))


if __name__ == "__main__":
    unittest.main()
