# StratAgenda — servidor central

A mesma StratAgenda (tarefas, blocos na semana, janela de trabalho, time, convites e sugestões do gestor), agora com os dados **no servidor do hub**: abra no computador ou no celular e veja sempre o mesmo, sem sincronização manual.

- **Login único:** a identidade vem do hub (sem senha própria). No primeiro acesso a pessoa faz o onboarding (janela de trabalho).
- **Time:** convites gestor ↔ membro como antes; a pessoa precisa ter entrado uma vez para ser convidada.
- **Você é o dono da sua agenda.** Edite título, prioridade, duração e descrição das suas tarefas. O gestor **não altera** a agenda do time (nem as tarefas que ele criou): **sugere** — mover, agendar, **nova duração** ou **substituição** (passar a tarefa para outra pessoa do time, com aceite de quem tem e de quem recebe). Recusar não expõe o seu comentário.
- **Tarefas × ações do ecossistema:** o que consome tempo (preparar o TAP, revisar pleitos, enviar tendências, ata em rascunho…) entra **sozinho no seu backlog** com atalho para a ferramenta; quando várias pessoas podem tratar, aparece para todas e **quem assumir leva**. O que é decisão (aprovar, devolver, de acordo) chega como **notificação ⚡** com os botões da própria ferramenta — a agenda executa em seu nome e a ferramenta confere a permissão e registra. Concluída na origem → fecha sozinha; concluída aqui com a origem aberta → **“pendente na origem”**.
- **Ordens de serviço (regime operacional):** a execução de OS entra **travada** (🔒 “OS — definido pelo planejamento”), conta na carga do dia, muda quando o planejador replaneja (você é avisado) e marca **“em conflito com OS”** o compromisso administrativo que cruzar o horário.
- **Feriados destacados** (calendário do hub pela sua localidade). Alocar em feriado nunca é bloqueado: pede **ciência**; sem escala de feriado, fica tracejado até o **de acordo do seu gestor**. Sugestão do gestor em feriado pede ciência dos dois lados.
- **Caixa de entrada do ecossistema** (menu ☰ › Ecossistema): tudo o que as ferramentas publicam, por ferramenta, com assumir, executar ação e abrir na ferramenta.
- **Reunião de projeto:** escolha o projeto da carteira, data, hora, participantes e pauta — entra na sua agenda e cria o **rascunho da ata na PMP**; remarcar atualiza o rascunho, excluir marca como cancelada.
- **Servidor e backup** (Configurações): backup completo em JSON (formato do app, restaurável no app local) e envio de cópia ao OneDrive. O hub também faz backup diário.

## Migração (importação única)
O administrador da agenda (dono/administradores do hub) traz os dados antigos uma vez:
- **Arquivos:** backups exportados do app local e/ou os arquivos da pasta `StratAgenda/usuarios` e `StratAgenda/compartilhados` do OneDrive; ou
- **Direto do OneDrive:** com o ID do aplicativo Azure da sincronização antiga (cadastre o endereço de retorno mostrado na tela).

A conciliação é a mesma da sincronização antiga (o mais recente vence, campo a campo; excluídos ficam de fora). Cada pessoa assume os próprios dados ao entrar pelo hub com o **mesmo e-mail** usado no app.

## Celular
- **Navegador:** abra o endereço do hub (Tailscale) e a StratAgenda; dá para "adicionar à tela inicial".
- **App Android "StratAgenda" (servidor):** `StratAgenda-Servidor-v3.1.0.apk` pede o endereço do hub na primeira abertura e abre a agenda direto. Precisa de conexão (online-first); sem rede mostra "tentar de novo". Convive com o app local (pacote diferente).

## Integrações (Data Core do hub)
- **Consome** `agenda/pendencia` (de todas as ferramentas) e os projetos da carteira (`pmp/tap_aprovado`, `pmp/plano_aprovado`) para a reunião de projeto.
- **Publica** `pmp/ata_rascunho`.

## Perfis
| Perfil | Pode |
|---|---|
| Administrador da agenda | tudo, inclusive importação única e backup completo; vê todas as pendências do ecossistema |
| Membro | a própria agenda, time e pendências atribuídas a ele ou sem responsável |

## Endereço e dados
Isolada (desenvolvimento): `http://127.0.0.1:8870` · dados em `dados/` (variável `STRATAGENDA_DADOS`). A interface (`static/`) é o build web em modo servidor: `VITE_MODO=servidor VITE_BASE=/apps/stratagenda/ npm run build` no código-fonte do app.

## Instalar
**No STRAT·HUB:** **＋ Plugar app** com o `.zip` (ou copie a pasta para `apps\stratagenda\`). Se o hub já tinha a StratAgenda estática (versão 2, local-first), substitua — os dados locais de cada aparelho não são apagados; traga-os com a importação única.

## Testes
```
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
```

## Integração
Contrato completo entre as ferramentas em `docs/CONTRATO_INTEGRACAO.md`.
