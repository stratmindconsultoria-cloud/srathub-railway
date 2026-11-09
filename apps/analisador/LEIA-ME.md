# Analisador de Tendências

Análise comparativa entre **rodadas de tendência** da carteira, para entender o que se moveu e se a tendência é confiável.

- **Movimento:** ponte entre rodadas e catálogo de movimentos, com as categorias de desvio informadas na PMP.
- **Trajetória:** matriz por rodada × ano (plurianual), padrões de erosão e "serrote", credibilidade por unidade.
- **Exequibilidade:** ritmo necessário × ritmo realizado, cobertura por compromissos, projetos sem caminho para virar caixa.
- **Qualidade:** consistência dos meses fechados entre rodadas e alertas da base.
- **Visão por dimensão:** geral, econômico, financeiro, físico e por projeto, com filtro por unidade, área, gerente e grupo.

## Fonte dos dados
Não usa planilha: recebe as rodadas **publicadas pela PMP** ao fechar cada rodada.
- **No hub:** a página inicial lista as rodadas novas no Data Core; **Receber** grava o snapshot (reenvio de uma rodada substitui a anterior).
- **Isolada:** importe o JSON da rodada baixado na PMP (Rodadas › botão **JSON**).

As categorias de desvio da PMP são traduzidas para as categorias de análise em `config/analisador.json` (`mapa_categorias`); categorias desconhecidas entram como "Outros". Veja `config/analisador.exemplo.json`.

## Permissões
Consulta liberada a quem tem acesso à ferramenta no hub; receber, importar e excluir rodadas é de administradores do hub.

## Endereço e dados
Isolada: `http://127.0.0.1:8840` · dados em `dados/` (variável `ANALISADOR_DADOS`). Gráficos com Chart.js servido localmente (funciona sem internet).

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

## Fluxo automático (no hub)
Toda rodada fechada na PMP entra sozinha (ao abrir e a cada 2 minutos) — a tendência vem da PMP, nunca de planilha. A importação por arquivo JSON aparece só como **contingência** (recolhida) no hub e como entrada principal apenas no modo isolado. Se a PMP estiver com uma rodada em andamento, a tela avisa.
