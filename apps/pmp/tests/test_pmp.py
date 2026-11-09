"""Testes da API da PMP:  python -m pytest -q"""
import base64, importlib, io, os, sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
PMO = {"X-Usuario": "u-demo-pmo"}; G1 = {"X-Usuario": "u-demo-ger1"}; G2 = {"X-Usuario": "u-demo-ger2"}; LEI = {"X-Usuario": "u-demo-leitor"}


@pytest.fixture()
def client(tmp_path):
    os.environ["PMP_DADOS"] = str(tmp_path)
    os.environ.pop("HUB_URL", None); os.environ.pop("HUB_APP_TOKEN", None)
    import hubkit, appkit, app as modulo
    importlib.reload(hubkit); importlib.reload(appkit); importlib.reload(modulo)
    with TestClient(modulo.app) as c:
        c.mod = modulo
        yield c


def projeto(client, cod="PRJ-1", uni="Unidade Norte"):
    assert client.put(f"/api/projetos/{cod}", json={"nome": "Projeto " + cod, "unidade": uni}, headers=PMO).status_code == 200
    for tipo, vals in (("orcado", [100000] * 12), ("realizado", [80000] * 6)):
        for dim in ("econ", "fin"):
            client.put(f"/api/projetos/{cod}/curvas", json={"ano": 2026, "dimensao": dim, "tipo": tipo, "meses": vals}, headers=PMO)
    client.put(f"/api/projetos/{cod}/curvas", json={"ano": 2026, "dimensao": "fis", "tipo": "orcado", "meses": [k * 8 for k in range(1, 13)]}, headers=PMO)
    client.put(f"/api/projetos/{cod}/curvas", json={"ano": 2026, "dimensao": "fis", "tipo": "realizado", "meses": [k * 7 for k in range(1, 7)]}, headers=PMO)


def test_rodada_tendencia_desvios_aprovacao_e_publicacao(client):
    projeto(client); projeto(client, "PRJ-2", "Unidade Sul")
    assert client.put("/api/projetos/PRJ-1", json={"nome": "x"}, headers=LEI).status_code == 403
    assert client.post("/api/rodadas", json={"ano": 2026, "mes_ref": 6}, headers=G1).status_code == 403
    assert client.post("/api/rodadas", json={"ano": 2026, "mes_ref": 6}, headers=PMO).status_code == 200
    assert client.post("/api/rodadas", json={"ano": 2026, "mes_ref": 7}, headers=PMO).status_code == 409
    d = client.get("/api/projetos/PRJ-1").json()
    t = d["tendencia"]["fin"]
    assert t["status"] == "nova" and t["meses"][:6] == [80000] * 6 and t["meses"][6:] == [100000] * 6  # fechados = realizado
    meses = [0] * 6 + [100000] * 6   # meses fechados são forçados pelo realizado
    r = client.put("/api/rodadas/2026-06/tendencias/PRJ-1", json={"dimensao": "fin", "meses": meses, "enviar": True}, headers=G1)
    assert r.status_code == 400 and r.json()["detail"]["desvio"] == -120000  # 1.080.000 vs 1.200.000 orçado
    desv = [{"categoria": "Postergação", "valor": -100000}, {"categoria": "Economia", "valor": -20000}]
    assert client.put("/api/rodadas/2026-06/tendencias/PRJ-1", json={"dimensao": "fin", "meses": meses, "enviar": True, "desvios": desv[:1], "justificativa": "x"}, headers=G1).status_code == 400
    assert client.put("/api/rodadas/2026-06/tendencias/PRJ-1", json={"dimensao": "fin", "meses": meses, "enviar": True, "desvios": desv}, headers=G1).status_code == 400
    r = client.put("/api/rodadas/2026-06/tendencias/PRJ-1", json={"dimensao": "fin", "meses": meses, "enviar": True, "desvios": desv, "justificativa": "Contrato atrasou"}, headers=G1)
    assert r.status_code == 200 and r.json()["status"] == "enviada"
    assert client.put("/api/rodadas/2026-06/tendencias/PRJ-2", json={"dimensao": "fin", "meses": meses}, headers=G1).status_code == 403  # outra unidade
    assert client.put("/api/rodadas/2026-06/tendencias/PRJ-1", json={"dimensao": "fin", "meses": meses}, headers=G1).status_code == 409  # já enviada
    assert client.post("/api/rodadas/2026-06/fechar", headers=PMO).status_code == 409  # pendente de decisão
    assert client.post("/api/rodadas/2026-06/tendencias/PRJ-1/decidir", json={"decisao": "devolver"}, headers=PMO).status_code == 400
    assert client.post("/api/rodadas/2026-06/tendencias/PRJ-1/decidir", json={"decisao": "aprovar", "dimensao": "fin"}, headers=PMO).status_code == 200
    sit = client.get("/api/rodadas/2026-06/situacao").json()
    assert sit["projetos"][0]["dims"]["fin"]["status"] == "aprovada"
    r = client.post("/api/rodadas/2026-06/fechar", headers=PMO)
    assert r.status_code == 200 and r.json()["publicacao"]["rodada"] and r.json()["publicacao"]["execucao"] == 2
    hk = client.mod.hubkit
    snap = hk.consultar("forecast", "rodada", entity_key="2026-06")[0]["payload"]
    p1 = next(p for p in snap["projetos"] if p["codigo"] == "PRJ-1")
    assert sum(p1["fin"]["tendencia"]) == 1080000 and p1["fin"]["desvios"][0]["categoria"] == "Postergação" and p1["fin"]["status"] == "aprovada"
    assert next(p for p in snap["projetos"] if p["codigo"] == "PRJ-2")["fin"]["status"] == "sem_atualizacao"
    exe = hk.consultar("portfolio", "execucao", entity_key="PRJ-1")[0]["payload"]
    assert exe["tendencia_ano"]["2026"] == 1080000 and exe["realizado_ano"]["2026"] == 480000
    # nova rodada parte da tendência oficial anterior
    client.post("/api/rodadas", json={"ano": 2026, "mes_ref": 7}, headers=PMO)
    t = client.get("/api/projetos/PRJ-1").json()["tendencia"]["fin"]
    assert t["meses"][6] == 0 and t["meses"][7] == 100000   # julho = realizado (0); agosto = tendência anterior


def test_mudancas_atas_comentarios_documentos(client):
    projeto(client)
    m = client.post("/api/projetos/PRJ-1/mudancas", json={"titulo": "Ampliar escopo", "tipo": "Escopo", "impacto_custo": 50}, headers=G1)
    assert m.status_code == 200
    mid = m.json()["id"]
    assert client.post(f"/api/mudancas/{mid}/decidir", json={"status": "implementada"}, headers=PMO).status_code == 409
    assert client.post(f"/api/mudancas/{mid}/decidir", json={"status": "aprovada"}, headers=G1).status_code == 403
    assert client.post(f"/api/mudancas/{mid}/decidir", json={"status": "aprovada", "parecer": "ok"}, headers=PMO).json()["status"] == "aprovada"
    a = client.post("/api/atas", json={"titulo": "Status semanal", "codigo": "PRJ-1", "data": "2026-06-10", "acoes": [{"descricao": "Enviar cronograma", "responsavel": "Gerente", "prazo": "2026-06-20"}]}, headers=G1)
    assert a.status_code == 200
    assert len(client.get("/api/acoes").json()) == 1
    xid = client.get("/api/acoes").json()[0]["id"]
    assert client.post(f"/api/acoes/{xid}", json={"status": "concluida"}, headers=G1).status_code == 200
    assert client.post(f"/api/atas/{a.json()['id']}/aprovar", headers=PMO).status_code == 200
    assert client.post("/api/atas", json={"id": a.json()["id"], "titulo": "y"}, headers=PMO).status_code == 409
    assert client.post("/api/projetos/PRJ-1/comentarios", json={"texto": "ok"}, headers=LEI).status_code == 403
    assert client.post("/api/projetos/PRJ-1/comentarios", json={"texto": "Riscos revisados"}, headers=G1).status_code == 200
    r = client.post("/api/projetos/PRJ-1/documentos", files={"arquivo": ("cronograma.pdf", b"%PDF-1.4 teste")}, headers=G1)
    assert client.get(f"/api/documentos/{r.json()['id']}").content.startswith(b"%PDF")


def test_importacao_modelos(client):
    from openpyxl import Workbook
    def xl(linhas):
        wb = Workbook(); ws = wb.active
        for l in linhas: ws.append(l)
        b = io.BytesIO(); wb.save(b); return b.getvalue()
    assert client.get("/api/modelos/curvas.xlsx").status_code == 200
    r = client.post("/api/importar/projetos", files={"arquivo": ("p.xlsx", xl([["Código", "Nome", "Unidade"], ["A1", "Projeto A", "Unidade Norte"], ["", "", ""]]))}, headers=PMO)
    assert r.json()["importadas"] == 1
    linhas = [["Código", "Ano", "Dimensão", "Tipo"] + client.mod.MESES, ["A1", 2026, "Financeiro", "Orçado"] + [10] * 12,
              ["A1", 2026, "Físico", "Realizado"] + [5] * 12, ["X9", 2026, "Financeiro", "Orçado"] + [1] * 12, ["A1", 2026, "Abc", "Orçado"] + [1] * 12]
    r = client.post("/api/importar/curvas", files={"arquivo": ("c.xlsx", xl(linhas))}, headers=PMO).json()
    assert r["importadas"] == 2 and len(r["erros"]) == 2
    d = client.get("/api/projetos/A1?ano=2026").json()
    assert sum(d["curvas"]["fin"]["orcado"]) == 120 and d["curvas"]["fis"]["realizado"][0] == 5
    assert client.post("/api/importar/projetos", files={"arquivo": ("p.xlsx", xl([["Nome"], ["x"]]))}, headers=PMO).status_code == 400


def test_integracao_tap_e_plano(client):
    hk = client.mod.hubkit
    hk.publicar("pmp", "tap_aprovado", "ORI-000001", {"id_mestre": "ORI-000001", "codigo_erp": "", "nome": "Projeto da Originação", "unidade": "Unidade Norte",
        "area": "Engenharia", "responsavel": "Eng. Demo", "escopo_aprovado": "Escopo", "inicio_previsto": "2027-01-01", "conclusao_compromissada": "2028-06-30",
        "orcamento_total_aprovado": 300, "curva_mensal_aprovada": {"2027-01": 100, "2027-02": 100, "2028-01": 100}, "plano_id": "PPI-0001",
        "tap_aprovado_por": "Aprovador", "tap_aprovado_em": "2026-10-01", "tap_pdf": {"nome": "TAP.pdf", "base64": base64.b64encode(b"%PDF-x").decode()}},
        source_application="originacao")
    hk.publicar("pmp", "plano_aprovado", "WBS-9", {"plano_id": "PPI-0009", "codigo": "WBS-9", "nome": "Projeto em execução", "unidade": "Unidade Sul",
        "status": "aprovado", "ciclo_inicio": 2027, "ciclo_fim": 2028, "ciclo_nome": "PPI 2027–2028", "orcamento_total": 240,
        "curva_mensal": {"2027-03": 120, "2028-05": 120}}, source_application="plano")
    assert len(client.get("/api/integracoes/pendentes", headers=PMO).json()["tap"]) == 1
    r = client.post("/api/integracoes/sincronizar", headers=PMO).json()
    assert r == {"tap": ["ORI-000001"], "plano": ["WBS-9"], "atas": [], "ordens": []}
    assert client.post("/api/integracoes/sincronizar", headers=PMO).json() == {"tap": [], "plano": [], "atas": [], "ordens": []}
    d = client.get("/api/projetos/ORI-000001?ano=2027").json()
    assert sum(d["curvas"]["fin"]["orcado"]) == 200 and d["projeto"]["plano_id"] == "PPI-0001" and d["documentos"][0]["tipo"] == "TAP"
    w = client.get("/api/projetos/WBS-9?ano=2027").json()
    assert w["curvas"]["fin"]["orcado"][2] == 120 and w["futuros"]["orcado|fin"] == {"2028": 120}


def test_demo_carteira(client):
    assert client.post("/api/demo", headers=PMO).status_code == 200
    c = client.get("/api/carteira").json()
    assert len(c["projetos"]) == 10 and c["rodada"]["status"] == "aberta" and c["mes_ref"] == 8
    x = client.get("/api/exportar/tendencia.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"


def test_agenda_os_e_pendencias(client):
    hk = client.mod.hubkit
    projeto(client, "PRJ-7")
    hk.publicar("pmp", "ata_rascunho", "ev-123", {"titulo": "Status semanal", "data": "2026-11-05T10:00", "codigo_projeto": "PRJ-7",
                "participantes": ["Ana", "Bruno"], "pauta": "Cronograma"}, source_application="stratagenda")
    hk.publicar("servicos", "os_resumo", "OS-0001", {"numero": "OS-0001", "titulo": "Troca de disjuntor", "status": "planejada", "codigo_projeto": "PRJ-7",
                "custo_previsto": 500, "custo_real": 0, "origem_app": "ordens"}, source_application="ordens")
    r = client.post("/api/integracoes/sincronizar", headers=PMO).json()
    assert len(r["atas"]) == 1 and r["ordens"] == ["OS-0001"]
    d = client.get("/api/projetos/PRJ-7").json()
    assert d["atas"][0]["status"] == "rascunho" and d["atas"][0]["titulo"] == "Status semanal" and d["ordens"][0]["numero"] == "OS-0001"
    # republicar o mesmo evento atualiza o rascunho (não duplica)
    hk.publicar("pmp", "ata_rascunho", "ev-123", {"titulo": "Status semanal (remarcada)", "data": "2026-11-06T10:00", "codigo_projeto": "PRJ-7"}, source_application="stratagenda")
    client.post("/api/integracoes/sincronizar", headers=PMO)
    d = client.get("/api/projetos/PRJ-7").json()
    assert len(d["atas"]) == 1 and d["atas"][0]["titulo"].endswith("(remarcada)")
    # pendências: ata em rascunho e rodada aberta viram agenda/pendencia; o que some é publicado como resolvido
    client.post("/api/rodadas", json={"ano": 2026, "mes_ref": 6}, headers=PMO)
    st = client.post("/api/sync/agora", headers=PMO).json()
    assert st["ok"] and st["resultado"]["pendencias"]["abertas"] >= 2
    pend = {x["entity_key"]: x["payload"] for x in hk.consultar("agenda", "pendencia")}
    assert any(k.startswith("pmp:rodada:") for k in pend) and any(k.startswith("pmp:ata:") for k in pend)
    aid = d["atas"][0]["id"]
    client.post(f"/api/atas/{aid}/aprovar", headers=PMO)
    st = client.post("/api/sync/agora", headers=PMO).json()
    assert st["resultado"]["pendencias"]["resolvidas"] == 1
    assert hk.consultar("agenda", "pendencia", entity_key=f"pmp:ata:{aid}")[0]["payload"]["status"] == "resolvida"


def test_campos_extras_e_rotulos_de_perfil(client, tmp_path):
    cfg = client.mod.B.config()
    assert client.get("/api/meta").json()["campos_extras"] == []
    client.mod.B.config = lambda: {**cfg, "campos_extras": [{"chave": "contrato", "rotulo": "Contrato"}], "perfis": {"gerente": "Coordenador"}}
    m = client.get("/api/meta").json()
    assert m["campos_extras"][0]["chave"] == "contrato" and m["perfis"]["gerente"] == "Coordenador" and m["perfis"]["pmo"].startswith("PMO")
    projeto(client, "PRJ-8")
    client.put("/api/projetos/PRJ-8", json={"extras": {"contrato": "CT-1"}}, headers=PMO)
    client.put("/api/projetos/PRJ-8", json={"extras": {"risco": "Alto"}}, headers=PMO)
    assert client.get("/api/projetos/PRJ-8").json()["projeto"]["extras"] == {"contrato": "CT-1", "risco": "Alto"}
