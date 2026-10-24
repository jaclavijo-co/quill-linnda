"""Clasificación de cliente + notas estructuradas vía Gemini REST.

Una sola llamada devuelve JSON con: cliente (o empresa del prospecto nuevo),
título, resumen, detalles por tema, acuerdos, próximos pasos, preguntas
abiertas, datos clave, participantes y etiquetas.
"""
from __future__ import annotations

import json

import gemini_api as GA

GeminiError = GA.GeminiError


def _build_prompt(dialogo: str, clientes: list[dict], meta: dict,
                  ctx_reunion: str = "", ctx_cliente: str = "") -> str:
    import ntconfig
    yo = ntconfig.persona()
    lista = "\n".join(
        f"  - {c['nombre']}" + (" [prospecto]" if c.get("prospecto") else "")
        + (f"  (alias: {', '.join(c.get('aliases', []))})" if c.get("aliases") else "")
        for c in clientes
    )
    return f"""Eres quien toma las notas de reuniones de Linnda, una empresa colombiana que construye un "OS de ventas para retail" y opera proyectos de ecommerce, performance (Meta, Google y Amazon Ads), datos y producto para marcas y retailers en LatAm. Analiza la transcripción y devuelve SOLO un objeto JSON válido (sin markdown), con esta estructura exacta:

{{
  "cliente": "<nombre EXACTO de la lista de clientes conocidos, o 'Nuevo/Desconocido' si no calza ninguno>",
  "cliente_confianza": "<alta|media|baja>",
  "es_potencial": <true si es un prospecto/lead (empresa con la que aún no hay proyecto), false si es cliente activo o reunión interna>,
  "empresa_nueva": "<si cliente es 'Nuevo/Desconocido': nombre de la empresa o marca de la otra parte tal como se dice en la llamada (p. ej. 'Alpina', 'Grupo Éxito'); si es reunión interna de Linnda usa 'Linnda Interno'; si no se puede saber, cadena vacía>",
  "titulo": "<título corto y descriptivo, máx 8 palabras>",
  "resumen": "<3-5 frases: de qué se trató, qué se decidió y en qué quedó>",
  "participantes": ["<nombre (empresa) — rol si se menciona>"],
  "detalles": [{{"tema": "<tema>", "detalle": "<3-6 frases: qué se planteó, quién lo dijo, cifras exactas, objeciones y en qué quedó>"}}],
  "acuerdos": ["<decisiones o acuerdos concretos>"],
  "proximos_pasos": ["<acción — responsable (persona y empresa) — fecha si se mencionó>"],
  "preguntas_abiertas": ["<temas que quedaron sin resolver o pendientes de confirmar>"],
  "datos_clave": ["<cifras, presupuestos, precios, plazos, plataformas, nombres de campañas o productos>"],
  "avance_vs_anterior": ["<SOLO si hay CONTEXTO DEL CLIENTE: qué cambió o avanzó frente a lo que ya se sabía (decisiones nuevas, cambios de alcance, cifras que se actualizan, temas que quedaron atrás)>"],
  "conexion_backlog": ["<SOLO si hay tareas abiertas en el contexto: 'T-### — se cumple / avanza / cambia / queda bloqueada — por qué (según la reunión)'>"],
  "alertas": ["<contradicciones con acuerdos o datos anteriores, riesgos nuevos, compromisos que se cayeron; [] si no hay>"],
  "etiquetas": ["<3-6 etiquetas cortas para búsqueda>"]
}}

Reglas de cobertura (lo más importante):
- "detalles" debe cubrir TODOS los temas de la reunión, en el orden en que se hablaron, sin fusionar temas distintos: normalmente 6 a 12 bloques en una reunión de 30 a 60 minutos. Recorre la transcripción completa de principio a fin antes de escribir; los temas del inicio (contexto, novedades del cliente, pedidos que llegan "de paso") y del cierre cuentan igual que los de la presentación.
- Si alguien enumera elementos (alertas, fases, capas, requisitos, productos), escribe la lista COMPLETA, no "varios tipos" ni "6 alertas" sin nombrarlas.
- Cronogramas: cada hito con su semana o fecha (p. ej. "semana 8: tablero y alertas; semana 10: pedido sugerido; semana 12: decisión de ampliar").
- Toda cifra mencionada (porcentajes, montos, plazos, cantidades de personas, minutos ahorrados, volúmenes) va en "datos_clave" y, si aplica, en el bloque de detalle correspondiente.
- Atribuye bien: anota quién propuso cada idea o pidió cada cambio. Si no puedes saber quién del lado del cliente lo dijo, escribe "el cliente" en vez de adivinar un nombre.
- Pedidos o solicitudes que hagan las personas (aunque sea al pasar, p. ej. "la junta pidió un balanced scorecard") son temas propios en "detalles" y, si alguien queda a cargo, próximos pasos.

Reglas de contexto (si abajo hay CONTEXTO):
- "conexion_backlog": incluye una tarea SOLO si en la reunión se habló explícitamente de ella o de exactamente la misma acción (mismo entregable). No la incluyas por parecido temático ni por inferencia; ante la duda, déjala fuera. Lo mismo para avance_vs_anterior: cada punto debe apoyarse en algo dicho en la reunión.
- El contexto (calendario y memoria de Linnda) sirve para entender a qué se refieren, quién es quién y qué es nuevo. NO es parte de la reunión: acuerdos, próximos pasos, detalles y datos clave salen SOLO de la transcripción. Lo que compares con el contexto va en avance_vs_anterior, conexion_backlog y alertas.
- Usa los nombres de los invitados del calendario para "participantes" y para atribuir quién dijo qué cuando la transcripción lo permita (alguien lo nombra, se presenta o habla de su rol). Quien rechazó la invitación probablemente no estuvo.
- Si el calendario indica el cliente, úsalo salvo que la conversación muestre claramente que es otro.

Reglas generales:
- Quién habla: cada línea trae el nombre de quien habla ("{yo['etiqueta']}" es {yo['nombre']}, {yo['rol']}, quien grabó la reunión). Si dice "Yo" es {yo['corto']}; "Cliente" u "Otros" = todo el otro lado (pueden ser varias personas). Los nombres de las voces del otro lado los puso un proceso automático: si una frase claramente no cuadra con quien la tiene asignada, atribúyela según el contenido. "Persona sin identificar" no es un nombre: no lo uses como participante con nombre.
- Para "cliente": elige de la lista SOLO si hay evidencia clara (se nombra la marca, el producto, el proyecto o una persona asociada). Si dudas, confianza 'media' o 'baja'.
- Si solo hablan personas de Linnda (Rafa, Lina, Juan Pablo, el equipo), es reunión interna: cliente 'Nuevo/Desconocido', empresa_nueva 'Linnda Interno', es_potencial false.
- Sé concreto y fiel a lo dicho; no inventes acuerdos, fechas ni cifras. Los próximos pasos deben ser accionables y con responsable.
- Si una sección no aplica, usa [] o "".
- Todo en español.

CLIENTES Y PROSPECTOS CONOCIDOS DE LINNDA:
{lista}

METADATOS: fecha={meta.get("fecha", "")}, hora inicio={meta.get("hora", "")}, duración={meta.get("duracion", "")}

{("CONTEXTO · REUNIÓN EN EL CALENDARIO:" + chr(10) + ctx_reunion + chr(10) + chr(10)) if ctx_reunion else ""}{("CONTEXTO · MEMORIA DE LINNDA SOBRE EL CLIENTE (anterior a esta reunión):" + chr(10) + ctx_cliente + chr(10) + chr(10)) if ctx_cliente else ""}TRANSCRIPCIÓN (diálogo con marcas de tiempo):
---
{dialogo}
---
"""


def identificar_cliente(dialogo: str, clientes: list[dict], api_key: str, model, log=None) -> dict:
    """Primera pasada rápida: ¿de qué cliente es la llamada? (para buscar su contexto)."""
    lista = "\n".join(f"- {c['nombre']}" + (f" (alias: {', '.join(c.get('aliases', [])[:8])})" if c.get("aliases") else "")
                      for c in clientes)
    prompt = f"""Lee el inicio de esta transcripción de una llamada de Linnda y di de qué cliente o prospecto es.
Devuelve SOLO JSON: {{"cliente": "<nombre exacto de la lista, o 'Nuevo/Desconocido'>", "confianza": "<alta|media|baja>"}}
Si solo hablan personas de Linnda (Rafa, Lina, Juan Pablo…), es "Linnda Interno".

LISTA:
{lista}

TRANSCRIPCIÓN (inicio):
{dialogo[:12000]}
"""
    raw, _ = GA.generate([{"text": prompt}], api_key, model,
                         {"temperature": 0, "maxOutputTokens": 512, "responseMimeType": "application/json"}, log=log)
    try:
        return json.loads(raw.strip().removeprefix("```json").removesuffix("```"))
    except Exception:
        return {"cliente": "Nuevo/Desconocido", "confianza": "baja"}


def analizar(dialogo: str, clientes: list[dict], meta: dict, api_key: str, model, log=None,
             ctx_reunion: str = "", ctx_cliente: str = "") -> dict:
    """Devuelve el dict estructurado. Lanza GeminiError si falla."""
    raw, used = GA.generate(
        [{"text": _build_prompt(dialogo, clientes, meta, ctx_reunion, ctx_cliente)}], api_key, model,
        {"temperature": 0.3, "maxOutputTokens": 32768, "responseMimeType": "application/json"},
        log=log,
    )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(cleaned)
    data["_modelo"] = used
    return data
