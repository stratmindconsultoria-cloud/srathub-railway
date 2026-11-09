# Originação de Projetos

Acompanha o projeto **da demanda até o Termo de Abertura (TAP)**, num registro único que evolui por etapas e gates:

```
Solicitação → Ficha de Demanda → Business Case → Ficha de Projeto → Elegibilidade → Maduro
   → aprovação executiva → envio ao Plano de Investimentos → retorno (aprovado / devolvido / cancelado)
   → revisão do TAP pela Engenharia → aprovação executiva → PDF do TAP → carteira da PMP
```

- **Central (torre de controle):** linha de maturidade, quantidade e valores por etapa, maturidade para o ciclo, CAPEX por unidade.
- **Portfólio, pendências por perfil, ciclos de planejamento, solicitações de estudo** (página simplificada para solicitantes em `/solicitante.html`).
- **Rota extraordinária** para demandas que não podem esperar o ciclo.
- **TAP em PDF** com marca, cores e termos da organização; orçamento e curva vêm do plano aprovado e ficam bloqueados.
- **Exportação .xlsx** da carteira.

## Fluxo automático (no hub)
Os retornos do plano chegam sozinhos (ao abrir e a cada 2 minutos) e TAPs aprovados que ficaram na fila são republicados para a PMP. A importação de retorno por arquivo JSON fica recolhida como **contingência**. O que depende de alguém (análise de solicitações, validação de gates, aprovação de envio, preparar/aprovar o TAP) é publicado em `agenda/pendencia` e aparece na StratAgenda. O indicador "Sincronizado há X min" fica no menu lateral.

## Perfis
| Perfil | Pode |
|---|---|
| Solicitante | cria e acompanha os próprios projetos |
| Engenharia / Analista | preenche Business Case e Ficha de Projeto, revisa o TAP |
| Gestor da unidade | aprova o Business Case da própria unidade |
| Aprovador Executivo | aprova o envio ao plano e o TAP |
| Administrador PMO | tudo, inclusive integrações e perfis |

No hub, quem entra pela primeira vez vira Solicitante (administradores do hub entram como PMO); o PMO ajusta em **Usuários e perfis**. Para testar outro perfil, crie no hub um usuário comum com esse perfil (em ambiente de teste, o MASTER TESTE executa qualquer papel).

## Configuração (`config/originacao.json`, opcional)
Nomes das etapas, troca de termos exibidos (`rotulos`), modelos de EAP e a simulação de perfis. Veja `config/originacao.exemplo.json`. Termos como "PPI", "Código ERP" e "Aprovador Executivo" vêm do vocabulário da organização no hub.

## Endereço e dados
Isolada: `http://127.0.0.1:8780` · dados em `dados/` (variável `ORIGINACAO_DATA`).

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
