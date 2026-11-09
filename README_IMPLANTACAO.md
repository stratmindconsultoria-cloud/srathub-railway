# STRAT·HUB 1.3.3 | Railway temporário

Pacote de implantação experimental preparado a partir dos nove arquivos fornecidos.
**Ainda não foi validado em uma instância Railway real.** Não contém bancos, usuários e configurações locais do Lenovo.

## Pelo tablet
1. Crie um repositório **privado** no GitHub. Envie **os arquivos desta pasta na raiz**, sem incluir senhas nem dados pessoais. GitHub no navegador permite upload múltiplo. No tablet, ative *Site para computador*.
2. No Railway, crie projeto usando o repositório GitHub e selecione o plano aplicável. A implantação utiliza o `Dockerfile` automaticamente.
3. **Antes de colocar no ar**, crie um volume persistente e monte em `/data` (caminho exato).
4. Em Variables, configure `HUB_INITIAL_PASSWORD` com uma senha exclusiva de pelo menos 12 caracteres. Não inclua a senha no repositório. `PORT` pode ser definido como `8800` se não estiver configurado pelo Railway.
5. Execute o deploy, consulte logs e gere domínio HTTPS pela opção Networking. Você não precisa de domínio próprio.
6. Acesse o endereço `/entrar` e entre com a senha. O portal terá os módulos de origem, sem seus dados anteriores.
7. Abra cada módulo sob demanda. A primeira execução pode demorar porque cada serviço instala suas dependências em um ambiente isolado no volume.
8. Não ative webhooks públicos e integrações externas antes de validar autenticação, callbacks e dados.

## Segurança e limites
- Todos os módulos foram alterados apenas nos manifestos para iniciar sob demanda, reduzindo o gasto enquanto estiverem ociosos. Rotas públicas do conector CRM foram desabilitadas na cópia temporária.
- O código do HUB não foi modificado. O bootstrap define senha antes de abrir tráfego, contornando a configuração inicial local. `HUB_INITIAL_PASSWORD` deve continuar configurada para permitir reinícios, embora a senha seja armazenada em hash no volume.
- Volume precisa ser criado antes da primeira implantação. Sem volume, o programa encerra de propósito.
- O código e os dados dos módulos ficam em `/data/apps`; os dados centrais em `/data/hub`. Depois de criados, os apps não são sobrescritos por novos deploys.
- Deploy Railway pode custar mais de US$5 conforme CPU, RAM, disco, rede e tempo. Não é garantia de funcionamento em plano gratuito.
- Uma única instância da aplicação/replica para evitar concorrência sobre SQLite.
- Testar especificamente Microsoft 365 OAuth e processos de sincronização. Configuração/segredos de integrações não constam dos ZIPs.
- Para baixar backup após uso: Admin técnico -> Infraestrutura e backup. Garanta que inclua os dados das apps.
- O app principal expõe porta atribuída pelo Railway e as demais apps usam apenas loopback dentro do mesmo container.

## Verificações
- GET `/health` deve retornar `ok: true` após deploy.
- `/entrar` deve renderizar a tela de acesso; `/` deve solicitar login.
- Inicie um módulo leve e consulte seu log no Admin técnico.
- A primeira carga completa dos oito módulos pode não caber em plano de recursos mínimos. Evite iniciar todos ao mesmo tempo.
