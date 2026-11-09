# Plano de Investimentos

Ciclo de planejamento **plurianual** do portfólio (ex.: 5 anos), do levantamento à aprovação do comitê:

```
Preparação: base do ciclo (Originação + carteira em execução + planilha)  ── congela ──▶
Pleitos: rodadas com alterações justificadas, revisadas pelo PMO  ──▶
Comitê: cenários (manter / cortar / postergar), metas de corte, comparação e ponte  ── congela e aprova ──▶
Aprovado: plano oficial publicado (retorno à Originação e orçamento para a PMP)
```

- **Base imutável** depois da preparação; toda mudança fica no log com rodada, autor e justificativa.
- **Cenários congelados** para comparar lado a lado; aprovação só de cenário congelado.
- **Comparação e ponte** A → B (novos, aumentos, reduções, cortes, postergações) por unidade, com **metas de corte**.
- **Painel:** totais por ano, unidade, área e origem; **VPL** pela taxa de desconto do ciclo; valores reais pelos **índices de inflação**.
- **Risco de execução (Monte Carlo):** reamostra a execução histórica (realizado ÷ orçado) publicada pela PMP e estima a probabilidade de executar o planejado.
- **Planilhas:** modelo de base, importação na preparação e exportação de todas as camadas e do log.

## Fluxo automático (no hub)
Novos projetos da Originação e a execução da PMP entram sozinhos no ciclo aberto (ao abrir e a cada 2 minutos); a carga da base por planilha fica recolhida como **contingência**. Fase do ciclo, pleitos a revisar e cenários a aprovar são publicados em `agenda/pendencia` e aparecem na StratAgenda.

## Perfis
| Perfil | Pode |
|---|---|
| PMO | tudo: ciclos, base, revisão de pleitos, cenários, integrações |
| Comitê / Diretoria | metas de corte e aprovação do cenário |
| Gestor de unidade | pleitos da própria unidade |
| Leitura | consulta |

## Configuração (`config/plano.json`, opcional)
Categorias de projeto, número de simulações, troca de termos. Sigla e nome do plano (ex.: "PPI"), horizonte e taxa de desconto vêm do perfil da organização.

## Endereço e dados
Isolada: `http://127.0.0.1:8820` · dados em `dados/` (variável `PLANO_DADOS`).

## Instalar

**No STRAT·HUB (recomendado):** copie esta pasta para `apps\<id>\` do hub ou use **＋ Plugar app** com o `.zip`. O hub lê o `hub.manifest.json`, cria o ambiente Python da ferramenta, instala o `requirements.txt` e sobe sob demanda. Identidade, perfil da organização e integração com as outras ferramentas passam a funcionar sozinhos.

**Sozinha (modo isolado):**
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      (Linux/Mac: .venv/bin/pip)
.venv\Scripts\python app.py
```
Abra o endereço indicado abaixo. No modo isolado há usuários de demonstração (um por perfil, escolhidos no canto da tela), a organização vem de `config/organizacao.json` (copie de `organizacao.exemplo.json`) e a integração usa um Data Core local em `dados/datacore_local.json`.

**No HUB PMO:** o mesmo `hub.manifest.json` é aceito (porta fixa e `--root-path`); lá a ferramenta roda em modo isolado.

## Dados e backup
Tudo fica em `dados/` (SQLite + anexos). Atualizar a ferramenta preserva essa pasta. Para outro local, defina a variável indicada abaixo.

## Testes
```
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
```

## Integração
Contrato completo entre as ferramentas em `docs/CONTRATO_INTEGRACAO.md`.
