"""
config.py — Central configuration for Flow Mapper Phase 1.
All tunable settings. Override via env vars or CLI args.
"""
import os, logging
from urllib.parse import urlparse

LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG")
logging.basicConfig(level=LOG_LEVEL,
                    format="[%(asctime)s][%(name)s][%(levelname)s] %(message)s")
logger = logging.getLogger("flow_mapper")

# LLM
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "https://ollama.com")
OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY", "305deef28fa54bd883e20b7116651d0e.aUNJ9b8duG8ww6uxpJDpmq-A")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3-next:80b-cloud")
VISION_MODEL  = os.environ.get("VISION_MODEL",  "gemma4:31b-cloud")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "180"))

# Browser
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
NAVIGATION_TIMEOUT_MS = int(os.environ.get("NAVIGATION_TIMEOUT_MS", "30000"))
NETWORK_IDLE_TIMEOUT_MS = int(os.environ.get("NETWORK_IDLE_TIMEOUT_MS", "10000"))

# BFS
MAX_DEPTH = int(os.environ.get("MAX_DEPTH", "5"))
MAX_NODES = int(os.environ.get("MAX_NODES", "100"))
MAX_ACTIONS_PER_PAGE = int(os.environ.get("MAX_ACTIONS_PER_PAGE", "20"))
PAGE_LOAD_WAIT_SEC = float(os.environ.get("PAGE_LOAD_WAIT_SEC", "3.0"))

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "results")
NODES_DIR = os.path.join(RESULTS_DIR, "nodes")
FLOWS_DIR = os.path.join(RESULTS_DIR, "flows")
MEMORY_DIR = os.path.join(BASE_DIR, "memory", "nodes")

# Network
WS_HOST = os.environ.get("WS_HOST", "0.0.0.0")
WS_PORT = int(os.environ.get("WS_PORT", "8765"))
UI_PORT = int(os.environ.get("UI_PORT", "8090"))

# Domain scope
ALLOWED_DOMAINS: list[str] = []

def set_seed_url(url: str):
    parsed = urlparse(url)
    if parsed.netloc and parsed.netloc not in ALLOWED_DOMAINS:
        ALLOWED_DOMAINS.append(parsed.netloc)
    logger.info(f"[CONFIG] Scope: {ALLOWED_DOMAINS}")

def is_in_scope(url: str) -> bool:
    if not ALLOWED_DOMAINS:
        return True
    return urlparse(url).netloc in ALLOWED_DOMAINS
