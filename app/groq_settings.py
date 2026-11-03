"""Local-only Groq key management for Teams CC.

Never return, log, or render the key. The main application must authenticate
requests AND restrict the endpoint to a LAN/loopback client.
"""
import ipaddress
import json
import os
import re
import secrets
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

GROQ_KEY_PATH = Path(os.environ.get("GROQ_API_KEY_FILE", "/data/private/groq_api_key"))
KEY_PATTERN = re.compile(r"^gsk_[A-Za-z0-9_-]{16,256}$")
LOCAL_NETS = tuple(ipaddress.ip_network(net) for net in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128",
))
API_MODELS_URL = "https://api.groq.com/openai/v1/models"
# Identify our integration transparently; the default Python-urllib user agent
# triggers Cloudflare 1010, before requests reach Groq authentication.
GROQ_USER_AGENT = "TeamsCaptions-Homelab/2.1"

class KeyErrorSafe(Exception):
    def __init__(self, error, status=400):
        super().__init__(error)
        self.error = error
        self.status = status

def permitted_client(address):
    try:
        ip = ipaddress.ip_address(address)
        return any(ip in network for network in LOCAL_NETS)
    except ValueError:
        return False

def key_status():
    try:
        if GROQ_KEY_PATH.is_symlink():
            return False
        return GROQ_KEY_PATH.is_file() and GROQ_KEY_PATH.stat().st_size >= 20
    except OSError:
        return False

def save_key(value):
    if not isinstance(value,str) or not KEY_PATTERN.fullmatch(value):
        raise KeyErrorSafe("invalid_groq_key_format", 400)
    folder=GROQ_KEY_PATH.parent
    if folder.is_symlink() or GROQ_KEY_PATH.is_symlink():
        raise KeyErrorSafe("unsafe_secret_path", 500)
    tmp=None
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder.chmod(0o700)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="ascii", dir=folder,
            prefix=".groq-", suffix=".tmp", delete=False
        ) as output:
            tmp=Path(output.name)
            os.fchmod(output.fileno(),0o600)
            output.write(value + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp,GROQ_KEY_PATH)
        GROQ_KEY_PATH.chmod(0o600)
    except (OSError, UnicodeError):
        if tmp: tmp.unlink(missing_ok=True)
        raise KeyErrorSafe("groq_key_store_failed", 500) from None

def check_key():
    if not key_status():
        raise KeyErrorSafe("groq_not_configured", 503)
    try:
        token=GROQ_KEY_PATH.read_text(encoding="ascii").strip()
        req=urllib.request.Request(
            API_MODELS_URL,
            headers={"Authorization":"Bearer "+token,"Accept":"application/json",
                     "User-Agent":GROQ_USER_AGENT}
        )
        with urllib.request.urlopen(req,timeout=12) as response:
            payload=json.loads(response.read(512*1024))
        if not isinstance(payload,dict) or not isinstance(payload.get("data"),list):
            raise KeyErrorSafe("groq_unexpected_response",502)
        return {"ok":True,"valid":True,"model":"whisper-large-v3"}
    except urllib.error.HTTPError as err:
        if err.code == 401:
            raise KeyErrorSafe("groq_key_unauthorized", 422) from None
        if err.code == 403:
            # A Cloudflare 1010 response is text/plain and is not a Groq
            # project permissions error. Read only a few bytes, never log data.
            if b"error code: 1010" in err.read(128).lower():
                raise KeyErrorSafe("groq_client_rejected",502) from None
            raise KeyErrorSafe("groq_permission_denied", 422) from None
        if err.code==429:
            raise KeyErrorSafe("groq_rate_limited",429) from None
        raise KeyErrorSafe("groq_api_unavailable",502) from None
    except (urllib.error.URLError,TimeoutError,OSError,ValueError):
        raise KeyErrorSafe("groq_network_unavailable",502) from None
