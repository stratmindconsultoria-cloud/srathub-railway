"""Testes da API do Plano de Investimentos:  python -m pytest -q"""
import importlib, os, sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
PMO = {"X-Usuario": "u-demo-pmo"}; COM = {"X-Usuario": "u-demo-comite"}
G1 = {"X-Usuario": "u-demo-gestor1"}; G2 = {"X-Usuario": "u-demo-gestor2"}; LEI = {"X-Usuario": "u-demo-leitor"}


@pytest.fixture()
def client(tmp_path):
    os.environ["PLANO_DADOS"] = str(tmp_path)
    os.environ.pop("HUB_URL", None); os.environ.pop("HUB_APP_TOKEN", None)
    import hubkit, appkit, app as modulo
    importlib.reload(hubkit); importlib.reload(appkit); importlib.reload(modulo)
    with TestClient(modulo.app) as c:
        c.mod = modulo
        yield c


def ciclo_com_base(client):
    r = client.post("/api/ciclos", json={"ano_inicio": 2027, "ano_fim": 2031}, headers=PMO)
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    a = client.post(f"/api/ciclos/{cid}/projetos", json={"nome": "Subestação", "unidade": "Unidade Norte", "valores": {"2027": 100, "2028": 200}}, headers=PMO).json()["plano_id"]
    b = client.post(f"/api/ciclos/{cid}/projetos", json={"nome": "Sistemas", "unidade": "Unidade Sul", "valores": {"2027": 50, "2029": 50}}, headers=PMO).json()["plano_id"]
    return cid, a, b


def test_fluxo_completo_base_pleitos_cenario_aprovacao(client):
    cid, a, b = ciclo_com_base(client)
    assert client.post("/api/ciclos", json={"ano_inicio": 2027, "ano_fim": 2031}, headers=G1).status_code == 403
    # base editável só na preparação
    assert client.put(f"/api/ciclos/{cid}/valores", json={"plano_id": a, "camada": "base", "valores": {"2027": 120}}, headers=PMO).status_code == 200
    assert client.put(f"/api/ciclos/{cid}/valores", json={"plano_id": a, "camada": "pleito", "valores": {"2027": 1}}, headers=PMO).status_code == 409
    assert client.post(f"/api/ciclos/{cid}/fase", json={"fase": "pleitos"}, headers=PMO).json()["rodada"] == 1
    assert client.put(f"/api/ciclos/{cid}/valores", json={"plano_id": a, "camada": "base", "valores": {"2027": 1}}, headers=PMO).status_code == 403
    # pleito exige justificativa e respeita a unidade do gestor
    assert client.put(f"/api/ciclos/{cid}/valores", json={"plano_id": a, "camada": "pleito", "valores": {"2027": 150}}, headers=G1).status_code == 400
    assert client.put(f"/api/ciclos/{cid}/valores", json={"plano_id": a, "camada": "pleito", "valores": {"2027": 150}, "justificativa": "reajuste"}, headers=G1).status_code == 200
    assert client.put(f"/api/ciclos/{cid}/valores", json={"plano_id": a, "camada": "pleito", "valores": {"2027": 160}, "justificativa": "x"}, headers=G2).status_code == 403
    assert client.put(f"/api/ciclos/{cid}/valores", json={"plano_id": a, "camada": "pleito", "valores": {"2027": 160}, "justificativa": "x"}, headers=LEI).status_code == 403
    alts = client.get(f"/api/ciclos/{cid}/alteracoes").json()
    assert alts[0]["de"] == 120 and alts[0]["para"] == 150 and alts[0]["rodada"] == 1
    rej = client.put(f"/api/ciclos/{cid}/valores", json={"plano_id": b, "camada": "pleito", "valores": {"2029": 80}, "justificativa": "y"}, headers=G2)
    assert rej.status_code == 200
    seq = client.get(f"/api/ciclos/{cid}/alteracoes").json()[0]["seq"]
    assert client.post(f"/api/ciclos/{cid}/alteracoes/{seq}/revisar", json={"decisao": "rejeitado"}, headers=PMO).status_code == 200
    cart = client.get(f"/api/ciclos/{cid}/carteira?camadas=base,pleito").json()
    vb = next(p for p in cart["projetos"] if p["plano_id"] == b)["valores"]
    assert vb["pleito"]["2029"] == 50 and vb["base"]["2027"] == 50
    # comitê
    assert client.post(f"/api/ciclos/{cid}/fase", json={"fase": "comite"}, headers=PMO).status_code == 200
    sid = client.post(f"/api/ciclos/{cid}/cenarios", json={"nome": "A"}, headers=PMO).json()["id"]
    assert client.post(f"/api/ciclos/{cid}/cenarios/{sid}/decisao", json={"plano_id": b, "decisao": "cortar"}, headers=PMO).status_code == 200
    assert client.post(f"/api/ciclos/{cid}/cenarios/{sid}/decisao", json={"plano_id": a, "decisao": "postergar", "anos": 1}, headers=PMO).status_code == 200
    v = client.get(f"/api/ciclos/{cid}/carteira?camadas=c:{sid}").json()["projetos"]
    va = next(p for p in v if p["plano_id"] == a)["valores"][f"c:{sid}"]
    assert va["2027"] == 0 and va["2028"] == 150 and va["2029"] == 200
    comp = client.get(f"/api/ciclos/{cid}/comparar?a=pleito&b=c:{sid}").json()
    assert comp["ponte"]["cortes"] == -100 and comp["total_b"] == 350
    client.put(f"/api/ciclos/{cid}/metas", json={"metas": [{"escopo": "unidade", "chave": "Unidade Sul", "percentual": 0.5}]}, headers=COM)
    sul = next(u for u in client.get(f"/api/ciclos/{cid}/comparar?a=pleito&b=c:{sid}").json()["unidades"] if u["unidade"] == "Unidade Sul")
    assert sul["meta_ok"] is True
    assert client.post(f"/api/ciclos/{cid}/cenarios/{sid}/aprovar", json={}, headers=COM).status_code == 409  # não congelado
    client.post(f"/api/ciclos/{cid}/cenarios/{sid}/congelar", headers=PMO)
    assert client.post(f"/api/ciclos/{cid}/cenarios/{sid}/decisao", json={"plano_id": a, "decisao": "cortar"}, headers=PMO).status_code == 409
    assert client.post(f"/api/ciclos/{cid}/cenarios/{sid}/aprovar", json={}, headers=G1).status_code == 403
    r = client.post(f"/api/ciclos/{cid}/cenarios/{sid}/aprovar", json={}, headers=COM)
    assert r.status_code == 200 and r.json()["publicacao"]["publicados"] == 2
    pub = client.mod.hubkit.consultar("pmp", "plano_aprovado")
    assert {p["payload"]["status"] for p in pub} == {"postergado", "cortado"}
    pa = next(p for p in pub if p["payload"]["plano_id"] == a)["payload"]
    assert pa["orcamento_total"] == 350 and round(sum(pa["curva_mensal"].values()), 2) == 350
    painel = client.get(f"/api/ciclos/{cid}/painel?camada=aprovado").json()
    assert painel["total"] == 350 and painel["base"]["total"] == 420


def test_entrada_da_originacao_e_retorno(client):
    hk = client.mod.hubkit
    cid = client.post("/api/ciclos", json={"ano_inicio": 2028, "ano_fim": 2032}, headers=PMO).json()["id"]
    hk.publicar("portfolio", "entrada_originacao", "ORI-000001", {"lote_id": "ENV-1", "projeto": {
        "id_mestre": "ORI-000001", "pep": "", "nome": "Projeto da Originação", "unidade": "Unidade Norte", "diretoria": "Engenharia",
        "ciclo_pqq": "c-orig", "ciclo_inicio": 2028, "ciclo_fim": 2032,
        "fsp": {"distribuicaoMensal": {"2028-01": 10, "2028-06": 30, "2029-03": 60}}}}, period="current", source_application="originacao")
    pend = client.get("/api/integracoes/pendentes", headers=PMO).json()
    assert [x["id_mestre"] for x in pend["originacao"]] == ["ORI-000001"]
    r = client.post("/api/integracoes/sincronizar", json={}, headers=PMO).json()
    assert r["originacao"][0]["resultado"] == "incluido" and r["originacao"][0]["ciclo"] == cid
    assert client.get("/api/integracoes/pendentes", headers=PMO).json()["originacao"] == []
    proj = client.get(f"/api/ciclos/{cid}/carteira?camadas=base").json()["projetos"][0]
    assert proj["valores"]["base"]["2028"] == 40 and proj["valores"]["base"]["2029"] == 60
    client.post(f"/api/ciclos/{cid}/fase", json={"fase": "pleitos"}, headers=PMO)
    client.post(f"/api/ciclos/{cid}/fase", json={"fase": "comite"}, headers=PMO)
    sid = client.post(f"/api/ciclos/{cid}/cenarios", json={}, headers=PMO).json()["id"]
    client.post(f"/api/ciclos/{cid}/cenarios/{sid}/congelar", headers=PMO)
    client.post(f"/api/ciclos/{cid}/cenarios/{sid}/aprovar", json={}, headers=PMO)
    ret = hk.consultar("portfolio", "retorno_originacao", entity_key="ORI-000001")[0]["payload"]
    assert ret["status"] == "aprovado" and ret["ciclo_id"] == "c-orig" and ret["orcamento_total"] == 100
    assert ret["curva_mensal"]["2028-06"] == 30 and ret["curva_mensal"]["2029-03"] == 60  # respeita a curva da Ficha de Projeto


def test_importacao_modelo_e_exportacao(client):
    import io
    from openpyxl import Workbook, load_workbook
    cid = client.post("/api/ciclos", json={"ano_inicio": 2027, "ano_fim": 2029}, headers=PMO).json()["id"]
    m = client.get(f"/api/ciclos/{cid}/modelo.xlsx")
    assert m.status_code == 200 and load_workbook(io.BytesIO(m.content)).active["G1"].value == "2027"
    wb = Workbook(); ws = wb.active
    ws.append(["Código", "Nome", "Unidade", "Área", "Categoria", "Prioridade", "2027", "2028", "2029"])
    ws.append(["WBS-1", "Projeto A", "Unidade Norte", "Engenharia", "Expansão", 1, 10, 20, 30])
    ws.append(["WBS-2", "Projeto B", "Unidade Sul", "Tecnologia", "Tecnologia", 2, 5, 0, 0])
    buf = io.BytesIO(); wb.save(buf)
    r = client.post(f"/api/ciclos/{cid}/importar", files={"arquivo": ("base.xlsx", buf.getvalue())}, headers=PMO)
    assert r.status_code == 200 and r.json()["incluidos"] == 2
    r = client.post(f"/api/ciclos/{cid}/importar", files={"arquivo": ("base.xlsx", buf.getvalue())}, headers=PMO)
    assert r.json()["atualizados"] == 2
    x = client.get(f"/api/ciclos/{cid}/exportar.xlsx?camadas=base")
    wbx = load_workbook(io.BytesIO(x.content))
    assert wbx["base"].max_row == 3 and "Alterações" in wbx.sheetnames


def test_risco_execucao_monte_carlo(client):
    hk = client.mod.hubkit
    cid = client.post("/api/ciclos", json={"ano_inicio": 2027, "ano_fim": 2031}, headers=PMO).json()["id"]
    client.post(f"/api/ciclos/{cid}/projetos", json={"nome": "P", "valores": {"2027": 1000}}, headers=PMO)
    assert client.get(f"/api/ciclos/{cid}/risco-execucao?camada=base").json()["suficiente"] is False
    for i in range(12):
        hk.publicar("portfolio", "execucao", f"P{i}", {"ano_rodada": 2026, "orcado_ano": {"2024": 100, "2025": 100}, "realizado_ano": {"2024": 60 + i, "2025": 70}}, source_application="pmp")
    r = client.get(f"/api/ciclos/{cid}/risco-execucao?camada=base").json()
    assert r["suficiente"] and r["amostras"] == 24 and r["p50"] < 1000 and r["prob_atingir"]["1.0"] == 0


def test_demo_e_sessao(client):
    assert client.post("/api/demo", headers=PMO).status_code == 200
    assert client.post("/api/demo", headers=PMO).status_code == 409
    assert client.get("/api/meta").json()["perfis"]["comite"]
    assert client.get("/api/ciclos").json()[0]["projetos"] == 14


def test_sincronizacao_automatica_e_pendencias(client):
    hk = client.mod.hubkit
    cid = client.post("/api/ciclos", json={"ano_inicio": 2028, "ano_fim": 2030}, headers=PMO).json()["id"]
    hk.publicar("portfolio", "entrada_originacao", "ORI-000009", {"lote_id": "ENV-2", "projeto": {
        "id_mestre": "ORI-000009", "nome": "Entrada automática", "unidade": "Unidade Norte", "ciclo_inicio": 2028, "ciclo_fim": 2030,
        "distribuicao_anual_referencia": {"2028": 50}}}, period="current", source_application="originacao")
    st = client.post("/api/sync/agora", headers=PMO).json()
    assert st["ok"] and st["resultado"]["projetos_originacao"] == 1
    assert client.get(f"/api/ciclos/{cid}/carteira?camadas=base").json()["projetos"][0]["id_mestre"] == "ORI-000009"
    pend = {x["entity_key"]: x["payload"] for x in hk.consultar("agenda", "pendencia")}
    assert f"plano:ciclo:{cid}:preparacao" in pend and pend[f"plano:ciclo:{cid}:preparacao"]["origem_app"] == "plano"
    client.post(f"/api/ciclos/{cid}/fase", json={"fase": "pleitos"}, headers=PMO)
    client.post("/api/sync/agora", headers=PMO)
    pend = {x["entity_key"]: x["payload"] for x in hk.consultar("agenda", "pendencia")}
    assert pend[f"plano:ciclo:{cid}:preparacao"]["status"] == "resolvida" and pend[f"plano:ciclo:{cid}:pleitos"]["status"] == "aberta"
    assert client.get("/api/sync/status").json()["modo"] == "isolado"
