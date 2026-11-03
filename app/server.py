#!/usr/bin/env python3
"""Teams CC collector with opt-in Groq Whisper audio transcription."""
import hmac, json, os, re, sqlite3, threading, urllib.parse, shutil, tempfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from asr_groq import MAX_AUDIO, TranscriptionError, transcribe
from groq_settings import KeyErrorSafe, check_key, key_status, permitted_client, save_key

ROOT = Path("/app")
DB = Path("/data/transcripts.sqlite3")
TOKEN = Path("/run/secrets/teams_token").read_text().strip()
LOCK = threading.RLock()
AUDIO_LOCK = threading.RLock()
ASR_RUN_LOCK = threading.Lock()
AUDIO_DIR = Path("/data/audio")
AUDIO_QUOTA_BYTES = 2 * 1024**3
MIN_FREE_BYTES = 1024**3
BIND = "0.0.0.0"
PORT = 8765
ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,100}$")
ORIGIN_RE = re.compile(r"^chrome-extension://[a-p]{32}$")

def connect():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=15000")
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS meetings (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            started_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS captions (
            meeting_id TEXT NOT NULL,
            caption_id TEXT NOT NULL,
            speaker TEXT NOT NULL,
            content TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            PRIMARY KEY(meeting_id, caption_id),
            FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS captions_meeting_time ON captions(meeting_id, captured_at);
        CREATE TABLE IF NOT EXISTS audio_segments (
            meeting_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK(sequence BETWEEN 0 AND 9999),
            content TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            model TEXT NOT NULL,
            PRIMARY KEY(meeting_id, sequence),
            FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS asr_meeting_time ON audio_segments(meeting_id, captured_at);
        CREATE TABLE IF NOT EXISTS audio_files (
            meeting_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK(sequence BETWEEN 0 AND 9999),
            filename TEXT NOT NULL,
            bytes INTEGER NOT NULL,
            captured_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'stored',
            error TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(meeting_id,sequence),
            FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
        );
        """)
        # After a restart an interrupted job is not resumed automatically; a user
        # must explicitly request transcription again.
        c.execute("UPDATE audio_files SET status='stored',error='' WHERE status IN ('queued','processing')")

CHILE_TIMEZONE = ZoneInfo("America/Santiago")

def format_chile_timestamp(value):
    """Human representation only; original ISO UTC values remain unchanged."""
    if not value:
        return "Sin fecha"
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        local = timestamp.astimezone(CHILE_TIMEZONE)
        offset = local.strftime("%z")
        readable_offset = offset[:3] + ":" + offset[3:]
        return local.strftime("%d-%m-%Y %H:%M:%S") + " (Chile UTC" + readable_offset + ")"
    except (ValueError, TypeError, OverflowError):
        return str(value)

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TeamsCaptions/1.0"
    def log_message(self, *_): pass  # Avoid meeting metadata/tokens in access logs
    def _headers(self, status=200, ctype="application/json; charset=utf-8", size=None, filename=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        origin = self.headers.get("Origin", "")
        if ORIGIN_RE.fullmatch(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Meeting-Id, X-Sequence, X-Captured-At, X-Started-At")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(size if size is not None else 0))
        self.end_headers()
    def send_bytes(self, b, status=200, ctype="application/json; charset=utf-8", filename=None):
        self._headers(status, ctype, len(b), filename)
        self.wfile.write(b)
    def send_json(self, obj, status=200):
        self.send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"), status)
    def authorized(self):
        hdr = self.headers.get("Authorization", "")
        if not hmac.compare_digest(hdr, "Bearer " + TOKEN):
            self.send_json({"error":"unauthorized"}, 401)
            return False
        return True
    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "")
        if not ORIGIN_RE.fullmatch(origin):
            self._headers(403)
            return
        self._headers(204)
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/health":
            return self.send_json({"status":"ok", "service":"teams-captions", "ai":False})
        static = {"/":("index.html","text/html; charset=utf-8"),
                  "/app.js":("app.js","application/javascript; charset=utf-8"),
                  "/ca.crt":("ca.crt","application/x-x509-ca-cert")}
        if path in static:
            name, mime = static[path]
            file = ROOT / name
            if not file.exists(): return self.send_json({"error":"not_found"}, 404)
            return self.send_bytes(file.read_bytes(), ctype=mime, filename="teams-captions-extension.zip" if name.endswith(".zip") else None)
        if not path.startswith("/api/"): return self.send_json({"error":"not_found"},404)
        if not self.authorized(): return
        if path == "/api/settings/groq":
            if not permitted_client(self.client_address[0]):
                return self.send_json({"error":"lan_only"},403)
            return self.send_json({"configured":key_status(),"model":"whisper-large-v3"})

        audio_match = re.fullmatch(r"/api/meetings/([A-Za-z0-9_-]{4,100})/audio/([0-9]{1,4})",path)
        if audio_match:
            mid,seq = audio_match.groups()
            with connect() as db:
                rec=db.execute("SELECT filename FROM audio_files WHERE meeting_id=? AND sequence=?",
                               (mid,int(seq))).fetchone()
            if not rec:return self.send_json({"error":"audio_not_found"},404)
            file=AUDIO_DIR / mid / rec["filename"]
            try:
                audio=file.read_bytes()
            except OSError:
                return self.send_json({"error":"audio_unavailable"},404)
            return self.send_bytes(audio,ctype="audio/webm",filename=f"teams-{mid}-{seq}.webm")
        with connect() as db:
            if path == "/api/meetings":
                rows = db.execute("""
                SELECT m.id,m.title,m.started_at,m.last_seen_at,COUNT(c.caption_id) AS caption_count,
                    (SELECT COUNT(*) FROM audio_segments a WHERE a.meeting_id=m.id) AS asr_count,
                    (SELECT COUNT(*) FROM audio_files f WHERE f.meeting_id=m.id) AS audio_count
                FROM meetings m LEFT JOIN captions c ON c.meeting_id=m.id
                GROUP BY m.id ORDER BY m.last_seen_at DESC LIMIT 200""").fetchall()
                return self.send_json({"meetings":[dict(x) for x in rows]})
            match = re.fullmatch(r"/api/meetings/([A-Za-z0-9_-]{4,100})(?:/(txt|json))?", path)
            if not match: return self.send_json({"error":"not_found"},404)
            meeting_id, fmt = match.groups()
            m = db.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            if not m: return self.send_json({"error":"not_found"},404)
            rows = db.execute("SELECT speaker,content,captured_at FROM captions WHERE meeting_id=? ORDER BY captured_at, rowid",(meeting_id,)).fetchall()
            items = [dict(r) for r in rows]
            asr = [dict(r) for r in db.execute(
                "SELECT sequence,content,captured_at,model FROM audio_segments WHERE meeting_id=? ORDER BY sequence",
                (meeting_id,)).fetchall()]
            audio_files=[dict(r) for r in db.execute(
                "SELECT sequence,bytes,captured_at,status,error FROM audio_files WHERE meeting_id=? ORDER BY sequence",
                (meeting_id,)).fetchall()]
            if fmt == "txt":
                text = f"{m['title']}\nInicio: {format_chile_timestamp(m['started_at'])}\nZona horaria: America/Santiago\n\n" + "\n".join(
                    f"[{format_chile_timestamp(r['captured_at'])}] {r['speaker']}: {r['content']}" for r in items)
                if asr:
                    text += "\n\n--- TRANSCRIPCIÓN GROQ (ASR; hablantes no identificados) ---\n" + "\n".join(
                        f"[{format_chile_timestamp(r['captured_at'])}] {r['content']}" for r in asr)
                return self.send_bytes(text.encode(),ctype="text/plain; charset=utf-8",filename=f"teams-{meeting_id}.txt")
            payload = {"meeting":dict(m), "captions":items, "asr_segments":asr, "audio_files":audio_files}
            if fmt == "json":
                payload["timezone"] = "America/Santiago"
                payload["meeting"]["started_at_chile"] = format_chile_timestamp(m["started_at"])
                for caption in payload["captions"]:
                    caption["captured_at_chile"] = format_chile_timestamp(caption["captured_at"])
                for segment in payload["asr_segments"]:
                    segment["captured_at_chile"] = format_chile_timestamp(segment["captured_at"])
            return self.send_bytes(json.dumps(payload,ensure_ascii=False,indent=2).encode(),ctype="application/json; charset=utf-8",
                                   filename=f"teams-{meeting_id}.json" if fmt=="json" else None)
    def post_audio(self):
        """Durable audio ingestion only. Groq is never contacted in this path."""
        if not self.authorized(): return
        meeting_id = self.headers.get("X-Meeting-Id", "")
        sequence = self.headers.get("X-Sequence", "")
        started_at = self.headers.get("X-Started-At", "")
        captured_at = self.headers.get("X-Captured-At", "")
        size = self.headers.get("Content-Length", "")
        ctype = self.headers.get("Content-Type", "").split(";")[0].lower().strip()
        if not ID_RE.fullmatch(meeting_id) or not sequence.isdecimal() or not (0 <= int(sequence) <= 9999):
            return self.send_json({"error":"invalid_audio_id"},400)
        if not size.isdecimal() or not (100 <= int(size) <= MAX_AUDIO):
            return self.send_json({"error":"invalid_audio_size"},413)
        if ctype != "audio/webm":
            return self.send_json({"error":"unsupported_audio_type"},415)
        try:
            for value in (started_at, captured_at):
                if len(value) > 40: raise ValueError()
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if stamp.tzinfo is None: raise ValueError()
        except ValueError:
            return self.send_json({"error":"invalid_audio_timestamp"},400)
        seq=int(sequence)
        with AUDIO_LOCK, connect() as db:
            existing=db.execute(
                "SELECT bytes FROM audio_files WHERE meeting_id=? AND sequence=?",
                (meeting_id,seq)).fetchone()
            if existing:
                return self.send_json({"ok":True,"stored":True,"duplicate":True,"sequence":seq})
            total=db.execute("SELECT COALESCE(SUM(bytes),0) FROM audio_files").fetchone()[0]
            if total+int(size)>AUDIO_QUOTA_BYTES:
                return self.send_json({"error":"audio_storage_quota"},507)
            AUDIO_DIR.mkdir(parents=True,exist_ok=True)
            if shutil.disk_usage(AUDIO_DIR).free < int(size)+MIN_FREE_BYTES:
                return self.send_json({"error":"audio_storage_low"},507)
            audio=self.rfile.read(int(size))
            if len(audio)!=int(size) or not audio.startswith(b"\x1a\x45\xdf\xa3"):
                return self.send_json({"error":"invalid_webm_audio"},415)
            folder=AUDIO_DIR/meeting_id
            folder.mkdir(parents=True,exist_ok=True)
            folder.chmod(0o700)
            name=f"{seq:04d}.webm"
            target=folder/name
            tmp=None
            try:
                with tempfile.NamedTemporaryFile(dir=folder,prefix=".audio-",suffix=".tmp",delete=False) as handle:
                    tmp=Path(handle.name)
                    handle.write(audio)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp,target)
                now=datetime.now(timezone.utc).isoformat()
                db.execute("""INSERT INTO meetings(id,title,started_at,last_seen_at)
                    VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
                    (meeting_id,"Teams (audio guardado)",started_at,now))
                db.execute("""INSERT INTO audio_files(meeting_id,sequence,filename,bytes,captured_at)
                    VALUES(?,?,?,?,?)""",(meeting_id,seq,name,len(audio),captured_at))
                db.commit()
            except (OSError,sqlite3.Error):
                if tmp: tmp.unlink(missing_ok=True)
                return self.send_json({"error":"audio_store_failed"},500)
        return self.send_json({"ok":True,"stored":True,"sequence":seq})

    def post_transcribe(self,meeting_id):
        """Explicit, authenticated selection is the only Groq invocation trigger."""
        if not self.authorized():return
        size=self.headers.get("Content-Length","")
        if not size.isdecimal() or not 2<=int(size)<=8192:
            return self.send_json({"error":"invalid_request_size"},413)
        try:
            payload=json.loads(self.rfile.read(int(size)))
            seqs=payload["sequences"]
            if not isinstance(seqs,list) or not 1<=len(seqs)<=1000:
                raise ValueError("invalid_selection")
            if any(type(seq) is not int or not 0<=seq<=9999 for seq in seqs):
                raise ValueError("invalid_sequence")
            seqs=list(dict.fromkeys(seqs))
            if payload.get("confirm_external_processing") is not True:
                raise ValueError("confirmation_required")
        except (ValueError,TypeError,KeyError,json.JSONDecodeError):
            return self.send_json({"error":"invalid_selection_or_consent"},400)
        if not ASR_RUN_LOCK.acquire(blocking=False):
            return self.send_json({"error":"transcription_busy"},409)
        try:
            with connect() as db:
                rows=db.execute(
                    "SELECT sequence,status FROM audio_files WHERE meeting_id=?",
                    (meeting_id,)).fetchall()
                states={r["sequence"]:r["status"] for r in rows}
                if not all(seq in states for seq in seqs):
                    ASR_RUN_LOCK.release()
                    return self.send_json({"error":"audio_not_found"},404)
                seqs=[seq for seq in seqs if states[seq]!="transcribed"]
                if not seqs:
                    ASR_RUN_LOCK.release()
                    return self.send_json({"error":"already_transcribed"},409)
                db.executemany(
                    "UPDATE audio_files SET status='queued',error='' WHERE meeting_id=? AND sequence=?",
                    [(meeting_id,seq) for seq in seqs])
                db.commit()
            worker=threading.Thread(target=transcribe_selected,args=(meeting_id,seqs),daemon=True)
            worker.start()
        except Exception:
            if ASR_RUN_LOCK.locked(): ASR_RUN_LOCK.release()
            raise
        return self.send_json({"ok":True,"queued":len(seqs),"sequences":seqs},202)

    def post_groq_settings(self, operation):
        """LAN-only, authenticated secret management. Never echo the key."""
        if not self.authorized(): return
        if not permitted_client(self.client_address[0]):
            return self.send_json({"error":"lan_only"},403)
        # Same-origin check mitigates CSRF, but cannot replace TLS.
        origin=self.headers.get("Origin","")
        # A privately configured public origin takes precedence. Otherwise use
        # the actual incoming Host header; no customer domain belongs in code.
        configured=os.environ.get("TEAMSCC_WEB_ORIGIN","").rstrip("/")
        host=self.headers.get("Host","")
        allowed_origin=configured or ("http://" + host if host else "")
        if origin and (not allowed_origin or origin != allowed_origin):
            return self.send_json({"error":"origin_not_allowed"},403)
        n=self.headers.get("Content-Length","")
        if not n.isdecimal() or not (2 <= int(n) <= 1024):
            return self.send_json({"error":"invalid_request_size"},413)
        try:
            data=json.loads(self.rfile.read(int(n)))
            if not isinstance(data,dict): raise ValueError()
            if operation=="save":
                if set(data.keys()) != {"api_key"}: raise ValueError()
                save_key(data["api_key"])
                return self.send_json({"ok":True,"configured":True},200)
            if operation=="test":
                if data: raise ValueError()
                return self.send_json(check_key(),200)
        except KeyErrorSafe as exc:
            return self.send_json({"error":exc.error},exc.status)
        except (json.JSONDecodeError,ValueError,TypeError):
            return self.send_json({"error":"invalid_request"},400)
        return self.send_json({"error":"not_found"},404)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/settings/groq": return self.post_groq_settings("save")
        if path == "/api/settings/groq/test": return self.post_groq_settings("test")
        if path == "/api/audio": return self.post_audio()
        job_match=re.fullmatch(r"/api/meetings/([A-Za-z0-9_-]{4,100})/transcribe",path)
        if job_match:return self.post_transcribe(job_match.group(1))
        if path != "/api/events": return self.send_json({"error":"not_found"},404)
        if not self.authorized(): return
        n = self.headers.get("Content-Length", "")
        if not n.isdigit() or not (0 < int(n) <= 256*1024):
            return self.send_json({"error":"invalid_size"},413)
        try:
            data = json.loads(self.rfile.read(int(n)))
            mid = data["meeting_id"]
            if not isinstance(mid,str) or not ID_RE.fullmatch(mid): raise ValueError("invalid_meeting")
            title = str(data.get("title") or "Reunión de Teams")[:250]
            started = str(data.get("started_at") or "")[:40]
            if not started: raise ValueError("invalid_start")
            captions = data["captions"]
            if not isinstance(captions,list) or not 1 <= len(captions) <= 150: raise ValueError("invalid_captions")
            cleaned = []
            for r in captions:
                cid = r["id"]
                if not isinstance(cid,str) or not ID_RE.fullmatch(cid): raise ValueError("invalid_id")
                txt = str(r["text"]).strip()[:10000]
                if not txt: continue
                cleaned.append((mid,cid,str(r.get("speaker") or "Desconocido")[:200],txt,
                                str(r.get("at") or "")[:40]))
            if not cleaned: raise ValueError("empty")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return self.send_json({"error":"invalid_payload"},400)
        now = datetime.now(timezone.utc).isoformat()
        with LOCK, connect() as db:
            db.execute("""INSERT INTO meetings(id,title,started_at,last_seen_at) VALUES(?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,last_seen_at=excluded.last_seen_at""",(mid,title,started,now))
            db.executemany("""INSERT INTO captions(meeting_id,caption_id,speaker,content,captured_at)
                VALUES(?,?,?,?,?) ON CONFLICT(meeting_id,caption_id) DO UPDATE SET
                speaker=excluded.speaker,content=excluded.content""",cleaned)
            db.commit()
        self.send_json({"ok":True,"received":len(cleaned)})

def transcribe_selected(meeting_id, sequences):
    """Single Groq worker; persisted per-file status enables explicit retries."""
    try:
        for seq in sequences:
            with connect() as db:
                rec=db.execute(
                    "SELECT filename,captured_at FROM audio_files WHERE meeting_id=? AND sequence=?",
                    (meeting_id,seq)).fetchone()
                if not rec: continue
                db.execute("UPDATE audio_files SET status='processing',error='' WHERE meeting_id=? AND sequence=?",
                           (meeting_id,seq))
            try:
                audio=(AUDIO_DIR/meeting_id/rec["filename"]).read_bytes()
                text,model=transcribe(audio)
                with connect() as db:
                    db.execute("""INSERT INTO audio_segments(meeting_id,sequence,content,captured_at,model)
                        VALUES(?,?,?,?,?) ON CONFLICT(meeting_id,sequence) DO UPDATE SET
                        content=excluded.content,model=excluded.model""",
                        (meeting_id,seq,text,rec["captured_at"],model))
                    db.execute("UPDATE audio_files SET status='transcribed',error='' WHERE meeting_id=? AND sequence=?",
                               (meeting_id,seq))
            except TranscriptionError as exc:
                with connect() as db:
                    db.execute("UPDATE audio_files SET status='failed',error=? WHERE meeting_id=? AND sequence=?",
                               (exc.reason,meeting_id,seq))
                # Stop on quota/transport errors; other fragments stay stored for later.
                break
            except (OSError,sqlite3.Error):
                with connect() as db:
                    db.execute("UPDATE audio_files SET status='failed',error='processing_failed' WHERE meeting_id=? AND sequence=?",
                               (meeting_id,seq))
                break
    finally:
        with connect() as db:
            db.execute("UPDATE audio_files SET status='stored' WHERE meeting_id=? AND status='queued'",
                       (meeting_id,))
        ASR_RUN_LOCK.release()

if __name__ == "__main__":
    init_db()
    # The actual public host/port are deployment configuration, never source.
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    httpd.serve_forever()
