"""Transcribe una sesión de Quill (dos pistas) a un diálogo etiquetado.

Quill escribe en cada carpeta de sesión:
  mic.caf     -> tu voz          (speaker = tu nombre corto)
  system.caf  -> audio del sistema = el otro lado (speaker "Otros")
  meta.json   -> tiempos + start_offset_ms por pista

Cada pista se transcribe por separado (mejor precisión con una sola fuente),
se desplaza por su offset para compartir un mismo reloj, y se fusiona por
timestamp. El modelo se carga una sola vez por proceso (module-level cache).
"""
from __future__ import annotations

import json
from pathlib import Path

_MODEL = None
_MODEL_KEY = None


def _get_model(name: str, compute_type: str):
    """Carga faster-whisper una sola vez. device='auto' usa lo mejor disponible."""
    global _MODEL, _MODEL_KEY
    key = (name, compute_type)
    if _MODEL is not None and _MODEL_KEY == key:
        return _MODEL
    from faster_whisper import WhisperModel
    # 'auto' elige CPU con int8 en Apple Silicon (CTranslate2 no usa Metal aún,
    # pero int8 en CPU va ~1 min por hora de audio en un M-series moderno).
    _MODEL = WhisperModel(name, device="auto", compute_type=compute_type)
    _MODEL_KEY = key
    return _MODEL


def _read_meta(session_dir: Path) -> list[dict]:
    """Devuelve las pistas a transcribir: [{file, speaker, offset_ms}]."""
    meta_path = session_dir / "meta.json"
    files = {"mic": "mic.caf", "system": "system.caf"}
    offsets = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            files = meta.get("files", files)
            offsets = meta.get("start_offset_ms", {})
        except Exception:
            pass
    tracks = []
    if "mic" in files:
        tracks.append({"file": files["mic"], "speaker": "Yo", "offset_ms": offsets.get("mic", 0)})
    if "system" in files:
        tracks.append({"file": files["system"], "speaker": "Otros", "offset_ms": offsets.get("system", 0)})
    if "mono" in files:  # grabación del teléfono: todos en una sola pista
        tracks.append({"file": files["mono"], "speaker": "Sala", "offset_ms": 0})
    return tracks


def transcribe_session(session_dir: Path, cfg: dict, log=print, asistentes=None) -> list[dict]:
    """Transcribe las pistas y devuelve segmentos fusionados y ordenados.

    Cada segmento: {speaker, start_ms, end_ms, text}. `asistentes` (del
    calendario) ayuda a ponerle nombre a cada voz del otro lado.
    """
    engine = cfg.get("transcribe_engine", "gemini")
    if engine == "gemini":
        return _transcribe_gemini(session_dir, cfg, log, asistentes or [])
    return _transcribe_local(session_dir, cfg, log)


# Descripción de cada pista para dar contexto al transcriptor.
def _speaker_desc(track: str) -> str:
    import ntconfig
    p = ntconfig.persona()
    return {
        "Yo": f"{p['nombre']}, {p['rol']} (micrófono de su computador)",
        "Otros": f"todo lo que se oyó del otro lado de la llamada (audio del computador): las demás personas de la reunión, que pueden ser del cliente o del equipo de {p['org']}",
        "Sala": f"una reunión presencial grabada con el teléfono de {p['corto']}: hablan {p['nombre']} ({p['org']}) y las demás personas presentes",
    }.get(track, track)


def _transcribe_gemini(session_dir, cfg, log, asistentes=None) -> list[dict]:
    """Transcribe ambas pistas con Gemini (nube, rápido). Silencio digital se
    omite (una pista muda no gasta llamada)."""
    import ntconfig
    import gemini_transcribe as G

    api_key = ntconfig.gemini_api_key()
    if not api_key:
        log("  sin GEMINI_API_KEY — no puedo transcribir por Gemini; cae a local")
        return _transcribe_local(session_dir, cfg, log)
    model = cfg.get("gemini_transcribe_models") or cfg.get("gemini_transcribe_model", "gemini-2.5-flash")

    merged: list[dict] = []
    for track in _read_meta(session_dir):
        audio = session_dir / track["file"]
        if not audio.exists() or audio.stat().st_size < 5_000:
            log(f"  (pista {track['file']} ausente o vacía — omitida)")
            continue
        if _is_silent(audio):
            log(f"  (pista {track['file']} en silencio digital — omitida)")
            continue
        log(f"  transcribiendo {track['file']} como «{track['speaker']}» (Gemini, por trozos)…")
        desc = _speaker_desc(track["speaker"])
        diarize = track["speaker"] in ("Otros", "Sala") and cfg.get("separar_voces", True)
        yo = ntconfig.persona()
        nombres = [n for n in (asistentes or []) if not (track["speaker"] == "Otros" and n.startswith(yo["nombre"]))]
        try:
            segs = G.transcribe_track(audio, track["speaker"], desc, api_key, model, log=log,
                                      nombres=nombres, diarize=diarize)
        except Exception as e:
            log(f"    ERROR Gemini en {track['file']}: {e} — omito esta pista")
            continue
        offset = track["offset_ms"]
        for s in segs:
            s["start_ms"] += offset
            s["end_ms"] += offset
        merged += segs
        log(f"    {len(segs)} segmentos")

    merged.sort(key=lambda x: x["start_ms"])
    try:  # copia cruda (rótulos por trozo) para poder re-unificar sin transcribir otra vez
        import json as _json
        (session_dir / "segmentos_crudos.json").write_text(_json.dumps(merged, ensure_ascii=False))
    except Exception:
        pass
    if cfg.get("separar_voces", True) and any(m.get("track") in ("Otros", "Sala") for m in merged):
        try:
            import hablantes
            merged = hablantes.unificar(merged, asistentes or [], api_key,
                                        cfg.get("gemini_models") or cfg.get("gemini_model"), log=log)
        except Exception as e:
            log(f"  (no pude unificar nombres de voces: {e})")
    return merged


def _is_silent(audio) -> bool:
    """True si la pista es silencio digital (RMS < -70 dB) — no vale transcribir."""
    import math
    try:
        import av
        import numpy as np
        c = av.open(str(audio))
        chunks = []
        for i, fr in enumerate(c.decode(audio=0)):
            chunks.append(fr.to_ndarray().flatten())
            if i > 2000:  # muestra suficiente
                break
        c.close()
        if not chunks:
            return True
        d = np.concatenate(chunks).astype(np.float64)
        if d.max() > 1.5:
            d = d / 32768.0
        rms = float(np.sqrt(np.mean(d ** 2)))
        db = 20 * math.log10(rms) if rms > 0 else -999
        return db < -70
    except Exception:
        return False  # ante la duda, intentar transcribir


def _transcribe_local(session_dir, cfg, log) -> list[dict]:
    model = _get_model(cfg["whisper_model"], cfg["whisper_compute_type"])
    language = cfg["language"]
    merged: list[dict] = []

    for track in _read_meta(session_dir):
        audio = session_dir / track["file"]
        if not audio.exists() or audio.stat().st_size < 5_000:
            log(f"  (pista {track['file']} ausente o vacía — omitida)")
            continue
        if _is_silent(audio):
            log(f"  (pista {track['file']} en silencio digital — omitida)")
            continue
        log(f"  transcribiendo {track['file']} como «{track['speaker']}» (whisper {cfg['whisper_model']})…")
        segments, info = model.transcribe(
            str(audio),
            language=language,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 200},
            beam_size=cfg.get("whisper_beam_size", 1),
            # Clave anti-alucinación: NO condicionar en el texto previo evita el
            # bucle donde una alucinación ("Gracias", "Suscríbete") se repite.
            condition_on_previous_text=False,
            # Umbrales que descartan segmentos sin habla real o de baja confianza.
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
            # Suprime tokens de silencio/relleno al inicio.
            temperature=[0.0, 0.2, 0.4],
        )
        offset = track["offset_ms"]
        raw = []
        for s in segments:
            text = s.text.strip()
            if not text:
                continue
            raw.append({
                "speaker": track["speaker"],
                "start_ms": int(s.start * 1000) + offset,
                "end_ms": int(s.end * 1000) + offset,
                "text": text,
                "no_speech_prob": getattr(s, "no_speech_prob", 0.0),
            })
        cleaned = _drop_hallucinations(raw)
        merged += cleaned
        log(f"    {len(cleaned)} segmentos" + (f" ({len(raw) - len(cleaned)} descartados)" if len(raw) != len(cleaned) else ""))

    merged.sort(key=lambda x: x["start_ms"])
    return merged


# Frases que Whisper alucina sobre silencio/ruido (subtítulos de YouTube en su
# entrenamiento). Si un segmento es SOLO una de estas, se descarta.
_HALLUCINATION_PHRASES = {
    "gracias", "gracias.", "muchas gracias", "muchas gracias.",
    "gracias por ver el video", "gracias por ver el vídeo",
    "suscríbete", "suscríbete al canal", "subtítulos realizados por la comunidad de amara.org",
    "subtitulado por la comunidad de amara.org", "¡gracias!", "¡suscríbete!",
    "thank you", "thanks for watching", "you", ".", "...", "sí", "sí.",
}


def _norm(t: str) -> str:
    return t.strip().lower().strip(" .!¡¿?")


def _drop_hallucinations(segs: list[dict]) -> list[dict]:
    """Descarta alucinaciones: frases-basura conocidas, alta prob. de no-habla,
    y repeticiones idénticas consecutivas (el bucle 'Gracias'×10)."""
    out: list[dict] = []
    prev_norm = None
    repeat = 0
    for s in segs:
        n = _norm(s["text"])
        # 1) frase de relleno conocida y muy corta -> fuera
        if n in _HALLUCINATION_PHRASES:
            continue
        # 2) alta probabilidad de que no haya habla -> fuera
        if s.get("no_speech_prob", 0.0) > 0.7:
            continue
        # 3) misma frase repetida consecutivamente -> conservar solo la 1ª
        if n == prev_norm:
            repeat += 1
            if repeat >= 1:
                continue
        else:
            repeat = 0
        prev_norm = n
        s.pop("no_speech_prob", None)
        out.append(s)
    return out


def dialogo_texto(segments: list[dict]) -> str:
    """Renderiza los segmentos como diálogo legible con marcas de tiempo."""
    lines = []
    for s in segments:
        lines.append(f"[{_clock(s['start_ms'])}] {s['speaker']}: {s['text']}")
    return "\n".join(lines)


def _clock(ms: int) -> str:
    t = ms // 1000
    h, m, sec = t // 3600, (t % 3600) // 60, t % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
