"""Testes do servidor central do StratAgenda:  python -m pytest -q"""
import importlib, io, json, os, sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
A = {"X-Usuario": "u-demo-1"}; Bh = {"X-Usuario": "u-demo-2"}


@pytest.fixture()
def client(tmp_path):
    os.environ["STRATAGENDA_DADOS"] = str(tmp_path)
    os.environ.pop("HUB_URL", None); os.environ.pop("HUB_APP_TOKEN", None)
    import hubkit, appkit, app as modulo
    importlib.reload(hubkit); importlib.reload(appkit); importlib.reload(modulo)
    with TestClient(modulo.app) as c:
        c.mod = modulo
        yield c


def test_sessao_tarefas_blocos(client):
    s = client.get("/api/session", headers=A).json()
    assert s["user"]["id"] == "u-demo-1" and s["user"]["onboarded"] is False and s["modo"] == "servidor"
    assert len(client.get("/api/profile", headers=A).json()["workHours"]) == 5
    assert client.post("/api/register", json={}).status_code == 410
    assert client.post("/api/tasks", json={"title": " "}, headers=A).status_code == 400
    t = client.post("/api/tasks", json={"title": "Relatório", "estimateH": 2, "priority": "alta", "tags": ["x"]}, headers=A).json()
    assert t["priority"] == "ALTA" and t["status"] == "BACKLOG" and t["blocks"] == []
    b = client.post("/api/blocks", json={"taskId": t["id"], "date": "2026-11-03", "slotStart": 4, "slotEnd": 8}, headers=A)
    assert b.status_code == 201
    assert client.post("/api/blocks", json={"taskId": t["id"], "date": "2026-11-03", "slotStart": 4, "slotEnd": 8}, headers=Bh).status_code == 403
    ts = client.get("/api/tasks?weekStart=2026-11-02", headers=A).json()
    assert ts[0]["status"] == "AGENDADA" and len(ts[0]["blocks"]) == 1 and ts[0]["creator"]["name"] == "Pessoa Demo 1"
    assert client.get("/api/tasks?weekStart=2026-11-09", headers=A).json()[0]["blocks"] == []
    client.delete(f"/api/blocks/{b.json()['id']}", headers=A)
    assert client.get("/api/tasks", headers=A).json()[0]["status"] == "BACKLOG"
    assert client.patch(f"/api/tasks/{t['id']}", json={"status": "CONCLUIDA"}, headers=Bh).status_code == 403
    assert client.patch(f"/api/tasks/{t['id']}", json={"status": "CONCLUIDA"}, headers=A).json()["status"] == "CONCLUIDA"
    assert client.patch("/api/profile", json={"type": "password"}, headers=A).status_code == 400
    assert client.post("/api/onboarding", json={"complete": True, "jobTitle": "Gerente"}, headers=A).json()["onboarded"] is True


def test_time_convites_e_sugestoes(client):
    client.get("/api/session", headers=Bh)
    emailB = client.get("/api/profile", headers=Bh).json()["email"]
    r = client.post("/api/invite", json={"email": emailB, "type": "MANAGER_TO_MEMBER"}, headers=A)
    assert r.status_code == 201
    assert client.post("/api/invite", json={"email": emailB, "type": "MANAGER_TO_MEMBER"}, headers=A).status_code == 409
    inv = client.get("/api/invite", headers=Bh).json()[0]
    assert inv["sender"]["name"] == "Pessoa Demo 1"
    assert client.patch(f"/api/invite/{inv['token']}", json={"action": "accept"}, headers=Bh).json()["status"] == "ACCEPTED"
    assert client.get("/api/profile", headers=Bh).json()["manager"]["id"] == "u-demo-1"
    t = client.post("/api/tasks", json={"title": "Tarefa do membro", "estimateH": 1}, headers=Bh).json()
    assert [m["id"] for m in client.get("/api/team/members", headers=A).json()] == ["u-demo-2"]
    s = client.post("/api/suggestions", json={"taskId": t["id"], "type": "SCHEDULE", "targetDate": "2026-11-03", "targetStart": 6}, headers=A)
    assert s.status_code == 201 and client.post("/api/suggestions", json={"taskId": t["id"], "type": "SCHEDULE"}, headers=Bh).status_code == 403
    n = client.get("/api/notifications", headers=Bh).json()
    assert n["unread"] == 2 and {x["type"] for x in n["notifications"]} == {"INVITE", "SUGGESTION"}
    sid = client.get("/api/suggestions", headers=Bh).json()[0]["id"]
    assert client.patch(f"/api/suggestions/{sid}", json={"action": "accept"}, headers=Bh).json()["status"] == "ACCEPTED"
    bl = client.get("/api/tasks", headers=Bh).json()[0]["blocks"][0]
    assert bl["date"] == "2026-11-03" and bl["slotStart"] == 6 and bl["slotEnd"] == 8
    client.patch("/api/notifications", headers=Bh)
    assert client.get("/api/notifications", headers=Bh).json()["unread"] == 0


def test_importacao_unica_onedrive_e_backup(client):
    ts = 1_700_000_000_000
    st = lambda: [ts, "uX", "dev1"]
    backup = {"app": "StratAgenda", "kind": "backup", "schema": 1, "tables": {
        "users": [{"id": "uX", "name": "Pessoa Importada", "email": "u-demo-2@demo.local", "role": "MEMBER", "onboarded": True, "_f": {"name": st(), "email": st()}, "_deleted": 0, "_createdBy": "uX"},
                  {"id": "demo_ana", "name": "Demo", "email": "ana@x", "_local": 1, "_f": {}, "_deleted": 0}],
        "tasks": [{"id": "tk1", "title": "Antiga", "priority": "MEDIA", "estimateH": 1, "status": "BACKLOG", "tags": [], "creatorId": "uX", "assigneeId": "uX", "_f": {"title": st()}, "_deleted": 0},
                  {"id": "tk2", "title": "Excluída", "assigneeId": "uX", "_f": {"_deleted": st()}, "_deleted": 1}]}}
    onedrive = {"app": "StratAgenda", "kind": "usuario", "schema": 1, "authorId": "uX", "records": {
        "tasks": [{"id": "tk1", "cb": "uX", "f": {"title": ["Antiga (renomeada no celular)", ts + 5, "dev2"]}}],
        "blocks": [{"id": "bk1", "cb": "uX", "f": {"taskId": ["tk1", ts, "dev2"], "userId": ["uX", ts, "dev2"], "date": ["2026-11-04", ts, "dev2"], "slotStart": [2, ts, "dev2"], "slotEnd": [4, ts, "dev2"]}}]}}
    arqs = [("arquivos", ("b.json", io.BytesIO(json.dumps(backup).encode()))), ("arquivos", ("usuario_uX.json", io.BytesIO(json.dumps(onedrive).encode()))),
            ("arquivos", ("x.json", io.BytesIO(b"nao e json")))]
    assert client.post("/api/importar", files=arqs, headers=Bh).status_code == 403
    arqs = [("arquivos", ("b.json", io.BytesIO(json.dumps(backup).encode()))), ("arquivos", ("usuario_uX.json", io.BytesIO(json.dumps(onedrive).encode()))),
            ("arquivos", ("x.json", io.BytesIO(b"nao e json")))]
    r = client.post("/api/importar", files=arqs, headers=A).json()
    assert r["registros"] == 3 and r["excluidos_ignorados"] == 1 and len(r["avisos"]) == 1
    # a pessoa entra pelo hub com o mesmo e-mail e assume o perfil importado
    s = client.get("/api/session", headers=Bh).json()
    assert s["user"]["id"] == "uX" and s["user"]["name"] == "Pessoa Importada"
    t = client.get("/api/tasks", headers=Bh).json()
    assert len(t) == 1 and t[0]["title"] == "Antiga (renomeada no celular)" and t[0]["blocks"][0]["date"] == "2026-11-04"
    # backup do servidor no formato do app (restaurável no app local / enviado ao OneDrive)
    bk = client.get("/api/backup", headers=A)
    doc = bk.json()
    assert doc["kind"] == "backup" and doc["app"] == "StratAgenda" and any(x["id"] == "tk1" and x["_f"]["title"] for x in doc["tables"]["tasks"])
    assert client.get("/api/backup", headers=Bh).status_code == 403
    assert client.get("/api/importacoes", headers=A).json()["historico"][0]["registros"] == 3


def _time(client):
    """A (u-demo-1) gestor de B (u-demo-2) e de C (u-demo-3)."""
    for h in (A, Bh):
        client.get("/api/session", headers=h)
    with client.mod.B.con() as c:
        c.execute("INSERT OR IGNORE INTO usuarios(id,nome,perfil,origem) VALUES ('u-demo-3','Pessoa Demo 3','membro','demo')")
    client.get("/api/session", headers=C)
    for h in (Bh, C):
        email = client.get("/api/profile", headers=h).json()["email"]
        client.post("/api/invite", json={"email": email, "type": "MANAGER_TO_MEMBER"}, headers=A)
        inv = client.get("/api/invite", headers=h).json()[0]
        client.patch(f"/api/invite/{inv['token']}", json={"action": "accept"}, headers=h)


C = {"X-Usuario": "u-demo-3"}


def test_edicao_e_sugestoes_sem_microgerenciamento(client):
    _time(client)
    t = client.post("/api/tasks", json={"title": "Relatório", "estimateH": 2}, headers=Bh).json()
    r = client.patch(f"/api/tasks/{t['id']}", json={"title": "Relatório mensal", "priority": "alta", "estimateH": 3.2, "description": "versão final"}, headers=Bh).json()
    assert r["title"] == "Relatório mensal" and r["priority"] == "ALTA" and r["estimateH"] == 3 and r["description"] == "versão final"
    assert client.patch(f"/api/tasks/{t['id']}", json={"estimateH": 1}, headers=A).status_code == 403   # gestor não altera
    # nova duração (sugestão) → o dono aceita
    s = client.post("/api/suggestions", json={"taskId": t["id"], "type": "ESTIMATE", "targetEstimate": 1.5}, headers=A)
    assert s.status_code == 201
    sid = client.get("/api/suggestions", headers=Bh).json()[0]["id"]
    client.patch(f"/api/suggestions/{sid}", json={"action": "accept"}, headers=Bh)
    assert client.get("/api/tasks", headers=Bh).json()[0]["estimateH"] == 1.5
    # substituição: aceite do dono e de quem recebe
    assert client.post("/api/suggestions", json={"taskId": t["id"], "type": "REASSIGN", "targetUserId": "u-inexistente"}, headers=A).status_code == 400
    client.post("/api/blocks", json={"taskId": t["id"], "date": "2026-11-04", "slotStart": 2, "slotEnd": 5}, headers=Bh)
    client.post("/api/suggestions", json={"taskId": t["id"], "type": "REASSIGN", "targetUserId": "u-demo-3"}, headers=A)
    assert client.get("/api/suggestions", headers=C).json() == []
    sid = client.get("/api/suggestions", headers=Bh).json()[0]["id"]
    assert client.patch(f"/api/suggestions/{sid}", json={"action": "accept"}, headers=Bh).json()["status"] == "AWAITING_RECEIVER"
    rec = client.get("/api/suggestions", headers=C).json()
    assert rec[0]["papel"] == "recebedor" and rec[0]["fromName"] == "Pessoa Demo 2"
    assert client.get("/api/notifications", headers=C).json()["notifications"][0]["type"] == "RECEIVE"
    assert client.patch(f"/api/suggestions/{sid}", json={"action": "accept"}, headers=C).json()["status"] == "ACCEPTED"
    tc = client.get("/api/tasks", headers=C).json()
    assert tc[0]["id"] == t["id"] and tc[0]["status"] == "BACKLOG" and tc[0]["blocks"] == [] and client.get("/api/tasks", headers=Bh).json() == []
    # recusa não expõe detalhes ao gestor
    client.post("/api/suggestions", json={"taskId": t["id"], "type": "ESTIMATE", "targetEstimate": 4}, headers=A)
    sid = client.get("/api/suggestions", headers=C).json()[0]["id"]
    client.patch(f"/api/suggestions/{sid}", json={"action": "decline", "comment": "estou sem tempo por motivo pessoal"}, headers=C)
    txt = json.dumps(client.get("/api/notifications", headers=A).json(), ensure_ascii=False)
    assert "não foi aceita" in txt and "motivo pessoal" not in txt


def test_feriado_ciencia_e_de_acordo_do_gestor(client):
    _time(client)
    t = client.post("/api/tasks", json={"title": "Inventário", "estimateH": 2}, headers=Bh).json()
    r = client.post("/api/blocks", json={"taskId": t["id"], "date": "2026-11-20", "slotStart": 2, "slotEnd": 6}, headers=Bh)
    assert r.status_code == 409 and r.json()["code"] == "FERIADO" and r.json()["precisaGestor"] and "Consciência" in r.json()["feriado"]["nome"]
    b = client.post("/api/blocks", json={"taskId": t["id"], "date": "2026-11-20", "slotStart": 2, "slotEnd": 6, "ciencia_feriado": True}, headers=Bh).json()
    assert b["pendenteGestor"] is True and b["feriado"]
    n = client.get("/api/notifications", headers=A).json()["notifications"][0]
    assert n["type"] == "FERIADO_ACORDO" and json.loads(n["data"])["blockId"] == b["id"]
    assert client.post(f"/api/feriado-acordo/{b['id']}", json={"decisao": "de_acordo"}, headers=C).status_code == 403
    assert client.post(f"/api/feriado-acordo/{b['id']}", json={"decisao": "de_acordo"}, headers=A).json()["success"]
    bl = client.get("/api/tasks?weekStart=2026-11-16", headers=Bh).json()[0]["blocks"][0]
    assert bl["pendenteGestor"] is False and bl["acordoPor"] == "Pessoa Demo 1"
    # sem gestor: só a ciência
    t2 = client.post("/api/tasks", json={"title": "Plantão"}, headers=A).json()
    b2 = client.post("/api/blocks", json={"taskId": t2["id"], "date": "2026-12-25", "slotStart": 2, "slotEnd": 4, "ciencia_feriado": True}, headers=A).json()
    assert b2["pendenteGestor"] is False and b2["feriado"] == "Natal"
    # gestor sugere em feriado: precisa da ciência dos dois lados
    t3 = client.post("/api/tasks", json={"title": "Auditoria"}, headers=Bh).json()
    assert client.post("/api/suggestions", json={"taskId": t3["id"], "type": "SCHEDULE", "targetDate": "2026-11-20", "targetStart": 8}, headers=A).status_code == 409
    client.post("/api/suggestions", json={"taskId": t3["id"], "type": "SCHEDULE", "targetDate": "2026-11-20", "targetStart": 8, "ciencia_feriado": True}, headers=A)
    sid = client.get("/api/suggestions", headers=Bh).json()[0]["id"]
    assert client.patch(f"/api/suggestions/{sid}", json={"action": "accept"}, headers=Bh).status_code == 409
    assert client.patch(f"/api/suggestions/{sid}", json={"action": "accept", "ciencia_feriado": True}, headers=Bh).json()["status"] == "ACCEPTED"
    assert client.get("/api/feriados?inicio=2026-11-01&fim=2026-11-30", headers=Bh).json()["feriados"][0]["data"] == "2026-11-02"


def test_ecossistema_tarefas_acoes_e_os(client):
    hk = client.mod.hubkit
    _time(client)
    pub = lambda k, p: hk.publicar("agenda", "pendencia", k, {"origem_app": k.split(":")[0], "origem_nome": k.split(":")[0].upper(), **p}, source_application=k.split(":")[0])
    pub("pmp:rodada:x", {"status": "aberta", "natureza": "tarefa", "esforco_h": 2, "titulo": "Rodada: 4 projetos sem tendência", "responsavel": "Pessoa Demo 2", "link": "#/rodadas", "prioridade": "alta"})
    pub("pmp:mudanca:9", {"status": "aberta", "natureza": "acao", "titulo": "Mudança X", "destinatarios": [{"email": "u-demo-1@demo.local"}], "link": "#/m",
                          "acoes": [{"codigo": "aprovar", "rotulo": "Aprovar", "metodo": "POST", "caminho": "/api/mudancas/9/decidir", "corpo": {"status": "aprovada"}, "comentario": "opcional", "campo": "parecer"}]})
    pub("plano:revisar:1", {"status": "aberta", "natureza": "tarefa", "titulo": "Revisar pleitos", "destinatarios": [{"email": "u-demo-2@demo.local"}, {"email": "u-demo-3@demo.local"}]})
    pub("ordens:exec:OS-1:h-x", {"status": "aberta", "natureza": "operacional", "titulo": "OS-1 · Troca de disjuntor", "inicio": "2026-11-05T13:00:00Z", "fim": "2026-11-05T15:00:00Z",
                                 "destinatarios": [{"email": "u-demo-3@demo.local"}], "executor": True, "link": "campo.html#/os/OS-1"})
    # bloco administrativo de C que vai cruzar com a OS (10:00–12:00 local)
    adm = client.post("/api/tasks", json={"title": "Reunião interna"}, headers=C).json()
    client.post("/api/blocks", json={"taskId": adm["id"], "date": "2026-11-05", "slotStart": 7, "slotEnd": 9}, headers=C)
    st = client.post("/api/sync/agora", headers=A).json()
    assert st["ok"] and st["resultado"]["pendencias_entregues"] == 5
    # tarefa com responsável → backlog dele, com atalho para a ferramenta
    tb = client.get("/api/tasks", headers=Bh).json()
    rod = next(t for t in tb if t["pendencia"] == "pmp:rodada:x")
    assert rod["status"] == "BACKLOG" and rod["estimateH"] == 2 and rod["origemUrl"] == "/apps/pmp/#/rodadas" and rod["priority"] == "ALTA"
    assert client.patch(f"/api/tasks/{rod['id']}", json={"title": "outro"}, headers=Bh).status_code == 409
    assert client.patch(f"/api/tasks/{rod['id']}", json={"estimateH": 3, "description": "ver com o time"}, headers=Bh).json()["estimateH"] == 3
    # concluir na agenda com a origem ainda aberta → "pendente na origem"
    client.patch(f"/api/tasks/{rod['id']}", json={"status": "CONCLUIDA"}, headers=Bh)
    assert next(t for t in client.get("/api/tasks", headers=Bh).json() if t["id"] == rod["id"])["pendenteNaOrigem"] is True
    # ação → notificação com botões; modal lê a pendência
    na = client.get("/api/notifications", headers=A).json()["notifications"]
    assert any(n["type"] == "ACAO" for n in na)
    p = client.get("/api/ecossistema/pendencia?chave=pmp:mudanca:9", headers=A).json()
    assert p["acoes"][0]["campo"] == "parecer" and p["url"] == "/apps/pmp/#/m" and p["minha"]
    # tarefa para várias pessoas → disponível para assumir; quem assume leva
    assert any(n["type"] == "TAREFA_DISPONIVEL" for n in client.get("/api/notifications", headers=C).json()["notifications"])
    assert client.post("/api/ecossistema/pendencias/agendar", json={"chave": "plano:revisar:1"}, headers=C).status_code == 201
    r = client.post("/api/ecossistema/pendencias/agendar", json={"chave": "plano:revisar:1"}, headers=Bh)
    assert r.status_code == 409 and "Pessoa Demo 3" in r.json()["error"]
    itens = {i["chave"]: i for i in client.get("/api/ecossistema/pendencias", headers=Bh).json()["itens"]}
    assert itens["plano:revisar:1"]["assumida_por"] == "Pessoa Demo 3"
    # OS → bloco travado na agenda do técnico, com conflito marcado no bloco administrativo
    tc = {t["title"]: t for t in client.get("/api/tasks?weekStart=2026-11-02", headers=C).json()}
    os1 = tc["OS-1 · Troca de disjuntor"]
    assert os1["travado"] and os1["blocks"][0]["slotStart"] == 6 and os1["blocks"][0]["slotEnd"] == 10 and os1["blocks"][0]["date"] == "2026-11-05"
    assert tc["Reunião interna"]["blocks"][0]["conflitoOS"] == "OS-1"
    assert any(n["type"] == "CONFLITO_OS" for n in client.get("/api/notifications", headers=C).json()["notifications"])
    bid = os1["blocks"][0]["id"]
    assert client.patch(f"/api/blocks/{bid}", json={"date": "2026-11-06"}, headers=C).status_code == 403
    assert client.delete(f"/api/blocks/{bid}", headers=C).status_code == 403
    assert client.delete(f"/api/tasks/{os1['id']}", headers=C).status_code == 403
    # replanejamento move o bloco e avisa; resolução na origem fecha tudo
    pub("ordens:exec:OS-1:h-x", {"status": "aberta", "natureza": "operacional", "titulo": "OS-1 · Troca de disjuntor", "inicio": "2026-11-06T11:00:00Z", "fim": "2026-11-06T12:00:00Z",
                                 "destinatarios": [{"email": "u-demo-3@demo.local"}]})
    pub("pmp:rodada:x", {"status": "resolvida"}); pub("pmp:mudanca:9", {"status": "resolvida"})
    client.post("/api/sync/agora", headers=A)
    b = next(t for t in client.get("/api/tasks?weekStart=2026-11-02", headers=C).json() if t["id"] == os1["id"])["blocks"][0]
    assert (b["date"], b["slotStart"], b["slotEnd"]) == ("2026-11-06", 2, 4)
    assert "OS replanejada" in [n["title"] for n in client.get("/api/notifications", headers=C).json()["notifications"]]
    r2 = next(t for t in client.get("/api/tasks", headers=Bh).json() if t["id"] == rod["id"])
    assert r2["status"] == "CONCLUIDA" and r2["pendenteNaOrigem"] is False
    n = next(n for n in client.get("/api/notifications", headers=A).json()["notifications"] if n["type"] == "ACAO")
    assert json.loads(n["data"])["resolvida"] is True


def test_reuniao_de_projeto(client):
    hk = client.mod.hubkit
    client.get("/api/session", headers=A)
    # reunião de projeto → rascunho de ata na PMP, atualizado ao remarcar
    assert client.post("/api/reunioes", json={"titulo": "Status"}, headers=A).status_code == 400
    r = client.post("/api/reunioes", json={"titulo": "Status do projeto", "codigo_projeto": "PRJ-7", "data": "2026-11-06", "hora": "10:30", "duracao_h": 1,
                                           "participantes": ["Bruno"], "pauta": "Cronograma"}, headers=A).json()
    ata = hk.consultar("pmp", "ata_rascunho", entity_key=f"agenda:{r['id']}")[0]["payload"]
    assert ata["data"] == "2026-11-06T10:30" and ata["codigo_projeto"] == "PRJ-7" and ata["participantes"] == ["Pessoa Demo 1", "Bruno"] and ata["pauta"] == "Cronograma"
    bid = client.get("/api/tasks", headers=A).json()
    bid = next(x for x in bid if x["id"] == r["id"])["blocks"][0]["id"]
    client.patch(f"/api/blocks/{bid}", json={"date": "2026-11-07", "slotStart": 4, "slotEnd": 6}, headers=A)
    assert hk.consultar("pmp", "ata_rascunho", entity_key=f"agenda:{r['id']}")[0]["payload"]["data"] == "2026-11-07T09:00"
    client.delete(f"/api/tasks/{r['id']}", headers=A)
    assert hk.consultar("pmp", "ata_rascunho", entity_key=f"agenda:{r['id']}")[0]["payload"]["cancelada"] is True


def test_calendario_externo_e_compromissos(client):
    from datetime import date, timedelta
    hk = client.mod.hubkit
    D = (date.today() + timedelta(days=2)).isoformat(); SEG = (date.today() + timedelta(days=2 - (date.today() + timedelta(days=2)).weekday())).isoformat()
    client.get("/api/session", headers=A)
    t = client.post("/api/tasks", json={"title": "Preparar comitê", "estimateH": 2}, headers=A).json()
    b = client.post("/api/blocks", json={"taskId": t["id"], "date": D, "slotStart": 6, "slotEnd": 10, "ciencia_feriado": True}, headers=A).json()
    # Outlook → agenda (ocupado, travado)
    hk.publicar("agenda", "ocupado", "microsoft:x:ev1", {"status": "ativo", "pessoa": {"email": "u-demo-1@demo.local"}, "titulo": "Reunião com cliente",
                "inicio": D + "T17:00:00Z", "fim": D + "T18:30:00Z", "origem_nome": "Outlook"}, source_application="conector_microsoft")
    st = client.post("/api/sync/agora", headers=A).json()["resultado"]
    assert st["compromissos_externos"] == 1 and st["compromissos_publicados"] == 1
    ts = {x["title"]: x for x in client.get("/api/tasks?weekStart=" + SEG, headers=A).json()}
    ext = ts["Reunião com cliente"]
    assert ext["travado"] and ext["blocks"][0]["externo"] == "Outlook" and (ext["blocks"][0]["slotStart"], ext["blocks"][0]["slotEnd"]) == (14, 17)
    assert client.patch(f"/api/blocks/{ext['blocks'][0]['id']}", json={"slotStart": 2}, headers=A).status_code == 403
    assert ts["Preparar comitê"]["blocks"][0]["conflitoOS"] is None  # compromisso externo não é OS
    # agenda → outros calendários: só os blocos do hub (o externo não volta)
    comp = {r["entity_key"]: r["payload"] for r in hk.consultar("agenda", "compromisso")}
    assert list(comp) == [f"stratagenda:{b['id']}"]
    p = comp[f"stratagenda:{b['id']}"]
    assert p["titulo"] == "Preparar comitê" and p["inicio"] == D + "T10:00:00-03:00" and p["fim"] == D + "T12:00:00-03:00" and p["pessoa"]["email"] == "u-demo-1@demo.local"
    # remarcar e remover
    client.patch(f"/api/blocks/{b['id']}", json={"slotStart": 8, "slotEnd": 12}, headers=A)
    hk.publicar("agenda", "ocupado", "microsoft:x:ev1", {"status": "removido", "pessoa": {"email": "u-demo-1@demo.local"}}, source_application="conector_microsoft")
    client.post("/api/sync/agora", headers=A)
    assert hk.consultar("agenda", "compromisso", entity_key=f"stratagenda:{b['id']}")[0]["payload"]["inicio"] == D + "T11:00:00-03:00"
    assert "Reunião com cliente" not in {x["title"] for x in client.get("/api/tasks", headers=A).json()}
    client.delete(f"/api/blocks/{b['id']}", headers=A)
    client.post("/api/sync/agora", headers=A)
    assert hk.consultar("agenda", "compromisso", entity_key=f"stratagenda:{b['id']}")[0]["payload"]["status"] == "removido"


from datetime import datetime as _dt, timedelta as _td
D = {"X-Usuario": "u-demo-4"}


def _dia(n: int) -> str:
    return (_dt.now() + _td(days=n)).strftime("%Y-%m-%d")


def _cadeia(client):
    """A lidera B e C; B lidera D (convites no modo isolado; no hub a hierarquia vem do líder direto do cadastro)."""
    _time(client)
    with client.mod.B.con() as c:
        c.execute("INSERT OR IGNORE INTO usuarios(id,nome,perfil,origem) VALUES ('u-demo-4','Pessoa Demo 4','membro','demo')")
    client.get("/api/session", headers=D)
    email = client.get("/api/profile", headers=D).json()["email"]
    client.post("/api/invite", json={"email": email, "type": "MANAGER_TO_MEMBER"}, headers=Bh)
    inv = client.get("/api/invite", headers=D).json()[0]
    client.patch(f"/api/invite/{inv['token']}", json={"action": "accept"}, headers=D)


def test_sobreposicao_bloqueada(client):
    t1 = client.post("/api/tasks", json={"title": "Relatório"}, headers=A).json()
    t2 = client.post("/api/tasks", json={"title": "Planilha"}, headers=A).json()
    d = "2026-11-10"
    b1 = client.post("/api/blocks", json={"taskId": t1["id"], "date": d, "slotStart": 4, "slotEnd": 8, "ciencia_feriado": True}, headers=A).json()
    r = client.post("/api/blocks", json={"taskId": t2["id"], "date": d, "slotStart": 6, "slotEnd": 10, "ciencia_feriado": True}, headers=A)
    assert r.status_code == 409 and r.json()["code"] == "OCUPADO" and "Relatório" in r.json()["error"]
    b2 = client.post("/api/blocks", json={"taskId": t2["id"], "date": d, "slotStart": 8, "slotEnd": 10, "ciencia_feriado": True}, headers=A).json()
    assert client.patch(f"/api/blocks/{b2['id']}", json={"slotStart": 7, "slotEnd": 9}, headers=A).status_code == 409
    assert client.patch(f"/api/blocks/{b1['id']}", json={"slotStart": 3, "slotEnd": 7}, headers=A).status_code == 200  # mexer no próprio bloco não conflita com ele mesmo


def test_hierarquia_atribuir_repassar_recolher_impedimento(client):
    _cadeia(client)
    r = client.post("/api/team/tasks", json={"assigneeId": "u-demo-4", "title": "Pular nível"}, headers=A)
    assert r.status_code == 403  # só um nível por vez
    t = client.post("/api/team/tasks", json={"assigneeId": "u-demo-2", "title": "Levantamento", "estimateH": 2, "priority": "alta", "prazoMax": _dia(10),
                                             "sugestao": {"date": "2026-11-11", "slotStart": 4, "ciencia_feriado": True}}, headers=A)
    assert t.status_code == 201
    t = t.json()
    assert t["cadeia"] == ["u-demo-1", "u-demo-2"] and t["sugestao"]["type"] == "SCHEDULE"
    tipos = {n["type"] for n in client.get("/api/notifications", headers=Bh).json()["notifications"]}
    assert {"TAREFA_ATRIBUIDA", "SUGGESTION"} <= tipos
    assert client.delete(f"/api/tasks/{t['id']}", headers=Bh).status_code == 403             # não recusa nem exclui
    assert client.patch(f"/api/tasks/{t['id']}", json={"title": "Outro"}, headers=Bh).status_code == 403
    assert client.patch(f"/api/tasks/{t['id']}", json={"estimateH": 3}, headers=Bh).status_code == 200  # quanto tempo é dela
    ts = {x["id"]: x for x in client.get("/api/tasks", headers=Bh).json()}
    assert ts[t["id"]]["doLider"] and ts[t["id"]]["atribuidaPorNome"] == "Pessoa Demo 1"
    assert client.post(f"/api/tasks/{t['id']}/repassar", json={"userId": "u-demo-3"}, headers=Bh).status_code == 403  # C não responde a B
    rp = client.post(f"/api/tasks/{t['id']}/repassar", json={"userId": "u-demo-4"}, headers=Bh).json()
    assert rp["assigneeId"] == "u-demo-4" and rp["cadeia"] == ["u-demo-1", "u-demo-2", "u-demo-4"] and rp["atribuidaPor"] == "u-demo-2"
    assert client.post(f"/api/tasks/{t['id']}/impedimento", json={"motivo": ""}, headers=D).status_code == 400
    client.post(f"/api/tasks/{t['id']}/impedimento", json={"motivo": "Sem acesso ao sistema X"}, headers=D)
    assert any(n["type"] == "IMPEDIMENTO" and "sistema X" in n["body"] for n in client.get("/api/notifications", headers=Bh).json()["notifications"])
    assert client.post(f"/api/tasks/{t['id']}/impedimento/resolver", json={"acao": "manter"}, headers=A).status_code == 403  # quem decide é B
    assert client.post(f"/api/tasks/{t['id']}/impedimento/resolver", json={"acao": "prazo", "prazoMax": _dia(20)}, headers=Bh).json()["impedimento"] is None
    rc = client.post(f"/api/tasks/{t['id']}/recolher", headers=A).json()   # quem está acima na cadeia recolhe
    assert rc["assigneeId"] == "u-demo-1" and rc["cadeia"] == ["u-demo-1"]
    assert any(n["type"] == "TAREFA_RECOLHIDA" for n in client.get("/api/notifications", headers=D).json()["notifications"])
    assert client.post(f"/api/tasks/{t['id']}/recolher", headers=D).status_code == 403
    # cancelar antes de começar
    t2 = client.post("/api/team/tasks", json={"assigneeId": "u-demo-3", "title": "Cancelável"}, headers=A).json()
    assert client.delete(f"/api/team/tasks/{t2['id']}", headers=C).status_code == 403
    assert client.delete(f"/api/team/tasks/{t2['id']}", headers=A).json()["success"]


def test_fechamento_diario_e_prazo_critico(client):
    _time(client)
    ontem = _dia(-1)
    t = client.post("/api/team/tasks", json={"assigneeId": "u-demo-2", "title": "Relatório mensal", "estimateH": 3, "prazoMax": ontem}, headers=A).json()
    assert any(n["type"] == "PRAZO_CRITICO" for n in client.get("/api/notifications", headers=A).json()["notifications"])   # prazo já passou
    assert client.get("/api/tasks", headers=Bh).json()[0]["critica"] is True
    b = client.post("/api/blocks", json={"taskId": t["id"], "date": ontem, "slotStart": 2, "slotEnd": 6, "ciencia_feriado": True}, headers=Bh).json()
    p = client.post("/api/tasks", json={"title": "Minha tarefa", "estimateH": 1}, headers=Bh).json()
    b2 = client.post("/api/blocks", json={"taskId": p["id"], "date": ontem, "slotStart": 8, "slotEnd": 10, "ciencia_feriado": True}, headers=Bh).json()
    itens = client.get("/api/fechamento", headers=Bh).json()["itens"]
    assert [i["blockId"] for i in itens] == [b["id"], b2["id"]] and itens[0]["doLider"]
    r = client.post("/api/fechamento", json={"itens": [{"blockId": b["id"], "acao": "nao_executar"}]}, headers=Bh).json()
    assert not r["resultados"][0]["ok"] and "impedimento" in r["resultados"][0]["erro"]            # do líder: não recusa
    r = client.post("/api/fechamento", json={"itens": [{"blockId": b["id"], "acao": "parcial", "restanteH": 1.5},
                                                        {"blockId": b2["id"], "acao": "reagendar"}]}, headers=Bh).json()
    assert all(x["ok"] for x in r["resultados"]) and r["restantes"] == 0
    ts = {x["id"]: x for x in client.get("/api/tasks", headers=Bh).json()}
    assert ts[t["id"]]["estimateH"] == 1.5 and ts[t["id"]]["status"] == "BACKLOG" and ts[p["id"]]["reagendamentos"] == 1 and ts[p["id"]]["status"] == "BACKLOG"
    b3 = client.post("/api/blocks", json={"taskId": t["id"], "date": _dia(-2), "slotStart": 12, "slotEnd": 14, "ciencia_feriado": True}, headers=Bh).json()
    client.post("/api/fechamento", json={"itens": [{"blockId": b3["id"], "acao": "concluiu"}]}, headers=Bh)
    assert {x["id"]: x for x in client.get("/api/tasks", headers=Bh).json()}[t["id"]]["status"] == "CONCLUIDA"
    assert any(n["type"] == "TAREFA_CONCLUIDA" for n in client.get("/api/notifications", headers=A).json()["notifications"])


def test_gantt_da_equipe(client):
    _cadeia(client)
    seg = "2026-11-09"
    tb = client.post("/api/tasks", json={"title": "Tarefa B"}, headers=Bh).json()
    client.post("/api/blocks", json={"taskId": tb["id"], "date": seg, "slotStart": 2, "slotEnd": 10}, headers=Bh)
    td = client.post("/api/tasks", json={"title": "Tarefa D"}, headers=D).json()
    client.post("/api/blocks", json={"taskId": td["id"], "date": seg, "slotStart": 2, "slotEnd": 4}, headers=D)
    g = client.get(f"/api/team/gantt?inicio={seg}&dias=5", headers=A).json()
    linhas = {p["id"]: p for p in g["pessoas"]}
    assert set(linhas) == {"u-demo-2", "u-demo-3"} and linhas["u-demo-2"]["carga"][seg] == 4.0 and linhas["u-demo-2"]["temEquipe"] and g["podeSugerir"]
    assert linhas["u-demo-2"]["jornada"]["1"] == 8.0 and linhas["u-demo-2"]["blocos"][0]["title"] == "Tarefa B"
    g2 = client.get(f"/api/team/gantt?inicio={seg}&de=u-demo-2", headers=A).json()   # descer um nível
    assert [p["id"] for p in g2["pessoas"]] == ["u-demo-4"] and not g2["podeSugerir"] and [x["id"] for x in g2["caminho"]] == ["u-demo-1", "u-demo-2"]
    assert client.get(f"/api/team/gantt?inicio={seg}&de=u-demo-1", headers=Bh).status_code == 403  # não sobe na hierarquia
