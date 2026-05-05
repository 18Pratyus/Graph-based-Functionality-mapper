"""
tools/mcp_tools.py — FastMCP Tool Server for browser exploration.

Each tool has clear docstrings so LLM agents understand when/how to use them.
Uses FastMCP Context elicitation for HITL credential requests.
"""
import logging
from fastmcp import FastMCP, Context
from tools.browser_session import browser

logger = logging.getLogger("flow_mapper.mcp")

mcp = FastMCP(
    name="FlowMapperBrowser",
    instructions="""You are a web exploration agent. Systematically explore pages
    by navigating, reading state, filling forms, clicking elements.
    ALWAYS call get_page_state after navigation or clicks.
    When you detect a login page, call request_credentials.""")

@mcp.tool()
async def navigate_to_url(url: str) -> dict:
    """Navigate browser to URL. ALWAYS call get_page_state after this."""
    logger.info(f"[TOOL] navigate: {url}")
    return await browser.navigate(url)

@mcp.tool()
async def get_page_state() -> dict:
    """Get ALL interactive elements with index numbers. Call after every
    navigation or click. Use returned indices with click_element/fill_input."""
    logger.info("[TOOL] get_page_state")
    return await browser.get_state()

@mcp.tool()
async def get_page_text(max_chars: int = 5000) -> dict:
    """Extract visible text from current page. Use to read error messages,
    success banners, or verify content."""
    text = await browser.get_page_text(max_chars)
    page = await browser.page()
    return {"status":"success","url":page.url,"text":text}

@mcp.tool()
async def click_element(index: int) -> dict:
    """Click element by index from get_page_state. Check 'navigated' in response."""
    logger.info(f"[TOOL] click: {index}")
    return await browser.click(index)

@mcp.tool()
async def fill_input(index: int, value: str, press_enter: bool = False) -> dict:
    """Type text into input field by index. Set press_enter=True for search boxes."""
    logger.info(f"[TOOL] fill: [{index}] = '{value[:30]}'")
    return await browser.fill(index, value, press_enter)

@mcp.tool()
async def request_credentials(ctx: Context, page_url: str,
                                fields: list[dict]) -> dict:
    """Ask human operator for login credentials via elicitation.
    Call ONLY when you detect a login/auth page. Never guess credentials.

    Args:
        page_url: URL of the login page
        fields: List of input fields, each with {index, name, type, label}
    """
    logger.info(f"[TOOL] request_credentials for {page_url}")
    descs = [f"  - {f.get('label') or f.get('name','field')} (idx:{f.get('index')})"
             for f in fields]
    msg = (f"🔐 Login detected: {page_url}\n\nFields:\n" + "\n".join(descs)
           + "\n\nProvide as JSON: {{\"field\": \"value\"}} or type 'skip'")
    try:
        resp = await ctx.elicit(message=msg, response_type=str)
        if resp.action == "accept" and resp.data:
            raw = resp.data.strip()
            if raw.lower() == "skip":
                return {"status": "skipped"}
            import json
            try:
                creds = json.loads(raw)
                return {"status": "received", "credentials": creds}
            except:
                return {"status": "received", "credentials": {"password": raw}}
        return {"status": "skipped"}
    except Exception as e:
        logger.error(f"[TOOL] Elicitation error: {e}")
        return {"status": "error", "error": str(e)}

@mcp.tool()
async def take_screenshot() -> dict:
    """Capture screenshot of current viewport as base64 PNG."""
    b64 = await browser.screenshot_b64()
    page = await browser.page()
    return {"status":"success","current_url":page.url,
            "has_screenshot":bool(b64)}
