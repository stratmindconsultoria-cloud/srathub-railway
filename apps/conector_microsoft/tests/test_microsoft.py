"""Testes do Conector Microsoft com uma Microsoft simulada (login por código, Graph de calendário e de aprovações).
python -m pytest -q"""
import json, importlib, os, sys, urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ADM = {"X-Usuario": "u-demo-adm"}; USU = {"X-Usuario": "u-demo-usu"}
APP_ID = "11111111-2222-3333-4444-555555555555"


class FakeMS:
    """Imita login.microsoftonline.com e graph.microsoft.com o suficiente para o conector."""

    def __init__(self):
        self.pendente = 1          # quantas verificações devolvem authorization_pending
        self.eventos = {}          # id → evento (calendário da pessoa)
        self.externos = []         # eventos do Outlook da pessoa
        self.aprovacoes = {}       # id → {corpo, respostas, cancelada}
        self.chamadas = []
        self.usuarios = {"u-demo-adm@demo.local": {"id": "ms-adm", "displayName": "Adm"}, "aprovador@empresa.com": {"id": "ms-apr", "displayName": "Aprovadora"}}
        self.n = 0
        self.teams = {}            # aprovações criadas direto no Teams: id → item (com "respostas")
        self.sem_consentimento = False
        self.recusar_criacao = False
        self.sem_permissao = set()   # escopos sem consentimento do administrador
        self.enviados = []           # e-mails, avisos no feed e posts em canal

    def __call__(self, metodo, url, json_corpo=None, form=None, token=None, cabecalhos=None):
        self.chamadas.append((metodo, url))
        u = urllib.parse.urlparse(url); p = u.path
        if p.endswith("/devicecode"):
            assert form["client_id"] == APP_ID and "Calendars.ReadWrite" in form["scope"]
            return 200, {"device_code": "DC", "user_code": "ABCD-1234", "verification_uri": "https://microsoft.com/devicelogin", "expires_in": 900, "interval": 5}, {}
        if p.endswith("/token"):
            if form["grant_type"].endswith("device_code"):
                if self.pendente:
                    self.pendente -= 1
                    return 400, {"error": "authorization_pending"}, {}
                return 200, {"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600}, {}
            assert form["grant_type"] == "refresh_token"
            if any(x in form["scope"].split() for x in self.sem_permissao):
                return 400, {"error": "invalid_grant", "error_description": "AADSTS65001: The user or administrator has not consented"}, {}
            if self.sem_consentimento and "ApprovalSolutionResponse" in form["scope"]:
                return 400, {"error": "invalid_grant", "error_description": "AADSTS65001: The user or administrator has not consented"}, {}
            return 200, {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 3600}, {}
        assert token, "Graph sem token"
        if p == "/v1.0/me":
            return 200, {"id": "ms-adm", "displayName": "Adm Microsoft", "userPrincipalName": "u-demo-adm@demo.local"}, {}
        if p == "/v1.0/me/sendMail":
            self.enviados.append(("email", json_corpo)); return 202, {}, {}
        if p.endswith("/teamwork/sendActivityNotification"):
            self.enviados.append(("feed", p.split("/users/")[1].split("/")[0], json_corpo)); return 204, {}, {}
        if p == "/v1.0/me/joinedTeams":
            return 200, {"value": [{"id": "T1", "displayName": "Manutenção"}]}, {}
        if p == "/v1.0/teams/T1/channels":
            return 200, {"value": [{"id": "C1", "displayName": "Alertas"}]}, {}
        if p == "/v1.0/teams/T1/channels/C1/messages":
            self.enviados.append(("canal", json_corpo)); return 201, {"id": "m1"}, {}
        if p.startswith("/v1.0/users/"):
            e = urllib.parse.unquote(p.split("/users/")[1])
            return (200, {**self.usuarios[e], "mail": e}, {}) if e in self.usuarios else (404, {"error": {"code": "Request_ResourceNotFound", "message": "x"}}, {})
        if p == "/v1.0/me/events" and metodo == "POST":
            self.n += 1; eid = f"ev{self.n}"; self.eventos[eid] = json_corpo
            return 201, {"id": eid}, {}
        if p == "/v1.0/me/events" and metodo == "GET":
            return 200, {"value": []}, {}
        if p.startswith("/v1.0/me/events/"):
            eid = p.rsplit("/", 1)[1]
            if eid not in self.eventos:
                return 404, {"error": {"code": "ErrorItemNotFound", "message": "x"}}, {}
            if metodo == "PATCH":
                self.eventos[eid] = json_corpo; return 200, {"id": eid}, {}
            if metodo == "DELETE":
                del self.eventos[eid]; return 204, {}, {}
        if p == "/v1.0/me/calendarView":
            nossos = [{"id": k, "subject": v["subject"], "start": v["start"], "end": v["end"], "showAs": "busy", "categories": v["categories"]} for k, v in self.eventos.items()]
            return 200, {"value": nossos + self.externos}, {}
        if p == "/beta/solutions/approval":
            return 200, {"provisioningStatus": "notProvisioned"}, {}
        if p == "/beta/solutions/approval/provision":
            return 202, {}, {}
        if p == "/beta/solutions/approval/approvalItems" and metodo == "POST" and self.recusar_criacao:
            return 403, {"error": {"code": "Forbidden", "message": "Approvals não habilitado"}}, {}
        if p == "/beta/solutions/approval/approvalItems" and metodo == "GET":
            nossas = [{"id": k, "displayName": v["corpo"]["displayName"], "state": "pending", "approvers": v["corpo"]["approvers"]} for k, v in self.aprovacoes.items()]
            return 200, {"value": nossas + [{k: v for k, v in t.items() if k != "respostas"} for t in self.teams.values()]}, {}
        if p.endswith("/responses") and metodo == "POST":
            aid = p.split("/approvalItems/")[1].split("/")[0]
            if self.sem_consentimento:
                return 403, {"error": {"code": "Forbidden", "message": "Missing scope"}}, {}
            self.teams[aid]["respostas"].append({**json_corpo, "createdBy": {"user": {"id": "ms-adm"}}})
            return 201, {}, {}
        if p == "/beta/solutions/approval/approvalItems" and metodo == "POST":
            self.n += 1; aid = f"ap{self.n}"
            self.aprovacoes[aid] = {"corpo": json_corpo, "respostas": [], "cancelada": False}
            return 202, {}, {"Location": f"https://graph.microsoft.com/beta/solutions/approval/operations/op-{aid}"}
        if p.startswith("/beta/solutions/approval/operations/op-"):
            aid = p.rsplit("op-", 1)[1]
            return 200, {"status": "succeeded", "resourceLocation": f"https://graph.microsoft.com/beta/solutions/approval/approvalItems/{aid}"}, {}
        if p.endswith("/responses"):
            aid = p.split("/approvalItems/")[1].split("/")[0]
            return 200, {"value": (self.aprovacoes.get(aid) or self.teams[aid])["respostas"]}, {}
        if p.endswith("/cancel"):
            aid = p.split("/approvalItems/")[1].split("/")[0]
            self.aprovacoes[aid]["cancelada"] = True
            return 202, {}, {}
        raise AssertionError(f"rota não simulada: {metodo} {url}")


@pytest.fixture()
def amb(tmp_path):
    os.environ["CONECTOR_MICROSOFT_DADOS"] = str(tmp_path)
    os.environ.pop("HUB_URL", None); os.environ.pop("HUB_APP_TOKEN", None)
    import hubkit, appkit, app as modulo
    importlib.reload(hubkit); importlib.reload(appkit); importlib.reload(modulo)
    ms = FakeMS(); modulo._http = ms
    agidas = []
    modulo.hubkit.agir = lambda app, caminho, pessoa, corpo=None, metodo="POST": agidas.append((app, caminho, pessoa, corpo, metodo)) or {"status": 200, "resposta": {"status": "encerrada"}}
    with TestClient(modulo.app) as c:
        c.mod, c.ms, c.agidas = modulo, ms, agidas
        yield c


def conectar(c, h=ADM, emissor=True):
    c.put("/api/config", json={"client_id": APP_ID}, headers=ADM)
    r = c.post("/api/conta/conectar", headers=h).json()
    assert r["codigo"] == "ABCD-1234" and "devicelogin" in r["endereco"]
    assert c.post("/api/conta/verificar", headers=h).json() == {"pendente": True}
    v = c.post("/api/conta/verificar", headers=h).json()
    assert v["pendente"] is False and v["conta"]["ms_upn"] == "u-demo-adm@demo.local"
    if emissor:  # o administrador escolhe a conta emissora (nunca é escolhida sozinha)
        assert c.put("/api/config", json={"emissor": v["conta"]["id_hub"]}, headers=ADM).status_code == 200
    return v


def test_configuracao_e_login_por_codigo(amb):
    c = amb
    assert c.post("/api/conta/conectar", headers=ADM).status_code == 409          # sem ID do aplicativo
    assert c.put("/api/config", json={"client_id": "abc"}, headers=ADM).status_code == 400
    assert c.put("/api/config", json={"client_id": APP_ID}, headers=USU).status_code == 403
    conectar(c)
    cfg = c.get("/api/config", headers=ADM).json()
    assert cfg["emissor"] == "u-demo-adm" and len(cfg["contas"]) == 1 and "refresh" not in str(cfg)
    with c.mod.B.con() as con:
        guardado = con.execute("SELECT refresh FROM contas").fetchone()[0]
    assert not guardado.startswith("RT") and c.mod._ler(guardado) in ("RT1", "RT2")  # token cifrado
    c.mod._tokens.clear()
    assert c.post("/api/conta/testar", headers=ADM).json()["ok"]                    # renova pelo refresh
    with c.mod.B.con() as con:
        assert c.mod._ler(con.execute("SELECT refresh FROM contas").fetchone()[0]) == "RT2"
    assert c.get("/api/conta", headers=ADM).json()["emissor"] is True
    assert c.delete("/api/conta", headers=ADM).json()["ok"] and c.get("/api/conta", headers=ADM).json()["conta"] is None


def test_calendario_nos_dois_sentidos(amb):
    c, ms, hk = amb, amb.ms, amb.mod.hubkit
    conectar(c)
    d = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    hk.publicar("agenda", "compromisso", "stratagenda:b1", {"status": "ativo", "pessoa": {"email": "u-demo-adm@demo.local"}, "titulo": "Preparar comitê",
                "inicio": f"{d}T10:00:00-03:00", "fim": f"{d}T12:00:00-03:00", "travado": False}, source_application="stratagenda")
    hk.publicar("agenda", "compromisso", "stratagenda:b2", {"status": "ativo", "pessoa": {"email": "outra@x.com"}, "titulo": "De outra pessoa",
                "inicio": f"{d}T10:00:00-03:00", "fim": f"{d}T11:00:00-03:00"}, source_application="stratagenda")
    ms.externos = [{"id": "out1", "subject": "Almoço com cliente", "start": {"dateTime": f"{d}T15:00:00.0000000"}, "end": {"dateTime": f"{d}T16:00:00.0000000"}, "showAs": "busy"},
                   {"id": "out2", "subject": "Particular", "sensitivity": "private", "start": {"dateTime": f"{d}T18:00:00"}, "end": {"dateTime": f"{d}T19:00:00"}, "showAs": "busy"},
                   {"id": "out3", "subject": "Livre", "start": {"dateTime": f"{d}T20:00:00"}, "end": {"dateTime": f"{d}T21:00:00"}, "showAs": "free"}]
    r = c.post("/api/sync/agora", headers=ADM).json()["resultado"]["calendario"]["Administrador Demo"]
    assert r == {"criados": 1, "alterados": 0, "removidos": 0, "ocupados": 2}
    ev = next(iter(ms.eventos.values()))
    assert ev["subject"] == "Preparar comitê" and ev["start"] == {"dateTime": f"{d}T13:00:00", "timeZone": "UTC"} and "STRAT·HUB" in ev["categories"]
    ocup = {x["entity_key"]: x["payload"] for x in hk.consultar("agenda", "ocupado")}
    assert ocup["microsoft:u-demo-adm:out1"]["titulo"] == "Almoço com cliente" and ocup["microsoft:u-demo-adm:out2"]["titulo"] == "Ocupado"
    assert not any("ev" in k.split(":")[-1] for k in ocup)                      # o evento criado pelo conector não volta
    # remarcar → altera; remover → apaga; evento do Outlook some → ocupado removido
    hk.publicar("agenda", "compromisso", "stratagenda:b1", {"status": "ativo", "pessoa": {"email": "u-demo-adm@demo.local"}, "titulo": "Preparar comitê",
                "inicio": f"{d}T14:00:00-03:00", "fim": f"{d}T15:00:00-03:00"}, source_application="stratagenda")
    ms.externos = ms.externos[1:]
    r = c.post("/api/sync/agora", headers=ADM).json()["resultado"]["calendario"]["Administrador Demo"]
    assert r["alterados"] == 1 and next(iter(ms.eventos.values()))["start"]["dateTime"] == f"{d}T17:00:00"
    assert hk.consultar("agenda", "ocupado", entity_key="microsoft:u-demo-adm:out1")[0]["payload"]["status"] == "removido"
    hk.publicar("agenda", "compromisso", "stratagenda:b1", {"status": "removido"}, source_application="stratagenda")
    c.post("/api/sync/agora", headers=ADM)
    assert ms.eventos == {}


def test_aprovacao_no_teams_executa_na_origem(amb):
    c, ms, hk = amb, amb.ms, amb.mod.hubkit
    conectar(c)
    acoes = [{"codigo": "encerrar", "rotulo": "Aprovar e encerrar", "metodo": "POST", "caminho": "/api/ordens/OS-1/acoes/encerrar", "corpo": {}, "comentario": "nenhum", "campo": "comentario"},
             {"codigo": "devolver", "rotulo": "Devolver ao executor", "metodo": "POST", "caminho": "/api/ordens/OS-1/acoes/devolver", "corpo": {}, "comentario": "obrigatorio", "campo": "motivo"}]
    base = {"status": "aberta", "natureza": "acao", "titulo": "Aprovar encerramento: OS-1", "origem_app": "ordens", "origem_nome": "Ordens de Serviço",
            "destinatarios": [{"email": "aprovador@empresa.com"}, {"email": "naoexiste@empresa.com"}], "acoes": acoes}
    hk.publicar("agenda", "pendencia", "ordens:aprovar:OS-1", base, source_application="ordens")
    hk.publicar("agenda", "pendencia", "ordens:aprovar:OS-2", {**base, "titulo": "OS-2", "acoes": [{**a, "caminho": a["caminho"].replace("OS-1", "OS-2")} for a in acoes]}, source_application="ordens")
    hk.publicar("agenda", "pendencia", "pmp:rodada:x", {"status": "aberta", "natureza": "tarefa", "titulo": "não é ação"}, source_application="pmp")
    r = c.post("/api/sync/agora", headers=ADM).json()["resultado"]["aprovacoes"]
    assert r["criadas"] == 2
    a1 = next(v for v in ms.aprovacoes.values() if v["corpo"]["displayName"].startswith("Aprovar encerramento"))
    assert a1["corpo"]["responsePrompts"] == ["Aprovar e encerrar", "Devolver ao executor"] and [x["user"]["id"] for x in a1["corpo"]["approvers"]] == ["ms-apr"]
    assert ("POST", "https://graph.microsoft.com/beta/solutions/approval/provision") in ms.chamadas
    # 1) responde no Teams → executa na origem em nome de quem respondeu
    aid1 = next(k for k, v in ms.aprovacoes.items() if v is a1)
    a1["respostas"] = [{"response": "Aprovar e encerrar", "comments": "", "createdDateTime": "2026-11-03T12:00:00Z", "createdBy": {"user": {"id": "ms-apr"}}}]
    # 2) a OS-2 foi resolvida na agenda antes → cancela no Teams
    hk.publicar("agenda", "pendencia", "ordens:aprovar:OS-2", {"status": "resolvida"}, source_application="ordens")
    r = c.post("/api/sync/agora", headers=ADM).json()["resultado"]["aprovacoes"]
    assert r["executadas"] == 1 and r["canceladas"] == 1
    assert c.agidas == [("ordens", "/api/ordens/OS-1/acoes/encerrar", {"email": "aprovador@empresa.com"}, {}, "POST")]
    assert any(v["cancelada"] for k, v in ms.aprovacoes.items() if k != aid1)
    at = {x["pendencia"]: x for x in c.get("/api/atividade", headers=ADM).json()["aprovacoes"]}
    assert at["ordens:aprovar:OS-1"]["estado"] == "executada" and at["ordens:aprovar:OS-1"]["respondido_por"] == "aprovador@empresa.com"
    assert at["ordens:aprovar:OS-2"]["estado"] == "cancelada"
    # 3) devolver sem comentário (obrigatório) não executa: falha explicada
    hk.publicar("agenda", "pendencia", "ordens:aprovar:OS-3", {**base, "titulo": "OS-3"}, source_application="ordens")
    c.post("/api/sync/agora", headers=ADM)
    a3 = next(v for v in ms.aprovacoes.values() if v["corpo"]["displayName"] == "OS-3")
    a3["respostas"] = [{"response": "Devolver ao executor", "comments": "", "createdBy": {"user": {"id": "ms-apr"}}}]
    c.post("/api/sync/agora", headers=ADM)
    x = {x["pendencia"]: x for x in c.get("/api/atividade", headers=ADM).json()["aprovacoes"]}["ordens:aprovar:OS-3"]
    assert x["estado"] == "falha" and "comentário" in x["erro"] and len(c.agidas) == 1
    # 4) com comentário, vai no campo pedido pela ação (motivo)
    hk.publicar("agenda", "pendencia", "ordens:aprovar:OS-4", {**base, "titulo": "OS-4"}, source_application="ordens")
    c.post("/api/sync/agora", headers=ADM)
    a4 = next(v for v in ms.aprovacoes.values() if v["corpo"]["displayName"] == "OS-4")
    a4["respostas"] = [{"response": "Devolver ao executor", "comments": "Falta a foto do quadro", "createdBy": {"user": {"id": "ms-apr"}}}]
    c.post("/api/sync/agora", headers=ADM)
    assert c.agidas[-1][3] == {"motivo": "Falta a foto do quadro", "comentario": "Falta a foto do quadro"}


def test_sem_aprovador_e_desligado(amb):
    c, hk = amb, amb.mod.hubkit
    conectar(c)
    hk.publicar("agenda", "pendencia", "x:y", {"status": "aberta", "natureza": "acao", "titulo": "Sem ninguém", "destinatarios": [{"email": "ninguem@x.com"}],
                "acoes": [{"codigo": "a", "rotulo": "A", "caminho": "/api/a"}]}, source_application="x")
    c.post("/api/sync/agora", headers=ADM)
    assert c.get("/api/atividade", headers=ADM).json()["aprovacoes"][0]["estado"] == "sem_aprovador"
    at = c.get("/api/atividade", headers=ADM).json()["aprovacoes"][0]
    assert "ninguem@x.com" in at["erro"] and "mesmo e-mail da conta Microsoft" in at["erro"]
    # e-mail corrigido no hub → a ferramenta republica a pendência com o e-mail certo → a aprovação vai para o Teams
    hk.publicar("agenda", "pendencia", "x:y", {"status": "aberta", "natureza": "acao", "titulo": "Sem ninguém", "destinatarios": [{"email": "aprovador@empresa.com"}],
                "acoes": [{"codigo": "a", "rotulo": "A", "caminho": "/api/a"}]}, source_application="x")
    c.post("/api/sync/agora", headers=ADM)
    assert c.get("/api/atividade", headers=ADM).json()["aprovacoes"][0]["estado"] in ("criando", "criada")
    c.put("/api/config", json={"aprovacoes": False, "calendario": False}, headers=ADM)
    r = c.post("/api/sync/agora", headers=ADM).json()["resultado"]
    assert r["calendario"] == {} and "desligadas" in r["aprovacoes"]["aviso"] and "teams" not in r
    assert "desligadas" in c.get("/api/atividade", headers=ADM).json()["sincronizacao"]["aprovacoes"]["aviso"]
    assert c.get("/api/atividade", headers=USU).json()["aprovacoes"] == []          # usuário comum não vê as aprovações de todos


def test_aprovacao_criada_no_teams_vira_acao_na_agenda(amb):
    c, ms, hk = amb, amb.ms, amb.mod.hubkit
    conectar(c)
    ms.teams = {"t1": {"id": "t1", "displayName": "teste", "description": "Compra de material", "state": "pending", "approvers": [{"user": {"id": "ms-adm"}}],
                       "owner": {"user": {"displayName": "Fulano"}}, "respostas": []},
                "t2": {"id": "t2", "displayName": "Escolha", "state": "pending", "viewPoint": {"roles": ["approver"]}, "responsePrompts": ["Opção A", "Opção B"], "respostas": []},
                "t3": {"id": "t3", "displayName": "De outra pessoa", "state": "pending", "approvers": [{"user": {"id": "ms-x"}}], "respostas": []},
                "t4": {"id": "t4", "displayName": "Já encerrada", "state": "completed", "approvers": [{"user": {"id": "ms-adm"}}], "respostas": []}}
    st = c.post("/api/sync/agora", headers=ADM).json()
    assert st["resultado"]["teams"]["Administrador Demo"] == 2
    pend = {x["entity_key"]: x["payload"] for x in hk.consultar("agenda", "pendencia")}
    p1 = pend["conector_microsoft:teams:t1:u-demo-adm"]
    assert p1["status"] == "aberta" and p1["natureza"] == "acao" and p1["titulo"] == "Teams: teste" and "Fulano" in p1["descricao"]
    assert [(a["rotulo"], a["corpo"]) for a in p1["acoes"]] == [("Aprovar", {"resposta": "Approve"}), ("Rejeitar", {"resposta": "Reject"})]
    assert p1["acoes"][0]["caminho"] == "/api/teams/t1/responder" and p1["destinatarios"][0]["id_hub"] == "u-demo-adm"
    assert [a["rotulo"] for a in pend["conector_microsoft:teams:t2:u-demo-adm"]["acoes"]] == ["Opção A", "Opção B"]
    assert "conector_microsoft:teams:t3:u-demo-adm" not in pend and "conector_microsoft:teams:t4:u-demo-adm" not in pend
    # respondeu na agenda → vai para o Teams em nome da pessoa e a pendência fecha
    assert c.post("/api/teams/t1/responder", json={"resposta": "Talvez"}, headers=ADM).status_code == 400
    assert c.post("/api/teams/t1/responder", json={"resposta": "Approve"}, headers=USU).status_code == 404   # não é aprovador
    assert c.post("/api/teams/t1/responder", json={"resposta": "Approve", "comentario": "ok, pode comprar"}, headers=ADM).json()["ok"]
    assert ms.teams["t1"]["respostas"][0]["response"] == "Approve" and ms.teams["t1"]["respostas"][0]["comments"] == "ok, pode comprar"
    # respondeu no Teams → some da agenda
    ms.teams["t2"]["respostas"] = [{"response": "Opção B", "createdBy": {"user": {"id": "ms-adm"}}}]
    c.post("/api/sync/agora", headers=ADM)
    pend = {x["entity_key"]: x["payload"] for x in hk.consultar("agenda", "pendencia")}
    assert pend["conector_microsoft:teams:t1:u-demo-adm"]["status"] == "resolvida" and pend["conector_microsoft:teams:t2:u-demo-adm"]["status"] == "resolvida"


def test_permissao_nova_sem_consentimento_nao_derruba_o_resto(amb):
    c, ms = amb, amb.ms
    conectar(c)
    ms.sem_consentimento = True
    c.mod._tokens.clear()
    assert c.post("/api/conta/testar", headers=ADM).json()["ok"]                     # calendário segue funcionando
    ms.teams = {"t9": {"id": "t9", "displayName": "x", "state": "pending", "approvers": [{"user": {"id": "ms-adm"}}], "respostas": []}}
    c.post("/api/sync/agora", headers=ADM)
    r = c.post("/api/teams/t9/responder", json={"resposta": "Approve"}, headers=ADM)
    assert r.status_code == 409 and "ApprovalSolutionResponse.ReadWrite" in r.json()["detail"]


def test_sem_emissor_aguarda_e_recusa_visivel(amb):
    """Sem conta emissora escolhida nada é criado (e a Atividade avisa); recusa da Microsoft aparece e é tentada de novo."""
    c, ms, hk = amb, amb.ms, amb.mod.hubkit
    conectar(c, emissor=False)
    hk.publicar("agenda", "pendencia", "x:z", {"status": "aberta", "natureza": "acao", "titulo": "Recusada", "destinatarios": [{"email": "aprovador@empresa.com"}],
                "acoes": [{"codigo": "a", "rotulo": "A", "caminho": "/api/a"}]}, source_application="x")
    r = c.post("/api/sync/agora", headers=ADM).json()["resultado"]
    assert "Nenhuma conta emissora" in r["aprovacoes"]["aviso"] and not c.get("/api/config", headers=ADM).json()["emissor"]
    assert not ms.aprovacoes
    c.put("/api/config", json={"emissor": "u-demo-adm"}, headers=ADM)
    ms.recusar_criacao = True
    r = c.post("/api/sync/agora", headers=ADM).json()["resultado"]
    assert r["aprovacoes"]["falhas"] == 1
    at = c.get("/api/atividade", headers=ADM).json()
    a = next(x for x in at["aprovacoes"] if x["pendencia"] == "x:z")
    assert a["estado"] == "erro" and "403" in a["erro"] and "ApprovalSolution.ReadWrite" in a["erro"]
    assert at["sincronizacao"]["aprovacoes"]["falhas"] == 1
    # liberado na Microsoft → nova tentativa (forçada aqui) cria no Teams
    ms.recusar_criacao = False
    with amb.mod.B.con() as cc:
        cc.execute("UPDATE aprovacoes SET atualizado_em='2000-01-01' WHERE pendencia='x:z'")
    c.post("/api/sync/agora", headers=ADM)
    a = next(x for x in c.get("/api/atividade", headers=ADM).json()["aprovacoes"] if x["pendencia"] == "x:z")
    assert a["estado"] in ("criando", "criada")


def test_aprovador_pela_conta_conectada_so_depois_de_conferida(amb):
    """O cadastro do hub tem um e-mail pessoal e a pessoa conectou a conta da empresa: até o administrador confirmar,
    a conta conectada não vale (ninguém recebe as aprovações de outro conectando a conta errada); confirmada, vale."""
    c, ms, hk = amb, amb.ms, amb.mod.hubkit
    v = conectar(c)
    assert v["conta"]["confere"] is False  # demo sem e-mail no cadastro
    hk.publicar("agenda", "pendencia", "x:w", {"status": "aberta", "natureza": "acao", "titulo": "E-mail pessoal",
                "destinatarios": [{"id_hub": "u-demo-adm", "email": "pessoal@gmail.com"}],
                "acoes": [{"codigo": "a", "rotulo": "A", "caminho": "/api/a"}]}, source_application="x")
    c.post("/api/sync/agora", headers=ADM)
    a = next(x for x in c.get("/api/atividade", headers=ADM).json()["aprovacoes"] if x["pendencia"] == "x:w")
    assert a["estado"] == "sem_aprovador"
    assert c.post("/api/contas/u-demo-adm/confirmar", headers=USU).status_code == 403
    assert c.post("/api/contas/u-demo-adm/confirmar", headers=ADM).json()["confere"] is True
    c.post("/api/sync/agora", headers=ADM)
    a = next(x for x in c.get("/api/atividade", headers=ADM).json()["aprovacoes"] if x["pendencia"] == "x:w")
    assert a["estado"] in ("criando", "criada") and a["aprovadores"][0]["id"] == "ms-adm"
    corpo = next(v["corpo"] for v in ms.aprovacoes.values() if v["corpo"]["displayName"] == "E-mail pessoal")
    assert corpo["approvers"] == [{"user": {"id": "ms-adm", "displayName": "Adm Microsoft"}}]


def test_mesma_conta_microsoft_em_duas_pessoas_e_bloqueada(amb):
    c = amb
    conectar(c)
    c.post("/api/conta/conectar", headers=USU); c.post("/api/conta/verificar", headers=USU)
    amb.ms.pendente = 0
    r = c.post("/api/conta/verificar", headers=USU)
    assert r.status_code == 409 and "já está conectada" in r.json()["detail"]


def test_notificacoes_do_hub_email_feed_e_canal(amb):
    """Pedidos de envio do hub (notificacao/envio) saem pela conta emissora; o estado diz ao hub o que está pronto."""
    c, ms, hk = amb, amb.ms, amb.mod.hubkit
    conectar(c)
    hk.publicar("notificacao", "envio", "email:p1:x", {"canal": "email", "situacao": "pendente", "pessoa": {"id": "p1", "email": "aprovador@empresa.com"},
                "assunto": "[STRAT·HUB] Aprovar OS", "html": "<p>oi</p>"}, source_application="strathub")
    hk.publicar("notificacao", "envio", "teams:u-demo-adm:x", {"canal": "teams", "situacao": "pendente", "pessoa": {"id": "u-demo-adm", "email": ""},
                "titulo": "Aprovar OS 7", "texto": "Ordens", "url": "https://hub/apps/ordens/#/os/7"}, source_application="strathub")
    hk.publicar("notificacao", "envio", "canal:ordens:sla:7", {"canal": "canal", "situacao": "pendente", "ferramenta": "ordens", "titulo": "SLA estourado", "url": "https://hub/x"},
                source_application="strathub")
    c.post("/api/contas/u-demo-adm/confirmar", headers=ADM)
    r = c.post("/api/sync/agora", headers=ADM).json()["resultado"]
    assert r["notificacoes"] == {"enviadas": 2, "falhas": 1}  # canal ainda não escolhido
    em, fd = next(e for e in ms.enviados if e[0] == "email"), next(e for e in ms.enviados if e[0] == "feed")
    assert len(ms.enviados) == 2 and em[1]["message"]["toRecipients"][0]["emailAddress"]["address"] == "aprovador@empresa.com"
    assert fd[1] == "ms-adm" and fd[2]["activityType"] == "pendencia" and "teams.microsoft.com/l/entity/" in fd[2]["topic"]["webUrl"]
    env = {x["entity_key"]: x["payload"] for x in hk.consultar("notificacao", "envio")}
    assert env["canal:ordens:sla:7"]["situacao"] == "falha" and "Nenhum canal" in env["canal:ordens:sla:7"]["erro"] and env["email:p1:x"]["situacao"] == "enviado"
    # escolher o canal; pacote do app do Teams; estado publicado para o hub
    can = c.get("/api/teams/canais", headers=ADM).json()
    assert can["canais"][0]["nome"] == "Manutenção › Alertas"
    c.put("/api/config", json={"canais": {"ordens": {"team_id": "T1", "channel_id": "C1", "nome": "Manutenção › Alertas"}}, "endereco_publico": "https://hub.exemplo.org"}, headers=ADM)
    hk.publicar("notificacao", "envio", "canal:ordens:sla:8", {"canal": "canal", "situacao": "pendente", "ferramenta": "ordens", "titulo": "SLA estourado 8"}, source_application="strathub")
    c.post("/api/sync/agora", headers=ADM)
    assert ms.enviados[-1][0] == "canal" and "SLA estourado 8" in ms.enviados[-1][1]["body"]["content"]
    z = c.get("/api/teams/pacote", headers=ADM)
    import io, zipfile
    man = json.loads(zipfile.ZipFile(io.BytesIO(z.content)).read("manifest.json"))
    assert man["webApplicationInfo"]["id"] == APP_ID and man["activities"]["activityTypes"][0]["type"] == "pendencia" and man["validDomains"] == ["hub.exemplo.org"]
    est = hk.consultar("integracao", "microsoft", entity_key="estado")[0]["payload"]
    assert est["email"] and est["teams_canal"] and not est["teams_feed"]  # feed só depois de marcar o app como publicado
    c.post("/api/teams/instalado", json={"instalado": True}, headers=ADM); c.post("/api/sync/agora", headers=ADM)
    assert hk.consultar("integracao", "microsoft", entity_key="estado")[0]["payload"]["teams_feed"]


def test_permissao_opcional_sem_consentimento_nao_derruba_o_resto(amb):
    c, ms, hk = amb, amb.ms, amb.mod.hubkit
    ms.sem_permissao = {"Mail.Send"}
    conectar(c)
    cfg = c.get("/api/config", headers=ADM).json()
    assert "email" not in cfg["permissoes_liberadas"] and "teams_feed" in cfg["permissoes_liberadas"]
    c.post("/api/sync/agora", headers=ADM)
    est = hk.consultar("integracao", "microsoft", entity_key="estado")[0]["payload"]
    assert not est["email"] and est["aprovacoes_teams"]
