#!/usr/bin/env python3
"""Teams CC: manually update an unpacked Chrome extension from a public GitHub repo.

Chrome Native Messaging process. Strict protocol: fixed action only, no input
paths/URLs/commands. Updates download anonymous HTTPS archives from the fixed
PUBLIC GitHub repository, without Git credential helpers, prompts or tokens.
No background jobs, polling, or automatic installation.
"""
import fcntl
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from pathlib import Path

CONFIG = Path.home() / ".config" / "teamscc-updater" / "config.json"
HOST = "com.machina13.teamscc_updater"
NAME = "Teams CC Homelab"
MAX_MESSAGE = 8192
PUBLIC_API = "https://api.github.com/repos/machina13/teams-cc-homelab"
MAX_DOWNLOAD = 8 * 1024 * 1024
MAX_EXPANDED = 16 * 1024 * 1024
ALLOWED_HOSTS = {"api.github.com", "codeload.github.com"}
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
    if branch != "main":
        raise UpdateError("El actualizador solo permite la rama main")
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


def public_get(url, max_size):
    """Fetch fixed public GitHub endpoints anonymously; never consult Git auth."""
    request = Request(url, headers={
        "User-Agent": "TeamsCC-Homelab-NoAuth-Updater",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urlopen(request, timeout=35) as response:
            actual = urlsplit(response.geturl())
            if actual.scheme != "https" or actual.hostname not in ALLOWED_HOSTS:
                raise UpdateError("Redirección de GitHub no autorizada")
            data = response.read(max_size + 1)
            if len(data) > max_size:
                raise UpdateError("Descarga de GitHub excede el tamaño permitido")
            return data
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise UpdateError(
            "No se pudo consultar GitHub público por HTTPS (sin credenciales). "
            "Comprueba la red o el límite de API."
        ) from exc


def public_head():
    """Pin the archive to a validated 40-digit commit SHA on main."""
    try:
        obj = json.loads(public_get(PUBLIC_API + "/commits/main", 262144))
        sha = obj.get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid commit")
        return sha
    except (UnicodeError, ValueError, TypeError, AttributeError) as exc:
        raise UpdateError("GitHub no devolvió un commit válido para main") from exc


def public_extension(sha, destination):
    """Extract ONLY extension/ from a size-limited pinned GitHub ZIP archive."""
    archive = public_get(PUBLIC_API + "/zipball/" + sha, MAX_DOWNLOAD)
    destination.mkdir(mode=0o700)
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            members = package.infolist()
            if len(members) > 300:
                raise UpdateError("Paquete GitHub contiene demasiados archivos")
            total = 0
            seen = set()
            for member in members:
                parts = member.filename.split("/")
                if (
                    len(parts) < 3 or
                    not parts[0].startswith("machina13-teams-cc-homelab-") or
                    parts[1] != "extension"
                ):
                    continue
                if (
                    "\\\\" in member.filename or
                    any(p in ("", ".", "..") for p in parts[:-1]) or
                    (not member.filename.endswith("/") and parts[-1] in ("", ".", ".."))
                ):
                    raise UpdateError("Ruta no permitida en archivo GitHub")
                mode = (member.external_attr >> 16) & 0o170000
                if mode not in (0, 0o100000, 0o040000):
                    raise UpdateError("Enlace u objeto no permitido en paquete")
                relative = parts[2:]
                if not relative or member.filename.endswith("/"):
                    continue
                if member.file_size > 3 * 1024 * 1024:
                    raise UpdateError("Archivo GitHub demasiado grande")
                total += member.file_size
                if total > MAX_EXPANDED:
                    raise UpdateError("Paquete GitHub excede tamaño descomprimido")
                path = destination.joinpath(*relative)
                if str(path) in seen:
                    raise UpdateError("Archivos duplicados en paquete GitHub")
                seen.add(str(path))
                path.parent.mkdir(parents=True, exist_ok=True)
                # Copy with an explicit bound; never call extractall().
                with package.open(member) as source, open(path, "wb") as target:
                    remaining = member.file_size
                    while remaining:
                        piece = source.read(min(65536, remaining))
                        if not piece:
                            raise UpdateError("Archivo ZIP truncado")
                        target.write(piece)
                        remaining -= len(piece)
            version(destination)
    except (zipfile.BadZipFile, OSError, EOFError, RuntimeError) as exc:
        raise UpdateError("Archivo público de GitHub inválido o incompleto") from exc


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
        dest=Path(cfg["extension_dir"]).expanduser().absolute()
        if cfg["branch"] != "main":
            raise UpdateError("Solo se permite actualizar desde main público")
        if not dest.is_dir() or dest.is_symlink():
            raise UpdateError("No se encuentra la extensión instalada")
        version(dest)
        # No git fetch, credential.helper, SSH, tokens or password prompts.
        target = public_head()
        changed = cfg["installed_commit"] != target
        new_version = version(dest)
        if changed:
            with tempfile.TemporaryDirectory(
                prefix=".teamscc-public-", dir=str(dest.parent)
            ) as work:
                source = Path(work) / "extension"
                public_extension(target, source)
                new_version = version(source)
                sync_extension(source, dest)
            cfg["installed_commit"] = target
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
