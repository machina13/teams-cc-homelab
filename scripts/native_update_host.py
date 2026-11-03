#!/usr/bin/env python3
"""Teams CC: manually update an unpacked Chrome extension from a public GitHub repo.

Chrome Native Messaging process. Strict protocol: fixed action only, no input
paths/URLs/commands, Git access stays in the local user's environment.
No background jobs, polling, remote update endpoints, or automatic installation.
"""
import fcntl
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG = Path.home() / ".config" / "teamscc-updater" / "config.json"
HOST = "com.machina13.teamscc_updater"
NAME = "Teams CC Homelab"
MAX_MESSAGE = 8192
REPO_URLS = {
    "git@github.com:machina13/teams-cc-homelab.git",
    "ssh://git@github.com/machina13/teams-cc-homelab.git",
    "https://github.com/machina13/teams-cc-homelab.git",
    "https://github.com/machina13/teams-cc-homelab",
}


class UpdateError(Exception):
    pass


def git(repo, *args, timeout=65):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    try:
        process = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
            universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, env=env, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError("Git no está disponible o se agotó el tiempo") from exc
    if process.returncode:
        # Never propagate arbitrary Git stderr: credentials / private locations.
        raise UpdateError("Error de Git: comprueba la red y la rama del repositorio público")
    return process.stdout.strip()


def verify_repo(repo, branch):
    if not isinstance(branch, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._/-]{0,120}", branch
    ) or ".." in branch:
        raise UpdateError("Rama no autorizada")
    origin = git(repo, "remote", "get-url", "origin")
    if origin not in REPO_URLS:
        raise UpdateError("Repositorio no autorizado (origen distinto de machina13/teams-cc-homelab)")
    if git(repo, "symbolic-ref", "--quiet", "--short", "HEAD") != branch:
        raise UpdateError("El checkout está en otra rama")


def version(path):
    try:
        if path.is_symlink():
            raise UpdateError("Carpeta de extensión no permitida")
        data = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if data.get("name") != NAME:
            raise UpdateError("La carpeta no corresponde a Teams CC Homelab")
        if "nativeMessaging" not in data.get("permissions", []):
            raise UpdateError("Esta versión no incluye actualizaciones manuales")
        value = data["version"]
        if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", value):
            raise UpdateError("Versión de extensión inválida")
        if not (path / "popup.html").is_file() or not (path / "updater.js").is_file():
            raise UpdateError("Paquete de Teams CC incompleto")
        return value
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise UpdateError("No se pudo validar manifest.json") from exc


def sync_extension(source, dest):
    """Atomic swap at the SAME installed path, retaining extension ID and settings.

    Retain one local rollback backup. No Chrome profile or token is copied.
    """
    source=source.resolve()
    dest=dest.absolute()
    if source == dest:
        return
    version(source)
    if not dest.is_dir() or dest.is_symlink():
        raise UpdateError("Carpeta instalada inexistente o no permitida")
    # Avoid following symlinks from an untrusted repository snapshot.
    if any(entry.is_symlink() for entry in source.rglob("*")):
        raise UpdateError("La extensión de GitHub contiene enlaces simbólicos")
    stage=Path(tempfile.mkdtemp(prefix=".teamscc-stage-",dir=str(dest.parent)))
    staged_extension=stage/"extension"
    backup=dest.parent/(".teamscc-backup-"+dest.name)
    try:
        # Python 3.6 (RHEL 8) does not support copytree(dirs_exist_ok=True).
        shutil.copytree(str(source),str(staged_extension),symlinks=True)
        version(staged_extension)
        if backup.exists() or backup.is_symlink():
            if backup.is_symlink() or not backup.is_dir():
                raise UpdateError("Respaldo local no seguro")
            shutil.rmtree(backup)
        os.replace(dest,backup)
        try:
            os.replace(staged_extension,dest)
        except OSError:
            os.replace(backup,dest)
            raise
    except (OSError,shutil.Error) as exc:
        raise UpdateError("Error al reemplazar la extensión; revisa permisos de carpeta") from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def read_config(path=CONFIG):
    try:
        if path.is_symlink():
            raise UpdateError("Configuración no autorizada")
        cfg=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cfg,dict):
            raise ValueError()
        for name in ("repo_dir","extension_dir","branch","extension_id","installed_commit"):
            if not isinstance(cfg.get(name),str) or not cfg[name]:
                raise ValueError()
        if not re.fullmatch(r"[a-p]{32}",cfg["extension_id"]):
            raise ValueError()
        if not re.fullmatch(r"[0-9a-f]{40}",cfg["installed_commit"]):
            raise ValueError()
        return cfg
    except (OSError,ValueError,TypeError,KeyError):
        raise UpdateError("Actualizador local no configurado; ejecuta el instalador Linux") from None


def perform_update(config_path=CONFIG, origin=None):
    cfg=read_config(config_path)
    expected_origin="chrome-extension://"+cfg["extension_id"]+"/"
    if origin is not None and origin.rstrip("/")+"/"!=expected_origin:
        raise UpdateError("Origen Chrome no autorizado")
    # A native host may be invoked twice; avoid concurrent directory swaps.
    with open(config_path.parent/"update.lock","a+b") as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise UpdateError("Ya existe una actualización en curso") from None
        repo=Path(cfg["repo_dir"]).expanduser().resolve()
        dest=Path(cfg["extension_dir"]).expanduser().absolute()
        if not repo.is_dir() or not dest.is_dir():
            raise UpdateError("No se encuentra el repositorio o la extensión instalada")
        verify_repo(repo,cfg["branch"])
        version(dest)
        if git(repo,"status","--porcelain"):
            raise UpdateError("El repositorio contiene cambios locales; revísalos antes de actualizar")
        old_head=git(repo,"rev-parse","HEAD")
        git(repo,"fetch","--no-tags","origin","refs/heads/"+cfg["branch"],timeout=90)
        target=git(repo,"rev-parse","FETCH_HEAD")
        if old_head!=target:
            git(repo,"merge-base","--is-ancestor",old_head,target)
            git(repo,"merge","--ff-only","FETCH_HEAD")
        # Always verify identity and required files before replacing the unpacked folder.
        new_version=version(repo/"extension")
        changed=cfg["installed_commit"]!=target
        if changed:
            sync_extension(repo/"extension",dest)
            cfg["installed_commit"]=target
            fd,tmp=tempfile.mkstemp(prefix=".teamscc-config-",dir=str(config_path.parent))
            try:
                with os.fdopen(fd,"w",encoding="utf-8") as file:
                    os.fchmod(file.fileno(),0o600)
                    json.dump(cfg,file,ensure_ascii=False,indent=2)
                    file.write("\n")
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(tmp,config_path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return {
            "ok":True,"updated":changed,"version":new_version,
            "commit":target[:12],
            "message":"Actualización manual desde repositorio público"
        }


def read_message():
    size=sys.stdin.buffer.read(4)
    if len(size)!=4:
        raise UpdateError("Mensaje incompleto")
    count=struct.unpack("<I",size)[0]
    if not 0<count<=MAX_MESSAGE:
        raise UpdateError("Tamaño de mensaje no autorizado")
    raw=sys.stdin.buffer.read(count)
    if len(raw)!=count:
        raise UpdateError("Mensaje truncado")
    try:
        return json.loads(raw)
    except (ValueError,UnicodeError):
        raise UpdateError("Mensaje no válido") from None


def write_message(value):
    raw=json.dumps(value,ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I",len(raw)))
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def main():
    try:
        request=read_message()
        if not isinstance(request,dict) or request!={"action":"update"}:
            raise UpdateError("Acción no autorizada")
        caller=sys.argv[1] if len(sys.argv)>1 else None
        reply=perform_update(origin=caller)
    except (UpdateError,OSError,KeyError,TypeError,ValueError) as exc:
        reply={"ok":False,"error":str(exc)[:220]}
    write_message(reply)


if __name__=="__main__":
    main()
