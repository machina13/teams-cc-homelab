# Migración y QA de Teams CC 2.2.1 — RHEL 8 (Chrome)

Este procedimiento completa **la migración de la instalación existente** al repositorio público `machina13/teams-cc-homelab`, manteniendo **la misma carpeta descomprimida**, el ID de Chrome y `chrome.storage.local`. No requiere reinstalar, eliminar ni volver a levantar el backend de Docker.

## A. Preparación segura

1. Termina cualquier grabación y comprueba que no quedan fragmentos de audio pendientes de subir. No actualices durante una reunión.
2. Abre `chrome://extensions`, activa *Modo de desarrollador*, ubica Teams CC y anota el **ID actual**. Mantén abierta esta pestaña.
3. Confirma que utilizas el **mismo perfil de Chrome** de la extensión instalada y ejecuta todos los comandos como ese usuario Linux, **sin `sudo`**.
4. Verifica disponibilidad de Git y Python: `git --version && python3 --version`. El instalador/host nativo no requiere módulos externos de Python.

## B. Obtener el código definitivo

En una terminal del usuario habitual:

```bash
REPO="$HOME/.local/share/teams-cc-homelab-source"
if [ -d "$REPO/.git" ]; then
  git -C "$REPO" remote -v
  git -C "$REPO" pull --ff-only origin main
elif [ ! -e "$REPO" ]; then
  git clone https://github.com/machina13/teams-cc-homelab.git "$REPO"
else
  echo "La ruta existe, pero no es un clon Git: revisar manualmente antes de continuar"
fi
```

Si el repositorio existente tiene un origen diferente, cambios locales o un error de Git, **no fuerces la actualización**: revisa antes de ejecutar el instalador.

## C. Identificar la extensión existente (solo lectura)

```bash
python3 "$REPO/scripts/install_native_updater.py" --browser chrome --diagnose
```

Resultado esperado: **una sola** instalación de Teams CC detectada, con el mismo ID anotado en Chrome y una ruta de carpeta descomprimida accesible.

Si detecta cero o varias, no ejecutes instalación automática. Verifica el perfil y usa los parámetros explícitos **solo después de confirmar la carpeta real de la instalación existente**:

```bash
python3 "$REPO/scripts/install_native_updater.py" \
  --browser chrome --id "ID_ACTUAL_DE_CHROME" \
  --extension-dir "/ruta/real/de/extension/existente"
```

Nunca proporciones una nueva carpeta vacía, no utilices el ZIP y no cargues una segunda extensión.

## D. Instalar el actualizador nativo una sola vez

Si el diagnóstico devolvió **una sola coincidencia correcta**, ejecuta:

```bash
python3 "$REPO/scripts/install_native_updater.py" --browser chrome
```

Resultado esperado: `ACTUALIZADOR INSTALADO`, el mismo ID de Chrome, versión **2.2.1**, rama `main`. El instalador actualiza la carpeta actual en su misma ruta, guarda un respaldo local y registra un Native Messaging host restringido al ID de la extensión.

Abre `chrome://extensions` y pulsa **Recargar**, no **Eliminar**. Verifica que el ID no cambió y que la versión mostrada sea 2.2.1. Chrome puede requerir reiniciarse si no habilita inmediatamente Native Messaging.

## E. Pruebas funcionales, en orden

| Prueba | Acción | Resultado esperado |
| --- | --- | --- |
| E1. Persistencia | Abre el popup y las opciones de la misma extensión | ID sin cambios; configuraciones locales existentes conservadas |
| E2. Backend | Revisa la URL del servidor (esquema + puerto), guárdala y pulsa **Probar conexión** | Conexión y autorización con token exitosas; si usas HTTP, solo en red confiable |
| E3. Actualizador | Sin grabaciones activas, pulsa **Actualizar desde GitHub** | Mensaje de versión actualizada o «Ya tienes la última versión» sin crear otra extensión |
| E4. Subtítulos | En una reunión de prueba consentida, habilita CC de Teams Web, deja hablar a un participante | Captura de subtítulos visibles y guardado conforme a la configuración |
| E5. Audio | En esa reunión, inicia captura, registra unos segundos y detén la grabación | Audio cargado correctamente; sin fragmentos pendientes |
| E6. Transcripción | Ejecuta Groq Whisper **solo cuando lo autorices** | Transcripción correcta asociada al audio; no se dispara automáticamente |
| E7. Protección de grabaciones | Intenta el botón de actualización con grabación activa o envíos pendientes | Actualización bloqueada; no se interrumpe ni reinicia el servicio |
| E8. Persistencia final | Recarga Chrome y vuelve a abrir Teams CC | Mismo ID, configuración y acceso al servidor; sin reinstalaciones |

Para E4–E6 utiliza **solo una reunión de prueba con consentimiento**, sin material de clientes.

## F. Diagnósticos si algo falla

- **Host local no disponible:** comprueba que existan `~/.config/google-chrome/NativeMessagingHosts/com.machina13.teamscc_updater.json` y `~/.config/teamscc-updater/config.json` para el mismo usuario. Reinicia Chrome y consulta `chrome://policy`, especialmente las políticas de Native Messaging. No envíes la configuración ni tokens a terceros.
- **El actualizador dice que el manifiesto es antiguo:** vuelve a `chrome://extensions`, **Recargar**. No borres la extensión.
- **El backend no responde:** valida conectividad con la URL que ya utilizabas, token, HTTPS y permisos de host del navegador; el código público **no incorpora** la dirección del Homelab.
- **Hay audio pendiente:** no intentes forzar el reemplazo; primero comprueba que terminó la subida.
- **Varias instalaciones detectadas:** verifica cuál tiene el ID original; evita instalar duplicados.
- **Conflicto de Git:** no utilices `reset --hard`, no elimines la carpeta y no modifiques la rama sin revisar los cambios.

## G. Criterios de cierre

- Nuevo repositorio público con pipeline verde y sin exposición evidente de secretos.
- Repositorio anterior privado e independiente.
- Única extensión existente con ID original, versión 2.2.1 y backend local configurado.
- Actualización manual a través de Native Messaging; nunca durante una grabación.
- Captura de CC y audio verificadas, y Groq ejecutado **bajo demanda**.

**Limitación:** los tests de GitHub Actions prueban el código de forma automatizada; no acreditan por sí solos que Chrome, las políticas corporativas, la conectividad privada ni Groq funcionen en la laptop. Para cerrar E1–E8 se requiere la validación de esa instalación real.
