"""Testes da API de Ordens de Serviço:  python -m pytest -q"""
import importlib, io, json, os, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
GES = {"X-Usuario": "u-demo-gestor"}; SUP = {"X-Usuario": "u-demo-sup"}; PLA = {"X-Usuario": "u-demo-plan"}
T1 = {"X-Usuario": "u-demo-tec1"}; T2 = {"X-Usuario": "u-demo-tec2"}; SOL = {"X-Usuario": "u-demo-sol"}


def _abrir(tmp_path):
    os.environ["ORDENS_DADOS"] = str(tmp_path)
    os.environ.pop("HUB_URL", None); os.environ.pop("HUB_APP_TOKEN", None)
    import hubkit, appkit, app as modulo
    importlib.reload(hubkit); importlib.reload(appkit); importlib.reload(modulo)
    return modulo


@pytest.fixture()
def client(tmp_path):
    modulo = _abrir(tmp_path)
    # datas relativas a "agora" podem cair em feriado real: o fluxo de feriado tem teste próprio com data fixa
    modulo.feriado_real, modulo.hubkit.feriado_em = modulo.hubkit.feriado_em, (lambda *a, **k: None)
    with TestClient(modulo.app) as c:
        c.mod = modulo
        yield c


def equipe(client, nome="Equipe A", ana_uid="u-demo-tec1", beto_uid="u-demo-tec2", escala=False):
    return client.put("/api/equipes/nova", json={"nome": nome, "membros": [
        {"nome": "Ana", "funcao": "Técnico", "custo_hora": 50, "usuario_id": ana_uid, "escala_feriado": escala},
        {"nome": "Beto", "funcao": "Auxiliar", "custo_hora": 30, "usuario_id": beto_uid, "escala_feriado": escala}]}, headers=PLA).json()


def quando(h=24):
    return (datetime.now(timezone.utc) + timedelta(hours=h)).replace(microsecond=0).isoformat()


def foto(client, n, etapa="depois", seq=None, h=T1):
    dados = {"etapa": etapa}
    if seq:
        dados["checklist_seq"] = str(seq)
    return client.post(f"/api/ordens/{n}/anexos", data=dados, files={"arquivo": ("f.jpg", io.BytesIO(b"\xff\xd8x"), "image/jpeg")}, headers=h)


def checklist_completo(client, n, h=T1):
    itens = client.get(f"/api/ordens/{n}", headers=h).json()["itens_checklist"]
    for i in itens:
        for _ in range(i["foto_min"]):
            foto(client, n, "item", i["seq"], h)
    r = client.put(f"/api/ordens/{n}/checklist", json={"itens": [{"seq": i["seq"], "feito": True, "resposta": "1" if i["resposta_tipo"] != "check" else None}
                                                                 for i in itens], "parcial": True}, headers=h)
    assert r.status_code == 200, r.text


def test_fluxo_executor_cronometro_aprovacao_e_custos(client):
    eq = equipe(client)
    client.put("/api/materiais/CAB-1", json={"descricao": "Cabo", "unidade_medida": "m", "custo_unitario": 2}, headers=PLA)
    assert client.post("/api/ordens", json={"titulo": "x"}, headers=T1).status_code == 403
    o = client.post("/api/ordens", json={"titulo": "Trocar tomada", "tipo": "corretiva", "prioridade": "alta", "unidade": "Unidade Norte"}, headers=SOL).json()
    n = o["numero"]
    assert o["status"] == "aberta" and o["sla"]["situacao"] == "no_prazo" and o["checklist"]["total"] == 5 and n.startswith("OS-")
    assert "custos" not in o  # solicitante não vê custos
    assert client.post(f"/api/ordens/{n}/acoes/iniciar", headers=T1).status_code == 409          # precisa planejar
    assert client.post(f"/api/ordens/{n}/planejar", json={"equipe_id": eq["id"]}, headers=PLA).status_code == 400
    p = client.post(f"/api/ordens/{n}/planejar", json={"equipe_id": eq["id"], "execucao_inicio": quando(), "duracao_h": 2}, headers=PLA).json()
    assert p["status"] == "planejada" and p["custos"]["mao_obra_prevista"] == 160 and p["conflitos"] == [] and p["executor"] == "Ana"
    assert [x["executor"] for x in p["participantes"]] == [1, 0]
    m = client.post(f"/api/ordens/{n}/materiais", json={"codigo": "CAB-1", "qtd_prevista": 10}, headers=PLA).json()
    assert client.post(f"/api/ordens/{n}/materiais", json={"descricao": "x", "qtd_prevista": 1}, headers=T1).status_code == 403
    # só o executor registra a execução; o auxiliar acompanha
    assert client.post(f"/api/ordens/{n}/acoes/iniciar", headers=T2).status_code == 403
    vb = client.get(f"/api/ordens/{n}", headers=T2).json()
    assert vb["eu"] == {"participante": True, "executor": False, "pode_executar": False, "concluiu": False, "master": False}
    r = client.post(f"/api/ordens/{n}/acoes/iniciar", headers=T1).json()
    assert r["status"] == "em_execucao" and r["cronometro_ativo"]
    # cronômetro: 1 h decorrida → pausar aponta 1 h para cada participante
    with client.mod.B.con() as c:
        c.execute("UPDATE ordens SET cronometro_inicio=? WHERE numero=?", ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), n))
    assert client.post(f"/api/ordens/{n}/acoes/pausar", json={}, headers=T1).status_code == 400
    assert client.post(f"/api/ordens/{n}/acoes/pausar", json={"motivo": "Falta de peça/material"}, headers=T1).json()["status"] == "pausada"
    aps = client.get(f"/api/ordens/{n}", headers=GES).json()["apontamentos"]
    assert sorted((a["nome"], round(a["horas"], 1)) for a in aps) == [("Ana", 1.0), ("Beto", 1.0)]
    client.post(f"/api/ordens/{n}/acoes/iniciar", headers=T1)
    r = client.post(f"/api/ordens/{n}/acoes/concluir", json={}, headers=T1)
    assert r.status_code == 409 and "Checklist" in r.json()["detail"]
    itens = client.get(f"/api/ordens/{n}", headers=T1).json()["itens_checklist"]
    ult = itens[-1]
    assert ult["foto_min"] == 1
    r = client.put(f"/api/ordens/{n}/checklist", json={"itens": [{"seq": ult["seq"], "feito": True}], "parcial": True}, headers=T1)
    assert r.status_code == 409 and "foto" in r.json()["detail"]
    checklist_completo(client, n)
    r = client.post(f"/api/ordens/{n}/acoes/concluir", json={}, headers=T1)
    assert r.status_code == 409 and "materiais" in r.json()["detail"]
    client.put(f"/api/ordens/{n}/materiais/{m['id']}", json={"status": "separado"}, headers=T1)
    client.put(f"/api/ordens/{n}/materiais/{m['id']}", json={"status": "consumido", "qtd_real": 8}, headers=T1)
    r = client.post(f"/api/ordens/{n}/acoes/concluir", json={}, headers=T1)
    assert r.status_code == 409 and "fotos do depois" in r.json()["detail"]
    assert foto(client, n, "depois").status_code == 200
    c1 = client.post(f"/api/ordens/{n}/acoes/concluir", json={"resumo": "Tomada trocada", "aceite_nome": "Maria"}, headers=T1).json()
    assert c1["status"] == "concluida" and c1["aprovacao"] == "pendente" and c1["aceite_nome"] == "Maria" and "custos" not in c1
    # supervisor devolve (motivo obrigatório) → executor retoma e conclui de novo → supervisor aprova
    assert client.post(f"/api/ordens/{n}/acoes/encerrar", headers=T1).status_code == 403
    assert client.post(f"/api/ordens/{n}/acoes/devolver", json={}, headers=SUP).status_code == 400
    d = client.post(f"/api/ordens/{n}/acoes/devolver", json={"motivo": "Falta foto do quadro"}, headers=SUP).json()
    assert d["status"] == "pausada" and d["aprovacao"] == "devolvida"
    client.post(f"/api/ordens/{n}/acoes/iniciar", headers=T1)
    client.post(f"/api/ordens/{n}/acoes/concluir", json={"resumo": "Foto incluída"}, headers=T1)
    e = client.post(f"/api/ordens/{n}/acoes/encerrar", headers=SUP).json()
    assert e["status"] == "encerrada" and e["aprovacao"] == "aprovada" and e["sla"]["situacao"] == "cumprido"
    custos = client.get(f"/api/ordens/{n}", headers=GES).json()["custos"]
    assert custos["material_previsto"] == 20.0 and custos["material_real"] == 16.0 and custos["mao_obra_prevista"] == 160.0
    assert custos["mao_obra_real"] == 80.0 and custos["horas_reais"] == 2.0
    ev = [x["acao"] for x in client.get(f"/api/ordens/{n}", headers=GES).json()["eventos"]]
    assert "OS encerrada (aprovada)" in ev and "Encerramento devolvido" in ev and "OS pausada" in ev
    # solicitante só vê as próprias
    assert len(client.get("/api/ordens", headers=SOL).json()) == 1
    client.post("/api/ordens", json={"titulo": "Outra", "tipo": "inspecao"}, headers=PLA)
    assert len(client.get("/api/ordens", headers=SOL).json()) == 1 and len(client.get("/api/ordens", headers=GES).json()) == 2


def test_regras_de_executor_e_funcoes(client):
    so_aux = client.put("/api/equipes/nova", json={"nome": "Só auxiliares", "membros": [{"nome": "Caio", "funcao": "Ajudante geral"},
                                                                                      {"nome": "Duda", "funcao": "Auxiliar"}]}, headers=PLA).json()
    assert [m["pode_executor"] for m in so_aux["membros"]] == [False, False]  # função nova com "ajud" nasce não executora
    n = client.post("/api/ordens", json={"titulo": "A", "tipo": "inspecao"}, headers=PLA).json()["numero"]
    r = client.post(f"/api/ordens/{n}/planejar", json={"equipe_id": so_aux["id"], "execucao_inicio": quando(), "duracao_h": 1}, headers=PLA)
    assert r.status_code == 400 and "executor" in r.json()["detail"]
    eq = equipe(client, "Equipe B")
    beto = next(m["id"] for m in eq["membros"] if m["nome"] == "Beto")
    ana = next(m["id"] for m in eq["membros"] if m["nome"] == "Ana")
    r = client.post(f"/api/ordens/{n}/planejar", json={"equipe_id": eq["id"], "execucao_inicio": quando(), "duracao_h": 1, "executor_id": beto}, headers=PLA)
    assert r.status_code == 400 and "Auxiliar" in r.json()["detail"]
    r = client.post(f"/api/ordens/{n}/planejar", json={"equipe_id": eq["id"], "execucao_inicio": quando(), "duracao_h": 1,
                                                      "participantes": [ana], "executor_id": ana}, headers=PLA).json()
    assert [p["nome"] for p in r["participantes"]] == ["Ana"] and r["custos"]["mao_obra_prevista"] == 50
    # funções configuráveis
    client.put("/api/funcoes", json={"funcoes": [{"nome": "Auxiliar", "pode_executor": True}]}, headers=GES)
    assert next(f for f in client.get("/api/funcoes", headers=PLA).json() if f["nome"] == "Auxiliar")["pode_executor"] == 1
    assert client.put("/api/funcoes", json={"funcoes": []}, headers=T1).status_code == 403


def test_custos_so_do_planejador_para_cima(client):
    eq = equipe(client)
    client.put("/api/materiais/CAB-1", json={"descricao": "Cabo", "custo_unitario": 2}, headers=PLA)
    n = client.post("/api/ordens", json={"titulo": "A", "tipo": "corretiva"}, headers=PLA).json()["numero"]
    client.post(f"/api/ordens/{n}/planejar", json={"equipe_id": eq["id"], "execucao_inicio": quando(), "duracao_h": 1}, headers=PLA)
    client.post(f"/api/ordens/{n}/materiais", json={"codigo": "CAB-1", "qtd_prevista": 3}, headers=PLA)
    for h in (T1, T2):
        o = client.get(f"/api/ordens/{n}", headers=h).json()
        txt = json.dumps(o) + json.dumps(client.get("/api/equipes", headers=h).json()) + json.dumps(client.get("/api/materiais", headers=h).json())
        txt += json.dumps(client.get("/api/painel", headers=h).json()) + json.dumps(client.get("/api/agenda", headers=h).json())
        assert "custo" not in txt, h
    o = client.get(f"/api/ordens/{n}", headers=SUP).json()
    assert o["custos"]["material_previsto"] == 6 and o["materiais"][0]["custo_unitario"] == 2 and o["membros"][0]["custo_hora"] > 0


def test_modelos_snapshot_respostas_aceite_e_importacao(client):
    eq = equipe(client)
    assert client.put("/api/modelos/novo", json={"nome": "X"}, headers=T1).status_code == 403
    m = client.put("/api/modelos/novo", json={"nome": "Medição de aterramento", "tipo": "inspecao", "prioridade": "alta",
                                              "sla_horas": {"alta": 10, "media": 20}, "duracao_h": 3, "exige_aprovacao": False, "exige_aceite": True,
                                              "composicao": [{"funcao": "Eletricista", "qtd": 1, "executor": True}, {"funcao": "Auxiliar", "qtd": 1}],
                                              "checklist": [{"item": "Medir resistência", "resposta_tipo": "numero", "unidade": "Ω", "foto_min": 1},
                                                            {"item": "Conforme?", "resposta_tipo": "simnao"}, {"item": "Obs. opcional", "obrigatorio": False}],
                                              "orientacoes": "Usar luvas isolantes", "origens": ["orcamentos:visita"]}, headers=PLA).json()
    assert m["id"] == "medicao-de-aterramento" and m["checklist"][0]["unidade"] == "Ω" and m["origens"] == ["orcamentos:visita"]
    o = client.post("/api/ordens", json={"titulo": "Medir", "modelo_id": m["id"]}, headers=PLA).json()
    n = o["numero"]
    assert o["prioridade"] == "alta" and o["duracao_h"] == 3 and o["modelo_nome"] == "Medição de aterramento"
    assert (datetime.fromisoformat(o["prazo_sla"].replace("Z", "+00:00")) - datetime.fromisoformat(o["aberta_em"].replace("Z", "+00:00"))).total_seconds() == 36000
    # editar o modelo não muda a OS já aberta (cópia)
    client.put(f"/api/modelos/{m['id']}", json={**m, "checklist": [{"item": "Outro"}], "sla_horas": {"alta": 99}}, headers=PLA)
    det = client.get(f"/api/ordens/{n}", headers=PLA).json()
    assert len(det["itens_checklist"]) == 3 and det["regras"]["orientacoes"] == "Usar luvas isolantes" and det["regras"]["exige_aceite"]
    client.post(f"/api/ordens/{n}/planejar", json={"equipe_id": eq["id"], "execucao_inicio": quando(), "duracao_h": 3}, headers=PLA)
    client.post(f"/api/ordens/{n}/acoes/iniciar", headers=T1)
    seq1 = det["itens_checklist"][0]["seq"]
    foto(client, n, "item", seq1)
    r = client.put(f"/api/ordens/{n}/checklist", json={"itens": [{"seq": seq1, "feito": True}], "parcial": True}, headers=T1)
    assert r.status_code == 409 and "Responda" in r.json()["detail"]
    assert client.put(f"/api/ordens/{n}/checklist", json={"itens": [{"seq": seq1, "feito": True, "resposta": "abc"}], "parcial": True}, headers=T1).status_code == 400
    client.put(f"/api/ordens/{n}/checklist", json={"itens": [{"seq": seq1, "feito": True, "resposta": "4,2"}, {"seq": seq1 + 1, "feito": True, "resposta": "sim"}],
                                                   "parcial": True}, headers=T1)
    r = client.post(f"/api/ordens/{n}/acoes/concluir", json={}, headers=T1)
    assert r.status_code == 409 and "aceite" in r.json()["detail"]
    f = client.post(f"/api/ordens/{n}/acoes/concluir", json={"aceite_nome": "Cliente X"}, headers=T1).json()
    assert f["status"] == "encerrada"  # modelo sem aprovação: conclui e encerra
    # duplicar, desativar e padrão por origem
    dup = client.post(f"/api/modelos/{m['id']}/duplicar", headers=PLA).json()
    assert dup["nome"].endswith("(cópia)") and dup["origens"] == []
    client.put(f"/api/modelos/{dup['id']}", json={**dup, "ativo": False}, headers=PLA)
    assert dup["id"] not in [x["id"] for x in client.get("/api/modelos", headers=PLA).json()]
    assert client.post("/api/ordens", json={"titulo": "z", "modelo_id": dup["id"]}, headers=PLA).status_code == 400
    # exportar → importar (adicionar ignora existentes; atualizar sobrescreve e guarda a versão anterior)
    exp = client.get("/api/modelos-exportar", headers=PLA)
    assert exp.status_code == 200 and json.loads(exp.content)["formato"].startswith("strathub-ordens-modelos")
    r = client.post("/api/modelos-importar", data={"modo": "adicionar"}, files={"arquivo": ("m.json", exp.content)}, headers=GES).json()
    assert r["criados"] == 0 and r["ignorados"] >= 6
    doc = json.loads(exp.content); doc["modelos"][0]["nome"] = "Renomeado"; doc["modelos"].append({"nome": "Modelo novo importado", "tipo": "x"})
    r = client.post("/api/modelos-importar", data={"modo": "atualizar"}, files={"arquivo": ("m.json", json.dumps(doc).encode())}, headers=GES).json()
    assert r["criados"] == 1 and r["atualizados"] >= 6
    assert "Renomeado" in [x["nome"] for x in client.get("/api/modelos?todos=true", headers=PLA).json()]
    aud = client.get("/api/auditoria", headers=GES).json()
    assert any("versão anterior" in a["acao"] for a in aud)


def test_modelos_semeados_uma_vez(tmp_path):
    modulo = _abrir(tmp_path)
    with TestClient(modulo.app) as c:
        assert len(c.get("/api/modelos", headers=PLA).json()) == 5
        c.put("/api/modelos/inspecao", json={"nome": "Inspeção editada", "tipo": "inspecao"}, headers=PLA)
    (tmp_path / "x").mkdir()
    modulo = _abrir(tmp_path)  # reinício: a configuração não sobrescreve o que foi editado na tela
    with TestClient(modulo.app) as c:
        assert "Inspeção editada" in [m["nome"] for m in c.get("/api/modelos", headers=PLA).json()]


def test_migracao_de_banco_1_0(tmp_path):
    con = sqlite3.connect(tmp_path / "ordens.db")
    con.executescript("""CREATE TABLE checklist(numero TEXT NOT NULL, seq INTEGER NOT NULL, item TEXT NOT NULL, obrigatorio INTEGER NOT NULL DEFAULT 1,
        feito INTEGER NOT NULL DEFAULT 0, obs TEXT, por TEXT, em TEXT, PRIMARY KEY(numero, seq));
        CREATE TABLE anexos(id TEXT PRIMARY KEY, numero TEXT NOT NULL, nome TEXT, arquivo TEXT, por TEXT, em TEXT);""")
    con.close()
    modulo = _abrir(tmp_path)
    with TestClient(modulo.app) as c:
        assert c.get("/health").status_code == 200
    cols = {r[1] for r in sqlite3.connect(tmp_path / "ordens.db").execute("PRAGMA table_info(checklist)")}
    assert {"foto_min", "resposta_tipo", "unidade", "resposta"} <= cols


def test_replanejar_quadro(client):
    eq = equipe(client); eq2 = equipe(client, "Equipe C")
    a = client.post("/api/ordens", json={"titulo": "A", "tipo": "corretiva", "prioridade": "alta"}, headers=PLA).json()["numero"]  # SLA 24 h
    b = client.post("/api/ordens", json={"titulo": "B", "tipo": "corretiva", "prioridade": "baixa"}, headers=PLA).json()["numero"]
    assert client.post(f"/api/ordens/{a}/replanejar", json={"execucao_inicio": quando(5)}, headers=PLA).json()["detail"]["codigo"] == "STATUS"
    client.post(f"/api/ordens/{a}/planejar", json={"equipe_id": eq["id"], "execucao_inicio": quando(5), "duracao_h": 2}, headers=PLA)
    client.post(f"/api/ordens/{b}/planejar", json={"equipe_id": eq["id"], "execucao_inicio": quando(30), "duracao_h": 2}, headers=PLA)
    assert client.post(f"/api/ordens/{a}/replanejar", json={"execucao_inicio": quando(6)}, headers=T1).status_code == 403
    r = client.post(f"/api/ordens/{a}/replanejar", json={"execucao_inicio": quando(6), "equipe_id": eq2["id"]}, headers=PLA)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "EQUIPE"
    r = client.post(f"/api/ordens/{a}/replanejar", json={"execucao_inicio": quando(30)}, headers=PLA)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "SLA" and r.json()["detail"]["excede_h"] > 0
    r = client.post(f"/api/ordens/{a}/replanejar", json={"execucao_inicio": quando(30), "confirmar_sla": True, "duracao_h": 3}, headers=PLA).json()
    assert r["duracao_h"] == 3 and [x["numero"] for x in r["conflitos"]] == [b] and r["anterior"]["execucao_inicio"]
    ev = client.get(f"/api/ordens/{a}", headers=PLA).json()["eventos"][0]
    assert ev["acao"] == "OS replanejada" and ev["de"] and ev["para"]
    ag = client.get("/api/agenda?inicio=" + quando(-24)[:10] + "&dias=4", headers=PLA).json()
    assert {i["numero"] for i in ag["itens"]} == {a, b} and ag["a_planejar"] == [] and all(i["executor"] == "Ana" for i in ag["itens"])


def test_feriado_ciencia_e_de_acordo(client):
    client.mod.hubkit.feriado_em = client.mod.feriado_real
    eq = equipe(client)
    feriado = "2026-11-20T13:00:00+00:00"  # Consciência Negra (nacional)
    n = client.post("/api/ordens", json={"titulo": "A", "tipo": "preventiva"}, headers=PLA).json()["numero"]
    r = client.post(f"/api/ordens/{n}/planejar", json={"equipe_id": eq["id"], "execucao_inicio": feriado, "duracao_h": 2}, headers=PLA)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "FERIADO" and set(r.json()["detail"]["sem_escala"]) == {"Ana", "Beto"}
    p = client.post(f"/api/ordens/{n}/planejar", json={"equipe_id": eq["id"], "execucao_inicio": feriado, "duracao_h": 2, "ciencia_feriado": True}, headers=PLA).json()
    assert p["feriado"]["situacao"] == "aguardando_gestor" and p["feriado"]["ciente_por"] == "Planejador Demo"
    assert client.post(f"/api/ordens/{n}/acoes/iniciar", headers=T1).status_code == 409
    assert client.post(f"/api/ordens/{n}/feriado", json={"decisao": "de_acordo"}, headers=PLA).status_code == 403
    assert client.post(f"/api/ordens/{n}/feriado", json={"decisao": "de_acordo"}, headers=GES).json()["feriado"]["situacao"] == "de_acordo"
    # sem de acordo → volta para planejar
    n2 = client.post("/api/ordens", json={"titulo": "B", "tipo": "preventiva"}, headers=PLA).json()["numero"]
    client.post(f"/api/ordens/{n2}/planejar", json={"equipe_id": eq["id"], "execucao_inicio": feriado, "duracao_h": 2, "ciencia_feriado": True}, headers=PLA)
    pend = {x["entity_key"]: x["payload"] for x in client.mod.hubkit.consultar("agenda", "pendencia")} if client.post("/api/sync/agora", headers=GES).json()["ok"] else {}
    acoes = pend[f"ordens:feriado:{n2}"]["acoes"]
    assert [x["codigo"] for x in acoes] == ["de_acordo", "nao"] and acoes[0]["caminho"] == f"/api/ordens/{n2}/feriado"
    r = client.post(f"/api/ordens/{n2}/feriado", json={"decisao": "nao", "comentario": "Sem hora extra"}, headers=GES).json()
    assert r["status"] == "aberta" and r["execucao_inicio"] is None and "feriado" in r["motivo"]
    # equipe toda com escala → normal, sem ciência
    eq3 = equipe(client, "Plantão", escala=True)
    n3 = client.post("/api/ordens", json={"titulo": "C", "tipo": "preventiva"}, headers=PLA).json()["numero"]
    r = client.post(f"/api/ordens/{n3}/planejar", json={"equipe_id": eq3["id"], "execucao_inicio": feriado, "duracao_h": 2}, headers=PLA).json()
    assert r["feriado"]["situacao"] == "escala"
    ag = client.get("/api/agenda?inicio=2026-11-16&dias=7", headers=PLA).json()
    assert any(f["data"] == "2026-11-20" for f in ag["feriados"][eq["id"]])


def test_integracao_crm_e_pendencias_da_agenda(client):
    hk = client.mod.hubkit
    hk.publicar("servicos", "solicitacao_externa", "crm:demo:T-100", {"titulo": "Ar-condicionado sem refrigerar", "descricao": "Sala 3",
        "tipo": "atendimento", "prioridade": "alta", "cliente": {"nome": "Cliente Exemplo"}, "contato": {"nome": "Maria", "telefone": "1199"},
        "local": {"endereco": "Rua Um, 10"}, "crm_ref": "T-100", "crm_nome": "CRM demo", "codigo_projeto": "PRJ-1"}, source_application="conector_crm")
    st = client.post("/api/sync/agora", headers=GES).json()
    assert st["ok"] and st["resultado"]["solicitacoes_do_crm"] == 1
    o = client.get("/api/ordens", headers=GES).json()[0]
    assert o["origem"] == "crm" and o["cliente"] == "Cliente Exemplo" and o["ref_externa"] == "T-100" and o["local"] == "Rua Um, 10" and "Maria" in o["contato"]
    assert o["modelo_id"] == "atendimento"  # modelo padrão da origem CRM
    client.post("/api/sync/agora", headers=GES)
    assert len(client.get("/api/ordens", headers=GES).json()) == 1
    s = hk.consultar("servicos", "status_os", entity_key="crm:demo:T-100")[0]["payload"]
    assert s["status"] == "aberta" and s["numero"] == o["numero"] and s["ref_externa"] == "T-100"
    r = hk.consultar("servicos", "os_resumo", entity_key=o["numero"])[0]["payload"]
    assert r["codigo_projeto"] == "PRJ-1" and r["origem_app"] == "ordens"
    pend = {x["entity_key"]: x["payload"] for x in hk.consultar("agenda", "pendencia")}
    pl = pend[f"ordens:planejar:{o['numero']}"]
    assert pl["status"] == "aberta" and pl["natureza"] == "tarefa" and pl["esforco_h"] == 0.5
    eq = equipe(client, ana_uid="h-ana", beto_uid="h-beto")
    client.post(f"/api/ordens/{o['numero']}/planejar", json={"equipe_id": eq["id"], "execucao_inicio": quando(5), "duracao_h": 2}, headers=PLA)
    client.post("/api/sync/agora", headers=GES)
    pend = {x["entity_key"]: x["payload"] for x in hk.consultar("agenda", "pendencia")}
    assert pend[f"ordens:planejar:{o['numero']}"]["status"] == "resolvida"
    ex = pend[f"ordens:exec:{o['numero']}:h-ana"]
    assert ex["status"] == "aberta" and ex["natureza"] == "operacional" and ex["executor"] and ex["destinatarios"][0]["id_hub"] == "ana"
    assert ex["inicio"] and ex["fim"] and ex["link"].startswith("campo.html")
    assert pend[f"ordens:exec:{o['numero']}:h-beto"]["executor"] is False
    assert hk.consultar("servicos", "status_os", entity_key="crm:demo:T-100")[0]["payload"]["status"] == "planejada"


def test_pendencia_de_aprovacao_com_acoes(client):
    eq = equipe(client)
    n = client.post("/api/ordens", json={"titulo": "A", "tipo": "inspecao"}, headers=PLA).json()["numero"]
    client.post(f"/api/ordens/{n}/planejar", json={"equipe_id": eq["id"], "execucao_inicio": quando(), "duracao_h": 1}, headers=PLA)
    client.post(f"/api/ordens/{n}/acoes/iniciar", headers=T1)
    checklist_completo(client, n); foto(client, n, "depois")
    client.post(f"/api/ordens/{n}/acoes/concluir", json={"resumo": "ok"}, headers=T1)
    client.post("/api/sync/agora", headers=GES)
    p = {x["entity_key"]: x["payload"] for x in client.mod.hubkit.consultar("agenda", "pendencia")}[f"ordens:aprovar:{n}"]
    assert p["natureza"] == "acao" and [a["codigo"] for a in p["acoes"]] == ["encerrar", "devolver"] and p["acoes"][1]["comentario"] == "obrigatorio"


def test_demo_e_meta(client):
    assert client.post("/api/demo", headers=PLA).status_code == 403
    assert client.post("/api/demo", headers=GES).status_code == 200
    assert client.post("/api/demo", headers=GES).status_code == 409
    os_ = client.get("/api/ordens", headers=GES).json()
    assert len(os_) == 8 and any(o["sla"]["situacao"] == "atrasada" for o in os_)
    assert all(o["executor"] for o in os_ if o["status"] != "aberta")
    ag = client.get("/api/agenda?dias=14", headers=PLA).json()
    assert len(ag["equipes"]) == 3 and ag["a_planejar"]
    assert any(x["conflitos"] for x in client.get("/api/agenda?inicio=" + (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d") + "&dias=10", headers=PLA).json()["itens"])
    m = client.get("/api/meta").json()
    assert m["perfis"]["tecnico"] == "Técnico" and m["perfis"]["supervisor"] == "Supervisor" and len(m["tipos"]) == 5 and m["ecossistema"] is None


def test_acoes_offline_usam_a_hora_do_toque(client):
    eq = equipe(client)
    n = client.post("/api/ordens", json={"titulo": "A", "tipo": "inspecao"}, headers=PLA).json()["numero"]
    client.post(f"/api/ordens/{n}/planejar", json={"equipe_id": eq["id"], "execucao_inicio": quando(), "duracao_h": 1}, headers=PLA)
    agora = datetime.now(timezone.utc)
    client.post(f"/api/ordens/{n}/acoes/iniciar", json={"em": (agora - timedelta(hours=2)).isoformat()}, headers=T1)
    client.post(f"/api/ordens/{n}/acoes/pausar", json={"motivo": "Outro", "em": (agora - timedelta(minutes=30)).isoformat()}, headers=T1)
    aps = client.get(f"/api/ordens/{n}", headers=GES).json()["apontamentos"]
    assert sorted(a["horas"] for a in aps) == [1.5, 1.5]
    # hora no futuro ou antiga demais é ignorada (vale a hora do servidor)
    r = client.post(f"/api/ordens/{n}/acoes/iniciar", json={"em": (agora + timedelta(hours=3)).isoformat()}, headers=T1).json()
    assert abs((datetime.fromisoformat(r["cronometro_inicio"].replace("Z", "+00:00")) - agora).total_seconds()) < 60


def test_modelos_vao_no_ecossistema_e_implantam_outra_empresa(tmp_path):
    origem = tmp_path / "a"; origem.mkdir()
    modulo = _abrir(origem)
    os.environ["ORDENS_ESPELHO"] = "1"
    try:
        prog = tmp_path / "prog"; (prog / "config").mkdir(parents=True)
        modulo.B.base_dir = prog
        with TestClient(modulo.app) as c:
            c.put("/api/modelos/novo", json={"nome": "Vistoria de telhado", "tipo": "inspecao", "checklist": [{"item": "Fotografar calhas", "foto_min": 2}]}, headers=PLA)
        doc = json.loads((prog / "config" / "modelos-os.json").read_text())
        assert "Vistoria de telhado" in [m["nome"] for m in doc["modelos"]]
        # outra empresa recebe a pasta config (exportação do ecossistema do hub) e sobe com os mesmos modelos
        destino = tmp_path / "b"; destino.mkdir()
        modulo = _abrir(destino); modulo.B.base_dir = prog
        with TestClient(modulo.app) as c:
            nomes = [m["nome"] for m in c.get("/api/modelos", headers=PLA).json()]
            assert "Vistoria de telhado" in nomes and len(nomes) == 6
            assert c.get("/api/modelos/vistoria-de-telhado", headers=PLA).json()["checklist"][0]["foto_min"] == 2
    finally:
        os.environ.pop("ORDENS_ESPELHO", None)


def test_segregacao_quem_concluiu_nao_aprova(client):
    """Supervisor que executou e concluiu a OS não aprova o próprio encerramento; outro aprovador sim."""
    eq = equipe(client)
    n = client.post("/api/ordens", json={"titulo": "Seg", "tipo": "inspecao"}, headers=PLA).json()["numero"]
    client.post(f"/api/ordens/{n}/planejar", json={"equipe_id": eq["id"], "execucao_inicio": quando(), "duracao_h": 1}, headers=PLA)
    assert client.post(f"/api/ordens/{n}/acoes/iniciar", headers=SUP).status_code == 200
    checklist_completo(client, n, h=SUP); foto(client, n, "depois", h=SUP)
    assert client.post(f"/api/ordens/{n}/acoes/concluir", json={"resumo": "ok"}, headers=SUP).status_code == 200
    d = client.get(f"/api/ordens/{n}", headers=SUP).json()
    assert d["eu"]["concluiu"] and not d["eu"]["master"]
    r = client.post(f"/api/ordens/{n}/acoes/encerrar", headers=SUP)
    assert r.status_code == 403 and "próprio encerramento" in r.json()["detail"]
    assert client.post(f"/api/ordens/{n}/acoes/devolver", json={"motivo": "x"}, headers=SUP).status_code == 403
    assert client.post(f"/api/ordens/{n}/acoes/encerrar", headers=GES).json()["status"] == "encerrada"
