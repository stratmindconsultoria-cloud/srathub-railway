"""Conector Microsoft — liga o ecossistema ao Microsoft 365 (Outlook/Teams), sem dependência: sem ele, nada muda.

• Conta Microsoft por pessoa (login por código de dispositivo: o hub mostra um código e a pessoa confirma em
  microsoft.com/devicelogin). O token fica cifrado no servidor; a pessoa desconecta quando quiser.
• Calendário nos dois sentidos: os compromissos da StratAgenda (agenda/compromisso) viram eventos no Outlook/Teams;
  os eventos do Outlook entram na agenda como "ocupado" (agenda/ocupado). Eventos criados pelo conector não voltam.
• Aprovações no Teams: pendências do tipo ação com botões (agenda/pendencia) viram aprovações no app Aprovações do
  Teams, com as mesmas respostas. A primeira resposta, em qualquer canal, vale: respondeu no Teams → o conector executa a
  ação na ferramenta de origem em nome de quem respondeu (pelo hub: a origem confere a permissão e audita); resolveu na
  agenda/ferramenta → a aprovação do Teams é cancelada.
A API de aprovações da Microsoft é beta e só aceita acesso delegado: as aprovações saem em nome da "conta emissora".
"""
from __future__ import annotations
import hashlib, json, os, threading, time, urllib.error, urllib.parse, urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException

import hubkit
from appkit import Base, agora, dump, load

BASE_DIR = Path(__file__).resolve().parent
VERSAO = "1.3.0"
GRAPH = os.environ.get("MS_GRAPH_URL", "https://graph.microsoft.com")
LOGIN = os.environ.get("MS_LOGIN_URL", "https://login.microsoftonline.com")
ESCOPOS_BASE = "offline_access User.Read User.ReadBasic.All Calendars.ReadWrite ApprovalSolution.ReadWrite"
ESCOPO_RESPOSTA = "ApprovalSolutionResponse.ReadWrite"  # responder pela agenda às aprovações criadas no Teams
# permissões opcionais: cada grupo liga um recurso; sem o consentimento dele, o resto continua funcionando
OPCIONAIS = {"resposta": ESCOPO_RESPOSTA, "email": "Mail.Send", "teams_feed": "TeamsActivity.Send",
             "canal": "Team.ReadBasic.All Channel.ReadBasic.All ChannelMessage.Send"}
ESCOPOS = ESCOPOS_BASE + " " + " ".join(OPCIONAIS.values())
ESCOPOS_LOGIN = ESCOPOS_BASE + " " + ESCOPO_RESPOSTA  # o login pede o essencial; o resto vem do consentimento do administrador
SCHEMA = """
CREATE TABLE IF NOT EXISTS ajustes(chave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS contas(id_hub TEXT PRIMARY KEY, nome TEXT, email_hub TEXT, ms_id TEXT, ms_upn TEXT, ms_nome TEXT, refresh TEXT,
  conectado_em TEXT, ultimo_uso TEXT, erro TEXT, ms_emails TEXT, confirmada_por TEXT, confirmada_em TEXT);
CREATE TABLE IF NOT EXISTS dispositivos(id_hub TEXT PRIMARY KEY, device_code TEXT, user_code TEXT, verification_uri TEXT, expira REAL, intervalo INTEGER);
CREATE TABLE IF NOT EXISTS eventos(chave TEXT PRIMARY KEY, id_hub TEXT NOT NULL, event_id TEXT NOT NULL, hash TEXT, em TEXT);
CREATE TABLE IF NOT EXISTS ocupados(chave TEXT PRIMARY KEY, id_hub TEXT NOT NULL, hash TEXT, em TEXT);
CREATE TABLE IF NOT EXISTS teams(chave TEXT PRIMARY KEY, item_id TEXT NOT NULL, id_hub TEXT NOT NULL, dados TEXT NOT NULL, em TEXT);
CREATE TABLE IF NOT EXISTS aprovacoes(pendencia TEXT PRIMARY KEY, app TEXT, titulo TEXT, approval_id TEXT, operacao TEXT, emissor TEXT,
  aprovadores TEXT, acoes TEXT, estado TEXT NOT NULL, resposta TEXT, respondido_por TEXT, comentario TEXT, resultado TEXT, erro TEXT,
  criada_em TEXT, atualizado_em TEXT);
"""
PERFIS = {"admin": "Administrador da integração", "usuario": "Usuário"}
DEMO = [("u-demo-adm", "Administrador Demo", "admin", None), ("u-demo-usu", "Usuário Demo", "usuario", None)]
CONFIG = {"tenant": "organizations", "client_id": "", "calendario": True, "aprovacoes": True, "dias_calendario": 14, "rotulos": {},
          "notif_email": True, "notif_teams": True, "notif_canal": True}
B = Base("conector_microsoft", "Conector Microsoft", VERSAO, BASE_DIR, SCHEMA, PERFIS, admin="admin", padrao="usuario", demo=DEMO, config_padrao=CONFIG)
_tokens: dict[str, tuple[str, float]] = {}  # id_hub → (access token, expira)
_sem_resposta: set[str] = set()             # contas cujo consentimento ainda não inclui responder aprovações
_grupos: dict[str, tuple[set, float]] = {}  # id_hub → (grupos opcionais liberados, quando conferiu)
_lk = threading.Lock()


@asynccontextmanager
async def _vida(_app):
    B.iniciar()
    with B.con() as c:  # bancos da 1.1: colunas novas
        cols = {r[1] for r in c.execute("PRAGMA table_info(contas)")}
        for col in ("ms_emails", "confirmada_por", "confirmada_em"):
            if col not in cols:
                c.execute(f"ALTER TABLE contas ADD COLUMN {col} TEXT")
    yield

app = FastAPI(title="Conector Microsoft", version=VERSAO, lifespan=_vida)


def ajustes(c=None) -> dict:
    """Configuração: arquivo de implantação (config/conector_microsoft.json) + o que o administrador salvou na tela."""
    cfg = {k: B.config().get(k) for k in ("tenant", "client_id", "calendario", "aprovacoes", "dias_calendario", "notif_email", "notif_teams", "notif_canal")}
    cfg["canais"], cfg["endereco_publico"], cfg["teams_app_id"] = {}, "", ""
    cfg["emissor"] = ""
    if c is None:
        with B.con() as c2:
            return ajustes(c2)
    for r in c.execute("SELECT * FROM ajustes"):
        cfg[r["chave"]] = load(r["valor"], r["valor"])
    return cfg


B.montar(app, lambda: {"escopos": ESCOPOS.split(), "configurado": bool(ajustes().get("client_id"))})


# ───────────────────────────────────────────────── HTTP com a Microsoft (substituível nos testes)
class ErroMS(Exception):
    def __init__(self, status: int, codigo: str, msg: str):
        super().__init__(f"{status} {codigo}: {msg}")
        self.status, self.codigo, self.msg = status, codigo, msg


def _http(metodo: str, url: str, json_corpo=None, form: dict | None = None, token: str | None = None, cabecalhos: dict | None = None) -> tuple[int, dict, dict]:
    """(status, corpo json, cabeçalhos). Erros HTTP também voltam como (status, corpo)."""
    h = {"Accept": "application/json", **(cabecalhos or {})}
    dados = None
    if form is not None:
        dados = urllib.parse.urlencode(form).encode(); h["Content-Type"] = "application/x-www-form-urlencoded"
    elif json_corpo is not None:
        dados = json.dumps(json_corpo).encode(); h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=dados, method=metodo, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            bruto = r.read()
            return r.status, (json.loads(bruto) if bruto else {}), dict(r.headers)
    except urllib.error.HTTPError as e:
        bruto = e.read()
        try:
            corpo = json.loads(bruto) if bruto else {}
        except ValueError:
            corpo = {"error": {"message": bruto.decode(errors="replace")[:300]}}
        return e.code, corpo, dict(e.headers or {})


def _erro(st: int, corpo: dict) -> ErroMS:
    e = corpo.get("error")
    if isinstance(e, dict):
        return ErroMS(st, e.get("code") or "erro", e.get("message") or "")
    return ErroMS(st, str(e or "erro"), corpo.get("error_description") or "")


# ───────────────────────────────────────────────── tokens (cifrados no servidor)
def _cifra():
    from cryptography.fernet import Fernet
    arq = B.dados / "chave_tokens.key"
    if not arq.exists():
        B.dados.mkdir(parents=True, exist_ok=True)
        arq.write_bytes(Fernet.generate_key())
        try:
            os.chmod(arq, 0o600)
        except OSError:
            pass
    return Fernet(arq.read_bytes())


def _guardar(s: str) -> str:
    return _cifra().encrypt(s.encode()).decode()


def _ler(s: str) -> str:
    return _cifra().decrypt(s.encode()).decode()


def _url_token(cfg: dict) -> str:
    return f"{LOGIN}/{cfg.get('tenant') or 'organizations'}/oauth2/v2.0/token"


def token_de(c, id_hub: str) -> str:
    """Access token da pessoa (renova pelo refresh token; a Microsoft devolve um refresh novo, que substitui o antigo)."""
    with _lk:
        t = _tokens.get(id_hub)
        if t and t[1] > time.time() + 60:
            return t[0]
    conta = c.execute("SELECT * FROM contas WHERE id_hub=?", (id_hub,)).fetchone()
    if not conta or not conta["refresh"]:
        raise ErroMS(401, "sem_conta", "Conta Microsoft não conectada")
    cfg = ajustes(c)
    pedir = lambda escopos: _http("POST", _url_token(cfg), form={"client_id": cfg["client_id"], "grant_type": "refresh_token", "scope": escopos,
                                                                  "refresh_token": _ler(conta["refresh"])})
    sem_consent = lambda st_, r_: st_ != 200 and ("65001" in str(r_.get("error_description")) or r_.get("error") in ("invalid_scope", "consent_required"))
    g = _grupos.get(id_hub)
    if g and time.time() - g[1] < 3600 and g[0] != set(OPCIONAIS):
        st, r, _ = pedir(ESCOPOS_BASE + "".join(" " + OPCIONAIS[k] for k in sorted(g[0])))
    else:
        st, r, _ = pedir(ESCOPOS)
        if st == 200:
            _grupos[id_hub] = (set(OPCIONAIS), time.time())
        elif sem_consent(st, r):  # alguma permissão ainda sem consentimento: descobre quais existem e segue com elas
            ok = set()
            for k, esc in OPCIONAIS.items():
                if pedir(ESCOPOS_BASE + " " + esc)[0] == 200:
                    ok.add(k)
            _grupos[id_hub] = (ok, time.time())
            st, r, _ = pedir(ESCOPOS_BASE + "".join(" " + OPCIONAIS[k] for k in sorted(ok)))
    if st == 200:
        (_sem_resposta.discard if "resposta" in _grupos.get(id_hub, (set(OPCIONAIS), 0))[0] else _sem_resposta.add)(id_hub)
    if st != 200:
        e = _erro(st, r)
        c.execute("UPDATE contas SET erro=? WHERE id_hub=?", ("Reconecte a conta Microsoft (" + e.codigo + ")", id_hub))
        raise e
    with _lk:
        _tokens[id_hub] = (r["access_token"], time.time() + int(r.get("expires_in") or 3600))
    c.execute("UPDATE contas SET refresh=COALESCE(?,refresh), ultimo_uso=?, erro=NULL WHERE id_hub=?",
              (_guardar(r["refresh_token"]) if r.get("refresh_token") else None, agora(), id_hub))
    return r["access_token"]


def graph(c, id_hub: str, metodo: str, caminho: str, corpo=None, versao: str = "v1.0", cabecalhos: dict | None = None) -> tuple[int, dict, dict]:
    st, r, h = _http(metodo, f"{GRAPH}/{versao}{caminho}", json_corpo=corpo, token=token_de(c, id_hub), cabecalhos=cabecalhos)
    if st >= 400:
        raise _erro(st, r)
    return st, r, h


def _id_hub(u: dict) -> str:
    return u["id"][2:] if u["id"].startswith("h-") else u["id"]


# ───────────────────────────────────────────────── conta Microsoft da pessoa
def _confere(r) -> bool:
    """A conta conectada é da pessoa? Confere quando o e-mail do cadastro no hub é um dos endereços da conta Microsoft
    (e-mail, login ou aliases) ou quando o administrador confirmou. Só conta conferida recebe aprovações pela conexão."""
    if not r:
        return False
    if r["confirmada_por"]:
        return True
    e = (r["email_hub"] or "").strip().lower()
    return bool(e) and e in [x.lower() for x in load(r["ms_emails"], []) or [r["ms_upn"] or ""]]


def _conta_publica(r) -> dict | None:
    return ({k: r[k] for k in ("id_hub", "nome", "email_hub", "ms_upn", "ms_nome", "conectado_em", "ultimo_uso", "erro", "confirmada_por", "confirmada_em")}
            | {"confere": _confere(r)}) if r else None


def _ms_emails(me: dict) -> list[str]:
    out = []
    for x in [me.get("mail"), me.get("userPrincipalName"), *[str(p).split(":", 1)[-1] for p in me.get("proxyAddresses") or []], *(me.get("otherMails") or [])]:
        if x and "@" in str(x) and str(x).lower() not in out:
            out.append(str(x).lower())
    return out


@app.get("/api/conta")
def minha_conta(x_usuario: str | None = Header(None)):
    with B.con() as c:
        u = B.usuario(c, x_usuario)
        r = c.execute("SELECT * FROM contas WHERE id_hub=?", (_id_hub(u),)).fetchone()
        cfg = ajustes(c)
        return {"conta": _conta_publica(r), "configurado": bool(cfg.get("client_id")), "calendario": cfg.get("calendario"),
                "aprovacoes": cfg.get("aprovacoes"), "emissor": cfg.get("emissor") == _id_hub(u)}


@app.post("/api/conta/conectar")
def conectar(x_usuario: str | None = Header(None)):
    """Começa o login por código: devolve o código que a pessoa digita em microsoft.com/devicelogin."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); cfg = ajustes(c)
        if not cfg.get("client_id"):
            raise HTTPException(409, "O administrador ainda não informou o ID do aplicativo (Configuração).")
        st, r, _ = _http("POST", f"{LOGIN}/{cfg.get('tenant') or 'organizations'}/oauth2/v2.0/devicecode", form={"client_id": cfg["client_id"], "scope": ESCOPOS_LOGIN})
        if st != 200:
            e = _erro(st, r)
            raise HTTPException(502, f"A Microsoft recusou o início do login: {e.codigo} {e.msg[:200]}")
        c.execute("INSERT OR REPLACE INTO dispositivos(id_hub,device_code,user_code,verification_uri,expira,intervalo) VALUES (?,?,?,?,?,?)",
                  (_id_hub(u), r["device_code"], r["user_code"], r.get("verification_uri") or "https://microsoft.com/devicelogin",
                   time.time() + int(r.get("expires_in") or 900), int(r.get("interval") or 5)))
        return {"codigo": r["user_code"], "endereco": r.get("verification_uri") or "https://microsoft.com/devicelogin",
                "expira_em_s": int(r.get("expires_in") or 900), "intervalo_s": int(r.get("interval") or 5)}


@app.post("/api/conta/verificar")
def verificar(x_usuario: str | None = Header(None)):
    """Consulta se a pessoa já confirmou o código. {"pendente": true} enquanto não confirmou."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); cfg = ajustes(c); idh = _id_hub(u)
        d = c.execute("SELECT * FROM dispositivos WHERE id_hub=?", (idh,)).fetchone()
        if not d:
            raise HTTPException(404, "Comece pelo botão Conectar.")
        if time.time() > d["expira"]:
            c.execute("DELETE FROM dispositivos WHERE id_hub=?", (idh,))
            raise HTTPException(410, "O código expirou. Comece de novo.")
        st, r, _ = _http("POST", _url_token(cfg), form={"client_id": cfg["client_id"], "device_code": d["device_code"],
                                                         "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        if st != 200:
            e = _erro(st, r)
            if e.codigo in ("authorization_pending", "slow_down"):
                return {"pendente": True}
            c.execute("DELETE FROM dispositivos WHERE id_hub=?", (idh,))
            msg = {"authorization_declined": "Login recusado na Microsoft.", "expired_token": "O código expirou. Comece de novo."}.get(e.codigo)
            raise HTTPException(400, msg or f"A Microsoft recusou: {e.codigo} {e.msg[:200]}")
        with _lk:
            _tokens[idh] = (r["access_token"], time.time() + int(r.get("expires_in") or 3600))
        st2, me, _ = _http("GET", f"{GRAPH}/v1.0/me?$select=id,displayName,userPrincipalName,mail,proxyAddresses,otherMails", token=r["access_token"])
        if st2 != 200:
            raise HTTPException(502, "Login feito, mas não foi possível ler o perfil Microsoft.")
        c.execute("DELETE FROM dispositivos WHERE id_hub=?", (idh,))
        outro = c.execute("SELECT nome FROM contas WHERE ms_id=? AND id_hub<>?", (me.get("id"), idh)).fetchone()
        teste = ((B._ident.get() or {}).get("ambiente") == "teste")
        if outro and not teste:  # cada conta Microsoft é de uma pessoa só (em ambiente de teste a mesma conta pode servir a mais de um usuário)
            with _lk:
                _tokens.pop(idh, None)
            raise HTTPException(409, f"Esta conta Microsoft já está conectada por {outro['nome']}. Cada pessoa conecta a própria conta.")
        c.execute("INSERT OR REPLACE INTO contas(id_hub,nome,email_hub,ms_id,ms_upn,ms_nome,refresh,conectado_em,ultimo_uso,erro,ms_emails,confirmada_por,confirmada_em) "
                  "VALUES (?,?,?,?,?,?,?,?,?,NULL,?,NULL,NULL)",
                  (idh, u["nome"], u.get("email") or "", me.get("id"), me.get("mail") or me.get("userPrincipalName"), me.get("displayName"),
                   _guardar(r.get("refresh_token") or ""), agora(), agora(), dump(_ms_emails(me))))
        c.execute("DELETE FROM aprovacoes WHERE estado IN ('sem_aprovador','erro')")
        B.auditar(c, u, "Conta Microsoft conectada", me.get("mail") or me.get("userPrincipalName") or "")
        return {"pendente": False, "conta": _conta_publica(c.execute("SELECT * FROM contas WHERE id_hub=?", (idh,)).fetchone())}


@app.post("/api/contas/{id_hub}/confirmar")
def confirmar_conta(id_hub: str, x_usuario: str | None = Header(None)):
    """O administrador confirma que a conta Microsoft conectada é mesmo da pessoa (quando o e-mail do hub não bate)."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "admin")
        r = c.execute("SELECT * FROM contas WHERE id_hub=?", (id_hub,)).fetchone()
        if not r:
            raise HTTPException(404, "Conta não encontrada.")
        c.execute("UPDATE contas SET confirmada_por=?, confirmada_em=? WHERE id_hub=?", (u["nome"], agora(), id_hub))
        c.execute("DELETE FROM aprovacoes WHERE estado IN ('sem_aprovador','erro')")
        B.auditar(c, u, "Conta Microsoft confirmada pelo administrador", r["ms_upn"] or "", {"pessoa": r["nome"], "email_hub": r["email_hub"]})
        return _conta_publica(c.execute("SELECT * FROM contas WHERE id_hub=?", (id_hub,)).fetchone())


@app.delete("/api/conta")
def desconectar(x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); idh = _id_hub(u)
        c.execute("DELETE FROM contas WHERE id_hub=?", (idh,)); c.execute("DELETE FROM eventos WHERE id_hub=?", (idh,))
        _tokens.pop(idh, None)
        B.auditar(c, u, "Conta Microsoft desconectada", "")
        return {"ok": True}


@app.post("/api/conta/testar")
def testar(x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario)
        try:
            _, me, _ = graph(c, _id_hub(u), "GET", "/me?$select=displayName,userPrincipalName")
            _, ev, _ = graph(c, _id_hub(u), "GET", "/me/events?$top=1&$select=id")
        except ErroMS as e:
            raise HTTPException(502, f"Microsoft: {e.codigo} {e.msg[:200]}")
        return {"ok": True, "nome": me.get("displayName"), "conta": me.get("userPrincipalName"), "calendario": "ok"}


# ───────────────────────────────────────────────── configuração (administrador)
def _salvar_ajuste(c, k: str, v) -> None:
    c.execute("INSERT OR REPLACE INTO ajustes(chave,valor) VALUES (?,?)", (k, dump(v)))


def _config(c) -> dict:
    dup = [r["ms_upn"] for r in c.execute("SELECT ms_upn FROM contas WHERE ms_id IS NOT NULL GROUP BY ms_id HAVING COUNT(*) > 1")]  # só em ambiente de teste
    cfg_ = ajustes(c); em_ = _emissor(c, cfg_)
    perm = sorted(_grupos_de(c, em_)) if em_ else []
    return {**ajustes(c), "contas": [_conta_publica(r) for r in c.execute("SELECT * FROM contas ORDER BY nome")], "escopos": ESCOPOS.split(), "duplicadas": dup, "permissoes_liberadas": perm,
            "opcionais": {k: v for k, v in OPCIONAIS.items()},
            "no_hub": hubkit.no_hub()}


@app.get("/api/config")
def ver_config(x_usuario: str | None = Header(None)):
    with B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "admin")
        return _config(c)


@app.put("/api/config")
def salvar_config(corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "admin")
        if "client_id" in corpo:
            v = str(corpo["client_id"] or "").strip()
            if v and not (len(v) == 36 and v.count("-") == 4):
                raise HTTPException(400, "ID do aplicativo inválido (formato 00000000-0000-0000-0000-000000000000).")
            _salvar_ajuste(c, "client_id", v)
        if "tenant" in corpo:
            _salvar_ajuste(c, "tenant", str(corpo["tenant"] or "").strip() or "organizations")
        for k in ("calendario", "aprovacoes", "notif_email", "notif_teams", "notif_canal"):
            if k in corpo:
                _salvar_ajuste(c, k, bool(corpo[k]))
        if "endereco_publico" in corpo:
            e = str(corpo["endereco_publico"] or "").strip().rstrip("/")
            if e and not e.startswith("https://"):
                raise HTTPException(400, "O endereço público do hub precisa ser https:// (o Teams só abre endereços seguros).")
            _salvar_ajuste(c, "endereco_publico", e)
        if "canais" in corpo:  # ferramenta → canal do Teams que recebe os alertas de equipe
            canais = {}
            for app_id, v in (corpo["canais"] or {}).items():
                if v and v.get("team_id") and v.get("channel_id"):
                    canais[str(app_id)] = {"team_id": str(v["team_id"]), "channel_id": str(v["channel_id"]), "nome": str(v.get("nome") or "")[:200]}
            _salvar_ajuste(c, "canais", canais)
        if "dias_calendario" in corpo:
            _salvar_ajuste(c, "dias_calendario", max(1, min(60, int(corpo["dias_calendario"] or 14))))
        if "emissor" in corpo:
            if corpo["emissor"] and not c.execute("SELECT 1 FROM contas WHERE id_hub=?", (corpo["emissor"],)).fetchone():
                raise HTTPException(400, "A conta emissora precisa estar conectada.")
            _salvar_ajuste(c, "emissor", corpo["emissor"] or "")
        B.auditar(c, u, "Configuração do Conector Microsoft salva", "", {k: v for k, v in corpo.items()})
        return _config(c)


@app.get("/api/atividade")
def atividade(x_usuario: str | None = Header(None)):
    with B.con() as c:
        u = B.usuario(c, x_usuario)
        meu = _id_hub(u)
        filtro = "" if u["perfil"] == "admin" else " WHERE id_hub=?"
        arg = () if u["perfil"] == "admin" else (meu,)
        return {"aprovacoes": [dict(r) | {"aprovadores": load(r["aprovadores"], []), "acoes": load(r["acoes"], [])}
                               for r in c.execute("SELECT * FROM aprovacoes ORDER BY atualizado_em DESC LIMIT 200")] if u["perfil"] == "admin" else [],
                "sincronizacao": ajustes(c).get("ultima_sincronizacao") if u["perfil"] == "admin" else None,
                "eventos": c.execute("SELECT COUNT(*) FROM eventos" + filtro, arg).fetchone()[0],
                "ocupados": c.execute("SELECT COUNT(*) FROM ocupados" + filtro, arg).fetchone()[0]}


# ───────────────────────────────────────────────── calendário
def _utc(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00")[:32])
    except ValueError:
        try:
            d = datetime.fromisoformat(str(s)[:19])
        except ValueError:
            return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _ms_dt(d: datetime) -> dict:
    return {"dateTime": d.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"}


def _h(*partes) -> str:
    return hashlib.sha1(json.dumps(partes, ensure_ascii=False, default=str).encode()).hexdigest()


def _minha(p: dict, conta) -> bool:
    pe = p.get("pessoa") or {}
    return str(pe.get("id_hub") or "") == conta["id_hub"] or (bool(pe.get("email")) and str(pe["email"]).lower() in
                                                           {(conta["email_hub"] or "").lower(), (conta["ms_upn"] or "").lower()})


def _sync_calendario(c, conta, compromissos: list[dict], cfg: dict) -> dict:
    idh, n = conta["id_hub"], {"criados": 0, "alterados": 0, "removidos": 0, "ocupados": 0}
    # 1) agenda → Outlook
    meus = {r["entity_key"]: r.get("payload") or {} for r in compromissos if _minha(r.get("payload") or {}, conta)}
    atuais = {r["chave"]: r for r in c.execute("SELECT * FROM eventos WHERE id_hub=?", (idh,))}
    for chave, p in meus.items():
        ini, fim = _utc(p.get("inicio")), _utc(p.get("fim"))
        ativo = p.get("status", "ativo") == "ativo" and ini and fim
        ev = atuais.get(chave)
        if not ativo:
            continue
        h = _h(p.get("titulo"), p.get("inicio"), p.get("fim"))
        corpo = {"subject": (("🔒 " if p.get("travado") else "") + (p.get("titulo") or "Compromisso"))[:250], "start": _ms_dt(ini), "end": _ms_dt(fim),
                 "showAs": "busy", "isReminderOn": False, "categories": ["STRAT·HUB"],
                 "body": {"contentType": "text", "content": "Compromisso da StratAgenda (STRAT·HUB). Para mudar, use a agenda do hub."}}
        if not ev:
            _, r, _ = graph(c, idh, "POST", "/me/events", corpo)
            c.execute("INSERT OR REPLACE INTO eventos(chave,id_hub,event_id,hash,em) VALUES (?,?,?,?,?)", (chave, idh, r["id"], h, agora())); n["criados"] += 1
        elif ev["hash"] != h:
            try:
                graph(c, idh, "PATCH", f"/me/events/{ev['event_id']}", corpo)
            except ErroMS as e:
                if e.status != 404:
                    raise
                _, r, _ = graph(c, idh, "POST", "/me/events", corpo)  # apagado no Outlook: recria
                c.execute("UPDATE eventos SET event_id=? WHERE chave=?", (r["id"], chave))
            c.execute("UPDATE eventos SET hash=?, em=? WHERE chave=?", (h, agora(), chave)); n["alterados"] += 1
    for chave, ev in atuais.items():
        p = meus.get(chave)
        if p is None or p.get("status", "ativo") != "ativo":
            try:
                graph(c, idh, "DELETE", f"/me/events/{ev['event_id']}")
            except ErroMS as e:
                if e.status != 404:
                    raise
            c.execute("DELETE FROM eventos WHERE chave=?", (chave,)); n["removidos"] += 1
    # 2) Outlook → agenda (ocupado), sem os eventos que o próprio conector criou
    nossos = {r["event_id"] for r in c.execute("SELECT event_id FROM eventos WHERE id_hub=?", (idh,))}
    ini = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    fim = ini + timedelta(days=int(cfg.get("dias_calendario") or 14) + 1)
    q = urllib.parse.urlencode({"startDateTime": ini.strftime("%Y-%m-%dT%H:%M:%SZ"), "endDateTime": fim.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "$select": "id,subject,start,end,showAs,isCancelled,sensitivity,categories", "$top": "250"})
    _, r, _ = graph(c, idh, "GET", "/me/calendarView?" + q, cabecalhos={"Prefer": 'outlook.timezone="UTC"'})
    vistos = set()
    pessoa = {"id_hub": idh, "email": conta["email_hub"] or conta["ms_upn"], "nome": conta["nome"]}
    for e in r.get("value") or []:
        if e.get("id") in nossos or e.get("isCancelled") or e.get("showAs") in ("free", "workingElsewhere") or "STRAT·HUB" in (e.get("categories") or []):
            continue
        chave = f"microsoft:{idh}:{e['id']}"
        vistos.add(chave)
        privado = e.get("sensitivity") in ("private", "confidential")
        p = {"status": "ativo", "pessoa": pessoa, "titulo": "Ocupado" if privado else (e.get("subject") or "Ocupado"),
             "inicio": (e.get("start") or {}).get("dateTime", "")[:19] + "Z", "fim": (e.get("end") or {}).get("dateTime", "")[:19] + "Z",
             "origem_app": B.app_id, "origem_nome": "Outlook"}
        h = _h(p)
        ant = c.execute("SELECT hash FROM ocupados WHERE chave=?", (chave,)).fetchone()
        if not ant or ant["hash"] != h:
            hubkit.publicar("agenda", "ocupado", chave, p, source_application=B.app_id, source_version=B.versao)
            c.execute("INSERT OR REPLACE INTO ocupados(chave,id_hub,hash,em) VALUES (?,?,?,?)", (chave, idh, h, agora())); n["ocupados"] += 1
    for x in c.execute("SELECT chave FROM ocupados WHERE id_hub=?", (idh,)).fetchall():
        if x["chave"] not in vistos:
            hubkit.publicar("agenda", "ocupado", x["chave"], {"status": "removido", "pessoa": pessoa, "origem_app": B.app_id},
                            source_application=B.app_id, source_version=B.versao)
            c.execute("DELETE FROM ocupados WHERE chave=?", (x["chave"],))
    return n


# ───────────────────────────────────────────────── aprovações no Teams
def _ms_usuario(c, emissor: str, email: str) -> dict | None:
    try:
        _, r, _ = graph(c, emissor, "GET", f"/users/{urllib.parse.quote(email)}?$select=id,displayName,mail,userPrincipalName")
        return r
    except ErroMS as e:
        if e.status in (403, 404):
            return None
        raise


def _provisionar(c, emissor: str) -> None:
    if ajustes(c).get("aprovacoes_provisionado"):
        return
    try:
        _, r, _ = graph(c, emissor, "GET", "/solutions/approval", versao="beta")
        if r.get("provisioningStatus") not in ("provisioned", "provisioningCompleted"):
            graph(c, emissor, "POST", "/solutions/approval/provision", {}, versao="beta")
    except ErroMS:
        pass
    _salvar_ajuste(c, "aprovacoes_provisionado", True)


def _upd(c, chave: str, **kw) -> None:
    kw["atualizado_em"] = agora()
    c.execute("UPDATE aprovacoes SET " + ", ".join(f"{k}=?" for k in kw) + " WHERE pendencia=?", (*kw.values(), chave))


def _emails(p: dict) -> list[str]:
    emails = []
    for d in (p.get("destinatarios") or []):
        if d.get("email") and d["email"].lower() not in emails:
            emails.append(d["email"].lower())
    if "@" in str(p.get("responsavel") or "") and p["responsavel"].lower() not in emails:
        emails.append(p["responsavel"].lower())
    return emails


def _aprovadores(c, emissor: str, p: dict) -> list[dict]:
    """Quem aprova no Teams: quem conectou a própria conta Microsoft (conferida com o cadastro ou confirmada pelo administrador)
    recebe nela, mesmo que o e-mail do hub seja um alias; os demais são procurados no Microsoft 365 pelo e-mail do cadastro no hub."""
    contas = {r["id_hub"]: r for r in c.execute("SELECT * FROM contas WHERE ms_id IS NOT NULL") if _confere(r)}
    out, vistos = [], set()
    for d in p.get("destinatarios") or []:
        conta = contas.get(str(d.get("id_hub") or ""))
        if conta and conta["ms_id"] not in vistos:
            vistos.add(conta["ms_id"])
            out.append({"id": conta["ms_id"], "email": (conta["ms_upn"] or d.get("email") or "").lower(), "displayName": conta["ms_nome"] or conta["nome"]})
    ja = {x["email"] for x in out} | {(d.get("email") or "").lower() for d in p.get("destinatarios") or [] if str(d.get("id_hub") or "") in contas}
    for e in _emails(p):
        if e in ja:
            continue
        r = _ms_usuario(c, emissor, e) or {}
        if r.get("id") and r["id"] not in vistos:
            vistos.add(r["id"]); out.append({"email": e, **r})
    return out


def _criar_aprovacao(c, emissor: str, chave: str, p: dict) -> None:
    acoes = [a for a in p.get("acoes") or [] if a.get("rotulo") and a.get("caminho")][:10]
    emails = _emails(p)
    aprovadores = _aprovadores(c, emissor, p)
    base = {"pendencia": chave, "app": p.get("origem_app") or chave.split(":")[0], "titulo": p.get("titulo"), "emissor": emissor,
            "aprovadores": dump([{"id": a["id"], "email": a["email"], "nome": a.get("displayName")} for a in aprovadores]), "acoes": dump(acoes),
            "criada_em": agora(), "atualizado_em": agora()}
    if not aprovadores:
        c.execute("INSERT OR REPLACE INTO aprovacoes(pendencia,app,titulo,emissor,aprovadores,acoes,estado,erro,criada_em,atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (chave, base["app"], base["titulo"], emissor, dump([{"email": e} for e in emails]), base["acoes"], "sem_aprovador",
                   ("Nenhum destinatário encontrado no Microsoft 365 pelo e-mail" + (f" ({', '.join(emails)})" if emails else " (destinatários sem e-mail no hub)")
                    + ". O aprovador precisa conectar a própria conta em Minha conta (ou ter no hub o mesmo e-mail da conta Microsoft)."), base["criada_em"], base["atualizado_em"]))
        return
    descricao = "\n".join(x for x in (p.get("descricao"), f"Origem: {p.get('origem_nome') or base['app']}",
                                      "Responder aqui executa a ação na ferramenta de origem (STRAT·HUB).") if x)
    corpo = {"displayName": (p.get("titulo") or "Aprovação")[:100], "description": descricao[:1000], "approvalType": "custom",
             "responsePrompts": [a["rotulo"][:50] for a in acoes], "allowEmailNotification": True,
             "approvers": [{"user": {"id": a["id"], "displayName": a.get("displayName") or a["email"]}} for a in aprovadores]}
    st, r, h = graph(c, emissor, "POST", "/solutions/approval/approvalItems", corpo, versao="beta")
    approval_id = r.get("id")
    operacao = h.get("Location") or h.get("location") or ""
    c.execute("INSERT OR REPLACE INTO aprovacoes(pendencia,app,titulo,approval_id,operacao,emissor,aprovadores,acoes,estado,criada_em,atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (chave, base["app"], base["titulo"], approval_id, operacao, emissor, base["aprovadores"], base["acoes"],
               "criada" if approval_id else "criando", base["criada_em"], base["atualizado_em"]))


def _tentar_criar(c, emissor: str, chave: str, p: dict) -> bool:
    """Cria no Teams; se a Microsoft recusar, a recusa fica visível na Atividade e é tentada de novo em 10 min."""
    try:
        _criar_aprovacao(c, emissor, chave, p)
        r = c.execute("SELECT estado FROM aprovacoes WHERE pendencia=?", (chave,)).fetchone()
        return bool(r) and r["estado"] in ("criando", "criada")
    except ErroMS as e:
        dica = {401: " — reconecte a conta emissora.", 403: " — confira a permissão ApprovalSolution.ReadWrite (com consentimento) e se a conta emissora usa o app Aprovações do Teams."}.get(e.status, "")
        c.execute("INSERT OR REPLACE INTO aprovacoes(pendencia,app,titulo,emissor,aprovadores,acoes,estado,erro,criada_em,atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (chave, p.get("origem_app") or chave.split(":")[0], p.get("titulo"), emissor, dump([{"email": x} for x in _emails(p)]),
                   dump(p.get("acoes") or []), "erro", f"A Microsoft recusou a criação: {e.status} {e.codigo} {e.msg[:200]}{dica}", agora(), agora()))
        return False


def _emissor(c, cfg: dict) -> str:
    """Conta que emite as aprovações: só a escolhida pelo administrador na Configuração (nunca escolhida sozinha).
    O ideal é uma conta de integração, que não aprova nada."""
    atual = cfg.get("emissor") or ""
    return atual if atual and c.execute("SELECT 1 FROM contas WHERE id_hub=? AND refresh IS NOT NULL", (atual,)).fetchone() else ""


def _acompanhar(c, a, abertas: dict) -> str:
    """Uma aprovação já criada: resolve o id (operação assíncrona), lê respostas e executa; cancela se a pendência fechou."""
    chave, emissor = a["pendencia"], a["emissor"]
    if a["estado"] == "criando":
        rel = a["operacao"].split(f"{GRAPH}/beta", 1)[-1] if a["operacao"] else ""
        if not rel:
            _upd(c, chave, estado="falha", erro="A Microsoft não devolveu a operação de criação"); return "falha"
        _, op, _ = graph(c, emissor, "GET", rel, versao="beta")
        if op.get("status") == "failed":
            _upd(c, chave, estado="falha", erro=str((op.get("error") or {}).get("message") or "falha na criação")[:300]); return "falha"
        rid = op.get("resourceId") or str(op.get("resourceLocation") or "").rstrip("/").rsplit("/", 1)[-1]
        if op.get("status") != "succeeded" or not rid:
            return "criando"
        _upd(c, chave, approval_id=rid, estado="criada")
        a = c.execute("SELECT * FROM aprovacoes WHERE pendencia=?", (chave,)).fetchone()
    if chave not in abertas:  # resolvida no hub (agenda ou ferramenta) antes: cancela no Teams
        try:
            graph(c, emissor, "POST", f"/solutions/approval/approvalItems/{a['approval_id']}/cancel", {}, versao="beta")
        except ErroMS:
            pass
        _upd(c, chave, estado="cancelada"); return "cancelada"
    _, rs, _ = graph(c, emissor, "GET", f"/solutions/approval/approvalItems/{a['approval_id']}/responses", versao="beta")
    respostas = sorted(rs.get("value") or [], key=lambda x: x.get("createdDateTime") or "")
    if not respostas:
        return "criada"
    resp = respostas[0]
    autor = ((resp.get("createdBy") or {}).get("user") or {})
    aprov = {x["id"]: x for x in load(a["aprovadores"], [])}
    email = (aprov.get(autor.get("id")) or {}).get("email") or autor.get("userPrincipalName") or autor.get("mail")
    acao = next((x for x in load(a["acoes"], []) if x["rotulo"][:50] == resp.get("response")), None)
    coment = str(resp.get("comments") or "").strip()
    _upd(c, chave, resposta=resp.get("response"), respondido_por=email, comentario=coment)
    if not acao:
        _upd(c, chave, estado="falha", erro=f"Resposta “{resp.get('response')}” não corresponde a uma ação"); return "falha"
    if acao.get("comentario") == "obrigatorio" and not coment:
        _upd(c, chave, estado="falha", erro=f"“{acao['rotulo']}” exige comentário: responda de novo na agenda ou na ferramenta"); return "falha"
    corpo = {**(acao.get("corpo") or {}), **({acao.get("campo") or "comentario": coment, "comentario": coment} if coment else {})}
    try:
        r = hubkit.agir(a["app"], acao["caminho"], {"email": email}, corpo, acao.get("metodo") or "POST")
    except Exception as e:  # sem hub, sem autorização do administrador, ferramenta fora do ar: tenta de novo no próximo ciclo
        _upd(c, chave, erro=f"Ainda não executada: {str(e)[:250]}"); return "aguardando"
    ok = int(r.get("status") or 500) < 400
    det = r.get("resposta") or {}
    _upd(c, chave, estado="executada" if ok else "falha", resultado=dump({"status": r.get("status")}),
         erro=None if ok else str(det.get("detail") if isinstance(det.get("detail"), str) else (det.get("detail") or {}).get("mensagem") or det)[:300])
    return "executada" if ok else "falha"


def _sync_aprovacoes(c, emissor: str, pendencias: list[dict]) -> dict:
    n = {"criadas": 0, "executadas": 0, "canceladas": 0, "falhas": 0}
    abertas = {r["entity_key"]: r.get("payload") or {} for r in pendencias
               if (r.get("payload") or {}).get("status") == "aberta" and (r.get("payload") or {}).get("natureza") == "acao"
               and (r.get("payload") or {}).get("acoes")}
    _provisionar(c, emissor)
    for chave, p in abertas.items():
        if not c.execute("SELECT 1 FROM aprovacoes WHERE pendencia=?", (chave,)).fetchone():
            if _tentar_criar(c, emissor, chave, p):
                n["criadas"] += 1
            else:
                n["falhas"] += 1
    for a in c.execute("SELECT * FROM aprovacoes WHERE estado IN ('criando','criada')").fetchall():
        try:
            r = _acompanhar(c, a, abertas)
        except ErroMS as e:
            _upd(c, a["pendencia"], erro=f"{e.codigo}: {e.msg[:200]}"); n["falhas"] += 1; continue
        n["executadas"] += r == "executada"; n["canceladas"] += r == "cancelada"; n["falhas"] += r == "falha"
    limite = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    for a in c.execute("SELECT * FROM aprovacoes WHERE estado IN ('sem_aprovador','erro')").fetchall():
        p = abertas.get(a["pendencia"])
        if p is None:
            _upd(c, a["pendencia"], estado="encerrada")
        elif sorted(_emails(p)) != sorted(x.get("email") for x in load(a["aprovadores"], [])) or (a["atualizado_em"] or "") < limite:
            c.execute("DELETE FROM aprovacoes WHERE pendencia=?", (a["pendencia"],))  # e-mail corrigido (ou nova tentativa): tenta de novo
            n["criadas" if _tentar_criar(c, emissor, a["pendencia"], p) else "falhas"] += 1
    return n


# ───────────────────────────────────────────────── aprovações criadas no Teams → agenda
RESPOSTAS_BASICAS = [("Approve", "Aprovar", "ok"), ("Reject", "Rejeitar", "bad")]


def _sync_teams(c, conta, nossas: set) -> int:
    """Aprovações do app Aprovações do Teams em que a pessoa é aprovadora (e ainda não respondeu) viram ação na agenda."""
    idh, ms_id = conta["id_hub"], conta["ms_id"]
    _, r, _ = graph(c, idh, "GET", "/solutions/approval/approvalItems", versao="beta")
    abertas = {}
    for it in r.get("value") or []:
        if it.get("id") in nossas or str(it.get("state") or "").lower() not in ("pending", "created", "inprogress"):
            continue
        papeis = [str(x).lower() for x in ((it.get("viewPoint") or {}).get("roles") or [])]
        aprov = [((a.get("user") or {}).get("id")) for a in it.get("approvers") or []]
        if "approver" not in papeis and ms_id not in aprov:
            continue
        _, rs, _ = graph(c, idh, "GET", f"/solutions/approval/approvalItems/{it['id']}/responses", versao="beta")
        if any(((x.get("createdBy") or {}).get("user") or {}).get("id") == ms_id for x in rs.get("value") or []):
            continue  # já respondi (no Teams ou pela agenda)
        abertas[f"teams:{it['id']}:{idh}"] = it
    for chave, it in abertas.items():
        c.execute("INSERT OR REPLACE INTO teams(chave,item_id,id_hub,dados,em) VALUES (?,?,?,?,?)", (chave, it["id"], idh, dump(it), agora()))
    for x in c.execute("SELECT chave FROM teams WHERE id_hub=?", (idh,)).fetchall():
        if x["chave"] not in abertas:
            c.execute("DELETE FROM teams WHERE chave=?", (x["chave"],))
    return len(abertas)


def _respostas(it: dict) -> list[tuple[str, str, str]]:
    prompts = [p for p in it.get("responsePrompts") or [] if p]
    return [(p, p, "") for p in prompts] if prompts else RESPOSTAS_BASICAS


def _pendencias(c) -> list[dict]:
    out = []
    contas = {r["id_hub"]: r for r in c.execute("SELECT * FROM contas")}
    for r in c.execute("SELECT * FROM teams"):
        it, conta = load(r["dados"], {}), contas.get(r["id_hub"])
        if not conta:
            continue
        dono = ((it.get("owner") or {}).get("user") or {}).get("displayName")
        out.append({"chave": r["chave"], "natureza": "acao", "tipo": "aprovacao", "titulo": f"Teams: {it.get('displayName') or 'Aprovação'}",
                    "descricao": " · ".join(x for x in (it.get("description"), f"pedida por {dono}" if dono else "") if x)[:300],
                    "prioridade": "normal", "link": "#/atividade", "origem_nome": "Teams · Aprovações",
                    "destinatarios": [{"id_hub": r["id_hub"], "email": conta["email_hub"] or conta["ms_upn"], "nome": conta["nome"]}],
                    "acoes": [B.acao(f"r{k}", rot, f"/api/teams/{it['id']}/responder", corpo={"resposta": val}, comentario="opcional", estilo=est)
                              for k, (val, rot, est) in enumerate(_respostas(it))]})
    return out


@app.post("/api/teams/{item_id}/responder")
def responder_teams(item_id: str, corpo: dict, x_usuario: str | None = Header(None)):
    """Resposta dada na agenda a uma aprovação do Teams: vai para o Teams em nome de quem respondeu."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); idh = _id_hub(u)
        r = c.execute("SELECT * FROM teams WHERE item_id=? AND id_hub=?", (item_id, idh)).fetchone()
        if not r:
            raise HTTPException(404, "Esta aprovação não está aberta para você (ou já foi respondida).")
        validas = [v for v, _, _ in _respostas(load(r["dados"], {}))]
        resp = str(corpo.get("resposta") or "")
        if resp not in validas:
            raise HTTPException(400, "Resposta inválida.")
        try:
            graph(c, idh, "POST", f"/solutions/approval/approvalItems/{item_id}/responses",
                  {"response": resp, "comments": str(corpo.get("comentario") or "")[:1000]}, versao="beta")
        except ErroMS as e:
            if e.status in (401, 403) or idh in _sem_resposta:
                raise HTTPException(409, "Falta a permissão ApprovalSolutionResponse.ReadWrite (com consentimento do administrador) no aplicativo do Entra.")
            raise HTTPException(502, f"O Teams recusou a resposta: {e.codigo} {e.msg[:200]}")
        c.execute("DELETE FROM teams WHERE chave=?", (r["chave"],))
        B.auditar(c, u, "Aprovação do Teams respondida pela agenda", item_id, {"resposta": resp})
        return {"ok": True, "resposta": resp}


# ───────────────────────────────────────────────── notificações do hub (e-mail, Teams, canal)
def _grupos_de(c, id_hub: str) -> set:
    try:
        if id_hub not in _grupos:  # ainda não sabemos o que foi consentido: renova o token para descobrir
            with _lk:
                _tokens.pop(id_hub, None)
        token_de(c, id_hub)
    except ErroMS:
        return set()
    return _grupos.get(id_hub, (set(OPCIONAIS), 0))[0]


def _teams_app_id(c) -> str:
    """Identificador do app do Teams (manifesto) — gerado uma vez e mantido."""
    cfg = ajustes(c)
    if not cfg.get("teams_app_id"):
        import uuid
        _salvar_ajuste(c, "teams_app_id", str(uuid.uuid4()))
        cfg = ajustes(c)
    return cfg["teams_app_id"]


def _estado(c, cfg: dict, emissor: str) -> dict:
    """O que está pronto, para o hub decidir os canais (integracao/microsoft/estado)."""
    g = _grupos_de(c, emissor) if emissor else set()
    return {"aprovacoes_teams": bool(cfg.get("aprovacoes") and emissor), "email": bool(cfg.get("notif_email") and "email" in g),
            "teams_feed": bool(cfg.get("notif_teams") and "teams_feed" in g and cfg.get("teams_instalado")),
            "teams_canal": bool(cfg.get("notif_canal") and "canal" in g and cfg.get("canais")), "permissoes": sorted(g), "em": agora()}


def _destino_ms(c, emissor: str, pessoa: dict) -> str | None:
    conta = c.execute("SELECT * FROM contas WHERE id_hub=?", (str(pessoa.get("id") or ""),)).fetchone()
    if conta and conta["ms_id"] and _confere(conta):
        return conta["ms_id"]
    r = _ms_usuario(c, emissor, pessoa["email"]) if pessoa.get("email") else None
    return (r or {}).get("id")


def _enviar_notificacao(c, cfg: dict, emissor: str, p: dict) -> None:
    canal = p.get("canal")
    if canal == "email":
        para = (p.get("pessoa") or {}).get("email")
        if not para:
            raise ErroMS(400, "sem_email", "Pessoa sem e-mail no hub")
        graph(c, emissor, "POST", "/me/sendMail", {"message": {"subject": p.get("assunto") or "STRAT·HUB", "body": {"contentType": "HTML", "content": p.get("html") or p.get("texto") or ""},
                                                               "toRecipients": [{"emailAddress": {"address": para}}]}, "saveToSentItems": False})
    elif canal == "teams":
        uid = _destino_ms(c, emissor, p.get("pessoa") or {})
        if not uid:
            raise ErroMS(404, "sem_destinatario", "Pessoa não encontrada no Microsoft 365")
        app_id = _teams_app_id(c)
        web = f"https://teams.microsoft.com/l/entity/{app_id}/strathub?webUrl={urllib.parse.quote(p.get('url') or '', safe='')}&label={urllib.parse.quote('STRAT·HUB')}"
        graph(c, emissor, "POST", f"/users/{uid}/teamwork/sendActivityNotification",
              {"topic": {"source": "text", "value": (p.get("titulo") or "STRAT·HUB")[:150], "webUrl": web}, "activityType": "pendencia",
               "previewText": {"content": (p.get("titulo") or "")[:150]},
               "templateParameters": [{"name": "origem", "value": (p.get("texto") or "STRAT·HUB")[:100]}, {"name": "titulo", "value": (p.get("titulo") or "")[:150]}]})
    elif canal == "canal":
        alvo = (cfg.get("canais") or {}).get(p.get("ferramenta") or "")
        if not alvo:
            raise ErroMS(404, "sem_canal", f"Nenhum canal do Teams escolhido para {p.get('ferramenta')}")
        import html as _h
        corpo = f"<b>⚠ {_h.escape(p.get('titulo') or '')}</b>" + (f"<br>{_h.escape(p.get('texto') or '')[:500]}" if p.get("texto") else "") + \
                (f'<br><a href="{_h.escape(p["url"])}">Abrir no STRAT·HUB</a>' if p.get("url") else "")
        graph(c, emissor, "POST", f"/teams/{alvo['team_id']}/channels/{alvo['channel_id']}/messages", {"body": {"contentType": "html", "content": corpo}})
    else:
        raise ErroMS(400, "canal_desconhecido", str(canal))


def _sync_notificacoes(c, cfg: dict, emissor: str) -> dict:
    n = {"enviadas": 0, "falhas": 0}
    if not emissor:
        return n
    for r in B.pendentes(c, "notificacao", "envio", lambda r: (r.get("payload") or {}).get("situacao") == "pendente"):
        p = r["payload"] or {}
        try:
            _enviar_notificacao(c, cfg, emissor, p)
            sit, erro = "enviado", None; n["enviadas"] += 1
        except ErroMS as e:
            sit, erro = "falha", f"{e.status} {e.codigo}: {e.msg[:200]}"; n["falhas"] += 1
        hubkit.publicar("notificacao", "envio", r["entity_key"], {**p, "situacao": sit, "erro": erro, "tratado_em": agora()},
                        source_application=B.app_id, source_version=B.versao)
        B.marcar_recebido(c, {**r, "version": int(r.get("version") or 1) + 1}, sit)
    return n


@app.get("/api/teams/canais")
def canais_teams(x_usuario: str | None = Header(None)):
    """Equipes e canais que a conta emissora enxerga (para escolher onde cada ferramenta posta os alertas)."""
    with B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "admin")
        cfg = ajustes(c); emissor = _emissor(c, cfg)
        if not emissor:
            raise HTTPException(409, "Escolha antes a conta emissora.")
        try:
            _, eq, _ = graph(c, emissor, "GET", "/me/joinedTeams?$select=id,displayName")
            out = []
            for t in (eq.get("value") or [])[:50]:
                _, ch, _ = graph(c, emissor, "GET", f"/teams/{t['id']}/channels?$select=id,displayName")
                out += [{"team_id": t["id"], "channel_id": x["id"], "nome": f"{t.get('displayName')} › {x.get('displayName')}"} for x in ch.get("value") or []]
        except ErroMS as e:
            raise HTTPException(502 if e.status != 403 else 409, f"A Microsoft recusou: {e.codigo} {e.msg[:200]} (permissões Team.ReadBasic.All e Channel.ReadBasic.All).")
        return {"canais": out, "ferramentas": [{"id": a.get("id"), "nome": a.get("nome")} for a in (hubkit.instaladas() or [])]}


def _png(w: int, h: int, cor: tuple, borda: bool = False) -> bytes:
    """PNG mínimo (sem dependências): quadrado de uma cor; com borda = contorno branco em fundo transparente."""
    import struct, zlib
    linhas = b""
    for y in range(h):
        linhas += b"\x00"
        for x in range(w):
            if borda:
                dentro = 2 <= x < w - 2 and 2 <= y < h - 2 and not (5 <= x < w - 5 and 5 <= y < h - 5)
                linhas += bytes((255, 255, 255, 255 if dentro else 0))
            else:
                linhas += bytes((*cor, 255))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + ch(b"IDAT", zlib.compress(linhas, 9)) + ch(b"IEND", b"")


@app.get("/api/teams/pacote")
def pacote_teams(x_usuario: str | None = Header(None)):
    """Pacote do app do Teams (manifesto + ícones) para o administrador do Teams publicar na empresa."""
    import io, zipfile
    from fastapi.responses import Response
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "admin")
        cfg = ajustes(c)
        if not cfg.get("client_id"):
            raise HTTPException(409, "Informe antes o ID do aplicativo.")
        app_id, end = _teams_app_id(c), cfg.get("endereco_publico") or ""
        dom = urllib.parse.urlparse(end).hostname if end else None
        man = {"$schema": "https://developer.microsoft.com/en-us/json-schemas/teams/v1.17/MicrosoftTeams.schema.json", "manifestVersion": "1.17",
               "version": "1.0.0", "id": app_id, "developer": {"name": "STRAT·HUB", "websiteUrl": end or "https://localhost", "privacyUrl": (end or "https://localhost") + "/",
                                                             "termsOfUseUrl": (end or "https://localhost") + "/"},
               "name": {"short": "STRAT·HUB", "full": "STRAT·HUB — notificações das ferramentas"},
               "description": {"short": "Notificações das ferramentas do STRAT·HUB", "full": "Avisos de pendências, tarefas e alertas das ferramentas do STRAT·HUB no feed de atividades do Teams."},
               "icons": {"color": "color.png", "outline": "outline.png"}, "accentColor": "#1E3A6E",
               "webApplicationInfo": {"id": cfg["client_id"], "resource": f"api://{dom or 'localhost'}/{cfg['client_id']}"},
               "activities": {"activityTypes": [{"type": "pendencia", "description": "Pendência de uma ferramenta do STRAT·HUB", "templateText": "{origem}: {titulo}"}]},
               "validDomains": [dom] if dom else []}
        if end:
            man["staticTabs"] = [{"entityId": "strathub", "name": "STRAT·HUB", "contentUrl": end + "/teams/aba", "websiteUrl": end + "/", "scopes": ["personal"]}]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(man, ensure_ascii=False, indent=2))
            z.writestr("color.png", _png(192, 192, (0x1E, 0x3A, 0x6E)))
            z.writestr("outline.png", _png(32, 32, (255, 255, 255), borda=True))
        B.auditar(c, u, "Pacote do app do Teams gerado", app_id)
        return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="strathub_teams.zip"'})


@app.post("/api/teams/instalado")
def teams_instalado(corpo: dict, x_usuario: str | None = Header(None)):
    """O administrador confirma que publicou o app do Teams (o feed só funciona com o app instalado)."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "admin")
        _salvar_ajuste(c, "teams_instalado", bool(corpo.get("instalado")))
        B.auditar(c, u, "App do Teams marcado como " + ("publicado" if corpo.get("instalado") else "não publicado"), "")
        return _config(c)


# ───────────────────────────────────────────────── laço automático
def _sincronizar(c, u) -> dict:
    cfg = ajustes(c)
    if not cfg.get("client_id"):
        return {"configurado": False}
    res = {"calendario": {}, "aprovacoes": {}}
    for r in c.execute("SELECT id_hub, email_hub FROM contas").fetchall():  # o e-mail do cadastro no hub pode mudar depois da conexão
        p = hubkit.pessoa(id_hub=r["id_hub"])
        if p and (p.get("email") or "") != (r["email_hub"] or ""):
            c.execute("UPDATE contas SET email_hub=? WHERE id_hub=?", (p.get("email") or "", r["id_hub"]))
    contas = c.execute("SELECT * FROM contas WHERE refresh IS NOT NULL").fetchall()
    if cfg.get("calendario") and contas:
        comp = hubkit.consultar("agenda", "compromisso", limit=5000)
        for conta in contas:
            try:
                res["calendario"][conta["nome"]] = _sync_calendario(c, conta, comp, cfg)
            except ErroMS as e:
                c.execute("UPDATE contas SET erro=? WHERE id_hub=?", (f"Calendário: {e.codigo} {e.msg[:150]}", conta["id_hub"]))
    if cfg.get("aprovacoes") and contas:
        nossas = {r["approval_id"] for r in c.execute("SELECT approval_id FROM aprovacoes WHERE approval_id IS NOT NULL")}
        res["teams"] = {}
        for conta in contas:
            try:
                res["teams"][conta["nome"]] = _sync_teams(c, conta, nossas)
            except ErroMS as e:
                res["teams"][conta["nome"]] = f"{e.codigo}: {e.msg[:150]}"
    emissor = _emissor(c, cfg) if cfg.get("aprovacoes") and contas else ""
    if not cfg.get("aprovacoes"):
        res["aprovacoes"] = {"aviso": "Aprovações no Teams desligadas na Configuração."}
    elif not emissor:
        res["aprovacoes"] = {"aviso": "Nenhuma conta emissora: escolha uma em Configuração › Conta emissora das aprovações (de preferência uma conta de integração). As aprovações aguardam até lá."}
    else:
        try:
            pend = hubkit.consultar("agenda", "pendencia", limit=5000)
            res["aprovacoes"] = _sync_aprovacoes(c, emissor, pend) | {"pendencias_lidas": len(pend)}
        except ErroMS as e:
            res["aprovacoes"] = {"erro": f"{e.status} {e.codigo}: {e.msg[:200]}"}
        except Exception as e:  # Data Core fora do ar, etc.: aparece na Atividade
            res["aprovacoes"] = {"erro": str(e)[:250]}
    emissor_n = _emissor(c, cfg) if contas else ""
    try:
        res["notificacoes"] = _sync_notificacoes(c, cfg, emissor_n)
    except Exception as e:  # Data Core indisponível etc.
        res["notificacoes"] = {"erro": str(e)[:200]}
    try:
        hubkit.publicar("integracao", "microsoft", "estado", _estado(c, ajustes(c), emissor_n), source_application=B.app_id, source_version=B.versao)
    except Exception:
        pass
    _salvar_ajuste(c, "ultima_sincronizacao", {"em": agora(), **res})
    return res


B.automatico(_sincronizar, _pendencias, intervalo_s=60)
B.servir_spa(app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=os.environ.get("CONECTOR_MICROSOFT_HOST", "127.0.0.1"), port=int(os.environ.get("PORT") or 8880),
                root_path=os.environ.get("HUB_ROOT_PATH", ""))
