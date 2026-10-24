"""Transcripción de audio vía Gemini, por trozos.

Cada pista se decodifica a WAV mono 16 kHz, se parte en trozos (10 min por
defecto), se descartan los trozos en silencio y cada trozo se manda inline a
Gemini en paralelo. Así no hay límite de duración (antes una reunión de más de
~1 h 30 fallaba con "audio demasiado grande para inline") y los tiempos de cada
frase son más precisos.

NOTA DE PRIVACIDAD: el audio se envía a la nube de Google para transcribir.
"""
from __future__ import annotations

import base64
import math
import shutil
import subprocess
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gemini_api as GA

SAMPLE_RATE = 16000
MAX_INLINE_BYTES = 18_000_000


class GeminiTranscribeError(Exception):
    pass


def _to_wav(src: Path, dst: Path) -> Path:
    """Decodifica cualquier audio (AAC-in-CAF incluido) a WAV mono 16 kHz."""
    import av

    c = av.open(str(src))
    res = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    out = av.open(str(dst), "w")
    st = out.add_stream("pcm_s16le", rate=SAMPLE_RATE, layout="mono")
    for fr in c.decode(audio=0):
        for rf in res.resample(fr):
            for pkt in st.encode(rf):
                out.mux(pkt)
    for pkt in st.encode(None):
        out.mux(pkt)
    out.close()
    c.close()
    return dst


def _rms_db(raw: bytes) -> float:
    import numpy as np
    if not raw:
        return -999.0
    d = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    rms = float(np.sqrt(np.mean(d ** 2))) if d.size else 0.0
    return 20 * math.log10(rms) if rms > 0 else -999.0


def _split(wav: Path, workdir: Path, chunk_s: int, silence_db: float):
    """Parte el WAV en trozos. Devuelve [(offset_ms, wav_trozo)] sin los silenciosos."""
    chunks = []
    with wave.open(str(wav), "rb") as w:
        frames_per_chunk = chunk_s * w.getframerate()
        i = 0
        while True:
            raw = w.readframes(frames_per_chunk)
            if not raw:
                break
            offset_ms = i * chunk_s * 1000
            if _rms_db(raw) > silence_db:
                p = workdir / f"chunk_{i:04d}.wav"
                with wave.open(str(p), "wb") as o:
                    o.setnchannels(1)
                    o.setsampwidth(2)
                    o.setframerate(SAMPLE_RATE)
                    o.writeframes(raw)
                chunks.append((offset_ms, p))
            i += 1
    return chunks, i


def _to_m4a(wav: Path) -> Path:
    dst = wav.with_suffix(".m4a")
    subprocess.run(
        ["afconvert", "-f", "m4af", "-d", "aac", "-b", "32000", str(wav), str(dst)],
        check=True, capture_output=True,
    )
    return dst


_PROMPT = """Transcribe este audio en español, palabra por palabra y de forma fiel.

Contexto: es un fragmento de UNA de las dos pistas de una llamada de negocios de Linnda (empresa de tecnología y ventas para retail). Esta pista corresponde a: {speaker_desc}.

Reglas:
- Devuelve SOLO la transcripción, sin comentarios tuyos.
- Formato: una línea por intervención, con marca de tiempo desde el inicio de ESTE fragmento: [mm:ss] texto
- Agrupa el habla en frases naturales; NO pongas una línea por cada palabra.
- OMITE ruido, silencios, muletillas aisladas ("ah", "mmm", "eh") y sonidos no verbales. Si un tramo no tiene habla real, sáltalo.
- No inventes contenido: si no hay habla clara, no escribas nada para ese tramo. Si todo el fragmento es silencio o ruido, devuelve una respuesta vacía.
- Si se oye a otra persona en el fondo (eco de parlantes), transcribe solo a la voz principal.
- Conserva nombres propios, cifras, marcas y términos de negocio tal como se oyen (en inglés si los dicen en inglés).
"""


_PROMPT_VOCES = """

Varias personas pueden hablar en esta pista. Empieza CADA línea con quién habla:
[mm:ss] Nombre: texto
{pistas_nombres}- Usa un nombre solo si lo deduces del audio: alguien lo llama por su nombre, se presenta, o es claramente la misma voz que ya identificaste en este fragmento. Si no, usa "Voz 1", "Voz 2", … y mantén el mismo rótulo para la misma voz en todo el fragmento.
- No inventes nombres que no estén en la lista ni se digan en el audio.
- Escribe cada intervención UNA sola vez. Nada de comentarios, dudas, verificaciones ni notas tuyas (ni en español ni en inglés): solo líneas de transcripción.
"""


def _prompt(speaker_desc: str, nombres: list[str] | None, diarize: bool) -> str:
    base = _PROMPT.format(speaker_desc=speaker_desc)
    if not diarize:
        return base
    base = base.replace("- Si se oye a otra persona en el fondo (eco de parlantes), transcribe solo a la voz principal.\n", "")
    pistas = ""
    if nombres:
        pistas = "- Personas que pueden estar en la reunión (según el calendario): " + "; ".join(nombres) + ".\n"
    return base + _PROMPT_VOCES.format(pistas_nombres=pistas)


def _transcribe_chunk(m4a: Path, speaker: str, speaker_desc: str, api_key: str, models,
                      nombres=None, diarize=False) -> list[dict]:
    if m4a.stat().st_size > MAX_INLINE_BYTES:
        raise GeminiTranscribeError(f"trozo demasiado grande ({m4a.stat().st_size/1e6:.0f} MB)")
    parts = [
        {"text": _prompt(speaker_desc, nombres, diarize)},
        {"inline_data": {"mime_type": "audio/mp4", "data": base64.b64encode(m4a.read_bytes()).decode()}},
    ]
    try:
        text, _ = GA.generate(parts, api_key, models,
                              {"temperature": 0.1, "maxOutputTokens": 32768}, timeout_s=300)
    except GA.GeminiError as e:
        if "respuesta vacía" in str(e):
            return []
        raise
    return _parse_lines(text, speaker, diarize)


def transcribe_track(audio: Path, speaker: str, speaker_desc: str,
                     api_key: str, model, log=print,
                     chunk_seconds: int = 600, workers: int = 4,
                     silence_db: float = -60.0, nombres=None, diarize=False) -> list[dict]:
    """Transcribe una pista. Devuelve segmentos [{speaker,start_ms,end_ms,text}]
    con tiempos relativos al inicio de la pista."""
    workdir = Path(tempfile.mkdtemp(prefix="nt_"))
    try:
        log(f"  decodificando {audio.name}…")
        wav = _to_wav(audio, workdir / "full.wav")
        chunks, total = _split(wav, workdir, chunk_seconds, silence_db)
        wav.unlink(missing_ok=True)
        log(f"  {audio.name}: {total} trozos de {chunk_seconds//60} min, {len(chunks)} con audio")
        if not chunks:
            return []

        def work(item):
            offset_ms, cw = item
            m4a = _to_m4a(cw)
            cw.unlink(missing_ok=True)
            segs = _transcribe_chunk(m4a, speaker, speaker_desc, api_key, model, nombres, diarize)
            if not segs and diarize:
                # a veces el modelo devuelve vacío al separar voces: reintento y, si no, sin separar
                segs = _transcribe_chunk(m4a, speaker, speaker_desc, api_key, model, nombres, diarize)
                if not segs:
                    log(f"    trozo de {offset_ms // 60000} min vacío separando voces; lo transcribo sin separar")
                    segs = _transcribe_chunk(m4a, speaker, speaker_desc, api_key, model, nombres, False)
            for s in segs:
                s["start_ms"] += offset_ms
                s["end_ms"] += offset_ms
                s["chunk"] = offset_ms // (chunk_seconds * 1000)
            return offset_ms, segs

        out: list[dict] = []
        errors = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(work, c) for c in chunks]
            for f, (offset_ms, _) in zip(futs, chunks):
                try:
                    _, segs = f.result()
                    out += segs
                except Exception as e:  # un trozo fallido no tumba la llamada
                    errors.append(f"{offset_ms//60000} min: {e}")
        for e in errors:
            log(f"    ERROR en trozo {e}")
        if errors and not out:
            raise GeminiTranscribeError(f"fallaron todos los trozos ({errors[0]})")
        out.sort(key=lambda s: s["start_ms"])
        return out
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


import re as _re

# Restos de "pensar en voz alta" del modelo que a veces se cuelan en la salida.
_META = _re.compile(r"(\b(wait|let'?s (listen|check|combine|see)|let me|i need to|checking|does (he|she|they) say|"
                    r"re-?listen|listen(ing)? to|nothing else|combine)\b.*$)", _re.I | _re.S)
_TS_SUELTO = _re.compile(r"\s*\(?\b\d{1,2}:\d{2}(:\d{2})?\)?\s*(-\s*\d{1,2}:\d{2})?:?\s*$")


def _limpiar(txt: str) -> str:
    txt = _META.sub("", txt).strip()
    prev = None
    while prev != txt:  # marcas de tiempo sueltas al final ("… 01:33:")
        prev, txt = txt, _TS_SUELTO.sub("", txt).strip()
    return txt.strip(' "')


def _dedupe(segs: list[dict]) -> list[dict]:
    out = []
    for s in segs:
        if out:
            p = out[-1]
            same_who = p["speaker"] == s["speaker"]
            near = abs(s["start_ms"] - p["start_ms"]) <= 15000
            if same_who and near and (s["text"] == p["text"] or p["text"].startswith(s["text"])):
                continue
            if same_who and near and s["text"].startswith(p["text"]):
                out[-1] = s  # versión más completa de la misma frase
                continue
        out.append(s)
    return out


def _parse_lines(text: str, speaker: str, diarize: bool = False) -> list[dict]:
    """Parsea líneas '[mm:ss] texto' o '[hh:mm:ss] texto' a segmentos.
    Con diarize, '[mm:ss] Nombre: texto' -> speaker = Nombre (rótulo crudo)."""
    import re
    segs = []
    pat = re.compile(r"^\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s*(.+)$")
    lab = re.compile(r"^\**([^:\[\]*]{1,40}?)\**:\s*(.+)$")
    for line in text.splitlines():
        line = line.strip().strip("`")
        if not line:
            continue
        m = pat.match(line)
        if m:
            a, b, c, txt = m.groups()
            start = ((int(a) * 3600 + int(b) * 60 + int(c)) if c else (int(a) * 60 + int(b))) * 1000
            txt = txt.strip()
            who = speaker
            if diarize:
                ml = lab.match(txt)
                if ml:
                    who, txt = ml.group(1).strip(), ml.group(2).strip().lstrip("*").strip()
            txt = _limpiar(txt)
            if txt:
                segs.append({"speaker": who, "start_ms": start, "end_ms": start, "text": txt,
                             "track": speaker})
        elif segs:
            extra = _limpiar(line)
            if extra and extra == line.strip(' "'):
                segs[-1]["text"] += " " + extra
    return _dedupe(segs)
