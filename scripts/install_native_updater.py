#!/usr/bin/env python3
"""One-time Linux installer for public, manual Chrome Teams CC GitHub updates.

Run as desktop user (NOT sudo) from a public GitHub clone.
Finds the unpacked extension via Chrome Preferences, replaces it IN PLACE,
registers a per-user Native Messaging host and preserves the extension ID.
No background services, scheduled checks, tokens or public releases.
"""
import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path

from native_update_host import (
    CONFIG, HOST, NAME, UpdateError, git, sync_extension, verify_repo, version
)

BROWSERS = {
    "chrome": ".config/google-chrome",
    "chromium": ".config/chromium",
    "brave": ".config/BraveSoftware/Brave-Browser",
}


def profile_root(browser, home=None):
    """Respect XDG_CONFIG_HOME on Linux, including custom RHEL desktops."""
    supplied_home = home is not None
    home = home or Path.home()
    if browser not in BROWSERS:
        raise UpdateError("Navegador no soportado; usa chrome, chromium o brave")
    if not supplied_home:
        xdg = os.environ.get("XDG_CONFIG_HOME", "")
        if xdg and Path(xdg).is_absolute():
            return Path(xdg) / Path(BROWSERS[browser]).name
    return home / BROWSERS[browser]


def profile_paths(browser, home=None):
    root = profile_root(browser, home)
    paths = [root / "Default", *sorted(root.glob("Profile *")),
             *sorted(root.glob("Guest Profile*"))]
    # Include profile paths recorded in Local State, if Chrome uses unusual names.
    local_state = root / "Local State"
    if local_state.is_file():
        try:
            doc = json.loads(local_state.read_text(encoding="utf-8"))
            for key in doc.get("profile", {}).get("info_cache", {}):
                folder = root / key
                if folder not in paths and folder.resolve().parent == root.resolve():
                    paths.append(folder)
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    return paths


def discover_extensions(browser, home=None):
    """Read-only discovery in Preferences and Secure Preferences of each profile.

    Chrome may store extensions.settings under either file and may omit the
    extension display name in the cached manifest. The unpacked directory
    manifest.json is the authoritative name check.
    """
    results = []
    seen = set()
    for profile in profile_paths(browser, home):
        for pref_file in (profile / "Preferences", profile / "Secure Preferences"):
            if not pref_file.is_file():
                continue
            try:
                doc = json.loads(pref_file.read_text(encoding="utf-8"))
                entries = doc.get("extensions", {}).get("settings", {})
            except (OSError, ValueError, AttributeError):
                continue
            if not isinstance(entries, dict):
                continue
            for ext_id, entry in entries.items():
                if not re.fullmatch(r"[a-p]{32}", ext_id) or not isinstance(entry, dict):
                    continue
                raw = entry.get("path")
                if not isinstance(raw, str) or not raw:
                    continue
                folder = Path(raw).expanduser()
                if not folder.is_absolute() or folder.is_symlink():
                    # Packed extensions and ambiguous paths cannot be replaced.
                    continue
                try:
                    local = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if local.get("name") != NAME:
                    continue
                candidate = (ext_id, folder, profile.name)
                if candidate not in seen:
                    seen.add(candidate)
                    results.append(candidate)
    return results


def locate_extension_folders(home=None):
    """Find probable unpacked Teams CC folders without reading private profiles.

    Used ONLY for diagnostics when Chrome has no usable extension ID/path
    entry. Never guess an ID or modify a directory found this way.
    """
    home = home or Path.home()
    roots = [home / "Downloads", home / "Descargas", home / "Desktop",
             home / "Escritorio", home / "Documents", home / "Documentos",
             home / ".local" / "share"]
    found = []
    blocked = {".git", "node_modules", "__pycache__", "venv", ".venv"}
    for base in roots:
        if not base.is_dir():
            continue
        for current, dirs, files in os.walk(str(base)):
            depth = len(Path(current).relative_to(base).parts)
            dirs[:] = [] if depth >= 5 else [
                name for name in dirs if name not in blocked and
                not (Path(current) / name).is_symlink()
            ]
            if "manifest.json" not in files:
                continue
            path = Path(current)
            try:
                doc = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if doc.get("name") == NAME and path not in found:
                found.append(path)
    return found


def show_diagnostics(browser):
    root = profile_root(browser)
    profiles = profile_paths(browser)
    print("Navegador:", browser)
    print("Directorio de perfiles:", root)
    print("Perfiles encontrados:", ", ".join(p.name for p in profiles if p.is_dir())
          or "(ninguno)")
    matches = discover_extensions(browser)
    print("Extensiones Teams CC detectadas:", len(matches))
    for ext_id, folder, profile in matches:
        print("  Perfil:", profile, "| ID:", ext_id, "| Carpeta:", folder)
    folders = locate_extension_folders()
    print("Carpetas Teams CC encontradas:", len(folders))
    for folder in folders[:15]:
        print("  ", folder)
    if not matches:
        print("Abre chrome://extensions y activa Modo de desarrollador.")
        print("Copia el ID de Teams CC y la carpeta original que cargaste.")
        print("Después ejecuta el instalador con --id ID --extension-dir RUTA.")
    if not root.is_dir():
        for alt in BROWSERS:
            if alt != browser and profile_root(alt).is_dir():
                print("También se detectó navegador:", alt, "-> prueba --browser", alt)

def install(repo,extension_id,extension_dir,browser="chrome",home=None):
    home=home or Path.home()
    if os.geteuid()==0:
        raise UpdateError("Ejecuta como el usuario de Chrome, sin sudo")
    if not re.fullmatch(r"[a-p]{32}",extension_id):
        raise UpdateError("ID de extensión inválido; cópialo de chrome://extensions")
    repo=Path(repo).expanduser().resolve()
    target=Path(extension_dir).expanduser().absolute()
    if target.is_symlink():
        raise UpdateError("La carpeta de extensión no debe ser un enlace simbólico")
    try:
        existing=json.loads((target/"manifest.json").read_text(encoding="utf-8"))
        if existing.get("name")!=NAME:
            raise UpdateError("La carpeta no contiene Teams CC Homelab")
    except (OSError,ValueError):
        raise UpdateError("No se pudo identificar la carpeta instalada") from None
    branch=git(repo,"symbolic-ref","--quiet","--short","HEAD")
    if branch != "main":
        raise UpdateError("Instala solo desde main del repositorio público")
    verify_repo(repo,branch)
    if git(repo,"status","--porcelain"):
        raise UpdateError("El repositorio tiene cambios locales; utiliza un clon limpio")
    version(repo/"extension")   # Requires new nativeMessaging feature and updater.js
    if not os.access(target.parent,os.W_OK|os.X_OK):
        raise UpdateError("No tienes permisos de escritura en la carpeta de extensión")
    commit=git(repo,"rev-parse","HEAD")
    # Perform one-time initial upgrade at SAME installed path. No ZIP installation.
    sync_extension(repo/"extension",target)

    hostdir=profile_root(browser,home)/"NativeMessagingHosts"
    hostdir.mkdir(parents=True,exist_ok=True)
    script=repo/"scripts"/"native_update_host.py"
    if not script.is_file():
        raise UpdateError("Falta scripts/native_update_host.py")
    wrapperdir=home/".local"/"share"/"teamscc-updater"
    wrapperdir.mkdir(parents=True,exist_ok=True,mode=0o700)
    wrapper=wrapperdir/"native_host"
    wrapper.write_text(
        "#!/bin/sh\nexec "+shlex.quote(sys.executable)+" "+
        shlex.quote(str(script))+' "$@"\n',encoding="utf-8"
    )
    wrapper.chmod(0o700)
    native_manifest={
        "name":HOST,
        "description":"Teams CC: actualizaciones manuales desde GitHub",
        "path":str(wrapper.absolute()),
        "type":"stdio",
        "allowed_origins":["chrome-extension://"+extension_id+"/"]
    }
    hostfile=hostdir/(HOST+".json")
    hostfile.write_text(json.dumps(native_manifest,indent=2)+"\n",encoding="utf-8")
    hostfile.chmod(0o600)

    CONFIG.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    CONFIG.parent.chmod(0o700)
    conf={
        "repo_dir":str(repo),
        "extension_dir":str(target),
        "branch":branch,
        "extension_id":extension_id,
        "installed_commit":commit,
    }
    CONFIG.write_text(json.dumps(conf,indent=2)+"\n",encoding="utf-8")
    CONFIG.chmod(0o600)
    return {"id":extension_id,"path":str(target),"branch":branch,
            "version":version(target),"hostfile":str(hostfile)}


def main(argv=None):
    parser=argparse.ArgumentParser(
        description="Instalar una sola vez actualizador Teams CC para Chrome Linux"
    )
    parser.add_argument("--browser",choices=tuple(BROWSERS),default="chrome")
    parser.add_argument("--diagnose",action="store_true",
                        help="Inspeccionar perfiles/rutas sin modificar Chrome ni archivos")
    parser.add_argument("--repo",default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--id",dest="extension_id",help="ID de chrome://extensions si no se detecta")
    parser.add_argument("--extension-dir",help="Ruta de carpeta descomprimida instalada si no se detecta")
    args=parser.parse_args(argv)
    try:
        if args.diagnose:
            show_diagnostics(args.browser)
            return 0
        if bool(args.extension_id) != bool(args.extension_dir):
            raise UpdateError("--id y --extension-dir deben especificarse juntos")
        if args.extension_id:
            extension_id,folder=args.extension_id,Path(args.extension_dir)
        else:
            candidates=discover_extensions(args.browser)
            if len(candidates)!=1:
                if candidates:
                    for ext_id,folder,profile in candidates:
                        print("Candidata:",profile,ext_id,folder,file=sys.stderr)
                raise UpdateError(
                    "No pude identificar una única Teams CC. Ejecuta primero "
                    "--diagnose y revisa los perfiles; alternativamente usa "
                    "--id ID --extension-dir RUTA desde chrome://extensions"
                )
            extension_id,folder,_profile=candidates[0]
        result=install(args.repo,extension_id,folder,args.browser)
        print("ACTUALIZADOR INSTALADO")
        print("Chrome:",args.browser)
        print("Extensión:",result["id"],"v"+result["version"])
        print("GitHub: rama",result["branch"])
        print("Recarga UNA SOLA VEZ Teams CC en chrome://extensions.")
        print("Después utiliza «Actualizar desde GitHub» dentro del popup.")
        print("La actualización nunca se ejecuta automáticamente.")
        return 0
    except UpdateError as exc:
        print("ERROR:",str(exc),file=sys.stderr)
        return 1


if __name__=="__main__":
    sys.exit(main())
