"""
models/prompts.py — Prompt templates for LLM-driven DOM analysis.

LLM classifies interactive elements into navigations vs functionalities.
No hardcoded rules — LLM decides everything.
"""

DOM_ANALYZER_SYSTEM = """You are a web application analyzer. Classify every interactive
element on a page into NAVIGATIONS (lead to new page/URL) or FUNCTIONALITIES
(actions on current page: forms, search, filters, buttons). Respond ONLY with valid JSON."""

DOM_ANALYZER_PROMPT = """Analyze this web page and classify all interactive elements.

PAGE URL: {url}
PAGE TITLE: {title}
HEADINGS: {headings}

INTERACTIVE ELEMENTS:
{elements_text}

Respond with this exact JSON structure:
{{
    "page_summary": "Brief 1-line description of this page",
    "page_type": "login|dashboard|listing|detail|form|search|settings|checkout|other",
    "navigations": [
        {{
            "index": 5,
            "label": "About Us",
            "safe_to_click": true,
            "priority": "high|medium|low",
            "reason": "Main nav link"
        }}
    ],
    "functionalities": [
        {{
            "func_id": "f1",
            "type": "form|search|filter|button|toggle|dropdown|upload",
            "description": "Login form with email and password",
            "elements_involved": [
                {{"index": 1, "role": "input", "field_name": "email", "fill_value": "test@test.com"}},
                {{"index": 2, "role": "input", "field_name": "password", "fill_value": "Test123!"}},
                {{"index": 3, "role": "submit_button", "field_name": "", "fill_value": ""}}
            ],
            "execution_order": [1, 2, 3],
            "expected_result": "redirect_to_new_page|dom_change|api_call|download|modal"
        }}
    ],
    "dangerous_elements": [
        {{
            "index": 10,
            "label": "Logout",
            "reason": "Will destroy session"
        }}
    ]
}}

RULES:
- Every element must be classified as navigation, functionality, OR dangerous
- Elements with href="#" or href="javascript:void(0)" are STILL clickable — they use JS. Classify them normally by their label/text, NOT by their href
- Do NOT use href to predict URLs. We will click each element and observe the actual result
- safe_to_click: set FALSE for logout, sign out, delete account, clear data, reset, unsubscribe
- Group related elements into ONE functionality (e.g., login form fields = 1 func)
- For inputs, suggest realistic test values (email: testuser@example.com, password: Test123!, name: Test User, search: test query)
- priority: high=main nav, medium=secondary, low=footer
- Ignore non-interactive elements (pure text, images, divs)"""

FLOW_NARRATOR_SYSTEM = """You are a technical writer documenting web application flows.
Given raw JSON exploration data, write clear English flow descriptions."""

FLOW_NARRATOR_PROMPT = """Convert this raw exploration data into readable flow descriptions.

DATA:
{json_data}

For each flow path write:
1. Step-by-step actions
2. Data flow (what enters where, goes where)
3. URL transitions
4. Forms and their fields"""


def format_elements_for_llm(elements: list[dict]) -> str:
    """Convert DOM elements to text for LLM. One line per element."""
    lines = []
    for el in elements:
        idx = el.get("index", "?")
        tag = el.get("tag", "?")
        parts = [f"[{idx}] <{tag}>"]
        for key in ("type", "name", "label", "placeholder", "text"):
            val = el.get(key, "")
            if val:
                parts.append(f'{key}="{str(val)[:60]}"')
        if el.get("href"):
            parts.append(f'href="{el["href"][:80]}"')
        if el.get("is_input"):
            parts.append("[INPUT]")
        lines.append(" ".join(parts))
    return "\n".join(lines)
