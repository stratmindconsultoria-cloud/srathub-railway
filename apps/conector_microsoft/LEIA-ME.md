# Conector Microsoft · 1.3

Liga o ecossistema ao **Microsoft 365** (Outlook e Teams). É opcional: sem ele, nada muda nas outras ferramentas.

- **Conta Microsoft por pessoa:** em *Minha conta › Conectar*, o hub mostra um código; a pessoa abre microsoft.com/devicelogin, digita o código e aceita. O acesso fica **cifrado no servidor**; dá para desconectar a qualquer momento. Não precisa de HTTPS nem de endereço público.
- **Calendário nos dois sentidos:** os blocos da StratAgenda (inclusive as execuções de OS, com 🔒) aparecem no Outlook/Teams como **ocupado**, com a etiqueta *STRAT·HUB*; os compromissos do Outlook entram na agenda como **ocupado** (📅, travados; os privados aparecem só como “Ocupado”). O que um lado criou não volta para ele.
- **Aprovações no Teams:** pendências do tipo *ação* com botões (aprovar encerramento de OS, mudança, envio ao plano, TAP, de acordo de feriado…) também aparecem no app **Aprovações** do Teams, com as mesmas respostas, para os destinatários encontrados no Microsoft 365 pelo e-mail. **A primeira resposta vale:**
  - respondeu no Teams → o conector executa a ação na ferramenta de origem **em nome de quem respondeu**, pelo hub (a origem confere a permissão e audita; o hub registra “Ação executada via Conector Microsoft em nome da pessoa”). Ação que exige comentário e veio sem comentário não é executada (fica explicado em *Atividade*);
  - resolveu na agenda ou na ferramenta → a aprovação do Teams é **cancelada**.
- **Aprovações criadas no Teams → agenda:** as aprovações do app Aprovações em que a pessoa é **aprovadora** (e ainda não respondeu) aparecem na StratAgenda como ação ⚡ com “Aprovar”/“Rejeitar” (ou as respostas personalizadas). Respondeu na agenda → a resposta vai para o Teams em nome da pessoa; respondeu no Teams → some da agenda. Cada pessoa precisa ter a própria conta conectada.
- **Notificações do hub (1.3):** o hub decide quem recebe o quê (preferências de cada pessoa); o conector entrega pela conta emissora: **e-mail** (quando o hub não tem SMTP), aviso no **feed de atividades do Teams** (com o app do Teams publicado) e **alerta no canal da equipe** escolhido para cada ferramenta.
- **Conta conferida (1.2):** a aprovação vai para a conta Microsoft que a pessoa conectou quando ela confere com o e-mail do cadastro no hub (e-mail, login ou alias) ou quando o administrador confirma. A mesma conta Microsoft em duas pessoas é bloqueada (permitida só em ambiente de teste).
- **Atividade:** o que foi para o Teams, quem respondeu, o que foi executado, por que algo não foi e o resultado da última sincronização.

## Configuração (uma vez)
1. **Microsoft Entra** (administrador do Microsoft 365): registre o aplicativo (*Vários locatários de ID de Entra*), ligue **Permitir fluxos de cliente público** e dê as permissões delegadas `User.Read`, `User.ReadBasic.All`, `offline_access`, `Calendars.ReadWrite`, `ApprovalSolution.ReadWrite`, `ApprovalSolutionResponse.ReadWrite` com **consentimento do administrador**. Opcionais (cada uma liga um recurso; sem ela, o resto funciona): `Mail.Send` (e-mails do hub), `TeamsActivity.Send` (feed de atividades), `Team.ReadBasic.All` + `Channel.ReadBasic.All` + `ChannelMessage.Send` (alertas no canal). A tela *Configuração* mostra quais estão liberadas.
2. **Conector › Configuração:** cole o **ID do aplicativo (cliente)**. Locatário: `organizations` (qualquer empresa) ou o ID do diretório.
3. **Hub › Admin técnico › Ferramentas › Conector Microsoft › Ajustar:** marque **Autorizar a agir em nome das pessoas** (sem isso, as respostas do Teams ficam aguardando).
4. Cada pessoa conecta a própria conta. O administrador escolhe a **conta emissora** (de preferência uma conta de integração, que não aprova nada); sem ela, as aprovações e notificações aguardam.
5. **Notificações no Teams (opcional):** *Configuração › Baixar pacote do app do Teams* e publique no Centro de administração do Teams; marque “App publicado”. Escolha o canal de cada ferramenta para os alertas de equipe.

## Limites conhecidos
- A API de aprovações da Microsoft é **beta** e só aceita acesso delegado: as aprovações aparecem como pedidas pela conta emissora. Mantenha a agenda e as ferramentas como canal principal.
- O calendário é sincronizado a cada minuto (janela configurável, padrão 14 dias à frente).
- Contas pessoais (Outlook.com/Hotmail) não usam o app Aprovações.

## Integração (Data Core)
Consome `agenda/compromisso` (StratAgenda 3.2+), `agenda/pendencia` e `notificacao/envio` (pedidos do hub); publica `agenda/ocupado`, `agenda/pendencia` (aprovações do Teams), `integracao/microsoft` (o que está pronto) e a situação de cada `notificacao/envio`. Executa ações por `POST /api/hub/agir` (STRAT·HUB 1.3.1+).

## Endereço e dados
Isolado: `http://127.0.0.1:8880` · dados em `dados/` (variável `CONECTOR_MICROSOFT_DADOS`), com a chave de cifragem dos tokens em `dados/chave_tokens.key` (faça backup junto).

## Testes
`python -m pytest -q` (Microsoft simulada) · ponta a ponta no hub: `tests/e2e_microsoft.py` no pacote do ecossistema.
