"""Configuração do hub (data/hub.config.json). Criada na primeira execução; nada de senha fixa no código."""
from __future__ import annotations
import json, os, secrets, threading
from pathlib import Path

ROOT = Path(os.environ.get("STRATHUB_HOME", Path(__file__).resolve().parent.parent))
APPS_DIR = Path(os.environ.get("STRATHUB_APPS", ROOT / "apps"))
DATA_DIR = Path(os.environ.get("STRATHUB_DATA", ROOT / "data"))
LOGS_DIR = DATA_DIR / "logs"
CONFIG_FILE = DATA_DIR / "hub.config.json"

DEFAULTS = {
    "nome": "STRAT·HUB",
    "subtitulo": "Laboratório pessoal de ferramentas",
    "host": "127.0.0.1",          # 127.0.0.1 = só esta máquina (acesso remoto via Tailscale). "0.0.0.0" = rede local.
    "porta": 8800,
    "porta_inicial_apps": 9100,   # portas internas atribuídas aos apps (não precisam ser abertas no firewall)
    "ocioso_min_padrao": 30,      # apps de processo param após X min sem uso (0 = nunca)
    "inicio_timeout_s": 90,
    "sessao_dias": 30,
    "senha_hash": None,
    "senha_salt": None,
    "segredo_sessao": None,
}

_lock = threading.Lock()


def load() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    if not cfg.get("segredo_sessao"):
        cfg["segredo_sessao"] = secrets.token_hex(32)
        save(cfg)
    return cfg


def save(cfg: dict) -> None:
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, CONFIG_FILE)
