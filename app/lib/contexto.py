"""Contexto para armar la nota: la reunión del calendario y la memoria de Linnda
sobre el cliente (ficha de Linnda Conocimiento, última llamada y tareas abiertas).

Todo se lee en el Mac. Nada de esto se toma como "dicho en la reunión": el
resumen lo usa solo para entender, comparar y conectar con el backlog.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

NT_DIR = Path(__file__).resolve().parent.parent
HOME = Path.home()
def _conocimiento_dir() -> Path:
    try:
        cfg = json.loads((NT_DIR / "config.json").read_text())
        return Path(os.path.expanduser(cfg.get("conocimiento_dir", "~/Documents/Linnda Conocimiento")))
    except Exception:
        return HOME / "Documents" / "Linnda Conocimiento"


CONOCIMIENTO = _conocimiento_dir()
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


# ------------------------------------------------------------ calendario

def reunion(session_dir: Path, log=print) -> dict | None:
    """Reunión del calendario que corresponde a la grabación (y la guarda en evento.json)."""
    cache = session_dir / "evento.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text()) or None
        except Exception:
            pass
    try:
        meta = json.loads((session_dir / "meta.json").read_text())
        ini = datetime.fromisoformat(meta["started"].replace("Z", "+00:00"))
        fin = datetime.fromisoformat(meta["ended"].replace("Z", "+00:00"))
    except Exception:
        return None
    try:
        sys.path.insert(0, str(NT_DIR))
        import calendario
        ev = calendario.reunion_en(ini, fin)
    except Exception as e:
        log(f"  (calendario no disponible: {e})")
        return None
    cache.write_text(json.dumps(ev or {}, ensure_ascii=False, indent=1))
    return ev


def texto_reunion(ev: dict) -> str:
    asis = []
    for a in ev.get("asistentes", []):
        dom = a["email"].split("@")[-1]
        estado = {"ACCEPTED": "aceptó", "DECLINED": "rechazó", "TENTATIVE": "tal vez",
                  "NEEDS-ACTION": "sin responder"}.get(a.get("estado", ""), a.get("estado", ""))
        asis.append(f"  - {a['nombre']} <{a['email']}> ({dom}; {estado}{'; opcional' if a.get('opcional') else ''})")
    return (f"Título: {ev.get('titulo','')}\nHorario: {ev.get('inicio','')} a {ev.get('fin','')}\n"
            f"Cliente según el calendario: {ev.get('cliente') or '(no identificado)'}\n"
            f"Invitados (no todos asisten; quien rechazó probablemente no estuvo):\n" + "\n".join(asis))


# ------------------------------------------------------------ memoria del cliente

def _ficha_path(cliente: str) -> Path | None:
    n = _norm(cliente)
    if not n or not CONOCIMIENTO.exists():
        return None
    cands = []
    for sub in ("20 Clientes", "30 Prospectos"):
        d = CONOCIMIENTO / sub
        if d.exists():
            cands += [p for p in d.glob("*.md") if p.stem.lower() != "pipeline"]
    for p in cands:
        if _norm(p.stem) == n:
            return p
    for p in cands:
        s = _norm(p.stem)
        if s and (s in n or n in s):
            return p
    return None


def _fechas_reunion(fecha: str) -> list[str]:
    """Formas en que puede aparecer la fecha de la reunión en la ficha."""
    try:
        d = datetime.strptime(fecha, "%Y-%m-%d")
    except Exception:
        return [fecha]
    m = MESES[d.month - 1]
    return [fecha, f"{d.day}-{m}", f"{d.day:02d}-{m}", f"{d.day} de {m}"]


def ficha(cliente: str, fecha: str, max_chars: int = 7000) -> str:
    """Estado actual, riesgos y bitácora reciente ANTERIOR a la reunión."""
    p = _ficha_path(cliente)
    if not p:
        return ""
    txt = p.read_text(errors="replace")
    marcas = _fechas_reunion(fecha)
    secciones = re.split(r"(?m)^## ", txt)
    out = [secciones[0].strip()]
    bitacora = []
    for sec in secciones[1:]:
        titulo, _, cuerpo = sec.partition("\n")
        lineas = [l for l in cuerpo.splitlines() if l.strip()]
        # nada de la propia reunión ni posterior (para no "adivinar" con la nota ya escrita)
        lineas = [l for l in lineas if not any(m in l for m in marcas)]
        if titulo.lower().startswith("bit"):
            fechadas = []
            for l in lineas:
                mm = re.search(r"\*\*(\d{4}-\d{2}-\d{2})\*\*", l)
                if mm and mm.group(1) >= fecha:
                    continue
                fechadas.append(l)
            bitacora = fechadas[-8:]
            continue
        if lineas:
            out.append(f"## {titulo.strip()}\n" + "\n".join(lineas))
    if bitacora:
        out.append("## Bitácora reciente (antes de esta reunión)\n" + "\n".join(bitacora))
    res = "\n\n".join(out)
    return res[:max_chars] + ("…" if len(res) > max_chars else "")


def ultima_llamada(notes_dir: Path, cliente: str, session_name: str, fecha: str) -> str:
    idx = notes_dir / "index.jsonl"
    if not idx.exists():
        return ""
    n = _norm(cliente)
    previas = []
    for line in idx.read_text().splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("sesion") == session_name or _norm(e.get("cliente", "")) != n:
            continue
        if e.get("fecha", "") > fecha:
            continue
        previas.append(e)
    if not previas:
        return ""
    e = sorted(previas, key=lambda x: (x.get("fecha", ""), x.get("sesion", "")))[-1]
    li = lambda xs: "\n".join(f"  - {x}" for x in (xs or [])[:8]) or "  —"
    return (f"{e.get('fecha')} · {e.get('titulo')}\nResumen: {e.get('resumen','')}\n"
            f"Acuerdos:\n{li(e.get('acuerdos'))}\nPróximos pasos:\n{li(e.get('proximos_pasos'))}")


def tareas_abiertas(notes_dir: Path, cliente: str) -> str:
    p = notes_dir / "_backlog_abierto.json"
    if not p.exists():
        return ""
    try:
        data = json.loads(p.read_text())
    except Exception:
        return ""
    n = _norm(cliente)
    out = []
    for t in data.get("tareas", []):
        c = _norm(t.get("cliente", ""))
        if c and (c == n or n in c or c in n):
            fl = f" · vence {t['fecha_limite']}" if t.get("fecha_limite") else ""
            out.append(f"  - {t['id']} [{t.get('tipo','')} · {t.get('responsable','')} · {t.get('estado','')}{fl}] {t.get('tarea','')}")
    if not out:
        return ""
    return f"(copia del backlog del {data.get('generado','?')})\n" + "\n".join(out)


def bloque_cliente(notes_dir: Path, cliente: str, session_name: str, fecha: str) -> str:
    partes = []
    f = ficha(cliente, fecha)
    if f:
        partes.append("### Ficha del cliente (Linnda Conocimiento)\n" + f)
    u = ultima_llamada(notes_dir, cliente, session_name, fecha)
    if u:
        partes.append("### Última llamada registrada con este cliente\n" + u)
    t = tareas_abiertas(notes_dir, cliente)
    if t:
        partes.append("### Tareas abiertas del cliente en el backlog\n" + t)
    return "\n\n".join(partes)


def contactos(cliente: str) -> list[str]:
    """Nombres de la línea '**Contacto(s):**' de la ficha del cliente."""
    p = _ficha_path(cliente)
    if not p:
        return []
    m = re.search(r"\*\*Contacto\(s\):\*\*\s*(.+)", p.read_text(errors="replace"))
    if not m:
        return []
    txt = re.sub(r"\([^)]*\)", "", m.group(1))
    out = [x.strip(" ._*") for x in re.split(r",| y ", txt)]
    return [x for x in out if x and "completar" not in x.lower() and len(x) < 40]
