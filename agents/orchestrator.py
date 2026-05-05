"""
agents/orchestrator.py — LangGraph BFS Orchestrator.

Builds StateGraph → BFS loop → store → export.
"""
import asyncio, json, logging, os, time
from datetime import datetime, timezone
from typing import Optional, Callable

from langgraph.graph import StateGraph, END
from agents.graph_state import ExplorationState
from agents.graph_nodes import (
    navigate_node, extract_dom_node, llm_analyze_node,
    detect_auth_node, hitl_credentials_node, fill_auth_node,
    execute_funcs_node, collect_urls_node, finalize_node,
    route_after_navigate, route_after_detect_auth, route_after_fill_auth,
)
from graph.bfs_manager import BFSManager
from graph.tree_builder import ExplorationTree
from memory.node_store import NodeStore
from tools.browser_session import browser
from config import RESULTS_DIR, FLOWS_DIR, set_seed_url

logger = logging.getLogger("flow_mapper.orchestrator")


def build_graph():
    """
    Compile the LangGraph StateGraph.

    Graph:
      navigate ──(error)──→ finalize → END
               ──(ok)────→ extract_dom → llm_analyze → detect_auth
                                ├─(auth)──→ hitl_credentials → fill_auth
                                │              ├─(creds)──→ extract_dom (loop)
                                │              └─(skip)───→ execute_funcs
                                └─(normal)──→ execute_funcs → collect_urls → finalize → END
    """
    g = StateGraph(ExplorationState)
    g.add_node("navigate",          navigate_node)
    g.add_node("extract_dom",       extract_dom_node)
    g.add_node("llm_analyze",       llm_analyze_node)
    g.add_node("detect_auth",       detect_auth_node)
    g.add_node("hitl_credentials",  hitl_credentials_node)
    g.add_node("fill_auth",         fill_auth_node)
    g.add_node("execute_funcs",     execute_funcs_node)
    g.add_node("collect_urls",      collect_urls_node)
    g.add_node("finalize",          finalize_node)

    g.set_entry_point("navigate")

    g.add_conditional_edges("navigate", route_after_navigate,
                             {"extract_dom":"extract_dom","finalize":"finalize"})
    g.add_edge("extract_dom",  "llm_analyze")
    g.add_edge("llm_analyze",  "detect_auth")
    g.add_conditional_edges("detect_auth", route_after_detect_auth,
                             {"hitl_credentials":"hitl_credentials","execute_funcs":"execute_funcs"})
    g.add_edge("hitl_credentials", "fill_auth")
    g.add_conditional_edges("fill_auth", route_after_fill_auth,
                             {"extract_dom":"extract_dom","execute_funcs":"execute_funcs"})
    g.add_edge("execute_funcs", "collect_urls")
    g.add_edge("collect_urls",  "finalize")
    g.add_edge("finalize",      END)

    compiled = g.compile()
    logger.info("[ORCH] ✅ LangGraph compiled")
    return compiled


class Orchestrator:
    """BFS orchestrator powered by LangGraph."""

    def __init__(self, credential_provider: Optional[Callable] = None):
        self.bfs       = BFSManager()
        self.tree      = ExplorationTree()
        self.store     = NodeStore()
        self.graph     = build_graph()
        self.cred_prov = credential_provider
        self.seed_url  = ""
        self.start_t   = 0.0
        self._running  = False
        # Auth state persisted across ALL BFS iterations
        self._auth_state = {
            "is_authenticated": False,
            "session_cookies": None,
            "login_completed_url": None,
            "stored_credentials": None,  # {purpose: value} e.g. {"username":"Admin","password":"admin123"}
        }

    async def run(self, seed_url: str):
        self.seed_url = seed_url
        self.start_t  = time.time()
        self._running = True
        logger.info(f"\n{'#'*60}\n[ORCH] 🚀 {seed_url}\n{'#'*60}")

        set_seed_url(seed_url)
        await browser.initialize()

        seed = await self.bfs.add_seed(seed_url)
        await self.tree.add_node(seed.node_id, seed_url, 0, None, "seed_url")

        # BFS loop
        while self._running:
            task = await self.bfs.get_next()
            if task is None:
                if self.bfs.is_empty():
                    logger.info("[ORCH] 🏁 Done")
                    break
                await asyncio.sleep(0.5)
                continue
            logger.info(f"\n[ORCH] ━━ {task.node_id} | depth={task.depth} | q={self.bfs.stats['queue_size']} ━━")
            await self._process(task)

        await self._export()
        await browser.close()

    async def stop(self): self._running = False

    async def _process(self, task):
        try:
            await self.tree.update_node(task.node_id, status="exploring")
            initial: ExplorationState = {
                "node_id": task.node_id, "target_url": task.url,
                "depth": task.depth, "parent_node_id": task.parent_node_id,
                "action_to_reach": task.action_to_reach, "status": "pending",
                "navigations": [], "functionalities": [],
                "functionality_results": [], "discovered_urls": [],
                "is_auth_page": False, "auth_fields": [],
                "auth_credentials": None, "auth_status": "none",
                "funcs_completed": False, "raw_elements": [],
                "input_elements": [], "button_elements": [],
                "page_headings": [], "messages": [],
                "page_screenshot_b64": None,
                "_credential_provider": self.cred_prov,
                # Inject persistent auth state from previous iterations
                "is_authenticated": self._auth_state["is_authenticated"],
                "session_cookies": self._auth_state["session_cookies"],
                "login_completed_url": self._auth_state["login_completed_url"],
                "stored_credentials": self._auth_state["stored_credentials"],
            }
            final = await self.graph.ainvoke(initial)
            logger.info(f"[ORCH] Graph done: {final.get('status')}")

            # Persist auth state whenever login happened (first login OR re-login after session expiry)
            if final.get("is_authenticated"):
                was_first_login = not self._auth_state["is_authenticated"]
                self._auth_state["is_authenticated"] = True
                self._auth_state["session_cookies"] = final.get("session_cookies")
                self._auth_state["login_completed_url"] = final.get("login_completed_url")

                # Store credentials by purpose on first login only (values don't change)
                if was_first_login and not self._auth_state["stored_credentials"]:
                    creds  = final.get("auth_credentials", {}) or {}
                    fields = final.get("auth_fields", []) or []
                    if creds and fields:
                        purpose_map = {f.get("field_name",""): f.get("purpose","other") for f in fields}
                        stored = {}
                        for fname, cd in creds.items():
                            purpose = purpose_map.get(fname, fname)
                            stored[purpose] = cd.get("value", "")
                        self._auth_state["stored_credentials"] = stored
                        safe = {k: ("[HIDDEN]" if "pass" in k else v) for k,v in stored.items()}
                        logger.info(f"[ORCH] 🔑 Credentials stored for re-login: {safe}")

                logger.info(f"[ORCH] 🔑 Auth state persisted (first_login={was_first_login})")

            await self._store(task, final)

            if final.get("dom_hash"):
                if not await self.bfs.mark_page_hash(final["dom_hash"]):
                    logger.info(f"[ORCH] Dup DOM: {task.node_id}")
                    await self.tree.update_node(task.node_id, status="duplicate")
                    return

            for disc in final.get("discovered_urls", []):
                nt = await self.bfs.add_discovered_url(
                    url=disc["url"], parent_node_id=task.node_id,
                    action_to_reach=disc.get("action_to_reach","link"),
                    parent_depth=task.depth)
                if nt:
                    await self.tree.add_node(nt.node_id, nt.url, nt.depth,
                                              task.node_id, nt.action_to_reach)
        except Exception as e:
            logger.error(f"[ORCH] ❌ {task.node_id}: {e}")
            import traceback; traceback.print_exc()
            await self.tree.update_node(task.node_id, status="error")

    async def _store(self, task, state: dict):
        nd = self.store.create(task.node_id, task.url)
        nd.page_title           = state.get("page_title","")
        nd.page_type            = state.get("page_type","")
        nd.page_summary         = state.get("page_summary","")
        nd.headings             = state.get("page_headings",[])
        nd.raw_elements         = state.get("raw_elements",[])
        nd.total_elements       = state.get("total_elements",0)
        nd.navigations          = state.get("navigations",[])
        nd.functionalities      = state.get("functionalities",[])
        nd.functionality_results= state.get("functionality_results",[])
        nd.parent_node_id       = task.parent_node_id
        nd.action_to_reach      = task.action_to_reach
        nd.discovered_urls      = [d["url"] for d in state.get("discovered_urls",[])]
        nd.dom_hash             = state.get("dom_hash","")
        nd.depth                = task.depth
        nd.explored_at          = datetime.now(timezone.utc).isoformat()
        nd.status               = state.get("status","done")
        nd.error                = state.get("error")
        self.store.save(nd)

        await self.tree.update_node(task.node_id,
            page_title=nd.page_title, page_summary=nd.page_summary,
            page_type=nd.page_type, navigations=nd.navigations,
            functionalities=nd.functionalities,
            functionality_results=nd.functionality_results,
            dom_hash=nd.dom_hash, explored_at=nd.explored_at, status=nd.status)

        for fr in nd.functionality_results:
            if fr.get("status") == "done":
                await self.tree.add_func_edge(task.node_id,
                    fr.get("description",""), result_url=fr.get("result_url"))

    async def _export(self):
        elapsed = round(time.time() - self.start_t, 2)
        logger.info(f"\n{'#'*60}\n[ORCH] Exporting | {elapsed}s\n{'#'*60}")
        os.makedirs(RESULTS_DIR, exist_ok=True)
        os.makedirs(FLOWS_DIR, exist_ok=True)
        self.store.export_all()
        self.store.export_combined(os.path.join(RESULTS_DIR,"all_nodes.json"))
        with open(os.path.join(RESULTS_DIR,"exploration_tree.json"),"w") as f:
            json.dump({"seed_url":self.seed_url,
                       "stats":{**self.bfs.stats,"duration_sec":elapsed,
                                 "completed_at":datetime.now(timezone.utc).isoformat()},
                       "tree":self.tree.get_full_tree(),
                       "nodes":self.tree.export_all_nodes()},f,indent=2,default=str)
        flows = _build_flows(self.store.all_nodes())
        with open(os.path.join(FLOWS_DIR,"flow_mappings.json"),"w") as f:
            json.dump({"total_flows":len(flows),"flows":flows},f,indent=2,default=str)
        with open(os.path.join(RESULTS_DIR,"summary.json"),"w") as f:
            json.dump({"seed_url":self.seed_url,
                       "completed_at":datetime.now(timezone.utc).isoformat(),
                       "duration_sec":elapsed,"total_nodes":self.store.count,
                       "total_flows":len(flows),"stats":self.bfs.stats},f,indent=2,default=str)
        logger.info(f"[ORCH] ✅ {RESULTS_DIR}")


def _build_flows(nodes: list) -> list:
    children: dict = {}
    nm: dict = {}
    for n in nodes:
        nm[n.node_id] = n
        if n.parent_node_id:
            children.setdefault(n.parent_node_id,[]).append(n.node_id)
    roots = [n for n in nodes if not n.parent_node_id]
    flows, counter = [], 0
    for root in roots:
        paths: list = []
        _dfs(root.node_id, children, [root.node_id], paths)
        for path in paths:
            counter += 1
            steps = []
            for nid in path:
                n = nm.get(nid)
                if not n: continue
                steps.append({
                    "node":nid,"url":n.url,"page_title":n.page_title,
                    "page_type":n.page_type,"action_to_reach":n.action_to_reach,
                    "functionalities":[{"type":f.get("type"),"description":f.get("description"),
                        "fields":[{"name":e.get("field_name"),"value":e.get("fill_value")}
                                  for e in f.get("elements_involved",[]) if e.get("role")=="input"]}
                        for f in n.functionalities],
                    "results":[{"func_id":fr.get("func_id"),"status":fr.get("status"),
                                 "navigated":fr.get("navigated"),"result_url":fr.get("result_url")}
                               for fr in n.functionality_results]})
            flows.append({"flow_id":f"flow_{counter:03d}","path":steps})
    return flows

def _dfs(nid, children, path, all_paths):
    kids = children.get(nid,[])
    if not kids: all_paths.append(list(path)); return
    for kid in kids:
        path.append(kid); _dfs(kid,children,path,all_paths); path.pop()
