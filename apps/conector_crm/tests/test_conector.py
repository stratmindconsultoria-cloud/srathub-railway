"""Testes do Conector CRM:  python -m pytest -q"""
import hashlib, hmac, importlib, io, json, os, sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
INT = {"X-Usuario": "u-demo-int"}; LEI = {"X-Usuario": "u-demo-leitor"}


@pytest.fixture()
def client(tmp_path):
    os.environ["CONECTOR_CRM_DADOS"] = str(tmp_path)
    os.environ.pop("HUB_URL", None); os.environ.pop("HUB_APP_TOKEN", None)
    import hubkit, appkit, app as modulo
    importlib.reload(hubkit); importlib.reload(appkit); importlib.reload(modulo)
    with TestClient(modulo.app) as c:
        c.mod = modulo
        yield c


def nova(client, **extra):
    r = client.put("/api/integracoes/nova", json={"nome": "CRM Comercial", **extra}, headers=INT).json()
    return r["id"], r["chave"]


def test_entrada_por_webhook_com_chave(client):
    assert client.put("/api/integracoes/nova", json={"nome": "x"}, headers=LEI).status_code == 403
    iid, chave = nova(client)
    assert iid == "crm-comercial" and chave.startswith("crm_")
    lista = client.get("/api/integracoes", headers=LEI).json()
    assert "chave_hash" not in lista[0] and lista[0]["chave_dica"].endswith(chave[-4:])
    sol = {"crm_ref": "T-1", "titulo": "Vazamento", "prioridade": "high", "cliente": {"nome": "ACME"}, "contato": {"telefone": "1199"}, "local": "Rua A, 1"}
    assert client.post(f"/entrada/{iid}/solicitacoes", json=sol).status_code == 401
    assert client.post(f"/entrada/{iid}/solicitacoes", json=sol, headers={"X-Chave": "errada"}).status_code == 401
    r = client.post(f"/entrada/{iid}/solicitacoes", json={"solicitacoes": [sol, {"titulo": "sem ref"}]}, headers={"Authorization": "Bearer " + chave}).json()
    assert r["recebidas"] == 1 and r["erros"][0]["item"] == 2 and "crm_ref" in r["erros"][0]["erro"]
    reg = client.mod.hubkit.consultar("servicos", "solicitacao_externa", entity_key=f"crm:{iid}:T-1")[0]["payload"]
    assert reg["prioridade"] == "alta" and reg["cliente"]["nome"] == "ACME" and reg["local"]["endereco"] == "Rua A, 1" and reg["crm_nome"] == "CRM Comercial"
    # status antes da OS existir
    s = client.get(f"/entrada/{iid}/status/T-1", headers={"X-Chave": chave}).json()
    assert s["status"] == "recebida"
    assert client.get(f"/entrada/{iid}/status/T-404", headers={"X-Chave": chave}).status_code == 404
    # nova chave invalida a anterior
    k2 = client.put(f"/api/integracoes/{iid}", json={"gerar_chave": True}, headers=INT).json()["chave"]
    assert client.post(f"/entrada/{iid}/solicitacoes", json=sol, headers={"X-Chave": chave}).status_code == 401
    assert client.post(f"/entrada/{iid}/solicitacoes", json=sol, headers={"X-Chave": k2}).status_code == 200


def test_de_para_e_arquivo(client):
    iid, chave = nova(client)
    mapa = {"crm_ref": "id", "titulo": "properties.subject", "contato.email": "contacts.0.email", "cliente.nome": "company.name"}
    assert client.put(f"/api/integracoes/{iid}", json={"mapeamento": {"inexistente": "x"}}, headers=INT).status_code == 400
    client.put(f"/api/integracoes/{iid}", json={"mapeamento": mapa, "padroes": {"tipo": "corretiva", "prioridade": "media"}}, headers=INT)
    ex = {"id": 987, "properties": {"subject": "Porta emperrada"}, "contacts": [{"email": "a@b.c"}], "company": {"name": "Beta"}}
    t = client.post(f"/api/integracoes/{iid}/testar", json={"exemplo": ex}, headers=INT).json()
    assert t["ok"] and t["resultado"] == {"crm_ref": "987", "titulo": "Porta emperrada", "tipo": "corretiva", "prioridade": "media",
                                          "cliente": {"nome": "Beta"}, "contato": {"email": "a@b.c"}}
    client.post(f"/entrada/{iid}/solicitacoes", json=[ex], headers={"X-Chave": chave})
    assert client.mod.hubkit.consultar("servicos", "solicitacao_externa", entity_key=f"crm:{iid}:987")
    # contingência por CSV (sem de-para: formato neutro achatado)
    iid2, _ = nova(client, crm="Planilha")
    csv_ = "crm_ref;titulo;cliente.nome;contato_telefone\nP-1;Troca de lâmpada;Gama;1188\nP-2;;Delta;\n"
    r = client.post(f"/api/integracoes/{iid2}/importar", files={"arquivo": ("s.csv", io.BytesIO(csv_.encode()))}, headers=INT).json()
    assert r["recebidas"] == 1 and len(r["erros"]) == 1
    p = client.mod.hubkit.consultar("servicos", "solicitacao_externa", entity_key=f"crm:{iid2}:P-1")[0]["payload"]
    assert p["cliente"]["nome"] == "Gama" and p["contato"]["telefone"] == "1188" and p["canal"] == "arquivo"
    det = client.get(f"/api/integracoes/{iid2}", headers=LEI).json()
    assert [e["status"] for e in det["entradas"]] == ["erro", "ok"]


def test_saida_de_status_por_webhook_assinado_com_reenvio(client, monkeypatch):
    iid, chave = nova(client)
    client.put(f"/api/integracoes/{iid}", json={"webhook_url": "https://crm.exemplo/hook", "webhook_segredo": "s3gr3d0"}, headers=INT)
    hk = client.mod.hubkit
    enviados, falhar = [], {"n": 1}
    def http(metodo, url, corpo, cab, timeout=15):
        if falhar["n"] > 0:
            falhar["n"] -= 1; return 503, b"indisponivel"
        enviados.append((url, json.loads(corpo), cab)); return 200, b"ok"
    monkeypatch.setattr(client.mod, "_http", http)
    hk.publicar("servicos", "status_os", f"crm:{iid}:T-9", {"numero": "OS-2026-00001", "status": "planejada", "status_nome": "Planejada", "ref_externa": "T-9",
                "equipe": "Equipe A", "execucao_inicio": "2026-11-03T13:00:00Z"}, source_application="ordens")
    r = client.post("/api/sincronizar", headers=INT).json()
    assert r["falhas_de_envio"] == 1 and not enviados
    r = client.post("/api/sincronizar", headers=INT).json()
    assert r["status_enviados_ao_crm"] == 1
    url, ev, cab = enviados[0]
    assert ev["evento"] == "status_os" and ev["crm_ref"] == "T-9" and ev["numero_os"] == "OS-2026-00001" and ev["status"] == "planejada"
    assert cab["X-Assinatura"] == "sha256=" + hmac.new(b"s3gr3d0", json.dumps(ev, ensure_ascii=False).encode(), hashlib.sha256).hexdigest()
    assert client.post("/api/sincronizar", headers=INT).json()["status_enviados_ao_crm"] == 0  # já entregue
    sai = client.get(f"/api/integracoes/{iid}", headers=INT).json()["saidas"]
    assert len(sai) == 1 and sai[0]["tentativas"] == 2 and sai[0]["enviado_em"]
    # consulta pelo CRM
    s = client.get(f"/entrada/{iid}/status/T-9", headers={"X-Chave": chave}).json()
    assert s["status"] == "planejada" and s["equipe"] == "Equipe A"
    assert len(client.get(f"/entrada/{iid}/status", headers={"X-Chave": chave}).json()["itens"]) == 1


def test_busca_periodica(client, monkeypatch):
    iid, _ = nova(client)
    client.put(f"/api/integracoes/{iid}", json={"pull_url": "https://crm.exemplo/api/tickets", "pull_intervalo_min": 5, "pull_token": "tk"}, headers=INT)
    chamadas = []
    def http(metodo, url, corpo, cab, timeout=15):
        chamadas.append((metodo, url, cab.get("Authorization")))
        return 200, json.dumps({"results": [{"crm_ref": "B-1", "titulo": "Buscado"}]}).encode()
    monkeypatch.setattr(client.mod, "_http", http)
    st = client.post("/api/sync/agora", headers=INT).json()
    assert st["resultado"]["solicitacoes_buscadas"] == 1 and chamadas == [("GET", "https://crm.exemplo/api/tickets", "Bearer tk")]
    client.post("/api/sync/agora", headers=INT)  # dentro do intervalo: não busca de novo
    assert len(chamadas) == 1
    assert client.mod.hubkit.consultar("servicos", "solicitacao_externa", entity_key=f"crm:{iid}:B-1")[0]["payload"]["canal"] == "busca"
