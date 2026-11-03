# Teams CC Homelab

Extensión de Chrome para subtítulos de Microsoft Teams Web y captura opcional de audio WebM/Opus. Almacena los archivos en un backend privado y transcribe mediante Groq Whisper solo después de la autorización explícita del usuario.

**Versión: 2.2.1.** Repositorio público independiente, sin el historial de desarrollo anterior. La URL del servidor, su puerto y las credenciales se configuran localmente; no se incluyen en este código.

## Migrar la extensión existente en Chrome Linux (RHEL 8)

**No desinstales la versión anterior.** Detén las grabaciones y confirma que no haya fragmentos pendientes. Se conserva la misma carpeta que Chrome tiene instalada, su ID y los valores de `chrome.storage.local`.

Desde tu usuario habitual de Linux (sin `sudo`):

```bash
git clone https://github.com/machina13/teams-cc-homelab.git \
  "$HOME/.local/share/teams-cc-homelab-source"

python3 "$HOME/.local/share/teams-cc-homelab-source/scripts/install_native_updater.py" \
  --browser chrome
```

El instalador detecta la extensión instalada y registra un host de mensajería nativa que solo acepta actualizaciones desde el repositorio público `machina13/teams-cc-homelab`, rama `main`. Si detecta varias instalaciones o ninguna, utiliza `--diagnose` y, si es necesario, los parámetros `--id` y `--extension-dir` de la extensión existente.

Después:

1. Abre `chrome://extensions` y pulsa **Recargar** en **Teams CC Homelab**.
2. Abre su popup y comprueba que la versión sea **2.2.1**.
3. Revisa la **URL del servidor** que ya tenías guardada; incluye el esquema (`http://` o `https://`) y el puerto cuando sea necesario.
4. Pulsa **Guardar** para conceder a Chrome acceso al servidor elegido. El hostname y puerto no se publican en Git.
5. Pulsa **Probar conexión** y verifica el token.
6. Pulsa **Actualizar desde GitHub**. Si no hay commits nuevos, mostrará que estás al día.

Las siguientes actualizaciones serán manuales con el botón del popup, sin volver a clonar ni instalar ZIP. El actualizador bloquea el reemplazo mientras existe una grabación o fragmentos pendientes.

## Actualización anónima desde GitHub (sin usuario ni contraseña)

El botón **Actualizar desde GitHub** consulta exclusivamente el repositorio público mediante HTTPS anónimo (API y ZIP de un commit fijo). No utiliza `git fetch`, credenciales de GitHub, SSH, PAT ni un gestor de contraseñas. El host de mensajería nativa está restringido al ID de la extensión instalada; la actualización se inicia solo al pulsar el botón y se bloquea mientras haya grabaciones pendientes.

**Si instalaste Teams CC 2.2.1 antes de este cambio**, actualiza **solo** el host nativo una vez, desde RHEL 8, con tu usuario habitual y sin `sudo`:

```bash
curl -fsSL --retry 2 \
  -o /tmp/teamscc-upgrade-noauth.py \
  https://raw.githubusercontent.com/machina13/teams-cc-homelab/main/scripts/upgrade_noauth_host.py

python3 /tmp/teamscc-upgrade-noauth.py
```

Espera el mensaje `ACTUALIZADOR ANÓNIMO INSTALADO`. Se conserva el mismo ID, extensión, URL del backend y token local. No reinstales ni vuelvas a cargar la extensión. Luego abre el popup y pulsa **Actualizar desde GitHub** (sin grabaciones activas). Debe informar de la versión vigente o instalar la versión más reciente sin solicitar credenciales.

## Checklist de migración y QA en RHEL 8

Para actualizar **la extensión ya instalada**, preservando su ID, configuración y token, sigue la guía de verificación [RHEL 8: migración y pruebas](docs/RHEL8_MIGRATION_QA.md). **No desinstales ni cargues otra extensión.** El backend existente no necesita desplegarse de nuevo.

## Backend

El archivo `compose.yaml` lee variables de un `.env` privado no versionado:

- `TEAMSCC_BIND_ADDRESS`: interfaz en que publica Docker (por defecto loopback).
- `TEAMSCC_HOST_PORT`: puerto publicado, configurable.
- `TEAMSCC_WEB_ORIGIN`: origen web permitido para modificar la clave Groq, si corresponde.

Coloca el token de Teams CC en `secrets/token`. El audio y la base SQLite permanecen bajo `data/`. La clave Groq se almacena en `data/private/groq_api_key` con permisos restringidos. No subas estos archivos a GitHub.

El popup solicita permisos de Chrome al hostname que configuras. **Las autorizaciones de host de Chrome no limitan el puerto**; el puerto de conexión lo determina la URL guardada. Para datos confidenciales, utiliza HTTPS y una VPN o firewall. HTTP expone token y contenido en tránsito.

## Pruebas

```bash
python3 scripts/check_public_secrets.py
python3 -m unittest discover -s tests -v
node --test tests/*.cjs
```

GitHub Actions ejecuta las pruebas en cada push a `main`. La extensión es Manifest V3 y se mantiene como instalación descomprimida; no está publicada en Chrome Web Store.

## Privacidad

Este repositorio contiene únicamente código fuente, pruebas e infraestructura genérica. No importes `.git` ni logs, artefactos, grabaciones, claves o historial desde proyectos anteriores. Conserva la configuración específica del entorno en archivos locales excluidos por `.gitignore`.
