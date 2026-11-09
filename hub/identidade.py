"""Identidade repassada aos apps e credenciais app→hub.

• Navegador → hub → app: o proxy remove qualquer cabeçalho X-Hub-* vindo de fora e injeta
  X-Hub-User-Id / -Email / -Name (url-encoded) / -Role / -Ts / -Signature.
  Assinatura = HMAC-SHA256(token_do_app, "id|email|papel|ts"). O app confere com HUB_APP_TOKEN.
  X-Hub-Ambiente: teste | producao. Em ambiente de teste, um MASTER TESTE recebe também
  X-Hub-User-Master = HMAC-SHA256(token_do_app, "master|id|ts") (ferramentas antigas simplesmente ignoram).
• App → hub (servidor a servidor): cabeçalho X-Hub-App-Token: <HUB_APP_TOKEN>.
"""
from __future__ import annotations
import hashlib, hmac, secrets, time
from urllib.parse import quote

from . import config, usuarios


def _segredo(cfg: dict) -> bytes:
    if not cfg.get("segredo_apps"):
        cfg["segredo_apps"] = secrets.token_hex(32)
        config.save(cfg)
    return bytes.fromhex(cfg["segredo_apps"])


def token_app(cfg: dict, app_id: str) -> str:
    return hmac.new(_segredo(cfg), f"app:{app_id}".encode(), hashlib.sha256).hexdigest()


def app_do_token(cfg: dict, token: str, app_ids) -> str | None:
    if not token:
        return None
    for a in app_ids:
        if hmac.compare_digest(token, token_app(cfg, a)):
            return a
    return None


def cabecalhos(cfg: dict, app_id: str, u: dict) -> list[tuple[str, str]]:
    ts = str(int(time.time()))
    base = f"{u['id']}|{u.get('email', '')}|{u.get('papel', '')}|{ts}"
    sig = hmac.new(token_app(cfg, app_id).encode(), base.encode(), hashlib.sha256).hexdigest()
    return [("x-hub-user-id", u["id"]), ("x-hub-user-email", quote(u.get("email", ""))),
            ("x-hub-user-name", quote(u.get("nome", ""))), ("x-hub-user-role", u.get("papel", "")),
            ("x-hub-ts", ts), ("x-hub-signature", sig), ("x-hub-ambiente", usuarios.ambiente(cfg))] + (
        [("x-hub-user-master", hmac.new(token_app(cfg, app_id).encode(), f"master|{u['id']}|{ts}".encode(), hashlib.sha256).hexdigest())]
        if usuarios.eh_master(cfg, u) else [])
