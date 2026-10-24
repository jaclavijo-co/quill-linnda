#!/usr/bin/env python3
"""CLI del notetaker de Linnda.

Comandos:
  notetaker buscar <texto>              busca en todas las llamadas registradas
  notetaker lista [--cliente X]         lista llamadas (opcionalmente de un cliente)
  notetaker reclasificar <sesion> <Cliente>   corrige el cliente de una llamada
  notetaker reprocesar <sesion>         re-transcribe y re-resume una sesión
  notetaker procesar <sesion>           procesa una sesión (igual que el hook)
  notetaker pendientes                  procesa sesiones grabadas aún sin nota
  notetaker audio [sesion]              mide dB de ambas pistas (verifica captura)
  notetaker estado                      chequeo de salud del sistema

<sesion> puede ser el nombre de carpeta (2026.08.03-1430) o una ruta.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

NT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(NT_DIR / "lib"))

import ntconfig  # noqa: E402


# ─────────────────────────────── helpers ────────────────────────────────

def _load_index(notes_dir: Path) -> list[dict]:
    p = notes_dir / "index.jsonl"
    out = []
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _resolve_session(arg: str, recordings_dir: Path) -> Path:
    p = Path(arg).expanduser()
    if p.exists() and p.is_dir():
        return p.resolve()
    cand = recordings_dir / arg
    if cand.exists():
        return cand.resolve()
    raise SystemExit(f"No encuentro la sesión «{arg}» (ni como ruta ni en {recordings_dir})")


def _c(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


# ─────────────────────────────── comandos ───────────────────────────────

def cmd_buscar(args):
    cfg = ntconfig.load_config()
    if not args:
        raise SystemExit("uso: notetaker buscar <texto>")
    query = " ".join(args).lower()
    terms = [t for t in re.split(r"\s+", query) if t]
    entries = _load_index(cfg["notes_dir"])
    if not entries:
        print("(aún no hay llamadas registradas)")
        return 0

    def haystack(e: dict) -> str:
        parts = [e.get("cliente", ""), e.get("titulo", ""), e.get("resumen", "")]
        parts += e.get("temas", []) + e.get("acuerdos", []) + e.get("proximos_pasos", []) + e.get("etiquetas", [])
        return " ".join(parts).lower()

    scored = []
    for e in entries:
        h = haystack(e)
        score = sum(h.count(t) for t in terms)
        if all(t in h for t in terms):
            score += 5  # bonus si contiene todos los términos
        if score:
            scored.append((score, e))
    scored.sort(key=lambda x: (x[0], x[1].get("fecha", "")), reverse=True)

    if not scored:
        print(f"Sin resultados para «{query}».")
        return 0

    print(_c(f"\n{len(scored)} resultado(s) para «{query}»:\n", "1"))
    for score, e in scored[:25]:
        print(_c(f"  {e.get('fecha','')}  ·  {e.get('cliente','?')}", "36") + _c(f"  — {e.get('titulo','')}", "1"))
        if e.get("resumen"):
            print(f"      {e['resumen'][:160]}")
        print(_c(f"      → {cfg['notes_dir'] / e['nota']}", "2"))
        print()
    return 0


def cmd_lista(args):
    cfg = ntconfig.load_config()
    cliente_filtro = None
    if "--cliente" in args:
        i = args.index("--cliente")
        cliente_filtro = " ".join(args[i + 1:]).lower()
    entries = _load_index(cfg["notes_dir"])
    if cliente_filtro:
        entries = [e for e in entries if cliente_filtro in e.get("cliente", "").lower()]
    if not entries:
        print("(sin llamadas)")
        return 0
    print(_c(f"\n{len(entries)} llamada(s):\n", "1"))
    for e in entries:
        pot = " 🌱" if e.get("potencial") else ""
        print(_c(f"  {e.get('fecha','')}  {e.get('cliente','?')}{pot}", "36") + f"  — {e.get('titulo','')}")
    print(f"\nÍndice completo: {cfg['notes_dir'] / 'INDICE.md'}")
    return 0


def cmd_reclasificar(args):
    """Mueve la nota de una sesión a la carpeta de otro cliente y actualiza índice."""
    cfg = ntconfig.load_config()
    if len(args) < 2:
        raise SystemExit("uso: notetaker reclasificar <sesion> <Nombre Cliente>")
    sesion = args[0]
    nuevo_cliente = " ".join(args[1:])
    notes_dir = cfg["notes_dir"]

    entries = _load_index(notes_dir)
    match = next((e for e in entries if e.get("sesion") == sesion or Path(e.get("nota", "")).stem.startswith(sesion)), None)
    if not match:
        raise SystemExit(f"No encuentro una nota para la sesión «{sesion}» en el índice.")

    old_path = notes_dir / match["nota"]
    if not old_path.exists():
        raise SystemExit(f"La nota {old_path} no existe en disco.")

    import archive as A  # noqa: E402
    new_folder = notes_dir / A._safe_folder(nuevo_cliente)
    new_folder.mkdir(parents=True, exist_ok=True)
    new_path = new_folder / old_path.name
    n = 2
    while new_path.exists() and new_path != old_path:
        new_path = new_folder / f"{old_path.stem}-{n}.md"
        n += 1

    # Actualiza el frontmatter (cliente + confianza alta) en el propio archivo.
    text = old_path.read_text()
    text = re.sub(r"^cliente: .*$", f"cliente: {nuevo_cliente}", text, count=1, flags=re.M)
    text = re.sub(r"^confianza_cliente: .*$", "confianza_cliente: alta (manual)", text, count=1, flags=re.M)
    text = re.sub(r"^(\*\*Cliente:\*\* ).*?( \s*·)", rf"\g<1>{nuevo_cliente}\g<2>", text, count=1, flags=re.M)
    new_path.write_text(text)
    if new_path != old_path:
        old_path.unlink()

    # Actualiza el índice.
    match["cliente"] = nuevo_cliente
    match["confianza"] = "alta (manual)"
    match["nota"] = str(new_path.relative_to(notes_dir))
    _rewrite_index(notes_dir, entries)

    print(_c(f"✓ Reclasificada: {sesion} → {nuevo_cliente}", "32"))
    print(f"  {new_path}")
    return 0


def _rewrite_index(notes_dir: Path, entries: list[dict]):
    import archive as A  # noqa: E402
    entries.sort(key=lambda e: (e.get("fecha", ""), e.get("sesion", "")), reverse=True)
    (notes_dir / "index.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n"
    )
    A._render_indice_md(notes_dir, entries)


def cmd_reprocesar(args):
    cfg = ntconfig.load_config()
    if not args:
        raise SystemExit("uso: notetaker reprocesar <sesion>")
    session = _resolve_session(args[0], cfg["recordings_dir"])
    done = session / ".notetaker_done"
    if done.exists():
        done.unlink()
    return _run_process(session)


def cmd_procesar(args):
    cfg = ntconfig.load_config()
    if not args:
        raise SystemExit("uso: notetaker procesar <sesion>")
    session = _resolve_session(args[0], cfg["recordings_dir"])
    return _run_process(session)


def cmd_pendientes(args):
    cfg = ntconfig.load_config()
    root = cfg["recordings_dir"]
    if not root.exists():
        print(f"(no existe {root} todavía)")
        return 0
    pend = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        if (d / "meta.json").exists() and not (d / ".notetaker_done").exists() and not (d / ".notetaker_skip").exists():
            pend.append(d)
    if not pend:
        print("Sin sesiones pendientes. ✓")
        return 0
    print(f"{len(pend)} sesión(es) pendiente(s). Procesando…\n")
    rc = 0
    for d in pend:
        print(_c(f"── {d.name} ──", "1"))
        rc |= _run_process(d)
    return rc


def _run_process(session: Path) -> int:
    import process_session  # noqa: E402
    return process_session.process(session)


def cmd_audio(args):
    """Mide el nivel de audio (dB) de ambas pistas de una sesión.

    Sirve para confirmar rápido que se capturó tanto tu voz (mic) como el
    otro lado de la llamada (system). Silencio digital ≈ permiso faltante o
    nada sonando por el Mac.
    """
    import math
    cfg = ntconfig.load_config()
    if not args:
        # sin argumento: la sesión más reciente
        root = cfg["recordings_dir"]
        dirs = sorted([d for d in root.glob("*") if d.is_dir()], reverse=True)
        if not dirs:
            raise SystemExit("no hay grabaciones en " + str(root))
        session = dirs[0]
    else:
        session = _resolve_session(args[0], cfg["recordings_dir"])

    try:
        import av
        import numpy as np
    except ImportError:
        raise SystemExit("falta PyAV/numpy en este Python")

    print(_c(f"\nNiveles de audio — {session.name}\n", "1"))
    labels = {"mic.caf": "Tú (mic)", "system.caf": "Cliente (system)"}
    for fname, label in labels.items():
        f = session / fname
        if not f.exists():
            print(f"  {label:20s} — (sin archivo)")
            continue
        try:
            c = av.open(str(f))
            chunks = [fr.to_ndarray().flatten() for fr in c.decode(audio=0)]
            c.close()
            if not chunks:
                print(f"  {label:20s} — sin frames")
                continue
            d = np.concatenate(chunks).astype(np.float64)
            if d.max() > 1.5:
                d = d / 32768.0
            rms = float(np.sqrt(np.mean(d ** 2)))
            db = 20 * math.log10(rms) if rms > 0 else -999
            if db > -55:
                verdict = _c("✓ con audio", "32")
            elif db > -75:
                verdict = _c("⚠ muy bajo", "33")
            else:
                verdict = _c("✗ SILENCIO", "31")
            print(f"  {label:20s} {db:6.1f} dB   {verdict}")
        except Exception as e:
            print(f"  {label:20s} — error: {e}")
    print()
    return 0


def cmd_estado(args):
    import json as _json
    import subprocess as _sp
    cfg = ntconfig.load_config()
    yo = ntconfig.persona()
    print(_c("\nQuill — estado\n", "1"))

    def ok(b): return _c("✓", "32") if b else _c("✗", "31")

    print(f"  {ok(bool(yo.get('email')))} dueño: {yo['nombre']} <{yo.get('email') or 'sin correo'}> · {yo['rol']}")
    key = ntconfig.gemini_api_key()
    print(f"  {ok(bool(key))} llave de Gemini {'guardada' if key else 'FALTA (~/.notetaker/.env)'}")

    quill = shutil.which("quill") or str(Path.home() / ".local/bin/quill")
    corre = _sp.run(["pgrep", "-f", "quill run"], capture_output=True).returncode == 0
    print(f"  {ok(Path(quill).exists())} pluma instalada ({quill})")
    print(f"  {ok(corre)} pluma {'corriendo' if corre else 'DETENIDA: vuelve a correr el instalador'}")

    qcfg = Path.home() / ".config/quill/config.json"
    auto = "?"
    try:
        auto = _json.loads(qcfg.read_text()).get("auto_start", "off")
    except Exception:
        pass
    print(f"  {ok(qcfg.exists())} config de la pluma · arranque automático: {auto}")

    ag = ntconfig.NT_DIR / "agenda.json"
    try:
        a = _json.loads(ag.read_text())
        print(f"  {ok(a.get('ok'))} calendario: {len(a.get('eventos', []))} eventos (leído {a.get('generado', a.get('intento', '?'))})"
              + ("" if a.get("ok") else f" · error: {a.get('error')}"))
    except Exception:
        print(f"  {ok(False)} calendario sin conectar (falta CALENDAR_ICS_URL en ~/.notetaker/.env)")

    n_cli = len(ntconfig.load_clientes())
    print(f"  {ok(True)} {n_cli} clientes en la lista (opcional: sin lista se usa el dominio del correo)")

    entries = _load_index(cfg["notes_dir"])
    print(f"  {ok(cfg['notes_dir'].exists())} {len(entries)} nota(s) en {cfg['notes_dir']}")
    recs = sorted([d for d in cfg["recordings_dir"].glob("20*") if d.is_dir()]) if cfg["recordings_dir"].exists() else []
    if recs:
        last = recs[-1]
        estado = "nota lista" if (last / ".notetaker_done").exists() else "sin nota (mira notetaker.log)"
        print(f"  {ok((last / '.notetaker_done').exists())} última grabación: {last.name} · {estado}")
    print()
    return 0


COMMANDS = {
    "buscar": cmd_buscar,
    "lista": cmd_lista,
    "reclasificar": cmd_reclasificar,
    "reprocesar": cmd_reprocesar,
    "procesar": cmd_procesar,
    "pendientes": cmd_pendientes,
    "audio": cmd_audio,
    "estado": cmd_estado,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = sys.argv[1]
    fn = COMMANDS.get(cmd)
    if not fn:
        print(f"comando desconocido: {cmd}\n", file=sys.stderr)
        print(__doc__)
        return 1
    return fn(sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
