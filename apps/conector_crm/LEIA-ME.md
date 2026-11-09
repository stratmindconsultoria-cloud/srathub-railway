# Conector CRM

**Integração, não CRM.** O CRM da empresa continua sendo o CRM. O conector recebe as **solicitações de serviço** num formato neutro, entrega para **Ordens de Serviço** e devolve ao CRM o **status de cada OS**.

```
CRM / automação (n8n, Make, Zapier)
   │  POST /apps/conector_crm/entrada/<id>/solicitacoes   (cabeçalho X-Chave)
   ▼
Conector CRM ──servicos/solicitacao_externa──▶ Ordens de Serviço
   ▲                                               │
   └──── webhook assinado  ou  GET …/status/<crm_ref> ◀── servicos/status_os
```

## Como usar
1. **Nova integração** → copie a **chave** (mostrada uma única vez; gere outra se perder).
2. No CRM/automação, envie `POST …/entrada/<id>/solicitacoes` com `X-Chave: <chave>` (ou `Authorization: Bearer <chave>`) e um objeto ou lista no **formato neutro** (`strat-servicos/1`):
   ```json
   {"crm_ref": "T-100", "titulo": "Ar-condicionado sem refrigerar", "descricao": "Sala 3", "tipo": "atendimento", "prioridade": "alta",
    "cliente": {"nome": "Cliente Exemplo"}, "contato": {"nome": "Maria", "telefone": "(11) 99999-0000"}, "local": {"endereco": "Rua Um, 10"}}
   ```
   Obrigatórios: `crm_ref` e `titulo`. Aceita prioridade em inglês (low/medium/high/urgent).
3. **De-para de campos:** se o CRM manda outro formato, informe para cada campo neutro o caminho no JSON do CRM (`properties.subject`, `contacts.0.email`…) e valores padrão. A tela testa com um registro de exemplo.
4. **Saída de status:** configure um **webhook** (POST JSON com `X-Assinatura: sha256=<HMAC do corpo com o segredo>`; falhas são reenviadas a cada minuto, até 8 tentativas) **ou** deixe o CRM **consultar** `GET …/entrada/<id>/status/<crm_ref>` (uma) ou `GET …/entrada/<id>/status?desde=<epoch>` (lote).
5. **Busca periódica** (para CRMs na nuvem, que não alcançam um servidor interno/Tailscale): o servidor busca a cada N minutos uma URL do CRM que devolve a lista (com token Bearer opcional).
6. **Contingência:** carga por arquivo JSON ou CSV (cabeçalhos do formato neutro: `crm_ref;titulo;cliente.nome;…`).

Logs de **entradas** (com erro por item, sem interromper o lote) e **saídas** (HTTP, tentativas) ficam na tela de cada integração.

**Segurança:** só `/entrada/…` é público no hub (manifesto: `"rotas_publicas": ["entrada"]`) e exige a chave da integração (guardada só como hash). A interface do conector exige login do hub.

**Adaptadores específicos** (HubSpot, Pipedrive, RD Station, Salesforce…) ficam para versões futuras; o de-para cobre a maioria dos casos hoje.

## Perfis
| Perfil | Pode |
|---|---|
| Integrador | criar e configurar integrações, chaves, de-para, importar arquivos |
| Leitura | consultar integrações e logs |

## Endereço e dados
Isolada: `http://127.0.0.1:8860` · dados em `dados/` (variável `CONECTOR_CRM_DADOS`).

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
