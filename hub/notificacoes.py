"""Notificações do ecossistema (e-mail e Teams), lidas e preferências por pessoa.

Fonte única: as pendências que as ferramentas publicam no Data Core (agenda/pendencia). O hub:
- lista para cada pessoa as pendências endereçadas a ela (tela de notificações de cada ferramenta e do hub);
- guarda o que cada pessoa já leu (vale em todo lugar: ferramenta, agenda, hub);
- envia na hora ou num resumo diário, conforme a preferência da pessoa e os padrões/obrigatórios da organização.

Canais: e-mail (SMTP configurado no hub ou, se não houver, a conta de integração do Conector Microsoft) e Teams
(feed de atividades e post no canal da equipe, pelo Conector Microsoft). Para o conector, o hub deixa pedidos
de envio no Data Core (notificacao/envio); o conector envia e devolve a situação no mesmo registro.
Aprovação que já vai para o app Aprovações do Teams não gera e-mail nem aviso nosso: a Microsoft já avisa.
"""
from __future__ import annotations
import html, json, smtplib, sqlite3, ssl, threading, time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from . import config, datacore, feriados, organizacao, usuarios

DB = config.DATA_DIR / "notificacoes.db"
_lock = threading.Lock()
CATEGORIAS = {"decisao": "Decisões e aprovações", "alerta": "Alertas (prazo, SLA, críticos)", "tarefa": "Tarefas", "os": "Execução de OS"}
CANAIS = {"email": "E-mail", "teams": "Teams"}
FREQ = ("imediato", "resumo", "desligado")
PADRAO = {"decisao": {"email": "imediato", "teams": "imediato"}, "alerta": {"email": "imediato", "teams": "imediato"},
          "tarefa": {"email": "resumo", "teams": "desligado"}, "os": {"email": "resumo", "teams": "desligado"}}
OBRIGATORIAS = ["alerta"]


@contextmanager
def _con():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def iniciar() -> None:
    with _con() as c:
        c.executescript("""
CREATE TABLE IF NOT EXISTS lidas(pessoa TEXT NOT NULL, chave TEXT NOT NULL, em REAL NOT NULL, PRIMARY KEY(pessoa, chave));
CREATE TABLE IF NOT EXISTS enviadas(pessoa TEXT NOT NULL, chave TEXT NOT NULL, canal TEXT NOT NULL, em REAL NOT NULL, situacao TEXT, erro TEXT,
  PRIMARY KEY(pessoa, chave, canal));
CREATE TABLE IF NOT EXISTS resumos(pessoa TEXT NOT NULL, dia TEXT NOT NULL, canal TEXT NOT NULL, em REAL NOT NULL, itens INTEGER, PRIMARY KEY(pessoa, dia, canal));
CREATE TABLE IF NOT EXISTS preferencias(pessoa TEXT PRIMARY KEY, dados TEXT NOT NULL, em REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta(chave TEXT PRIMARY KEY, valor TEXT);
""")
        if not c.execute("SELECT 1 FROM meta WHERE chave='inicio'").fetchone():
            # pendências anteriores à ativação não disparam envio (evita uma avalanche de e-mails na atualização)
            c.execute("INSERT INTO meta(chave,valor) VALUES ('inicio',?)", (str(time.time()),))


def _meta(c, k: str, padrao=None):
    r = c.execute("SELECT valor FROM meta WHERE chave=?", (k,)).fetchone()
    return r["valor"] if r else padrao


# ── configuração da organização (Admin › Notificações) ─────────────────────────
def cfg_org(cfg: dict) -> dict:
    n = cfg.get("notificacoes") or {}
    padrao = {k: {**PADRAO[k], **((n.get("padrao") or {}).get(k) or {})} for k in CATEGORIAS}
    return {"padrao": padrao, "obrigatorias": [k for k in (n.get("obrigatorias", OBRIGATORIAS) or []) if k in CATEGORIAS],
            "hora_resumo": str(n.get("hora_resumo") or "07:30"), "ativo": bool(n.get("ativo", True)),
            "endereco_publico": str(cfg.get("endereco_publico") or "").rstrip("/"),
            "smtp": {k: (n.get("smtp") or {}).get(k) for k in ("host", "porta", "usuario", "remetente", "seguranca")}
            | {"senha_definida": bool((n.get("smtp") or {}).get("senha"))}}


def salvar_cfg_org(cfg: dict, dados: dict) -> dict:
    n = dict(cfg.get("notificacoes") or {})
    if "padrao" in dados:
        p = {}
        for k in CATEGORIAS:
            v = (dados["padrao"] or {}).get(k) or {}
            p[k] = {ca: (v.get(ca) if v.get(ca) in FREQ else PADRAO[k][ca]) for ca in CANAIS}
        n["padrao"] = p
    if "obrigatorias" in dados:
        n["obrigatorias"] = [k for k in dados["obrigatorias"] or [] if k in CATEGORIAS]
    if "hora_resumo" in dados:
        h = str(dados["hora_resumo"] or "07:30")
        try:
            datetime.strptime(h, "%H:%M")
        except ValueError:
            raise ValueError("Hora do resumo inválida (use HH:MM)")
        n["hora_resumo"] = h
    if "ativo" in dados:
        n["ativo"] = bool(dados["ativo"])
    if "smtp" in dados:
        s = dict(n.get("smtp") or {})
        for k in ("host", "usuario", "remetente", "seguranca"):
            if k in dados["smtp"]:
                s[k] = str(dados["smtp"][k] or "").strip() or None
        if "porta" in dados["smtp"]:
            s["porta"] = int(dados["smtp"]["porta"] or 0) or None
        if dados["smtp"].get("senha"):
            s["senha"] = str(dados["smtp"]["senha"])
        if dados["smtp"].get("limpar_senha"):
            s.pop("senha", None)
        if s.get("seguranca") not in (None, "starttls", "ssl", "nenhuma"):
            raise ValueError("Segurança do SMTP: starttls, ssl ou nenhuma")
        n["smtp"] = s
    if "endereco_publico" in dados:
        e = str(dados["endereco_publico"] or "").strip().rstrip("/")
        if e and not e.startswith(("http://", "https://")):
            raise ValueError("Endereço público deve começar com http:// ou https://")
        cfg["endereco_publico"] = e or None
    cfg["notificacoes"] = n
    config.save(cfg)
    return cfg_org(cfg)


# ── preferências da pessoa ──────────────────────────────────────────────────────
def preferencias(cfg: dict, pessoa: str) -> dict:
    org = cfg_org(cfg)
    with _con() as c:
        r = c.execute("SELECT dados FROM preferencias WHERE pessoa=?", (pessoa,)).fetchone()
    minhas = json.loads(r["dados"]) if r else {}
    efetivas = {}
    for k in CATEGORIAS:
        efetivas[k] = {}
        for ca in CANAIS:
            v = (minhas.get(k) or {}).get(ca) or org["padrao"][k][ca]
            if k in org["obrigatorias"] and v == "desligado" and org["padrao"][k][ca] != "desligado":
                v = org["padrao"][k][ca]  # obrigatória: a pessoa escolhe a frequência, mas não desliga o canal padrão
            efetivas[k][ca] = v
    return {"categorias": CATEGORIAS, "canais": CANAIS, "frequencias": list(FREQ), "efetivas": efetivas, "personalizadas": minhas,
            "obrigatorias": org["obrigatorias"], "padrao": org["padrao"], "hora_resumo": org["hora_resumo"],
            "disponivel": canais_disponiveis(cfg)}


def salvar_preferencias(cfg: dict, pessoa: str, dados: dict) -> dict:
    org = cfg_org(cfg)
    limpo = {}
    for k, v in (dados.get("categorias") or dados or {}).items():
        if k not in CATEGORIAS or not isinstance(v, dict):
            continue
        limpo[k] = {}
        for ca, f in v.items():
            if ca in CANAIS and f in FREQ:
                if k in org["obrigatorias"] and f == "desligado" and org["padrao"][k][ca] != "desligado":
                    raise ValueError(f"“{CATEGORIAS[k]}” é obrigatória pela organização: escolha na hora ou resumo diário.")
                limpo[k][ca] = f
    with _con() as c:
        c.execute("INSERT OR REPLACE INTO preferencias(pessoa,dados,em) VALUES (?,?,?)", (pessoa, json.dumps(limpo), time.time()))
    return preferencias(cfg, pessoa)


def canais_disponiveis(cfg: dict) -> dict:
    ms = estado_microsoft()
    smtp = bool(((cfg.get("notificacoes") or {}).get("smtp") or {}).get("host"))
    return {"email": smtp or bool(ms.get("email")), "email_via": "smtp" if smtp else ("microsoft" if ms.get("email") else None),
            "teams": bool(ms.get("teams_feed")), "teams_canal": bool(ms.get("teams_canal")), "aprovacoes_teams": bool(ms.get("aprovacoes_teams"))}


def estado_microsoft() -> dict:
    """O Conector Microsoft publica o que está pronto para uso (integracao/microsoft/estado)."""
    r = datacore.listar("integracao", "microsoft", "estado", limit=1)
    if not r:
        return {}
    p = r[0]["payload"] or {}
    return p if time.time() - float(r[0].get("updated_ts") or 0) < 3600 else {}  # conector parado há mais de 1 h: indisponível


# ── pendências → notificações da pessoa ─────────────────────────────────────────
def categoria(p: dict) -> str:
    nat = p.get("natureza") or "acao"
    if nat == "operacional":
        return "os"
    if nat == "tarefa":
        return "tarefa"
    return "alerta" if p.get("tipo") == "alerta" or p.get("prioridade") == "critica" else "decisao"


def _eh_para(p: dict, pessoa: dict) -> bool:
    pid, email = pessoa["id"], (pessoa.get("email") or "").lower()
    for d in p.get("destinatarios") or []:
        if str(d.get("id_hub") or "") == pid or (email and str(d.get("email") or "").lower() == email):
            return True
    r = str(p.get("responsavel") or "").strip().lower()
    return bool(r) and r in {email, (pessoa.get("nome") or "").strip().lower()}


def abertas() -> list[dict]:
    return [r for r in datacore.listar("agenda", "pendencia", limit=5000) if (r["payload"] or {}).get("status") == "aberta"]


def link(cfg: dict, p: dict, absoluto: bool = False) -> str:
    app, ln = p.get("origem_app") or "", str(p.get("link") or "")
    rel = ln if ln.startswith("/") else (f"/apps/{app}/{ln}" if app else "/")
    if not absoluto:
        return rel
    base = cfg_org(cfg)["endereco_publico"] or f"http://localhost:{cfg.get('porta', 8800)}"
    return base + rel


def da_pessoa(cfg: dict, pessoa: dict, app: str | None = None, estado: str = "pendentes") -> list[dict]:
    with _con() as c:
        lidas = {r["chave"]: r["em"] for r in c.execute("SELECT chave, em FROM lidas WHERE pessoa=?", (pessoa["id"],))}
    out = []
    for r in abertas():
        p = r["payload"]
        if app and p.get("origem_app") != app:
            continue
        if not _eh_para(p, pessoa):
            continue
        lida = r["entity_key"] in lidas
        if (estado == "pendentes" and lida) or (estado == "lidas" and not lida):
            continue
        out.append({"chave": r["entity_key"], "app": p.get("origem_app"), "origem_nome": p.get("origem_nome") or p.get("origem_app"),
                    "titulo": p.get("titulo") or "", "descricao": p.get("descricao") or "", "prazo": p.get("prazo"),
                    "prioridade": p.get("prioridade") or "normal", "natureza": p.get("natureza") or "acao", "tipo": p.get("tipo"),
                    "categoria": categoria(p), "link": link(cfg, p), "acoes": p.get("acoes") or [], "lida": lida,
                    "em": r.get("updated_at")})
    ordem = {"critica": 0, "alta": 1, "normal": 2, "baixa": 3}
    return sorted(out, key=lambda x: (x["lida"], ordem.get(x["prioridade"], 2), x["prazo"] or "9999", x["titulo"]))


def marcar(pessoa: str, chaves: list[str], lida: bool = True) -> int:
    with _con() as c:
        for k in chaves:
            if lida:
                c.execute("INSERT OR IGNORE INTO lidas(pessoa,chave,em) VALUES (?,?,?)", (pessoa, str(k), time.time()))
            else:
                c.execute("DELETE FROM lidas WHERE pessoa=? AND chave=?", (pessoa, str(k)))
    return len(chaves)


def lidas_desde(pessoa: str, desde: float = 0) -> list[dict]:
    with _con() as c:
        return [dict(r) for r in c.execute("SELECT chave, em FROM lidas WHERE pessoa=? AND em>? ORDER BY em", (pessoa, float(desde or 0)))]


# ── envio ───────────────────────────────────────────────────────────────────────
def _agora_local(cfg: dict) -> datetime:
    return datetime.now(timezone(timedelta(hours=float(cfg.get("fuso_horas", -3)))))


def _dia_util(cfg: dict, pessoa: dict, d: datetime) -> bool:
    if d.weekday() >= 5:
        return False
    org = organizacao.ler()
    dia = d.date().isoformat()
    return not any(f["data"] == dia and f.get("conta") for f in feriados.do_ano(org, d.year, pessoa.get("localidade")))


def _corpo(cfg: dict, itens: list[dict], titulo: str) -> tuple[str, str]:
    linhas_txt, linhas_html = [], []
    for p in itens:
        url = link(cfg, p, absoluto=True)
        det = " · ".join(x for x in (p.get("origem_nome") or p.get("origem_app"), f"prazo {str(p['prazo'])[:10]}" if p.get("prazo") else "",
                                      "prioridade " + p["prioridade"] if p.get("prioridade") in ("alta", "critica") else "") if x)
        linhas_txt.append(f"- {p.get('titulo')}\n  {det}\n  {url}")
        linhas_html.append(f'<li style="margin:0 0 10px"><a href="{html.escape(url)}" style="font-weight:600;color:#1e3a6e">{html.escape(p.get("titulo") or "")}</a>'
                           f'<div style="color:#555;font-size:13px">{html.escape(det)}</div>'
                           + (f'<div style="color:#333;font-size:13px">{html.escape(str(p.get("descricao") or "")[:300])}</div>' if p.get("descricao") else "") + "</li>")
    rodape = "Para decidir, abra pelo link e entre no STRAT·HUB. Mude o que você recebe em Portal › Preferências de notificação."
    txt = f"{titulo}\n\n" + "\n".join(linhas_txt) + f"\n\n{rodape}\n"
    htm = (f'<div style="font-family:Segoe UI,Arial,sans-serif;max-width:640px"><h2 style="color:#1e3a6e;font-size:18px">{html.escape(titulo)}</h2>'
           f'<ul style="padding-left:18px">{"".join(linhas_html)}</ul><p style="color:#777;font-size:12px">{html.escape(rodape)}</p></div>')
    return txt, htm


def enviar_smtp(cfg: dict, para: str, assunto: str, texto: str, htm: str) -> None:
    s = (cfg.get("notificacoes") or {}).get("smtp") or {}
    if not s.get("host"):
        raise RuntimeError("SMTP não configurado")
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = assunto, s.get("remetente") or s.get("usuario") or "strathub@localhost", para
    m.set_content(texto)
    m.add_alternative(htm, subtype="html")
    porta = int(s.get("porta") or (465 if s.get("seguranca") == "ssl" else 587))
    if s.get("seguranca") == "ssl":
        srv = smtplib.SMTP_SSL(s["host"], porta, timeout=20, context=ssl.create_default_context())
    else:
        srv = smtplib.SMTP(s["host"], porta, timeout=20)
        if s.get("seguranca", "starttls") in (None, "starttls"):
            srv.starttls(context=ssl.create_default_context())
    try:
        if s.get("usuario") and s.get("senha"):
            srv.login(s["usuario"], s["senha"])
        srv.send_message(m)
    finally:
        try:
            srv.quit()
        except Exception:
            pass


def _pedir_conector(canal: str, chave_envio: str, payload: dict) -> None:
    datacore.gravar(domain="notificacao", entity_type="envio", entity_key=f"{canal}:{chave_envio}"[:255],
                    payload={"canal": canal, "situacao": "pendente", "pedido_em": time.time(), **payload},
                    source_application="strathub", source_version="")


def _email(cfg: dict, disp: dict, pessoa: dict, chave_envio: str, assunto: str, itens: list[dict], titulo: str) -> str:
    if not pessoa.get("email"):
        return "sem_email"
    txt, htm = _corpo(cfg, itens, titulo)
    if disp["email_via"] == "smtp":
        try:
            enviar_smtp(cfg, pessoa["email"], assunto, txt, htm)
            return "enviado"
        except Exception as e:
            return "falha: " + str(e)[:200]
    if disp["email_via"] == "microsoft":
        _pedir_conector("email", chave_envio, {"pessoa": {k: pessoa.get(k) for k in ("id", "nome", "email")}, "assunto": assunto, "texto": txt, "html": htm})
        return "pedido_ao_conector"
    return "sem_canal"


def _teams(cfg: dict, pessoa: dict, chave_envio: str, titulo: str, texto: str, url: str, ferramenta: str | None = None) -> str:
    _pedir_conector("teams", chave_envio, {"pessoa": {k: pessoa.get(k) for k in ("id", "nome", "email")}, "titulo": titulo[:150],
                                           "texto": texto[:150], "url": url, "ferramenta": ferramenta})
    return "pedido_ao_conector"


def despachar(cfg: dict, agora: datetime | None = None) -> dict:
    """Uma rodada: envios imediatos de pendências novas + resumos diários vencidos. Chamado a cada ~30 s pelo hub."""
    org = cfg_org(cfg)
    res = {"imediatos": 0, "resumos": 0, "canal_equipe": 0}
    if not org["ativo"]:
        return res
    disp = canais_disponiveis(cfg)
    pessoas = [p for p in usuarios.todos(cfg) if p.get("ativo", True) is not False]
    with _con() as c:
        inicio = float(_meta(c, "inicio", time.time()))
        lidas = {(r["pessoa"], r["chave"]) for r in c.execute("SELECT pessoa, chave FROM lidas")}
        feitas = {(r["pessoa"], r["chave"], r["canal"]) for r in c.execute("SELECT pessoa, chave, canal FROM enviadas")}
    pend = abertas()
    prefs = {p["id"]: preferencias(cfg, p["id"])["efetivas"] for p in pessoas}
    registrar = []
    for r in pend:
        p, chave = r["payload"], r["entity_key"]
        if float(r.get("created_ts") or r.get("updated_ts") or 0) < inicio and float(r.get("updated_ts") or 0) < inicio:
            continue
        cat = categoria(p)
        no_teams_aprov = bool(p.get("acoes")) and disp["aprovacoes_teams"]  # o app Aprovações do Teams já avisa (e manda e-mail)
        url = link(cfg, p, absoluto=True)
        for pes in pessoas:
            if (pes["id"], chave) in lidas or not _eh_para(p, pes):
                continue
            for canal in ("email", "teams"):
                if prefs[pes["id"]][cat][canal] != "imediato" or (pes["id"], chave, canal) in feitas or no_teams_aprov:
                    continue
                if canal == "email" and disp["email"]:
                    sit = _email(cfg, disp, pes, f"{pes['id']}:{chave}", f"[STRAT·HUB] {p.get('titulo') or 'Nova pendência'}", [p],
                                 f"{p.get('origem_nome') or 'STRAT·HUB'}: {p.get('titulo') or ''}")
                elif canal == "teams" and disp["teams"]:
                    sit = _teams(cfg, pes, f"{pes['id']}:{chave}", p.get("titulo") or "Nova pendência", p.get("origem_nome") or "", url, p.get("origem_app"))
                else:
                    continue
                registrar.append((pes["id"], chave, canal, sit)); res["imediatos"] += 1
        if cat == "alerta" and disp["teams_canal"] and ("*", chave, "teams_canal") not in feitas:
            _pedir_conector("canal", chave, {"ferramenta": p.get("origem_app"), "titulo": p.get("titulo") or "", "texto": p.get("descricao") or "",
                                             "url": url, "unidades": p.get("unidades") or []})
            registrar.append(("*", chave, "teams_canal", "pedido_ao_conector")); res["canal_equipe"] += 1
    # resumo diário (dias úteis da pessoa), a partir da hora configurada
    agora = agora or _agora_local(cfg)
    hh, mm = map(int, org["hora_resumo"].split(":"))
    if (agora.hour, agora.minute) >= (hh, mm):
        dia = agora.date().isoformat()
        with _con() as c:
            feitos = {(r["pessoa"], r["canal"]) for r in c.execute("SELECT pessoa, canal FROM resumos WHERE dia=?", (dia,))}
        for pes in pessoas:
            if not _dia_util(cfg, pes, agora):
                continue
            for canal in ("email", "teams"):
                if (pes["id"], canal) in feitos or not disp[canal]:
                    continue
                itens = [r["payload"] | {"_chave": r["entity_key"]} for r in pend if _eh_para(r["payload"], pes)
                         and (pes["id"], r["entity_key"]) not in lidas and prefs[pes["id"]][categoria(r["payload"])][canal] == "resumo"]
                if not itens:
                    continue
                titulo = f"Seu resumo do dia: {len(itens)} pendência(s)"
                if canal == "email":
                    sit = _email(cfg, disp, pes, f"resumo:{pes['id']}:{dia}", f"[STRAT·HUB] {titulo}", itens, titulo)
                else:
                    sit = _teams(cfg, pes, f"resumo:{pes['id']}:{dia}", titulo, ", ".join(i.get("titulo") or "" for i in itens[:3]),
                                 (cfg_org(cfg)["endereco_publico"] or f"http://localhost:{cfg.get('porta', 8800)}") + "/")
                with _con() as c:
                    c.execute("INSERT OR REPLACE INTO resumos(pessoa,dia,canal,em,itens) VALUES (?,?,?,?,?)", (pes["id"], dia, canal, time.time(), len(itens)))
                res["resumos"] += 1
                _ = sit
    if registrar:
        with _con() as c:
            for pes, chave, canal, sit in registrar:
                c.execute("INSERT OR REPLACE INTO enviadas(pessoa,chave,canal,em,situacao,erro) VALUES (?,?,?,?,?,?)",
                          (pes, chave, canal, time.time(), "falha" if sit.startswith("falha") else sit, sit[7:] if sit.startswith("falha") else None))
    return res


def envios_recentes(limite: int = 100) -> list[dict]:
    with _con() as c:
        return [dict(r) for r in c.execute("SELECT * FROM enviadas ORDER BY em DESC LIMIT ?", (limite,))]
