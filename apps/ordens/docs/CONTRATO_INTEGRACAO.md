# Contrato de integração entre as ferramentas (Data Core do hub)

As ferramentas são **independentes**: cada uma tem seu banco, sua interface e suas regras.
Elas se conectam **somente** pelo Data Core do hub (`/api/data-core/records`), que guarda cada registro
com origem, usuário, versão e histórico. Nenhuma ferramenta lê o banco de outra.

Registro = `(domain, entity_type, entity_key, period)` + `payload` JSON.
Leitura/gravação pelo servidor de cada app com `hubkit.py` (cabeçalho `X-Hub-App-Token`).
Rodando fora do hub, cada app usa um Data Core local (`dados/datacore_local.json`) e aceita importação/exportação por arquivo JSON.

## Fluxo do ciclo de vida

```
Originação ──entrada_originacao──▶ Plano de Investimentos ──retorno_originacao──▶ Originação
     │                                   │   ▲                                      │
     │                                   │   └────────── execucao ───────────┐      │ (TAP aprovado)
     │                                   └──plano_aprovado──▶ PMP (carteira) ◀──────┘ tap_aprovado
     │                                                          │
     │                                                          └──rodada──▶ Analisador de Tendências
```

| domain / entity_type | De → Para | entity_key | Conteúdo (payload) |
|---|---|---|---|
| `portfolio/entrada_originacao` | Originação → Plano | id mestre (`ORI-000001`) | `lote_id`, `enviado_em`, `projeto{id_mestre, pep (código ERP), nome, unidade, diretoria (área), ciclo_pqq, ciclo_inicio, ciclo_fim, solicitante, responsavel, fse, business_case, fsp{…, distribuicaoMensal}, distribuicao_anual_referencia}` |
| `portfolio/retorno_originacao` | Plano → Originação | id mestre | `return_batch_id`, `status` (aprovado · devolvido · cancelado), `plano_id`, `plano_revisao`, `ciclo_id`, `ciclo_inicio`, `ciclo_fim`, `orcamento_total`, `curva_mensal{AAAA-MM: valor}`, `aprovado_por`, `aprovado_em`, `comentario` |
| `pmp/tap_aprovado` | Originação → PMP | id mestre | projeto, escopo, cronograma, datas, orçamento e curva aprovados, `plano_id`, `tap_pdf{nome, base64}` |
| `pmp/plano_aprovado` | Plano → PMP | código do projeto na carteira | `plano_id`, `codigo`, `nome`, `unidade`, `area`, `ciclo_id`, `ciclo_inicio`, `ciclo_fim`, `status`, `orcamento_total`, `valores_ano{AAAA: valor}`, `curva_mensal{AAAA-MM: valor}` |
| `portfolio/execucao` | PMP → Plano | código do projeto | `nome`, `unidade`, `area`, `rodada`, `realizado_ano{AAAA: valor}`, `tendencia_ano{AAAA: valor}`, `orcado_ano{AAAA: valor}` |
| `forecast/rodada` | PMP → Analisador | id da rodada (`2026-09`) | `rodada`, `nome`, `ano`, `mes_referencia`, `fechada_em`, `projetos[]` com orçado, realizado e tendência mensais (econômico, financeiro e físico), desvios e justificativas |

Regras gerais

- **Idempotência**: a chave identifica o objeto; reenviar substitui (versão +1) e entra no histórico.
- **Quem recebe controla o que já processou** (pela versão do registro), então nada é importado duas vezes.
- **Termos exibidos** (ex.: "PPI", "Código ERP", "Aprovador Executivo") vêm do perfil da organização no hub; o contrato usa nomes neutros.

## Agenda do ecossistema (todas as ferramentas → StratAgenda)

| domain / entity_type | De → Para | entity_key | Conteúdo (payload) |
|---|---|---|---|
| `agenda/pendencia` | qualquer ferramenta → StratAgenda | `<app>:<chave interna>` (ex.: `pmp:rodada:2026-09:Gerente A`, `ordens:exec:OS-2026-00012:h-<id hub>`) | `status` (aberta · resolvida), **`natureza`** (tarefa · acao · operacional), `tipo` (rodada, aprovacao, validacao, preparar, ata, execucao, planejamento, alerta, integracao…), `titulo`, `descricao`, `prioridade` (alta · normal · baixa), `prazo` ou `inicio`/`fim` (ISO), `esforco_h` (tarefas), `responsavel` (nome ou e-mail), `perfis[]`, **`destinatarios[]`** (`{id_hub, email, nome}`), `unidades[]`, `projeto`, `equipe`, `link` (rota dentro da ferramenta), **`acoes[]`**, `executor` (operacional), `origem_app`, `origem_nome` |
| `agenda/compromisso` | StratAgenda → outros calendários (Conector Microsoft) | `stratagenda:<id do bloco>` | `status` (ativo · removido), `pessoa` `{id_hub, email, nome}`, `titulo`, `inicio`/`fim` (ISO com fuso), `travado` (OS), `origem_app` — de ontem até 60 dias; compromissos vindos de fora não são publicados |
| `agenda/ocupado` | Conector Microsoft → StratAgenda | `microsoft:<id_hub>:<id do evento>` | `status` (ativo · removido), `pessoa`, `titulo` (“Ocupado” se privado), `inicio`/`fim` (UTC), `origem_nome` (Outlook) — entra travado na agenda |
| `pmp/ata_rascunho` | StratAgenda → PMP | `agenda:<id da tarefa>` | `titulo`, `data` (AAAA-MM-DDTHH:MM), `codigo_projeto`, `participantes[]`, `pauta`, `criado_por`, `cancelada` |

- Cada ferramenta publica **só quando a pendência muda** e publica como `resolvida` o que deixou de existir.
- **natureza** decide o que a agenda faz (a origem é dona do quê/quem/prazo/feito; a agenda, do quando e de quanto tempo):
  - `tarefa` (consome tempo: preparar TAP, revisar pleitos, enviar tendência…) → entra sozinha no backlog do `responsavel`; sem responsável e com um só destinatário, no dele; com vários destinatários, aparece para todos e **quem assumir leva**. Resolvida na origem → concluída na agenda; concluída na agenda com a origem aberta → marca **“pendente na origem”**.
  - `acao` (aprovar, devolver, decidir) → notificação com modal; os botões de `acoes[]` são executados pela agenda **em nome da pessoa**, chamando `/apps/<origem_app><caminho>` pelo hub (a origem confere o perfil e registra a auditoria) ou “Abrir na ferramenta”.
  - `operacional` (execução de OS) → bloco **travado** (“OS — definido pelo planejamento”) na agenda de cada `destinatario`, contado na carga do dia, movido a cada replanejamento (com aviso) e marcado como conflito nos compromissos administrativos que cruzam o horário.
- **`acoes[]`**: `{codigo, rotulo, metodo, caminho, corpo, comentario: nenhum|opcional|obrigatorio, campo (nome do campo do comentário: comentario, motivo, parecer…), estilo: ok|bad}`. No kit: `B.acao(...)`.
- **`destinatarios[]`**: o kit calcula a partir de `perfis[]` (pessoas do hub com o perfil na ferramenta, filtradas por unidade); o id do hub liga a pessoa entre as ferramentas (`h-<id>`).
- Quem publica: Originação (análise de solicitações, validação de gates, aprovação de envio ao plano, preparar/aprovar TAP, fila de publicação), Plano (fase do ciclo, pleitos a revisar, cenário a aprovar), PMP (rodada por gerente, aprovação do PMO, mudanças, ações e atas em rascunho), Ordens de Serviço (execução por participante — operacional, OS a planejar — tarefa, SLA em risco, aprovação de encerramento e de acordo de feriado — ações), Conector CRM (status sem entrega).

## Agir em nome de uma pessoa (STRAT·HUB 1.3.1)

Conectores que recebem uma decisão fora do hub (ex.: aprovação respondida no Teams) executam a ação na ferramenta de origem por `POST /api/hub/agir` `{app, caminho (/api/...), metodo, corpo, pessoa: {id_hub | email}}` — no `hubkit`: `agir(app, caminho, pessoa, corpo, metodo)`. Exige `"agir_em_nome": true` no manifesto **e** autorização do administrador (Admin técnico › Ferramentas). A ferramenta de destino recebe a identidade assinada da pessoa e confere a permissão como sempre; o hub audita.

## Ambiente de teste, MASTER TESTE e segregação de funções (STRAT·HUB 1.3.2)

- O hub declara o ambiente **só** no `hub.config.json` do servidor: `"ambiente": "teste"`; sem a linha é **produção**. As ferramentas recebem `X-Hub-Ambiente` (`hubkit.identidade()["ambiente"]`) e, em teste, mostram a faixa **AMBIENTE DE TESTE**.
- **MASTER TESTE** (marcado em Admin técnico › Usuários, só em ambiente de teste): o hub assina `X-Hub-User-Master` (= HMAC(token, `master|id|ts`)); `hubkit.identidade()["master"]` só é verdadeiro com assinatura válida **e** ambiente de teste. Na ferramenta (`appkit`), o MASTER entra com o perfil de administrador, passa em qualquer `B.exigir` (`B.master(u)`), é exceção à segregação de funções e aparece na auditoria como "(MASTER TESTE) · <papel> · exceção de teste" (`B.auditar(..., papel=)`).
- **Destinatários:** pendências `acao` e `tarefa` recebem também os MASTER TESTE (`hubkit.masters()`, `B.com_masters(lista, excluir=)`); a execução de OS (`operacional`) fica com quem executa.
- **Sem "agir como":** no hub a pessoa é sempre quem o hub disse; não existem usuários de demonstração nem `permitir_simulacao` (usuários de demonstração só no modo isolado). Para ver uma tela como outro perfil, crie no hub um usuário comum com esse perfil.
- **Segregação de funções** (regra da ferramenta de origem, vale na tela, na agenda e no Teams): quem concluiu a OS não aprova nem devolve o próprio encerramento (Ordens); quem enviou para aprovação (envio ao plano, TAP) ou abriu a solicitação não decide o próprio pedido (Originação). Essas pessoas também saem dos destinatários da pendência.

## Hierarquia, notificações e navegação (STRAT·HUB 1.3.3)

- **Hierarquia:** cada pessoa do hub tem um `lider_id` (líder direto, sem ciclos) e uma `jornada_h` (padrão 8). `/api/hub/pessoas` traz os dois; no `hubkit`: `liderados(id_hub)`, `lider_de(id_hub)`. Remover alguém do meio faz os liderados responderem ao líder acima.
- **Notificações:** o hub lê as pendências (`agenda/pendencia`) e avisa cada destinatário por e-mail e Teams, conforme a preferência da pessoa (decisões, alertas, tarefas, OS × na hora / resumo diário / desligado) e os padrões e obrigatórias da organização (Admin › Notificações). Pendências anteriores à ativação não geram envio. Aprovação que já vai para o app Aprovações do Teams não gera aviso nosso.
- **Lidas valem em todo lugar:** `GET /api/hub/notificacoes?app=&estado=` (lista da pessoa), `POST /api/hub/notificacoes/lidas` `{chaves, lida}`; ferramentas com o token do app pedem em nome da pessoa (`pessoa=`). No `hubkit`: `notificacoes_lidas(id_hub, desde)`, `marcar_notificacoes(id_hub, chaves)`.
- **Envio pelo Conector Microsoft:** o hub grava `notificacao/envio` (`canal`: `email` | `teams` | `canal`, `situacao: pendente`); o conector envia e devolve `situacao` (`enviado`/`falha`) no mesmo registro. O conector publica `integracao/microsoft/estado` (`email`, `teams_feed`, `teams_canal`, `aprovacoes_teams`) para o hub decidir os canais.
- **Navegação garantida:** em toda página HTML de ferramenta que passa pelo hub (com a pessoa logada), o hub acrescenta o botão **S·H** (`/hub-static/navhub.js`): início da ferramenta, portal, notificações da ferramenta (com as ações da origem) e preferências. `window.STRATHUB.abrirNotificacoes()` abre a tela a partir do menu da ferramenta. Uma ferramenta pode pedir `"strathub": {"botao_hub": false}` no manifesto.

## Serviços do hub para as ferramentas (1.3)

| Rota | Para quê |
|---|---|
| `GET /api/hub/pessoas` | pessoas (id, nome, e-mail, papel, localidade, escala de feriado; `master_teste` só em ambiente de teste) — sem dados sensíveis. `hubkit.pessoas()`, `hubkit.pessoa(id_hub, email)`, `hubkit.masters()` |
| `GET /api/hub/feriados?inicio=&fim=&localidade=` (ou `?ano=`) | nacionais (inclui móveis), facultativos marcados (`conta` = se vale como feriado) e locais da localidade. `hubkit.feriados()`, `hubkit.feriado_em()` — fora do hub, só os nacionais |
| `GET /api/hub/instaladas` | ferramentas ativas no hub, para a interface esconder o que depende de quem não está instalado. `hubkit.instaladas()` (None fora do hub) |

Regra geral: **integração sem dependência** — toda ferramenta funciona sozinha (a agenda vai de brinde); o lado que falta aparece no mapa como **opcional**, nunca como erro.

## Serviços (Conector CRM ↔ Ordens de Serviço ↔ PMP)

```
CRM / automação ──webhook·busca·arquivo──▶ Conector CRM ──solicitacao_externa──▶ Ordens de Serviço
       ▲                                        │                                    │   │
       └──────── webhook ou consulta ◀── status_os ◀─────────────────────────────────┘   └──os_resumo──▶ PMP (OS do projeto)
```

| domain / entity_type | De → Para | entity_key | Conteúdo (payload) |
|---|---|---|---|
| `servicos/solicitacao_externa` | Conector CRM → Ordens | `crm:<integração>:<crm_ref>` | formato neutro `strat-servicos/1`: `crm_ref`, `titulo`, `descricao`, `tipo`, `prioridade`, `cliente{nome, documento, id}`, `contato{nome, telefone, email}`, `local{endereco, cidade, uf, referencia}`, `unidade`, `ativo`, `codigo_projeto`, `data_desejada`, `crm_nome`, `integracao`, `canal` (webhook · busca · arquivo), `recebida_em` |
| `servicos/status_os` | Ordens → Conector CRM | a mesma chave da solicitação | `numero`, `status` (aberta · planejada · em_execucao · pausada · concluida · encerrada · cancelada), `status_nome`, `sla` (no_prazo · em_risco · atrasada · cumprido · violado), `prazo_sla`, `execucao_inicio`, `equipe`, `concluida_em`, `motivo`, `resumo_execucao`, `ref_externa` |
| `servicos/os_resumo` | Ordens → PMP (e painéis) | número da OS | `numero`, `titulo`, `tipo`, `prioridade`, `status`, `sla`, `prazo_sla`, `unidade`, `local`, `equipe`, `execucao_inicio`, `duracao_h`, `codigo_projeto`, `cliente`, `custo_previsto`, `custo_real`, `aberta_em`, `concluida_em` |

- O Conector entrega o status ao CRM por **webhook** (POST JSON assinado: `X-Assinatura: sha256=<HMAC do corpo>`, reenvio automático em falha) ou o CRM **consulta** `GET /apps/conector_crm/entrada/<integração>/status/<crm_ref>`.
- CRMs na nuvem não alcançam um servidor interno (Tailscale): use a **busca periódica** (o servidor busca numa URL do CRM) ou exponha só a rota `/entrada` (ex.: `tailscale funnel`).
