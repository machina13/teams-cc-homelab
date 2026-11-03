"""Integration tests: audio is archived, Groq runs only on explicit selection."""
import http.client
import importlib
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
with mock.patch.object(Path,"read_text",return_value="test-server-token"):
    server=importlib.import_module("server")

AUDIO=bytes.fromhex("1a45dfa3")+b"test-audio"*90
MID="m_test_12345"
AT="2026-11-02T12:00:00+00:00"

class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        server.DB=Path(self.tmp.name)/"test.sqlite3"
        server.AUDIO_DIR=Path(self.tmp.name)/"audio"
        server.MIN_FREE_BYTES=0
        server.AUDIO_QUOTA_BYTES=10000000
        server.init_db()
        self.http=server.ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()
    def request(self,method,path,body=None,extra=None):
        conn=http.client.HTTPConnection("127.0.0.1",self.http.server_port,timeout=5)
        hdr={"Authorization":"Bearer test-server-token"}
        hdr.update(extra or {})
        conn.request(method,path,body=body,headers=hdr)
        res=conn.getresponse()
        data=res.read()
        status=res.status
        conn.close()
        return status,json.loads(data) if res.getheader("Content-Type","").startswith("application/json") else data
    def upload(self):
        return self.request("POST","/api/audio",AUDIO,{
            "Content-Type":"audio/webm",
            "X-Meeting-Id":MID,
            "X-Sequence":"0",
            "X-Started-At":AT,
            "X-Captured-At":AT,
        })
    def test_archive_without_groq_and_explicit_transcription(self):
        with mock.patch.object(server,"transcribe",return_value=("transcrito correctamente","whisper-large-v3")) as groq:
            status,payload=self.upload()
            self.assertEqual(status,200)
            self.assertTrue(payload["stored"])
            groq.assert_not_called()
            stored=server.AUDIO_DIR/MID/"0000.webm"
            self.assertEqual(stored.read_bytes(),AUDIO)
            status,payload=self.upload()
            self.assertEqual(status,200)
            self.assertTrue(payload["duplicate"])
            groq.assert_not_called()
            status,details=self.request("GET","/api/meetings/"+MID)
            self.assertEqual(status,200)
            self.assertEqual(details["audio_files"][0]["status"],"stored")
            self.assertEqual(details["asr_segments"],[])
            status,payload=self.request("POST","/api/meetings/"+MID+"/transcribe",
              json.dumps({"sequences":[0],"confirm_external_processing":False}),
              {"Content-Type":"application/json"})
            self.assertEqual(status,400)
            groq.assert_not_called()
            status,payload=self.request("POST","/api/meetings/"+MID+"/transcribe",
              json.dumps({"sequences":[0],"confirm_external_processing":True}),
              {"Content-Type":"application/json"})
            self.assertEqual(status,202)
            for _ in range(150):
                status,details=self.request("GET","/api/meetings/"+MID)
                if details["audio_files"][0]["status"]=="transcribed":break
                time.sleep(.02)
            self.assertEqual(details["audio_files"][0]["status"],"transcribed")
            self.assertEqual(details["asr_segments"][0]["content"],"transcrito correctamente")
            self.assertEqual(groq.call_count,1)
            self.assertEqual(stored.read_bytes(),AUDIO)
    def test_private_extension_zip_is_not_publicly_served(self):
        conn=http.client.HTTPConnection("127.0.0.1",self.http.server_port,timeout=5)
        conn.request("GET","/extension.zip")
        res=conn.getresponse()
        self.assertEqual(res.status,404)
        res.read()
        conn.close()

    def test_unauthorized_audio_cannot_be_downloaded(self):
        self.upload()
        conn=http.client.HTTPConnection("127.0.0.1",self.http.server_port,timeout=5)
        conn.request("GET","/api/meetings/"+MID+"/audio/0")
        res=conn.getresponse()
        self.assertEqual(res.status,401)
        res.read()
        conn.close()
    def test_quota_protects_disk(self):
        server.AUDIO_QUOTA_BYTES=100
        status,data=self.upload()
        self.assertEqual(status,507)
        self.assertEqual(data["error"],"audio_storage_quota")
        self.assertFalse((server.AUDIO_DIR/MID/"0000.webm").exists())

if __name__=="__main__":
    unittest.main()
