"""Desliga o hub de forma ordenada (para todos os apps antes de sair)."""
import json, sys, urllib.request
from pathlib import Path

raiz = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(raiz))
from hub import config  # noqa: E402

porta = config.load()["porta"]
try:
    req = urllib.request.Request(f"http://127.0.0.1:{porta}/api/hub/desligar", data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=60)
    print("Hub desligado.")
except Exception as e:
    try:
        import psutil
        for c in psutil.net_connections(kind="tcp"):
            if c.laddr and c.laddr.port == porta and c.status == psutil.CONN_LISTEN and c.pid:
                p = psutil.Process(c.pid)
                for x in p.children(recursive=True) + [p]:
                    x.kill()
                print("Hub encerrado à força (não respondia).")
                break
        else:
            print("O hub não está em execução.")
    except Exception as e2:
        print("Não foi possível desligar:", e, e2)
