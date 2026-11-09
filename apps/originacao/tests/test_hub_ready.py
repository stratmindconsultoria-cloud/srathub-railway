import os
import importlib
from pathlib import Path


def test_fluxo_solicitacao_para_central(tmp_path, monkeypatch):
    monkeypatch.setenv('ORIGINACAO_DATA', str(tmp_path / 'data'))
    import app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as c:
        assert c.get('/health').json()['hub_ready'] is True
        payload = {
            'conteudo': {
                'necessidade_descricao': 'Nova capacidade operacional',
                'contexto_evidencias': 'Contexto teste',
                'impacto_atual': 'Impacto atual',
                'impacto_nao_implementacao': 'Impacto futuro',
                'beneficios_descricao': 'Benefícios',
                'beneficios_categorias': 'Disponibilidade',
                'alinhamento_negocio': 'Objetivo do negócio',
                'natureza_intervencao': 'Ampliação / melhoria',
            },
            'capex_respostas': {'C01': 'Sim', 'C02': 'Sim', 'C07': 'Não', 'C08': 'Não'},
            'unidade': 'Unidade Norte',
            'diretoria': 'Engenharia',
        }
        r = c.post('/api/solicitacoes', json=payload, headers={'X-Usuario': 'u-demo-solic1'})
        assert r.status_code == 200
        rid = r.json()['request_uuid']
        a = c.post(f'/api/solicitacoes/{rid}/approve', json={}, headers={'X-Usuario': 'u-demo-pmo'})
        assert a.status_code == 200
        pid = a.json()['project_id']
        assert pid.startswith('PMO-')
        p = c.get(f'/api/projetos/{pid}').json()
        assert p['estagio'] == 'BC'
        assert p['gates']['FSE']['status'] == 'aprovado'
        again = c.post(f'/api/solicitacoes/{rid}/approve', json={}, headers={'X-Usuario': 'u-demo-pmo'}).json()
        assert again['idempotent'] is True
