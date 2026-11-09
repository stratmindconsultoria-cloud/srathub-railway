"""Usuários do hub (além do dono). O hub responde QUEM é o usuário; cada app decide O QUE ele pode fazer.

data/usuarios.json:
{"usuarios": [{"id": "u-ab12cd", "nome": "...", "email": "...", "papel": "usuario"|"admin",
               "apps": ["*"] | ["originacao", ...], "ativo": true, "senha_hash": "...", "senha_salt": "...",
               "criado_em": 1730000000}]}
O dono (senha definida na 1ª execução) é o usuário "dono", papel "dono", acesso total.
"""
from __future__ import annotations
import json, os, re, secrets, threading, time

from . import auth, config

ARQ = config.DATA_DIR / "usuarios.json"
_lock = threading.Lock()
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PAPEIS = {"usuario", "admin"}


def _ler() -> dict:
    if ARQ.exists():
        try:
            return json.loads(ARQ.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"usuarios": []}


def _gravar(d: dict) -> None:
    ARQ.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARQ.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ARQ)


def dono(cfg: dict) -> dict:
    return {"id": "dono", "nome": cfg.get("dono_nome") or "Dono do hub", "email": cfg.get("dono_email") or "",
            "papel": "dono", "apps": ["*"], "ativo": True, "localidade": cfg.get("dono_localidade") or None,
            "escala_feriado": bool(cfg.get("dono_escala_feriado", False)), "master_teste": bool(cfg.get("dono_master_teste", False)),
            "lider_id": cfg.get("dono_lider_id") or None, "jornada_h": _jornada(cfg.get("dono_jornada_h"))}


def _jornada(v) -> float:
    try:
        j = float(v)
    except (TypeError, ValueError):
        return 8.0
    return j if 1 <= j <= 16 else 8.0


def ambiente(cfg: dict) -> str:
    """"teste" ou "producao" (padrão). Declarado só no hub.config.json do servidor, nunca por tela."""
    return "teste" if str(cfg.get("ambiente") or "").strip().lower() == "teste" else "producao"


def eh_master(cfg: dict, u: dict | None) -> bool:
    """MASTER TESTE só vale em ambiente de teste; em produção a marcação é ignorada."""
    return bool(u and u.get("master_teste")) and ambiente(cfg) == "teste"


def publico(u: dict) -> dict:
    return {k: u.get(k) for k in ("id", "nome", "email", "papel", "apps", "ativo", "criado_em", "localidade", "escala_feriado", "master_teste",
                                  "lider_id")} | {"jornada_h": _jornada(u.get("jornada_h"))}


# ── hierarquia: cada pessoa tem (no máximo) um líder direto; nunca ciclos ──────────
def todos(cfg: dict) -> list[dict]:
    return [dono(cfg)] + [publico(u) for u in _ler()["usuarios"]]


def validar_lider(cfg: dict, uid: str, lider_id: str | None) -> str | None:
    """Confere o líder direto de `uid`: precisa existir, não pode ser a própria pessoa nem alguém abaixo dela."""
    lider_id = (lider_id or "").strip() or None
    if not lider_id:
        return None
    mapa = {p["id"]: p for p in todos(cfg)}
    if lider_id not in mapa:
        raise ValueError("Líder direto não encontrado")
    if lider_id == uid:
        raise ValueError("A pessoa não pode ser líder de si mesma")
    atual, vistos = lider_id, set()
    while atual and atual not in vistos:
        if atual == uid:
            raise ValueError("Esse líder está abaixo da pessoa na hierarquia (formaria um ciclo)")
        vistos.add(atual)
        atual = (mapa.get(atual) or {}).get("lider_id")
    return lider_id


def liderados(cfg: dict, uid: str) -> list[dict]:
    """Subordinados diretos (ativos)."""
    return [p for p in todos(cfg) if p.get("lider_id") == uid and p.get("ativo", True) is not False]


def obter(cfg: dict, uid: str | None) -> dict | None:
    if not uid:
        return None
    if uid == "dono":
        return dono(cfg)
    for u in _ler()["usuarios"]:
        if u["id"] == uid:
            return u if u.get("ativo", True) else None
    return None


def listar() -> list[dict]:
    return [publico(u) for u in _ler()["usuarios"]]


def por_email(email: str) -> dict | None:
    e = (email or "").strip().lower()
    return next((u for u in _ler()["usuarios"] if u["email"] == e), None)


def autenticar(email: str, senha: str) -> dict | None:
    u = por_email(email)
    if not u or not u.get("ativo", True):
        return None
    h, _ = auth.hash_senha(senha, u["senha_salt"])
    return u if secrets.compare_digest(h, u["senha_hash"]) else None


def _validar(nome: str, email: str, papel: str, apps) -> tuple[str, str, str, list[str]]:
    nome, email = (nome or "").strip()[:80], (email or "").strip().lower()[:120]
    if not nome:
        raise ValueError("Informe o nome")
    if not EMAIL_RE.match(email):
        raise ValueError("E-mail inválido")
    if papel not in PAPEIS:
        raise ValueError("Papel inválido")
    apps = [str(a) for a in (apps or ["*"]) if str(a).strip()][:200] or ["*"]
    return nome, email, papel, apps


def criar(nome: str, email: str, senha: str, papel: str = "usuario", apps=None, localidade: str | None = None,
          escala_feriado: bool = False) -> dict:
    nome, email, papel, apps = _validar(nome, email, papel, apps)
    if len(senha or "") < 8:
        raise ValueError("A senha precisa ter pelo menos 8 caracteres")
    with _lock:
        d = _ler()
        if any(u["email"] == email for u in d["usuarios"]):
            raise ValueError("Já existe um usuário com esse e-mail")
        h, s = auth.hash_senha(senha)
        u = {"id": "u-" + secrets.token_hex(4), "nome": nome, "email": email, "papel": papel, "apps": apps,
             "ativo": True, "senha_hash": h, "senha_salt": s, "criado_em": int(time.time()),
             "localidade": (localidade or None), "escala_feriado": bool(escala_feriado)}
        d["usuarios"].append(u)
        _gravar(d)
    return publico(u)


def alterar(uid: str, dados: dict) -> dict:
    with _lock:
        d = _ler()
        u = next((x for x in d["usuarios"] if x["id"] == uid), None)
        if not u:
            raise KeyError("Usuário não encontrado")
        nome, email, papel, apps = _validar(dados.get("nome", u["nome"]), dados.get("email", u["email"]),
                                            dados.get("papel", u["papel"]), dados.get("apps", u["apps"]))
        if email != u["email"] and any(x["email"] == email for x in d["usuarios"]):
            raise ValueError("Já existe um usuário com esse e-mail")
        u.update(nome=nome, email=email, papel=papel, apps=apps, ativo=bool(dados.get("ativo", u.get("ativo", True))))
        if "localidade" in dados:
            u["localidade"] = str(dados.get("localidade") or "").strip()[:40] or None
        if "escala_feriado" in dados:
            u["escala_feriado"] = bool(dados["escala_feriado"])
        if "master_teste" in dados:
            u["master_teste"] = bool(dados["master_teste"])
        if "lider_id" in dados:
            u["lider_id"] = dados["lider_id"] or None  # validado antes (validar_lider) por quem chama
        if "jornada_h" in dados:
            u["jornada_h"] = _jornada(dados["jornada_h"])
        if dados.get("senha"):
            if len(dados["senha"]) < 8:
                raise ValueError("A senha precisa ter pelo menos 8 caracteres")
            u["senha_hash"], u["senha_salt"] = auth.hash_senha(dados["senha"])
        _gravar(d)
        return publico(u)


def remover(uid: str) -> list[str]:
    """Remove o usuário; quem respondia a ele passa a responder ao líder dele (a cadeia não se rompe). Devolve os afetados."""
    with _lock:
        d = _ler()
        alvo = next((u for u in d["usuarios"] if u["id"] == uid), None)
        if not alvo:
            raise KeyError("Usuário não encontrado")
        d["usuarios"] = [u for u in d["usuarios"] if u["id"] != uid]
        afetados = []
        for u in d["usuarios"]:
            if u.get("lider_id") == uid:
                u["lider_id"] = alvo.get("lider_id") or None
                afetados.append(u["id"])
        _gravar(d)
        return afetados


def pode_app(u: dict | None, app_id: str) -> bool:
    if not u:
        return False
    if u.get("papel") in ("dono", "admin"):
        return True
    apps = u.get("apps") or ["*"]
    return "*" in apps or app_id in apps
