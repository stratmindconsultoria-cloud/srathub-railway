"""App de exemplo plugado no hub. Rode isolado com:  python -m uvicorn app:app --port 9000"""
import os
from fastapi import FastAPI, Form, Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI(title="Exemplo FastAPI")
NOTAS: list[str] = []


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    base = request.scope.get("root_path", "")  # "/apps/exemplo-fastapi" quando servido pelo hub
    itens = "".join(f"<li>{n}</li>" for n in NOTAS) or "<li><i>nenhuma nota</i></li>"
    return f"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/hub-static/hub.css">
<main style="max-width:640px;margin:30px auto;padding:0 16px">
<h2>🐍 Exemplo FastAPI</h2>
<p style="color:var(--text2)">Servido em <code>{base or '/'}</code> · porta interna {os.environ.get('PORT')}</p>
<h3 style="margin-top:20px">Notas</h3><ul id=notas>{itens}</ul>
<form method=post action="{base}/notas" style="display:flex;gap:8px;margin-top:10px"><input class=campo name=texto required placeholder="Nova nota"><button class="btn primario">Adicionar</button></form>
<h3 style="margin-top:20px">WebSocket (eco)</h3><p id=ws>conectando…</p>
<script>
const proto = location.protocol === 'https:' ? 'wss' : 'ws'
const s = new WebSocket(proto + '://' + location.host + '{base}/ws')
s.onopen = () => s.send('olá pelo hub')
s.onmessage = e => document.getElementById('ws').textContent = 'resposta: ' + e.data
</script></main>"""


@app.post("/notas")
def nova_nota(request: Request, texto: str = Form(...)):
    NOTAS.append(texto[:200])
    return RedirectResponse("/", status_code=303)   # o hub reescreve para o caminho do app


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    while True:
        msg = await sock.receive_text()
        await sock.send_text(f"eco: {msg}")
