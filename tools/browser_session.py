"""
tools/browser_session.py — Persistent browser-use session singleton.
One browser, one context, shared across all tools and agents.
"""
import asyncio, base64, logging, os, re
from config import HEADLESS, NAVIGATION_TIMEOUT_MS, NETWORK_IDLE_TIMEOUT_MS

logger = logging.getLogger("flow_mapper.browser")

_TAG_ROLE = {"a":"link","button":"button","input":"textbox",
             "textarea":"textbox","select":"combobox","nav":"navigation"}

def _build_css_selector(tag: str, attrs: dict) -> str:
    """
    Build a stable Playwright CSS selector from element attributes.
    Priority: id > data-testid > href > aria-label > name
    Returns empty string if no stable selector can be built.
    """
    el_id = attrs.get("id","")
    if el_id and " " not in el_id and len(el_id) < 60:
        return f"#{el_id}"
    testid = attrs.get("data-testid","")
    if testid:
        return f"[data-testid='{testid.replace(chr(39), chr(92)+chr(39))}']"
    href = attrs.get("href","")
    if href and href not in ("#","","javascript:void(0)","javascript:"):
        safe = href.replace("'","\\'")
        return f"{tag}[href='{safe}']"
    aria = attrs.get("aria-label","")
    if aria:
        safe = aria.replace("'","\\'")
        return f"[aria-label='{safe}']"
    name = attrs.get("name","")
    if name and tag in ("input","select","textarea","button"):
        return f"{tag}[name='{name.replace(chr(39), chr(92)+chr(39))}']"
    return ""

def _el_summary(el) -> dict:
    """Convert browser-use DOMElementNode to JSON-safe dict."""
    attrs = getattr(el, "attributes", {}) or {}
    tag = getattr(el, "tag_name", "")
    text_fn = getattr(el, "get_all_text_till_next_clickable_element", None)
    text = ""
    if text_fn:
        try: text = str(text_fn()).strip()[:80]
        except: pass
    label = (attrs.get("aria-label","") or attrs.get("placeholder","")
             or attrs.get("name","") or attrs.get("id",""))
    el_type = attrs.get("type", "")
    is_input = tag in ("input", "textarea", "select")
    is_pw = el_type == "password" or "password" in (
        attrs.get("name","") + attrs.get("id","")).lower()
    role = attrs.get("role","") or _TAG_ROLE.get(tag, "")
    css_class = " ".join(attrs.get("class","").split())[:120]
    css_selector = _build_css_selector(tag, attrs)
    return {
        "index": getattr(el, "highlight_index", None),
        "tag": tag, "type": el_type, "text": text,
        "label": str(label)[:80], "name": attrs.get("name",""),
        "id": attrs.get("id",""), "placeholder": attrs.get("placeholder",""),
        "href": attrs.get("href",""),
        "role": role, "css_class": css_class, "css_selector": css_selector,
        "is_input": is_input, "is_password": is_pw,
    }

class BrowserSession:
    def __init__(self):
        self._browser = None
        self._context = None
        self._ready = False

    async def initialize(self):
        if self._ready: return
        from browser_use import Browser, BrowserConfig
        from browser_use.browser.context import BrowserContextConfig
        logger.info(f"[BROWSER] Starting (headless={HEADLESS})")
        self._browser = Browser(config=BrowserConfig(
            headless=HEADLESS, disable_security=True,
            extra_chromium_args=["--no-sandbox","--disable-dev-shm-usage",
                                 "--ignore-certificate-errors",
                                 "--disable-blink-features=AutomationControlled"]))
        try: self._context = await self._browser.new_context(config=BrowserContextConfig())
        except TypeError: self._context = await self._browser.new_context()
        self._ready = True
        logger.info("[BROWSER] ✅ Ready")

    async def ctx(self):
        await self.initialize()
        return self._context

    async def page(self):
        return await (await self.ctx()).get_current_page()

    async def navigate(self, url: str) -> dict:
        if not url: return {"status":"error","error":"Empty URL"}
        page = await self.page()
        if page.url.rstrip("/") == url.rstrip("/"):
            try: title = await page.title()
            except: title = ""
            return {"status":"success","url":page.url,"title":title,"navigated":False}
        logger.info(f"[BROWSER] → {url}")
        try:
            await page.goto(url, wait_until="networkidle", timeout=NAVIGATION_TIMEOUT_MS)
        except:
            try: await page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
            except Exception as e: return {"status":"error","error":str(e)}
        await asyncio.sleep(1)
        try: title = await page.title()
        except: title = ""
        return {"status":"success","url":page.url,"title":title,"navigated":True}

    async def get_state(self) -> dict:
        ctx = await self.ctx()
        state = await ctx.get_state()
        page = await self.page()
        smap = getattr(state, "selector_map", {}) or {}
        elements = []
        for idx, node in sorted(smap.items()):
            el = _el_summary(node)
            el["index"] = int(idx)
            elements.append(el)
        headings = await page.evaluate(
            "()=>Array.from(document.querySelectorAll('h1,h2,h3'))"
            ".map(h=>h.textContent.trim()).filter(t=>t).slice(0,15)")
        try: page_title = await page.title()
        except: page_title = ""
        return {"status":"success","url": state.url or page.url,
                "title": page_title,"headings":headings,
                "elements":elements,
                "inputs":[e for e in elements if e.get("is_input")],
                "buttons":[e for e in elements if not e.get("is_input")],
                "total_elements":len(elements)}

    async def click(self, index: int) -> dict:
        ctx = await self.ctx()
        page = await self.page()
        state = await ctx.get_state()
        smap = getattr(state, "selector_map", {}) or {}
        node = smap.get(index)
        if node is None:
            return {"status":"error","error":f"Index {index} not found"}
        prev = page.url
        dialog_info = {}
        async def _on_dlg(d):
            try: dialog_info["message"]=d.message; dialog_info["type"]=d.type
            except: pass
        page.on("dialog", _on_dlg)
        try: await page.evaluate("()=>document.querySelectorAll('a[target]').forEach(a=>a.removeAttribute('target'))")
        except: pass
        try: await ctx._click_element_node(node)
        except Exception as e:
            page.remove_listener("dialog",_on_dlg)
            return {"status":"error","error":str(e)}
        try: await page.wait_for_load_state("networkidle",timeout=NETWORK_IDLE_TIMEOUT_MS)
        except: pass
        page.remove_listener("dialog",_on_dlg)
        return {"status":"success","index":index,"element":_el_summary(node),
                "prev_url":prev,"current_url":page.url,
                "navigated":prev!=page.url,"dialog":dialog_info or None}

    async def fill(self, index: int, value: str, press_enter=False) -> dict:
        ctx = await self.ctx()
        page = await self.page()
        state = await ctx.get_state()
        smap = getattr(state, "selector_map", {}) or {}
        node = smap.get(index)
        if node is None:
            return {"status":"error","error":f"Index {index} not found"}
        try:
            await ctx._input_text_element_node(node, value)
            if press_enter:
                await page.keyboard.press("Enter")
                try: await page.wait_for_load_state("networkidle",timeout=NETWORK_IDLE_TIMEOUT_MS)
                except: pass
        except Exception as e: return {"status":"error","error":str(e)}
        s = _el_summary(node)
        return {"status":"success","index":index,"element":s,
                "value_set":"[HIDDEN]" if s.get("is_password") else value,
                "current_url":page.url}

    async def screenshot_b64(self) -> str:
        try: return await (await self.ctx()).take_screenshot(full_page=False)
        except:
            try:
                png = await (await self.page()).screenshot(full_page=False, type="png")
                return base64.b64encode(png).decode("utf-8")
            except: return ""

    async def get_page_text(self, max_chars=5000) -> str:
        try:
            return (await (await self.page()).evaluate(
                "()=>document.body.innerText||''"))[:max_chars]
        except: return ""

    async def click_by_selector(self, selector: str) -> dict:
        """
        L1 click: stable Playwright CSS selector — bypasses browser-use index system entirely.
        Uses attribute-based selectors (href, id, aria-label) that survive DOM rebuilds.
        Short timeout (8s) so failures escalate quickly to L2.
        """
        if not selector:
            return {"status":"error","error":"no stable selector"}
        page = await self.page()
        prev = page.url
        try:
            try: await page.evaluate("()=>document.querySelectorAll('a[target]').forEach(a=>a.removeAttribute('target'))")
            except: pass
            locator = page.locator(selector).first
            await locator.click(timeout=8000)
            try: await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
            except: pass
            return {"status":"success","current_url":page.url,"navigated":prev!=page.url}
        except Exception as e:
            return {"status":"error","error":str(e)}

    async def click_by_role(self, role: str, name: str) -> dict:
        """Layer 3 fallback: Playwright accessibility-tree locator (survives DOM rebuilds)."""
        page = await self.page()
        prev = page.url
        try:
            # Try exact name first
            locator = page.get_by_role(role, name=name)
            if await locator.count() == 0:
                # Case-insensitive partial match
                locator = page.get_by_role(role, name=re.compile(re.escape(name), re.IGNORECASE))
            if await locator.count() == 0:
                # Last attempt: any role with matching text
                locator = page.get_by_text(re.compile(re.escape(name), re.IGNORECASE))
            if await locator.count() == 0:
                return {"status":"error","error":f"Role locator not found: role={role} name={name}"}
            try: await page.evaluate("()=>document.querySelectorAll('a[target]').forEach(a=>a.removeAttribute('target'))")
            except: pass
            await locator.first.click()
            try: await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
            except: pass
            return {"status":"success","current_url":page.url,"navigated":prev!=page.url}
        except Exception as e:
            return {"status":"error","error":str(e)}

    async def close(self):
        try:
            if self._browser: await self._browser.close()
        except: pass
        self._browser=None; self._context=None; self._ready=False

browser = BrowserSession()
