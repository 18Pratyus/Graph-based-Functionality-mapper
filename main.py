"""
main.py — Flow Mapper Entry Point.

Usage:
  python main.py https://target-site.com
  python main.py https://target-site.com --depth 3 --max-nodes 50 --headed
"""
import argparse, asyncio, logging, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from agents.orchestrator import Orchestrator
from server.ws_server import WSServer, start_ui_server

logger = logging.getLogger("flow_mapper.main")


def parse_args():
    p = argparse.ArgumentParser(description="Flow Mapper — BFS Web Explorer")
    p.add_argument("seed_url", help="Starting URL")
    p.add_argument("--depth",       type=int, default=5)
    p.add_argument("--max-nodes",   type=int, default=100)
    p.add_argument("--max-actions", type=int, default=20)
    p.add_argument("--headed",      action="store_true")
    p.add_argument("--no-ui",       action="store_true")
    p.add_argument("--model",       type=str)
    p.add_argument("--ollama-url",  type=str)
    return p.parse_args()


async def main():
    args = parse_args()

    config.MAX_DEPTH           = args.depth
    config.MAX_NODES           = args.max_nodes
    config.MAX_ACTIONS_PER_PAGE= args.max_actions
    if args.headed:     config.HEADLESS = False
    if args.model:      config.OLLAMA_MODEL = args.model
    if args.ollama_url: config.OLLAMA_BASE_URL = args.ollama_url

    print(f"""
╔══════════════════════════════════════════════╗
║     FLOW MAPPER — BFS Web Explorer           ║
║                                              ║
║  URL:       {args.seed_url[:42]:<42}║
║  Depth:     {args.depth:<42}║
║  MaxNodes:  {args.max_nodes:<42}║
║  Model:     {config.OLLAMA_MODEL:<42}║
║  Dashboard: {'Disabled' if args.no_ui else f'http://localhost:{config.UI_PORT}':<42}║
╚══════════════════════════════════════════════╝""")

    orch = Orchestrator()
    ws_server = None

    if not args.no_ui:
        ws_server = WSServer(orch.tree)
        await ws_server.start()
        ui_dir = os.path.join(os.path.dirname(__file__), "ui")
        start_ui_server(ui_dir)
        logger.info(f"[MAIN] Dashboard: http://localhost:{config.UI_PORT}")
        logger.info(f"[MAIN] WebSocket: ws://localhost:{config.WS_PORT}")

    try:
        await orch.run(args.seed_url)
    except KeyboardInterrupt:
        logger.info("[MAIN] Interrupted — stopping...")
        await orch.stop()
    except Exception as e:
        logger.error(f"[MAIN] Fatal: {e}")
        import traceback; traceback.print_exc()
    finally:
        if ws_server: await ws_server.stop()

    print(f"""
╔══════════════════════════════════════════════╗
║         EXPLORATION COMPLETE                 ║
║                                              ║
║  results/summary.json                        ║
║  results/nodes/        (per-node JSONs)      ║
║  results/flows/        (flow mappings)       ║
║  results/exploration_tree.json               ║
║  memory/nodes/         (full DOM backups)    ║
╚══════════════════════════════════════════════╝""")


if __name__ == "__main__":
    asyncio.run(main())
