"""Testes do Analisador com rodadas no formato publicado pela PMP:  python -m pytest -q"""
import importlib, io, json, os, random, sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def rodada(mes: int, semente: int, ano: int = 2026) -> dict:
    rnd = random.Random(semente)
    projs = []
    for i in range(12):
        orc = [100_000.0 * (1 + i % 3)] * 12
        real = [v * rnd.uniform(.6, 1.1) for v in orc[:mes]] + [0.0] * (12 - mes)
        tend = real[:mes] + [v * rnd.uniform(.7, 1.0) for v in orc[mes:]]
        dev = sum(tend) - sum(orc)
        fis_plan = [min(100, 8.0 * (k + 1)) for k in range(12)]
        projs.append({"codigo": f"PRJ-{i:03d}", "nome": f"Projeto {i}", "unidade": ["Unidade Norte", "Unidade Sul", "Unidade Leste"][i % 3],
                      "area": ["Engenharia", "Tecnologia"][i % 2], "grupo": "Grupo A", "gerente": "Gerente Demo", "status": "ativo", "peso_fisico": 1,
                      "extras": {},
                      "econ": {"orcado": orc, "realizado": real, "tendencia": tend, "orcado_anos_futuros": {str(ano + 1): 500_000}, "tendencia_anos_futuros": {str(ano + 1): 450_000 + 10_000 * semente},
                               "desvios": [{"categoria": "Postergação", "valor": dev * .7}, {"categoria": "Economia", "valor": dev * .3}], "justificativa": "teste", "status": "aprovada"},
                      "fin": {"orcado": orc, "realizado": real, "tendencia": tend, "orcado_anos_futuros": {str(ano + 1): 500_000}, "tendencia_anos_futuros": {str(ano + 1): 450_000},
                              "desvios": [{"categoria": "Postergação", "valor": dev}], "justificativa": "teste", "status": "aprovada"},
                      "fis": {"orcado": fis_plan, "realizado": [p * .9 for p in fis_plan[:mes]] + [0] * (12 - mes), "tendencia": None, "desvios": [], "justificativa": ""},
                      "compromisso": {"requisicao": [10_000] * 12, "pedido": [50_000] * 12}})
    return {"schema_version": "1.0", "rodada": f"{ano}-{mes:02d}", "nome": f"Rodada {mes}/{ano}", "ano": ano, "mes_referencia": mes, "projetos": projs}


@pytest.fixture()
def client(tmp_path):
    os.environ["ANALISADOR_DADOS"] = str(tmp_path)
    os.environ.pop("HUB_URL", None); os.environ.pop("HUB_APP_TOKEN", None)
    import hubkit, database, trend_engine, analitico, app as modulo
    for m in (hubkit, database, trend_engine, analitico, modulo):
        importlib.reload(m)
    with TestClient(modulo.app) as c:
        c.mod = modulo
        yield c


def test_importacao_por_arquivo_e_todas_as_telas(client):
    r = client.get("/")
    assert r.status_code == 200 and "Modo isolado" in r.text
    assert "Nenhuma rodada" in client.get("/tendencia/geral").text
    for mes, sem in ((6, 1), (7, 2), (8, 3)):
        r = client.post("/importar", files={"arquivo": ("r.json", json.dumps(rodada(mes, sem)).encode())})
        assert r.status_code == 200 and "Importado" in r.text, r.text[:400]
    r = client.post("/importar", files={"arquivo": ("x.json", b"{}")})
    assert "não é uma rodada" in r.text
    r = client.post("/importar", files={"arquivo": ("r.json", json.dumps(rodada(8, 3)).encode())})
    assert "substituiu" in r.text
    for url in ["/tendencia/geral", "/tendencia/fisico", "/tendencia/economico", "/tendencia/financeiro", "/tendencia/pep/PRJ-001",
                "/analise/movimento", "/analise/trajetoria", "/analise/exequibilidade", "/analise/qualidade",
                "/tendencia/geral?filtro_campo=unidade&filtro_valores=Unidade%20Norte", "/analise/movimento?rodada=2026-08&comparar=2026-06&dim=econ&corte=0.1",
                "/tendencia/economico?categoria=economia"]:
        r = client.get(url)
        assert r.status_code == 200, (url, r.text[:600])
        assert "2026-08" in r.text
    traj = client.get("/analise/trajetoria").text
    assert "2027" in traj   # plurianual entra na matriz vintage
    assert client.mod.db.rodada_sort_key("2026-10") > client.mod.db.rodada_sort_key("2026-09")


def test_sincroniza_rodadas_do_data_core(client):
    hk = client.mod.hubkit
    hk.publicar("forecast", "rodada", "2026-06", rodada(6, 1), source_application="pmp")
    assert "Receber" not in client.get("/").text  # fora do hub a tela mostra o modo isolado
    r = client.post("/api/sincronizar")
    assert r.json()["recebidas"] == ["2026-06"]
    assert client.post("/api/sincronizar").json()["recebidas"] == []
    hk.publicar("forecast", "rodada", "2026-06", rodada(6, 9), source_application="pmp")  # republicada
    assert client.post("/api/sincronizar").json()["recebidas"] == ["2026-06"]


def test_mapa_de_categorias(client):
    from categorias import chave_categoria, mapa_categorias
    m = mapa_categorias(Path(client.mod.BASE_DIR))
    assert chave_categoria("Postergação", m) == "postergado" and chave_categoria("Fatores externos / cliente", m) == "poder"
    assert chave_categoria("Algo novo", m) == "outros"
