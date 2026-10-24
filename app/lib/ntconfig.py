"""Carga de configuración y secretos del notetaker.

Todo vive bajo ~/.notetaker/ para que la herramienta sea autónoma:
  - config.json      parámetros (rutas, modelo, idioma)
  - clientes.json    lista de clientes de Linnda + alias (para clasificar)
  - .env             GEMINI_API_KEY (permisos 600)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

NT_DIR = Path(__file__).resolve().parent.parent  # ~/.notetaker


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve()


def load_config() -> dict:
    cfg_path = NT_DIR / "config.json"
    cfg = {}
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text())
    cfg.setdefault("recordings_dir", "~/Recordings")
    cfg.setdefault("notes_dir", "~/Notas-Llamadas")
    cfg.setdefault("language", "es")
    cfg.setdefault("transcribe_engine", "gemini")  # "gemini" (nube, rápido) | "local" (whisper)
    cfg.setdefault("gemini_transcribe_model", "gemini-2.5-flash")
    cfg.setdefault("whisper_model", "large-v3-turbo")
    cfg.setdefault("whisper_compute_type", "int8")
    cfg.setdefault("whisper_beam_size", 1)
    cfg.setdefault("gemini_model", "gemini-2.5-pro")
    cfg.setdefault("max_hours_process", 5)
    cfg.setdefault("min_seconds", 20)
    cfg.setdefault("separar_voces", True)
    cfg.setdefault("telegram_resumen", "ninguna")
    cfg.setdefault("conocimiento_dir", "~/Documents/Linnda Conocimiento")
    cfg["recordings_dir"] = _expand(cfg["recordings_dir"])
    cfg["notes_dir"] = _expand(cfg["notes_dir"])
    return cfg


def persona() -> dict:
    """Quién es el dueño de este Mac (lo pide el instalador).

    {"nombre": "José Clavijo", "corto": "José", "email": "jose@linnda.co",
     "rol": "fundador de Linnda", "org": "Linnda"}
    """
    cfg = {}
    try:
        cfg = json.loads((NT_DIR / "config.json").read_text())
    except Exception:
        pass
    p = dict(cfg.get("persona") or {})
    p.setdefault("email", cfg.get("mi_email", ""))
    p.setdefault("nombre", (p.get("email") or "yo").split("@")[0].replace(".", " ").title())
    p.setdefault("corto", p["nombre"].split()[0])
    p.setdefault("org", "Linnda")
    p.setdefault("rol", f"equipo de {p['org']}")
    p["etiqueta"] = f"{p['corto']} ({p['org']})"
    return p


def clientes_path() -> Path:
    """La lista vive junto a las notas (Notas-Llamadas/_clientes.json) para que
    el dueño y sus rutinas la vean y editen. Respaldo: ~/.notetaker/clientes.json."""
    try:
        p = load_config()["notes_dir"] / "_clientes.json"
        if p.exists():
            return p
    except Exception:
        pass
    return NT_DIR / "clientes.json"


def load_clientes() -> list[dict]:
    p = clientes_path()
    if not p.exists():
        return []
    data = json.loads(p.read_text())
    return data.get("clientes", [])


def load_env() -> dict:
    """Lee ~/.notetaker/.env (KEY=VALUE por línea). Ignora comentarios."""
    env = {}
    p = NT_DIR / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    # variables reales del entorno ganan sobre el archivo
    for k in ("GEMINI_API_KEY", "GEMINI_MODEL"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def gemini_api_key() -> str | None:
    return load_env().get("GEMINI_API_KEY") or None


def _norm(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return "".join(ch for ch in t.lower() if ch.isalnum())


def add_cliente(nombre: str, aliases: list[str] | None = None, prospecto: bool = True) -> bool:
    """Agrega un cliente/prospecto a clientes.json si no existe. True si lo agregó."""
    nombre = (nombre or "").strip()
    if not nombre:
        return False
    p = clientes_path()
    data = json.loads(p.read_text()) if p.exists() else {"clientes": []}
    n = _norm(nombre)
    for c in data.get("clientes", []):
        if _norm(c.get("nombre", "")) == n or n in {_norm(a) for a in c.get("aliases", [])}:
            return False
    entry = {"nombre": nombre, "aliases": sorted({nombre.lower(), *(a.lower() for a in (aliases or []))})}
    if prospecto:
        entry["prospecto"] = True
    data.setdefault("clientes", []).append(entry)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return True
