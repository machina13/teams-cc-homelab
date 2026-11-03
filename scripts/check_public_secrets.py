#!/usr/bin/env python3
"""Fail CI if tracked files contain obvious credentials or forbidden secret files.

Safety check for a future PUBLIC GitHub repo. Scans tracked files only, and
prints filenames/rule names, NEVER matching secret text. Does not replace a
history audit or GitHub secret scanning / push protection.
"""
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
RULES={
    "Groq API key":re.compile(rb"gsk_[A-Za-z0-9_-]{20,}"),
    "Dynamic DNS hostname":re.compile(rb"(?i:[a-z0-9-]+\.duckdns\.org)"),
    "Embedded LAN host:port":re.compile(
        rb"(?<![0-9])(?:192\.168|10\.[0-9]{1,3}|172\.(?:1[6-9]|2[0-9]|3[01]))\.[0-9]{1,3}\.[0-9]{1,3}:[0-9]{2,5}\b"
    ),
    "GitHub access token":re.compile(rb"(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{15,}"),
    "Slack token":re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}"),
    "AWS access key":re.compile(rb"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    "PEM private key":re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "Long Bearer token":re.compile(rb"(?i:authorization)\s*[:=]\s*[\\\"']?Bearer\s+[A-Za-z0-9._~-]{35,}"),
    "Inline credential":re.compile(
        rb"(?i:(?:api[_-]?key|access[_-]?token|password|passwd|secret))\s*[:=]\s*"
        rb"[\\\"'](?!(?:test|sample|example|dummy|fake|placeholder))[A-Za-z0-9_+./=-]{24,}[\\\"']"
    ),
}
BLOCKED_PARTS={"secrets",".ssh","private","backups","audio"}
BLOCKED_EXT={".pem",".p12",".pfx",".sqlite3",".db",".key",".webm",".wav",".mp3"}
BLOCKED_FILES={".env",".env.local",".env.production","id_rsa","id_ed25519"}

def tracked():
    result=subprocess.run(
        ["git","-C",str(ROOT),"ls-files","-z"],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True
    )
    return [ROOT/Path(x.decode("utf-8","surrogateescape"))
            for x in result.stdout.split(b"\x00") if x]

def scan_file(path):
    relative=path.relative_to(ROOT)
    problems=[]
    if (relative.name in BLOCKED_FILES
        or relative.suffix.lower() in BLOCKED_EXT
        or any(part.lower() in BLOCKED_PARTS for part in relative.parts[:-1])):
        problems.append("Private file path")
    if path.is_symlink():
        return ["Symlink must not be public"]
    try:
        data=path.read_bytes()
    except OSError:
        return ["Unreadable tracked file"]
    if len(data)>2*1024*1024:
        return problems+["File too large for scanner"]
    for label,pattern in RULES.items():
        if pattern.search(data):
            problems.append(label)
    return problems

def main():
    found=[]
    for path in tracked():
        for rule in scan_file(path):
            found.append((str(path.relative_to(ROOT)),rule))
    if found:
        print("FAIL: potential secrets in tracked repository files:",file=sys.stderr)
        for name,rule in found:
            print(" -",name,":",rule,file=sys.stderr)
        return 1
    print("PASS: no known credential signatures or secret files in tracked source.")
    return 0

if __name__=="__main__":
    sys.exit(main())
