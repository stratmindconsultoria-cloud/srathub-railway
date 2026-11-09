"""STRAT·HUB — servidor único: portal de acesso + apps plugados por pasta (apps/<id>/hub.json)."""
from __future__ import annotations
import asyncio, io, json, mimetypes, re, shutil, tempfile, time, zipfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from . import __version__, admin, auditoria, auth, config, datacore, feriados, identidade, notificacoes, organizacao, registry, usuarios
from .runner import Runner

STATIC = Path(__file__).resolve().parent / "static"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "host"}
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/manifest+json", ".webmanifest")


class Hub:
    def __init__(self):
        self.cfg = config.load()
        self.apps: dict[str, registry.AppDef] = registry.scan()
        self.runner = Runner(self.cfg)
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0), follow_redirects=False)
        self.tasks: list[asyncio.Task] = []
        self.inicio = time.time()

    def rescan(self):
        novos = registry.scan()
        for app_id, old in self.apps.items():  # app removido ou desativado: desliga
            n = novos.get(app_id)
            if (not n or not n.ativo or n.oculto) and self.runner.alive(app_id):
                asyncio.create_task(self.runner.stop(app_id, "app removido, desativado ou oculto"))
        self.apps = novos

    async def loop_rescan(self):
        while True:
            await asyncio.sleep(4)
            try:
                self.rescan()
            except Exception as e:
                print("[hub] erro no rescan:", e)

    async def loop_notificacoes(self):
        await asyncio.sleep(5)
        while True:
            try:
                await asyncio.to_thread(notificacoes.despachar, self.cfg)
            except Exception as e:
                print("[hub] erro nas notificações:", e)
            await asyncio.sleep(float(self.cfg.get("notificacoes_intervalo_s", 30)))

    async def loop_housekeeping(self):
        while True:
            await asyncio.sleep(20)
            try:
                await self.runner.housekeeping(self.apps)
                if self.cfg.get("backup_diario", True) and time.time() - admin.ultimo_backup_diario() > 23.5 * 3600:
                    r = await asyncio.to_thread(admin.criar_backup, "diario")
                    admin.manter_backups(int(self.cfg.get("backup_manter", 14)))
                    auditoria.registrar("hub", "Backup diário", r["nome"])
            except Exception as e:
                print("[hub] erro na manutenção:", e)


hub: Hub | None = None
SERVIDOR = None  # uvicorn.Server (definido em __main__) — usado no desligamento ordenado


@asynccontextmanager
async def lifespan(app: FastAPI):
    global hub
    hub = Hub()
    datacore.iniciar()
    notificacoes.iniciar()
    hub.tasks = [asyncio.create_task(hub.loop_rescan()), asyncio.create_task(hub.loop_housekeeping()), asyncio.create_task(hub.loop_notificacoes())]
    for a in hub.apps.values():
        if a.ativo and a.is_process and a.sempre_ligado:
            asyncio.create_task(hub.runner.ensure_running(a))
    nome = hub.cfg["nome"]
    print(f"\n  {nome} v{__version__}  →  http://localhost:{hub.cfg['porta']}\n  Apps: {', '.join(hub.apps) or '(nenhum)'}\n")
    yield
    for t in hub.tasks:
        t.cancel()
    await hub.runner.stop_all()
    await hub.client.aclose()


app = FastAPI(title="STRAT·HUB", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


# ── segurança ─────────────────────────────────────────────────
def usuario(request) -> dict | None:
    return usuarios.obter(hub.cfg, auth.sessao_usuario(hub.cfg, request.cookies.get(auth.COOKIE)))


def logado(request: Request) -> bool:
    return usuario(request) is not None


def eh_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    encaminhado = any(h in request.headers for h in ("x-forwarded-for", "forwarded", "tailscale-user-login", "cf-connecting-ip"))
    return host in ("127.0.0.1", "::1", "localhost") and not encaminhado


def exigir_login(request: Request) -> dict:
    u = usuario(request)
    if not u:
        raise HTTPException(401, "Faça login no hub")
    return u


def exigir_admin(request: Request) -> dict:
    u = exigir_login(request)
    if u["papel"] not in ("dono", "admin"):
        raise HTTPException(403, "Apenas administradores do hub")
    return u


def app_do_pedido(request: Request) -> str | None:
    """Chamada servidor→hub feita por um app plugado (cabeçalho X-Hub-App-Token)."""
    return identidade.app_do_token(hub.cfg, request.headers.get("x-hub-app-token", ""), list(hub.apps))


def https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


@app.middleware("http")
async def cabecalhos(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/teams/"):
        resp.headers["Content-Security-Policy"] = "frame-ancestors 'self' https://teams.microsoft.com https://*.teams.microsoft.com https://*.office.com https://*.cloud.microsoft"
    elif not request.url.path.startswith("/apps/"):
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


# ── páginas do hub ────────────────────────────────────────────
def pagina(nome: str) -> HTMLResponse:
    html = (STATIC / nome).read_text(encoding="utf-8").replace("{{NOME}}", hub.cfg["nome"])
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
async def portal(request: Request):
    if not auth.senha_definida(hub.cfg):
        return RedirectResponse("/configurar", 303)
    if not logado(request):
        return RedirectResponse("/entrar", 303)
    return pagina("portal.html")


@app.get("/notificacoes", include_in_schema=False)
async def pagina_notificacoes(request: Request):
    if not logado(request):
        return RedirectResponse("/entrar?volta=/notificacoes", 303)
    return pagina("notificacoes.html")


@app.get("/teams/aba", include_in_schema=False)
async def aba_teams():
    """Aba do app do Teams: um atalho para abrir o hub no navegador (o login do hub não roda dentro do Teams)."""
    base = notificacoes.cfg_org(hub.cfg)["endereco_publico"] or "/"
    return HTMLResponse(f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>STRAT·HUB</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f5f5f5}}
a{{background:#1e3a6e;color:#fff;padding:12px 22px;border-radius:8px;text-decoration:none;font-weight:600}}</style></head>
<body><div style="text-align:center"><h2>{hub.cfg.get('nome') or 'STRAT·HUB'}</h2><p>Suas notificações e ferramentas ficam no hub.</p>
<p><a href="{base}/notificacoes" target="_blank" rel="noopener">Abrir notificações no navegador</a></p></div></body></html>""")


@app.get("/entrar", include_in_schema=False)
async def pagina_login(request: Request):
    if not auth.senha_definida(hub.cfg):
        return RedirectResponse("/configurar", 303)
    return pagina("login.html")


@app.get("/configurar", include_in_schema=False)
async def pagina_setup(request: Request):
    if auth.senha_definida(hub.cfg):
        return RedirectResponse("/", 303)
    if not eh_local(request):
        return HTMLResponse("<h3 style='font-family:sans-serif'>Defina a senha do hub abrindo http://localhost nesta máquina.</h3>", 403)
    return pagina("setup.html")


@app.get("/hub-static/{arquivo:path}", include_in_schema=False)
async def estaticos(arquivo: str):
    f = (STATIC / arquivo).resolve()
    if not str(f).startswith(str(STATIC)) or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/health", include_in_schema=False)
async def health():
    return {"ok": True, "versao": __version__}


# ── API do hub ────────────────────────────────────────────────
@app.post("/api/hub/configurar")
async def api_setup(request: Request):
    if auth.senha_definida(hub.cfg) or not eh_local(request):
        raise HTTPException(403, "Senha já definida ou acesso não local")
    body = await request.json()
    senha, nome = str(body.get("senha", "")), str(body.get("nome", "")).strip()
    if len(senha) < 8:
        raise HTTPException(400, "A senha precisa ter pelo menos 8 caracteres")
    if nome:
        hub.cfg["nome"] = nome[:40]
    auth.definir_senha(hub.cfg, senha)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE, auth.criar_sessao(hub.cfg), httponly=True, samesite="lax", secure=https(request), max_age=int(hub.cfg["sessao_dias"] * 86400))
    return resp


@app.post("/api/hub/entrar")
async def api_login(request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    if auth.bloqueado(ip):
        raise HTTPException(429, "Muitas tentativas. Aguarde 15 minutos.")
    body = await request.json()
    email, senha = str(body.get("email", "")).strip(), str(body.get("senha", ""))
    uid = None
    if not email or email.lower() in ("dono", (hub.cfg.get("dono_email") or "").lower()):
        uid = "dono" if auth.verificar_senha(hub.cfg, senha) else None
    if uid is None and email:
        u = usuarios.autenticar(email, senha)
        uid = u["id"] if u else None
    if not uid:
        auth.registrar_falha(ip)
        auditoria.registrar(email or "dono", "Login recusado", ip=ip)
        await asyncio.sleep(0.8)
        raise HTTPException(401, "Usuário ou senha incorretos")
    auditoria.registrar(usuarios.obter(hub.cfg, uid), "Login", ip=ip)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE, auth.criar_sessao(hub.cfg, uid), httponly=True, samesite="lax", secure=https(request), max_age=int(hub.cfg["sessao_dias"] * 86400))
    return resp


@app.post("/api/hub/sair")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.post("/api/hub/desligar")
async def api_desligar(request: Request):
    """Desligamento ordenado (somente desta máquina): para os apps e encerra o hub."""
    if not eh_local(request):
        raise HTTPException(403, "Só pode ser desligado a partir desta máquina")
    await hub.runner.stop_all()
    if SERVIDOR is not None:
        SERVIDOR.should_exit = True
    return {"ok": True}


@app.get("/api/hub/info")
async def api_info(request: Request):
    u = exigir_login(request)
    return {"nome": hub.cfg["nome"], "subtitulo": hub.cfg.get("subtitulo", ""), "versao": __version__,
            "pasta_apps": str(config.APPS_DIR), "porta": hub.cfg["porta"], "host": hub.cfg["host"],
            "usuario": usuarios.publico(u), "admin": u["papel"] in ("dono", "admin")}


@app.get("/api/hub/eu")
async def api_eu(request: Request):
    return usuarios.publico(exigir_login(request)) | {"ambiente": usuarios.ambiente(hub.cfg)}


# ── notificações ──────────────────────────────────────────────
def _pessoa_do_app(request: Request, pessoa_id: str | None) -> dict:
    """Ferramenta (X-Hub-App-Token) pedindo em nome de uma pessoa, ou a própria pessoa logada."""
    if app_do_pedido(request):
        p = next((x for x in usuarios.todos(hub.cfg) if x["id"] == pessoa_id), None)
        if not p:
            raise HTTPException(404, "Pessoa não encontrada")
        return p
    return exigir_login(request)


@app.get("/api/hub/notificacoes")
async def api_notificacoes(request: Request, app: str | None = None, estado: str = "pendentes", pessoa: str | None = None):
    p = _pessoa_do_app(request, pessoa)
    itens = await asyncio.to_thread(notificacoes.da_pessoa, hub.cfg, p, app or None, estado if estado in ("pendentes", "lidas", "todas") else "pendentes")
    return {"itens": itens, "pendentes": sum(1 for i in itens if not i["lida"])}


@app.post("/api/hub/notificacoes/lidas")
async def api_notificacoes_lidas(request: Request):
    b = await request.json()
    p = _pessoa_do_app(request, b.get("pessoa"))
    chaves = [str(x) for x in (b.get("chaves") or [])][:500]
    return {"marcadas": notificacoes.marcar(p["id"], chaves, bool(b.get("lida", True)))}


@app.get("/api/hub/notificacoes/lidas")
async def api_notificacoes_lidas_desde(request: Request, pessoa: str | None = None, desde: float = 0):
    p = _pessoa_do_app(request, pessoa)
    return notificacoes.lidas_desde(p["id"], desde)


@app.get("/api/hub/notificacoes/preferencias")
async def api_pref(request: Request):
    return notificacoes.preferencias(hub.cfg, exigir_login(request)["id"])


@app.put("/api/hub/notificacoes/preferencias")
async def api_pref_salvar(request: Request):
    u = exigir_login(request)
    try:
        return notificacoes.salvar_preferencias(hub.cfg, u["id"], await request.json())
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/admin/notificacoes")
async def api_adm_notif(request: Request):
    exigir_admin(request)
    return notificacoes.cfg_org(hub.cfg) | {"categorias": notificacoes.CATEGORIAS, "canais": notificacoes.CANAIS,
                                             "disponivel": notificacoes.canais_disponiveis(hub.cfg), "envios": notificacoes.envios_recentes(60)}


@app.put("/api/admin/notificacoes")
async def api_adm_notif_salvar(request: Request):
    eu = exigir_admin(request)
    b = await request.json()
    try:
        r = notificacoes.salvar_cfg_org(hub.cfg, b)
    except ValueError as e:
        raise HTTPException(400, str(e))
    auditoria.registrar(eu, "Notificações: configuração alterada", "", {k: v for k, v in b.items() if k != "smtp"} | ({"smtp": "alterado"} if "smtp" in b else {}))
    return r


@app.post("/api/admin/notificacoes/teste")
async def api_adm_notif_teste(request: Request):
    eu = exigir_admin(request)
    if not eu.get("email"):
        raise HTTPException(400, "Cadastre o seu e-mail em Usuários para receber o teste.")
    try:
        txt, htm = notificacoes._corpo(hub.cfg, [{"titulo": "Teste de notificação do STRAT·HUB", "origem_nome": "STRAT·HUB", "link": "/"}], "Teste de envio")
        await asyncio.to_thread(notificacoes.enviar_smtp, hub.cfg, eu["email"], "[STRAT·HUB] Teste de notificação", txt, htm)
    except Exception as e:
        raise HTTPException(502, f"O servidor de e-mail recusou: {e}")
    return {"ok": True, "para": eu["email"]}


# ── usuários ──────────────────────────────────────────────────
@app.get("/api/hub/usuarios")
async def api_usuarios(request: Request):
    exigir_admin(request)
    return [usuarios.publico(usuarios.dono(hub.cfg))] + usuarios.listar()


@app.post("/api/hub/usuarios")
async def api_usuario_novo(request: Request):
    exigir_admin(request)
    b = await request.json()
    try:
        novo = usuarios.criar(b.get("nome", ""), b.get("email", ""), b.get("senha", ""), b.get("papel", "usuario"), b.get("apps") or ["*"],
                              b.get("localidade"), bool(b.get("escala_feriado")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    auditoria.registrar(usuario(request), "Usuário criado", novo["email"], {"papel": novo["papel"], "apps": novo["apps"]})
    return novo


def _checar_master(b: dict) -> None:
    if b.get("master_teste") and usuarios.ambiente(hub.cfg) != "teste":
        raise HTTPException(400, "MASTER TESTE só existe em ambiente de teste (hub.config.json: \"ambiente\": \"teste\").")


@app.put("/api/hub/usuarios/{uid}")
async def api_usuario_alterar(uid: str, request: Request):
    eu = exigir_admin(request)
    b = await request.json()
    if uid == "dono":
        if eu["papel"] != "dono":
            raise HTTPException(403, "Só o dono altera o próprio cadastro")
        antes = hub.cfg.get("dono_email") or ""
        em = str(b.get("email") if "email" in b else antes or "").strip().lower()[:120]
        if em and not usuarios.EMAIL_RE.match(em):
            raise HTTPException(400, "E-mail inválido")
        if em and any(x.get("email") == em for x in usuarios.listar()):
            raise HTTPException(400, "Já existe um usuário com esse e-mail")
        if "master_teste" in b:
            _checar_master(b)
        try:
            lider = usuarios.validar_lider(hub.cfg, "dono", b["lider_id"]) if "lider_id" in b else hub.cfg.get("dono_lider_id")
        except ValueError as e:
            raise HTTPException(400, str(e))
        hub.cfg["dono_lider_id"] = lider
        if "jornada_h" in b:
            hub.cfg["dono_jornada_h"] = usuarios._jornada(b["jornada_h"])
        hub.cfg["dono_nome"] = str(b.get("nome") or hub.cfg.get("dono_nome") or "Dono do hub")[:80]
        hub.cfg["dono_email"] = em
        if "localidade" in b:
            hub.cfg["dono_localidade"] = str(b.get("localidade") or "").strip()[:40] or None
        if "escala_feriado" in b:
            hub.cfg["dono_escala_feriado"] = bool(b["escala_feriado"])
        if "master_teste" in b:
            hub.cfg["dono_master_teste"] = bool(b["master_teste"])
        config.save(hub.cfg)
        auditoria.registrar(eu, "Usuário alterado", "dono", {k: v for k, v in b.items() if k != "senha"}
                            | ({"email_anterior": antes} if em != antes else {}))
        return usuarios.publico(usuarios.dono(hub.cfg))
    if "master_teste" in b:
        _checar_master(b)
    try:
        if "lider_id" in b:
            b["lider_id"] = usuarios.validar_lider(hub.cfg, uid, b["lider_id"])
        antes = next((x["email"] for x in usuarios.listar() if x["id"] == uid), None)
        r = usuarios.alterar(uid, b)
        auditoria.registrar(eu, "Usuário alterado", r["email"], {k: v for k, v in b.items() if k != "senha"} | ({"senha": "redefinida"} if b.get("senha") else {})
                            | ({"email_anterior": antes} if antes and antes != r["email"] else {}))
        return r
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/hub/usuarios/{uid}")
async def api_usuario_remover(uid: str, request: Request):
    eu = exigir_admin(request)
    try:
        afetados = usuarios.remover(uid)
    except KeyError as e:
        raise HTTPException(404, str(e))
    if hub.cfg.get("dono_lider_id") == uid:
        hub.cfg["dono_lider_id"] = None; config.save(hub.cfg)
    auditoria.registrar(eu, "Usuário removido", uid, {"liderados_passam_ao_lider_acima": afetados} if afetados else None)
    return {"ok": True}


# ── perfil da organização ─────────────────────────────────────
@app.get("/api/hub/organizacao")
async def api_org(request: Request):
    if not app_do_pedido(request):
        exigir_login(request)
    return organizacao.ler()


@app.put("/api/hub/organizacao")
async def api_org_gravar(request: Request):
    eu = exigir_admin(request)
    try:
        r = organizacao.gravar(await request.json())
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    auditoria.registrar(eu, "Perfil da organização salvo", r.get("nome", ""))
    return r


# ── serviços para as ferramentas: pessoas, feriados, ecossistema instalado ──
def _app_ou_login(request: Request) -> None:
    if not app_do_pedido(request):
        exigir_login(request)


@app.get("/api/hub/pessoas")
async def api_pessoas(request: Request):
    """Dados não sensíveis das pessoas (nome, e-mail, papel, localidade, escala em feriados) para as ferramentas."""
    _app_ou_login(request)
    campos = ("id", "nome", "email", "papel", "localidade", "escala_feriado", "ativo", "lider_id", "jornada_h")
    return [{k: u.get(k) for k in campos} | ({"master_teste": True} if usuarios.eh_master(hub.cfg, u) else {})
            for u in [usuarios.dono(hub.cfg)] + usuarios.listar()]


@app.get("/api/hub/feriados")
async def api_feriados(request: Request, inicio: str = "", fim: str = "", ano: int = 0, localidade: str = ""):
    _app_ou_login(request)
    org = organizacao.ler()
    if ano:
        return feriados.do_ano(org, ano, localidade or None)
    if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", inicio) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", fim)):
        raise HTTPException(400, "Informe ano ou inicio/fim (AAAA-MM-DD)")
    return feriados.periodo(org, inicio, fim, localidade or None)


@app.get("/api/hub/instaladas")
async def api_instaladas(request: Request):
    """Ferramentas instaladas e ativas (para as interfaces se adaptarem ao que existe)."""
    _app_ou_login(request)
    return [{"id": a.id, "nome": a.nome, "publica": a.publica, "consome": a.consome}
            for a in hub.apps.values() if a.ativo and not a.erro and not a.oculto]


# ── conectores: agir em nome de uma pessoa ────────────────────
@app.post("/api/hub/agir")
async def api_agir(request: Request):
    """Um conector (ex.: Microsoft) executa numa ferramenta a ação que a pessoa escolheu fora do hub (ex.: aprovou no Teams).
    Exige: manifesto com "agir_em_nome": true **e** autorização do administrador (Admin técnico › Ferramentas).
    A ferramenta de destino recebe a identidade da pessoa (assinada pelo hub) e confere a permissão como sempre;
    só rotas /api/ e só ferramentas que a pessoa pode usar. Tudo fica na auditoria."""
    origem = app_do_pedido(request)
    if not origem:
        raise HTTPException(401, "Só ferramentas plugadas")
    ag = hub.apps.get(origem)
    if not (ag and ag.agir_em_nome and ag.agir_liberado):
        raise HTTPException(403, f"{origem} não está autorizado a agir em nome das pessoas (Admin técnico › Ferramentas)")
    b = await request.json()
    destino, caminho, metodo = str(b.get("app") or ""), str(b.get("caminho") or ""), str(b.get("metodo") or "POST").upper()
    a = hub.apps.get(destino)
    if not a or not a.ativo or not a.is_process or a.oculto:
        raise HTTPException(404, "Ferramenta de destino indisponível")
    if not caminho.startswith("/api/") or ".." in caminho or metodo not in ("POST", "PUT", "PATCH", "DELETE"):
        raise HTTPException(400, "Só ações em rotas /api/")
    p = b.get("pessoa") or {}
    u = usuarios.obter(hub.cfg, str(p.get("id_hub") or "")) if p.get("id_hub") else None
    if not u and p.get("email"):
        e = str(p["email"]).strip().lower()
        u = usuarios.por_email(e) or (usuarios.dono(hub.cfg) if (hub.cfg.get("dono_email") or "").lower() == e else None)
    if not u or u.get("ativo") is False:
        raise HTTPException(404, "Pessoa não encontrada no hub")
    if not usuarios.pode_app(u, destino):
        raise HTTPException(403, "A pessoa não tem acesso a esta ferramenta")
    st = await hub.runner.ensure_running(a)
    if st.status != "ativo":
        raise HTTPException(503, f"Ferramenta indisponível: {st.mensagem}")
    url = f"http://127.0.0.1:{st.porta}{a.base if a.manter_prefixo else '/'}{caminho.lstrip('/')}"
    headers = identidade.cabecalhos(hub.cfg, destino, u) + [("content-type", "application/json"), ("x-hub-agente", origem)]
    try:
        r = await hub.client.request(metodo, url, headers=headers, content=json.dumps(b.get("corpo") or {}).encode())
    except httpx.HTTPError as e:
        raise HTTPException(502, f"A ferramenta não respondeu: {e}")
    auditoria.registrar(u, f"Ação executada via {ag.nome} em nome da pessoa", f"{destino}{caminho}", {"status": r.status_code, "metodo": metodo}, _ip(request))
    try:
        corpo = r.json()
    except ValueError:
        corpo = {"texto": r.text[:500]}
    return {"status": r.status_code, "resposta": corpo}


# ── Data Core (compatível com HUB PMO) ────────────────────────
def _ator_datacore(request: Request) -> tuple[str | None, dict | None]:
    app_id = app_do_pedido(request)
    if app_id:
        return app_id, None
    return None, exigir_login(request)


@app.get("/api/data-core/health")
async def dc_health(request: Request):
    _ator_datacore(request)
    return datacore.resumo()


@app.get("/api/data-core/records")
async def dc_listar(request: Request, domain: str | None = None, entity_type: str | None = None, entity_key: str | None = None,
                    period: str | None = None, since: float | None = None, include_deleted: bool = False, limit: int = 200, payload: bool = True):
    _ator_datacore(request)
    return datacore.listar(domain, entity_type, entity_key, period, since, include_deleted, limit, payload)


@app.get("/api/data-core/records/{rid}")
async def dc_obter(rid: int, request: Request):
    _ator_datacore(request)
    r = datacore.obter(rid)
    if not r:
        raise HTTPException(404, "Registro não encontrado")
    return r


@app.get("/api/data-core/records/{rid}/history")
async def dc_historico(rid: int, request: Request, limit: int = 100):
    _ator_datacore(request)
    return datacore.historico(rid, limit)


@app.post("/api/data-core/records")
async def dc_gravar(request: Request):
    app_id, u = _ator_datacore(request)
    b = await request.json()
    if not isinstance(b, dict):
        raise HTTPException(400, "Corpo inválido")
    origem = app_id or str(b.get("source_application") or "").strip()[:120]
    if not origem:
        raise HTTPException(400, "Informe source_application")
    quem = (u.get("email") or u.get("nome") or u["id"]) if u else str(b.get("source_user") or "")[:120]
    try:
        return datacore.gravar(domain=str(b.get("domain", "")), entity_type=str(b.get("entity_type", "")),
                               entity_key=str(b.get("entity_key", "")), period=str(b.get("period") or ""),
                               payload=b.get("payload"), source_application=origem, source_user=quem,
                               source_version=str(b.get("source_version") or "")[:120],
                               expected_version=b.get("expected_version"), excluir=bool(b.get("delete", False)))
    except datacore.Conflito as e:
        raise HTTPException(409, f"Conflito de versão: {e}")
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/hub/apps")
async def api_apps(request: Request):
    u = exigir_login(request)
    return [{**a.public_dict(), **hub.runner.snapshot(a)} for a in sorted(hub.apps.values(), key=lambda x: (x.ordem, x.categoria, x.nome))
            if usuarios.pode_app(u, a.id) and not a.oculto]


def _app_or_404(app_id: str) -> registry.AppDef:
    a = hub.apps.get(app_id)
    if not a:
        raise HTTPException(404, "App não encontrado")
    return a


@app.post("/api/hub/apps/{app_id}/{acao}")
async def api_acao(app_id: str, acao: str, request: Request):
    auditoria.registrar(exigir_admin(request), f"Ferramenta: {acao}", app_id)
    a = _app_or_404(app_id)
    if not a.is_process:
        raise HTTPException(400, "Apps estáticos não têm processo")
    if acao == "iniciar":
        if not a.ativo:
            raise HTTPException(400, a.erro or "App desativado")
        asyncio.create_task(hub.runner.ensure_running(a))
    elif acao == "parar":
        await hub.runner.stop(app_id)
    elif acao == "reiniciar":
        await hub.runner.stop(app_id, "reiniciando")
        asyncio.create_task(hub.runner.ensure_running(a))
    else:
        raise HTTPException(400, "Ação inválida")
    return {"ok": True}


@app.get("/api/hub/apps/{app_id}/logs")
async def api_logs(app_id: str, request: Request, linhas: int = 300):
    exigir_admin(request)
    _app_or_404(app_id)
    return {"log": hub.runner.tail(app_id, min(max(linhas, 10), 2000))}


@app.get("/api/hub/apps/{app_id}/estado")
async def api_estado(app_id: str, request: Request):
    a = _app_or_404(app_id)
    if not a.publico:
        exigir_login(request)
    s = hub.runner.snapshot(a)
    if s.get("status") == "erro":
        s["log"] = hub.runner.tail(app_id, 25)
    return s


@app.post("/api/hub/recarregar")
async def api_rescan(request: Request):
    exigir_admin(request)
    hub.rescan()
    return {"ok": True, "apps": list(hub.apps)}


PRESERVAR = {".venv", "node_modules", "dados", "data"}


@app.post("/api/hub/plugar")
async def api_upload(request: Request, arquivo: UploadFile = File(...), app_id: str = Form(""), substituir: bool = Form(False)):
    """Pluga um app a partir de um .zip (com hub.json na raiz ou numa única pasta)."""
    exigir_admin(request)
    data = await arquivo.read()
    if len(data) > 500 * 1024 * 1024:
        raise HTTPException(413, "Arquivo maior que 500 MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(400, "Arquivo não é um .zip válido")
    nomes = [n for n in z.namelist() if not n.startswith("__MACOSX/")]
    for n in nomes:
        p = Path(n)
        if p.is_absolute() or ".." in p.parts or ":" in n:
            raise HTTPException(400, f"Caminho inseguro no zip: {n}")
    prefixo = ""
    if "hub.json" not in nomes and "hub.manifest.json" not in nomes:
        manifests = sorted({n.rsplit("/", 1)[0] + "/" for n in nomes
                            if n.endswith(("/hub.json", "/hub.manifest.json")) and n.count("/") == 1})
        if len(manifests) != 1:
            raise HTTPException(400, "O zip precisa ter um hub.json (ou hub.manifest.json) na raiz ou dentro de uma única pasta")
        prefixo = manifests[0]
    alvo_id = (app_id or prefixo.strip("/") or Path(arquivo.filename or "app").stem).lower()
    alvo_id = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in alvo_id).strip("-_")[:48]
    if not registry.ID_RE.match(alvo_id):
        raise HTTPException(400, "Nome do app inválido")
    destino = config.APPS_DIR / alvo_id
    if destino.exists() and not substituir:
        raise HTTPException(409, f"Já existe um app '{alvo_id}'. Marque 'substituir' para atualizar.")
    # 1) extrai tudo numa pasta temporária (valida o zip antes de mexer na ferramenta instalada)
    temp = Path(tempfile.mkdtemp(prefix=f".novo-{alvo_id}-", dir=config.APPS_DIR))
    try:
        for n in nomes:
            if not n.startswith(prefixo) or n.endswith("/"):
                continue
            rel = n[len(prefixo):]
            if not rel:
                continue
            out = (temp / rel).resolve()
            if not str(out).startswith(str(temp.resolve())):
                raise HTTPException(400, "Caminho inseguro")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(n))
        anterior = None
        if destino.exists():
            # 2) para o app e troca os arquivos com o app travado (ninguém o reinicia no meio da troca)
            if hub.runner.alive(alvo_id):
                await hub.runner.stop(alvo_id, "atualizando pelo portal")
            st = hub.runner.state(alvo_id)
            async with st.lock:
                await asyncio.sleep(1.0)  # Windows: dá tempo de o processo liberar os arquivos
                try:
                    anterior = await asyncio.to_thread(admin.substituir_arquivos, destino, temp, PRESERVAR)
                except RuntimeError as e:
                    raise HTTPException(409, str(e))
        else:
            temp.rename(destino)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    hub.rescan()
    a = hub.apps.get(alvo_id)
    auditoria.registrar(usuario(request), "Ferramenta plugada por .zip", alvo_id, {"arquivo": arquivo.filename, "substituir": substituir, "versao_anterior": anterior})
    return {"ok": True, "id": alvo_id, "erro": a.erro if a else "não reconhecido", "url": f"/apps/{alvo_id}/"}


# ── Admin técnico ─────────────────────────────────────────────
@app.get("/admin", include_in_schema=False)
async def pagina_admin(request: Request):
    u = usuario(request)
    if not u:
        return RedirectResponse("/entrar?volta=/admin", 303)
    if u["papel"] not in ("dono", "admin"):
        return HTMLResponse("<h3 style='font-family:sans-serif'>Área restrita a administradores do hub.</h3>", 403)
    return pagina("admin.html")


def _ip(request: Request) -> str:
    return request.headers.get("x-forwarded-for", request.client.host if request.client else "").split(",")[0].strip()


@app.get("/api/admin/visao")
async def adm_visao(request: Request):
    exigir_admin(request)
    apps = [{**a.public_dict(), **hub.runner.snapshot(a)} for a in hub.apps.values()]
    mem = sum(a.get("memoria_mb") or 0 for a in apps)
    dc = datacore.resumo()
    mp = admin.mapa(hub.apps)
    bks = admin.listar_backups()
    disco = shutil.disk_usage(config.DATA_DIR)
    alertas = []
    for a in apps:
        if a.get("erro") or a.get("status") == "erro":
            alertas.append({"nivel": "erro", "texto": f"{a['nome']}: {a.get('erro') or a.get('mensagem')}", "link": "#ferramentas"})
    for c in mp["contratos"]:
        if c["situacao"] == "sem_produtor":
            alertas.append({"nivel": "aviso", "texto": f"Fluxo sem origem: {', '.join(c['consome'])} espera {c['contrato']}, mas nenhuma ferramenta ativa publica.", "link": "#ecossistema"})
    if disco.free < 5e9:
        alertas.append({"nivel": "aviso", "texto": f"Pouco espaço em disco: {disco.free / 1e9:.1f} GB livres.", "link": "#infra"})
    if not bks or time.time() - (config.DATA_DIR / "backups" / bks[0]["nome"]).stat().st_mtime > 3 * 86400:
        alertas.append({"nivel": "aviso", "texto": "Nenhum backup nos últimos 3 dias.", "link": "#infra"})
    return {"ferramentas": {"total": len(apps), "ativas": sum(1 for a in apps if a.get("status") == "ativo"),
                            "erro": sum(1 for a in apps if a.get("status") == "erro" or a.get("erro")), "ocultas": sum(1 for a in apps if a["oculto"]),
                            "memoria_mb": mem},
            "datacore": {"registros": dc["records"], "historico": dc["history"], "tipos": len(dc["tipos"])},
            "usuarios": len(usuarios.listar()) + 1, "backup": bks[0] if bks else None, "lixeira": len(admin.listar_lixeira()),
            "alertas": alertas, "eventos": auditoria.listar(12), "versao": __version__, "ligado_desde": hub.inicio}


@app.get("/api/admin/ferramentas")
async def adm_ferramentas(request: Request):
    exigir_admin(request)
    aj = admin.ler_ajustes()
    return [{**a.public_dict(), **hub.runner.snapshot(a), "ajustes": aj.get(a.id, {}), "pasta": str(a.dir), "requisitos": a.requisitos}
            for a in sorted(hub.apps.values(), key=lambda x: (x.ordem, x.categoria, x.nome))]


@app.put("/api/admin/ferramentas/{app_id}")
async def adm_ajustar(app_id: str, request: Request):
    eu = exigir_admin(request)
    _app_or_404(app_id)
    dados = await request.json()
    try:
        r = admin.gravar_ajuste(app_id, dados)
    except (TypeError, ValueError):
        raise HTTPException(400, "Valor inválido")
    hub.rescan()
    auditoria.registrar(eu, "Ferramenta ajustada", app_id, dados, _ip(request))
    return r


@app.post("/api/admin/ferramentas/{app_id}/remover")
async def adm_remover(app_id: str, request: Request):
    eu = exigir_admin(request)
    _app_or_404(app_id)
    if hub.runner.alive(app_id):
        await hub.runner.stop(app_id, "removido para a lixeira")
    await asyncio.sleep(0.5)
    try:
        pasta = await asyncio.to_thread(admin.remover_para_lixeira, app_id)
    except (OSError, FileNotFoundError) as e:
        raise HTTPException(409, f"Não foi possível mover a pasta: {e}")
    hub.rescan()
    auditoria.registrar(eu, "Ferramenta removida para a lixeira", app_id, {"pasta": pasta}, _ip(request))
    return {"ok": True, "pasta": pasta}


@app.get("/api/admin/lixeira")
async def adm_lixeira(request: Request):
    exigir_admin(request)
    return admin.listar_lixeira()


@app.post("/api/admin/lixeira/{pasta}/restaurar")
async def adm_restaurar(pasta: str, request: Request):
    eu = exigir_admin(request)
    try:
        app_id = await asyncio.to_thread(admin.restaurar, pasta)
    except (ValueError, FileNotFoundError, FileExistsError) as e:
        raise HTTPException(409, str(e))
    hub.rescan()
    auditoria.registrar(eu, "Ferramenta restaurada da lixeira", app_id, _ip(request))
    return {"ok": True, "id": app_id}


@app.delete("/api/admin/lixeira/{pasta}")
async def adm_excluir(pasta: str, request: Request):
    eu = exigir_admin(request)
    try:
        await asyncio.to_thread(admin.excluir_definitivo, pasta)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))
    auditoria.registrar(eu, "Ferramenta excluída definitivamente", pasta, ip=_ip(request))
    return {"ok": True}


@app.get("/api/admin/dependencias")
async def adm_deps(request: Request):
    exigir_admin(request)
    return admin.checar_dependencias(hub.apps)


@app.get("/api/admin/ferramentas/{app_id}/pacotes")
async def adm_pacotes(app_id: str, request: Request):
    exigir_admin(request)
    a = _app_or_404(app_id)
    py = hub.runner.venv_python(a)
    if a.tipo != "python" or not py.exists():
        return {"ambiente": False, "pacotes": []}
    return {"ambiente": True, "pacotes": await asyncio.to_thread(admin.pacotes_python, py)}


@app.post("/api/admin/ferramentas/{app_id}/reinstalar")
async def adm_reinstalar(app_id: str, request: Request):
    eu = exigir_admin(request)
    a = _app_or_404(app_id)
    for m in (a.dir / ".venv" / ".hub_requirements.sha256", a.dir / "node_modules" / ".hub_package.sha256"):
        m.unlink(missing_ok=True)
    await hub.runner.stop(app_id, "reinstalando dependências")
    asyncio.create_task(hub.runner.ensure_running(a))
    auditoria.registrar(eu, "Dependências reinstaladas", app_id, ip=_ip(request))
    return {"ok": True}


@app.get("/api/admin/datacore")
async def adm_datacore(request: Request, domain: str | None = None, entity_type: str | None = None, busca: str = "", limite: int = 300):
    exigir_admin(request)
    regs = datacore.listar(domain, entity_type, None, None, None, True, 5000 if busca else limite, False)
    if busca:
        b = busca.lower()
        regs = [r for r in regs if b in (r["entity_key"] + " " + r["source_application"] + " " + r["source_user"]).lower()][:limite]
    return {"resumo": datacore.resumo(), "registros": regs}


@app.get("/api/admin/logs/{alvo}")
async def adm_logs(alvo: str, request: Request, linhas: int = 500, busca: str = ""):
    exigir_admin(request)
    if alvo == "hub":
        f = config.DATA_DIR / "hub.log"
        txt = "\n".join(f.read_text(encoding="utf-8", errors="replace").splitlines()[-linhas:]) if f.exists() else "(o hub registra em arquivo quando inicia com o Windows; rodando em janela, o log fica na própria janela)"
    else:
        _app_or_404(alvo)
        txt = hub.runner.tail(alvo, min(max(linhas, 10), 20000))
    if busca:
        txt = "\n".join(l for l in txt.splitlines() if busca.lower() in l.lower())
    return {"log": txt}


@app.get("/api/admin/auditoria")
async def adm_auditoria(request: Request, busca: str = "", limite: int = 500, formato: str = "json"):
    exigir_admin(request)
    evs = auditoria.listar(min(limite, 20000), busca)
    if formato == "csv":
        import csv
        buf = io.StringIO(); w = csv.writer(buf, delimiter=";")
        w.writerow(["em", "por", "acao", "alvo", "ip", "detalhes"])
        for e in evs:
            w.writerow([e["em"], e["por"], e["acao"], e["alvo"], e.get("ip", ""), json.dumps(e["detalhes"], ensure_ascii=False) if e["detalhes"] else ""])
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="auditoria.csv"'})
    return evs


@app.get("/api/admin/infra")
async def adm_infra(request: Request):
    exigir_admin(request)
    info = await asyncio.to_thread(admin.infraestrutura, hub.cfg, hub.inicio)
    info["portas"] = [{"id": a.id, "nome": a.nome, "porta": hub.runner.state(a.id).porta, "status": hub.runner.snapshot(a).get("status")} for a in hub.apps.values() if a.is_process]
    info["backups"] = admin.listar_backups()
    info["backup_diario"], info["backup_manter"] = bool(hub.cfg.get("backup_diario", True)), int(hub.cfg.get("backup_manter", 14))
    return info


@app.post("/api/admin/backups")
async def adm_backup(request: Request):
    eu = exigir_admin(request)
    r = await asyncio.to_thread(admin.criar_backup, "manual")
    auditoria.registrar(eu, "Backup manual", r["nome"], ip=_ip(request))
    return r


@app.get("/api/admin/backups/{nome}")
async def adm_baixar_backup(nome: str, request: Request):
    exigir_admin(request)
    try:
        return FileResponse(admin.caminho_backup(nome), filename=nome)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))


CONFIG_EDITAVEL = {"nome": str, "subtitulo": str, "ocioso_min_padrao": float, "sessao_dias": float, "inicio_timeout_s": float,
                   "backup_diario": bool, "backup_manter": int}


@app.get("/api/admin/config")
async def adm_config(request: Request):
    exigir_admin(request)
    return {**{k: hub.cfg.get(k) for k in CONFIG_EDITAVEL}, "host": hub.cfg.get("host"), "porta": hub.cfg.get("porta"),
            "porta_inicial_apps": hub.cfg.get("porta_inicial_apps"), "ambiente": usuarios.ambiente(hub.cfg), "backup_diario": bool(hub.cfg.get("backup_diario", True)),
            "backup_manter": int(hub.cfg.get("backup_manter", 14))}


@app.put("/api/admin/config")
async def adm_config_gravar(request: Request):
    eu = exigir_admin(request)
    b = await request.json()
    mud = {}
    for k, t in CONFIG_EDITAVEL.items():
        if k in b and b[k] not in (None, ""):
            try:
                v = t(b[k])
            except (TypeError, ValueError):
                raise HTTPException(400, f"Valor inválido para {k}")
            if isinstance(v, str):
                v = v.strip()[:60]
            hub.cfg[k] = v; mud[k] = v
    config.save(hub.cfg)
    auditoria.registrar(eu, "Configurações do hub", "", mud, _ip(request))
    return {"ok": True}


@app.get("/api/admin/mapa")
async def adm_mapa(request: Request):
    exigir_admin(request)
    return admin.mapa(hub.apps)


@app.get("/api/admin/ecossistema/exportar")
async def adm_eco_exportar(request: Request, nome: str = "Ecossistema"):
    eu = exigir_admin(request)
    eco = admin.exportar_ecossistema(hub.apps, nome)
    auditoria.registrar(eu, "Ecossistema exportado", nome, ip=_ip(request))
    arq = re.sub(r"[^\w-]+", "-", nome.lower()).strip("-") or "ecossistema"
    return Response(json.dumps(eco, indent=2, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="ecossistema-{arq}.json"'})


MODELOS_ECO = Path(__file__).resolve().parent / "modelos"


@app.get("/api/admin/ecossistema/modelos")
async def adm_eco_modelos(request: Request):
    """Modelos de ecossistema que acompanham o hub (Projetos, Serviços…)."""
    exigir_admin(request)
    out = []
    for f in sorted(MODELOS_ECO.glob("ecossistema-*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        ids = [x.get("id") for x in d.get("ferramentas") or []]
        out.append({"arquivo": f.name, "nome": d.get("nome"), "descricao": d.get("descricao", ""), "ferramentas": ids,
                    "faltando": [i for i in ids if i not in hub.apps]})
    return out


@app.post("/api/admin/ecossistema/modelos/{arquivo}")
async def adm_eco_aplicar_modelo(arquivo: str, request: Request):
    if not re.fullmatch(r"ecossistema-[\w-]+\.json", arquivo) or not (MODELOS_ECO / arquivo).is_file():
        raise HTTPException(404, "Modelo não encontrado")
    eu = exigir_admin(request)
    r = admin.importar_ecossistema(hub.apps, json.loads((MODELOS_ECO / arquivo).read_text(encoding="utf-8")), False)
    hub.rescan()
    for app_id in r["configuradas"]:
        if hub.runner.alive(app_id):
            await hub.runner.stop(app_id, "modelo de ecossistema aplicado")
    auditoria.registrar(eu, "Modelo de ecossistema aplicado", arquivo, r, _ip(request))
    return r


@app.post("/api/admin/ecossistema/importar")
async def adm_eco_importar(request: Request, organizacao_tambem: bool = True):
    eu = exigir_admin(request)
    try:
        r = admin.importar_ecossistema(hub.apps, await request.json(), organizacao_tambem)
    except ValueError as e:
        raise HTTPException(400, str(e))
    hub.rescan()
    for app_id in r["configuradas"]:  # reinicia para aplicar a configuração
        if hub.runner.alive(app_id):
            await hub.runner.stop(app_id, "configuração do ecossistema aplicada")
    auditoria.registrar(eu, "Ecossistema importado", "", r, _ip(request))
    return r


# ── apps plugados ─────────────────────────────────────────────
PAGINA_INICIANDO = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Iniciando {nome}…</title><link rel="stylesheet" href="/hub-static/hub.css"></head>
<body class="iniciando"><div class="ini-card"><div class="ini-icone">{icone}</div><h1>{nome}</h1>
<p id="msg">Iniciando o app… na primeira vez as dependências são instaladas, o que pode levar alguns minutos.</p>
<div class="barra"><span></span></div><pre id="log" hidden></pre><a href="/" class="link">← Voltar ao portal</a></div>
<script>
const id={id_json};
async function t(){{try{{const r=await fetch('/api/hub/apps/'+id+'/estado');const s=await r.json();
if(s.status==='ativo'){{location.reload();return}}
if(s.status==='erro'){{document.getElementById('msg').textContent='Não foi possível iniciar: '+(s.mensagem||'erro');const l=document.getElementById('log');l.hidden=false;l.textContent=s.log||'';document.body.classList.add('falhou');return}}
document.getElementById('msg').textContent=s.status==='instalando'?'Instalando dependências (só na primeira vez)…':'Iniciando o app…';}}catch(e){{}}setTimeout(t,900)}}t();
</script></body></html>"""


def _autorizado_app(request_or_ws, a: registry.AppDef, caminho: str = "") -> bool:
    """Rotas públicas (manifesto: strathub.rotas_publicas) dispensam login do hub — o app autentica por conta própria
    (ex.: entrada de webhooks com chave por integração)."""
    if a.publico or any((caminho + "/").startswith(p) for p in a.rotas_publicas):
        return True
    return usuarios.pode_app(usuario(request_or_ws), a.id)


@app.api_route("/apps/{app_id}", methods=["GET", "HEAD"], include_in_schema=False)
async def app_sem_barra(app_id: str, request: Request):
    q = ("?" + request.url.query) if request.url.query else ""
    return RedirectResponse(f"/apps/{app_id}/{q}", 307)


@app.api_route("/apps/{app_id}/{caminho:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"], include_in_schema=False)
async def app_proxy(app_id: str, caminho: str, request: Request):
    a = hub.apps.get(app_id)
    if not a or not a.ativo or (a.oculto and not ((usuario(request) or {}).get("papel") in ("dono", "admin"))):
        raise HTTPException(404, "App não encontrado ou desativado")
    if not _autorizado_app(request, a, caminho):
        if logado(request):
            raise HTTPException(403, "Seu usuário não tem acesso a esta ferramenta")
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(f"/entrar?volta={request.url.path}", 303)
        raise HTTPException(401, "Faça login no hub")
    if a.tipo == "static":
        return servir_estatico(a, caminho, request)

    st = hub.runner.state(app_id)
    st.ultimo_acesso = time.time()
    if not (st.status == "ativo" and hub.runner.alive(app_id)):
        navegacao = request.method == "GET" and "text/html" in request.headers.get("accept", "")
        if navegacao:
            if st.status not in ("instalando", "iniciando"):
                asyncio.create_task(hub.runner.ensure_running(a))
            import json as _j
            return HTMLResponse(PAGINA_INICIANDO.format(nome=a.nome, icone=a.icone, id_json=_j.dumps(a.id)), 503, headers={"Retry-After": "2", "Cache-Control": "no-store"})
        st = await hub.runner.ensure_running(a)
        if st.status != "ativo":
            raise HTTPException(503, f"App indisponível: {st.mensagem}")
    return await proxy_http(a, st.porta, caminho, request)


def _injetar_botao(a: registry.AppDef, corpo: bytes) -> bytes:
    """Garante em toda página de ferramenta um caminho de volta (início da ferramenta e portal) e a tela de notificações."""
    import html as _h
    tag = (f'<script src="/hub-static/navhub.js" data-app="{_h.escape(a.id)}" data-nome="{_h.escape(a.nome)}" data-base="{_h.escape(a.base.rstrip("/"))}" '
           f'data-notif="{1 if "agenda/pendencia" in a.publica else 0}" defer></script>').encode()
    i = corpo.lower().rfind(b"</body>")
    return corpo[:i] + tag + corpo[i:] if i >= 0 else corpo + tag


def servir_estatico(a: registry.AppDef, caminho: str, request: Request | None = None):
    raiz = a.static_root()
    alvo = (raiz / caminho).resolve() if caminho else raiz / "index.html"
    if not str(alvo).startswith(str(raiz)):
        raise HTTPException(404)
    if alvo.is_dir():
        alvo = alvo / "index.html"
    if not alvo.is_file():
        ultimo = caminho.rsplit("/", 1)[-1]
        if a.spa and "." not in ultimo:
            alvo = raiz / "index.html"
        else:
            raise HTTPException(404)
    cache = "no-cache" if alvo.suffix in (".html", ".json", ".webmanifest") or alvo.name == "sw.js" else "public, max-age=86400"
    if alvo.suffix == ".html" and a.botao_hub and request is not None and logado(request):
        return Response(_injetar_botao(a, alvo.read_bytes()), media_type="text/html; charset=utf-8", headers={"Cache-Control": cache})
    return FileResponse(alvo, headers={"Cache-Control": cache})


def _reescrever_location(a: registry.AppDef, porta: int, loc: str) -> str:
    for interno in (f"http://127.0.0.1:{porta}", f"http://localhost:{porta}"):
        if loc.startswith(interno):
            loc = loc[len(interno):] or "/"
    if loc.startswith("/") and not loc.startswith("//") and not loc.startswith(a.base.rstrip("/")):
        return a.base + loc.lstrip("/")
    return loc


async def proxy_http(a: registry.AppDef, porta: int, caminho: str, request: Request):
    url = f"http://127.0.0.1:{porta}{a.base if a.manter_prefixo else '/'}{caminho}"
    if request.url.query:
        url += "?" + request.url.query
    # cabeçalhos de identidade só podem vir do hub
    headers = [(k, v) for k, v in request.headers.items() if k.lower() not in HOP and not k.lower().startswith("x-hub-")]
    u = usuario(request)
    if u:
        headers += identidade.cabecalhos(hub.cfg, a.id, u)
    pagina_html = bool(u) and a.botao_hub and request.method == "GET" and "text/html" in request.headers.get("accept", "")
    if pagina_html:  # a página volta sem compressão para o hub acrescentar o botão
        headers = [(k, v) for k, v in headers if k.lower() != "accept-encoding"] + [("accept-encoding", "identity")]
    # não repassa a sessão do hub para o app
    cookies = "; ".join(c.strip() for c in request.headers.get("cookie", "").split(";") if c.strip() and not c.strip().startswith(auth.COOKIE + "="))
    headers = [(k, v) for k, v in headers if k.lower() != "cookie"] + ([("cookie", cookies)] if cookies else [])
    cliente = request.client.host if request.client else ""
    headers += [("x-forwarded-prefix", a.base.rstrip("/")), ("x-forwarded-host", request.headers.get("host", "")),
                ("x-forwarded-proto", "https" if https(request) else "http"),
                ("x-forwarded-for", request.headers.get("x-forwarded-for", cliente))]
    req = hub.client.build_request(request.method, url, headers=headers, content=request.stream())
    try:
        r = await hub.client.send(req, stream=True)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"O app não respondeu: {e}")
    out_headers = []
    for k, v in r.headers.multi_items():
        kl = k.lower()
        if kl in HOP:
            continue
        if kl == "location":
            v = _reescrever_location(a, porta, v)
        if kl == "set-cookie":  # isola cookies de cada app no seu caminho
            v = v.replace("Path=/;", f"Path={a.base};").replace("path=/;", f"Path={a.base};")
            if v.rstrip().endswith(("Path=/", "path=/")):
                v = v.rstrip()[:-6] + f"Path={a.base}"
        out_headers.append((k, v))
    if pagina_html and r.headers.get("content-type", "").startswith("text/html") and not r.headers.get("content-encoding"):
        corpo = await r.aread()
        await r.aclose()
        resp = Response(_injetar_botao(a, corpo), status_code=r.status_code)
        resp.raw_headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in out_headers if k.lower() != "content-length"] + \
                           [(b"content-length", str(len(resp.body)).encode())]
        return resp
    resp = StreamingResponse(r.aiter_raw(), status_code=r.status_code, background=BackgroundTask(r.aclose))
    resp.raw_headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in out_headers]
    return resp


@app.websocket("/apps/{app_id}/{caminho:path}")
async def app_ws(ws: WebSocket, app_id: str, caminho: str):
    import websockets
    a = hub.apps.get(app_id)
    if not a or not a.ativo or not a.is_process or not _autorizado_app(ws, a):
        await ws.close(code=4403)
        return
    st = await hub.runner.ensure_running(a)
    if st.status != "ativo":
        await ws.close(code=1011)
        return
    url = f"ws://127.0.0.1:{st.porta}{a.base if a.manter_prefixo else '/'}{caminho}" + (f"?{ws.url.query}" if ws.url.query else "")
    protos = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()] or None
    try:
        async with websockets.connect(url, subprotocols=protos, open_timeout=15, max_size=None) as up:
            await ws.accept(subprotocol=up.subprotocol)

            async def cliente_para_app():
                while True:
                    m = await ws.receive()
                    if m["type"] == "websocket.disconnect":
                        await up.close()
                        return
                    st.ultimo_acesso = time.time()
                    if m.get("text") is not None:
                        await up.send(m["text"])
                    elif m.get("bytes") is not None:
                        await up.send(m["bytes"])

            async def app_para_cliente():
                async for m in up:
                    if isinstance(m, str):
                        await ws.send_text(m)
                    else:
                        await ws.send_bytes(m)
                await ws.close()

            tarefas = [asyncio.create_task(cliente_para_app()), asyncio.create_task(app_para_cliente())]
            _, pend = await asyncio.wait(tarefas, return_when=asyncio.FIRST_COMPLETED)
            for t in pend:
                t.cancel()
    except Exception:
        try:
            await ws.close(code=1011)
        except Exception:
            pass
