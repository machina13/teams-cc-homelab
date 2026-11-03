"""Offline tests: do not send requests or require a real Groq API key."""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from asr_groq import TranscriptionError, transcribe

VALID_WEBM = bytes.fromhex("1a45dfa3") + b"x" * 500

class Response:
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return None
    def read(self, n):
        return b'{"text":"Una prueba de Teams en espanol."}'

class GroqTests(unittest.TestCase):
    def test_invalid_webm_rejected(self):
        with self.assertRaises(TranscriptionError) as result:
            transcribe(b"wav" * 200)
        self.assertEqual(result.exception.code, 415)
    def test_missing_key_rejected(self):
        with patch.dict(os.environ, {"GROQ_API_KEY_FILE":"/nonexistent/groq_key"}, clear=False):
            with self.assertRaises(TranscriptionError) as result:
                transcribe(VALID_WEBM)
        self.assertEqual(result.exception.code, 503)
    def test_successful_groq_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "key"
            key.write_text("test-only-key", encoding="utf8")
            with patch.dict(os.environ, {"GROQ_API_KEY_FILE":str(key), "GROQ_MODEL":"whisper-large-v3"}):
                with patch("asr_groq.urllib.request.urlopen", return_value=Response()) as client:
                    text, model = transcribe(VALID_WEBM)
        self.assertEqual(text, "Una prueba de Teams en espanol.")
        self.assertEqual(model, "whisper-large-v3")
        request = client.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.groq.com/openai/v1/audio/transcriptions")
        self.assertIn(b"filename=\"chunk.webm\"", request.data)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-only-key")
        self.assertEqual(request.get_header("User-agent"), "TeamsCaptions-Homelab/2.1")
    def test_bad_model_does_not_transmit(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "key"
            key.write_text("test-only-key", encoding="utf8")
            with patch.dict(os.environ, {"GROQ_API_KEY_FILE":str(key), "GROQ_MODEL":"invalid"}):
                with patch("asr_groq.urllib.request.urlopen") as client:
                    with self.assertRaises(TranscriptionError) as result:
                        transcribe(VALID_WEBM)
                    self.assertEqual(result.exception.code, 503)
                    client.assert_not_called()

if __name__ == "__main__":
    unittest.main()
