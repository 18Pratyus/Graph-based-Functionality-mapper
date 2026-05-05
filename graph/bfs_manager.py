"""
graph/bfs_manager.py — BFS Queue Manager.

Manages exploration frontier (asyncio.Queue), visited URLs,
DOM hashes for dedup, node ID generation. Thread-safe.
"""
import asyncio, hashlib, logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urldefrag
from config import MAX_DEPTH, MAX_NODES, is_in_scope

logger = logging.getLogger("flow_mapper.bfs")

@dataclass
class ExploreTask:
    node_id: str
    url: str
    depth: int
    parent_node_id: Optional[str] = None
    action_to_reach: str = "seed_url"

class BFSManager:
    """Thread-safe BFS queue with visited tracking and limits."""

    def __init__(self):
        self._queue: asyncio.Queue[ExploreTask] = asyncio.Queue()
        self._visited_urls: set[str] = set()
        self._visited_hashes: set[str] = set()
        self._counter = 0
        self._lock = asyncio.Lock()
        self._explored = 0
        self._all_nodes: dict[str, ExploreTask] = {}
        logger.info(f"[BFS] Init: max_depth={MAX_DEPTH} max_nodes={MAX_NODES}")

    async def add_seed(self, url: str) -> ExploreTask:
        n = self._normalize(url)
        task = ExploreTask(node_id=await self._next_id(), url=n, depth=0)
        await self._queue.put(task)
        self._visited_urls.add(n)
        self._all_nodes[task.node_id] = task
        logger.info(f"[BFS] Seed: {task.url}")
        return task

    async def add_discovered_url(self, url: str, parent_node_id: str,
                                  action_to_reach: str, parent_depth: int
                                  ) -> Optional[ExploreTask]:
        n = self._normalize(url)
        async with self._lock:
            if n in self._visited_urls: return None
            if not is_in_scope(n): return None
            d = parent_depth + 1
            if d > MAX_DEPTH: return None
            if self._explored + self._queue.qsize() >= MAX_NODES: return None
            if self._is_static(n): return None
            self._visited_urls.add(n)

        task = ExploreTask(
            node_id=await self._next_id(), url=n, depth=d,
            parent_node_id=parent_node_id, action_to_reach=action_to_reach)
        await self._queue.put(task)
        self._all_nodes[task.node_id] = task
        logger.info(f"[BFS] + {task.node_id} ({n}) depth={d}")
        return task

    async def get_next(self) -> Optional[ExploreTask]:
        try:
            task = self._queue.get_nowait()
            async with self._lock:
                self._explored += 1
            return task
        except asyncio.QueueEmpty:
            return None

    async def mark_page_hash(self, h: str) -> bool:
        async with self._lock:
            if h in self._visited_hashes: return False
            self._visited_hashes.add(h)
            return True

    def is_empty(self) -> bool:
        return self._queue.empty()

    @property
    def stats(self) -> dict:
        return {
            "queue_size": self._queue.qsize(),
            "total_explored": self._explored,
            "unique_urls": len(self._visited_urls),
            "unique_pages": len(self._visited_hashes),
            "total_nodes": len(self._all_nodes),
        }

    async def _next_id(self) -> str:
        async with self._lock:
            self._counter += 1
            return f"node_{self._counter:03d}"

    @staticmethod
    def _normalize(url: str) -> str:
        url, _ = urldefrag(url)
        p = urlparse(url)
        return p._replace(scheme=p.scheme.lower(),
                           netloc=p.netloc.lower()).geturl().rstrip("/")

    @staticmethod
    def _is_static(url: str) -> bool:
        exts = {".css",".js",".png",".jpg",".jpeg",".gif",".svg",".ico",
                ".woff",".woff2",".ttf",".mp4",".mp3",".pdf",".zip"}
        return any(urlparse(url).path.lower().endswith(e) for e in exts)


def compute_dom_hash(url: str, elements: list[dict]) -> str:
    """Hash page structure for dedup (URL path + element signatures)."""
    path = urlparse(url).path
    sigs = sorted(f"{e.get('tag','')}-{e.get('type','')}-{e.get('name','')}"
                  for e in elements)
    raw = f"{path}|{'|'.join(sigs)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
