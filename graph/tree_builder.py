"""
graph/tree_builder.py — Exploration tree with real-time WebSocket broadcast.
Stores nodes/edges. Broadcasts mutations to connected D3.js clients.
"""
import json, logging, asyncio
from typing import Optional

logger = logging.getLogger("flow_mapper.tree")

class TreeNode:
    def __init__(self, node_id, url, depth, parent_id=None, action_to_reach=""):
        self.node_id = node_id
        self.url = url
        self.depth = depth
        self.parent_id = parent_id
        self.action_to_reach = action_to_reach
        self.page_title = ""
        self.page_summary = ""
        self.page_type = ""
        self.navigations = []
        self.functionalities = []
        self.functionality_results = []
        self.child_node_ids = []
        self.dom_hash = ""
        self.explored_at = ""
        self.status = "pending"

    def to_dict(self):
        return {k: getattr(self, k) for k in [
            "node_id","url","depth","parent_id","action_to_reach",
            "page_title","page_summary","page_type","navigations",
            "functionalities","functionality_results","child_node_ids",
            "dom_hash","explored_at","status"]}

    def to_vis(self):
        return {"id": self.node_id, "url": self.url,
                "title": self.page_title or self.url,
                "type": self.page_type, "depth": self.depth,
                "status": self.status,
                "num_links": len(self.navigations),
                "num_funcs": len(self.functionalities)}

class ExplorationTree:
    def __init__(self):
        self.nodes: dict[str, TreeNode] = {}
        self.edges: list[dict] = []
        self._ws: set = set()
        self._lock = asyncio.Lock()

    def register_ws_client(self, ws):
        self._ws.add(ws)

    def unregister_ws_client(self, ws):
        self._ws.discard(ws)

    async def add_node(self, node_id, url, depth, parent_id=None, action=""):
        async with self._lock:
            node = TreeNode(node_id, url, depth, parent_id, action)
            self.nodes[node_id] = node
            edge = None
            if parent_id and parent_id in self.nodes:
                edge = {"source": parent_id, "target": node_id,
                        "action": action, "type": "navigation"}
                self.edges.append(edge)
                self.nodes[parent_id].child_node_ids.append(node_id)
        await self._broadcast({"event":"node_added",
                                "node": node.to_vis(), "edge": edge})
        return node

    async def update_node(self, node_id, **kw):
        async with self._lock:
            node = self.nodes.get(node_id)
            if not node: return None
            for k, v in kw.items():
                if hasattr(node, k): setattr(node, k, v)
        await self._broadcast({"event":"node_updated","node": node.to_vis()})
        return node

    async def add_func_edge(self, src, desc, result_url=None, tgt=None):
        edge = {"source": src, "target": tgt or src,
                "action": desc, "type": "functionality"}
        async with self._lock: self.edges.append(edge)
        await self._broadcast({"event":"edge_added","edge": edge})

    def get_full_tree(self):
        return {"event":"full_tree",
                "nodes": [n.to_vis() for n in self.nodes.values()],
                "edges": self.edges}

    def export_all_nodes(self):
        return [n.to_dict() for n in self.nodes.values()]

    async def _broadcast(self, msg):
        if not self._ws: return
        data = json.dumps(msg, default=str)
        dead = set()
        for ws in self._ws:
            try: await ws.send(data)
            except: dead.add(ws)
        self._ws -= dead
