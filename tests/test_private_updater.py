"""Anonymous public updater tests using local fixtures; no network or credentials."""
import importlib
import io
import zipfile
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import native_update_host as host
import install_native_updater as installer

BRANCH="main"
EXT_ID="a"*32

def run(*cmd,cwd=None):
    p=subprocess.run(cmd,cwd=cwd,universal_newlines=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    if p.returncode:
        raise AssertionError("git command failed: "+" ".join(map(str,cmd))+"\n"+p.stderr)
    return p.stdout.strip()

def make_extension(folder,version="2.2.1",label="original"):
    folder.mkdir(parents=True,exist_ok=True)
    (folder/"manifest.json").write_text(json.dumps({
        "name":host.NAME,"version":version,
        "manifest_version":3,"permissions":["nativeMessaging"]
    }),encoding="utf-8")
    (folder/"popup.html").write_text("<html>"+label+"</html>",encoding="utf-8")
    (folder/"updater.js").write_text("// Manual GitHub update",encoding="utf-8")

class TestNativeUpdater(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.origin=self.root/"origin.git"
        self.seed=self.root/"seed"
        self.repo=self.root/"private-clone"
        self.installed=self.root/"installed"/"teams-cc"
        self.cfg=self.root/"cfg"/"config.json"
        self.cfg.parent.mkdir(parents=True)
        run("git","init","--bare",str(self.origin))
        self.seed.mkdir()
        run("git","init",cwd=self.seed)
        run("git","checkout","-b",BRANCH,cwd=self.seed)
        run("git","config","user.name","Teams CC Test",cwd=self.seed)
        run("git","config","user.email","ci@example.test",cwd=self.seed)
        run("git","remote","add","origin",str(self.origin),cwd=self.seed)
        make_extension(self.seed/"extension")
        (self.seed/"scripts").mkdir()
        shutil.copyfile(Path(host.__file__),self.seed/"scripts"/"native_update_host.py")
        self.commit_push()
        run("git","clone","-b",BRANCH,str(self.origin),str(self.repo))
        shutil.copytree(self.repo/"extension",self.installed)
        head=run("git","rev-parse","HEAD",cwd=self.repo)
        self.cfg.write_text(json.dumps({
            "repo_dir":str(self.repo),"extension_dir":str(self.installed),
            "branch":BRANCH,"extension_id":EXT_ID,"installed_commit":head
        }),encoding="utf-8")
        self.origin_patch=patch.object(host,"REPO_URLS",{str(self.origin)})
        self.origin_patch.start()
        self.addCleanup(self.origin_patch.stop)

    def commit_push(self):
        run("git","add","extension","scripts",cwd=self.seed)
        run("git","commit","-m","change extension",cwd=self.seed)
        run("git","push","origin","HEAD:refs/heads/"+BRANCH,cwd=self.seed)

    def update_from_fixture(self):
        with patch.object(
            host,"public_head",
            side_effect=lambda:run("git","rev-parse","HEAD",cwd=self.seed)
        ), patch.object(
            host,"public_extension",
            side_effect=lambda _sha,dest:shutil.copytree(self.seed/"extension",dest)
        ):
            return host.perform_update(self.cfg,"chrome-extension://"+EXT_ID+"/")

    def test_anonymous_update_is_explicit_and_keeps_same_extension_path(self):
        self.assertEqual(self.installed.joinpath("popup.html").read_text(),"<html>original</html>")
        # The remote changes on its own. Installed files must stay unchanged until click.
        (self.seed/"extension"/"popup.html").write_text("<html>updated</html>")
        self.commit_push()
        self.assertEqual(self.installed.joinpath("popup.html").read_text(),"<html>original</html>")
        result=self.update_from_fixture()
        self.assertTrue(result["ok"])
        self.assertTrue(result["updated"])
        self.assertEqual(result["version"],"2.2.1")
        self.assertEqual(self.installed.joinpath("popup.html").read_text(),"<html>updated</html>")
        self.assertEqual((self.installed.parent/(".teamscc-backup-"+self.installed.name)/"popup.html").read_text(),"<html>original</html>")
        self.assertEqual(json.loads(self.cfg.read_text())["installed_commit"],run("git","rev-parse","HEAD",cwd=self.seed))
        again=self.update_from_fixture()
        self.assertFalse(again["updated"])

    def test_sender_origin_is_restricted(self):
        with self.assertRaisesRegex(host.UpdateError,"Origen Chrome"):
            host.perform_update(self.cfg,"chrome-extension://"+"b"*32+"/")

    def test_only_main_branch_is_accepted(self):
        with self.assertRaisesRegex(host.UpdateError,"solo permite la rama main"):
            host.verify_repo(self.repo,"feature/testing")

    def test_untrusted_git_remote_is_rejected(self):
        run("git","remote","set-url","origin","https://github.com/other/repo.git",cwd=self.repo)
        with self.assertRaisesRegex(host.UpdateError,"Repositorio no autorizado"):
            host.verify_repo(self.repo,"main")


    def test_runtime_does_not_invoke_git_or_credentials(self):
        with patch.object(host,"git",side_effect=AssertionError("Git must never execute")):
            result=self.update_from_fixture()
        self.assertTrue(result["ok"])
        self.assertFalse(result["updated"])

    def test_anonymous_http_headers_do_not_contain_authorization(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self,n): return b'{"sha":"'+b'a'*40+b'"}'
            def geturl(self): return host.PUBLIC_API+"/commits/main"
        def fake_urlopen(req,timeout):
            self.assertEqual(req.get_header("Authorization"),None)
            self.assertIn("TeamsCC",req.get_header("User-agent"))
            return Response()
        with patch.object(host,"urlopen",side_effect=fake_urlopen):
            self.assertEqual(host.public_get(host.PUBLIC_API+"/commits/main",1024),
                             b'{"sha":"'+b'a'*40+b'"}')

    def test_public_zip_keeps_only_extension_content(self):
        stream=io.BytesIO()
        root="machina13-teams-cc-homelab-abc1234/"
        with zipfile.ZipFile(stream,"w") as archive:
            archive.writestr(root+"README.md","not installed")
            archive.writestr(root+"extension/manifest.json",json.dumps({
                "name":host.NAME,"version":"2.2.1",
                "permissions":["nativeMessaging"]}))
            archive.writestr(root+"extension/popup.html","<html>test</html>")
            archive.writestr(root+"extension/updater.js","// updater")
        with tempfile.TemporaryDirectory() as td:
            with patch.object(host,"public_get",return_value=stream.getvalue()):
                out=Path(td)/"extension"
                host.public_extension("a"*40,out)
                self.assertEqual(host.version(out),"2.2.1")
                self.assertFalse((Path(td)/"README.md").exists())

    def test_public_zip_rejects_directory_traversal(self):
        stream=io.BytesIO()
        root="machina13-teams-cc-homelab-abc1234/"
        with zipfile.ZipFile(stream,"w") as archive:
            archive.writestr(root+"extension/../leak.txt","should fail")
        with tempfile.TemporaryDirectory() as td:
            with patch.object(host,"public_get",return_value=stream.getvalue()):
                with self.assertRaisesRegex(host.UpdateError,"Ruta no permitida"):
                    host.public_extension("a"*40,Path(td)/"extension")

    def test_only_fixed_native_action_accepted(self):
        payload=json.dumps({"action":"run","command":"id"}).encode()
        data=struct.pack("<I",len(payload))+payload
        out=subprocess.run([sys.executable,str(Path(host.__file__))],input=data,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout
        size=struct.unpack("<I",out[:4])[0]
        response=json.loads(out[4:4+size])
        self.assertFalse(response["ok"])
        self.assertIn("no autorizada",response["error"])

    def test_installer_preserves_id_and_registers_private_host(self):
        # Start with a legacy unpacked Teams CC; installer upgrades in place.
        legacy=json.loads((self.installed/"manifest.json").read_text())
        legacy["version"]="2.0.4"
        legacy["permissions"]=[]
        (self.installed/"manifest.json").write_text(json.dumps(legacy))
        home=self.root/"home"
        with patch.object(installer.os,"geteuid",return_value=1000), \
             patch.object(installer,"CONFIG",self.cfg):
            result=installer.install(self.repo,EXT_ID,self.installed,"chrome",home=home)
        self.assertEqual(result["id"],EXT_ID)
        self.assertEqual(result["version"],"2.2.1")
        registered=home/".config/google-chrome/NativeMessagingHosts"/(host.HOST+".json")
        host_manifest=json.loads(registered.read_text())
        self.assertEqual(host_manifest["allowed_origins"],["chrome-extension://"+EXT_ID+"/"])
        self.assertTrue(Path(host_manifest["path"]).is_file())
        self.assertEqual(json.loads(self.cfg.read_text())["extension_id"],EXT_ID)

    def test_chrome_preference_discovery(self):
        home=self.root/"home"
        profile=home/".config/google-chrome/Default"
        profile.mkdir(parents=True)
        (profile/"Preferences").write_text(json.dumps({"extensions":{"settings":{
            EXT_ID:{"manifest":{"name":host.NAME},"path":str(self.installed)}
        }}}))
        found=installer.discover_extensions("chrome",home)
        self.assertEqual(found,[(EXT_ID,self.installed,"Default")])

    def test_secure_preferences_is_discovered_and_duplicate_entries_deduplicated(self):
        home=self.root/"home"
        profile=home/".config/google-chrome/Default"
        profile.mkdir(parents=True)
        entry={EXT_ID:{"manifest":{"name":"Localized display name"},
                       "path":str(self.installed)}}
        data=json.dumps({"extensions":{"settings":entry}})
        (profile/"Secure Preferences").write_text(data)
        (profile/"Preferences").write_text(data)
        self.assertEqual(installer.discover_extensions("chrome",home),
                         [(EXT_ID,self.installed,"Default")])

    def test_secondary_chrome_profile_is_discovered(self):
        home=self.root/"home"
        profile=home/".config/google-chrome/Profile 2"
        profile.mkdir(parents=True)
        (profile/"Secure Preferences").write_text(json.dumps({
            "extensions":{"settings":{EXT_ID:{"path":str(self.installed)}}}
        }))
        found=installer.discover_extensions("chrome",home)
        self.assertEqual(found,[(EXT_ID,self.installed,"Profile 2")])

    def test_diagnostic_finds_unpacked_directory_without_chrome_prefs(self):
        home=self.root/"home"
        download=home/"Descargas"/"addons"/"teamscc"
        download.parent.mkdir(parents=True)
        shutil.copytree(self.installed,download)
        self.assertIn(download,installer.locate_extension_folders(home))
        self.assertEqual(installer.discover_extensions("chrome",home),[])

    def test_xdg_config_home_is_respected_for_live_profile(self):
        custom=self.root/"custom-config"
        with patch.dict(os.environ,{"XDG_CONFIG_HOME":str(custom)}):
            self.assertEqual(installer.profile_root("chrome"),custom/"google-chrome")

if __name__=="__main__":
    unittest.main()
