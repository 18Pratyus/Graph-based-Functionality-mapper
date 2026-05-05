"""
memory/node_store.py — Per-node data storage.
RAM during crawl, JSON file backup per node. Full DOM stored without limits.
"""
import json, logging, os
from datetime import datetime, timezone
from typing import Optional
from config import MEMORY_DIR, NODES_DIR

logger = logging.getLogger("flow_mapper.store")

class NodeData:
    def __init__(self, node_id: str, url: str):
        self.node_id = node_id
        self.url = url
        self.page_title = ""
        self.page_type = ""
        self.page_summary = ""
        self.headings: list[str] = []
        self.raw_elements: list[dict] = []
        self.total_elements = 0
        self.navigations: list[dict] = []
        self.functionalities: list[dict] = []
        self.functionality_results: list[dict] = []
        self.parent_node_id: Optional[str] = None
        self.action_to_reach = ""
        self.child_node_ids: list[str] = []
        self.discovered_urls: list[str] = []
        self.dom_hash = ""
        self.depth = 0
        self.explored_at = ""
        self.exploration_duration_sec = 0.0
        self.status = "pending"
        self.error: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in [
            "node_id","url","page_title","page_type","page_summary",
            "headings","total_elements","navigations","functionalities",
            "functionality_results","parent_node_id","action_to_reach",
            "child_node_ids","discovered_urls","dom_hash","depth",
            "explored_at","exploration_duration_sec","status","error"]}

class NodeStore:
    def __init__(self):
        self._nodes: dict[str, NodeData] = {}
        os.makedirs(MEMORY_DIR, exist_ok=True)
        os.makedirs(NODES_DIR, exist_ok=True)

    def create(self, node_id: str, url: str) -> NodeData:
        n = NodeData(node_id, url)
        self._nodes[node_id] = n
        return n

    def get(self, node_id: str) -> Optional[NodeData]:
        return self._nodes.get(node_id)

    def save(self, node: NodeData):
        """Save to memory/nodes/ with full DOM."""
        fp = os.path.join(MEMORY_DIR, f"{node.node_id}.json")
        d = node.to_dict()
        d["raw_elements"] = node.raw_elements
        try:
            with open(fp, "w") as f: json.dump(d, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"[STORE] Save fail {node.node_id}: {e}")

    def export_all(self) -> str:
        """Export clean JSONs to results/nodes/."""
        for n in self._nodes.values():
            fp = os.path.join(NODES_DIR, f"{n.node_id}.json")
            try:
                with open(fp, "w") as f: json.dump(n.to_dict(), f, indent=2, default=str)
            except Exception as e:
                logger.error(f"[STORE] Export fail {n.node_id}: {e}")
        return NODES_DIR

    def export_combined(self, filepath: str):
        all_n = [n.to_dict() for n in self._nodes.values()]
        with open(filepath, "w") as f:
            json.dump({"total_nodes": len(all_n),
                        "exported_at": datetime.now(timezone.utc).isoformat(),
                        "nodes": all_n}, f, indent=2, default=str)

    @property
    def count(self): return len(self._nodes)
    def all_nodes(self): return list(self._nodes.values())
