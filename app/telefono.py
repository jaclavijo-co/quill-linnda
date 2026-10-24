#!/usr/bin/env python3
"""Grabaciones del teléfono → notas del Notetaker.

Fuentes (se revisan cada 3 min, lo lanza Quill):
  0. Google Drive/Linnda Grabaciones/ — desde el Android: grabadora → Compartir →
     Drive → Linnda Grabaciones (requiere Google Drive para escritorio en el Mac).
  1. ~/Recordings/_entrantes/  — un bot de Telegram (opcional) deja aquí los audios
     largos que le mandan (+ un .json con datos del mensaje).
  2. iCloud Drive/Linnda Grabaciones/ — desde Notas de Voz del iPhone:
     Compartir → Guardar en Archivos → iCloud Drive → Linnda Grabaciones.
Cada audio se vuelve una sesión en ~/Recordings/<fecha>-tel con una sola pista
("mono") y pasa por el mismo proceso: voces separadas, calendario, contexto
del cliente, nota en Notas-Llamadas y resumen por Telegram.

Uso: telefono.py scan
"""
from __future__ import annotations

import fcntl
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
NT = Path(__file__).resolve().parent
REC = HOME / "Recordings"
ENTRANTES = REC / "_entrantes"
ICLOUD = HOME / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Linnda Grabaciones"
CLOUDSTORAGE = HOME / "Library" / "CloudStorage"  # Google Drive para escritorio


def _carpetas_drive() -> list[Path]:
    """Google Drive para escritorio: .../GoogleDrive-<cuenta>/<Mi unidad|My Drive>/Linnda Grabaciones"""
    out = []
    try:
        for cuenta in CLOUDSTORAGE.glob("GoogleDrive-*"):
            for unidad in ("Mi unidad", "My Drive"):
                d = cuenta / unidad / "Linnda Grabaciones"
                if d.exists():
                    out.append(d)
    except PermissionError:
        log("sin permiso para leer Google Drive (macOS): revisa Privacidad → Archivos y carpetas")
    return out
STATE = NT / "cache" / "telefono_procesados.json"
LOCK = NT / "cache" / "telefono.lock"
LOG = REC / "_telefono.log"
PY = sys.executable  # el mismo Python (venv) que corre este scan
AUDIO = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".oga", ".opus", ".caf", ".qta", ".mp4", ".amr", ".flac"}
MIN_SEG = 30


def log(msg: str):
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line)
    with LOG.open("a") as f:
        f.write(line + "\n")


def _state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(st: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1))


def _info(path: Path) -> tuple[float, datetime | None]:
    """(duración en segundos, fecha de grabación si el archivo la trae)."""
    import av
    c = av.open(str(path))
    dur = (c.duration or 0) / 1_000_000
    meta = dict(c.metadata or {})
    for st in c.streams:
        meta.update({k: v for k, v in (st.metadata or {}).items() if k not in meta})
    if not dur and c.streams.audio:
        st = c.streams.audio[0]
        if st.duration and st.time_base:
            dur = float(st.duration * st.time_base)
    c.close()
    creado = None
    for k in ("creation_time", "date", "com.apple.quicktime.creationdate"):
        v = meta.get(k)
        if v:
            try:
                creado = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                if creado.tzinfo is None:
                    creado = creado.replace(tzinfo=timezone.utc)
                if creado.year < 2020:
                    creado = None
                break
            except Exception:
                continue
    return dur, creado


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nueva_sesion(audio: Path, origen: str, extra: dict) -> Path | None:
    dur, creado = _info(audio)
    if dur < MIN_SEG:
        log(f"  {audio.name}: {dur:.0f} s, muy corto; lo salto")
        return None
    if extra.get("fecha_mensaje"):  # nota de voz de Telegram: el mensaje llega al terminar
        fin = datetime.fromisoformat(extra["fecha_mensaje"])
        ini = creado or (fin - timedelta(seconds=dur))
    elif creado:
        ini = creado
    else:
        ini = datetime.fromtimestamp(audio.stat().st_mtime, timezone.utc) - timedelta(seconds=dur)
    fin = ini + timedelta(seconds=dur)
    base = ini.astimezone().strftime("%Y.%m.%d-%H%M") + "-tel"
    d, n = REC / base, 2
    while d.exists():
        d, n = REC / f"{base}-{n}", n + 1
    d.mkdir(parents=True)
    dst = d / ("audio" + audio.suffix.lower())
    shutil.copy2(audio, dst)
    meta = {
        "started": _iso(ini), "ended": _iso(fin), "duration_seconds": int(dur),
        "files": {"mono": dst.name}, "start_offset_ms": {"mono": 0},
        "origen": origen, "archivo_original": audio.name,
    }
    if extra.get("caption"):
        meta["comentario"] = extra["caption"]
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return d


def _procesar(d: Path):
    log(f"  procesando {d.name} …")
    r = subprocess.run([PY, str(NT / "process_session.py"), str(d)], capture_output=True, text=True)
    log(f"  {d.name}: código {r.returncode}")


def scan() -> int:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    lockf = open(LOCK, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0  # ya hay otro scan trabajando
    ENTRANTES.mkdir(parents=True, exist_ok=True)
    st = _state()
    fuentes = [(ENTRANTES, "telegram")]
    fuentes += [(d, "drive") for d in _carpetas_drive()]
    try:
        if ICLOUD.exists():
            fuentes.append((ICLOUD, "notas_de_voz"))
    except PermissionError:
        log("sin permiso para leer iCloud Drive (macOS): revisa Privacidad → Archivos y carpetas")
    try:  # estado para revisar que se ven las carpetas (Drive, iCloud)
        (NT / "cache" / "telefono_estado.json").write_text(json.dumps(
            {"revisado": datetime.now().isoformat(timespec="seconds"),
             "carpetas": [f"{o}: {c}" for c, o in fuentes]}, ensure_ascii=False, indent=1))
    except Exception:
        pass
    for carpeta, origen in fuentes:
        try:
            archivos = sorted(carpeta.iterdir())
        except PermissionError:
            log(f"sin permiso para leer {carpeta}")
            continue
        for f in archivos:
            if f.is_dir():
                continue
            if f.name.startswith(".") and f.name.endswith(".icloud"):
                real = f.with_name(f.name[1:-len(".icloud")])
                subprocess.run(["brctl", "download", str(real)], capture_output=True)
                continue
            if f.suffix.lower() not in AUDIO:
                continue
            sidecar = f.with_name(f.name + ".json")
            if origen == "telegram" and not sidecar.exists():
                continue  # el bot todavía lo está escribiendo
            if origen != "telegram" and time.time() - f.stat().st_mtime < 60:
                continue  # todavía se está sincronizando
            key = f"{origen}|{f.name}|{f.stat().st_size}"
            if key in st:
                continue
            extra = {}
            if sidecar.exists():
                try:
                    extra = json.loads(sidecar.read_text())
                except Exception:
                    pass
            log(f"nuevo audio ({origen}): {f.name}")
            try:
                d = _nueva_sesion(f, origen, extra)
            except Exception as e:
                log(f"  ERROR leyendo {f.name}: {e}")
                st[key] = f"error: {e}"
                _save_state(st)
                continue
            st[key] = d.name if d else "saltado"
            _save_state(st)
            if d:
                _procesar(d)
            hecho = carpeta / "Procesadas"
            hecho.mkdir(exist_ok=True)
            for x in (f, sidecar):
                if x.exists() and not (hecho / x.name).exists():
                    shutil.move(str(x), str(hecho / x.name))
    _cola_reprocesar()
    return 0


COLA = REC / "_reprocesar"
FLAGS_OK = {"--solo-resumen", "--sin-contexto", "--reunificar"}


def _cola_reprocesar():
    """Cola para rehacer notas sin abrir la Terminal: un archivo ~/Recordings/_reprocesar/<sesion>
    (contenido opcional: --solo-resumen y/o --sin-contexto). Solo corre el proceso del Notetaker."""
    if not COLA.exists():
        return
    for f in sorted(COLA.iterdir()):
        if f.is_dir() or f.name.startswith("."):
            continue
        nombre = f.name[:-4] if f.name.endswith(".txt") else f.name
        ses = REC / nombre
        flags = [x for x in f.read_text().split() if x in FLAGS_OK]
        f.unlink()
        if not (ses / "meta.json").exists():
            log(f"reprocesar: no existe la sesión {nombre}")
            continue
        (ses / ".notetaker_done").unlink(missing_ok=True)
        log(f"reprocesar {ses.name} {' '.join(flags)}")
        r = subprocess.run([PY, str(NT / "process_session.py"), str(ses), *flags], capture_output=True, text=True)
        log(f"  {ses.name}: código {r.returncode}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "scan":
        sys.exit(scan())
    print(__doc__)
    sys.exit(1)
