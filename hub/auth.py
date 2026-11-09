"""Login do hub: senha definida na 1ª execução (só a partir desta máquina), sessão em cookie assinado."""
from __future__ import annotations
import base64, hashlib, hmac, secrets, time
from collections import defaultdict

from . import config

COOKIE = "strathub_sessao"
_falhas: dict[str, list[float]] = defaultdict(list)


def hash_senha(senha: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(salt), 310_000)
    return h.hex(), salt


def senha_definida(cfg: dict) -> bool:
    return bool(cfg.get("senha_hash") and cfg.get("senha_salt"))


def definir_senha(cfg: dict, senha: str) -> None:
    h, s = hash_senha(senha)
    cfg["senha_hash"], cfg["senha_salt"] = h, s
    cfg["segredo_sessao"] = secrets.token_hex(32)  # invalida sessões antigas
    config.save(cfg)


def verificar_senha(cfg: dict, senha: str) -> bool:
    if not senha_definida(cfg):
        return False
    h, _ = hash_senha(senha, cfg["senha_salt"])
    return hmac.compare_digest(h, cfg["senha_hash"])


def bloqueado(ip: str) -> bool:
    agora = time.time()
    _falhas[ip] = [t for t in _falhas[ip] if agora - t < 900]
    return len(_falhas[ip]) >= 8


def registrar_falha(ip: str) -> None:
    _falhas[ip].append(time.time())


def _assinar(cfg: dict, payload: str) -> str:
    return hmac.new(bytes.fromhex(cfg["segredo_sessao"]), payload.encode(), hashlib.sha256).hexdigest()


def criar_sessao(cfg: dict, usuario_id: str = "dono") -> str:
    exp = int(time.time() + float(cfg.get("sessao_dias", 30)) * 86400)
    payload = f"{usuario_id}|{exp}|{secrets.token_hex(8)}"
    return base64.urlsafe_b64encode(f"{payload}|{_assinar(cfg, payload)}".encode()).decode()


def sessao_usuario(cfg: dict, token: str | None) -> str | None:
    """Devolve o id do usuário da sessão ("dono" para o dono do hub) ou None."""
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        payload, sig = raw.rsplit("|", 1)
        if not hmac.compare_digest(sig, _assinar(cfg, payload)):
            return None
        uid, exp = payload.split("|")[:2]
        return uid if int(exp) > time.time() else None
    except Exception:
        return None


def sessao_valida(cfg: dict, token: str | None) -> bool:
    return sessao_usuario(cfg, token) is not None
