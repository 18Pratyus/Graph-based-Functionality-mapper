"""
agents/graph_nodes.py — LangGraph Node Functions.

Each function = one node in the StateGraph.
NO hardcoded rules — LLM decides everything.

Pipeline:
  navigate → extract_dom → llm_analyze → detect_auth
    ├─ (auth) → hitl_credentials → fill_auth → extract_dom (loop)
    └─ (normal) → execute_funcs → collect_urls → finalize
"""
import asyncio, logging
from typing import Optional
from urllib.parse import urljoin

from agents.graph_state import ExplorationState
from tools.browser_session import browser
from models.llm_client import query_llm, query_vision_llm
from models.prompts import DOM_ANALYZER_SYSTEM, DOM_ANALYZER_PROMPT, format_elements_for_llm
from graph.bfs_manager import compute_dom_hash
from config import MAX_ACTIONS_PER_PAGE, PAGE_LOAD_WAIT_SEC

logger = logging.getLogger("flow_mapper.nodes")

# ─────────────────────────────────────────────────────────
# NODE 1 — NAVIGATE
# ─────────────────────────────────────────────────────────
async def navigate_node(state: ExplorationState) -> dict:
    url = state["target_url"]
    logger.info(f"\n{'─'*50}\n[NAVIGATE] {state['node_id']} → {url}")
    r = await browser.navigate(url)
    if r.get("status") == "error":
        logger.error(f"[NAVIGATE] ❌ {r.get('error')}")
        return {"status":"error","error":r.get("error"),"current_url":url}
    await asyncio.sleep(PAGE_LOAD_WAIT_SEC)
    logger.info(f"[NAVIGATE] ✅ {r.get('url')}")
    return {"current_url":r.get("url",url),"page_title":r.get("title",""),"status":"exploring"}

# ─────────────────────────────────────────────────────────
# NODE 2 — EXTRACT DOM
# ─────────────────────────────────────────────────────────
async def extract_dom_node(state: ExplorationState) -> dict:
    logger.info(f"[EXTRACT_DOM] {state['node_id']}")
    ps = await browser.get_state()
    if ps.get("status") != "success":
        return {"status":"error","error":"DOM extraction failed"}
    elements = ps.get("elements",[])
    dom_hash = compute_dom_hash(state.get("current_url",""), elements)
    logger.info(f"[EXTRACT_DOM] ✅ elements={len(elements)} hash={dom_hash[:16]}")
    return {
        "current_url":  ps.get("url", state.get("current_url")),
        "page_title":   ps.get("title",""),
        "page_headings":ps.get("headings",[]),
        "raw_elements": elements,
        "input_elements":  ps.get("inputs",[]),
        "button_elements": ps.get("buttons",[]),
        "total_elements":  len(elements),
        "dom_hash":        dom_hash,
    }

# ─────────────────────────────────────────────────────────
# NODE 3 — LLM ANALYZE
# ─────────────────────────────────────────────────────────
async def llm_analyze_node(state: ExplorationState) -> dict:
    elements = state.get("raw_elements",[])
    logger.info(f"[LLM_ANALYZE] {state['node_id']} — {len(elements)} elements")
    if not elements:
        return {"page_summary":"Empty page","page_type":"other",
                "navigations":[],"functionalities":[]}

    # Screenshot verification when inputs exist
    screenshot_b64 = None
    inputs = state.get("input_elements",[])
    if inputs:
        logger.info(f"[LLM_ANALYZE] 📸 Inputs detected ({len(inputs)}) — taking screenshot for verification")
        screenshot_b64 = await browser.screenshot_b64()

    prompt = DOM_ANALYZER_PROMPT.format(
        url=state.get("current_url",""), title=state.get("page_title",""),
        headings=", ".join(state.get("page_headings",[])) or "None",
        elements_text=format_elements_for_llm(elements))

    # Use vision model when screenshot available, main model otherwise
    if screenshot_b64 and len(screenshot_b64) > 100:
        logger.debug(f"[LLM_ANALYZE] Using vision model for screenshot analysis")
        result = await query_vision_llm(prompt, screenshot_b64, system_prompt=DOM_ANALYZER_SYSTEM)
        if result is None:
            logger.warning("[LLM_ANALYZE] Vision model failed — falling back to main model")
            result = await query_llm(prompt, system_prompt=DOM_ANALYZER_SYSTEM)
    else:
        result = await query_llm(prompt, system_prompt=DOM_ANALYZER_SYSTEM)
    if result is None:
        logger.warning("[LLM_ANALYZE] Failed — fallback")
        return {"page_summary":"LLM failed","page_type":"other",
                "navigations":_fallback_links(elements,state.get("current_url","")),
                "functionalities":[],"llm_analysis_raw":None,
                "page_screenshot_b64": screenshot_b64}
    navs  = result.get("navigations",[])
    funcs = result.get("functionalities",[])
    dangerous = result.get("dangerous_elements",[])

    # Log dangerous elements that will be skipped
    if dangerous:
        logger.info(f"[LLM_ANALYZE] ⚠️ {len(dangerous)} dangerous elements: {[d.get('label','?') for d in dangerous]}")

    logger.info(f"[LLM_ANALYZE] ✅ type={result.get('page_type')} navs={len(navs)} funcs={len(funcs)}")
    return {"page_summary":result.get("page_summary",""),
            "page_type":result.get("page_type","other"),
            "navigations":navs,"functionalities":funcs,"llm_analysis_raw":result,
            "page_screenshot_b64": screenshot_b64}

# ─────────────────────────────────────────────────────────
# NODE 4 — DETECT AUTH (fully LLM-driven)
# ─────────────────────────────────────────────────────────
_AUTH_SYS = "You are a security analyst. Determine if this page requires authentication. Respond ONLY with valid JSON."
_AUTH_PROMPT = """Is this a login/auth page?
URL: {url}  TITLE: {title}  PAGE_TYPE: {page_type}  SUMMARY: {summary}
INPUTS:\n{inputs}\nBUTTONS:\n{buttons}
Respond: {{"is_auth_page":true/false,"confidence":0.0-1.0,"reason":"...","auth_fields":[
{{"index":1,"field_name":"username","field_type":"text","label":"Username","purpose":"username|password|otp|other"}}
],"submit_button_index":3}}"""

async def detect_auth_node(state: ExplorationState) -> dict:
    inputs = state.get("input_elements",[])
    if not inputs: return {"is_auth_page":False,"auth_fields":[],"auth_status":"none"}
    logger.info(f"[DETECT_AUTH] {state['node_id']} — {len(inputs)} inputs")
    r = await query_llm(_AUTH_PROMPT.format(
        url=state.get("current_url",""), title=state.get("page_title",""),
        page_type=state.get("page_type",""), summary=state.get("page_summary",""),
        inputs=format_elements_for_llm(inputs),
        buttons=format_elements_for_llm(state.get("button_elements",[])[:10])),
        system_prompt=_AUTH_SYS)
    if r is None: return {"is_auth_page":False,"auth_fields":[],"auth_status":"none"}
    is_auth = r.get("is_auth_page",False) and r.get("confidence",0) > 0.6
    logger.info(f"[DETECT_AUTH] {'🔐 AUTH' if is_auth else '✅ normal'} conf={r.get('confidence')} {r.get('reason','')}")
    return {"is_auth_page":is_auth,"auth_fields":r.get("auth_fields",[]),
            "auth_status":"detected" if is_auth else "none"}

# ─────────────────────────────────────────────────────────
# NODE 5 — HITL CREDENTIALS
# ─────────────────────────────────────────────────────────
async def hitl_credentials_node(state: ExplorationState) -> dict:
    url    = state.get("current_url","")
    fields = state.get("auth_fields",[])

    # Use stored credentials (from prior login) — skip HITL entirely
    stored = state.get("stored_credentials") or {}
    if stored and fields:
        mapped = {}
        for field in fields:
            purpose    = field.get("purpose","")
            field_name = field.get("field_name", purpose)
            val = stored.get(purpose) or stored.get(field_name)
            if val:
                mapped[field_name] = {"index": field.get("index"), "value": val}
        if mapped:
            safe = {k: ("[HIDDEN]" if "pass" in k else v.get("value","")) for k,v in mapped.items()}
            logger.info(f"[HITL] 🔑 Re-login using stored credentials — {safe}")
            return {"auth_credentials": mapped, "auth_status": "credentials_received"}

    logger.info(f"\n{'='*50}\n[HITL] 🔐 Credentials needed: {url}")
    provider = state.get("_credential_provider")
    creds = (await provider(url, fields)) if (provider and callable(provider)) else (await _cli_input(url, fields))
    if creds:
        logger.info(f"[HITL] ✅ {len(creds)} fields received")
        return {"auth_credentials":creds,"auth_status":"credentials_received"}
    return {"auth_credentials":None,"auth_status":"skipped"}

async def _cli_input(url, fields) -> Optional[dict]:
    import getpass
    print(f"\n🔐 LOGIN PAGE: {url}")
    creds = {}
    for f in fields:
        lbl = f.get("label") or f.get("field_name","field")
        idx = f.get("index","?")
        val = (getpass.getpass(f"  [{idx}] {lbl}: ")
               if f.get("purpose")=="password" else input(f"  [{idx}] {lbl}: "))
        if val.strip().lower() == "skip": return None
        creds[f.get("field_name",str(idx))] = {"index":idx,"value":val}
    return creds or None

# ─────────────────────────────────────────────────────────
# NODE 6 — FILL AUTH
# ─────────────────────────────────────────────────────────
async def fill_auth_node(state: ExplorationState) -> dict:
    creds = state.get("auth_credentials")
    if not creds: return {}
    logger.info(f"[FILL_AUTH] Filling {len(creds)} fields")
    results = []
    for key, cd in creds.items():
        idx, val = cd.get("index"), cd.get("value","")
        masked = "[HIDDEN]" if "pass" in key.lower() else val
        logger.info(f"[FILL_AUTH] field='{key}' index={idx} value='{masked}'")
        if idx is not None:
            r = await browser.fill(int(idx), val)
            logger.info(f"[FILL_AUTH] fill result: status={r.get('status')} error={r.get('error','')}")
            results.append({"action":"fill_auth","field":key,"index":idx,"status":r.get("status")})
            await asyncio.sleep(0.5)  # longer wait — Vue reactive forms need time to process input

    # Get submit button index from LLM analysis (captured before filling — use directly, no get_state re-fetch)
    submit_idx = (state.get("llm_analysis_raw") or {}).get("submit_button_index")
    logger.info(f"[FILL_AUTH] submit_button_index from LLM={submit_idx}")
    submitted = False

    if submit_idx:
        logger.info(f"[FILL_AUTH] Clicking submit idx={submit_idx}")
        r = await browser.click(int(submit_idx))
        logger.info(f"[FILL_AUTH] submit click: navigated={r.get('navigated')} url={r.get('current_url')} error={r.get('error','')}")
        results.append({"action":"click_submit","index":submit_idx,"navigated":r.get("navigated"),"result_url":r.get("current_url")})
        submitted = True

    if not submitted:
        # Fallback: re-fetch fresh DOM to get correct button indices after filling
        ps = await browser.get_state()
        kws = {"login","sign in","log in","submit","continue","enter"}
        for el in ps.get("buttons", []):
            if any(k in (el.get("text","")+el.get("label","")).lower() for k in kws):
                logger.info(f"[FILL_AUTH] Fallback submit: clicking button idx={el['index']} text='{el.get('text','')}'")
                r = await browser.click(el["index"])
                logger.info(f"[FILL_AUTH] fallback click: navigated={r.get('navigated')} url={r.get('current_url')}")
                results.append({"action":"click_submit","index":el["index"],"navigated":r.get("navigated"),"result_url":r.get("current_url")})
                submitted = True; break

    if not submitted:
        logger.info(f"[FILL_AUTH] No submit button found — pressing Enter")
        await (await browser.page()).keyboard.press("Enter")
        await asyncio.sleep(2)

    await asyncio.sleep(1.5)  # wait for post-login redirect to complete
    page = await browser.page()
    post_login_url = page.url
    logger.info(f"[FILL_AUTH] Post-submit URL={post_login_url}")

    # Navigate back to target_url so exploration continues on the correct page
    target_url = state.get("target_url","")
    if target_url:
        logger.info(f"[FILL_AUTH] 🔄 Returning to target: {target_url}")
        nav = await browser.navigate(target_url)
        await asyncio.sleep(1.0)
        page = await browser.page()
    login_url = page.url
    logger.info(f"[FILL_AUTH] 📍 Final URL after return: {login_url}")

    # Save session cookies for reuse across BFS iterations
    session_cookies = {}
    try:
        raw_cookies = await page.context.cookies()
        session_cookies = {c["name"]: c["value"] for c in raw_cookies}
        logger.info(f"[FILL_AUTH] 🍪 Saved {len(session_cookies)} session cookies")
    except Exception as e:
        logger.warning(f"[FILL_AUTH] Cookie save failed: {e}")

    logger.info(f"[FILL_AUTH] ✅ Login complete → {login_url}")
    logger.info(f"[FILL_AUTH] 🔑 Marking session as authenticated")

    return {
        "current_url": login_url,
        "functionality_results": results,
        "is_authenticated": True,
        "session_cookies": session_cookies,
        "login_completed_url": login_url,
    }

# ─────────────────────────────────────────────────────────
# NODE 7 — EXECUTE FUNCTIONALITIES
# ─────────────────────────────────────────────────────────
async def execute_funcs_node(state: ExplorationState) -> dict:
    funcs = state.get("functionalities",[])
    target = state.get("target_url","")
    if not funcs:
        return {"functionality_results":[],"funcs_completed":True}
    logger.info(f"[EXEC_FUNCS] {state['node_id']} — {len(funcs)} funcs")
    results = list(state.get("functionality_results",[]))
    new_disc: list = []
    executed = 0
    for func in funcs:
        if executed >= MAX_ACTIONS_PER_PAGE: break
        fid   = func.get("func_id", f"f{executed}")
        ftype = func.get("type","unknown")
        desc  = func.get("description","")
        elems = func.get("elements_involved",[])
        order = func.get("execution_order",[])
        logger.info(f"[EXEC_FUNCS] ▶ {fid} ({ftype}): {desc}")
        fr = {"func_id":fid,"type":ftype,"description":desc,
              "actions_taken":[],"navigated":False,"result_url":None,"status":"pending"}
        try:
            await browser.navigate(target)
            await asyncio.sleep(0.5)
            for el in _order_elems(elems, order):
                idx  = el.get("index")
                role = el.get("role","")
                val  = el.get("fill_value","")
                if idx is None: continue
                if role == "input" and val:
                    r = await browser.fill(int(idx), val)
                    fr["actions_taken"].append({"action":"fill","index":idx,"field":el.get("field_name",""),"value":val,"status":r.get("status")})
                    await asyncio.sleep(0.3)
                elif role in ("submit_button","button",""):
                    r = await browser.click(int(idx))
                    nav = r.get("navigated",False)
                    rurl = r.get("current_url","")
                    fr["actions_taken"].append({"action":"click","index":idx,"navigated":nav,"result_url":rurl})
                    if nav:
                        fr["navigated"] = True; fr["result_url"] = rurl
                        new_disc.append({"url":rurl,"action_to_reach":f"{ftype}: {desc}"})
                    await asyncio.sleep(0.5)
            fr["status"] = "done"; executed += 1
        except Exception as e:
            logger.error(f"[EXEC_FUNCS] ❌ {fid}: {e}")
            fr["status"]="error"; fr["error"]=str(e)
        results.append(fr)
    return {"functionality_results":results,
            "discovered_urls": list(state.get("discovered_urls",[])) + new_disc,
            "funcs_completed":True}

# ─────────────────────────────────────────────────────────
# NODE 8 — COLLECT URLS  (3-layer robust click strategy)
# ─────────────────────────────────────────────────────────

def _build_fingerprints(navigations: list, raw_elements: list) -> list:
    """
    Enrich each LLM nav item with a multi-attribute fingerprint captured at
    original DOM scan time. css_selector is the primary stable locator —
    built from href/id/aria-label which survive DOM rebuilds across navigations.
    """
    elem_map = {e["index"]: e for e in raw_elements if e.get("index") is not None}
    enriched = []
    for nav in navigations:
        el = elem_map.get(nav.get("index"), {})
        enriched.append({
            **nav,
            "fingerprint": {
                "label":        nav.get("label", ""),
                "tag":          el.get("tag", ""),
                "role":         el.get("role", "link"),
                "href":         el.get("href", ""),
                "id":           el.get("id", ""),
                "css_class":    el.get("css_class", ""),
                "text":         el.get("text", ""),
                "name":         el.get("name", ""),
                "css_selector": el.get("css_selector", ""),  # stable Playwright locator
            }
        })
    return enriched


def _find_by_fingerprint(elements: list, fingerprint: dict) -> Optional[dict]:
    """
    Score every element in a fresh DOM against the stored fingerprint.
    Returns the full best-matching element dict (score >= 3), else None.
    Caller uses element's css_selector for clicking — no index needed.

    Weights (higher = more stable attribute):
      id match      +5
      href match    +4
      label in text +3  (+2 bonus for exact match)
      tag match     +1
      class overlap +1 per shared token (max 2)
    """
    label   = fingerprint.get("label", "").lower().strip()
    href    = fingerprint.get("href", "")
    el_id   = fingerprint.get("id", "")
    css_cls = fingerprint.get("css_class", "")
    tag     = fingerprint.get("tag", "")

    best_score, best_el = 0, None
    for el in elements:
        score = 0
        el_text = " ".join([el.get("text", ""), el.get("label", "")]).lower()

        if el_id and el_id == el.get("id", ""):         score += 5
        if href and href == el.get("href", ""):          score += 4
        if label and label in el_text:
            score += 3
            if label == el_text.strip():                 score += 2
        if tag and tag == el.get("tag", ""):             score += 1
        if css_cls and el.get("css_class", ""):
            shared = set(css_cls.split()) & set(el.get("css_class", "").split())
            score += min(len(shared), 2)

        if score > best_score:
            best_score, best_el = score, el

    return best_el if best_score >= 3 else None


async def collect_urls_node(state: ExplorationState) -> dict:
    """
    3-layer robust click strategy (ascending index order preserved):
      L1: click(original_index)             — fast, works ~90% of time
      L2: fingerprint match → fresh_index   — catches silent index-shift errors
      L3: Playwright get_by_role/text       — last resort for complex SPAs
    Global dedup via seen set.
    """
    target_url   = state.get("target_url", state.get("current_url", ""))
    navigations  = state.get("navigations", [])
    raw_elements = state.get("raw_elements", [])
    dangerous    = (state.get("llm_analysis_raw") or {}).get("dangerous_elements", [])
    dangerous_indices = {d.get("index") for d in dangerous if d.get("index") is not None}

    seen: set            = set()
    combined: list        = []
    clicked_labels_done: set = set()  # tracks labels clicked this session (for resume visibility)

    for d in state.get("discovered_urls", []):
        url = d.get("url", "")
        if url and url not in seen:
            seen.add(url); combined.append(d)

    if not navigations:
        logger.info(f"[COLLECT_URLS] {state['node_id']} — no nav elements to click")
        return {"discovered_urls": combined, "clicked_labels": [], "status": "done"}

    nav_list = _build_fingerprints(navigations, raw_elements)
    logger.info(f"[COLLECT_URLS] {state['node_id']} — {len(nav_list)} nav elements (3-layer)")

    # Dismiss any dialog left open by execute_funcs — check once before nav loop
    await browser.navigate(target_url)
    await asyncio.sleep(PAGE_LOAD_WAIT_SEC)
    await _dismiss_dialogs()

    for nav in nav_list:
        idx         = nav.get("index")
        label       = nav.get("label", "?")
        safe        = nav.get("safe_to_click", True)
        fingerprint = nav.get("fingerprint", {})
        role        = fingerprint.get("role", "") or "link"

        if idx is None:
            continue
        if not safe or idx in dangerous_indices:
            logger.info(f"[COLLECT_URLS] ⚠️ SKIP dangerous: [{idx}] '{label}'")
            continue

        # Return to target page before each click — wait full PAGE_LOAD_WAIT_SEC for SPA render
        await browser.navigate(target_url)
        await asyncio.sleep(PAGE_LOAD_WAIT_SEC)

        # ── LAYER 1: stable CSS selector (bypasses browser-use index entirely) ──
        css_selector = fingerprint.get("css_selector", "")
        logger.debug(f"[COLLECT_URLS] L1 selector '{label}' → '{css_selector}'")
        click_result = await browser.click_by_selector(css_selector)
        actual_url   = click_result.get("current_url", "")
        navigated    = click_result.get("navigated", False)
        layer_used   = 1

        # L1 fails only on hard error — navigated=False is valid (same-page link or modal)
        l1_failed = click_result.get("status") == "error"

        if l1_failed:
            # ── LAYER 2: fingerprint scoring → fresh element → its css_selector ──
            logger.debug(f"[COLLECT_URLS] L1 failed '{label}' ({click_result.get('error','')}) — L2 fingerprint")
            await browser.navigate(target_url)
            await asyncio.sleep(PAGE_LOAD_WAIT_SEC)
            fresh_state = await browser.get_state()
            best_el     = _find_by_fingerprint(fresh_state.get("elements", []), fingerprint)

            if best_el is not None:
                fresh_sel = best_el.get("css_selector", "")
                if fresh_sel:
                    click_result = await browser.click_by_selector(fresh_sel)
                else:
                    # element found but no stable selector → fall to role click
                    click_result = await browser.click_by_role(
                        best_el.get("role","link"), label)
                actual_url = click_result.get("current_url", "")
                navigated  = click_result.get("navigated", False)
                layer_used = 2

            l2_failed = best_el is None or click_result.get("status") == "error"
            if l2_failed:
                # ── LAYER 3: Playwright accessibility-tree locator ────
                logger.debug(f"[COLLECT_URLS] L2 failed '{label}' — L3 Playwright role")
                await browser.navigate(target_url)
                await asyncio.sleep(PAGE_LOAD_WAIT_SEC)
                click_result = await browser.click_by_role(role, label)
                actual_url   = click_result.get("current_url", "")
                navigated    = click_result.get("navigated", False)
                layer_used   = 3

                if click_result.get("status") == "error":
                    logger.debug(f"[COLLECT_URLS] All 3 layers failed '{label}' — skipping")
                    await asyncio.sleep(0.3)
                    continue

        if navigated and actual_url and actual_url not in seen:
            # Always check for login redirect — session can expire even after prior login
            landed_auth = await _quick_auth_check(actual_url)
            if landed_auth:
                logger.info(f"[COLLECT_URLS] 🔐 Session expired — login at: {actual_url} "
                            f"(was_auth={state.get('is_authenticated')}, "
                            f"done_so_far={list(clicked_labels_done)})")
                inline_ok = await _handle_inline_login(
                    actual_url, state.get("_credential_provider"), target_url)
                if inline_ok:
                    # Force back to target — never trust post-login redirect
                    await browser.navigate(target_url)
                    await asyncio.sleep(PAGE_LOAD_WAIT_SEC)
                    # Re-click using selector from fingerprint (indices useless post-login)
                    post_login_state = await browser.get_state()
                    best_el = _find_by_fingerprint(
                        post_login_state.get("elements", []), fingerprint)
                    fresh_sel = (best_el or {}).get("css_selector", "") or css_selector
                    if fresh_sel:
                        r2 = await browser.click_by_selector(fresh_sel)
                    else:
                        r2 = await browser.click_by_role(role, label)
                    actual_url = r2.get("current_url", "")
                    navigated  = r2.get("navigated", False)

            if actual_url and actual_url not in seen:
                seen.add(actual_url)
                clicked_labels_done.add(label)
                combined.append({
                    "url": actual_url,
                    "action_to_reach": f"clicked '{label}' (idx:{idx}, L{layer_used})"
                })
                logger.info(f"[COLLECT_URLS] ✅ L{layer_used} [{idx}] '{label}' → {actual_url}")

        elif not navigated:
            logger.debug(f"[COLLECT_URLS] [{idx}] '{label}' — DOM change only (no URL change)")

        await asyncio.sleep(0.3)

    logger.info(f"[COLLECT_URLS] {state['node_id']} — {len(combined)} URLs discovered, "
                f"clicked_labels={list(clicked_labels_done)}")
    return {"discovered_urls": combined, "clicked_labels": list(clicked_labels_done), "status": "done"}


async def _dismiss_dialogs() -> bool:
    """
    Dismiss any open modal/dialog before nav clicks begin.
    Called once per page, not per click — zero per-click overhead.
    Returns True if page is clear (no dialog blocking clicks).
    """
    page = await browser.page()
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
    except Exception:
        pass

    try:
        still_open = await page.locator('[role="dialog"]').count() > 0
    except Exception:
        still_open = False

    if still_open:
        # Try clicking a close/cancel button inside the dialog
        for kw in ["Cancel", "Close", "No", "Dismiss", "Got it", "×", "✕"]:
            try:
                btn = page.get_by_role("button", name=kw)
                if await btn.count() > 0:
                    await btn.first.click(timeout=3000)
                    await asyncio.sleep(0.3)
                    break
            except Exception:
                continue
        try:
            still_open = await page.locator('[role="dialog"]').count() > 0
        except Exception:
            still_open = False

    if still_open:
        # Nuclear option: full reload clears all SPA dialog state
        logger.warning("[COLLECT_URLS] ⚠️ Dialog persists — forcing page reload to clear it")
        try:
            await page.reload(wait_until="networkidle", timeout=15000)
            await asyncio.sleep(1.0)
            still_open = await page.locator('[role="dialog"]').count() > 0
        except Exception:
            still_open = True

    if still_open:
        logger.warning("[COLLECT_URLS] ⚠️ Dialog still open even after reload — proceeding anyway")
    return True  # always continue nav clicks regardless; clicks will fail naturally if truly blocked


async def _quick_auth_check(url: str) -> bool:
    """Quick LLM check: is this a login page? Uses page title + URL heuristic first."""
    page = await browser.page()
    title = (await page.title()).lower()
    url_lower = url.lower()
    # Quick heuristic check before burning LLM tokens
    login_keywords = {"login","signin","sign-in","sign_in","log-in","log_in","auth","sso"}
    return any(kw in title or kw in url_lower for kw in login_keywords)


async def _handle_inline_login(login_url: str, cred_provider, return_url: str) -> bool:
    """
    Handle login inline during click exploration.
    Returns True if login succeeded, False if skipped/failed.
    """
    logger.info(f"[INLINE_LOGIN] 🔐 Handling login at {login_url}")

    # Get page state for auth fields
    ps = await browser.get_state()
    inputs = ps.get("inputs", [])
    buttons = ps.get("buttons", [])

    if not inputs:
        logger.warning("[INLINE_LOGIN] No inputs found on login page")
        return False

    # Ask LLM to identify auth fields
    r = await query_llm(_AUTH_PROMPT.format(
        url=login_url, title=ps.get("title",""),
        page_type="login", summary="Login page detected during exploration",
        inputs=format_elements_for_llm(inputs),
        buttons=format_elements_for_llm(buttons[:10])),
        system_prompt=_AUTH_SYS)

    if not r or not r.get("is_auth_page"):
        return False

    auth_fields = r.get("auth_fields", [])

    # HITL: ask for credentials
    if cred_provider and callable(cred_provider):
        creds = await cred_provider(login_url, auth_fields)
    else:
        creds = await _cli_input(login_url, auth_fields)

    if not creds:
        logger.info("[INLINE_LOGIN] Credentials skipped")
        return False

    # Fill credentials
    for key, cd in creds.items():
        idx, val = cd.get("index"), cd.get("value", "")
        if idx is not None:
            await browser.fill(int(idx), val)
            await asyncio.sleep(0.3)

    # Click submit
    submit_idx = r.get("submit_button_index")
    if submit_idx:
        await browser.click(int(submit_idx))
    else:
        # Fallback: press Enter
        page = await browser.page()
        await page.keyboard.press("Enter")

    await asyncio.sleep(2)

    # Verify login worked — did we leave the login page?
    page = await browser.page()
    if page.url != login_url:
        logger.info(f"[INLINE_LOGIN] ✅ Login success → {page.url}")
        return True

    logger.warning("[INLINE_LOGIN] ❌ Still on login page after submit")
    return False

# ─────────────────────────────────────────────────────────
# NODE 9 — FINALIZE
# ─────────────────────────────────────────────────────────
async def finalize_node(state: ExplorationState) -> dict:
    logger.info(f"[FINALIZE] {state['node_id']} ✅ "
                f"navs={len(state.get('navigations',[]))} "
                f"funcs={len(state.get('functionalities',[]))} "
                f"disc={len(state.get('discovered_urls',[]))}")
    st = state.get("status","done")
    return {"status": st if st != "exploring" else "done"}

# ─────────────────────────────────────────────────────────
# CONDITIONAL EDGE ROUTERS
# ─────────────────────────────────────────────────────────
def route_after_navigate(state: ExplorationState) -> str:
    return "finalize" if state.get("status")=="error" else "extract_dom"

def route_after_detect_auth(state: ExplorationState) -> str:
    return "hitl_credentials" if (state.get("is_auth_page") and state.get("auth_status")=="detected") else "execute_funcs"

def route_after_fill_auth(state: ExplorationState) -> str:
    return "extract_dom" if state.get("auth_status")=="credentials_received" else "execute_funcs"

# ─────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────
def _resolve(url, base) -> Optional[str]:
    if not url: return None
    if any(url.startswith(p) for p in ("javascript:","mailto:","tel:")): return None
    # href="#" is NOT skipped — these are JS-driven navigations, handled by click-first
    if url == "#": return None  # only skip bare "#", not "#section"
    return urljoin(base,url) if (url.startswith("/") or not url.startswith("http")) else url

def _order_elems(elems, order):
    if order:
        m = {el.get("index"):el for el in elems}
        return [m[i] for i in order if i in m]
    return sorted(elems, key=lambda e: e.get("index",999))

def _fallback_links(elements, base) -> list:
    """Fallback: identify clickable <a> elements for click-first discovery."""
    return [{"index":el.get("index"),"label":el.get("text","") or el.get("label",""),
             "safe_to_click":True,"priority":"medium","reason":"fallback"}
            for el in elements if el.get("tag")=="a"]
