"""
agents/graph_state.py — LangGraph State Definition.
TypedDict state schema flowing through the StateGraph.
Each node reads from and writes to this state.
"""
from typing import Optional
from typing_extensions import TypedDict


class ExplorationState(TypedDict, total=False):
    # ── Task info (set at start) ─────────────────────
    node_id: str
    target_url: str
    depth: int
    parent_node_id: Optional[str]
    action_to_reach: str
    _credential_provider: object  # injected callable for HITL

    # ── Page state (from extract_dom) ────────────────
    current_url: str
    page_title: str
    page_headings: list
    raw_elements: list
    input_elements: list
    button_elements: list
    total_elements: int
    dom_hash: str

    # ── LLM analysis (from llm_analyze) ─────────────
    page_summary: str
    page_type: str
    navigations: list
    functionalities: list
    llm_analysis_raw: Optional[dict]

    # ── Auth detection (from detect_auth) ────────────
    is_auth_page: bool
    auth_fields: list
    auth_credentials: Optional[dict]
    auth_status: str   # none | detected | credentials_received | skipped

    # ── Auth persistence (shared across BFS iterations) ──
    is_authenticated: bool          # True after successful login
    session_cookies: Optional[dict] # saved cookies post-login
    login_completed_url: Optional[str]  # URL we landed on after login
    return_to_url: Optional[str]    # page to go back to after inline login
    stored_credentials: Optional[dict]  # {purpose: value} e.g. {"username":"Admin","password":"admin123"}

    # ── Screenshot for verification ──────────────────
    page_screenshot_b64: Optional[str]  # screenshot when inputs detected

    # ── Execution (from execute_funcs) ───────────────
    functionality_results: list
    funcs_completed: bool

    # ── Discovery (from collect_urls) ────────────────
    discovered_urls: list  # [{url, action_to_reach}]
    clicked_labels: list   # labels successfully clicked this page (for session-resume visibility)

    # ── Control ──────────────────────────────────────
    status: str   # pending | exploring | done | error | duplicate
    error: Optional[str]
    messages: list
