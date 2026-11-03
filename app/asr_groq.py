"""Groq Whisper transcription adapter. Audio is processed in memory, never logged."""
import json
import os
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from groq_settings import GROQ_USER_AGENT

ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
ALLOWED_MODELS = {"whisper-large-v3", "whisper-large-v3-turbo"}
MAX_AUDIO = 8 * 1024 * 1024

class TranscriptionError(Exception):
    def __init__(self, code, reason):
        super().__init__(reason)
        self.code, self.reason = code, reason

def transcribe(audio):
    if not 100 <= len(audio) <= MAX_AUDIO or not audio.startswith(b"\x1a\x45\xdf\xa3"):
        raise TranscriptionError(415, "invalid_webm_audio")
    path = Path(os.environ.get("GROQ_API_KEY_FILE", "/run/secrets/groq_api_key"))
    try:
        api_key = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise TranscriptionError(503, "groq_not_configured") from None
    if not api_key:
        raise TranscriptionError(503, "groq_not_configured")
    model = os.environ.get("GROQ_MODEL", "whisper-large-v3")
    if model not in ALLOWED_MODELS:
        raise TranscriptionError(503, "invalid_groq_model")
    boundary = "----teamscc" + secrets.token_hex(12)
    def field(name, value):
        return (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
        ).encode("utf-8")
    body = field("model", model) + field("response_format", "json")
    # Automatic multilingual detection; do not force Spanish on mixed-language meetings.
    body += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"chunk.webm\"\r\n"
        "Content-Type: audio/webm\r\n\r\n"
    ).encode("ascii") + audio + f"\r\n--{boundary}--\r\n".encode("ascii")
    req = urllib.request.Request(
        ENDPOINT, data=body, method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "Accept": "application/json",
            "User-Agent": GROQ_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=65) as res:
            result = json.loads(res.read(1024 * 1024))
    except urllib.error.HTTPError as e:
        # Never echo remote response bodies: they may include sensitive request details.
        if e.code == 429:
            raise TranscriptionError(429, "groq_rate_limited") from None
        raise TranscriptionError(502, "groq_upstream_error") from None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        raise TranscriptionError(502, "groq_unavailable") from None
    transcript = result.get("text") if isinstance(result, dict) else None
    if not isinstance(transcript, str):
        raise TranscriptionError(502, "groq_invalid_response")
    return transcript.strip(), model
