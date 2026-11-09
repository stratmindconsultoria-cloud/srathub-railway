"""StratAgenda — servidor central (FastAPI + SQLite) integrado ao STRAT·HUB.

Implementa a mesma API que a versão local-first emula no navegador (src/local/api.ts), com os mesmos formatos e
status HTTP, para que a interface rode sem alterações contra o servidor. Identidade vem do hub (login único);
o vínculo gestor ↔ membro continua pelos convites. A agenda vira a caixa de entrada do ecossistema: lê as
pendências que as ferramentas publicam (agenda/pendencia) e transforma reuniões de projeto em rascunho de ata na PMP.
"""
from __future__ import annotations
import hashlib, json, math, os, re, secrets, sqlite3, time, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

import hubkit
from appkit import Base, agora, dump, load

BASE_DIR = Path(__file__).resolve().parent
VERSAO = "3.3.0"
TABELAS = ("users", "workHours", "invites", "notifications", "tasks", "blocks", "changeRequests")
SCHEMA = """
CREATE TABLE IF NOT EXISTS registros(tabela TEXT NOT NULL, id TEXT NOT NULL, dados TEXT NOT NULL, PRIMARY KEY(tabela, id));
CREATE TABLE IF NOT EXISTS vinculos(usuario_hub TEXT PRIMARY KEY, user_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS importacoes(seq INTEGER PRIMARY KEY AUTOINCREMENT, em TEXT, por TEXT, arquivos INTEGER, registros INTEGER, detalhe TEXT);
CREATE TABLE IF NOT EXISTS pend_avisadas(chave TEXT PRIMARY KEY, user_id TEXT, em TEXT);
CREATE TABLE IF NOT EXISTS compromissos_publicados(chave TEXT PRIMARY KEY, hash TEXT NOT NULL, em TEXT);
CREATE TABLE IF NOT EXISTS fechamento_avisos(user_id TEXT NOT NULL, dia TEXT NOT NULL, PRIMARY KEY(user_id, dia));
CREATE TABLE IF NOT EXISTS lidas_sync(user_id TEXT PRIMARY KEY, desde REAL NOT NULL);
"""
PERFIS = {"admin": "Administrador da agenda", "membro": "Membro"}
DEMO = [("u-demo-1", "Pessoa Demo 1", "admin", None), ("u-demo-2", "Pessoa Demo 2", "membro", None)]
CONFIG = {"rotulos": {}, "fuso_horas": -3}
B = Base("stratagenda", "StratAgenda", VERSAO, BASE_DIR, SCHEMA, PERFIS, admin="admin", padrao="membro", demo=DEMO, config_padrao=CONFIG)


@asynccontextmanager
async def _vida(_app):
    B.iniciar()
    yield

app = FastAPI(title="StratAgenda", version=VERSAO, lifespan=_vida)
B.montar(app)


# ───────────────────────────────────────────────── armazenamento
def iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def novo_id() -> str:
    return "c" + format(int(time.time() * 1000), "x") + secrets.token_hex(7)


def get(c, t: str, i: str | None) -> dict | None:
    if not i:
        return None
    r = c.execute("SELECT dados FROM registros WHERE tabela=? AND id=?", (t, i)).fetchone()
    return json.loads(r["dados"]) if r else None


def todos(c, t: str, f=None) -> list[dict]:
    out = [json.loads(r["dados"]) for r in c.execute("SELECT dados FROM registros WHERE tabela=?", (t,))]
    return [x for x in out if not f or f(x)]


def _gravar(c, t: str, rec: dict) -> dict:
    c.execute("INSERT OR REPLACE INTO registros(tabela,id,dados) VALUES (?,?,?)", (t, rec["id"], json.dumps(rec, ensure_ascii=False)))
    return rec


def inserir(c, t: str, dados: dict) -> dict:
    agora_ = iso()
    rec = {"id": dados.get("id") or novo_id(), "createdAt": dados.get("createdAt") or agora_, "updatedAt": agora_, **dados}
    return _gravar(c, t, rec)


def alterar(c, t: str, i: str, mud: dict) -> dict | None:
    cur = get(c, t, i)
    if not cur:
        return None
    nxt = {**cur, **{k: v for k, v in mud.items() if v is not None or k in ("assigneeId", "respondedAt", "comment", "message", "impedimento", "atribuidaPor", "prazoMax")}}
    if nxt != cur:
        nxt["updatedAt"] = iso()
        _gravar(c, t, nxt)
    return nxt


def apagar(c, t: str, i: str) -> None:
    c.execute("DELETE FROM registros WHERE tabela=? AND id=?", (t, i))


def pick(o: dict, ks: list[str]) -> dict:
    return {k: o.get(k) for k in ks if k in o}


def add_dias(s: str, n: int) -> str:
    d = datetime.fromisoformat(s[:10]) + timedelta(days=n)
    return d.strftime("%Y-%m-%d")


def wh_padrao(uid: str) -> list[dict]:
    return [{"id": f"wh_{uid}_{d}", "dayOfWeek": d, "startSlot": 2, "endSlot": 22, "maxHours": 8, "isActive": True, "userId": uid} for d in (1, 2, 3, 4, 5)]


def tags_de(t: dict) -> list:
    v = t.get("tags")
    if isinstance(v, list):
        return v
    try:
        return json.loads(v or "[]")
    except (TypeError, ValueError):
        return []


def task_out(t: dict, extra: dict | None = None) -> dict:
    return {**t, "tags": tags_de(t), **(extra or {})}


def recalc_gestores(c) -> None:
    """managerId derivado dos convites aceitos — o aceito mais recente define o gestor (mesma regra da versão local).
    No hub a hierarquia vem do cadastro (líder direto): convites não mudam o gestor."""
    if hubkit.no_hub():
        return
    ult: dict[str, tuple[str, str]] = {}
    for inv in todos(c, "invites", lambda i: i.get("status") == "ACCEPTED"):
        mem, ger = (inv["receiverId"], inv["senderId"]) if inv.get("type") == "MANAGER_TO_MEMBER" else (inv["senderId"], inv["receiverId"])
        at = inv.get("respondedAt") or inv.get("updatedAt") or inv.get("createdAt") or ""
        cur = ult.get(mem)
        if not cur or at > cur[1] or (at == cur[1] and ger > cur[0]):
            ult[mem] = (ger, at)
    for u in todos(c, "users"):
        want = ult.get(u["id"], (None,))[0]
        if u.get("managerId") != want:
            _gravar(c, "users", {**u, "managerId": want})


# ───────────────────────────────────────────────── identidade
def eu(c, x_usuario: str | None) -> tuple[dict, dict]:
    """(registro da agenda, usuário da ferramenta). Primeiro acesso pelo hub cria o perfil; e-mail igual ao de um
    usuário importado (OneDrive/backup) assume aquele perfil, com tarefas, blocos e time."""
    if not hubkit.no_hub() and not x_usuario:
        x_usuario = "u-demo-1"  # modo isolado (desenvolvimento): pessoa de demonstração
    u = B.usuario(c, x_usuario)
    v = c.execute("SELECT user_id FROM vinculos WHERE usuario_hub=?", (u["id"],)).fetchone()
    me = get(c, "users", v["user_id"]) if v else None
    if not me:
        email = (u.get("email") or f"{u['id']}@demo.local").strip().lower()
        me = next((x for x in todos(c, "users") if (x.get("email") or "").lower() == email), None)
        if not me:
            me = inserir(c, "users", {"id": u["id"], "name": u["nome"], "email": email, "role": "MEMBER", "onboarded": False, "managerId": None})
            for wh in wh_padrao(me["id"]):
                inserir(c, "workHours", wh)
        c.execute("INSERT OR REPLACE INTO vinculos(usuario_hub,user_id) VALUES (?,?)", (u["id"], me["id"]))
        if hubkit.no_hub():
            _catchup(c, me)
    return me, u


def J(body, status: int = 200):
    return JSONResponse(body, status_code=status)


@app.get("/api/session")
def sessao(x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, u = eu(c, x_usuario)
        return {"user": {"id": me["id"], "name": me["name"], "email": me["email"], "role": me.get("role") or "MEMBER", "onboarded": bool(me.get("onboarded"))},
                "expires": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(), "modo": "servidor",
                "admin": u["perfil"] == "admin", "hub": hubkit.no_hub()}


@app.post("/api/register")
def registrar():
    return J({"error": "No servidor central as contas vêm do login do hub."}, 410)


# ───────────────────────────────────────────────── feriados (calendário do hub; fora dele, nacionais)
def _id_hub(c, me: dict) -> str | None:
    r = c.execute("SELECT usuario_hub FROM vinculos WHERE user_id=? AND usuario_hub LIKE 'h-%'", (me["id"],)).fetchone()
    return r["usuario_hub"][2:] if r else None


def _pessoa(c, me: dict) -> dict:
    """Cadastro da pessoa no hub (localidade, escala de feriado). Fora do hub: vazio."""
    try:
        return hubkit.pessoa(id_hub=_id_hub(c, me), email=me.get("email")) or {}
    except Exception:
        return {}


def _feriado(c, me: dict, data: str | None) -> dict | None:
    if not data or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(data)):
        return None
    try:
        return hubkit.feriado_em(data, _pessoa(c, me).get("localidade"))
    except Exception:
        return None


def _erro_feriado(f: dict, data: str, escala: bool, gestor: bool) -> JSONResponse:
    quem = "" if escala else (" Sem escala de feriado: o gestor precisa dar o de acordo." if gestor else "")
    return J({"error": f"{data[8:]}/{data[5:7]} é feriado ({f['nome']}). Trabalho em feriado não é o padrão.{quem}", "code": "FERIADO",
              "feriado": f, "escala": escala, "precisaGestor": (not escala) and gestor}, 409)


def _regra_feriado(c, me: dict, data: str, corpo: dict) -> tuple[JSONResponse | None, dict]:
    """Nunca bloqueia: pede ciência. Com escala → normal. Sem escala → ciência + de acordo do gestor (sem gestor, só ciência)."""
    f = _feriado(c, me, data)
    if not f:
        return None, {"feriado": None, "pendenteGestor": False}
    escala = bool(_pessoa(c, me).get("escala_feriado"))
    gestor = bool(me.get("managerId"))
    if not corpo.get("ciencia_feriado"):
        return _erro_feriado(f, data, escala, gestor), {}
    return None, {"feriado": f["nome"], "pendenteGestor": (not escala) and gestor, "cienteEm": iso()}


def _pedir_acordo(c, me: dict, b: dict, t: dict) -> None:
    if b.get("pendenteGestor") and me.get("managerId"):
        notificar(c, me["managerId"], "FERIADO_ACORDO", "De acordo: trabalho em feriado",
                  f"{me['name']} quer alocar \"{t['title']}\" em {b['date'][8:]}/{b['date'][5:7]} ({b.get('feriado')}).", {"blockId": b["id"], "taskId": t["id"]})


@app.get("/api/feriados")
def feriados(inicio: str, fim: str, x_usuario: str | None = Header(None)):
    """Feriados do período para a localidade da pessoa e se ela trabalha em escala."""
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        p = _pessoa(c, me)
    try:
        lista = [f for f in hubkit.feriados(inicio[:10], fim[:10], p.get("localidade")) if f.get("conta")]
    except Exception:
        lista = []
    return {"feriados": lista, "escala": bool(p.get("escala_feriado")), "localidade": p.get("localidade")}


@app.post("/api/feriado-acordo/{bid}")
def feriado_acordo(bid: str, corpo: dict, x_usuario: str | None = Header(None)):
    """O gestor dá (ou não) o de acordo para o membro trabalhar no feriado. Sem de acordo, o bloco sai e a tarefa volta ao backlog."""
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario); b = get(c, "blocks", bid)
        if not b or not b.get("pendenteGestor"):
            return J({"error": "Nada aguardando de acordo."}, 404)
        dono = get(c, "users", b.get("userId")) or {}
        if dono.get("managerId") != me["id"]:
            return J({"error": "Só o gestor da pessoa decide."}, 403)
        t = get(c, "tasks", b["taskId"]) or {"title": "tarefa"}
        if corpo.get("decisao") == "de_acordo":
            alterar(c, "blocks", bid, {"pendenteGestor": False, "acordoPor": me["name"], "acordoEm": iso()})
            notificar(c, dono["id"], "FERIADO_OK", "De acordo do gestor", f"{me['name']} concordou com \"{t['title']}\" no feriado.", {"taskId": b["taskId"]})
        else:
            apagar(c, "blocks", bid)
            if not todos(c, "blocks", lambda x: x["taskId"] == b["taskId"]):
                alterar(c, "tasks", b["taskId"], {"status": "BACKLOG"})
            notificar(c, dono["id"], "FERIADO_NAO", "Sem de acordo para o feriado", f"\"{t['title']}\" voltou ao backlog.", {"taskId": b["taskId"]})
        for n in todos(c, "notifications", lambda n: n.get("userId") == me["id"] and n.get("type") == "FERIADO_ACORDO" and (load(n.get("data"), {}) or {}).get("blockId") == bid):
            alterar(c, "notifications", n["id"], {"read": True, "data": json.dumps({**(load(n.get("data"), {}) or {}), "resolvida": True})})
        return {"success": True}


# ───────────────────────────────────────────────── tarefas e blocos
def _sobrepoe(a: dict, b: dict) -> bool:
    return a["date"] == b["date"] and a["slotStart"] < b["slotEnd"] and b["slotStart"] < a["slotEnd"]


def _url_origem(t: dict) -> str | None:
    return f"/apps/{t['origem_app']}/{t.get('link') or ''}" if t.get("origem_app") else None


def _blocos_com_flags(blocos: list[dict], todos_do_usuario: list[dict]) -> list[dict]:
    """Bloco administrativo que cruza com execução de OS (travada) leva a marca de conflito para o dono."""
    ops = [b for b in todos_do_usuario if b.get("travado") and b.get("os")]
    out = []
    for b in blocos:
        conf = None if b.get("travado") else next((o for o in ops if _sobrepoe(b, o)), None)
        out.append({**b, "conflitoOS": (conf.get("os") or True) if conf else None})
    return out


@app.get("/api/tasks")
def tasks_get(weekStart: str | None = None, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        tasks = sorted(todos(c, "tasks", lambda t: t.get("assigneeId") == me["id"]), key=lambda t: t.get("createdAt") or "", reverse=True)
        meus = todos(c, "blocks", lambda b: b.get("userId") == me["id"])
        users = {u["id"]: u for u in todos(c, "users")}
        fim = add_dias(weekStart, 7) if weekStart else None
        return [task_out(t, {"assignee": {"id": t["assigneeId"], "name": (users.get(t["assigneeId"]) or {}).get("name")} if t.get("assigneeId") else None,
                             "creator": {"id": t.get("creatorId"), "name": (users.get(t.get("creatorId")) or {}).get("name")},
                             "origemUrl": _url_origem(t), "pendenteNaOrigem": t.get("status") == "CONCLUIDA" and t.get("origemStatus") == "aberta",
                             "atribuidaPorNome": (users.get(t.get("atribuidaPor")) or {}).get("name"), "doLider": _do_lider(t, me),
                             "cadeiaNomes": [(users.get(x) or {}).get("name") for x in t.get("cadeia") or []],
                             "blocks": _blocos_com_flags([b for b in meus if b["taskId"] == t["id"] and (not weekStart or weekStart <= b["date"] < fim)], meus)}) for t in tasks]


@app.post("/api/tasks")
def tasks_post(corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        titulo = str(corpo.get("title") or "").strip()
        if not titulo:
            return J({"error": "Título obrigatório"}, 400)
        t = inserir(c, "tasks", {"title": titulo, "description": corpo.get("description"), "priority": str(corpo.get("priority") or "MEDIA").upper(),
                                 "estimateH": corpo.get("estimateH") or 1, "status": "BACKLOG", "tags": corpo.get("tags") or [], "creatorId": me["id"], "assigneeId": me["id"]})
        return J(task_out(t, {"assignee": {"id": me["id"], "name": me["name"]}, "creator": {"id": me["id"], "name": me["name"]}, "blocks": []}), 201)


def _pode_tarefa(t, me) -> bool:
    """O dono da agenda é quem decide: só quem tem a tarefa altera (o gestor sugere, mesmo nas que criou)."""
    return bool(t) and (t.get("assigneeId") == me["id"] or (not t.get("assigneeId") and t.get("creatorId") == me["id"]))


@app.patch("/api/tasks/{tid}")
def task_patch(tid: str, corpo: dict, x_usuario: str | None = Header(None)):
    """O dono da agenda edita título, prioridade, duração e descrição. Tarefa de OS é do planejamento (travada);
    tarefa vinda de outra ferramenta tem o título da origem."""
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario); t = get(c, "tasks", tid)
        if not t:
            return J({"error": "Não encontrado"}, 404)
        if not _pode_tarefa(t, me):
            return J({"error": "Não autorizado"}, 403)
        if t.get("travado"):
            return J({"error": "Execução de OS: definida pelo planejamento."}, 403)
        mud = {k: corpo[k] for k in ("status", "title", "priority", "estimateH") if corpo.get(k)}
        if _do_lider(t, me) and any(k in mud and mud[k] != t.get(k) for k in ("title", "priority")) or (_do_lider(t, me) and "description" in corpo
                                                                                                    and (corpo.get("description") or None) != t.get("description")):
            return J({"error": "Tarefa atribuída pelo seu líder: título, prioridade e descrição são dele. Você decide quando e quanto tempo."}, 403)
        if "description" in corpo:
            mud["description"] = str(corpo.get("description") or "") or None
        if t.get("pendencia") and "title" in mud and mud["title"] != t["title"]:
            return J({"error": "O título vem da ferramenta de origem."}, 409)
        if "priority" in mud:
            mud["priority"] = str(mud["priority"]).upper()
        if "estimateH" in mud:
            try:
                mud["estimateH"] = max(0.5, round(float(mud["estimateH"]) * 2) / 2)
            except (TypeError, ValueError):
                return J({"error": "Duração inválida"}, 400)
        nt = alterar(c, "tasks", tid, mud)
        if "description" in mud and mud["description"] is None:
            nt = _gravar(c, "tasks", {**nt, "description": None})
        return task_out(nt, {"origemUrl": _url_origem(nt), "pendenteNaOrigem": nt.get("status") == "CONCLUIDA" and nt.get("origemStatus") == "aberta"})


@app.delete("/api/tasks/{tid}")
def task_delete(tid: str, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario); t = get(c, "tasks", tid)
        if not t:
            return J({"error": "Não encontrado"}, 404)
        if not _pode_tarefa(t, me):
            return J({"error": "Não autorizado"}, 403)
        if t.get("travado"):
            return J({"error": "Execução de OS: definida pelo planejamento."}, 403)
        if _do_lider(t, me):
            return J({"error": "Tarefa atribuída pelo seu líder: não dá para excluir. Use “Sinalizar impedimento” se algo impede a execução."}, 403)
        for b in todos(c, "blocks", lambda b: b["taskId"] == tid):
            apagar(c, "blocks", b["id"])
        for x in todos(c, "changeRequests", lambda x: x["taskId"] == tid):
            apagar(c, "changeRequests", x["id"])
        apagar(c, "tasks", tid)
        _publicar_reuniao(c, t, removida=True)
        return {"success": True}


@app.post("/api/blocks")
def blocks_post(corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        tid, data, ini, fim = corpo.get("taskId"), corpo.get("date"), corpo.get("slotStart"), corpo.get("slotEnd")
        if not tid or not data or ini is None or fim is None:
            return J({"error": "Dados incompletos"}, 400)
        t = get(c, "tasks", tid)
        if not t:
            return J({"error": "Tarefa não encontrada"}, 404)
        if t.get("assigneeId") != me["id"]:
            return J({"error": "Não autorizado"}, 403)
        if t.get("travado"):
            return J({"error": "Execução de OS: definida pelo planejamento."}, 403)
        x = _ocupado(c, me["id"], data, ini, fim)
        if x:
            return _erro_ocupado(c, x)
        erro, fer = _regra_feriado(c, me, data, corpo)
        if erro:
            return erro
        b = inserir(c, "blocks", {"taskId": tid, "date": data, "slotStart": ini, "slotEnd": fim, "isPartial": False, "partIndex": None, "partTotal": None, "userId": me["id"], **fer})
        alterar(c, "tasks", tid, {"status": "AGENDADA"})
        _avaliar_prazo(c, get(c, "tasks", tid))
        _pedir_acordo(c, me, b, t)
        _publicar_reuniao(c, get(c, "tasks", tid))
        return J(b, 201)


@app.patch("/api/blocks/{bid}")
def block_patch(bid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario); b = get(c, "blocks", bid)
        if not b:
            return J({"error": "Não encontrado"}, 404)
        if b.get("userId") != me["id"]:
            return J({"error": "Não autorizado"}, 403)
        if b.get("travado"):
            return J({"error": "Execução de OS: definida pelo planejamento."}, 403)
        mud = {k: corpo[k] for k in ("date", "slotStart", "slotEnd") if corpo.get(k) is not None}
        alvo = {**b, **mud}
        if (alvo["date"], alvo["slotStart"], alvo["slotEnd"]) != (b["date"], b["slotStart"], b["slotEnd"]):
            x = _ocupado(c, me["id"], alvo["date"], alvo["slotStart"], alvo["slotEnd"], ignorar={bid})
            if x:
                return _erro_ocupado(c, x)
        if mud.get("date") and mud["date"] != b["date"]:
            erro, fer = _regra_feriado(c, me, mud["date"], corpo)
            if erro:
                return erro
            mud.update({k: v for k, v in fer.items()})
        nb = alterar(c, "blocks", bid, mud)
        _avaliar_prazo(c, get(c, "tasks", b["taskId"]))
        if mud.get("date") and not nb.get("pendenteGestor") and b.get("pendenteGestor"):
            nb = _gravar(c, "blocks", {**nb, "pendenteGestor": False})
        t = get(c, "tasks", b["taskId"])
        if mud.get("date") and nb.get("pendenteGestor") and not b.get("pendenteGestor"):
            _pedir_acordo(c, me, nb, t)
        _publicar_reuniao(c, t)
        return nb


@app.delete("/api/blocks/{bid}")
def block_delete(bid: str, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario); b = get(c, "blocks", bid)
        if not b:
            return J({"error": "Não encontrado"}, 404)
        if b.get("userId") != me["id"]:
            return J({"error": "Não autorizado"}, 403)
        if b.get("travado"):
            return J({"error": "Execução de OS: definida pelo planejamento."}, 403)
        apagar(c, "blocks", bid)
        if not todos(c, "blocks", lambda x: x["taskId"] == b["taskId"]):
            alterar(c, "tasks", b["taskId"], {"status": "BACKLOG"})
        return {"success": True}


# ───────────────────────────────────────────────── time, notificações, sugestões, convites
CAMPOS_TIME = ["id", "title", "priority", "estimateH", "status", "travado", "pendencia", "description", "origem_nome", "externo",
               "atribuidaPor", "cadeia", "impedimento", "prazoMax", "critica", "reatribuir", "reagendamentos"]


@app.get("/api/team/members")
def team_members(x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        if hubkit.no_hub():
            _sync_pessoas(c)
        tasks, blocks = todos(c, "tasks"), todos(c, "blocks")
        nomes = {u["id"]: u.get("name") for u in todos(c, "users")}
        return [{**pick(m, ["id", "name", "email", "jobTitle", "company"]),
                 "tasks": [{**pick(t, CAMPOS_TIME), "tags": tags_de(t), "cadeiaNomes": [nomes.get(x) for x in t.get("cadeia") or []],
                            "atribuidaPorNome": nomes.get(t.get("atribuidaPor")),
                            "blocks": [b for b in blocks if b["taskId"] == t["id"]]} for t in tasks if t.get("assigneeId") == m["id"]]}
                for m in todos(c, "users", lambda u: u.get("managerId") == me["id"] and not u.get("inativo"))]


@app.get("/api/notifications")
def notif_get(x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        ns = sorted(todos(c, "notifications", lambda n: n.get("userId") == me["id"]), key=lambda n: n.get("createdAt") or "", reverse=True)[:40]
        return {"notifications": ns, "unread": sum(1 for n in ns if not n.get("read"))}


@app.patch("/api/notifications")
def notif_patch(x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        chaves = []
        for n in todos(c, "notifications", lambda n: n.get("userId") == me["id"] and not n.get("read")):
            alterar(c, "notifications", n["id"], {"read": True})
            ch = (load(n.get("data"), {}) or {}).get("pendencia")
            if ch:
                chaves.append(ch)
        hid = _id_hub(c, me)
        if hid and chaves:  # lida na agenda = lida no hub, nas ferramentas e no botão S·H
            hubkit.marcar_notificacoes(hid, chaves)
        return {"success": True}


def notificar(c, uid: str, tipo: str, titulo: str, corpo: str, dados: dict) -> None:
    inserir(c, "notifications", {"type": tipo, "title": titulo, "body": corpo, "read": False, "data": json.dumps(dados, ensure_ascii=False), "userId": uid})


TIPOS_SUG = {"MOVE": "mover", "SCHEDULE": "agendar", "ESTIMATE": "mudar a duração de", "REASSIGN": "passar para outra pessoa"}


@app.get("/api/suggestions")
def sug_get(x_usuario: str | None = Header(None)):
    """Sugestões para eu responder: as do meu gestor sobre as minhas tarefas e as substituições em que sou quem recebe."""
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        tasks = {t["id"]: t for t in todos(c, "tasks")}
        users = {u["id"]: u for u in todos(c, "users")}
        xs = [s for s in todos(c, "changeRequests") if s["taskId"] in tasks and (
            (s.get("status") == "PENDING" and tasks[s["taskId"]].get("assigneeId") == me["id"]) or
            (s.get("status") == "AWAITING_RECEIVER" and s.get("targetUserId") == me["id"]))]
        xs.sort(key=lambda s: s.get("createdAt") or "", reverse=True)
        return [{**s, "papel": "recebedor" if s.get("status") == "AWAITING_RECEIVER" else "dono",
                 "targetUserName": (users.get(s.get("targetUserId")) or {}).get("name"),
                 "fromName": (users.get((tasks[s["taskId"]] or {}).get("assigneeId")) or {}).get("name"),
                 "task": pick(tasks[s["taskId"]], ["id", "title", "priority", "estimateH", "assigneeId", "description"])} for s in xs]


@app.post("/api/suggestions")
def sug_post(corpo: dict, x_usuario: str | None = Header(None)):
    """O gestor não altera a agenda do membro: sugere (mover, agendar, nova duração, substituição). O dono decide."""
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        t = get(c, "tasks", corpo.get("taskId"))
        if not t:
            return J({"error": "Tarefa não encontrada"}, 404)
        dono = get(c, "users", t.get("assigneeId"))
        if (dono or {}).get("managerId") != me["id"]:
            return J({"error": "Você não é gestor desta pessoa"}, 403)
        if t.get("travado"):
            return J({"error": "Execução de OS: definida pelo planejamento."}, 403)
        tipo = corpo.get("type")
        if tipo not in TIPOS_SUG:
            return J({"error": "Tipo de sugestão inválido"}, 400)
        extra: dict = {}
        if tipo in ("MOVE", "SCHEDULE"):
            fim_s = corpo.get("targetEnd") if corpo.get("targetEnd") is not None else int(corpo.get("targetStart") or 0) + math.ceil(float(t.get("estimateH") or 1) * 2)
            x = _ocupado(c, dono["id"], corpo.get("targetDate"), corpo.get("targetStart"), fim_s, ignorar={b["id"] for b in todos(c, "blocks", lambda b: b["taskId"] == t["id"])})
            if x:
                return _erro_ocupado(c, x, de=dono["name"])
            f = _feriado(c, dono, corpo.get("targetDate"))
            if f:
                if not corpo.get("ciencia_feriado"):
                    return _erro_feriado(f, corpo["targetDate"], bool(_pessoa(c, dono).get("escala_feriado")), False)
                extra["feriado"] = f["nome"]
        if tipo == "ESTIMATE":
            try:
                extra["targetEstimate"] = max(0.5, round(float(corpo.get("targetEstimate")) * 2) / 2)
            except (TypeError, ValueError):
                return J({"error": "Informe a nova duração"}, 400)
        if tipo == "REASSIGN":
            alvo = get(c, "users", corpo.get("targetUserId"))
            if not alvo or alvo["id"] == dono["id"] or alvo.get("managerId") != me["id"]:
                return J({"error": "Escolha outra pessoa do seu time"}, 400)
            extra["targetUserId"] = alvo["id"]
        for s in todos(c, "changeRequests", lambda s: s["taskId"] == t["id"] and s.get("status") in ("PENDING", "AWAITING_RECEIVER")):
            alterar(c, "changeRequests", s["id"], {"status": "SUPERSEDED"})
        s = inserir(c, "changeRequests", {"taskId": t["id"], "requestedById": me["id"], "type": tipo, "status": "PENDING",
                                          "targetDate": corpo.get("targetDate"), "targetStart": corpo.get("targetStart"), "targetEnd": corpo.get("targetEnd"),
                                          "message": corpo.get("message") or None, "seenByManager": False, **extra,
                                          "comment": json.dumps({"originalDate": corpo.get("originalDate"), "originalStart": corpo.get("originalStart"), "originalEnd": corpo.get("originalEnd")})})
        alvo_nome = (get(c, "users", extra.get("targetUserId")) or {}).get("name")
        notificar(c, t["assigneeId"], "SUGGESTION", "Sugestão do seu gestor",
                  f"{me['name']} sugeriu {TIPOS_SUG[tipo]} \"{t['title']}\"" + (f" ({alvo_nome})" if alvo_nome else "") + (f" — {extra['targetEstimate']:g} h" if "targetEstimate" in extra else ""),
                  {"suggestionId": s["id"], "taskId": t["id"]})
        return J(s, 201)


@app.patch("/api/suggestions/{sid}")
def sug_patch(sid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario); s = get(c, "changeRequests", sid)
        if not s:
            return J({"error": "Sugestão não encontrada"}, 404)
        t = get(c, "tasks", s["taskId"])
        receber = s.get("status") == "AWAITING_RECEIVER"
        if not t or (receber and s.get("targetUserId") != me["id"]) or (not receber and t.get("assigneeId") != me["id"]):
            return J({"error": "Não autorizado"}, 403)
        if s.get("status") not in ("PENDING", "AWAITING_RECEIVER"):
            return J({"error": "Sugestão já respondida"}, 409)
        gestor = s.get("requestedById")
        if corpo.get("action") != "accept":
            # recusa: o comentário fica com quem recusou; o gestor (e o dono, na substituição) só sabem que não foi aceita
            alterar(c, "changeRequests", sid, {"status": "DECLINED", "comment": corpo.get("comment") or None, "seenByManager": False})
            notificar(c, gestor, "SUGGESTION_RESULT", "Sugestão não aceita", f"\"{t['title']}\": a sugestão não foi aceita.", {"taskId": t["id"]})
            if receber:
                notificar(c, t["assigneeId"], "SUGGESTION_RESULT", "Substituição não aceita", f"\"{t['title']}\" continua com você.", {"taskId": t["id"]})
            return {"success": True, "status": "DECLINED"}
        blocos = todos(c, "blocks", lambda b: b["taskId"] == t["id"])
        tipo = s.get("type")
        if tipo in ("MOVE", "SCHEDULE") and s.get("feriado") and not corpo.get("ciencia_feriado"):
            return J({"error": f"A data sugerida é feriado ({s['feriado']}). Confirme que está ciente.", "code": "FERIADO", "feriado": {"nome": s["feriado"]}}, 409)
        fer = {"feriado": s.get("feriado"), "pendenteGestor": False, "cienteEm": iso()} if s.get("feriado") else {}
        if tipo in ("MOVE", "SCHEDULE"):  # a agenda pode ter mudado desde a sugestão
            fim_s = s.get("targetEnd") if s.get("targetEnd") is not None else int(s.get("targetStart") or 0) + math.ceil(float(t.get("estimateH") or 1) * 2)
            x = _ocupado(c, me["id"], s.get("targetDate"), s.get("targetStart"), fim_s, ignorar={b["id"] for b in blocos})
            if x:
                return _erro_ocupado(c, x)
        if tipo == "MOVE":
            orig = load(s.get("comment"), {}) or {}
            b = next((b for b in blocos if b["date"] == orig.get("originalDate") and (orig.get("originalStart") is None or b["slotStart"] == orig.get("originalStart"))), None) \
                or next((b for b in blocos if b["date"] == orig.get("originalDate")), None) or (blocos[0] if blocos else None)
            if b:
                alterar(c, "blocks", b["id"], {"date": s.get("targetDate"), "slotStart": s.get("targetStart"), "slotEnd": s.get("targetEnd"), **fer})
        elif tipo == "SCHEDULE":
            dur = math.ceil(float(t.get("estimateH") or 1) * 2)
            inserir(c, "blocks", {"taskId": t["id"], "userId": me["id"], "date": s.get("targetDate"), "slotStart": s.get("targetStart"),
                                  "slotEnd": s.get("targetEnd") or (int(s.get("targetStart") or 0) + dur), "isPartial": False, "partIndex": None, "partTotal": None, **fer})
            alterar(c, "tasks", t["id"], {"status": "AGENDADA"})
        elif tipo == "ESTIMATE":
            alterar(c, "tasks", t["id"], {"estimateH": s.get("targetEstimate")})
        elif tipo == "REASSIGN" and not receber:
            alterar(c, "changeRequests", sid, {"status": "AWAITING_RECEIVER", "ownerAcceptedAt": iso()})
            notificar(c, s["targetUserId"], "RECEIVE", "Tarefa para você", f"Proposta de passar \"{t['title']}\" para você. Aceita?", {"suggestionId": sid, "taskId": t["id"]})
            return {"success": True, "status": "AWAITING_RECEIVER"}
        elif tipo == "REASSIGN" and receber:
            antigo = t["assigneeId"]
            for b in blocos:
                apagar(c, "blocks", b["id"])
            alterar(c, "tasks", t["id"], {"assigneeId": me["id"], "status": "BACKLOG"})
            notificar(c, antigo, "SUGGESTION_RESULT", "Substituição concluída", f"\"{t['title']}\" passou para {me['name']}.", {"taskId": t["id"]})
            notificar(c, gestor, "SUGGESTION_RESULT", "Substituição concluída", f"\"{t['title']}\" passou para {me['name']}.", {"taskId": t["id"]})
        _avaliar_prazo(c, get(c, "tasks", t["id"]))
        alterar(c, "changeRequests", sid, {"status": "ACCEPTED", "seenByManager": False})
        if tipo != "REASSIGN":
            notificar(c, gestor, "SUGGESTION_RESULT", "Sugestão aceita", f"\"{t['title']}\": sugestão aceita.", {"taskId": t["id"]})
        return {"success": True, "status": "ACCEPTED"}


def por_email(c, email: str) -> dict | None:
    e = str(email or "").strip().lower()
    return next((u for u in todos(c, "users") if (u.get("email") or "").lower() == e), None) if e else None


@app.post("/api/invite")
def inv_post(corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        rec = por_email(c, corpo.get("email"))
        if not rec:
            return J({"error": "Usuário não encontrado com este e-mail. A pessoa precisa entrar uma vez na agenda pelo hub."}, 404)
        if rec["id"] == me["id"]:
            return J({"error": "Você não pode convidar a si mesmo."}, 400)
        if todos(c, "invites", lambda i: i["senderId"] == me["id"] and i["receiverId"] == rec["id"] and i.get("status") == "PENDING"):
            return J({"error": "Já existe um convite pendente para este usuário."}, 409)
        tipo = corpo.get("type")
        inv = inserir(c, "invites", {"type": tipo, "status": "PENDING", "token": novo_id(), "expiresAt": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                                     "senderId": me["id"], "receiverId": rec["id"], "respondedAt": None})
        g = tipo == "MANAGER_TO_MEMBER"
        notificar(c, rec["id"], "INVITE", "Convite de gestor" if g else "Convite de membro",
                  f"{me['name']} convidou você para fazer parte do time dele." if g else f"{me['name']} solicitou que você seja o gestor dele.",
                  {"inviteId": inv["id"], "inviteToken": inv["token"]})
        return J({"success": True, "inviteId": inv["id"]}, 201)


@app.get("/api/invite")
def inv_get(x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        users = {u["id"]: u for u in todos(c, "users")}
        xs = sorted(todos(c, "invites", lambda i: i["receiverId"] == me["id"] and i.get("status") == "PENDING"), key=lambda i: i.get("createdAt") or "", reverse=True)
        return [{**i, "sender": pick(users.get(i["senderId"]) or {}, ["id", "name", "email", "role", "company", "jobTitle"])} for i in xs]


@app.patch("/api/invite/{token}")
def inv_patch(token: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        inv = next(iter(todos(c, "invites", lambda i: i.get("token") == token)), None)
        if not inv:
            return J({"error": "Convite não encontrado."}, 404)
        if inv["receiverId"] != me["id"]:
            return J({"error": "Não autorizado."}, 403)
        if inv.get("status") != "PENDING":
            return J({"error": "Convite já respondido."}, 409)
        exp = datetime.fromisoformat(str(inv["expiresAt"]).replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > (exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)):
            return J({"error": "Convite expirado."}, 410)
        if corpo.get("action") == "accept":
            alterar(c, "invites", inv["id"], {"status": "ACCEPTED", "respondedAt": iso()})
            recalc_gestores(c)
            notificar(c, inv["senderId"], "INVITE", "Convite aceito!", f"{me['name']} aceitou seu convite.", {"inviteId": inv["id"]})
            return {"success": True, "status": "ACCEPTED"}
        alterar(c, "invites", inv["id"], {"status": "DECLINED", "respondedAt": iso()})
        return {"success": True, "status": "DECLINED"}


# ───────────────────────────────────────────────── perfil e onboarding
def _upsert_wh(c, me, lista) -> None:
    for wh in lista or []:
        wid = f"wh_{me['id']}_{wh.get('dayOfWeek')}"
        dados = {k: wh.get(k) for k in ("startSlot", "endSlot", "maxHours", "isActive")}
        if get(c, "workHours", wid):
            alterar(c, "workHours", wid, dados)
        else:
            inserir(c, "workHours", {"id": wid, "userId": me["id"], "dayOfWeek": wh.get("dayOfWeek"), **dados})


def perfil_de(c, uid: str) -> dict | None:
    u = get(c, "users", uid)
    if not u:
        return None
    campos = ["id", "name", "email", "jobTitle", "company"]
    ger = get(c, "users", u.get("managerId"))
    return {**pick(u, ["id", "name", "email", "role", "whatsapp", "company", "jobTitle", "sector", "companySize", "onboarded", "msAccountEmail"]),
            "workHours": sorted(todos(c, "workHours", lambda w: w.get("userId") == uid), key=lambda w: w.get("dayOfWeek") or 0),
            "manager": pick(ger, campos) if ger else None, "members": [pick(m, campos) for m in todos(c, "users", lambda m: m.get("managerId") == uid)]}


@app.post("/api/onboarding")
def onboarding(corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        mud = {k: corpo[k] for k in ("name", "whatsapp", "company", "jobTitle", "role", "sector", "companySize", "howFound") if corpo.get(k)}
        if corpo.get("complete"):
            mud["onboarded"] = True
        u = alterar(c, "users", me["id"], mud)
        if isinstance(corpo.get("workHours"), list):
            _upsert_wh(c, me, corpo["workHours"])
        return pick(u, ["id", "name", "role", "onboarded"])


@app.get("/api/profile")
def profile_get(x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        return perfil_de(c, me["id"])


@app.patch("/api/profile")
def profile_patch(corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        tipo = corpo.get("type")
        if tipo == "password":
            return J({"error": "A senha é a do login do hub: altere em Minha conta, no portal."}, 400)
        if tipo == "profile":
            u = alterar(c, "users", me["id"], {k: corpo.get(k) or None for k in ("name", "whatsapp", "company", "jobTitle", "role")})
            return pick(u, ["id", "name", "email", "role", "whatsapp", "company", "jobTitle"])
        if tipo == "workhours" and isinstance(corpo.get("workHours"), list):
            _upsert_wh(c, me, corpo["workHours"])
            return {"success": True}
        return J({"error": "Tipo inválido"}, 400)


# ───────────────────────────────────────────────── importação única (OneDrive/backup) e backup
def _carimbos_backup(rec: dict) -> dict[str, int]:
    return {f: int((st or [0])[0] or 0) for f, st in (rec.get("_f") or {}).items()}


def _juntar(acum: dict, tabela: str, rid: str, campos: dict[str, tuple], criador: str | None) -> None:
    """Último a escrever vence, campo a campo (carimbo HLC), como na sincronização original."""
    cur = acum.setdefault(tabela, {}).setdefault(rid, {"_v": {}, "_cb": criador})
    for f, (valor, ts) in campos.items():
        if ts >= cur["_v"].get(f, -1):
            cur[f] = valor; cur["_v"][f] = ts
    if criador and not cur.get("_cb"):
        cur["_cb"] = criador


def ler_documentos(docs: list[dict]) -> tuple[dict, list[str]]:
    """Aceita backups do app (kind=backup) e os arquivos da pasta StratAgenda do OneDrive (usuario_/equipes_/tarefas_compartilhadas_)."""
    acum: dict = {}
    avisos = []
    for i, d in enumerate(docs, 1):
        if not isinstance(d, dict) or d.get("app") != "StratAgenda":
            avisos.append(f"arquivo {i}: não é do StratAgenda — ignorado"); continue
        if d.get("kind") == "backup":
            for t, regs in (d.get("tables") or {}).items():
                if t not in TABELAS:
                    continue
                for r in regs or []:
                    if not isinstance(r, dict) or not r.get("id") or r.get("_local"):
                        continue
                    st = _carimbos_backup(r)
                    _juntar(acum, t, r["id"], {k: (v, st.get(k, 0)) for k, v in r.items() if not k.startswith("_") and k != "id"} | {"_deleted": (r.get("_deleted", 0), st.get("_deleted", 0))},
                            r.get("_createdBy"))
        elif d.get("kind") in ("usuario", "equipes", "tarefas_compartilhadas"):
            for t, regs in (d.get("records") or {}).items():
                if t not in TABELAS:
                    continue
                for r in regs or []:
                    if not isinstance(r, dict) or not r.get("id"):
                        continue
                    _juntar(acum, t, r["id"], {f: (v[0], int(v[1] or 0)) for f, v in (r.get("f") or {}).items() if isinstance(v, list) and len(v) >= 2}, r.get("cb"))
        elif d.get("kind") == "backup-servidor":
            for t, regs in (d.get("tables") or {}).items():
                for r in regs or []:
                    if t in TABELAS and isinstance(r, dict) and r.get("id"):
                        _juntar(acum, t, r["id"], {k: (v, 1) for k, v in r.items() if k != "id"}, None)
        else:
            avisos.append(f"arquivo {i}: tipo '{d.get('kind')}' desconhecido — ignorado")
    return acum, avisos


@app.post("/api/importar")
async def importar(request: Request, arquivos: list[UploadFile] = File(default=[]), x_usuario: str | None = Header(None)):
    """Importação ÚNICA para o servidor (migração): backups JSON do app e/ou arquivos da pasta StratAgenda do OneDrive."""
    docs = []
    for f in arquivos:
        try:
            docs.append(json.loads((await f.read()).decode("utf-8-sig")))
        except (ValueError, UnicodeDecodeError):
            docs.append({"app": "?", "_nome": f.filename})
    if not arquivos:
        try:
            corpo = await request.json()
            docs = corpo if isinstance(corpo, list) else (corpo.get("documentos") or [corpo])
        except Exception:
            raise HTTPException(400, "Envie arquivos JSON (backup do StratAgenda ou arquivos da pasta do OneDrive).")
    with B.lock, B.con() as c:
        me, u = eu(c, x_usuario)
        B.exigir(u, "admin", msg="A importação para o servidor é feita pelo administrador da agenda.")
        acum, avisos = ler_documentos(docs)
        n = ignorados = 0
        for t, regs in acum.items():
            for rid, r in regs.items():
                r.pop("_v", None); cb = r.pop("_cb", None)
                if r.pop("_deleted", 0):
                    ignorados += 1; continue
                atual = get(c, t, rid) or {}
                rec = {**atual, **{k: v for k, v in r.items()}, "id": rid}
                rec.setdefault("createdAt", iso()); rec["updatedAt"] = rec.get("updatedAt") or iso()
                if t == "tasks" and not rec.get("creatorId") and cb:
                    rec["creatorId"] = cb
                _gravar(c, t, rec); n += 1
        recalc_gestores(c)
        c.execute("INSERT INTO importacoes(em,por,arquivos,registros,detalhe) VALUES (?,?,?,?,?)", (agora(), u["nome"], len(docs), n, dump({"avisos": avisos, "excluidos_ignorados": ignorados})))
        B.auditar(c, u, "Importação para o servidor", "", {"arquivos": len(docs), "registros": n})
        return {"arquivos": len(docs), "registros": n, "excluidos_ignorados": ignorados, "avisos": avisos,
                "usuarios": [pick(x, ["id", "name", "email"]) for x in todos(c, "users")]}


@app.get("/api/importacoes")
def importacoes(x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        _, u = eu(c, x_usuario)
        return {"admin": u["perfil"] == "admin", "historico": [{**dict(r), "detalhe": load(r["detalhe"], {})} for r in c.execute("SELECT * FROM importacoes ORDER BY seq DESC")]}


@app.get("/api/backup")
def backup(x_usuario: str | None = Header(None)):
    """Cópia completa no formato de backup do app (restaurável no app local); destino sugerido: OneDrive."""
    with B.lock, B.con() as c:
        me, u = eu(c, x_usuario)
        B.exigir(u, "admin", msg="O backup completo é do administrador da agenda.")
        ts = int(time.time() * 1000)
        tabelas = {t: [{**r, "_deleted": 0, "_createdBy": r.get("creatorId") or r.get("userId") or r.get("senderId") or r.get("id"),
                        "_f": {k: [ts, "servidor", "servidor"] for k in r if k not in ("id", "updatedAt", "managerId")} | {"_deleted": [ts, "servidor", "servidor"]}}
                       for r in todos(c, t)] for t in TABELAS}
        doc = {"app": "StratAgenda", "kind": "backup", "schema": 1, "exportedAt": iso(), "deviceId": "servidor", "origem": "servidor-central", "tables": tabelas, "credentials": []}
        nome = f"StratAgenda_servidor_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        return Response(json.dumps(doc, ensure_ascii=False), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{nome}"'})


# ───────────────────────────────────────────────── ecossistema: pendências das ferramentas
# natureza: tarefa (consome tempo → backlog de quem faz) · acao (decidir/clicar → notificação com botões) ·
# operacional (execução de OS → bloco travado na agenda do técnico). A origem é dona do quê/quem/prazo/feito;
# a agenda é dona do quando e de quanto tempo.
def _consultar(dom: str, tipo: str, **kw) -> list[dict]:
    try:
        return hubkit.consultar(dom, tipo, limit=2000, **kw)
    except Exception:
        return []


def _minha(p: dict, me: dict) -> bool:
    r = str(p.get("responsavel") or "").strip().lower()
    return bool(r) and r in {(me.get("name") or "").strip().lower(), (me.get("email") or "").strip().lower()}


def _usuario_por_hub(c, d: dict) -> dict | None:
    if d.get("id_hub"):
        v = c.execute("SELECT user_id FROM vinculos WHERE usuario_hub=?", ("h-" + str(d["id_hub"]),)).fetchone()
        if v and get(c, "users", v["user_id"]):
            return get(c, "users", v["user_id"])
    e = str(d.get("email") or "").strip().lower()
    return next((u for u in todos(c, "users") if e and (u.get("email") or "").lower() == e), None)


def _destinos(c, p: dict) -> tuple[list[dict], list[dict]]:
    """(responsáveis, destinatários): responsável nomeado pela origem e pessoas que têm o perfil para tratar."""
    resp = [u for u in todos(c, "users") if _minha(p, u)]
    dest = [u for u in (_usuario_por_hub(c, d) for d in p.get("destinatarios") or []) if u]
    vistos, uniq = set(), []
    for u in dest:
        if u["id"] not in vistos:
            vistos.add(u["id"]); uniq.append(u)
    return resp, uniq


def _para_mim(c, p: dict, me: dict) -> bool:
    resp, dest = _destinos(c, p)
    return any(u["id"] == me["id"] for u in resp + dest)


def _avisar(c, chave: str, u: dict, tipo: str, p: dict) -> None:
    if not c.execute("SELECT 1 FROM pend_avisadas WHERE chave=? AND user_id=?", (chave, u["id"])).fetchone():
        notificar(c, u["id"], tipo, p.get("origem_nome") or "Ecossistema", p.get("titulo") or "", {"pendencia": chave, "natureza": p.get("natureza")})
        c.execute("INSERT OR REPLACE INTO pend_avisadas(chave,user_id,em) VALUES (?,?,?)", (chave, u["id"], agora()))


def _local(s: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone(timedelta(hours=float(B.config().get("fuso_horas", -3)))))


def _slots(ini: datetime, fim: datetime) -> tuple[int, int]:
    """Grade da agenda: meia hora a partir das 07:00, 26 posições (até 20:00)."""
    a = max(0, min(25, (ini.hour - 7) * 2 + (1 if ini.minute >= 30 else 0)))
    m = (fim.hour - 7) * 2 + (1 if fim.minute > 0 else 0) + (1 if fim.minute > 30 else 0)
    return a, max(a + 1, min(26, m))


def _slot(hhmm: str) -> int:
    """HH:MM → índice de slot (meia hora a partir das 07:00, como a grade da agenda)."""
    h, m = [int(x) for x in hhmm.split(":")[:2]]
    return max(0, min(25, (h - 7) * 2 + (1 if m >= 30 else 0)))


def _campos_origem(chave: str, p: dict) -> dict:
    return {"pendencia": chave, "origem_app": p.get("origem_app") or chave.split(":")[0], "origem_nome": p.get("origem_nome"), "link": p.get("link"),
            "natureza": p.get("natureza") or "acao", "origemStatus": "aberta",
            **({"prazoMax": str(p["prazo"])[:10], "prazoOrigem": True} if p.get("prazo") else {})}


def _operacional(c, chave: str, p: dict, u: dict) -> None:
    """Execução de OS: bloco travado, contado na carga do dia, atualizado a cada replanejamento."""
    ini, fim = _local(p.get("inicio")), _local(p.get("fim"))
    if not ini or not fim:
        return
    data, (a, b) = ini.strftime("%Y-%m-%d"), _slots(ini, fim)
    numero = (p.get("titulo") or "").split(" · ")[0]
    t = next(iter(todos(c, "tasks", lambda t: t.get("pendencia") == chave and t.get("assigneeId") == u["id"])), None)
    dados = {"title": p.get("titulo") or chave, "description": "OS — definido pelo planejamento. " + (p.get("descricao") or ""),
             "priority": "ALTA" if p.get("prioridade") == "alta" else "MEDIA", "estimateH": round(max(0.5, (fim - ini).total_seconds() / 3600) * 2) / 2,
             "travado": True, "tags": ["OS"] + (["executor"] if p.get("executor") else []), **_campos_origem(chave, p)}
    if not t:
        t = inserir(c, "tasks", {**dados, "status": "AGENDADA", "creatorId": u["id"], "assigneeId": u["id"]})
        inserir(c, "blocks", {"taskId": t["id"], "userId": u["id"], "date": data, "slotStart": a, "slotEnd": b, "travado": True, "os": numero,
                              "isPartial": False, "partIndex": None, "partTotal": None})
        notificar(c, u["id"], "OS", "Nova OS na sua agenda", f"{p.get('titulo')} · {ini.strftime('%d/%m %H:%M')}", {"pendencia": chave, "taskId": t["id"]})
    else:
        alterar(c, "tasks", t["id"], dados)
        bl = todos(c, "blocks", lambda x: x["taskId"] == t["id"])
        if bl:
            antes = bl[0]
            if (antes["date"], antes["slotStart"], antes["slotEnd"]) != (data, a, b):
                alterar(c, "blocks", antes["id"], {"date": data, "slotStart": a, "slotEnd": b})
                notificar(c, u["id"], "OS", "OS replanejada", f"{p.get('titulo')} → {ini.strftime('%d/%m %H:%M')}", {"pendencia": chave, "taskId": t["id"]})
        else:
            inserir(c, "blocks", {"taskId": t["id"], "userId": u["id"], "date": data, "slotStart": a, "slotEnd": b, "travado": True, "os": numero,
                                  "isPartial": False, "partIndex": None, "partTotal": None})
    novo = {"date": data, "slotStart": a, "slotEnd": b}
    for x in todos(c, "blocks", lambda x: x.get("userId") == u["id"] and not x.get("travado") and _sobrepoe(x, novo)):
        tx = get(c, "tasks", x["taskId"]) or {}
        aviso = f"conf:{chave}:{x['id']}:{data}:{a}"
        if not c.execute("SELECT 1 FROM pend_avisadas WHERE chave=? AND user_id=?", (aviso, u["id"])).fetchone():
            notificar(c, u["id"], "CONFLITO_OS", "Conflito com OS", f"\"{tx.get('title')}\" está no horário de {numero}. Reorganize sua agenda.", {"taskId": x["taskId"], "pendencia": chave})
            c.execute("INSERT OR REPLACE INTO pend_avisadas(chave,user_id,em) VALUES (?,?,?)", (aviso, u["id"], agora()))


def _tarefa(c, chave: str, p: dict, u: dict) -> dict:
    t = next(iter(todos(c, "tasks", lambda t: t.get("pendencia") == chave and t.get("assigneeId") == u["id"])), None)
    pri = {"alta": "ALTA", "baixa": "BAIXA"}.get(p.get("prioridade"), "MEDIA")
    if t:
        mud = {"title": p.get("titulo") or t["title"], "priority": pri, **_campos_origem(chave, p)}
        if t.get("status") == "CONCLUIDA" and t.get("origemStatus") == "resolvida":
            mud["status"] = "BACKLOG"  # reaberta na origem
        return alterar(c, "tasks", t["id"], mud)
    t = inserir(c, "tasks", {"title": p.get("titulo") or chave, "description": p.get("descricao") or None, "priority": pri,
                             "estimateH": max(0.5, round(float(p.get("esforco_h") or 1) * 2) / 2), "status": "BACKLOG",
                             "tags": ["ecossistema", p.get("origem_app") or chave.split(":")[0]], "creatorId": u["id"], "assigneeId": u["id"], **_campos_origem(chave, p)})
    notificar(c, u["id"], "TAREFA", "Nova tarefa no seu backlog", f"{p.get('titulo')} ({p.get('origem_nome') or p.get('origem_app')})", {"pendencia": chave, "taskId": t["id"]})
    c.execute("INSERT OR REPLACE INTO pend_avisadas(chave,user_id,em) VALUES (?,?,?)", (chave, u["id"], agora()))
    return t


def _resolver(c, chave: str) -> None:
    """A origem fechou: tarefa concluída sozinha; OS tira os blocos futuros; ações somem das notificações."""
    hoje = (_local(iso()) or datetime.now()).strftime("%Y-%m-%d")
    for t in todos(c, "tasks", lambda t: t.get("pendencia") == chave):
        if t.get("travado"):
            bl = todos(c, "blocks", lambda b: b["taskId"] == t["id"])
            fut = [b for b in bl if b["date"] >= hoje]
            for b in fut:
                apagar(c, "blocks", b["id"])
            if fut and len(fut) == len(bl):
                apagar(c, "tasks", t["id"])
                notificar(c, t["assigneeId"], "OS", "OS saiu da sua agenda", t["title"], {"pendencia": chave})
            else:
                alterar(c, "tasks", t["id"], {"status": "CONCLUIDA", "origemStatus": "resolvida"})
        else:
            alterar(c, "tasks", t["id"], {"status": "CONCLUIDA", "origemStatus": "resolvida"})
    for n in todos(c, "notifications", lambda n: (load(n.get("data"), {}) or {}).get("pendencia") == chave and n.get("type") in ("ACAO", "TAREFA_DISPONIVEL")):
        alterar(c, "notifications", n["id"], {"read": True, "data": json.dumps({**(load(n.get("data"), {}) or {}), "resolvida": True}, ensure_ascii=False)})


def _entregar(c, chave: str, p: dict, so: dict | None = None) -> int:
    if p.get("status") != "aberta":
        _resolver(c, chave)
        return 0
    nat = p.get("natureza") or "acao"
    resp, dest = _destinos(c, p)
    filtro = (lambda us: [u for u in us if u["id"] == so["id"]]) if so else (lambda us: us)
    n = 0
    if nat == "operacional":
        for u in filtro(dest or resp):
            _operacional(c, chave, p, u); n += 1
    elif nat == "tarefa":
        donos = resp or (dest if len(dest) == 1 else [])
        if donos:
            for u in filtro(donos):
                _tarefa(c, chave, p, u); n += 1
        elif not todos(c, "tasks", lambda t: t.get("pendencia") == chave):
            for u in filtro(dest):  # várias pessoas podem tratar: aparece para todas, quem assumir leva
                _avisar(c, chave, u, "TAREFA_DISPONIVEL", p); n += 1
    else:
        for u in filtro(resp + [u for u in dest if u not in resp]):
            _avisar(c, chave, u, "ACAO", p); n += 1
    return n


def _catchup(c, me: dict) -> None:
    """Primeiro acesso de alguém: recebe o que já está aberto para ele."""
    for r in _consultar("agenda", "pendencia"):
        if (r.get("payload") or {}).get("status") == "aberta":
            _entregar(c, r["entity_key"], r["payload"], so=me)


def _item(c, r: dict, me: dict, u: dict, donos: dict) -> dict | None:
    p = r.get("payload") or {}
    if p.get("status") != "aberta":
        return None
    minha = _para_mim(c, p, me)
    if not minha and u["perfil"] != "admin" and (p.get("responsavel") or p.get("destinatarios")):
        return None
    app_id = p.get("origem_app") or str(r["entity_key"]).split(":")[0]
    return {"chave": r["entity_key"], **p, "natureza": p.get("natureza") or "acao", "minha": minha, "na_agenda": r["entity_key"] in donos.get(me["id"], set()),
            "assumida_por": next((nome for ch, nome in donos.get("_por", {}).values() if ch == r["entity_key"]), None),
            "url": f"/apps/{app_id}/{p.get('link') or ''}" if hubkit.no_hub() else None, "app": app_id, "atualizado": r.get("updated_at")}


def _donos(c) -> dict:
    users = {u["id"]: u for u in todos(c, "users")}
    out: dict = {"_por": {}}
    for t in todos(c, "tasks", lambda t: t.get("pendencia")):
        out.setdefault(t.get("assigneeId"), set()).add(t["pendencia"])
        out["_por"][t["id"]] = (t["pendencia"], (users.get(t.get("assigneeId")) or {}).get("name"))
    return out


@app.get("/api/ecossistema/pendencias")
def pendencias(x_usuario: str | None = Header(None)):
    """Pendências abertas das ferramentas: as que são para você primeiro; administradores veem todas."""
    regs = _consultar("agenda", "pendencia")
    with B.lock, B.con() as c:
        me, u = eu(c, x_usuario)
        donos = _donos(c)
        itens = [x for x in (_item(c, r, me, u, donos) for r in regs) if x]
    ordem = {"alta": 0, "normal": 1, "baixa": 2}
    itens.sort(key=lambda x: (not x["minha"], ordem.get(x.get("prioridade"), 1), x.get("inicio") or x.get("prazo") or "9999"))
    return {"itens": itens, "hub": hubkit.no_hub()}


@app.get("/api/ecossistema/pendencia")
def pendencia(chave: str, x_usuario: str | None = Header(None)):
    """Uma pendência (para o modal de ação aberto pela notificação)."""
    reg = next(iter(_consultar("agenda", "pendencia", entity_key=chave)), None)
    with B.lock, B.con() as c:
        me, u = eu(c, x_usuario)
        if not reg:
            return J({"error": "Pendência não encontrada"}, 404)
        p = reg.get("payload") or {}
        app_id = p.get("origem_app") or chave.split(":")[0]
        return {"chave": chave, **p, "natureza": p.get("natureza") or "acao", "minha": _para_mim(c, p, me), "app": app_id,
                "url": f"/apps/{app_id}/{p.get('link') or ''}"}


@app.post("/api/ecossistema/pendencias/agendar")
def agendar_pendencia(corpo: dict, x_usuario: str | None = Header(None)):
    """Trazer para a agenda / assumir: a pendência vira tarefa no seu backlog (com atalho para a ferramenta)."""
    chave = str(corpo.get("chave") or "")
    reg = next(iter(_consultar("agenda", "pendencia", entity_key=chave)), None)
    if not reg:
        raise HTTPException(404, "Pendência não encontrada.")
    p = reg.get("payload") or {}
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        if any(t.get("pendencia") == chave for t in todos(c, "tasks", lambda t: t.get("assigneeId") == me["id"])):
            return J({"error": "Esta pendência já está na sua agenda."}, 409)
        if (p.get("natureza") or "acao") == "operacional":
            return J({"error": "Execução de OS entra sozinha na agenda de quem vai."}, 409)
        outra = next(iter(todos(c, "tasks", lambda t: t.get("pendencia") == chave and (p.get("natureza") == "tarefa"))), None)
        if outra:
            return J({"error": f"Já assumida por {(get(c, 'users', outra.get('assigneeId')) or {}).get('name') or 'outra pessoa'}."}, 409)
        t = _tarefa(c, chave, {**p, "esforco_h": p.get("esforco_h") or 1}, me)
        for n in todos(c, "notifications", lambda n: (load(n.get("data"), {}) or {}).get("pendencia") == chave and n.get("type") == "TAREFA_DISPONIVEL"):
            alterar(c, "notifications", n["id"], {"read": True, "data": json.dumps({**(load(n.get("data"), {}) or {}), "resolvida": True, "assumida_por": me["name"]}, ensure_ascii=False)})
        return J(task_out(get(c, "tasks", t["id"])), 201)


@app.get("/api/ecossistema/projetos")
def projetos():
    vistos, out = set(), []
    for r in _consultar("pmp", "tap_aprovado") + _consultar("pmp", "plano_aprovado"):
        p = r.get("payload") or {}
        cod = (p.get("codigo_erp") or "").strip() or p.get("codigo") or r["entity_key"]
        if cod not in vistos:
            vistos.add(cod); out.append({"codigo": cod, "nome": p.get("nome") or cod, "unidade": p.get("unidade")})
    return sorted(out, key=lambda x: x["nome"])


def _projeto_da_tarefa(t: dict | None) -> str | None:
    return (t or {}).get("codigo_projeto")


def _publicar_reuniao(c, t: dict | None, removida: bool = False) -> None:
    """Reunião ligada a projeto (tag projeto:<código>) → rascunho de ata na PMP, atualizado a cada remarcação."""
    cod = _projeto_da_tarefa(t)
    if not cod:
        return
    b = sorted(todos(c, "blocks", lambda b: b["taskId"] == t["id"]), key=lambda b: (b["date"], b["slotStart"]))
    quando = None
    if b:
        h = 7 * 60 + int(b[0]["slotStart"]) * 30
        quando = f"{b[0]['date']}T{h // 60:02d}:{h % 60:02d}"
    dono = get(c, "users", t.get("assigneeId")) or {}
    extra = t.get("reuniao") or {}
    try:
        hubkit.publicar("pmp", "ata_rascunho", f"agenda:{t['id']}", {"titulo": t["title"], "data": quando, "codigo_projeto": cod,
                        "participantes": extra.get("participantes") or [dono.get("name")], "pauta": extra.get("pauta") or t.get("description") or "",
                        "criado_por": dono.get("name"), "cancelada": removida, "origem_app": B.app_id},
                        source_application=B.app_id, source_version=B.versao)
    except Exception:
        pass


@app.post("/api/reunioes")
def nova_reuniao(corpo: dict, x_usuario: str | None = Header(None)):
    """Agenda uma reunião de projeto: vira tarefa + bloco na agenda e rascunho de ata na PMP."""
    titulo = str(corpo.get("titulo") or "").strip()
    cod = str(corpo.get("codigo_projeto") or "").strip()
    data, hora = str(corpo.get("data") or ""), str(corpo.get("hora") or "09:00")
    if not titulo or not cod or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data) or not re.fullmatch(r"\d{1,2}:\d{2}", hora):
        raise HTTPException(400, "Informe título, projeto, data (AAAA-MM-DD) e hora (HH:MM).")
    dur = max(0.5, float(corpo.get("duracao_h") or 1))
    part = [str(x).strip() for x in (corpo.get("participantes") or []) if str(x).strip()]
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        t = inserir(c, "tasks", {"title": titulo, "description": corpo.get("pauta") or None, "reuniao": {"pauta": corpo.get("pauta") or "", "participantes": [me["name"], *part]},
                                 "priority": "MEDIA", "estimateH": dur, "status": "AGENDADA", "tags": ["reunião", cod], "codigo_projeto": cod, "creatorId": me["id"], "assigneeId": me["id"]})
        s = _slot(hora)
        inserir(c, "blocks", {"taskId": t["id"], "userId": me["id"], "date": data, "slotStart": s, "slotEnd": min(26, s + max(1, int(round(dur * 2)))),
                              "isPartial": False, "partIndex": None, "partTotal": None})
        _publicar_reuniao(c, get(c, "tasks", t["id"]))
        return J(task_out(get(c, "tasks", t["id"])), 201)


# ───────────────────────────────────────────────── calendários externos (Conector Microsoft e afins)
def _externo(c, chave: str, p: dict) -> int:
    """agenda/ocupado: compromisso de outro calendário (Outlook) entra como "ocupado", travado, na agenda da pessoa."""
    for t in todos(c, "tasks", lambda t: t.get("externo") == chave):
        for b in todos(c, "blocks", lambda b: b["taskId"] == t["id"]):
            apagar(c, "blocks", b["id"])
        apagar(c, "tasks", t["id"])
    if p.get("status") != "ativo":
        return 0
    u = _usuario_por_hub(c, p.get("pessoa") or {})
    ini, fim = _local(p.get("inicio")), _local(p.get("fim"))
    if not u or not ini or not fim or fim <= ini:
        return 0
    a, b = _slots(ini, fim)
    t = inserir(c, "tasks", {"title": p.get("titulo") or "Ocupado", "description": f"Compromisso de {p.get('origem_nome') or 'outro calendário'} — mude por lá.",
                             "priority": "BAIXA", "estimateH": round(max(0.5, (fim - ini).total_seconds() / 3600) * 2) / 2, "status": "AGENDADA",
                             "tags": [p.get("origem_nome") or "externo"], "travado": True, "externo": chave, "origem_nome": p.get("origem_nome"),
                             "creatorId": u["id"], "assigneeId": u["id"]})
    inserir(c, "blocks", {"taskId": t["id"], "userId": u["id"], "date": ini.strftime("%Y-%m-%d"), "slotStart": a, "slotEnd": b, "travado": True,
                          "externo": p.get("origem_nome") or "externo", "isPartial": False, "partIndex": None, "partTotal": None})
    return 1


def _iso_slot(data: str, slot: int) -> str:
    fuso = timezone(timedelta(hours=float(B.config().get("fuso_horas", -3))))
    m = 7 * 60 + int(slot) * 30
    return datetime.fromisoformat(data).replace(hour=m // 60, minute=m % 60, tzinfo=fuso).isoformat()


def _publicar_compromissos(c) -> int:
    """agenda/compromisso: blocos das pessoas (para outros calendários, como o Outlook). Só muda o que mudou; o que saiu
    é publicado como removido. Compromissos que vieram de outro calendário não voltam para ele."""
    hoje = (_local(iso()) or datetime.now()).date()
    de, ate = (hoje - timedelta(days=1)).isoformat(), (hoje + timedelta(days=60)).isoformat()
    users = {u["id"]: u for u in todos(c, "users")}
    hub_de = {r["user_id"]: r["usuario_hub"][2:] for r in c.execute("SELECT * FROM vinculos WHERE usuario_hub LIKE 'h-%'")}
    tasks = {t["id"]: t for t in todos(c, "tasks")}
    atuais = {}
    for b in todos(c, "blocks", lambda b: de <= b.get("date", "") <= ate and not b.get("externo")):
        t, u = tasks.get(b["taskId"]), users.get(b.get("userId"))
        if not t or not u or t.get("externo"):
            continue
        p = {"status": "ativo", "pessoa": {"id_hub": hub_de.get(u["id"]), "email": u.get("email"), "nome": u.get("name")},
             "titulo": t.get("title"), "inicio": _iso_slot(b["date"], b["slotStart"]), "fim": _iso_slot(b["date"], b["slotEnd"]),
             "travado": bool(b.get("travado")), "origem_app": B.app_id}
        atuais[f"stratagenda:{b['id']}"] = (p, hashlib.sha1(json.dumps(p, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
    antigos = {r["chave"]: r["hash"] for r in c.execute("SELECT * FROM compromissos_publicados")}
    n = 0
    for k, (p, h) in atuais.items():
        if antigos.get(k) != h:
            hubkit.publicar("agenda", "compromisso", k, p, source_application=B.app_id, source_version=B.versao)
            c.execute("INSERT OR REPLACE INTO compromissos_publicados(chave,hash,em) VALUES (?,?,?)", (k, h, agora())); n += 1
    for k in set(antigos) - set(atuais):
        hubkit.publicar("agenda", "compromisso", k, {"status": "removido", "origem_app": B.app_id}, source_application=B.app_id, source_version=B.versao)
        c.execute("DELETE FROM compromissos_publicados WHERE chave=?", (k,)); n += 1
    return n



# ───────────────────────────────────────────────── sobreposição: uma pessoa não faz duas coisas ao mesmo tempo
def _ocupado(c, uid: str, data, ini, fim, ignorar: set | None = None) -> dict | None:
    """Bloco da pessoa (tarefa, OS ou compromisso de outro calendário) que cruza o horário pedido."""
    if not data or ini is None or fim is None:
        return None
    novo = {"date": str(data), "slotStart": int(ini), "slotEnd": int(fim)}
    ign = ignorar or set()
    return next((b for b in todos(c, "blocks", lambda b: b.get("userId") == uid and b.get("date") == novo["date"] and b["id"] not in ign)
                 if _sobrepoe(b, novo)), None)


def _erro_ocupado(c, b: dict, de: str | None = None) -> JSONResponse:
    t = get(c, "tasks", b["taskId"]) or {}
    nome = f"um compromisso do {b['externo']}" if b.get("externo") else (f"execução da OS {b['os']}" if b.get("os") else f"“{t.get('title') or 'outra tarefa'}”")
    return J({"error": f"Horário ocupado {'na agenda de ' + de + ' ' if de else ''}por {nome}. Escolha outro horário.", "code": "OCUPADO", "ocupadoPor": nome}, 409)


# ───────────────────────────────────────────────── hierarquia (líder direto no hub) e tarefas atribuídas
def _do_lider(t: dict | None, me: dict) -> bool:
    """Tarefa que veio do líder (ou de mais acima): a pessoa não recusa nem exclui; sinaliza impedimento."""
    return bool(t and t.get("atribuidaPor") and t.get("atribuidaPor") != me["id"] and t.get("assigneeId") == me["id"])


def _acima(c, uid: str) -> list[str]:
    """Líderes acima da pessoa, do direto para cima."""
    out, atual, vistos = [], (get(c, "users", uid) or {}).get("managerId"), set()
    while atual and atual not in vistos:
        out.append(atual); vistos.add(atual)
        atual = (get(c, "users", atual) or {}).get("managerId")
    return out


def _sync_pessoas(c, ttl: int = 60) -> dict:
    """No hub: cada pessoa vira usuário da agenda (mesmo antes do 1º acesso, para poder receber tarefas); o gestor é o
    líder direto do cadastro e a jornada é a carga diária. Mudança na estrutura sinaliza as tarefas a reatribuir."""
    if not hubkit.no_hub():
        return {}
    ps = hubkit.pessoas(ttl=ttl)
    if not ps:
        return {}
    por_hub = {}
    for p in ps:
        hid = "h-" + p["id"]
        v = c.execute("SELECT user_id FROM vinculos WHERE usuario_hub=?", (hid,)).fetchone()
        u = get(c, "users", v["user_id"]) if v else None
        if not u:
            email = (p.get("email") or "").strip().lower()
            u = next((x for x in todos(c, "users") if email and (x.get("email") or "").lower() == email), None)
            if not u:
                u = inserir(c, "users", {"id": hid, "name": p.get("nome") or p["id"], "email": email or f"{hid}@hub.local", "role": "MEMBER",
                                         "onboarded": False, "managerId": None})
                for wh in wh_padrao(u["id"]):
                    inserir(c, "workHours", wh)
            c.execute("INSERT OR REPLACE INTO vinculos(usuario_hub,user_id) VALUES (?,?)", (hid, u["id"]))
            _catchup(c, u)  # recebe o que já está aberto para ela
        por_hub[p["id"]] = (p, u)
    lideres = {p.get("lider_id") for p, _ in por_hub.values() if p.get("lider_id")}
    n = 0
    for pid, (p, u) in por_hub.items():
        lider = por_hub.get(p.get("lider_id") or "")
        mud = {"managerId": lider[1]["id"] if lider else None, "role": "MANAGER" if pid in lideres else "MEMBER",
               "inativo": p.get("ativo") is False, "name": p.get("nome") or u.get("name")}
        if (u.get("role") or "") == "OWNER":
            mud.pop("role")
        mud = {k: v for k, v in mud.items() if u.get(k) != v and not (k == "inativo" and not v and not u.get(k))}
        if mud:
            antigo = u.get("managerId")
            nu = _gravar(c, "users", {**u, **mud}); n += 1
            if "managerId" in mud or mud.get("inativo"):
                _estrutura_mudou(c, nu, antigo)
        jornada = float(p.get("jornada_h") or 8)
        for wh in todos(c, "workHours", lambda w: w.get("userId") == u["id"]):
            if float(wh.get("maxHours") or 8) != jornada:
                _gravar(c, "workHours", {**wh, "maxHours": jornada})
    ativos = {u["id"] for _, u in por_hub.values()}
    for r in c.execute("SELECT * FROM vinculos WHERE usuario_hub LIKE 'h-%'").fetchall():  # saiu do hub
        u = get(c, "users", r["user_id"])
        if u and r["user_id"] not in ativos and not u.get("inativo"):
            nu = _gravar(c, "users", {**u, "inativo": True}); _estrutura_mudou(c, nu, u.get("managerId"))
    return {"pessoas": len(por_hub), "mudancas": n}


def _estrutura_mudou(c, u: dict, antigo_lider: str | None) -> None:
    """Quem atribuiu continua responsável: as tarefas que não seguem mais a cadeia aparecem para ele reatribuir."""
    acima = set(_acima(c, u["id"])) if not u.get("inativo") else set()
    for t in todos(c, "tasks", lambda t: t.get("assigneeId") == u["id"] and t.get("atribuidaPor") and t.get("status") not in ("CONCLUIDA", "CANCELADA")):
        if t["atribuidaPor"] in acima and not u.get("inativo"):
            if t.get("reatribuir"):
                alterar(c, "tasks", t["id"], {"reatribuir": False})
            continue
        if not t.get("reatribuir"):
            alterar(c, "tasks", t["id"], {"reatribuir": True})
            motivo = "saiu da equipe" if u.get("inativo") else "não responde mais a você"
            notificar(c, t["atribuidaPor"], "REATRIBUIR", "Tarefa a reatribuir", f"“{t['title']}” está com {u['name']}, que {motivo}. Recolha e repasse.",
                      {"taskId": t["id"]})


def _hoje() -> str:
    return (_local(iso()) or datetime.now()).strftime("%Y-%m-%d")


def _nova_atribuida(c, me: dict, dono: dict, corpo: dict, cadeia: list[str]) -> dict:
    titulo = str(corpo.get("title") or "").strip()
    if not titulo:
        raise HTTPException(400, "Título obrigatório")
    prazo = str(corpo.get("prazoMax") or "")[:10] or None
    if prazo and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", prazo):
        raise HTTPException(400, "Prazo máximo inválido")
    try:
        est = max(0.5, round(float(corpo.get("estimateH") or 1) * 2) / 2)
    except (TypeError, ValueError):
        raise HTTPException(400, "Duração inválida")
    return inserir(c, "tasks", {"title": titulo, "description": corpo.get("description") or None, "priority": str(corpo.get("priority") or "MEDIA").upper(),
                                "estimateH": est, "status": "BACKLOG", "tags": corpo.get("tags") or [], "creatorId": me["id"], "assigneeId": dono["id"],
                                "atribuidaPor": me["id"], "cadeia": cadeia, "prazoMax": prazo})


def _sugerir_agenda(c, me: dict, dono: dict, t: dict, sug: dict | None) -> dict | None:
    """Sugestão de alocação do líder (a pessoa aceita ou recusa); horário ocupado ou feriado sem ciência não passam."""
    if not sug or not sug.get("date") or sug.get("slotStart") is None:
        return None
    ini = int(sug["slotStart"]); fim = int(sug.get("slotEnd") or ini + math.ceil(float(t.get("estimateH") or 1) * 2))
    x = _ocupado(c, dono["id"], sug["date"], ini, fim)
    if x:
        raise HTTPException(409, f"Horário sugerido ocupado na agenda de {dono['name']}.")
    f = _feriado(c, dono, sug["date"])
    extra = {}
    if f:
        if not sug.get("ciencia_feriado"):
            raise HTTPException(409, f"{sug['date']} é feriado ({f['nome']}) para {dono['name']}: confirme que está ciente.")
        extra["feriado"] = f["nome"]
    s = inserir(c, "changeRequests", {"taskId": t["id"], "requestedById": me["id"], "type": "SCHEDULE", "status": "PENDING", "targetDate": sug["date"],
                                      "targetStart": ini, "targetEnd": fim, "message": sug.get("message") or None, "seenByManager": False, **extra,
                                      "comment": json.dumps({})})
    notificar(c, dono["id"], "SUGGESTION", "Sugestão do seu líder", f"{me['name']} sugeriu agendar “{t['title']}” em {sug['date'][8:]}/{sug['date'][5:7]}.",
              {"suggestionId": s["id"], "taskId": t["id"]})
    return s


@app.post("/api/team/tasks")
def atribuir(corpo: dict, x_usuario: str | None = Header(None)):
    """O líder cria e atribui uma tarefa a quem responde diretamente a ele (um nível por vez); pode sugerir o horário."""
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario); _sync_pessoas(c)
        dono = get(c, "users", corpo.get("assigneeId"))
        if not dono or dono.get("managerId") != me["id"] or dono.get("inativo"):
            return J({"error": "Atribua só para quem responde diretamente a você."}, 403)
        t = _nova_atribuida(c, me, dono, corpo, [me["id"], dono["id"]])
        notificar(c, dono["id"], "TAREFA_ATRIBUIDA", "Nova tarefa do seu líder", f"{me['name']} atribuiu “{t['title']}” a você." +
                  (f" Prazo: {t['prazoMax'][8:]}/{t['prazoMax'][5:7]}." if t.get("prazoMax") else ""), {"taskId": t["id"]})
        s = _sugerir_agenda(c, me, dono, t, corpo.get("sugestao"))
        _avaliar_prazo(c, t)
        return J(task_out(get(c, "tasks", t["id"]), {"sugestao": s}), 201)


@app.post("/api/tasks/{tid}/repassar")
def repassar(tid: str, corpo: dict, x_usuario: str | None = Header(None)):
    """Quem recebeu repassa para alguém que responde diretamente a ele (desce um nível). A agenda dele é liberada."""
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario); _sync_pessoas(c); t = get(c, "tasks", tid)
        if not t or t.get("assigneeId") != me["id"]:
            return J({"error": "Só quem está com a tarefa repassa."}, 403)
        if t.get("travado") or t.get("pendencia") or t.get("externo"):
            return J({"error": "Tarefas de OS, de outras ferramentas ou de outros calendários não são repassadas por aqui."}, 409)
        if t.get("status") in ("CONCLUIDA", "CANCELADA"):
            return J({"error": "Tarefa encerrada."}, 409)
        dono = get(c, "users", corpo.get("userId"))
        if not dono or dono.get("managerId") != me["id"] or dono.get("inativo"):
            return J({"error": "Repasse só para quem responde diretamente a você."}, 403)
        for b in todos(c, "blocks", lambda b: b["taskId"] == tid and b.get("userId") == me["id"] and not b.get("fechamento")):
            apagar(c, "blocks", b["id"])
        cadeia = (t.get("cadeia") or [t.get("creatorId") or me["id"]])
        if cadeia[-1] != me["id"]:
            cadeia = cadeia + [me["id"]]
        nt = alterar(c, "tasks", tid, {"assigneeId": dono["id"], "atribuidaPor": me["id"], "cadeia": cadeia + [dono["id"]], "status": "BACKLOG",
                                       "impedimento": None, "reatribuir": False})
        notificar(c, dono["id"], "TAREFA_ATRIBUIDA", "Nova tarefa do seu líder", f"{me['name']} repassou “{t['title']}” a você.", {"taskId": tid})
        s = _sugerir_agenda(c, me, dono, nt, corpo.get("sugestao"))
        return task_out(get(c, "tasks", tid), {"sugestao": s})


@app.post("/api/tasks/{tid}/recolher")
def recolher(tid: str, x_usuario: str | None = Header(None)):
    """Quem repassou (ou quem está acima na cadeia) pega a tarefa de volta; a agenda de quem estava com ela é liberada."""
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario); t = get(c, "tasks", tid)
        cad = (t or {}).get("cadeia") or []
        if not t or me["id"] not in cad[:-1]:
            return J({"error": "Só quem repassou esta tarefa (ou está acima na cadeia) pode recolher."}, 403)
        if t.get("status") in ("CONCLUIDA", "CANCELADA"):
            return J({"error": "Tarefa encerrada."}, 409)
        de = t["assigneeId"]
        for b in todos(c, "blocks", lambda b: b["taskId"] == tid and not b.get("fechamento")):
            apagar(c, "blocks", b["id"])
        for x in todos(c, "changeRequests", lambda x: x["taskId"] == tid and x.get("status") in ("PENDING", "AWAITING_RECEIVER")):
            alterar(c, "changeRequests", x["id"], {"status": "SUPERSEDED"})
        i = cad.index(me["id"])
        acima = cad[i - 1] if i > 0 else None
        alterar(c, "tasks", tid, {"assigneeId": me["id"], "cadeia": cad[:i + 1], "atribuidaPor": acima, "status": "BACKLOG", "impedimento": None, "reatribuir": False})
        if de != me["id"]:
            notificar(c, de, "TAREFA_RECOLHIDA", "Tarefa recolhida", f"{me['name']} recolheu “{t['title']}”: saiu da sua agenda.", {"taskId": tid})
        return task_out(get(c, "tasks", tid))


@app.post("/api/tasks/{tid}/impedimento")
def impedimento(tid: str, corpo: dict, x_usuario: str | None = Header(None)):
    """A pessoa não recusa tarefa do líder, mas avisa o que impede (férias, falta de acesso...). Quem decide é o líder."""
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario); t = get(c, "tasks", tid)
        if not t or t.get("assigneeId") != me["id"] or not t.get("atribuidaPor"):
            return J({"error": "Impedimento é para tarefas recebidas do líder."}, 403)
        motivo = str(corpo.get("motivo") or "").strip()
        if not motivo:
            return J({"error": "Descreva o impedimento."}, 400)
        alterar(c, "tasks", tid, {"impedimento": {"motivo": motivo[:500], "em": iso(), "por": me["name"]}})
        notificar(c, t["atribuidaPor"], "IMPEDIMENTO", "Impedimento sinalizado", f"{me['name']} sinalizou em “{t['title']}”: {motivo[:200]}",
                  {"taskId": tid})
        return task_out(get(c, "tasks", tid))


@app.post("/api/tasks/{tid}/impedimento/resolver")
def resolver_impedimento(tid: str, corpo: dict, x_usuario: str | None = Header(None)):
    """O líder decide: manter (a pessoa segue), novo prazo, ou recolher (rota própria)."""
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario); t = get(c, "tasks", tid)
        if not t or t.get("atribuidaPor") != me["id"]:
            return J({"error": "Quem decide é o líder que atribuiu."}, 403)
        mud: dict = {"impedimento": None}
        if corpo.get("acao") == "prazo":
            prazo = str(corpo.get("prazoMax") or "")[:10]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", prazo):
                return J({"error": "Informe o novo prazo."}, 400)
            mud.update(prazoMax=prazo, avisoPrazo=False)
        nt = alterar(c, "tasks", tid, mud)
        _avaliar_prazo(c, nt)
        notificar(c, t["assigneeId"], "IMPEDIMENTO_RESPOSTA", "Resposta do líder",
                  f"“{t['title']}”: " + (f"novo prazo {mud['prazoMax'][8:]}/{mud['prazoMax'][5:7]}." if "prazoMax" in mud else "mantida com você.") +
                  (f" {corpo['mensagem']}" if corpo.get("mensagem") else ""), {"taskId": tid})
        return task_out(get(c, "tasks", tid))


@app.patch("/api/team/tasks/{tid}")
def editar_atribuida(tid: str, corpo: dict, x_usuario: str | None = Header(None)):
    """Quem atribuiu ajusta o “quê” (título, descrição, prioridade, prazo) enquanto a tarefa não começou."""
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario); t = get(c, "tasks", tid)
        if not t or t.get("atribuidaPor") != me["id"]:
            return J({"error": "Só quem atribuiu ajusta."}, 403)
        if _iniciada(c, t):
            return J({"error": "A tarefa já começou: combine com a pessoa (ou recolha)."}, 409)
        mud = {k: corpo[k] for k in ("title", "description", "priority", "estimateH", "prazoMax") if k in corpo}
        if "priority" in mud:
            mud["priority"] = str(mud["priority"]).upper()
        if mud.get("prazoMax"):
            mud["prazoMax"] = str(mud["prazoMax"])[:10]; mud["avisoPrazo"] = False
        nt = alterar(c, "tasks", tid, mud)
        _avaliar_prazo(c, nt)
        notificar(c, t["assigneeId"], "TAREFA_ALTERADA", "Tarefa ajustada pelo líder", f"{me['name']} ajustou “{nt['title']}”.", {"taskId": tid})
        return task_out(get(c, "tasks", tid))


@app.delete("/api/team/tasks/{tid}")
def cancelar_atribuida(tid: str, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario); t = get(c, "tasks", tid)
        if not t or me["id"] not in (t.get("cadeia") or [])[:-1]:
            return J({"error": "Só quem atribuiu (ou está acima na cadeia) cancela."}, 403)
        if _iniciada(c, t):
            return J({"error": "A tarefa já começou: recolha antes de cancelar."}, 409)
        for b in todos(c, "blocks", lambda b: b["taskId"] == tid):
            apagar(c, "blocks", b["id"])
        alterar(c, "tasks", tid, {"status": "CANCELADA", "canceladaPor": me["name"], "canceladaEm": iso()})
        notificar(c, t["assigneeId"], "TAREFA_CANCELADA", "Tarefa cancelada pelo líder", f"“{t['title']}” saiu da sua agenda.", {"taskId": tid})
        return {"success": True}


def _iniciada(c, t: dict) -> bool:
    return t.get("status") in ("CONCLUIDA", "CANCELADA") or any(b.get("fechamento") for b in todos(c, "blocks", lambda b: b["taskId"] == t["id"]))


# ───────────────────────────────────────────────── fechamento diário e prazo máximo
def _dia_util(c, me: dict, data: str) -> bool:
    return datetime.fromisoformat(data).weekday() < 5 and not _feriado(c, me, data)


def _dias_uteis_entre(c, me: dict, de: str, ate: str) -> int:
    """Dias úteis da pessoa estritamente entre `de` e `ate`."""
    n, d = 0, datetime.fromisoformat(de) + timedelta(days=1)
    fim = datetime.fromisoformat(ate)
    while d < fim and n < 60:
        if _dia_util(c, me, d.strftime("%Y-%m-%d")):
            n += 1
        d += timedelta(days=1)
    return n


def _a_fechar(c, uid: str) -> list[dict]:
    """Blocos de dias anteriores sem fechamento (tarefas suas; OS e outros calendários fecham na origem)."""
    hoje, tasks = _hoje(), {t["id"]: t for t in todos(c, "tasks", lambda t: t.get("assigneeId") == uid)}
    out = []
    for b in todos(c, "blocks", lambda b: b.get("userId") == uid and b.get("date", "9999") < hoje and not b.get("travado") and not b.get("externo")
                    and not b.get("fechamento")):
        t = tasks.get(b["taskId"])
        if t and t.get("status") not in ("CONCLUIDA", "CANCELADA"):
            out.append({"block": b, "task": t})
    return sorted(out, key=lambda x: (x["block"]["date"], x["block"]["slotStart"]))


@app.get("/api/fechamento")
def fechamento_get(x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        users = {u["id"]: u for u in todos(c, "users")}
        return {"itens": [{"blockId": x["block"]["id"], "taskId": x["task"]["id"], "title": x["task"]["title"], "date": x["block"]["date"],
                           "slotStart": x["block"]["slotStart"], "slotEnd": x["block"]["slotEnd"], "horas": (x["block"]["slotEnd"] - x["block"]["slotStart"]) / 2,
                           "estimateH": x["task"].get("estimateH"), "prazoMax": x["task"].get("prazoMax"), "doLider": _do_lider(x["task"], me),
                           "atribuidaPorNome": (users.get(x["task"].get("atribuidaPor")) or {}).get("name"), "pendencia": x["task"].get("pendencia"),
                           "origemUrl": _url_origem(x["task"]), "origem_nome": x["task"].get("origem_nome")} for x in _a_fechar(c, me["id"])]}


@app.post("/api/fechamento")
def fechamento_post(corpo: dict, x_usuario: str | None = Header(None)):
    """concluiu · parcial (restanteH volta ao backlog) · reagendar (volta ao backlog) · nao_executar
    (tarefa do líder → vira impedimento; tarefa de outra ferramenta → volta para quem pode tratar; tarefa sua → cancelada)."""
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario)
        res = []
        for it in corpo.get("itens") or []:
            b = get(c, "blocks", it.get("blockId"))
            if not b or b.get("userId") != me["id"] or b.get("travado") or b.get("externo"):
                res.append({"blockId": it.get("blockId"), "ok": False, "erro": "Bloco não encontrado"}); continue
            t = get(c, "tasks", b["taskId"]) or {}
            acao = it.get("acao")
            futuros = [x for x in todos(c, "blocks", lambda x: x["taskId"] == t.get("id") and x["id"] != b["id"] and x.get("date", "") >= _hoje())]
            r = {"blockId": b["id"], "ok": True, "acao": acao}
            if acao == "concluiu":
                for x in futuros:
                    apagar(c, "blocks", x["id"])
                alterar(c, "tasks", t["id"], {"status": "CONCLUIDA", "concluidaEm": iso(), "critica": False})
                if t.get("pendencia") and t.get("origemStatus") == "aberta":
                    r["aviso"] = f"Ainda aberta em {t.get('origem_nome') or 'outra ferramenta'}: conclua lá."; r["origemUrl"] = _url_origem(t)
                if t.get("atribuidaPor"):
                    notificar(c, t["atribuidaPor"], "TAREFA_CONCLUIDA", "Tarefa concluída", f"{me['name']} concluiu “{t['title']}”.", {"taskId": t["id"]})
            elif acao == "parcial":
                try:
                    rest = max(0.5, round(float(it.get("restanteH")) * 2) / 2)
                except (TypeError, ValueError):
                    res.append({"blockId": b["id"], "ok": False, "erro": "Informe quantas horas faltam"}); continue
                alterar(c, "tasks", t["id"], {"estimateH": rest, "status": "AGENDADA" if futuros else "BACKLOG"})
            elif acao == "reagendar":
                alterar(c, "tasks", t["id"], {"status": "AGENDADA" if futuros else "BACKLOG", "reagendamentos": int(t.get("reagendamentos") or 0) + 1})
            elif acao == "nao_executar":
                motivo = str(it.get("motivo") or "").strip()
                if _do_lider(t, me):
                    if not motivo:
                        res.append({"blockId": b["id"], "ok": False, "erro": "Tarefa do seu líder: descreva o impedimento"}); continue
                    alterar(c, "tasks", t["id"], {"impedimento": {"motivo": motivo[:500], "em": iso(), "por": me["name"]}, "status": "AGENDADA" if futuros else "BACKLOG"})
                    notificar(c, t["atribuidaPor"], "IMPEDIMENTO", "Impedimento sinalizado", f"{me['name']} não vai executar “{t['title']}”: {motivo[:200]}", {"taskId": t["id"]})
                    r["virou"] = "impedimento"
                elif t.get("pendencia"):
                    for x in todos(c, "blocks", lambda x: x["taskId"] == t["id"] and x["id"] != b["id"]):
                        apagar(c, "blocks", x["id"])
                    alterar(c, "tasks", t["id"], {"status": "CANCELADA", "motivoCancelamento": motivo or None})
                    c.execute("DELETE FROM pend_avisadas WHERE chave=?", (t["pendencia"],))  # volta a aparecer para quem pode tratar
                    r["virou"] = "devolvida_ao_ecossistema"
                else:
                    for x in futuros:
                        apagar(c, "blocks", x["id"])
                    alterar(c, "tasks", t["id"], {"status": "CANCELADA", "motivoCancelamento": motivo or None})
            else:
                res.append({"blockId": b["id"], "ok": False, "erro": "Ação inválida"}); continue
            _gravar(c, "blocks", {**b, "fechamento": {"acao": acao, "em": iso(), "restanteH": it.get("restanteH"), "motivo": it.get("motivo")}})
            _avaliar_prazo(c, get(c, "tasks", t["id"]))
            res.append(r)
        for n in todos(c, "notifications", lambda n: n.get("userId") == me["id"] and n.get("type") == "FECHAMENTO" and not n.get("read")):
            if not _a_fechar(c, me["id"]):
                alterar(c, "notifications", n["id"], {"read": True})
        return {"resultados": res, "restantes": len(_a_fechar(c, me["id"]))}


def _avaliar_prazo(c, t: dict | None) -> None:
    """Crítica: passou do prazo máximo sem concluir, ou está agendada depois dele. O líder direto é avisado uma vez
    (prazo de SLA da ferramenta de origem já é alertado por ela)."""
    if not t or not t.get("prazoMax") or t.get("travado") or t.get("externo"):
        return
    aberta = t.get("status") not in ("CONCLUIDA", "CANCELADA")
    blocos = todos(c, "blocks", lambda b: b["taskId"] == t["id"] and not b.get("fechamento"))
    critica = aberta and (t["prazoMax"] < _hoje() or any(b["date"] > t["prazoMax"] for b in blocos))
    mud = {}
    if bool(t.get("critica")) != critica:
        mud["critica"] = critica
    if critica and not t.get("avisoPrazo") and not t.get("prazoOrigem"):
        lider = t.get("atribuidaPor") or (get(c, "users", t.get("assigneeId")) or {}).get("managerId")
        dono = (get(c, "users", t.get("assigneeId")) or {}).get("name") or "a pessoa"
        if lider and lider != t.get("assigneeId"):
            notificar(c, lider, "PRAZO_CRITICO", "Prazo em risco", f"“{t['title']}” ({dono}) passou ou vai passar do prazo máximo de {t['prazoMax'][8:]}/{t['prazoMax'][5:7]}.",
                      {"taskId": t["id"]})
        mud["avisoPrazo"] = True
    if mud:
        alterar(c, "tasks", t["id"], mud)


def _rotina_fechamento(c) -> int:
    """Manhã do dia útil (07:30): quem tem blocos sem fechamento recebe o aviso “Fechar tarefas”."""
    agora_l = _local(iso()) or datetime.now()
    if (agora_l.hour, agora_l.minute) < (7, 30):
        return 0
    hoje, n = agora_l.strftime("%Y-%m-%d"), 0
    for u in todos(c, "users", lambda u: not u.get("inativo")):
        if c.execute("SELECT 1 FROM fechamento_avisos WHERE user_id=? AND dia=?", (u["id"], hoje)).fetchone() or not _dia_util(c, u, hoje):
            continue
        pend = _a_fechar(c, u["id"])
        if pend:
            notificar(c, u["id"], "FECHAMENTO", "Fechar tarefas", f"{len(pend)} tarefa(s) de dias anteriores sem fechamento: concluiu, reagendar ou não vai executar?", {})
            n += 1
        c.execute("INSERT OR REPLACE INTO fechamento_avisos(user_id,dia) VALUES (?,?)", (u["id"], hoje))
    for t in todos(c, "tasks", lambda t: t.get("prazoMax") and t.get("status") not in ("CONCLUIDA", "CANCELADA")):
        _avaliar_prazo(c, t)
    return n


# ───────────────────────────────────────────────── visão da equipe (Gantt de pessoas)
@app.get("/api/team/gantt")
def gantt(inicio: str | None = None, dias: int = 7, de: str | None = None, x_usuario: str | None = Header(None)):
    """Linhas = quem responde diretamente a `de` (padrão: você); `de` pode ser você ou alguém abaixo de você (descer
    na hierarquia). Compromissos de outros calendários aparecem só como “Ocupado”."""
    with B.lock, B.con() as c:
        me, _ = eu(c, x_usuario); _sync_pessoas(c)
        raiz = de or me["id"]
        if raiz != me["id"] and me["id"] not in _acima(c, raiz):
            return J({"error": "Você só vê a sua equipe e as equipes abaixo dela."}, 403)
        ini = inicio or _hoje()
        dias = max(1, min(31, int(dias or 7)))
        fim = add_dias(ini, dias)
        users = {u["id"]: u for u in todos(c, "users")}
        tasks = {t["id"]: t for t in todos(c, "tasks")}
        pessoas = sorted([u for u in users.values() if u.get("managerId") == raiz and not u.get("inativo")], key=lambda u: u.get("name") or "")
        lideres = {u.get("managerId") for u in users.values() if not u.get("inativo")}
        hoje = _hoje()
        out = []
        for p in pessoas:
            wh = {w.get("dayOfWeek"): w for w in todos(c, "workHours", lambda w: w.get("userId") == p["id"])}
            blocos, carga = [], {}
            for b in todos(c, "blocks", lambda b: b.get("userId") == p["id"] and ini <= b.get("date", "") < fim):
                t = tasks.get(b["taskId"]) or {}
                h = (b["slotEnd"] - b["slotStart"]) / 2
                carga[b["date"]] = carga.get(b["date"], 0) + h
                tipo = "externo" if b.get("externo") else ("os" if b.get("os") else "tarefa")
                blocos.append({"id": b["id"], "taskId": b["taskId"], "date": b["date"], "slotStart": b["slotStart"], "slotEnd": b["slotEnd"], "tipo": tipo,
                               "title": "Ocupado" if tipo == "externo" else t.get("title"), "priority": t.get("priority"), "critica": bool(t.get("critica")),
                               "travado": bool(b.get("travado")), "fechamento": (b.get("fechamento") or {}).get("acao"), "feriado": b.get("feriado"),
                               "status": t.get("status")})
            atraso = sum(1 for x in _a_fechar(c, p["id"]) if _dias_uteis_entre(c, p, x["block"]["date"], hoje) >= 2)
            backlog = [{"id": t["id"], "title": t["title"], "priority": t.get("priority"), "estimateH": t.get("estimateH"), "prazoMax": t.get("prazoMax"),
                        "critica": bool(t.get("critica")), "impedimento": t.get("impedimento"), "reatribuir": bool(t.get("reatribuir")),
                        "atribuidaPorMim": t.get("atribuidaPor") == me["id"]}
                       for t in tasks.values() if t.get("assigneeId") == p["id"] and t.get("status") == "BACKLOG" and not t.get("travado") and not t.get("externo")]
            out.append({"id": p["id"], "name": p.get("name"), "email": p.get("email"), "temEquipe": p["id"] in lideres,
                        "jornada": {str(d): float((wh.get(d) or {}).get("maxHours") or 0) if (wh.get(d) or {}).get("isActive", True) else 0 for d in range(7)},
                        "carga": carga, "blocos": blocos, "fechamentoPendente": atraso, "backlog": backlog})
        reatribuir = [{"id": t["id"], "title": t["title"], "com": (users.get(t["assigneeId"]) or {}).get("name")}
                      for t in tasks.values() if t.get("atribuidaPor") == me["id"] and t.get("reatribuir") and t.get("status") not in ("CONCLUIDA", "CANCELADA")]
        cam = [raiz] + _acima(c, raiz)
        caminho = [{"id": x, "name": (users.get(x) or {}).get("name")} for x in reversed(cam[:cam.index(me["id"]) + 1])] if me["id"] in cam else []
        return {"inicio": ini, "dias": dias, "raiz": {"id": raiz, "name": (users.get(raiz) or {}).get("name")}, "caminho": caminho,
                "podeSugerir": raiz == me["id"], "pessoas": out, "reatribuir": reatribuir}


# ───────────────────────────────────────────────── lidas: agenda ⇄ hub (vale em todo lugar)
def _sync_lidas(c) -> int:
    if not hubkit.no_hub():
        return 0
    n = 0
    for r in c.execute("SELECT * FROM vinculos WHERE usuario_hub LIKE 'h-%'").fetchall():
        uid, hid = r["user_id"], r["usuario_hub"][2:]
        row = c.execute("SELECT desde FROM lidas_sync WHERE user_id=?", (uid,)).fetchone()
        lidas = hubkit.notificacoes_lidas(hid, row["desde"] if row else 0)
        if not lidas:
            continue
        chaves = {x["chave"] for x in lidas}
        for nt in todos(c, "notifications", lambda x: x.get("userId") == uid and not x.get("read") and (load(x.get("data"), {}) or {}).get("pendencia") in chaves):
            alterar(c, "notifications", nt["id"], {"read": True}); n += 1
        c.execute("INSERT OR REPLACE INTO lidas_sync(user_id,desde) VALUES (?,?)", (uid, max(float(x["em"]) for x in lidas)))
    return n


# ───────────────────────────────────────────────── laço: entrega das pendências do ecossistema
def _sincronizar(c, u) -> dict:
    hier = _sync_pessoas(c, ttl=0)  # estrutura sempre fresca na sincronização (as telas usam o cache de 1 min)
    n = 0
    for r in B.pendentes(c, "agenda", "pendencia"):
        p = r.get("payload") or {}
        n += _entregar(c, r["entity_key"], p)
        B.marcar_recebido(c, r, p.get("status") or "")
    ext = 0
    for r in B.pendentes(c, "agenda", "ocupado"):
        p = r.get("payload") or {}
        ext += _externo(c, r["entity_key"], p)
        B.marcar_recebido(c, r, p.get("status") or "")
    return {"pendencias_entregues": n, "compromissos_externos": ext, "compromissos_publicados": _publicar_compromissos(c), "hierarquia": hier,
            "avisos_fechamento": _rotina_fechamento(c), "lidas_do_hub": _sync_lidas(c)}


B.automatico(_sincronizar, None, intervalo_s=120)


# ───────────────────────────────────────────────── interface (build web em modo servidor)
STATIC = BASE_DIR / "static"


@app.get("/{caminho:path}", include_in_schema=False)
def spa(caminho: str):
    alvo = (STATIC / caminho).resolve()
    if caminho and alvo.is_file() and STATIC.resolve() in alvo.parents:
        return FileResponse(alvo, headers={"Cache-Control": "max-age=31536000, immutable" if caminho.startswith("assets/") else "no-cache"})
    if caminho.startswith("api/"):
        return J({"error": "Rota não encontrada"}, 404)
    idx = STATIC / "index.html"
    return FileResponse(idx, headers={"Cache-Control": "no-cache"}) if idx.is_file() else J({"error": "Interface não empacotada (rode o build em modo servidor)."}, 503)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=os.environ.get("STRATAGENDA_HOST", "127.0.0.1"), port=int(os.environ.get("PORT") or 8870),
                root_path=os.environ.get("HUB_ROOT_PATH", ""))
