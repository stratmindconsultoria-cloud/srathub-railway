# PMP · Carteira de Projetos

Gestão dos projetos **em execução**: orçado × realizado × tendência, nas dimensões **econômica** (competência), **financeira** (caixa) e **física** (% acumulado).

- **Carteira:** indicadores do ano, curva S, filtros por unidade, área e situação.
- **Projeto:** visão geral (curvas mensais e gráficos), tendência da rodada, mudanças, atas e ações, comentários, documentos (inclusive o PDF do TAP) e histórico entre rodadas.
- **Rodadas de tendência:** o PMO abre a rodada do mês; meses fechados usam o realizado; o gerente ajusta os meses seguintes e os anos futuros. Desvios acima da tolerância contra a referência (última tendência oficial ou orçado) precisam ser **classificados por categoria** (a soma explica o desvio) e justificados. O PMO aprova ou devolve e fecha a rodada. Projetos sem envio mantêm a referência e ficam marcados como "sem atualização".
- **Mudanças** (proposta → análise → aprovada/rejeitada → implementada) e **atas com ações** de acompanhamento.
- **Importações por modelos genéricos:** cadastro, curvas mensais (orçado, realizado, compromisso) e orçado de anos futuros. Qualquer sistema de origem (ERP, cronogramas, planilhas) entra convertendo para estes modelos.
- **Exportação** da tendência em .xlsx.

## Integrações
- **Recebe** o TAP aprovado da Originação (cria o projeto com orçamento, curva e PDF) e o orçamento aprovado do Plano (atualiza o orçado da carteira; projeto cortado vira cancelado). A sincronização roda ao abrir a ferramenta e em **Integrações**.
- **Publica**, ao fechar cada rodada, a tendência oficial para o **Analisador** (`forecast/rodada`) e a execução por projeto para o **Plano** (`portfolio/execucao`).

## Fluxo automático (no hub)
TAPs aprovados, orçamentos do plano, **rascunhos de ata** criados por reuniões de projeto na StratAgenda e **ordens de serviço** vinculadas aos projetos entram sozinhos (ao abrir e a cada 2 minutos). Rodada aberta por gerente, aprovações do PMO, mudanças, ações e atas em rascunho são publicadas em `agenda/pendencia`. **Campos extras** de projeto configuráveis em `config/pmp.json` (`campos_extras`).

## Perfis
| Perfil | Pode |
|---|---|
| PMO | tudo: rodadas, aprovação de tendências, mudanças, importações e integrações |
| Gerente de projeto | tendência, mudanças, atas e documentos dos projetos da própria unidade |
| Leitura | consulta |

## Configuração (`config/pmp.json`, opcional)
Categorias de desvio por dimensão, tolerâncias, etapas, tipos de mudança e de ata. Veja `config/pmp.exemplo.json`.

## Endereço e dados
Isolada: `http://127.0.0.1:8830` · dados em `dados/` (variável `PMP_DADOS`).

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
