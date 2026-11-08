# STRAT·HUB: hub de ferramentas

Um único servidor na sua máquina Windows com:
- **portal de acesso** com login (dono e usuários convidados);
- **ferramentas plugáveis** por pasta ou por .zip (`hub.json` ou `hub.manifest.json`);
- **início sob demanda** dos apps;
- **ferramentas conectadas, mas separadas**: identidade única, perfil da organização e Data Core compartilhados;
- **acesso remoto seguro** pelo celular ou de fora de casa.

```
Navegador / celular ──HTTPS (Tailscale)──▶ STRAT·HUB :8800 ──▶ /apps/<id>/
                                             │   ├─ static  → entrega os arquivos (HTML, build React)
                                             │   └─ python/node/comando → inicia o processo e faz o proxy
                                             └─ portal, login, logs, plugar .zip
```

## 1. Primeira execução
1. Instale o **Python 3.11+** (python.org) e marque **"Add python.exe to PATH"**.
2. Dê dois cliques em **`INICIAR_HUB.bat`**. Na primeira vez ele cria o ambiente e instala as dependências (cerca de 1 minuto).
3. O navegador abre em `http://localhost:8800`. Defina a **senha do hub**; isso só é possível a partir da própria máquina.

Para desligar, use **`PARAR_HUB.bat`**: ele para todos os apps antes de sair.

## 2. Plugar uma ferramenta
- **Por pasta:** copie a pasta do app para `apps\<id>\` com um `hub.json`. Ela aparece no portal em segundos.
- **Pelo portal:** **＋ Plugar app** → envie um `.zip`. Também funciona remotamente. Para **atualizar** uma ferramenta, envie o novo `.zip` marcando "substituir": o hub extrai numa pasta temporária, para a ferramenta e troca os arquivos de uma vez, preservando `.venv` e dados. Se algum arquivo estiver em uso (comum no Windows), nada é alterado e a versão anterior continua funcionando; a versão substituída fica em **Admin técnico › Ferramentas › Lixeira**.

Modelos e exemplos de `hub.json` (HTML único, build React/Vite, FastAPI, Flask, Streamlit, Node) estão em **`apps\_modelo\LEIA-ME.md`**. O manifesto mínimo é:
```json
{ "nome": "Meu painel", "icone": "📊", "categoria": "Painéis", "tipo": "static" }
```
- **Apps Python** ganham um `.venv` próprio, e o `requirements.txt` é instalado sozinho.
- **Apps de processo** sobem no primeiro acesso e param depois de 30 min sem uso. Isso é configurável por app (`ocioso_min`, `sempre_ligado`).
- **Pelo menu ⋯ de cada card:** iniciar, parar, reiniciar, ver log e copiar link.

## 3. Ferramentas conectadas (versão 1.1)
Cada ferramenta continua independente (banco, interface e regras próprias). O hub oferece três serviços comuns:

| Serviço | O que faz | Onde |
|---|---|---|
| **Identidade** | O hub diz **quem** é o usuário; cada ferramenta decide **o que** ele pode fazer. O proxy injeta `X-Hub-User-*` assinados (HMAC) e descarta qualquer cabeçalho forjado. | ⚙ Administração › Usuários |
| **Perfil da organização** | Nome, sigla, cores, unidades, áreas, vocabulário (ex.: "PPI", "Código ERP"), ano base e taxa de desconto. Todas as ferramentas leem daqui. | ⚙ Administração › Organização · `GET /api/hub/organizacao` |
| **Data Core** | Registros compartilhados `(domain, entity_type, entity_key, period)` + payload JSON, com versão, origem, usuário e histórico. É a única fronteira de troca entre ferramentas. | ⚙ Administração › Data Core · `/api/data-core/records` |

- **Usuários:** o dono cria usuários (nome, e-mail, senha) e escolhe quais ferramentas cada um vê. No login, o dono deixa o e-mail em branco.
- **SDK:** `sdk\hubkit.py` (Python, sem dependências) e `/hub-static/hubkit.js` (navegador). Com eles o app lê a identidade, o perfil da organização e publica/consulta o Data Core. Fora do hub, o `hubkit` cai para um Data Core local em arquivo, então a ferramenta também roda sozinha.
- **Compatível com HUB PMO:** pastas com `hub.manifest.json` (schema_version, runtime.start_command, runtime.port, runtime.health) são reconhecidas sem alteração. A porta fixa do manifesto vira porta dinâmica; um bloco opcional `"strathub": {…}` aceita qualquer campo do `hub.json`.

Credencial app → hub: cada app recebe `HUB_URL` e `HUB_APP_TOKEN` (derivado de um segredo local) e chama o hub com `X-Hub-App-Token`.

## 4. Admin técnico e ecossistemas (versão 1.2)
O botão **⚙ Admin técnico** do portal (só dono e administradores) abre `/admin`, a central de operação do hub:

| Tela | Para quê |
|---|---|
| **Visão geral** | ferramentas ativas/paradas/com erro, uso de disco, último backup, alertas |
| **Ferramentas** | ocultar/mostrar no portal; iniciar, parar, reiniciar; "sempre ligada" ou parar após X min; editar nome, ícone, categoria, ordem e descrição; **remover para a lixeira** (com os dados) e restaurar ou excluir de vez |
| **Ecossistema** | mapa de quem publica e quem consome cada contrato do Data Core (lado ausente = opcional; alerta só para ferramenta com problema); **modelos de ecossistema** (Projetos, Serviços); exportar/importar `ecossistema.json` |
| **Dependências** | pacotes instalados por ferramenta, verificação do `requirements.txt` e reinstalação |
| **Data Core** | navegação pelos registros, versões e histórico |
| **Logs** | log do hub e de cada ferramenta |
| **Auditoria** | logins (inclusive recusados), usuários, organização, ações nas ferramentas, ecossistema e backups — exportável em CSV |
| **Usuários / Organização** | os mesmos cadastros do portal, em tela cheia |
| **Infraestrutura e backup** | endereço Tailscale, disco, backup manual e **diário** (mantém os N últimos) com download |
| **Configurações** | nome, ociosidade padrão, sessão, backup diário |

- **Contratos no manifesto:** cada ferramenta declara `"contracts": {"publishes": [...], "consumes": [...]}` no `hub.manifest.json` (ou `"publica"/"consome"` no `hub.json`). É isso que o mapa desenha.
- **Modelos de ecossistema** (`hub\modelos\ecossistema-*.json`): aplicar um modelo configura as ferramentas e o portal para uma aplicação (mostra as do modelo, oculta as demais). Não altera a organização nem apaga dados. Para criar o seu, exporte o ecossistema atual e coloque o arquivo nessa pasta.
- **Rotas públicas por ferramenta:** `"strathub": {"rotas_publicas": ["entrada"]}` deixa só `/apps/<id>/entrada/…` acessível sem login do hub — a ferramenta autentica por conta própria (ex.: webhook do Conector CRM com chave por integração). O resto do app continua protegido.
- **Fluxo automático:** as ferramentas sincronizam sozinhas (ao abrir e a cada 2 min) e publicam o que depende de alguém em `agenda/pendencia`, que a StratAgenda mostra como caixa de entrada. A variável de ambiente `SYNC_INTERVALO_S` ajusta o intervalo.

## 4.1 Pessoas, localidades e feriados (versão 1.3)
- **Organização → Feriados:** nacionais automáticos (inclusive os móveis: Carnaval, Paixão, Corpus Christi), **facultativos** marcados à parte (opção “contar facultativos como feriado”), e **localidades** com seus feriados estaduais/municipais (`MM-DD` todo ano ou `AAAA-MM-DD`). Cada **unidade** aponta a sua localidade. Visualizador por ano e localidade.
- **Usuários:** localidade da pessoa e **“trabalha em feriados (escala)”**. As ferramentas usam isso para não bloquear nada automaticamente: com escala, normal; sem escala, ciência + de acordo do gestor.
- **Serviços para as ferramentas:** `/api/hub/pessoas`, `/api/hub/feriados`, `/api/hub/instaladas` (no `hubkit`: `pessoas()`, `pessoa()`, `feriados()`, `feriado_em()`, `instaladas()`). As interfaces perguntam o que está instalado e escondem o que depende de quem não está.
- **Integração sem dependência:** no mapa do ecossistema, o lado que falta aparece como **opcional**; alerta só quando uma ferramenta presente tem problema.

## 4.2 Conectores que agem em nome das pessoas (versão 1.3.1)
Um conector (ex.: **Conector Microsoft**) pode executar numa ferramenta a ação que a pessoa escolheu fora do hub — por exemplo, aprovou no Teams. Só com `"agir_em_nome": true` no manifesto **e** autorização do administrador em **Admin técnico › Ferramentas › Ajustar › Autorizar a agir em nome das pessoas**. A ferramenta de destino recebe a identidade da pessoa e confere a permissão como sempre; só rotas `/api/`, só pessoas com acesso à ferramenta, e tudo vai para a auditoria.

## 4.3 Ambiente de teste e MASTER TESTE (versão 1.3.2)
- **Produção é o padrão.** Para um hub de testes, acrescente `"ambiente": "teste"` em `data\hub.config.json` e reinicie. Aparece a faixa **AMBIENTE DE TESTE** no portal e em todas as ferramentas.
- Em **Admin técnico › Usuários** surge a opção **MASTER TESTE** por pessoa (pode haver mais de uma). O MASTER recebe todas as decisões e tarefas de todas as ferramentas (agenda e Teams), executa qualquer papel e pode aprovar o que ele mesmo concluiu — tudo marcado como **exceção de teste** na auditoria. Em produção a opção não existe e a marcação é ignorada.
- Não existe mais "Agir como" nem usuários de demonstração no hub. Para ver as telas de um perfil (técnico, solicitante…), crie um usuário comum com esse perfil e entre com ele numa janela anônima.
- Nome e e-mail dos usuários são editáveis aqui (e o do dono também), só pelo administrador, com confirmação e auditoria (o e-mail anterior fica registrado). O e-mail é o login e é por ele que as integrações encontram a pessoa. Quando houver login corporativo (SSO), o e-mail virá do diretório da empresa.

## 4.4 Hierarquia, notificações e navegação (versão 1.3.3)
- **Líder direto e jornada:** em **Admin técnico › Usuários**, cada pessoa tem um líder direto (o hub recusa ciclos) e a jornada diária. As ferramentas usam isso: na agenda, cada líder vê e atribui tarefas só para quem responde diretamente a ele, e a carga é medida contra a jornada.
- **Notificações:** em **Admin técnico › Notificações** ficam o servidor de e-mail (SMTP, opcional), o endereço público dos links, a hora do resumo diário e os padrões por tipo (decisões, alertas, tarefas, execução de OS), incluindo os obrigatórios. Cada pessoa ajusta o que recebe em **Portal › 🔔 Notificações**. Sem SMTP, o e-mail sai pela conta de integração do **Conector Microsoft**, que também leva os avisos ao Teams (feed de atividades e canal da equipe).
- **Botão S·H:** toda ferramenta aberta pelo hub ganha o botão **S·H** (canto inferior direito), com início da ferramenta, portal, notificações da ferramenta (com os botões de ação) e preferências. O que você lê ou resolve num lugar some dos outros (ferramenta, agenda, portal).

## 5. Acesso remoto (celular, notebook, fora de casa)
1. Instale o **Tailscale** no PC (tailscale.com/download/windows) e no celular, entrando com a **mesma conta**.
2. Rode **`tools\ACESSO_REMOTO.bat`**. Ele publica o hub só na sua rede privada Tailscale, com HTTPS, e mostra o endereço `https://<seu-pc>.<sua-rede>.ts.net`.
   - Se aparecer um link para habilitar HTTPS/certificados, abra e confirme uma vez.
3. Abra esse endereço no celular. A senha do hub continua sendo exigida.

Outros pontos:
- **Sem portas abertas:** nada fica exposto na internet nem no roteador. Para desfazer, rode `tools\DESATIVAR_ACESSO_REMOTO.bat`.
- **Mostrar algo para um cliente** (link público temporário): `tailscale funnel --bg 8800`, depois `tailscale funnel reset`. Prefira marcar só o app de demonstração como `"publico": true`.
- **PC dormindo:** se o PC hibernar, o acesso remoto cai. Ajuste em Configurações → Energia.

## 6. Iniciar junto com o Windows
**`tools\INICIAR_COM_WINDOWS.bat`** faz o hub subir sozinho, sem janela, ao entrar no Windows. Para desfazer, use `tools\REMOVER_INICIO_COM_WINDOWS.bat`. Nesse modo o log fica em `data\hub.log`.

## 7. Configuração (`data\hub.config.json`)
`nome`, `porta` (8800), `host` (`127.0.0.1` = só esta máquina + Tailscale; `0.0.0.0` = também a rede local), `ocioso_min_padrao`, `porta_inicial_apps`, `sessao_dias`, `ambiente` (`"teste"` liga o ambiente de teste; ausente = produção).
Para trocar a senha do dono, apague `senha_hash` e `senha_salt` desse arquivo e reabra `http://localhost:8800` na própria máquina.
Outros arquivos em `data\`: `usuarios.json`, `organizacao.json`, `data_core.db`, `apps_overrides.json` (ajustes do admin), `auditoria.jsonl`, `backups\` (backups do admin: `data\` do hub + `dados/config` de cada ferramenta). Remoções vão para `apps\_removidos\`.

## Segurança
- **Senha:** fica só nesta máquina (PBKDF2), nunca fixa no código. A sessão é por cookie assinado, e há bloqueio após tentativas erradas.
- **Configuração inicial:** só pode ser feita localmente.
- **Isolamento dos apps:** não recebem o cookie de sessão do hub, e os cookies de cada app ficam isolados no caminho dele.
- **Identidade:** cabeçalhos `X-Hub-*` vindos do navegador são descartados; o hub injeta os seus, assinados com o token do app.
- **Permissões:** só o dono e administradores iniciam/param apps, veem logs, plugam .zip e gerenciam usuários e organização.
- **Arquivos .zip:** os enviados são verificados contra caminhos maliciosos.
- **Processos:** se o PC desligar de repente, os processos que sobrarem são encerrados na próxima inicialização.

## Estrutura
```
INICIAR_HUB.bat  PARAR_HUB.bat  requirements.txt (versões fixadas)
hub\        servidor (FastAPI): main.py, registry.py, runner.py, auth.py, usuarios.py, identidade.py,
            organizacao.py, datacore.py, admin.py, auditoria.py, modelos\ (ecossistemas),
            static\ (portal, admin técnico, hubkit.js)
sdk\        hubkit.py — copie para a pasta do app que for usar identidade/organização/Data Core
apps\       suas ferramentas (uma pasta por app, com hub.json)
data\       configuração, logs, portas e processos (criado na 1ª execução)
tools\      início com o Windows, acesso remoto, desligamento
tests\      testes (node tests\e2e_hub.mjs · python tests\e2e_admin.py · python tests\test_substituir.py)
```
