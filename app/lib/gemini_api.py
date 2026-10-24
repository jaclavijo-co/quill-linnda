"""Llamadas REST a Gemini (sin SDK) con modelos de respaldo.

Google retira o restringe modelos con frecuencia (p. ej. "no longer available
to new users"). Por eso cada llamada recibe una LISTA de modelos: si uno ya no
existe o no está disponible para esta key, se prueba el siguiente.
Los errores transitorios (429/5xx/red) se reintentan con el mismo modelo.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

API_BASE = "https://generativelanguage.googleapis.com/v1beta"

_GONE_HINTS = (
    "not found", "no longer available", "is not supported", "deprecated",
    "not available", "unsupported", "does not exist",
)


class GeminiError(Exception):
    pass


class ModelUnavailable(GeminiError):
    pass


def as_list(models) -> list[str]:
    if not models:
        return []
    if isinstance(models, str):
        return [models]
    return [m for m in models if m]


def _extract_text(body: dict) -> str:
    cands = body.get("candidates") or []
    if not cands:
        block = body.get("promptFeedback", {}).get("blockReason")
        raise GeminiError(f"sin candidatos (blockReason={block})")
    cand = cands[0]
    parts = cand.get("content", {}).get("parts", []) or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    if not text:
        raise GeminiError(f"respuesta vacía (finishReason={cand.get('finishReason', '?')})")
    return text


def _one(model: str, parts: list, api_key: str, gen_cfg: dict,
         timeout_s: int, max_retries: int) -> str:
    url = f"{API_BASE}/models/{model}:generateContent"
    data = json.dumps({
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": gen_cfg,
    }).encode("utf-8")
    last = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(
                url, data=data, method="POST",
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            )
            resp = urllib.request.urlopen(req, timeout=timeout_s)
            return _extract_text(json.loads(resp.read().decode()))
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:400]
            last = f"HTTP {e.code}: {body}"
            low = body.lower()
            if e.code == 404 or (e.code in (400, 403) and any(h in low for h in _GONE_HINTS)):
                raise ModelUnavailable(f"{model}: {last}")
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries:
                time.sleep(8 * attempt)
                continue
            raise GeminiError(last)
        except (urllib.error.URLError, TimeoutError) as e:
            last = str(e)
            if attempt < max_retries:
                time.sleep(8 * attempt)
                continue
            raise GeminiError(f"red tras {max_retries} intentos: {last}")
    raise GeminiError(last or "error desconocido")


def generate(parts: list, api_key: str, models, gen_cfg: dict,
             timeout_s: int = 300, max_retries: int = 3, log=None) -> tuple[str, str]:
    """Devuelve (texto, modelo_usado). Prueba los modelos en orden."""
    last = None
    for model in as_list(models):
        try:
            return _one(model, parts, api_key, gen_cfg, timeout_s, max_retries), model
        except ModelUnavailable as e:
            last = e
            if log:
                log(f"    modelo {model} no disponible; pruebo el siguiente")
            continue
    raise GeminiError(f"ningún modelo disponible ({last})")
