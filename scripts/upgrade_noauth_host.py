#!/usr/bin/env python3
"""Upgrade the existing Teams CC native host to credential-free public downloads.

Run once on the Chrome user's RHEL 8 desktop. No Git, sudo, browser reset,
new extension, token input, or backend changes are needed.
"""
import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

SOURCE = (
    "https://raw.githubusercontent.com/machina13/"
    "teams-cc-homelab/main/scripts/native_update_host.py"
)
HOST_NAME = "com.machina13.teamscc_updater"
EXTENSION_NAME = "Teams CC Homelab"


def fetch_host():
    req = Request(SOURCE, headers={"User-Agent": "TeamsCC-NoAuth-Bootstrap"})
    with urlopen(req, timeout=35) as r:
        final = urlsplit(r.geturl())
        if final.scheme != "https" or final.hostname != "raw.githubusercontent.com":
            raise ValueError("El archivo no proviene del origen HTTPS esperado")
        data = r.read(160001)
    if len(data) > 160000:
        raise ValueError("El archivo de actualización supera el límite permitido")
    code = data.decode("utf-8")
    if "PUBLIC_API = " not in code or "def public_extension(" not in code:
        raise ValueError("El archivo no contiene el actualizador público esperado")
    compile(code, "native_update_host.py", "exec")
    return data


def atomic_write(path, content, mode=0o700):
    fd, tmp = tempfile.mkstemp(prefix=".teamscc-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), mode)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def install(home=None, download=fetch_host):
    home = home or Path.home()
    if os.geteuid() == 0:
        raise ValueError("Ejecuta como el usuario de Chrome, sin sudo")
    config = home / ".config" / "teamscc-updater" / "config.json"
    cfg = json.loads(config.read_text(encoding="utf-8"))
    ext_id = cfg.get("extension_id", "")
    if not re.fullmatch(r"[a-p]{32}", ext_id):
        raise ValueError("ID Chrome inválido en la configuración del actualizador")
    if cfg.get("branch") != "main":
        raise ValueError("El repositorio configurado debe usar la rama main")
    folder = Path(cfg.get("extension_dir", ""))
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("name") != EXTENSION_NAME:
        raise ValueError("La carpeta configurada no corresponde a Teams CC Homelab")
    if "nativeMessaging" not in manifest.get("permissions", []):
        raise ValueError("La extensión no tiene el permiso de Native Messaging")

    native_roots = [home / ".config" / "google-chrome" / "NativeMessagingHosts"]
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg and Path(xdg).is_absolute():
        native_roots.insert(0, Path(xdg) / "google-chrome" / "NativeMessagingHosts")
    hosts = [root / (HOST_NAME + ".json") for root in native_roots]
    matches = [path for path in hosts if path.is_file()]
    if not matches:
        raise ValueError("No hay Native Messaging host de Chrome registrado")
    expected_origin = "chrome-extension://" + ext_id + "/"
    for path in matches:
        registered = json.loads(path.read_text(encoding="utf-8"))
        if registered.get("allowed_origins") != [expected_origin]:
            raise ValueError("El Native Messaging host tiene otro ID de extensión")

    folder = home / ".local" / "share" / "teamscc-updater"
    wrapper = folder / "native_host"
    if not wrapper.is_file() or wrapper.is_symlink() or folder.is_symlink():
        raise ValueError("No se encontró el Native Messaging host original")
    if any(json.loads(p.read_text(encoding="utf-8")).get("path") != str(wrapper)
           for p in matches):
        raise ValueError("El registro nativo apunta a otro ejecutable")

    new_host = download()
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    backup = folder / "native_host.pre-noauth.bak"
    if not backup.exists():
        atomic_write(backup, wrapper.read_bytes(), 0o600)

    host_path = folder / "native_update_host.py"
    atomic_write(host_path, new_host, 0o600)
    wrapper_body = (
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " "
        + shlex.quote(str(host_path)) + ' "$@"\n'
    )
    atomic_write(wrapper, wrapper_body.encode("utf-8"), 0o700)
    return ext_id


def main():
    try:
        ext_id = install()
        print("ACTUALIZADOR ANÓNIMO INSTALADO")
        print("Extensión conservada:", ext_id)
        print("No necesita usuario, contraseña ni token GitHub.")
        print("Chrome: prueba el botón Actualizar desde GitHub sin grabaciones activas.")
        return 0
    except (OSError, ValueError, UnicodeError, RuntimeError) as exc:
        print("ERROR: No se cambió la extensión: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
