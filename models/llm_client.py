"""
models/llm_client.py — Async Ollama LLM client.
Provides query_llm (JSON) and query_llm_text (plain text).
"""
import json, logging, httpx
from typing import Optional
from config import OLLAMA_BASE_URL, OLLAMA_API_KEY, OLLAMA_MODEL, VISION_MODEL, OLLAMA_TIMEOUT

logger = logging.getLogger("flow_mapper.llm")

def _headers() -> dict:
    return {"Authorization": f"Bearer {OLLAMA_API_KEY}"} if OLLAMA_API_KEY else {}

async def query_llm(prompt: str, system_prompt: str = "") -> Optional[dict]:
    """Send prompt to Ollama, return parsed JSON dict or None."""
    payload = {
        "model": OLLAMA_MODEL, "prompt": prompt, "system": system_prompt,
        "stream": False, "format": "json",
        "options": {"temperature": 0.1, "num_predict": 4096},
    }
    logger.debug(f"[LLM] → model={OLLAMA_MODEL} len={len(prompt)}")
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT, headers=_headers()) as c:
            r = await c.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
            r.raise_for_status()
            raw = r.json().get("response", "")
            parsed = _extract_json(raw)
            if parsed is None:
                logger.error(f"[LLM] JSON parse fail: {raw[:300]}")
            return parsed
    except Exception as e:
        logger.error(f"[LLM] Error: {e}")
        return None

async def query_llm_text(prompt: str, system_prompt: str = "") -> str:
    """Send prompt to Ollama, return plain text."""
    payload = {
        "model": OLLAMA_MODEL, "prompt": prompt, "system": system_prompt,
        "stream": False, "options": {"temperature": 0.3, "num_predict": 8192},
    }
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT, headers=_headers()) as c:
            r = await c.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
            r.raise_for_status()
            return r.json().get("response", "")
    except Exception as e:
        logger.error(f"[LLM] Text error: {e}")
        return f"Error: {e}"

async def query_vision_llm(prompt: str, screenshot_b64: str, system_prompt: str = "") -> Optional[dict]:
    """Send screenshot + prompt to vision model. Returns parsed JSON or None."""
    raw_b64 = screenshot_b64.split(",", 1)[-1] if "," in screenshot_b64 else screenshot_b64
    payload = {
        "model": VISION_MODEL,
        "prompt": prompt, "system": system_prompt,
        "images": [raw_b64],
        "stream": False, "format": "json",
        "options": {"temperature": 0.1, "num_predict": 4096},
    }
    logger.debug(f"[VISION] → model={VISION_MODEL} img_len={len(raw_b64)}")
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT, headers=_headers()) as c:
            r = await c.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
            r.raise_for_status()
            raw = r.json().get("response", "")
            parsed = _extract_json(raw)
            if parsed is None:
                logger.error(f"[VISION] JSON parse fail: {raw[:300]}")
            return parsed
    except Exception as e:
        logger.error(f"[VISION] Error: {e}")
        return None


def _extract_json(text: str) -> Optional[dict]:
    """Extract JSON from LLM response (handles fences, noise)."""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for sc, ec in [('{', '}'), ('[', ']')]:
        start = text.find(sc)
        if start == -1: continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == sc: depth += 1
            elif text[i] == ec: depth -= 1
            if depth == 0:
                try: return json.loads(text[start:i+1])
                except: break
    return None
