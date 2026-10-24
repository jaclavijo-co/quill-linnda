"""Unifica los nombres de las voces en toda la transcripción.

Cada trozo de 10 min se transcribe por separado, así que "Voz 1" del trozo 0
no tiene por qué ser la misma persona que "Voz 1" del trozo 3. Aquí una sola
llamada a Gemini (solo texto) lee la transcripción completa con rótulos por
trozo y devuelve a quién corresponde cada rótulo, usando los invitados del
calendario y lo que se dice en la conversación (nombres, saludos, roles).
"""
from __future__ import annotations

import json

import gemini_api as GA
import ntconfig


def _clave(s: dict) -> str | None:
    if s.get("track") in ("Otros", "Sala"):
        return f"t{0 if s['track'] == 'Otros' else 1}c{s.get('chunk', 0)}|{s['speaker']}"
    return None


def _clock(ms: int) -> str:
    t = ms // 1000
    return f"{t // 3600}:{(t % 3600) // 60:02d}:{t % 60:02d}"


def unificar(segs: list[dict], asistentes: list[str], api_key: str, models, log=print) -> list[dict]:
    claves = sorted({k for s in segs if (k := _clave(s))})
    if not claves:
        return segs
    yo = ntconfig.persona()
    et = yo["etiqueta"]
    lineas = []
    for s in segs:
        k = _clave(s)
        quien = f"[{k}]" if k else f"[{et} · micrófono]"
        lineas.append(f"{_clock(s['start_ms'])} {quien} {s['text'][:400]}")
    texto = "\n".join(lineas)
    if len(texto) > 120_000:
        texto = texto[:120_000] + "\n…"
    invitados = "\n".join(f"- {a}" for a in asistentes) or "- (no hay reunión en el calendario)"
    prompt = f"""Esta es la transcripción de una reunión de {yo['org']}, grabada por {yo['nombre']}. Cada línea tiene un rótulo de quién habla.
- "[{et} · micrófono]" es siempre {yo['corto']}.
- Los demás rótulos tienen la forma tN cM|Rótulo: un rótulo provisional que se puso en el trozo M (trozos de 10 min, transcritos por separado). El mismo rótulo en trozos distintos PUEDE ser otra persona; y la misma persona puede tener rótulos distintos en trozos distintos.

Invitados según el calendario (no todos asisten; quien rechazó probablemente no estuvo):
{invitados}

Tarea: decide quién es cada rótulo, usando nombres que se dicen (saludos, "¿qué opinas, Jorge?", "gracias, Rafa"), presentaciones, roles y temas de cada uno, y la continuidad de la conversación entre trozos.
Devuelve SOLO un JSON {{"<rótulo exacto>": "<Nombre Apellido (Empresa)>"}} con TODOS estos rótulos:
{json.dumps(claves, ensure_ascii=False)}

Reglas:
- Usa el nombre completo del calendario cuando sea la misma persona (p. ej. "Pau" o "Paula" → "Paula Morales (Dicarina)"). Empresa = la del dominio del correo o la que se deduzca (Linnda para @linnda.co).
- Si dos personas tienen el mismo nombre de pila, prefiere al "contacto conocido del cliente" o a quien aceptó; nunca elijas a quien rechazó la invitación si hay otra opción.
- Pistas útiles: quien aceptó la invitación es más probable que esté que quien no respondió o era opcional. Quien conduce la reunión del lado del cliente (saluda, da la palabra, propone horarios) suele ser la misma persona en todos los trozos. Una persona que aparece nombrada por otro ("¿qué opinas, Jorge?") NO es quien está hablando en esa línea. Antes de asignar a alguien poco probable, revisa si el contenido encaja mejor con quien ya identificaste en otros trozos.
- {yo['corto']} siempre se escribe "{et}". En una grabación del teléfono (rótulos t1…) {yo['corto']} también aparece con rótulo: identifícalo así.
- Si no sabes la empresa de alguien, escribe solo el nombre, sin paréntesis (nada de "Empresa sin identificar").
- Si no hay forma razonable de saber quién es, usa "Persona sin identificar". Nunca inventes un nombre que no esté en el calendario ni se diga en la conversación.

TRANSCRIPCIÓN:
{texto}
"""
    mapa = None
    for intento in range(2):
        raw, _ = GA.generate([{"text": prompt}], api_key, models,
                             {"temperature": 0, "maxOutputTokens": 32768, "responseMimeType": "application/json"},
                             timeout_s=300, log=log)
        try:
            ini, fin = raw.find("{"), raw.rfind("}")
            mapa = json.loads(raw[ini:fin + 1])
            break
        except Exception:
            log(f"  (unificación ilegible, intento {intento + 1}: {raw[:120]!r} … {raw[-80:]!r})")
    if not isinstance(mapa, dict):
        return segs
    for s in segs:
        k = _clave(s)
        if k:
            s["speaker"] = mapa.get(k) or s["speaker"]
        elif s.get("track") == "Yo" or s.get("speaker") == "Yo":
            s["speaker"] = et
    nombres = sorted({v for v in mapa.values()})
    log(f"  voces identificadas: {', '.join(nombres)}")
    return segs
