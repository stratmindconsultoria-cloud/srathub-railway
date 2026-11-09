# Ordens de Serviço · 1.1

Execução operacional: cada **OS é um mini projeto** criado a partir de um **modelo**, com equipe, **um executor**, data de execução, checklist com fotos e respostas, materiais, horas automáticas e custo previsto × realizado.

- **Modelos de OS** (tela própria, para gestor e planejador): nome, tipo, escopo, prioridade padrão, **SLA por prioridade**, duração típica, **composição sugerida** (ex.: 1 eletricista executor + 1 auxiliar), **checklist** (ordem, obrigatório, **foto obrigatória com mínimo**, tipo de resposta: marcar / sim-não / número com unidade / texto), **fotos do antes e do depois**, materiais padrão, **exige aprovação** do supervisor, **exige aceite do cliente**, orientações de segurança/EPI e anexos fixos. Criar, duplicar, editar, desativar e pré-visualizar como o técnico verá. **Modelo padrão por origem** (`crm`, `crm:corretiva`, `orcamentos:visita`). Cada OS guarda a **cópia** do modelo: editar o modelo não muda as OS já abertas.
- **Implantação × dia a dia:** os modelos do arquivo de configuração entram **uma vez** (só adiciona); depois tudo é pela tela. **Importar** (só adicionar / atualizar com confirmação, versão anterior no histórico) e **Exportar** modelos. No hub, os modelos vão junto na **exportação do ecossistema** (`config/modelos-os.json`) e implantam a próxima empresa.
- **Equipe e executor:** a OS tem quem vai (1 a 50 pessoas) e **exatamente um executor**, cuja **função** pode executar (Funções: “pode ser executor”; ex.: Auxiliar não pode). Uma OS não pode ter só auxiliares. Os demais participantes veem a OS (escopo, local, horário, colegas) mas só o executor registra a execução.
- **Fluxo:** Aberta → Planejada → Em execução ⇄ Pausada → Concluída → **Encerrada (aprovação do supervisor)** ou devolvida ao executor com motivo. Modelos sem aprovação encerram ao concluir.
- **Modo execução (PWA, celular):** “Minhas OS de hoje”, **como chegar** e **ligar**, fotos do antes, **iniciar (cronômetro que aponta as horas sozinho para todos da OS)**, checklist com respostas e fotos, materiais (*usei tudo / usei X / devolvi*), **pausar** com motivo pronto, **concluir** com relato, fotos do depois e **aceite do cliente** (nome + confirmação). **Funciona sem sinal**: as ações ficam numa fila no aparelho e vão na ordem quando a conexão volta, com a hora real do toque. Instala como app (precisa de HTTPS) e tem **“Gerar atalho”** (link e QR).
- **Quadro das equipes:** semana e dia; **arrastar** para outro dia/horário (trocar de equipe abre o planejamento já preenchido), **redimensionar** a duração na vista do dia, coluna **A planejar**, **Desfazer**. Proteções: conflito destacado, SLA estourado pede confirmação, feriado segue o fluxo de ciência, só OS planejadas/pausadas se movem. Só planejador, supervisor e gestor movem; o executor é avisado pela agenda; histórico de/para. Funciona por toque.
- **Feriados:** calendário do hub (nacionais, móveis, locais da localidade da equipe). Equipe toda em escala → normal; senão o planejador confirma ciência e a OS fica **aguardando o de acordo do gestor de serviços** (sem de acordo, volta a planejar). Escala vem do cadastro da pessoa no hub (ou da marcação no membro sem login).
- **Regime operacional:** a execução entra **travada** na StratAgenda de cada participante (“OS — definido pelo planejamento”), conta na carga do dia, muda a cada replanejamento e marca conflito nos compromissos administrativos do técnico.
- **Custos só para a gestão:** custo unitário de material, custo/hora dos membros, custo previsto/real e custos do painel só para planejador, supervisor e gestor — **filtrado no servidor** (técnico e solicitante não recebem).
- **SLA**, **materiais** (catálogo; *a separar → separado → consumido / devolvido*), **painel**, kanban, lista e ficha completa continuam.

Fora do escopo: estoque e compras, roteirização e assinatura digital.

## Integrações (Data Core do hub) — opcionais
- **Recebe** `servicos/solicitacao_externa` do **Conector CRM** (modelo pelo padrão da origem). **Publica** `servicos/status_os`, `servicos/os_resumo` (PMP) e `agenda/pendencia`: execução por participante (*operacional*), OS a planejar (*tarefa*), SLA em risco, aprovação de encerramento e de acordo de feriado (*ações* com botões que a agenda executa).
- A interface pergunta ao hub o que está instalado e esconde o que depende de ferramenta ausente (projeto da PMP, CRM).

## Perfis
| Perfil | Pode |
|---|---|
| Gestor de serviços | tudo: modelos, funções, aprovar/devolver, de acordo de feriado, reabrir, usuários |
| Supervisor | planejar e replanejar, aprovar ou devolver o encerramento, de acordo de feriado |
| Planejador | abrir, planejar e replanejar (quadro), modelos de OS, materiais, equipes |
| Técnico | executar as OS em que é **executor**; ver as OS de que participa (sem custos) |
| Solicitante | abrir e acompanhar as próprias OS (sem custos) |

## Configuração (`config/ordens.json`, opcional — implantação)
Modelos iniciais (`tipos`), funções, modelo padrão por origem, prefixo da numeração, limite de "em risco", fuso horário e rótulos dos perfis. Veja `config/ordens.exemplo.json`.

## Endereço e dados
Isolada: `http://127.0.0.1:8850` · dados em `dados/` (variável `ORDENS_DADOS`).

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
