#!/bin/bash
# Quill Linnda: instalador para Mac.
#
#   Instalar:     curl -fsSL https://raw.githubusercontent.com/jaclavijo-co/quill-linnda/main/install.sh | bash
#   Actualizar:   lo hace solo cada día (o: ... | bash -s -- --update)
#   Desinstalar:  ... | bash -s -- --uninstall
#
# Qué deja en tu Mac:
#   ~/.local/bin/quill          la pluma de la barra de menú (graba las reuniones)
#   ~/.local/bin/notetaker      comandos para buscar, listar y rehacer notas
#   ~/.notetaker/               el proceso de notas, tu configuración y tus llaves (privadas)
#   ~/Recordings/               el audio de cada reunión
#   Notas-Llamadas/             tus notas, en tu Google Drive (o en Documentos)
set -euo pipefail

REPO="jaclavijo-co/quill-linnda"
RAW="https://raw.githubusercontent.com/$REPO/main"
NT="$HOME/.notetaker"
BIN="$HOME/.local/bin"
LA="$HOME/Library/LaunchAgents"
QCFG="$HOME/.config/quill/config.json"
LABEL="co.linnda.quill"
UPLABEL="co.linnda.quill.update"
MODE="install"
case "${1:-}" in
  --update) MODE="update" ;;
  --uninstall) MODE="uninstall" ;;
esac

b() { printf "\n\033[1m%s\033[0m\n" "$*"; }
ok() { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }
die() { printf "\n\033[31m✗ %s\033[0m\n" "$*"; exit 1; }
# Modo sin preguntas (pruebas o instalación asistida): QL_NOMBRE, QL_CORTO, QL_EMAIL,
# QL_ROL, QL_KEY, QL_ICS. QL_PRUEBA=1 no toca launchd, PATH ni abre el navegador.
PRUEBA="${QL_PRUEBA:-0}"
ask() { local q="$1" def="${2:-}" v; if [ -n "$def" ]; then read -r -p "  $q [$def]: " v </dev/tty || true; echo "${v:-$def}"; else read -r -p "  $q: " v </dev/tty || true; echo "$v"; fi; }
ask_secret() { local v; read -r -s -p "  $1: " v </dev/tty || true; echo >/dev/tty; echo "$v"; }

uid="$(id -u)"

# ------------------------------------------------------------------ desinstalar
if [ "$MODE" = "uninstall" ]; then
  b "Desinstalando Quill"
  launchctl bootout "gui/$uid" "$LA/$LABEL.plist" 2>/dev/null || true
  launchctl bootout "gui/$uid" "$LA/$UPLABEL.plist" 2>/dev/null || true
  rm -f "$LA/$LABEL.plist" "$LA/$UPLABEL.plist" "$BIN/quill" "$BIN/notetaker"
  ok "Quill detenido y quitado. Tus notas y grabaciones NO se borraron."
  ok "Configuración en ~/.notetaker (bórrala a mano si ya no la quieres)."
  exit 0
fi

# ------------------------------------------------------------------ revisiones
[ "$(uname)" = "Darwin" ] || die "Este instalador es para Mac."
osv="$(sw_vers -productVersion)"; major="${osv%%.*}"
[ "$major" -ge 15 ] || die "Quill necesita macOS 15 o más reciente (tienes $osv)."

if [ "$MODE" = "install" ]; then
  b "Quill Linnda · instalación"
  echo "  Graba tus reuniones (Meet, Zoom, Teams…), las transcribe y arma la nota"
  echo "  por cliente en tu Google Drive. Toma unos 3 minutos."
fi

mkdir -p "$NT" "$BIN" "$LA" "$HOME/.config/quill" "$HOME/Recordings"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

# ------------------------------------------------------------------ código
b "1/5 · Descargando Quill"
if [ -n "${QL_SRC:-}" ]; then
  src="$QL_SRC"   # copia local del repo (pruebas antes de publicar)
else
  curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/main" -o "$tmp/src.tgz" || die "No pude descargar el código."
  tar -xzf "$tmp/src.tgz" -C "$tmp"
  src="$(find "$tmp" -maxdepth 1 -type d -name '*quill-linnda*' | head -1)"
fi
[ -d "$src/app" ] || die "El paquete descargado no trae app/."
# solo código: nunca toca config.json, .env, clientes.json, cache/ ni agenda.json
cp "$src"/app/*.py "$NT/"
mkdir -p "$NT/lib" && cp "$src"/app/lib/*.py "$NT/lib/"
cp "$src/install.sh" "$NT/install.sh"
cp "$src/VERSION" "$NT/VERSION" 2>/dev/null || true
ok "Código en ~/.notetaker ($(cat "$NT/VERSION" 2>/dev/null || echo main))"

# la pluma: binario compilado por GitHub (universal); solo se reemplaza si cambió,
# porque macOS pide de nuevo los permisos cada vez que cambia el binario.
if [ -n "${QL_BIN:-}" ]; then
  cp "$QL_BIN" "$tmp/quill"   # binario local (pruebas antes de publicar)
else
  curl -fsSL "https://github.com/$REPO/releases/latest/download/quill-macos.tar.gz" -o "$tmp/quill.tgz" || die "No pude descargar la pluma (release)."
  tar -xzf "$tmp/quill.tgz" -C "$tmp"
fi
if [ -f "$BIN/quill" ] && cmp -s "$tmp/quill" "$BIN/quill"; then
  ok "Pluma al día"
  NEWBIN=0
else
  install -m 755 "$tmp/quill" "$BIN/quill"
  NEWBIN=1
  ok "Pluma instalada en ~/.local/bin/quill"
fi

# ------------------------------------------------------------------ python
b "2/5 · Preparando el proceso de notas"
UV="$(command -v uv || true)"; [ -z "$UV" ] && [ -x "$BIN/uv" ] && UV="$BIN/uv"
if [ -z "$UV" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$BIN" UV_NO_MODIFY_PATH=1 sh >/dev/null 2>&1 || die "No pude instalar uv."
  UV="$BIN/uv"
fi
PY="$NT/venv/bin/python"
[ -x "$PY" ] || "$UV" venv --quiet --python 3.12 "$NT/venv" >/dev/null 2>&1 || die "No pude crear el entorno de Python."
"$UV" pip install --quiet --python "$PY" av numpy python-dateutil >/dev/null 2>&1 || die "No pude instalar las librerías (av, numpy)."
ok "Python listo (~/.notetaker/venv)"

cat > "$BIN/notetaker" <<EOF
#!/bin/bash
exec "$PY" "$NT/notetaker.py" "\$@"
EOF
chmod +x "$BIN/notetaker"
# ~/.local/bin en el PATH para poder escribir "notetaker" en la Terminal
if [ "$PRUEBA" != 1 ] && ! grep -qs '.local/bin' "$HOME/.zprofile"; then
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zprofile"
fi

# ------------------------------------------------------------------ configuración
DRIVE=""
for d in "$HOME"/Library/CloudStorage/GoogleDrive-*/"Mi unidad" "$HOME"/Library/CloudStorage/GoogleDrive-*/"My Drive"; do
  [ -d "$d" ] && { DRIVE="$d"; break; }
done

if [ "$MODE" = "install" ] || [ ! -f "$NT/config.json" ]; then
  b "3/5 · Tus datos"
  full="$(id -F 2>/dev/null || echo "")"
  if [ -n "${QL_EMAIL:-}" ]; then
    NOMBRE="${QL_NOMBRE:-$full}"; CORTO="${QL_CORTO:-${NOMBRE%% *}}"; EMAIL="$QL_EMAIL"; ROL="${QL_ROL:-equipo de Linnda}"
  else
  NOMBRE="$(ask "Tu nombre completo" "$full")"
  CORTO="$(ask "Cómo te dicen en las reuniones" "${NOMBRE%% *}")"
  EMAIL="$(ask "Tu correo de Linnda" "")"
  ROL="$(ask "Tu rol en Linnda (ej. CTO, finanzas, growth)" "equipo de Linnda")"
  fi
  if [ -n "${QL_EMAIL:-}" ]; then KEY="${QL_KEY:-}"; else
  echo
  echo "  Llave de Gemini: pídesela a José (la de Linnda) y pégala aquí. No se ve al escribir."
  KEY="$(ask_secret "Llave de Gemini")"
  fi
  [ -n "$KEY" ] || warn "Sin llave no se pueden transcribir las reuniones; la puedes agregar luego en ~/.notetaker/.env"
  if [ -n "${QL_EMAIL:-}" ]; then ICS="${QL_ICS:-}"; else
  echo
  echo "  Calendario (para que Quill grabe solo en tus reuniones):"
  echo "  Google Calendar → ⚙️ Configuración → tu calendario → Integrar calendario →"
  echo "  'Dirección secreta en formato iCal' (termina en basic.ics). Enter para saltar."
  open "https://calendar.google.com/calendar/r/settings" >/dev/null 2>&1 || true
  ICS="$(ask "Dirección iCal" "")"
  fi

  if [ -n "$DRIVE" ]; then
    NOTES="$DRIVE/Notas-Llamadas"
    mkdir -p "$NOTES" "$DRIVE/Linnda Grabaciones"
    ok "Tus notas quedarán en Google Drive: Mi unidad/Notas-Llamadas"
  else
    NOTES="$HOME/Documents/Notas-Llamadas"
    mkdir -p "$NOTES"
    warn "No encontré Google Drive para escritorio: tus notas quedarán en Documentos/Notas-Llamadas."
    warn "Instálalo (google.com/drive/download) y vuelve a correr el instalador para que las nuevas notas queden en Drive."
  fi

  NOMBRE="$NOMBRE" CORTO="$CORTO" EMAIL="$EMAIL" ROL="$ROL" NOTES="$NOTES" "$PY" - <<'PYEOF'
import json, os
from pathlib import Path
nt = Path.home() / ".notetaker"
p = nt / "config.json"
c = json.loads(p.read_text()) if p.exists() else {}
c.update({
    "_comment": "Config de Quill Linnda. Los modelos van en listas: si Google retira uno, se usa el siguiente.",
    "recordings_dir": "~/Recordings",
    "notes_dir": os.environ["NOTES"],
    "language": "es",
    "transcribe_engine": "gemini",
    "gemini_transcribe_models": ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-2.5-flash"],
    "gemini_models": ["gemini-3.1-pro-preview", "gemini-2.5-pro", "gemini-3.8-flash"],
    "gemini_model": "gemini-2.5-pro",
    "max_hours_process": 5,
    "separar_voces": True,
    "telegram_resumen": c.get("telegram_resumen", "ninguna"),
    "mi_email": os.environ["EMAIL"].lower(),
    "persona": {"nombre": os.environ["NOMBRE"], "corto": os.environ["CORTO"],
                "email": os.environ["EMAIL"].lower(), "rol": os.environ["ROL"], "org": "Linnda"},
})
p.write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n")
PYEOF
  umask 077
  { [ -n "$KEY" ] && echo "GEMINI_API_KEY=$KEY"; [ -n "$ICS" ] && echo "CALENDAR_ICS_URL=$ICS"; true; } > "$NT/.env"
  chmod 600 "$NT/.env"
  umask 022
  ok "Configuración guardada (llaves en ~/.notetaker/.env, solo tú las puedes leer)"
else
  b "3/5 · Configuración: se mantiene la tuya"
fi

HAS_ICS=0; grep -q '^CALENDAR_ICS_URL=' "$NT/.env" 2>/dev/null && HAS_ICS=1
AUTO="off"; [ "$HAS_ICS" = 1 ] && AUTO="calendar"
PY="$PY" NT="$NT" AUTO="$AUTO" "$PY" - <<'PYEOF'
import json, os
from pathlib import Path
p = Path.home() / ".config/quill/config.json"
c = json.loads(p.read_text()) if p.exists() else {}
py, nt = os.environ["PY"], os.environ["NT"]
c.update({
    "recordings_dir": "~/Recordings",
    "transcription": {"enabled": False},
    "mic_voice_processing": False,
    "pin_builtin_mic": False,
    "on_stop": f'"{py}" "{nt}/process_session.py"',
    "auto_start": c.get("auto_start", os.environ["AUTO"]) if os.environ["AUTO"] == "calendar" else "off",
    "calendar_sync": f'"{py}" "{nt}/calendario.py" sync',
    "phone_scan": f'"{py}" "{nt}/telefono.py" scan',
})
p.write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n")
PYEOF

# ------------------------------------------------------------------ arranque
b "4/5 · Dejando la pluma lista"
# Si ya hay una pluma propia (instalación anterior), no se duplica.
if [ "$PRUEBA" = 1 ]; then
  ok "Modo prueba: no se registra en launchd"
elif [ -f "$LA/com.digimata.quill.plist" ]; then
  launchctl kickstart -k "gui/$uid/com.digimata.quill" 2>/dev/null || true
  ok "Pluma existente reiniciada"
else
  cat > "$LA/$LABEL.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$BIN/quill</string><string>run</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardErrorPath</key><string>/tmp/quill.err.log</string>
  <key>StandardOutPath</key><string>/tmp/quill.out.log</string>
</dict></plist>
EOF
  launchctl bootout "gui/$uid" "$LA/$LABEL.plist" 2>/dev/null || true
  launchctl bootstrap "gui/$uid" "$LA/$LABEL.plist" 2>/dev/null || launchctl kickstart -k "gui/$uid/$LABEL" 2>/dev/null || true
  ok "La pluma arranca sola al prender el Mac"
fi

# actualización automática diaria (7:12 a. m.)
if [ "$PRUEBA" != 1 ]; then
cat > "$LA/$UPLABEL.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$UPLABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>-c</string><string>curl -fsSL $RAW/install.sh | bash -s -- --update</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>12</integer></dict>
  <key>StandardOutPath</key><string>$HOME/Recordings/_actualizacion.log</string>
  <key>StandardErrorPath</key><string>$HOME/Recordings/_actualizacion.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$uid" "$LA/$UPLABEL.plist" 2>/dev/null || true
launchctl bootstrap "gui/$uid" "$LA/$UPLABEL.plist" 2>/dev/null || true
ok "Se actualiza solo cada mañana"
fi

# ------------------------------------------------------------------ prueba
b "5/5 · Revisando"
if [ "$HAS_ICS" = 1 ]; then
  out="$("$PY" "$NT/calendario.py" sync --fresco 2>&1 || true)"
  case "$out" in OK*) ok "Calendario conectado: $out" ;; *) warn "Calendario: $out" ;; esac
else
  warn "Sin calendario: la pluma no arrancará sola (solo avisa con la pluma naranja)."
fi
grep -q '^GEMINI_API_KEY=' "$NT/.env" 2>/dev/null && ok "Llave de Gemini guardada" || warn "Falta la llave de Gemini (~/.notetaker/.env)"
[ "$PRUEBA" != 1 ] && [ "$MODE" = "update" ] && [ "$NEWBIN" = 1 ] && launchctl kickstart -k "gui/$uid/$LABEL" 2>/dev/null || true

if [ "$MODE" = "install" ]; then
  b "Listo. Últimos dos pasos:"
  echo "  1. Busca la pluma en la barra de menú (arriba a la derecha) → 'Empezar a grabar'."
  echo "     macOS te pedirá dos permisos para 'quill': Micrófono y Grabación de pantalla"
  echo "     y audio del sistema. Acepta ambos (si no salen: Ajustes del Sistema → Privacidad)."
  echo "  2. Habla 30 segundos, 'Detener grabación', y en 1-2 minutos aparece la nota en"
  echo "     Notas-Llamadas. Si la reunión está en tu calendario, la próxima vez graba sola."
  echo
fi
