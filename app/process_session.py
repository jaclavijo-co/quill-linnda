#!/usr/bin/env python3
"""Procesa una sesión de Quill: transcribe (es) -> clasifica cliente + resume
(Gemini) -> archiva por cliente + índice buscable.

Invocado por el hook on_stop de Quill con la carpeta de sesión como argumento:
    process_session.py ~/Recordings/2026.08.03-1430

También sirve para reprocesar a mano una sesión existente. Es idempotente:
vuelve a escribir la nota y actualiza (no duplica) la entrada del índice.

Escribe un log en <sesion>/notetaker.log y notifica al terminar.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import ntconfig  # noqa: E402
import transcribe as T  # noqa: E402
import summarize as S  # noqa: E402
import archive as A  # noqa: E402


def notify(title: str, msg: str):
    """Notificación nativa de macOS (best-effort)."""
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification {json.dumps(msg)} with title {json.dumps(title)}'],
            check=False, capture_output=True, timeout=10,
        )
    except Exception:
        pass


def _fmt_dur(seconds) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return ""
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}h {m}m" if h else f"{m}m {sec}s"


def _session_meta(session_dir: Path) -> dict:
    meta = {}
    mp = session_dir / "meta.json"
    if mp.exists():
        try:
            meta = json.loads(mp.read_text())
        except Exception:
            pass
    started = meta.get("started", "")
    fecha = started[:10] if started else session_dir.name[:10].replace(".", "-")
    hora = ""
    if started:
        try:
            from datetime import timezone
            dt = datetime.fromisoformat(started.replace("Z", "+00:00")).astimezone()
            fecha, hora = dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
        except Exception:
            pass
    return {"fecha": fecha, "hora": hora, "duracion": _fmt_dur(meta.get("duration_seconds")),
            "segundos": meta.get("duration_seconds") or 0}


def process(session_dir: Path) -> int:
    cfg = ntconfig.load_config()
    log_path = session_dir / "notetaker.log"

    def log(msg: str):
        line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
        print(line)
        with log_path.open("a") as f:
            f.write(line + "\n")

    log(f"=== notetaker: procesando {session_dir.name} ===")

    if not session_dir.exists():
        log(f"ERROR: la carpeta {session_dir} no existe")
        return 2

    meta = _session_meta(session_dir)

    # 0) Grabaciones absurdamente largas (alguien olvidó parar) no se procesan
    #    solas: costarían mucho y casi todo sería silencio. Se marcan y se avisa.
    max_h = float(cfg.get("max_hours_process", 5))
    if meta["segundos"] > max_h * 3600 and "--forzar" not in sys.argv:
        log(f"Grabación de {meta['duracion']} (> {max_h:g} h): no se procesa sola. "
            f"Usa 'notetaker procesar {session_dir.name} --forzar' si de verdad la quieres.")
        (session_dir / ".notetaker_skip").write_text(f"duracion {meta['duracion']}\n")
        notify("Notetaker — grabación muy larga",
               f"{session_dir.name} duró {meta['duracion']}; no la procesé.")
        return 0

    # 0b) Reunión del calendario (antes de transcribir: sus invitados ayudan a
    #     ponerle nombre a cada voz).
    asistentes = []
    if "--sin-contexto" not in sys.argv:
        try:
            import contexto as CX
            ev0 = CX.reunion(session_dir, log)
            for a in (ev0 or {}).get("asistentes", []):
                dom = a["email"].split("@")[-1]
                empresa = "Linnda" if dom == "linnda.co" else dom
                nota = {"DECLINED": " — rechazó la invitación", "ACCEPTED": " — aceptó",
                        "TENTATIVE": " — tal vez", "NEEDS-ACTION": " — no respondió la invitación"}.get(a.get("estado", ""), "")
                if a.get("opcional"):
                    nota += " (invitado opcional)"
                yo = ntconfig.persona()
                nombre = yo["nombre"] if a["email"] == (yo.get("email") or "").lower() else a["nombre"]
                asistentes.append(f"{nombre} ({empresa}){nota}")
            cli0 = (ev0 or {}).get("cliente")
            if cli0 and cli0 != "Linnda Interno":
                for c in CX.contactos(cli0):
                    asistentes.append(f"{c} ({cli0}) — contacto conocido del cliente según Linnda Conocimiento (puede usar otro correo)")
        except Exception as e:
            log(f"  (sin calendario: {e})")

    # 1) Transcripción (local, español). Con --solo-resumen se reutiliza la
    #    transcripción existente y solo se vuelve a armar la nota.
    prev_tr = session_dir / "transcript_es.txt"
    if "--solo-resumen" in sys.argv and prev_tr.exists() and prev_tr.read_text().strip():
        dialogo = prev_tr.read_text()
        log("  reutilizando transcript_es.txt (--solo-resumen)")
        segments = None
    else:
        dialogo = None
    crudos = session_dir / "segmentos_crudos.json"
    if dialogo is None and "--reunificar" in sys.argv and crudos.exists():
        # rehace solo los nombres de las voces, sin transcribir otra vez
        import hablantes
        segs0 = json.loads(crudos.read_text())
        segs0 = hablantes.unificar(segs0, asistentes, ntconfig.gemini_api_key(),
                                   cfg.get("gemini_models") or cfg["gemini_model"], log=log)
        dialogo = T.dialogo_texto(segs0)
        prev_tr.write_text(dialogo)
        log("  voces re-unificadas desde segmentos_crudos.json")
    try:
        segments = T.transcribe_session(session_dir, cfg, log=log, asistentes=asistentes) if dialogo is None else None
    except Exception as e:
        log(f"ERROR en transcripción: {e}")
        notify("Notetaker — error", f"Falló la transcripción de {session_dir.name}")
        return 3

    if dialogo is None and not segments:
        log("Sin segmentos transcribibles (¿grabación vacía o muy corta?). Nada que archivar.")
        (session_dir / ".notetaker_done").write_text("vacía: sin audio transcribible\n")
        notify("Notetaker", f"{session_dir.name}: sin audio transcribible")
        return 0

    if dialogo is None:
        dialogo = T.dialogo_texto(segments)
        (session_dir / "transcript_es.txt").write_text(dialogo)
        log(f"  transcripción guardada: transcript_es.txt ({len(segments)} segmentos)")

    # 2) Clasificación de cliente + resumen (Gemini). Si no hay key o falla,
    #    archivamos igual con un análisis mínimo — nunca perdemos la llamada.
    api_key = ntconfig.gemini_api_key()
    clientes = ntconfig.load_clientes()
    if api_key:
        try:
            models = cfg.get("gemini_models") or cfg["gemini_model"]
            fast = cfg.get("gemini_transcribe_models") or models
            # Contexto: reunión del calendario + memoria del cliente (Linnda Conocimiento,
            # última llamada y tareas abiertas). --sin-contexto lo apaga.
            usar_ctx = "--sin-contexto" not in sys.argv
            ev, ctx_reunion, ctx_cliente = None, "", ""
            try:
                import contexto as CX
                ev = CX.reunion(session_dir, log) if usar_ctx else None
                ctx_reunion = CX.texto_reunion(ev) if ev else ""
                if ev:
                    meta["evento"] = ev.get("titulo", "")
                    log(f"  reunión del calendario: {ev.get('titulo')} · cliente: {ev.get('cliente') or '?'}")
                cliente_ctx = (ev or {}).get("cliente") or ""
                if usar_ctx and not cliente_ctx:
                    idc = S.identificar_cliente(dialogo, clientes, api_key, fast, log=log)
                    if idc.get("confianza") != "baja" and not str(idc.get("cliente", "")).lower().startswith(("nuevo", "desconocido")):
                        cliente_ctx = idc["cliente"]
                    log(f"  cliente (primera pasada): {idc.get('cliente')} ({idc.get('confianza')})")
                if usar_ctx and cliente_ctx and cliente_ctx != "Linnda Interno":
                    ctx_cliente = CX.bloque_cliente(cfg["notes_dir"], cliente_ctx, session_dir.name, meta["fecha"])
                    log(f"  contexto del cliente: {len(ctx_cliente)} caracteres")
            except Exception as e:  # el contexto nunca debe tumbar la nota
                log(f"  (sin contexto: {e})")
            log("  analizando con Gemini …")
            analisis = S.analizar(dialogo, clientes, meta, api_key, models, log=log,
                                  ctx_reunion=ctx_reunion, ctx_cliente=ctx_cliente)
            log(f"  modelo usado: {analisis.get('_modelo')}")
            cal_cli = (ev or {}).get("cliente")
            if cal_cli and cal_cli != "Linnda Interno" and analisis.get("cliente") != cal_cli:
                log(f"  cliente ajustado por calendario: {analisis.get('cliente')} → {cal_cli}")
                analisis["cliente"], analisis["cliente_confianza"] = cal_cli, "alta"
            log(f"  cliente inferido: {analisis.get('cliente')} (confianza {analisis.get('cliente_confianza')})")
        except Exception as e:
            log(f"ADVERTENCIA: Gemini falló ({e}). Archivo sin resumen.")
            analisis = _fallback_analisis(session_dir, meta)
    else:
        log("ADVERTENCIA: sin GEMINI_API_KEY. Archivo sin resumen.")
        analisis = _fallback_analisis(session_dir, meta)

    # 3) Archivar + índice
    try:
        dest = A.archivar(cfg["notes_dir"], analisis, meta, dialogo, session_dir.name, log=log)
    except Exception as e:
        log(f"ERROR al archivar: {e}")
        notify("Notetaker — error", f"Falló el archivado de {session_dir.name}")
        return 4

    # 4) Marca para no reprocesar en barridos automáticos
    (session_dir / ".notetaker_done").write_text(str(dest) + "\n")

    log(f"=== listo: {dest} ===")
    _avisar_telegram(session_dir, analisis, dest, cfg)
    notify(
        "Notetaker — nota lista",
        f"{analisis.get('cliente','?')}: {analisis.get('titulo', session_dir.name)}",
    )
    return 0


def _avisar_telegram(session_dir: Path, analisis: dict, dest: Path, cfg: dict):
    """Para grabaciones del teléfono (o todas, según config): deja el resumen en
    el outbox del bot de Telegram (config "telegram_outbox"), que lo reenvía al dueño."""
    try:
        meta = json.loads((session_dir / "meta.json").read_text())
    except Exception:
        meta = {}
    modo = cfg.get("telegram_resumen", "telefono")  # "telefono" | "todas" | "ninguna"
    if modo == "ninguna" or (modo == "telefono" and meta.get("origen") not in ("telegram", "notas_de_voz", "drive")):
        return
    if not cfg.get("telegram_outbox"):
        return
    outbox = Path(os.path.expanduser(cfg["telegram_outbox"]))
    if not outbox.exists():
        return
    li = lambda xs, n=6: "\n".join(f"- {x}" for x in (xs or [])[:n]) or "- (ninguno)"
    rel = dest.relative_to(cfg["notes_dir"]) if str(dest).startswith(str(cfg["notes_dir"])) else dest.name
    texto = (f"Nota lista: {analisis.get('titulo', session_dir.name)}\n"
             f"Cliente: {analisis.get('cliente', '?')} · {meta.get('duration_seconds', 0) // 60} min\n\n"
             f"{analisis.get('resumen', '')}\n\n"
             f"Acuerdos:\n{li(analisis.get('acuerdos'))}\n\n"
             f"Próximos pasos:\n{li(analisis.get('proximos_pasos'))}\n\n"
             f"Quedó en Notas-Llamadas/{rel}. Las tareas entran al backlog en la bandeja de la mañana.")
    (outbox / f"{datetime.now():%Y-%m-%d-%H%M%S}-nota-{session_dir.name}.md").write_text(texto)


def _fallback_analisis(session_dir: Path, meta: dict) -> dict:
    return {
        "cliente": "Nuevo/Desconocido",
        "cliente_confianza": "baja",
        "es_potencial": False,
        "titulo": f"Llamada {session_dir.name}",
        "resumen": "(Sin resumen: Gemini no disponible al procesar. Usa 'notetaker reprocesar' para generarlo.)",
        "participantes": [],
        "temas": [],
        "acuerdos": [],
        "proximos_pasos": [],
        "datos_clave": [],
        "etiquetas": ["sin-resumen"],
    }


def main():
    if len(sys.argv) < 2:
        print("uso: process_session.py <carpeta_de_sesion>", file=sys.stderr)
        return 1
    return process(Path(sys.argv[1]).expanduser().resolve())


if __name__ == "__main__":
    sys.exit(main())
