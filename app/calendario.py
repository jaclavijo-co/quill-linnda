#!/usr/bin/env python3
"""Agenda del dueño de este Mac desde su calendario de Google (dirección iCal privada).

Uso:
  calendario.py sync                 -> escribe ~/.notetaker/agenda.json (de hoy-1 a hoy+2).
                                        Quill lo llama cada 5 min para saber si hay reunión.
  calendario.py en <inicio> <fin>    -> imprime (JSON) la reunión que más se cruza con ese rango.

La URL va en ~/.notetaker/.env como CALENDAR_ICS_URL=... (privada: no compartir).
Parser propio (sin dependencias raras): desdobla líneas, zonas horarias con
zoneinfo y repeticiones con python-dateutil (RRULE, EXDATE, RECURRENCE-ID).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NT_DIR = Path(__file__).resolve().parent
CACHE = NT_DIR / "cache" / "calendar.ics"
AGENDA = NT_DIR / "agenda.json"
MAX_AGE_S = 240
LOCAL_TZ = datetime.now().astimezone().tzinfo


# ---------------------------------------------------------------- config

def _env() -> dict:
    env = {}
    p = NT_DIR / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _config() -> dict:
    try:
        return json.loads((NT_DIR / "config.json").read_text())
    except Exception:
        return {}


def mi_email() -> str:
    c = _config()
    return ((c.get("persona") or {}).get("email") or c.get("mi_email") or "").lower()


def dominio_propio() -> str:
    return mi_email().split("@")[-1] or "linnda.co"


# ---------------------------------------------------------------- descarga

def fetch_ics(max_age: int = MAX_AGE_S) -> str:
    """Devuelve el .ics (usa caché si tiene menos de max_age segundos)."""
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < max_age:
        return CACHE.read_text(errors="replace")
    url = _env().get("CALENDAR_ICS_URL") or os.environ.get("CALENDAR_ICS_URL")
    if not url:
        raise RuntimeError("falta CALENDAR_ICS_URL en ~/.notetaker/.env")
    req = urllib.request.Request(url, headers={"User-Agent": "notetaker-linnda"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read().decode("utf-8", errors="replace")
    if "BEGIN:VCALENDAR" not in data:
        raise RuntimeError("la URL no devolvió un calendario")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(data)
    os.chmod(tmp, 0o600)
    tmp.replace(CACHE)
    return data


# ---------------------------------------------------------------- parser

def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        elif raw:
            out.append(raw)
    return out


def _split_prop(line: str):
    """'ATTENDEE;CN="A, B";PARTSTAT=ACCEPTED:mailto:x' -> (name, params, value)."""
    i, inq = 0, False
    while i < len(line):
        ch = line[i]
        if ch == '"':
            inq = not inq
        elif ch == ":" and not inq:
            break
        i += 1
    head, value = line[:i], line[i + 1:]
    parts, buf, inq = [], "", False
    for ch in head:
        if ch == '"':
            inq = not inq
        if ch == ";" and not inq:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    name = parts[0].upper()
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, _, v = p.partition("=")
            params[k.upper()] = v.strip('"')
    return name, params, value


def _unescape(v: str) -> str:
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _parse_dt(value: str, params: dict):
    """Devuelve datetime con zona (o date para eventos de día completo)."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    utc = value.endswith("Z")
    dt = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    if utc:
        return dt.replace(tzinfo=timezone.utc)
    tzid = params.get("TZID")
    try:
        return dt.replace(tzinfo=ZoneInfo(tzid)) if tzid else dt.replace(tzinfo=LOCAL_TZ)
    except Exception:
        return dt.replace(tzinfo=LOCAL_TZ)


def _parse_duration(v: str) -> timedelta:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v.strip())
    if not m:
        return timedelta(0)
    sign, w, d, h, mi, s = m.groups()
    td = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0), seconds=int(s or 0))
    return -td if sign == "-" else td


def _vevents(text: str) -> list[dict]:
    events, cur, depth = [], None, 0
    for line in _unfold(text):
        up = line.upper()
        if up == "BEGIN:VEVENT":
            cur, depth = {"props": []}, 0
            continue
        if cur is None:
            continue
        if up.startswith("BEGIN:"):
            depth += 1  # VALARM u otros subcomponentes
            continue
        if up.startswith("END:") and depth:
            depth -= 1
            continue
        if up == "END:VEVENT":
            events.append(cur)
            cur = None
            continue
        if depth == 0:
            cur["props"].append(_split_prop(line))
    return events


def _first(ev, name):
    for n, p, v in ev["props"]:
        if n == name:
            return p, v
    return None, None


def _all(ev, name):
    return [(p, v) for n, p, v in ev["props"] if n == name]


_MEET_RE = re.compile(r"https?://(?:meet\.google\.com|[\w.-]*zoom\.us|teams\.microsoft\.com|teams\.live\.com|[\w.-]*webex\.com)/[^\s\"<>\\]+", re.I)


def _base_info(ev) -> dict:
    _, summary = _first(ev, "SUMMARY")
    _, desc = _first(ev, "DESCRIPTION")
    _, loc = _first(ev, "LOCATION")
    _, status = _first(ev, "STATUS")
    _, transp = _first(ev, "TRANSP")
    _, uid = _first(ev, "UID")
    _, conf = _first(ev, "X-GOOGLE-CONFERENCE")
    op, org = _first(ev, "ORGANIZER")
    yo = mi_email()
    asistentes, mi_estado = [], None
    for p, v in _all(ev, "ATTENDEE"):
        email = v.split(":", 1)[-1].strip().lower() if ":" in v else v.strip().lower()
        estado = (p.get("PARTSTAT") or "").upper()
        nombre = p.get("CN") or email
        if nombre.lower() == email:
            nombre = email.split("@")[0].replace(".", " ").title()
        if email == yo:
            mi_estado = estado
        asistentes.append({"nombre": nombre, "email": email, "estado": estado,
                           "opcional": p.get("ROLE", "").upper() == "OPT-PARTICIPANT"})
    texto = " ".join(_unescape(x or "") for x in (desc, loc, conf))
    m = _MEET_RE.search(texto)
    desc_txt = _unescape(desc or "")
    return {
        "uid": uid or "",
        "titulo": _unescape(summary or "(sin título)"),
        "descripcion": desc_txt[:1500],
        "organizador": (org or "").split(":", 1)[-1].lower(),
        "asistentes": asistentes,
        "mi_estado": mi_estado or ("ACCEPTED" if (org or "").lower().endswith(yo) else ""),
        "enlace": m.group(0) if m else "",
        "cancelado": (status or "").upper() == "CANCELLED",
        "transparente": (transp or "").upper() == "TRANSPARENT",
        "bloque_backlog": "[auto-backlog]" in desc_txt,
    }


def _occurrences(ev, desde: datetime, hasta: datetime):
    """Genera (inicio, fin) de cada ocurrencia que toca [desde, hasta]."""
    sp, sv = _first(ev, "DTSTART")
    if sv is None:
        return
    start = _parse_dt(sv, sp)
    if not isinstance(start, datetime):
        return  # día completo: no es una reunión
    ep, evv = _first(ev, "DTEND")
    if evv:
        end = _parse_dt(evv, ep)
        dur = end - start if isinstance(end, datetime) else timedelta(hours=1)
    else:
        _, dv = _first(ev, "DURATION")
        dur = _parse_duration(dv) if dv else timedelta(0)
    rrules = [v for _, v in _all(ev, "RRULE")]
    if not rrules:
        if start < hasta and start + dur > desde:
            yield start, start + dur
        return
    try:
        from dateutil.rrule import rruleset, rrulestr
    except ImportError:
        return
    rs = rruleset()
    for r in rrules:
        # UNTIL en UTC con DTSTART con zona: dateutil lo acepta si ambos tienen tz
        r2 = re.sub(r"UNTIL=(\d{8})(;|$)", r"UNTIL=\1T235959Z\2", r)
        rs.rrule(rrulestr(r2, dtstart=start))
    for p, v in _all(ev, "EXDATE"):
        for one in v.split(","):
            try:
                ex = _parse_dt(one, p)
                if isinstance(ex, datetime):
                    rs.exdate(ex)
            except Exception:
                pass
    for occ in rs.between(desde - dur, hasta, inc=True):
        if occ < hasta and occ + dur > desde:
            yield occ, occ + dur


def eventos(desde: datetime, hasta: datetime, ics: str | None = None) -> list[dict]:
    ics = ics if ics is not None else fetch_ics()
    vev = _vevents(ics)
    overrides = {}  # (uid, inicio original) -> vevent
    for ev in vev:
        rp, rv = _first(ev, "RECURRENCE-ID")
        if rv:
            _, uid = _first(ev, "UID")
            overrides[(uid, _parse_dt(rv, rp))] = ev
    out = []
    for ev in vev:
        if _first(ev, "RECURRENCE-ID")[1]:
            # instancia modificada: se trata como evento suelto
            for s, e in _occurrences({"props": [x for x in ev["props"] if x[0] != "RRULE"]}, desde, hasta):
                out.append(_mk(ev, s, e))
            continue
        _, uid = _first(ev, "UID")
        for s, e in _occurrences(ev, desde, hasta):
            if (uid, s) in overrides:
                continue  # la reemplaza la instancia modificada
            out.append(_mk(ev, s, e))
    out = [e for e in out if not e["cancelado"]]
    out.sort(key=lambda e: e["inicio"])
    return out


def _mk(ev, s: datetime, e: datetime) -> dict:
    info = _base_info(ev)
    otros = [a for a in info["asistentes"] if a["email"] != mi_email()]
    externos = [a for a in otros if not a["email"].endswith("@" + dominio_propio())]
    info.update({
        "inicio": s.astimezone(LOCAL_TZ).isoformat(timespec="minutes"),
        "fin": e.astimezone(LOCAL_TZ).isoformat(timespec="minutes"),
        "con_externos": bool(externos),
        "es_reunion": bool(otros or info["enlace"]) and info["mi_estado"] != "DECLINED"
                      and not info["bloque_backlog"],
    })
    info["cliente"] = cliente_de(info)
    return info


# ---------------------------------------------------------------- clientes

def _clientes() -> list[dict]:
    notes = Path(os.path.expanduser(_config().get("notes_dir", "~/Documents/Notas-Llamadas")))
    for p in (notes / "_clientes.json", NT_DIR / "clientes.json"):
        try:
            return json.loads(p.read_text()).get("clientes", [])
        except Exception:
            continue
    return []


def _norm(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", t.lower())


def cliente_de(ev: dict) -> str:
    """Cliente por dominio de los invitados externos; si no, por palabras del título."""
    clientes = _clientes()
    doms = {a["email"].split("@")[-1] for a in ev.get("asistentes", [])}
    for c in clientes:
        if c.get("nombre") == "Linnda Interno":
            continue
        if doms & set(c.get("dominios", [])):
            return c["nombre"]
    titulo = " " + _norm(ev.get("titulo", "")) + " "
    for c in clientes:
        for a in [c.get("nombre", "")] + c.get("aliases", []):
            a = _norm(a).strip()
            if len(a) >= 4 and f" {a} " in titulo:
                return c["nombre"]
    if ev.get("asistentes") and not ev.get("con_externos"):
        return "Linnda Interno"
    # Sin lista: el dominio corporativo de los invitados externos (dicarina.com → Dicarina)
    genericos = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com", "live.com",
                 "me.com", "msn.com", "protonmail.com", "googlemail.com", dominio_propio()}
    from collections import Counter
    externos = Counter(d for d in (a["email"].split("@")[-1] for a in ev.get("asistentes", []))
                       if d and d not in genericos and "calendar.google.com" not in d)
    if externos:
        dom = externos.most_common(1)[0][0]
        base = dom.split(".")[0]
        return base.replace("-", " ").title() if base else ""
    return ""


# ---------------------------------------------------------------- uso

def reunion_en(inicio: datetime, fin: datetime, ics: str | None = None) -> dict | None:
    """La reunión del calendario que más se cruza con [inicio, fin] (±15 min)."""
    margen = timedelta(minutes=15)
    cands = [e for e in eventos(inicio - margen, fin + margen, ics) if e["es_reunion"]]
    best, best_ov = None, timedelta(0)
    for e in cands:
        s, f = datetime.fromisoformat(e["inicio"]), datetime.fromisoformat(e["fin"])
        ov = min(f, fin) - max(s, inicio)
        if ov > best_ov:
            best, best_ov = e, ov
    if best is None:  # grabación corta que empezó un poco antes/después
        for e in cands:
            s = datetime.fromisoformat(e["inicio"])
            if abs(s - inicio) <= margen:
                return e
    return best


def sync(fresco: bool = False) -> int:
    now = datetime.now(LOCAL_TZ)
    try:
        ics = fetch_ics(max_age=0 if fresco else MAX_AGE_S)
        evs = eventos(now - timedelta(days=1), now + timedelta(days=2), ics)
        data = {"generado": now.isoformat(timespec="seconds"), "ok": True,
                "eventos": [{k: v for k, v in e.items() if k != "descripcion"} for e in evs]}
    except Exception as e:
        prev = {}
        try:
            prev = json.loads(AGENDA.read_text())
        except Exception:
            pass
        data = {**prev, "ok": False, "error": str(e)[:300], "intento": now.isoformat(timespec="seconds")}
    tmp = AGENDA.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(AGENDA)
    print(("OK " if data.get("ok") else "ERROR ") + f"{len(data.get('eventos', []))} eventos" +
          ("" if data.get("ok") else f": {data.get('error')}"))
    return 0 if data.get("ok") else 1


def main(argv):
    if len(argv) >= 2 and argv[1] == "sync":
        return sync(fresco="--fresco" in argv)
    if len(argv) >= 4 and argv[1] == "en":
        i, f = datetime.fromisoformat(argv[2]), datetime.fromisoformat(argv[3])
        i = i if i.tzinfo else i.replace(tzinfo=LOCAL_TZ)
        f = f if f.tzinfo else f.replace(tzinfo=LOCAL_TZ)
        print(json.dumps(reunion_en(i, f), ensure_ascii=False, indent=1))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
