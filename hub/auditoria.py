"""Auditoria do hub: quem fez o quê (logins, usuários, organização, ferramentas, backups). Uma linha JSON por evento."""
from __future__ import annotations
import json, threading, time

from . import config

ARQ = config.DATA_DIR / "auditoria.jsonl"
_lock = threading.Lock()


def registrar(ator: dict | str | None, acao: str, alvo: str = "", detalhes=None, ip: str = "") -> None:
    quem = ator if isinstance(ator, str) else ((ator or {}).get("email") or (ator or {}).get("nome") or (ator or {}).get("id") or "")
    linha = {"em": time.strftime("%Y-%m-%dT%H:%M:%S"), "ts": time.time(), "por": quem, "acao": acao, "alvo": alvo, "ip": ip,
             "detalhes": detalhes if detalhes is not None else ""}
    with _lock:
        ARQ.parent.mkdir(parents=True, exist_ok=True)
        with ARQ.open("a", encoding="utf-8") as f:
            f.write(json.dumps(linha, ensure_ascii=False) + "\n")


def listar(limite: int = 500, busca: str = "") -> list[dict]:
    if not ARQ.exists():
        return []
    linhas = ARQ.read_text(encoding="utf-8").splitlines()
    out = []
    b = busca.lower().strip()
    for l in reversed(linhas):
        if b and b not in l.lower():
            continue
        try:
            out.append(json.loads(l))
        except ValueError:
            continue
        if len(out) >= limite:
            break
    return out
