"""Inicia o hub:  python -m hub [--abrir]"""
import sys, threading, webbrowser
import uvicorn
from . import config


def main():
    cfg = config.load()
    if sys.stdout is None or sys.stderr is None:  # pythonw (início oculto com o Windows): registra em arquivo
        f = open(config.DATA_DIR / "hub.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = f
    url = f"http://localhost:{cfg['porta']}"
    if "--abrir" in sys.argv:
        threading.Timer(1.8, lambda: webbrowser.open(url)).start()
    server = uvicorn.Server(uvicorn.Config("hub.main:app", host=cfg["host"], port=int(cfg["porta"]), proxy_headers=True,
                                           forwarded_allow_ips="127.0.0.1", log_level="warning", ws="auto"))
    from . import main as hub_main
    hub_main.SERVIDOR = server
    server.run()


if __name__ == "__main__":
    main()
