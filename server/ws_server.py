"""
server/ws_server.py — WebSocket + HTTP servers.
WebSocket (8765): pushes tree updates to D3.js dashboard.
HTTP (8080): serves static UI files.
"""
import json, logging, threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from config import WS_HOST, WS_PORT, UI_PORT

logger = logging.getLogger("flow_mapper.server")


class WSServer:
    def __init__(self, tree):
        self.tree = tree
        self._server = None

    async def start(self):
        try:
            import websockets
            async def handler(ws, path=None):
                logger.info("[WS] Client connected")
                self.tree.register_ws_client(ws)
                try:
                    await ws.send(json.dumps(self.tree.get_full_tree(), default=str))
                except Exception as e:
                    logger.error(f"[WS] Initial sync error: {e}")
                try:
                    async for _ in ws: pass
                except Exception: pass
                finally:
                    self.tree.unregister_ws_client(ws)
            self._server = await websockets.serve(handler, WS_HOST, WS_PORT)
            logger.info(f"[WS] ✅ ws://{WS_HOST}:{WS_PORT}")
        except ImportError:
            logger.warning("[WS] websockets not installed — install: pip install websockets")
        except Exception as e:
            logger.error(f"[WS] Start failed: {e}")

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()


def start_ui_server(ui_dir: str):
    """Start HTTP server serving the D3.js dashboard (daemon thread)."""
    class Q(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw): super().__init__(*a, directory=ui_dir, **kw)
        def log_message(self, *a): pass

    try:
        srv = HTTPServer(("0.0.0.0", UI_PORT), Q)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        logger.info(f"[UI] ✅ http://localhost:{UI_PORT}")
    except Exception as e:
        logger.error(f"[UI] Start failed: {e}")
