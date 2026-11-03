"""Offline tests for secret management; do not contact Groq or use live keys."""
import http.client
import importlib
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
with patch.object(Path, "read_text", return_value="test-server-token"):
    server=importlib.import_module("server")
import groq_settings as settings

KEY_A="gsk_"+"a"*32
KEY_B="gsk_"+"b"*36

class FakeGroqResponse:
    def __enter__(self): return self
    def __exit__(self,*args): return None
    def read(self,limit): return b'{"data":[{"id":"whisper-large-v3"}]}'

class TestGroqSettings(unittest.TestCase):
    def setUp(self):
        self.work=tempfile.TemporaryDirectory()
        self.key_path=Path(self.work.name)/"private"/"groq_api_key"
        self.patch=patch.object(settings,"GROQ_KEY_PATH",self.key_path)
        self.patch.start()
        self.srv=server.ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        self.thread=threading.Thread(target=self.srv.serve_forever,daemon=True)
        self.thread.start()
    def tearDown(self):
        self.srv.shutdown();self.srv.server_close();self.thread.join(timeout=5)
        self.patch.stop();self.work.cleanup()
    def request(self,method,path,body=None,token="test-server-token",origin=None):
        conn=http.client.HTTPConnection("127.0.0.1",self.srv.server_port,timeout=5)
        if origin is None: origin="http://127.0.0.1:"+str(self.srv.server_port)
        headers={"Authorization":"Bearer "+token,"Origin":origin}
        if body is not None:
            body=json.dumps(body).encode()
            headers["Content-Type"]="application/json"
        conn.request(method,path,body=body,headers=headers)
        res=conn.getresponse()
        data=res.read()
        code=res.status
        conn.close()
        return code,json.loads(data)
    def test_initial_status_is_unconfigured(self):
        code,payload=self.request("GET","/api/settings/groq")
        self.assertEqual(code,200)
        self.assertFalse(payload["configured"])
        self.assertNotIn("api_key",payload)
    def test_key_is_stored_privately_never_returned(self):
        status,result=self.request("POST","/api/settings/groq",{"api_key":KEY_A})
        self.assertEqual(status,200)
        self.assertTrue(result["configured"])
        self.assertNotIn(KEY_A,json.dumps(result))
        self.assertEqual(self.key_path.read_text().strip(),KEY_A)
        self.assertEqual(stat.S_IMODE(self.key_path.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(self.key_path.parent.stat().st_mode),0o700)
        status,result=self.request("GET","/api/settings/groq")
        self.assertTrue(result["configured"])
        self.assertNotIn(KEY_A,json.dumps(result))
        status,result=self.request("POST","/api/settings/groq",{"api_key":KEY_B})
        self.assertEqual(status,200)
        self.assertEqual(self.key_path.read_text().strip(),KEY_B)
    def test_authentication_and_public_request_denied(self):
        code,_=self.request("POST","/api/settings/groq",{"api_key":KEY_A},token="wrong")
        self.assertEqual(code,401)
        self.assertFalse(self.key_path.exists())
        with patch.object(server,"permitted_client",return_value=False):
            code,_=self.request("POST","/api/settings/groq",{"api_key":KEY_A})
            self.assertEqual(code,403)
        self.assertFalse(self.key_path.exists())
    def test_cross_origin_and_invalid_format_rejected(self):
        code,_=self.request("POST","/api/settings/groq",{"api_key":KEY_A},origin="https://untrusted.example")
        self.assertEqual(code,403)
        code,_=self.request("POST","/api/settings/groq",{"api_key":"gsk_bad"})
        self.assertEqual(code,400)
        self.assertFalse(self.key_path.exists())
    def test_validation_is_manual_and_remote_output_is_redacted(self):
        self.request("POST","/api/settings/groq",{"api_key":KEY_A})
        with patch.object(settings.urllib.request,"urlopen",return_value=FakeGroqResponse()) as invoke:
            code,response=self.request("POST","/api/settings/groq/test",{})
            self.assertEqual(code,200)
            self.assertTrue(response["valid"])
            self.assertEqual(invoke.call_count,1)
            self.assertNotIn(KEY_A,json.dumps(response))
            req=invoke.call_args.args[0]
            self.assertEqual(req.full_url,"https://api.groq.com/openai/v1/models")
            self.assertEqual(req.get_header("Authorization"),"Bearer "+KEY_A)
            self.assertEqual(req.get_header("User-agent"),settings.GROQ_USER_AGENT)
    def test_groq_upstream_auth_failures_are_distinct(self):
        import urllib.error
        self.request("POST","/api/settings/groq",{"api_key":KEY_A})
        for upstream, expected in [
            (401,"groq_key_unauthorized"),
            (403,"groq_permission_denied"),
        ]:
            with self.subTest(upstream=upstream):
                error=urllib.error.HTTPError(
                    "https://api.groq.com/openai/v1/models",upstream,
                    "Rejected",{},None
                )
                with patch.object(settings.urllib.request,"urlopen",side_effect=error):
                    code,result=self.request("POST","/api/settings/groq/test",{})
                self.assertEqual(code,422)
                self.assertEqual(result["error"],expected)
                self.assertNotIn(KEY_A,json.dumps(result))

    def test_cloudflare_1010_not_misreported_as_project_permissions(self):
        import io
        import urllib.error
        self.request("POST","/api/settings/groq",{"api_key":KEY_A})
        err=urllib.error.HTTPError(
            "https://api.groq.com/openai/v1/models",403,"Forbidden",
            {"Content-Type":"text/plain"},io.BytesIO(b"error code: 1010\\n")
        )
        with patch.object(settings.urllib.request,"urlopen",side_effect=err):
            code,result=self.request("POST","/api/settings/groq/test",{})
        self.assertEqual(code,502)
        self.assertEqual(result["error"],"groq_client_rejected")
        self.assertNotIn(KEY_A,json.dumps(result))

    def test_checking_credentials_does_not_call_groq_automatically(self):
        with patch.object(settings.urllib.request,"urlopen") as network:
            self.request("POST","/api/settings/groq",{"api_key":KEY_A})
            self.request("GET","/api/settings/groq")
            network.assert_not_called()

if __name__=="__main__":
    unittest.main()
