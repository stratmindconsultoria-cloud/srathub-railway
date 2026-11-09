"""Testes da API. Executar na pasta do projeto:  python -m pytest -q"""
import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PMO = {"X-Usuario": "u-demo-pmo"}
ANA = {"X-Usuario": "u-demo-solic1"}
RAFAEL = {"X-Usuario": "u-demo-solic2"}
ENG = {"X-Usuario": "u-demo-eng"}
EXEC = {"X-Usuario": "u-demo-aprov"}


@pytest.fixture()
def client(tmp_path):
    os.environ["ORIGINACAO_DATA"] = str(tmp_path)
    import app as modulo
    importlib.reload(modulo)
    with TestClient(modulo.app) as c:
        yield c


def novo_projeto(pid, solicitante="u-demo-solic1", **extra):
    p = {
        "id": pid, "nome": "Projeto teste", "unidade": "Unidade Norte", "diretoria": "Engenharia",
        "estagio": "FSE", "status": "Ativo", "cicloId": "", "caminho": "regular",
        "capexEstimado": 1000, "capexAno": {}, "solicitanteId": solicitante,
        "fse": {"problema": "x"}, "gates": {"FSE": {"status": "rascunho"}},
        "hist": [{"acao": "Projeto criado", "por": "Ana", "para": "Projeto teste"}],
        "validacoes": [], "documentos": [],
    }
    p.update(extra)
    return p


def test_usuarios_e_sequencia(client):
    assert len(client.get("/api/usuarios").json()) == 7
    assert client.post("/api/sequencia", headers=PMO).json()["id"] == "ORI-000001"
    assert client.post("/api/sequencia", headers=PMO).json()["id"] == "ORI-000002"
    assert client.post("/api/sequencia").status_code == 401


def test_ciclo_somente_pmo(client):
    c = {"nome": "PQQ 2027–2031", "abertura": "2026-07-01", "corte": "2026-10-30", "inicio": 2027, "fim": 2031}
    assert client.put("/api/ciclos/c1", json=c, headers=ANA).status_code == 403
    assert client.put("/api/ciclos/c1", json=c, headers=PMO).status_code == 200
    assert client.put("/api/ciclos/c2", json={**c, "corte": "2026-01-01"}, headers=PMO).status_code == 400


def test_criar_editar_e_conflito(client):
    r = client.put("/api/projetos/ORI-000001", json=novo_projeto("ORI-000001"), headers=ANA)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["versao"] == 1 and p["fse"]["problema"] == "x" and len(p["hist"]) == 1

    p["capexAno"] = {"2027": 500, "2028": 700}
    p["hist"].append({"acao": "Alteração", "campo": "CAPEX 2027", "de": "", "para": 500})
    p2 = client.put("/api/projetos/ORI-000001", json=p, headers=ANA).json()
    assert p2["versao"] == 2
    assert p2["capexAno"] == {"2027": 500.0, "2028": 700.0}
    assert p2["hist"][-1]["para"] == 500

    # gravação com versão antiga -> conflito
    r = client.put("/api/projetos/ORI-000001", json=p, headers=ANA)
    assert r.status_code == 409
    assert r.json()["detail"]["atual"]["versao"] == 2


def test_historico_somente_acrescimo(client):
    p = client.put("/api/projetos/ORI-000001", json=novo_projeto("ORI-000001"), headers=ANA).json()
    p["hist"] = []  # tentativa de apagar histórico
    p2 = client.put("/api/projetos/ORI-000001", json=p, headers=ANA).json()
    assert len(p2["hist"]) == 1


def test_permissoes(client):
    client.put("/api/projetos/ORI-000001", json=novo_projeto("ORI-000001"), headers=ANA)
    p = client.get("/api/projetos/ORI-000001").json()
    assert client.put("/api/projetos/ORI-000001", json=p, headers=RAFAEL).status_code == 403
    assert client.put("/api/projetos/ORI-000001", json=p, headers={"X-Usuario": "u-demo-gestor2"}).status_code == 403
    assert client.put("/api/projetos/ORI-000001", json=p, headers={"X-Usuario": "u-demo-gestor1"}).status_code == 200
    avancado = novo_projeto("ORI-000002", estagio="FSP")
    assert client.put("/api/projetos/ORI-000002", json=avancado, headers=ANA).status_code == 403
    assert client.put("/api/projetos/ORI-000002", json=avancado, headers=PMO).status_code == 200


def test_documentos_e_arquivos(client):
    p = client.put("/api/projetos/ORI-000001", json=novo_projeto("ORI-000001"), headers=ANA).json()
    r = client.post("/api/projetos/ORI-000001/arquivos", headers=ANA,
                    files={"arquivo": ("estimativa.pdf", b"%PDF-1.4 teste", "application/pdf")})
    assert r.status_code == 200, r.text
    nome = r.json()["arquivo"]
    assert client.post("/api/projetos/ORI-000001/arquivos", headers=ANA,
                       files={"arquivo": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400
    p["documentos"] = [{"id": "d1", "tipo": "Estimativa", "nome": "estimativa.pdf", "arquivo": nome, "por": "Ana"}]
    p = client.put("/api/projetos/ORI-000001", json=p, headers=ANA).json()
    assert p["documentos"][0]["arquivo"] == nome
    assert client.get(f"/api/arquivos/{nome}").content.startswith(b"%PDF")
    p["documentos"] = []
    p = client.put("/api/projetos/ORI-000001", json=p, headers=ANA).json()
    assert p["documentos"] == []


def test_exportar_e_zerar(client):
    r = client.post("/api/exportar/xlsx", json={"linhas": [{"ID": "ORI-1", "CAPEX_Total_mil": 10}], "ciclos": []})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    client.put("/api/projetos/ORI-000001", json=novo_projeto("ORI-000001"), headers=ANA)
    assert client.post("/api/admin/zerar", headers=ANA).status_code == 403
    assert client.post("/api/admin/zerar", headers=PMO).status_code == 200
    assert client.get("/api/projetos").json() == []


def test_fluxo_pqq_lote_tap_pdf_e_fila_pmp(client):
    pid = "ORI-000001"
    ciclo = {"nome":"PQQ 2028–2032", "abertura":"2026-01-01", "corte":"2026-12-31", "inicio":2028, "fim":2032}
    assert client.put("/api/ciclos/c-2028", json=ciclo, headers=PMO).status_code == 200
    p = novo_projeto(pid, estagio="MADURO", gates={
        "FSE": {"status": "aprovado"}, "BC": {"status": "aprovado"},
        "FSP": {"status": "aprovado"}, "ELEG": {"status": "aprovado"}},
        cicloId="c-2028", pep="WBS.28.001",
        fsp={"escopo": "Escopo de referência", "cronograma": "2027 a 2028", "atividades": []})
    assert client.put(f"/api/projetos/{pid}", json=p, headers=PMO).status_code == 200

    r = client.post(f"/api/projetos/{pid}/pqq/enviar-aprovacao", json={}, headers=ENG)
    assert r.status_code == 200 and r.json()["pqq"]["status"] == "em_aprovacao_envio"
    assert client.post(f"/api/projetos/{pid}/pqq/decidir-envio", json={"decisao":"aprovar"}, headers=ENG).status_code == 403
    r = client.post(f"/api/projetos/{pid}/pqq/decidir-envio", json={"decisao":"aprovar", "aprovador_externo":"Aprovador externo falso"}, headers=EXEC)
    assert r.status_code == 200 and r.json()["pqq"]["status"] == "liberado_para_envio"
    assert r.json()["pqq"]["aprovacao_envio"]["decidido_por"] == "Aprovador Demo"

    r = client.post("/api/integracoes/pqq/envios", json={"projeto_ids":[pid]}, headers=PMO)
    assert r.status_code == 200 and r.json()["payload"]["tipo"] == "originacao_para_plano"
    assert client.get(f"/api/projetos/{pid}").json()["pqq"]["status"] == "enviado"
    assert r.json()["payload"]["projetos"][0]["id_mestre"] == pid
    enviado = r.json()["payload"]["projetos"][0]
    assert enviado["pep"] == "WBS.28.001"
    assert (enviado["ciclo_pqq"], enviado["ciclo_inicio"], enviado["ciclo_fim"]) == ("c-2028", 2028, 2032)
    assert r.json()["status"] == "publicado"

    retorno = {"schema_version":"1.1", "tipo":"pqq_to_origination", "lote_id":"PQQ-RET-TEST-001",
        "projetos":[{"id_mestre":pid,"pqq_id":"PQQ-123","ciclo_pqq":"c-2028","ciclo_inicio":2028,"ciclo_fim":2032,
                     "status":"aprovado","orcamento_total":100,
                     "curva_mensal":{"2028-01":50,"2028-02":50},"aprovado_por":"Diretoria","aprovado_em":"2026-10-15"}]}
    r = client.post("/api/integracoes/pqq/retornos", json=retorno, headers=PMO)
    assert r.status_code == 200 and r.json()["importados"] == 1
    assert client.post("/api/integracoes/pqq/retornos", json=retorno, headers=PMO).status_code == 409

    alterado = client.get(f"/api/projetos/{pid}").json()
    alterado["pqq"]["retorno"]["orcamento_total"] = 900
    r = client.put(f"/api/projetos/{pid}", json=alterado, headers=ENG)
    assert r.status_code == 200 and r.json()["pqq"]["retorno"]["orcamento_total"] == 100

    prop = {"escopo":"Escopo revisto pela Engenharia", "cronograma":"Execução entre 2028 e 2032",
            "inicio_previsto":"2028-01-01", "conclusao_compromissada":"2032-12-31",
            "premissas":"Janelas operacionais aprovadas pela unidade."}
    fora_da_curva = {**prop, "inicio_previsto":"2028-03-01"}
    assert client.put(f"/api/projetos/{pid}/tap/proposta", json=fora_da_curva, headers=ENG).status_code == 400
    assert client.put(f"/api/projetos/{pid}/tap/proposta", json=prop, headers=ENG).status_code == 200
    r = client.post(f"/api/projetos/{pid}/tap/enviar-aprovacao", json={}, headers=ENG)
    assert r.status_code == 200 and r.json()["tap"]["status"] == "em_aprovacao"
    r = client.post(f"/api/projetos/{pid}/tap/decidir", json={"decisao":"aprovar"}, headers=EXEC)
    assert r.status_code == 200 and r.json()["pmp"]["status"] == "publicado", r.json()["pmp"].get("erro_publicacao")
    assert r.json()["pqq"]["retorno"]["orcamento_total"] == 100
    assert r.json()["tap"]["proposta"]["escopo"] == prop["escopo"]
    tap_doc = next(d for d in r.json()["documentos"] if d["tipo"] == "TAP")
    pdf = client.get(f"/api/arquivos/{tap_doc['arquivo']}")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert [x["id"] for x in client.get("/api/integracoes/pmp/fila", headers=PMO).json()] == [pid]


def test_retorno_pqq_rejeita_curva_inconciliada(client):
    pid = "ORI-000001"
    ciclo = {"nome":"PQQ 2028–2032", "abertura":"2026-01-01", "corte":"2026-12-31", "inicio":2028, "fim":2032}
    client.put("/api/ciclos/c-2028", json=ciclo, headers=PMO)
    p = novo_projeto(pid, estagio="MADURO", cicloId="c-2028", gates={"ELEG":{"status":"aprovado"}})
    client.put(f"/api/projetos/{pid}", json=p, headers=PMO)
    client.post(f"/api/projetos/{pid}/pqq/enviar-aprovacao", json={}, headers=ENG)
    client.post(f"/api/projetos/{pid}/pqq/decidir-envio", json={"decisao":"aprovar"}, headers=EXEC)
    envio=client.post("/api/integracoes/pqq/envios", json={"projeto_ids":[pid]}, headers=PMO).json()
    retorno = {"lote_id":"PQQ-RET-BAD-001","projetos":[{"id_mestre":pid,"status":"aprovado","orcamento_total":100,
        "curva_mensal":{"2027-01":90}}]}
    r=client.post("/api/integracoes/pqq/retornos",json=retorno,headers=PMO)
    assert r.status_code == 200 and r.json()["importados"] == 0
    p=client.get(f"/api/projetos/{pid}").json()
    assert p["pqq"]["status"] == "enviado" and "tap" not in p


def test_retorno_pqq_rejeita_mes_fora_da_janela_do_ciclo(client):
    pid = "ORI-000001"
    ciclo = {"nome":"PQQ 2028–2032", "abertura":"2026-01-01", "corte":"2026-12-31", "inicio":2028, "fim":2032}
    client.put("/api/ciclos/c-2028", json=ciclo, headers=PMO)
    p = novo_projeto(pid, estagio="MADURO", cicloId="c-2028", gates={"ELEG":{"status":"aprovado"}})
    client.put(f"/api/projetos/{pid}", json=p, headers=PMO)
    client.post(f"/api/projetos/{pid}/pqq/enviar-aprovacao", json={}, headers=ENG)
    client.post(f"/api/projetos/{pid}/pqq/decidir-envio", json={"decisao":"aprovar"}, headers=EXEC)
    envio=client.post("/api/integracoes/pqq/envios", json={"projeto_ids":[pid]}, headers=PMO).json()
    retorno = {"lote_id":"PQQ-RET-OUT-OF-CYCLE","projetos":[{"id_mestre":pid,"pqq_id":"PQQ-123",
        "ciclo_pqq":"c-2028","ciclo_inicio":2028,"ciclo_fim":2032,"status":"aprovado","orcamento_total":100,
        "curva_mensal":{"2027-12":100}}]}
    r=client.post("/api/integracoes/pqq/retornos",json=retorno,headers=PMO)
    assert r.status_code == 200 and r.json()["importados"] == 0
    p=client.get(f"/api/projetos/{pid}").json()
    assert p["pqq"]["status"] == "enviado" and "tap" not in p


def test_envio_pqq_bloqueia_projeto_sem_ciclo(client):
    pid = "ORI-000001"
    p = novo_projeto(pid, estagio="MADURO", gates={"ELEG":{"status":"aprovado"}})
    client.put(f"/api/projetos/{pid}", json=p, headers=PMO)
    client.post(f"/api/projetos/{pid}/pqq/enviar-aprovacao", json={}, headers=ENG)
    client.post(f"/api/projetos/{pid}/pqq/decidir-envio", json={"decisao":"aprovar"}, headers=EXEC)
    r = client.post("/api/integracoes/pqq/envios", json={"projeto_ids":[pid]}, headers=PMO)
    assert r.status_code == 409
    assert client.get(f"/api/projetos/{pid}").json()["pqq"]["status"] == "liberado_para_envio"


def test_envio_pqq_nao_marca_enviado_se_data_core_falhar(client, monkeypatch):
    import app as modulo
    pid="ORI-000001"
    client.put("/api/ciclos/c1",json={"nome":"PQQ 2028–2032","abertura":"2026-01-01","corte":"2026-12-31","inicio":2028,"fim":2032},headers=PMO)
    p=novo_projeto(pid,estagio="MADURO",cicloId="c1",gates={"ELEG":{"status":"aprovado"}})
    client.put(f"/api/projetos/{pid}",json=p,headers=PMO)
    client.post(f"/api/projetos/{pid}/pqq/enviar-aprovacao",json={},headers=ENG)
    client.post(f"/api/projetos/{pid}/pqq/decidir-envio",json={"decisao":"aprovar"},headers=EXEC)
    def falha(*a, **k): raise RuntimeError("Data Core indisponível")
    monkeypatch.setattr(modulo.hubkit, "publicar", falha)
    r=client.post("/api/integracoes/pqq/envios",json={"projeto_ids":[pid]},headers=PMO)
    assert r.status_code==502
    assert client.get(f"/api/projetos/{pid}").json()["pqq"]["status"]=="liberado_para_envio"
    assert client.get("/api/integracoes/pqq/lotes",headers=PMO).json()==[]


def test_retorno_pqq_cancela_projeto_sem_abrir_tap(client):
    pid = "ORI-000004"
    ciclo = {"nome":"PQQ 2028–2032", "abertura":"2026-01-01", "corte":"2026-12-31", "inicio":2028, "fim":2032}
    client.put("/api/ciclos/c-2028", json=ciclo, headers=PMO)
    p = novo_projeto(pid, estagio="MADURO", cicloId="c-2028", gates={"ELEG":{"status":"aprovado"}})
    client.put(f"/api/projetos/{pid}", json=p, headers=PMO)
    client.post(f"/api/projetos/{pid}/pqq/enviar-aprovacao", json={}, headers=ENG)
    client.post(f"/api/projetos/{pid}/pqq/decidir-envio", json={"decisao":"aprovar"}, headers=EXEC)
    client.post("/api/integracoes/pqq/envios", json={"projeto_ids":[pid]}, headers=PMO)
    ret={"schema_version":"1.1","tipo":"pqq_to_origination","lote_id":"PQQ-RET-CANCEL-001",
         "projetos":[{"id_mestre":pid,"pqq_id":"PQQ-999","ciclo_pqq":"c-2028","ciclo_inicio":2028,"ciclo_fim":2032,
                      "status":"cancelado","orcamento_total":0,"curva_mensal":{},"aprovado_por":"PQQ"}]}
    result=client.post("/api/integracoes/pqq/retornos",json=ret,headers=PMO)
    assert result.status_code==200 and result.json()["importados"]==1
    project=client.get(f"/api/projetos/{pid}").json()
    assert project["status"]=="Cancelado"
    assert project["tap"]["status"]=="encerrado_por_cancelamento"


def test_retorno_sincronizado_pelo_data_core_e_tap_publicado(client):
    """Fluxo conectado: retorno publicado pelo plano no Data Core → Originação recebe → TAP → PMP."""
    import app as modulo
    pid = "ORI-000010"
    client.put("/api/ciclos/c-2028", json={"nome":"PPI 2028–2032","abertura":"2026-01-01","corte":"2026-12-31","inicio":2028,"fim":2032}, headers=PMO)
    p = novo_projeto(pid, estagio="MADURO", cicloId="c-2028", gates={"ELEG":{"status":"aprovado"}}, fsp={"escopo":"E","cronograma":"C","atividades":[]})
    client.put(f"/api/projetos/{pid}", json=p, headers=PMO)
    client.post(f"/api/projetos/{pid}/pqq/enviar-aprovacao", json={}, headers=ENG)
    client.post(f"/api/projetos/{pid}/pqq/decidir-envio", json={"decisao":"aprovar"}, headers=EXEC)
    assert client.post("/api/integracoes/pqq/envios", json={"projeto_ids":[pid]}, headers=PMO).status_code == 200
    entrada = modulo.hubkit.consultar("portfolio", "entrada_originacao", entity_key=pid)
    assert entrada and entrada[0]["payload"]["projeto"]["id_mestre"] == pid
    modulo.hubkit.publicar("portfolio", "retorno_originacao", pid, {"return_batch_id":"RET-X","status":"aprovado","plano_id":"PPI-0001",
        "ciclo_id":"c-2028","ciclo_inicio":2028,"ciclo_fim":2032,"orcamento_total":100,"curva_mensal":{"2028-01":40,"2028-02":60},
        "aprovado_por":"Comitê","aprovado_em":"2026-10-20"}, source_application="plano")
    assert [x["id_mestre"] for x in client.get("/api/integracoes/pqq/retornos/pendentes", headers=PMO).json()] == [pid]
    r = client.post("/api/integracoes/pqq/retornos/sincronizar", headers=PMO)
    assert r.status_code == 200 and r.json()["importados"] == 1
    assert client.get("/api/integracoes/pqq/retornos/pendentes", headers=PMO).json() == []
    assert client.post("/api/integracoes/pqq/retornos/sincronizar", headers=PMO).json()["importados"] == 0
    prop = {"escopo":"Escopo","cronograma":"Cron","inicio_previsto":"2028-01-01","conclusao_compromissada":"2028-12-31"}
    assert client.put(f"/api/projetos/{pid}/tap/proposta", json=prop, headers=ENG).status_code == 200
    client.post(f"/api/projetos/{pid}/tap/enviar-aprovacao", json={}, headers=ENG)
    assert client.post(f"/api/projetos/{pid}/tap/decidir", json={"decisao":"aprovar"}, headers=EXEC).json()["pmp"]["status"] == "publicado"
    tap = modulo.hubkit.consultar("pmp", "tap_aprovado", entity_key=pid)[0]["payload"]
    assert tap["orcamento_total_aprovado"] == 100 and tap["plano_id"] == "PPI-0001" and tap["tap_pdf"]["base64"]


def test_identidade_do_hub_sem_simulacao_e_master_teste(client, monkeypatch):
    import hashlib, hmac, time, app as modulo
    monkeypatch.setattr(modulo.hubkit, "TOKEN", "segredo-teste")
    def cab(uid, email, papel, ambiente="producao", master=False):
        ts = str(int(time.time()))
        sig = hmac.new(b"segredo-teste", f"{uid}|{email}|{papel}|{ts}".encode(), hashlib.sha256).hexdigest()
        h = {"x-hub-user-id": uid, "x-hub-user-email": email, "x-hub-user-name": "Pessoa%20Teste", "x-hub-user-role": papel, "x-hub-ts": ts,
             "x-hub-signature": sig, "x-hub-ambiente": ambiente}
        if master:
            h["x-hub-user-master"] = hmac.new(b"segredo-teste", f"master|{uid}|{ts}".encode(), hashlib.sha256).hexdigest()
        return h
    dono = cab("dono", "dono@exemplo.org", "dono")
    s = client.get("/api/sessao", headers=dono).json()
    assert s["modo"] == "hub" and s["usuario"]["perfil"] == "pmo" and s["usuario"]["nome"] == "Pessoa Teste"
    # no hub não existe "agir como": o X-Usuario é ignorado, e não há usuários de demonstração na lista
    s = client.get("/api/sessao", headers={**dono, "X-Usuario": "u-demo-solic1"}).json()
    assert not s["simulando"] and s["usuario"]["id"] == "h-dono"
    comum = cab("u-123", "ana@exemplo.org", "usuario")
    s = client.get("/api/sessao", headers={**comum, "X-Usuario": "u-demo-pmo"}).json()
    assert s["usuario"]["perfil"] == "solicitante" and not s["simulando"]
    # MASTER TESTE: só vale em ambiente de teste e com a assinatura do hub
    m = client.get("/api/sessao", headers=cab("u-777", "m@exemplo.org", "usuario", "teste", master=True)).json()
    assert m["master"] and m["usuario"]["perfil"] == "pmo" and "MASTER TESTE" in m["usuario"]["nome"] and m["ambiente"] == "teste"
    assert not client.get("/api/sessao", headers=cab("u-778", "p@exemplo.org", "usuario", "producao", master=True)).json()["master"]
    forjado = {**cab("u-779", "f@exemplo.org", "usuario", "teste"), "x-hub-user-master": "0" * 64}
    assert not client.get("/api/sessao", headers=forjado).json()["master"]
    # assinatura falsa é ignorada (sem identidade, modo isolado exige X-Usuario)
    falso = {**comum, "x-hub-signature": "0" * 64}
    assert client.post("/api/sequencia", headers=falso).status_code == 401
    # PMO promove o usuário
    assert client.put("/api/usuarios/h-u-123", json={"perfil": "validador", "unidade": "Unidade Sul"}, headers=dono).status_code == 200
    assert client.get("/api/sessao", headers=comum).json()["usuario"]["perfil"] == "validador"
    assert client.put("/api/usuarios/h-u-123", json={"perfil": "pmo"}, headers=comum).status_code == 403


def test_meta_rotulos_e_tap_com_vocabulario(client, tmp_path):
    import app as modulo
    m = client.get("/api/meta").json()
    assert m["modo"] == "isolado" and m["organizacao"]["unidades"][0]["nome"] == "Unidade Norte"
    assert m["rotulos"]["PQQ"] == "PPI" and m["rotulos"]["Gerente Executivo"] == "Aprovador Executivo"
    assert modulo.rotular("Ciclo PQQ e PEP do PQQ") == "Ciclo PPI e Código ERP do PPI"
    assert modulo.rotular("PQQs") == "PQQs"


def test_sincronizacao_automatica_e_pendencias_da_agenda(client):
    """Fluxo automático: o retorno do plano chega sem clique e as pendências vão para a agenda do ecossistema."""
    import app as modulo
    pid = "ORI-000020"
    client.put("/api/ciclos/c-2028", json={"nome":"PPI 2028–2032","abertura":"2026-01-01","corte":"2026-12-31","inicio":2028,"fim":2032}, headers=PMO)
    p = novo_projeto(pid, estagio="MADURO", cicloId="c-2028", gates={"ELEG":{"status":"aprovado"}}, fsp={"escopo":"E","cronograma":"C","atividades":[]})
    client.put(f"/api/projetos/{pid}", json=p, headers=PMO)
    client.post(f"/api/projetos/{pid}/pqq/enviar-aprovacao", json={}, headers=ENG)
    st = client.post("/api/sync/agora", headers=PMO).json()
    assert st["ok"], st
    pend = {x["entity_key"]: x["payload"] for x in modulo.hubkit.consultar("agenda", "pendencia")}
    assert pend[f"originacao:envio:{pid}"]["status"] == "aberta" and "gerente_executivo" in pend[f"originacao:envio:{pid}"]["perfis"]
    client.post(f"/api/projetos/{pid}/pqq/decidir-envio", json={"decisao":"aprovar"}, headers=EXEC)
    client.post("/api/integracoes/pqq/envios", json={"projeto_ids":[pid]}, headers=PMO)
    modulo.hubkit.publicar("portfolio", "retorno_originacao", pid, {"return_batch_id":"RET-Y","status":"aprovado","plano_id":"PPI-0002",
        "ciclo_id":"c-2028","ciclo_inicio":2028,"ciclo_fim":2032,"orcamento_total":100,"curva_mensal":{"2028-01":40,"2028-02":60},
        "aprovado_por":"Comitê","aprovado_em":"2026-10-20"}, source_application="plano")
    st = client.post("/api/sync/agora", headers=PMO).json()
    assert st["ok"] and st["resultado"]["retornos_do_plano"] == 1
    pend = {x["entity_key"]: x["payload"] for x in modulo.hubkit.consultar("agenda", "pendencia")}
    assert pend[f"originacao:envio:{pid}"]["status"] == "resolvida" and pend[f"originacao:tap:{pid}"]["status"] == "aberta"


def test_segregacao_quem_envia_nao_decide(client):
    """PMO que enviou o projeto para aprovação de entrada no plano não decide o próprio envio; o executivo sim."""
    pid = "ORI-000077"
    client.put("/api/ciclos/c-2029", json={"nome":"PPI 2029–2033","abertura":"2026-01-01","corte":"2026-12-31","inicio":2029,"fim":2033}, headers=PMO)
    p = novo_projeto(pid, estagio="MADURO", cicloId="c-2029", gates={"ELEG":{"status":"aprovado"}}, fsp={"escopo":"E","cronograma":"C","atividades":[]})
    client.put(f"/api/projetos/{pid}", json=p, headers=PMO)
    assert client.post(f"/api/projetos/{pid}/pqq/enviar-aprovacao", json={}, headers=PMO).status_code == 200
    r = client.post(f"/api/projetos/{pid}/pqq/decidir-envio", json={"decisao":"aprovar"}, headers=PMO)
    assert r.status_code == 403 and "próprio pedido" in r.json()["detail"]
    assert client.post(f"/api/projetos/{pid}/pqq/decidir-envio", json={"decisao":"aprovar"}, headers=EXEC).status_code == 200
