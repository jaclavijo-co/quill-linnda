"""Archiva una llamada analizada como nota Markdown por cliente y mantiene
el índice maestro buscable (INDICE.md + index.jsonl).

Estructura resultante:
  ~/Notas-Llamadas/
    INDICE.md                      <- tabla legible de todas las llamadas
    index.jsonl                    <- una línea JSON por llamada (para 'buscar')
    <Cliente>/
      2026-08-03 -- titulo.md      <- la nota completa
    _inbox/                        <- clientes con confianza baja / desconocidos
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path


def _slug(text: str, maxlen: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:maxlen].strip("-") or "llamada"


def _safe_folder(name: str) -> str:
    """Nombre de carpeta seguro conservando mayúsculas/acentos legibles."""
    name = name.replace("/", "-").strip()
    return re.sub(r"[<>:\"\\|?*]", "", name) or "Sin-Cliente"


def _md_list(items: list[str]) -> str:
    items = [i for i in (items or []) if str(i).strip()]
    if not items:
        return "_—_"
    return "\n".join(f"- {i}" for i in items)


def _md_detalles(analisis: dict) -> str:
    det = analisis.get("detalles") or []
    if det and isinstance(det[0], dict):
        return "\n\n".join(f"**{d.get('tema','')}.** {d.get('detalle','')}" for d in det) or "_—_"
    # notas viejas: solo lista de temas
    return _md_list(det or analisis.get("temas", []))


def _md_contexto(analisis: dict) -> str:
    """Sección que compara la reunión con la memoria de Linnda (si hubo contexto)."""
    partes = []
    for clave, titulo in (("avance_vs_anterior", "Qué cambió frente a lo que sabíamos"),
                          ("conexion_backlog", "Conexión con el backlog"),
                          ("alertas", "Alertas")):
        items = [i for i in (analisis.get(clave) or []) if str(i).strip()]
        if items:
            partes.append(f"**{titulo}**\n" + "\n".join(f"- {i}" for i in items))
    if not partes:
        return ""
    return "\n## Contexto y avance\n" + "\n\n".join(partes) + "\n"


def carpeta_destino(analisis: dict) -> tuple[str, bool]:
    """Devuelve (carpeta, es_nueva_empresa).

    - Cliente conocido con confianza media/alta -> su carpeta.
    - Empresa nueva identificada por nombre (confianza media/alta) -> carpeta
      con ese nombre (se crea) y se registra en clientes.json como prospecto.
    - Todo lo demás -> _inbox (la bandeja de la mañana lo reubica con ayuda
      del calendario).
    """
    cliente = (analisis.get("cliente") or "").strip()
    conf = analisis.get("cliente_confianza", "baja")
    empresa = (analisis.get("empresa_nueva") or "").strip()
    desconocido = not cliente or cliente.lower().startswith(("nuevo", "desconocido"))
    if not desconocido and conf != "baja":
        return _safe_folder(cliente), False
    if desconocido and empresa and conf != "baja":
        return _safe_folder(empresa), empresa != "Linnda Interno"
    return "_inbox", False


def render_nota(analisis: dict, meta: dict, dialogo: str, session_name: str) -> str:
    cliente = analisis.get("cliente", "Desconocido")
    conf = analisis.get("cliente_confianza", "?")
    potencial = ("Sí (prospecto)" if analisis.get("es_potencial")
                 else "No (reunión interna)" if cliente == "Linnda Interno" else "No (cliente activo)")
    titulo = analisis.get("titulo", session_name)
    etiquetas = " ".join(f"#{_slug(t)}" for t in analisis.get("etiquetas", []))

    return f"""---
cliente: {cliente}
confianza_cliente: {conf}
potencial: {analisis.get("es_potencial", False)}
fecha: {meta.get("fecha", "")}
hora: {meta.get("hora", "")}
duracion: {meta.get("duracion", "")}
sesion: {session_name}
titulo: {titulo}
etiquetas: [{", ".join(analisis.get("etiquetas", []))}]
---

# {titulo}

**Cliente:** {cliente}  ·  **Prospecto:** {potencial}  ·  **Fecha:** {meta.get("fecha", "")}  ·  **Duración:** {meta.get("duracion", "")}{("  ·  **Reunión:** " + meta["evento"]) if meta.get("evento") else ""}
{etiquetas}

## Resumen
{analisis.get("resumen", "_—_")}

## Participantes
{_md_list(analisis.get("participantes", []))}

## Detalles
{_md_detalles(analisis)}

## Acuerdos / decisiones
{_md_list(analisis.get("acuerdos", []))}

## Próximos pasos
{_md_list(analisis.get("proximos_pasos", []))}

## Preguntas abiertas
{_md_list(analisis.get("preguntas_abiertas", []))}
{_md_contexto(analisis)}
## Datos clave
{_md_list(analisis.get("datos_clave", []))}

---

## Transcripción completa
<details>
<summary>Ver diálogo completo ({session_name})</summary>

```
{dialogo}
```
</details>
"""


def archivar(notes_dir: Path, analisis: dict, meta: dict, dialogo: str, session_name: str, log=print) -> Path:
    cliente = analisis.get("cliente") or "Nuevo/Desconocido"
    conf = analisis.get("cliente_confianza", "baja")

    # Reprocesar la misma sesión no debe dejar notas huérfanas: si el índice ya
    # tiene una nota para esta sesión (aunque el título haya cambiado), bórrala.
    _eliminar_nota_previa(notes_dir, session_name, log)

    folder_name, empresa_nueva = carpeta_destino(analisis)
    if folder_name != "_inbox" and cliente.lower().startswith(("nuevo", "desconocido")):
        # la nota y el índice quedan con el nombre de la empresa
        analisis["cliente"] = analisis.get("empresa_nueva") or cliente
    if empresa_nueva:
        try:
            import ntconfig
            if ntconfig.add_cliente(analisis["cliente"], prospecto=bool(analisis.get("es_potencial", True))):
                log(f"  empresa nueva registrada en clientes.json: {analisis['cliente']}")
        except Exception as e:
            log(f"  (no pude registrar la empresa nueva: {e})")

    folder = notes_dir / folder_name
    folder.mkdir(parents=True, exist_ok=True)

    fecha = meta.get("fecha", session_name[:10])
    base = f"{fecha} -- {_slug(analisis.get('titulo', session_name))}"
    dest = folder / f"{base}.md"
    n = 2
    while dest.exists():
        dest = folder / f"{base}-{n}.md"
        n += 1

    dest.write_text(render_nota(analisis, meta, dialogo, session_name))
    log(f"  nota escrita: {dest}")

    _actualizar_indice(notes_dir, analisis, meta, session_name, dest)
    return dest


def _eliminar_nota_previa(notes_dir: Path, session_name: str, log=print):
    """Borra el .md previo de esta sesión (si existe según el índice)."""
    index_jsonl = notes_dir / "index.jsonl"
    if not index_jsonl.exists():
        return
    for line in index_jsonl.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("sesion") == session_name and e.get("nota"):
            prev = notes_dir / e["nota"]
            if prev.exists():
                prev.unlink()
                log(f"  (reemplazando nota previa: {prev.name})")


def _actualizar_indice(notes_dir: Path, analisis: dict, meta: dict, session_name: str, nota_path: Path):
    """Agrega una línea al índice JSONL (fuente para búsqueda) y regenera INDICE.md."""
    index_jsonl = notes_dir / "index.jsonl"
    rel = nota_path.relative_to(notes_dir)
    entry = {
        "sesion": session_name,
        "fecha": meta.get("fecha", ""),
        "cliente": analisis.get("cliente", ""),
        "confianza": analisis.get("cliente_confianza", ""),
        "potencial": bool(analisis.get("es_potencial", False)),
        "titulo": analisis.get("titulo", ""),
        "resumen": analisis.get("resumen", ""),
        "temas": analisis.get("temas", []),
        "acuerdos": analisis.get("acuerdos", []),
        "proximos_pasos": analisis.get("proximos_pasos", []),
        "preguntas_abiertas": analisis.get("preguntas_abiertas", []),
        "etiquetas": analisis.get("etiquetas", []),
        "hora": meta.get("hora", ""),
        "evento": meta.get("evento", ""),
        "alertas": analisis.get("alertas", []),
        "nota": str(rel),
    }

    # Reemplaza cualquier entrada previa de la misma sesión (idempotente al reprocesar).
    entries = []
    if index_jsonl.exists():
        for line in index_jsonl.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("sesion") != session_name:
                entries.append(e)
    entries.append(entry)
    entries.sort(key=lambda e: (e.get("fecha", ""), e.get("sesion", "")), reverse=True)
    index_jsonl.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n")

    _render_indice_md(notes_dir, entries)


def _render_indice_md(notes_dir: Path, entries: list[dict]):
    lines = [
        "# Índice de llamadas — Linnda",
        "",
        f"_{len(entries)} llamadas registradas. Generado por notetaker._",
        "",
        "| Fecha | Cliente | Título | Próximos pasos | Nota |",
        "|---|---|---|---|---|",
    ]
    for e in entries:
        ps = "; ".join(e.get("proximos_pasos", [])[:2]) or "—"
        ps = ps.replace("|", "\\|")
        titulo = e.get("titulo", "").replace("|", "\\|")
        cliente = e.get("cliente", "").replace("|", "\\|")
        pot = " 🌱" if e.get("potencial") else ""
        link = f"[abrir]({e['nota'].replace(' ', '%20')})"
        lines.append(f"| {e.get('fecha','')} | {cliente}{pot} | {titulo} | {ps} | {link} |")
    (notes_dir / "INDICE.md").write_text("\n".join(lines) + "\n")
