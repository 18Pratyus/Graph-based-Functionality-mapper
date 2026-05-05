# Flow Mapper — Intelligent Web Application Explorer for Pentesting

Flow Mapper is an autonomous BFS-based web crawler built specifically for agentic AI penetration testing workflows. it navigates modern single-page applications, identifies authentication flows, maps all user-reachable paths and functionalities, and exports a complete attack-surface graph — all without writing a single manual test script.

---

## Why Flow Mapper?

Most web crawlers are dumb. They follow links, collect URLs, and stop there. Flow Mapper actually *understands* the application. it uses a large language model to look at the page, figure out what it does, what buttons matter, what forms exist, and what flow a real user would take — then it executes that flow automatically. this is closer to how a senior penetration tester explores an application than any existing open-source tool.

---

## Key Features vs What's Already Out There

| Feature | Flow Mapper | Burp Suite Pro Crawler | OWASP ZAP Spider | Katana / GoSpider |
|---|---|---|---|---|
| LLM-driven page understanding | **YES** | No | No | No |
| Handles SPAs (React, Vue, Angular) | **YES** | Partial | Partial | No |
| Auth-aware exploration (auto login) | **YES** | Manual config | Manual config | No |
| Session expiry re-login | **YES** | No | No | No |
| HITL credential input | **YES** | No | No | No |
| Functional flow execution | **YES** | No | No | No |
| Vision model screenshot analysis | **YES** | No | No | No |
| 3-layer click strategy for SPAs | **YES** | No | No | No |
| Real-time exploration tree (WebSocket) | **YES** | No | No | No |
| Attack surface JSON export | **YES** | Limited | Limited | No |
| Open source + self-hosted LLM support | **YES** | No (paid) | YES | YES |
| BFS depth + node limits | **YES** | YES | YES | YES |

---

## How it Works

Flow Mapper uses a **LangGraph StateGraph** pipeline. each page visited goes through this exact sequence:

```
navigate → extract_dom → llm_analyze → detect_auth
                                            |
                              ┌─────────────┴─────────────┐
                         (auth page)                  (normal page)
                              |                            |
                      hitl_credentials              execute_funcs
                              |                            |
                          fill_auth                  collect_urls
                              |                            |
                         extract_dom  ←─────────      finalize
```

**Step by step:**

1. **Navigate** — browser-use + Playwright opens the URL in a real Chromium browser (headless or headed)
2. **Extract DOM** — browser-use extracts all clickable + interactive elements with stable CSS selectors, aria labels, and fingerprints
3. **LLM Analyze** — sends DOM elements to the main LLM (qwen3 / deepseek / any Ollama model). If input fields are detected, also sends a screenshot to the vision model for extra accuracy
4. **Detect Auth** — separate LLM call checks if this page is a login form with >60% confidence
5. **HITL Credentials** — if auth detected, checks for stored credentials from previous login. if first time, prompts user in terminal (or via credential provider API)
6. **Fill Auth** — fills fields by index, clicks submit, waits for redirect, saves session cookies
7. **Execute Funcs** — LLM-identified functionalities (search forms, modals, data submissions) get executed in sequence. any navigation that results from a form submit gets added to BFS queue
8. **Collect URLs** — 3-layer click strategy runs for every navigation element:
   - L1: stable CSS selector click (fastest, attribute-based, survives DOM rebuilds)
   - L2: fingerprint scoring against fresh DOM (catches SPA re-renders)
   - L3: Playwright accessibility tree locator (last resort for complex SPAs)
9. **Finalize** — node gets written to disk, BFS queue gets updated

---

## Technologies Used

| Layer | Technology |
|---|---|
| Graph orchestration | LangGraph (StateGraph, async nodes) |
| Browser automation | browser-use + Playwright (Chromium) |
| LLM integration | Ollama API (any cloud or local model) |
| Vision analysis | Separate vision model via Ollama |
| HTTP client | httpx (async) |
| Real-time UI | WebSockets (websockets library) |
| UI dashboard | Vanilla HTML/JS (no framework needed) |
| BFS engine | Custom async BFSManager with DOM hash dedup |
| Data export | JSON (per-node + combined + flow mappings) |

---

## What Gets Exported

After each run, results are saved in `results/`:

```
results/
├── summary.json              # run stats, timing, node count
├── exploration_tree.json     # full BFS tree with all relationships
├── all_nodes.json            # every page visited, combined
├── nodes/                    # individual per-page JSON files
│   ├── node_001.json
│   └── ...
└── flows/
    └── flow_mappings.json    # all user-reachable paths from seed URL
```

each node JSON contains: page title, type, summary, all nav elements, all functionalities with input fields + expected values, functionality execution results, discovered child URLs, DOM hash, auth status.

---

## Quick Start

```bash
cd flow_mapper
pip install -r requirements.txt
playwright install chromium

# basic run
python main.py https://target-site.com

# with options
python main.py https://target-site.com --depth 3 --max-nodes 50 --headed

# disable the UI dashboard
python main.py https://target-site.com --no-ui
```

Dashboard opens at `http://localhost:8090` — real-time tree of pages being explored, edges showing how each page was reached, functionality results.

---

## Configuration

All settings in `config.py` or via env vars:

| Variable | Default | Description |
|---|---|---|
| OLLAMA_BASE_URL | https://ollama.com | Ollama server URL |
| OLLAMA_MODEL | qwen3-next:80b-cloud | Main reasoning model |
| VISION_MODEL | gemma4:31b-cloud | Screenshot analysis model |
| MAX_DEPTH | 5 | Max BFS depth |
| MAX_NODES | 100 | Max pages to explore |
| MAX_ACTIONS_PER_PAGE | 20 | Max functionalities executed per page |
| HEADLESS | true | Run browser headless |

---

## Project Structure

```
flow_mapper/
├── main.py                  # entry point, CLI args
├── config.py                # all settings
├── requirements.txt
├── agents/
│   ├── graph_nodes.py       # all LangGraph node functions
│   ├── graph_state.py       # TypedDict state definition
│   └── orchestrator.py      # BFS loop + LangGraph graph builder
├── graph/
│   ├── bfs_manager.py       # async BFS queue with dedup
│   └── tree_builder.py      # exploration tree for WebSocket UI
├── memory/
│   └── node_store.py        # per-node persistence
├── models/
│   ├── llm_client.py        # Ollama async client (JSON + vision)
│   └── prompts.py           # LLM system + user prompts
├── server/
│   └── ws_server.py         # WebSocket server for real-time UI
├── tools/
│   └── browser_session.py   # browser-use + Playwright singleton
└── ui/
    └── index.html           # real-time exploration dashboard
```

---

## Special Things Worth Mentioning

**DOM hash deduplication** — before adding any discovered URL to the BFS queue, Flow Mapper hashes the DOM content. if same DOM hash seen before, skip it. this avoids crawling hundreds of paginated pages that are structurally identical.

**3-layer click strategy** — links in SPAs break index-based clicking when the Vue/React component re-renders. Flow Mapper builds a fingerprint for each nav element (id, href, aria-label, css class tokens) and can locate the same element in a fresh DOM even after full re-render.

**Inline session recovery** — if session expires mid-crawl, the next navigation redirect to login is detected automatically. Flow Mapper re-logins using stored credentials and continues from where it left off — no user intervention needed.

**Dangerous element detection** — LLM is instructed to flag elements like logout buttons, delete actions, social media links that would take the browser off-site. these get skipped in click exploration but are still reported in the output.

**Dual model setup** — main model handles JSON reasoning (page type, nav elements, functionalities). vision model analyzes screenshots when input fields are present, which catches dynamically rendered login pages that DOM text alone might misclassify.

---

## Use Cases in Pentesting

- Attack surface mapping before manual testing
- Identifying all forms and input vectors automatically
- Mapping authenticated vs unauthenticated pages
- Finding hidden functionality only reachable through specific user flows
- Generating a complete list of endpoints for further fuzzing or scanning
- Input to AI-driven vulnerability analysis pipelines

---

## Author

Built as part of the PTaaS (Penetration Testing as a Service) Platform — an end-to-end agentic AI security testing system. Flow Mapper is the reconnaissance and mapping engine that feeds downstream attack simulation and vulnerability analysis agents.
