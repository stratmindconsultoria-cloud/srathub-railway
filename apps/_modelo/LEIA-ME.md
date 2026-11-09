# Como plugar uma ferramenta no hub

1. Crie uma pasta em `apps/` com o identificador do app (minúsculas, números, `-` ou `_`), ex.: `apps/meu-painel/`.
2. Coloque os arquivos do app e um `hub.json` (veja os exemplos abaixo).
3. Pronto: o hub detecta em poucos segundos e o app aparece no portal em `/apps/meu-painel/`.
   (Ou compacte a pasta em .zip e use **＋ Plugar app** no portal — funciona também remotamente.)

Pastas que começam com `_` (como esta) são ignoradas.

## Tipos

| tipo | quando usar | o hub faz |
|---|---|---|
| `static` | HTML único, painel exportado, build de React/Vite | entrega os arquivos; sem processo |
| `python` | FastAPI, Flask, Streamlit, Dash… | cria `.venv` próprio, instala `requirements.txt`, inicia sob demanda |
| `node` | Express, Next (start), etc. | roda `npm install` quando `package.json` muda, inicia sob demanda |
| `comando` | qualquer executável que abra uma porta | inicia o comando informado |

## Marcadores no "comando"
`{python}` (python do .venv do app) · `{port}` (porta interna livre) · `{base}` (`/apps/<id>/`) · `{base_noslash}` (`/apps/<id>`) · `{dir}` (pasta do app).
O app também recebe as variáveis de ambiente `PORT`, `HUB_BASE_PATH` e `HUB_APP_ID`.

## Exemplos de hub.json

Estático (HTML único):
```json
{ "nome": "Painel Diretoria", "icone": "📊", "categoria": "Painéis", "tipo": "static" }
```

Build de React/Vite (gere com `base` = `/apps/<id>/`):
```json
{ "nome": "StratAgenda", "tipo": "static", "pasta": "dist", "spa": true }
```

FastAPI:
```json
{ "nome": "Analisador", "tipo": "python",
  "comando": "{python} -m uvicorn app:app --host 127.0.0.1 --port {port} --root-path {base_noslash}",
  "health": "/health", "ocioso_min": 30 }
```

Flask:
```json
{ "nome": "RUAH (dev)", "tipo": "python", "comando": "{python} -m flask --app app run --port {port}" }
```
No Flask, para links e redirects saírem com o prefixo, use no app:
`from werkzeug.middleware.proxy_fix import ProxyFix; app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1, x_proto=1, x_host=1)`

Streamlit:
```json
{ "nome": "Dashboard", "tipo": "python",
  "comando": "{python} -m streamlit run app.py --server.port {port} --server.baseUrlPath {base_noslash} --server.headless true",
  "manter_prefixo": true, "health": "/apps/<id>/_stcore/health" }
```

Node:
```json
{ "nome": "API Node", "tipo": "node", "comando": "node server.js" }
```

## Opções
`ocioso_min` (para após X min sem uso; 0 = nunca) · `sempre_ligado` (inicia com o hub) · `publico` (sem login — use só para demonstrações) · `manter_prefixo` (repassa `/apps/<id>/…` sem cortar — Streamlit, Next com basePath) · `ativo: false` (oculta) · `env` (variáveis extras).

## Dicas
- Use caminhos relativos ou o prefixo `HUB_BASE_PATH` em links internos. Redirects e cookies com caminho `/` o hub ajusta sozinho.
- Todos os apps compartilham o mesmo endereço (origem): use nomes próprios para chaves de `localStorage`/IndexedDB.
