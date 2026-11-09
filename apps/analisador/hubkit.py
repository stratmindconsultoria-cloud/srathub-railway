"""hubkit — integração de um app com o STRAT·HUB (sem dependências externas).

Copie este arquivo para a pasta do app. Funciona em dois modos:
  • no hub   (HUB_URL + HUB_APP_TOKEN definidos pelo hub): identidade assinada, Data Core e perfil da organização do hub;
  • isolado  (app rodando sozinho): identidade = None, Data Core local em dados/datacore_local.json,
             organização = config/organizacao.json do app (ou o padrão passado).

Uso:
    import hubkit
    u = hubkit.identidade(request.headers)            # {'id','email','nome','papel'} ou None
    org = hubkit.organizacao(PADRAO)                   # dict
    hubkit.publicar("pmp", "projeto", "PRJ-001", {...}, source_user=u and u["email"])
    for r in hubkit.consultar("origination", "pqq_inbound"): ...
"""
from __future__ import annotations
import hashlib, hmac, json, os, threading, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

HUB_URL = os.environ.get("HUB_URL", "").rstrip("/")
TOKEN = os.environ.get("HUB_APP_TOKEN", "")
APP_ID = os.environ.get("HUB_APP_ID", "")
ROOT_PATH = os.environ.get("HUB_ROOT_PATH", "")
_DIR = Path(os.environ.get("HUBKIT_DIR", Path.cwd()))
_DADOS = _DIR / "dados"      # Data Core local (modo isolado)
_CONFIG = _DIR / "config"    # organizacao.json local (modo isolado)
_lock = threading.Lock()
_cache: dict = {}


class ErroHub(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(f"{status}: {msg}")
        self.status = status


def configurar(dados: str | Path | None = None, config: str | Path | None = None) -> None:
    """Define as pastas usadas no modo isolado (por padrão ./dados e ./config)."""
    global _DADOS, _CONFIG
    if dados:
        _DADOS = Path(dados)
    if config:
        _CONFIG = Path(config)
    _cache.clear()


def no_hub() -> bool:
    return bool(HUB_URL and TOKEN)


def identidade(headers, max_idade_s: int = 600) -> dict | None:
    """Lê e confere a identidade injetada pelo hub. Retorna None se ausente ou inválida."""
    if not TOKEN:
        return None
    g = lambda k: headers.get(k) or headers.get(k.title()) or ""
    uid, email, papel, ts, sig = g("x-hub-user-id"), urllib.parse.unquote(g("x-hub-user-email")), g("x-hub-user-role"), g("x-hub-ts"), g("x-hub-signature")
    if not (uid and ts and sig):
        return None
    try:
        if abs(time.time() - int(ts)) > max_idade_s:
            return None
    except ValueError:
        return None
    esperado = hmac.new(TOKEN.encode(), f"{uid}|{email}|{papel}|{ts}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperado, sig):
        return None
    ambiente = "teste" if g("x-hub-ambiente") == "teste" else "producao"
    msig = g("x-hub-user-master")
    master = bool(msig) and ambiente == "teste" and hmac.compare_digest(
        hmac.new(TOKEN.encode(), f"master|{uid}|{ts}".encode(), hashlib.sha256).hexdigest(), msig)
    return {"id": uid, "email": email, "nome": urllib.parse.unquote(g("x-hub-user-name")) or email or uid, "papel": papel,
            "ambiente": ambiente, "master": master}


def _req(metodo: str, caminho: str, corpo=None, timeout=20):
    dados = json.dumps(corpo, ensure_ascii=False).encode() if corpo is not None else None
    r = urllib.request.Request(HUB_URL + caminho, data=dados, method=metodo,
                               headers={"X-Hub-App-Token": TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("detail", "")
        except Exception:
            msg = str(e)
        raise ErroHub(e.code, msg)


def organizacao(padrao: dict | None = None, ttl: int = 30) -> dict:
    """Perfil da organização (nome, unidades, áreas, vocabulário, marca)."""
    agora = time.time()
    if "org" in _cache and agora - _cache["org"][0] < ttl:
        return _cache["org"][1]
    org = None
    if no_hub():
        try:
            org = _req("GET", "/api/hub/organizacao")
        except Exception:
            org = None
    if org is None:
        local = _CONFIG / "organizacao.json"
        try:
            org = json.loads(local.read_text(encoding="utf-8")) if local.exists() else None
        except Exception:
            org = None
    org = {**(padrao or {}), **(org or {})}
    if padrao and isinstance(padrao.get("vocabulario"), dict):
        org["vocabulario"] = {**padrao["vocabulario"], **(org.get("vocabulario") or {})}
    _cache["org"] = (agora, org)
    return org


# ── pessoas, feriados e ecossistema instalado ───────────────
def _em_cache(chave: str, ttl: int, fn):
    agora = time.time()
    if chave in _cache and agora - _cache[chave][0] < ttl:
        return _cache[chave][1]
    v = fn()
    _cache[chave] = (agora, v)
    return v


def pessoas(ttl: int = 60) -> list[dict]:
    """Pessoas do hub (id, nome, e-mail, papel, localidade, escala_feriado). Fora do hub: lista vazia."""
    if not no_hub():
        return []
    try:
        return _em_cache("pessoas", ttl, lambda: _req("GET", "/api/hub/pessoas"))
    except Exception:
        return []


def liderados(id_hub: str) -> list[dict]:
    """Subordinados diretos de uma pessoa (hierarquia do hub: campo lider_id)."""
    return [p for p in pessoas() if p.get("lider_id") == id_hub and p.get("ativo", True) is not False]


def lider_de(id_hub: str) -> dict | None:
    p = pessoa(id_hub=id_hub)
    return pessoa(id_hub=p["lider_id"]) if p and p.get("lider_id") else None


def notificacoes_lidas(id_hub: str, desde: float = 0) -> list[dict]:
    """Chaves de pendências que a pessoa marcou como lidas no hub (em qualquer ferramenta), depois de `desde`."""
    if not no_hub():
        return []
    try:
        return _req("GET", f"/api/hub/notificacoes/lidas?pessoa={urllib.parse.quote(id_hub)}&desde={float(desde or 0)}") or []
    except Exception:
        return []


def marcar_notificacoes(id_hub: str, chaves: list[str], lida: bool = True) -> None:
    """Marca no hub (vale para a agenda, as ferramentas e o botão S·H)."""
    if no_hub() and chaves:
        try:
            _req("POST", "/api/hub/notificacoes/lidas", {"pessoa": id_hub, "chaves": list(chaves), "lida": lida})
        except Exception:
            pass


def masters() -> list[dict]:
    """MASTER TESTE do hub (só existem em ambiente de teste): recebem todas as pendências."""
    return [p for p in pessoas() if p.get("master_teste") and p.get("ativo", True) is not False]


def pessoa(id_hub: str | None = None, email: str | None = None) -> dict | None:
    for p in pessoas():
        if (id_hub and p.get("id") == id_hub) or (email and (p.get("email") or "").lower() == email.lower()):
            return p
    return None


def _pascoa(ano: int):
    from datetime import date
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e, f = b // 4, b % 4, (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    return date(ano, (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1)


def _nacionais(ano: int) -> list[dict]:
    from datetime import timedelta
    fixos = [("01-01", "Confraternização Universal"), ("04-21", "Tiradentes"), ("05-01", "Dia do Trabalho"), ("09-07", "Independência do Brasil"),
             ("10-12", "Nossa Senhora Aparecida"), ("11-02", "Finados"), ("11-15", "Proclamação da República"),
             ("11-20", "Dia Nacional de Zumbi e da Consciência Negra"), ("12-25", "Natal")]
    p = _pascoa(ano)
    out = [{"data": f"{ano}-{md}", "nome": n, "tipo": "nacional", "conta": True} for md, n in fixos]
    out.append({"data": (p - timedelta(days=2)).isoformat(), "nome": "Sexta-feira Santa", "tipo": "nacional", "conta": True})
    for dl, n in ((-48, "Carnaval (segunda-feira)"), (-47, "Carnaval (terça-feira)"), (-46, "Quarta-feira de Cinzas"), (60, "Corpus Christi")):
        out.append({"data": (p + timedelta(days=dl)).isoformat(), "nome": n, "tipo": "facultativo", "conta": False})
    return out


def feriados(inicio: str, fim: str, localidade: str | None = None, ttl: int = 300) -> list[dict]:
    """Feriados entre duas datas (AAAA-MM-DD) para a localidade. No hub vêm do calendário da organização;
    fora dele, só os nacionais (calculados)."""
    if no_hub():
        try:
            q = urllib.parse.urlencode({"inicio": inicio, "fim": fim, "localidade": localidade or ""})
            return _em_cache(f"fer:{q}", ttl, lambda: _req("GET", "/api/hub/feriados?" + q))
        except Exception:
            pass
    out = []
    for ano in range(int(inicio[:4]), int(fim[:4]) + 1):
        out += [f for f in _nacionais(ano) if inicio <= f["data"] <= fim]
    return sorted(out, key=lambda f: f["data"])


def feriado_em(data: str, localidade: str | None = None) -> dict | None:
    """O feriado que conta naquela data (ou None)."""
    return next((f for f in feriados(data[:10], data[:10], localidade) if f.get("conta")), None)


def agir(app: str, caminho: str, pessoa: dict, corpo: dict | None = None, metodo: str = "POST") -> dict:
    """Executa uma ação numa ferramenta em nome de uma pessoa ({"id_hub": ...} ou {"email": ...}). Só para conectores
    com "agir_em_nome": true no manifesto e autorizados no Admin técnico. Devolve {"status": <http>, "resposta": {...}}."""
    return _req("POST", "/api/hub/agir", {"app": app, "caminho": caminho, "metodo": metodo, "corpo": corpo or {}, "pessoa": pessoa})


def instaladas(ttl: int = 60) -> list[str] | None:
    """Ids das ferramentas instaladas e ativas no hub. None = desconhecido (fora do hub)."""
    if not no_hub():
        return None
    try:
        return [a["id"] for a in _em_cache("inst", ttl, lambda: _req("GET", "/api/hub/instaladas"))]
    except Exception:
        return None


# ── Data Core ────────────────────────────────────────────────
def _local_arq() -> Path:
    return _DADOS / "datacore_local.json"


def _local_ler() -> list:
    try:
        return json.loads(_local_arq().read_text(encoding="utf-8"))
    except Exception:
        return []


def publicar(domain: str, entity_type: str, entity_key: str, payload: dict, period: str = "",
             source_user: str = "", source_version: str = "", expected_version: int | None = None,
             delete: bool = False, source_application: str | None = None) -> dict:
    corpo = {"domain": domain, "entity_type": entity_type, "entity_key": str(entity_key), "period": period or "",
             "payload": payload, "source_application": source_application or APP_ID or "app",
             "source_user": source_user or "", "source_version": source_version or "", "delete": delete}
    if expected_version is not None:
        corpo["expected_version"] = expected_version
    if no_hub():
        return _req("POST", "/api/data-core/records", corpo)
    with _lock:  # modo isolado
        regs = _local_ler()
        r = next((x for x in regs if (x["domain"], x["entity_type"], x["entity_key"], x["period"]) == (domain, entity_type, str(entity_key), period or "")), None)
        if expected_version is not None and (r["version"] if r else 0) != expected_version:
            raise ErroHub(409, "Conflito de versão")
        if r is None:
            r = {"id": len(regs) + 1, "domain": domain, "entity_type": entity_type, "entity_key": str(entity_key), "period": period or "", "version": 0}
            regs.append(r)
        r.update(payload=payload, source_application=corpo["source_application"], source_user=source_user, source_version=source_version,
                 version=r["version"] + 1, deleted=delete, updated_ts=time.time())
        _local_arq().parent.mkdir(parents=True, exist_ok=True)
        _local_arq().write_text(json.dumps(regs, ensure_ascii=False), encoding="utf-8")
        return r


def consultar(domain: str | None = None, entity_type: str | None = None, entity_key: str | None = None,
              period: str | None = None, since: float | None = None, limit: int = 1000) -> list[dict]:
    if no_hub():
        q = {k: v for k, v in {"domain": domain, "entity_type": entity_type, "entity_key": entity_key,
                               "period": period, "since": since, "limit": limit}.items() if v not in (None, "")}
        return _req("GET", "/api/data-core/records?" + urllib.parse.urlencode(q))
    out = [r for r in _local_ler() if not r.get("deleted")
           and (domain is None or r["domain"] == domain) and (entity_type is None or r["entity_type"] == entity_type)
           and (entity_key is None or r["entity_key"] == entity_key) and (period is None or r["period"] == period)
           and (since is None or r.get("updated_ts", 0) > since)]
    return sorted(out, key=lambda r: -r.get("updated_ts", 0))[:limit]


def url_app(app_id: str, caminho: str = "") -> str:
    """URL (relativa ao navegador) de outro app plugado no hub."""
    return f"/apps/{app_id}/{caminho.lstrip('/')}"
