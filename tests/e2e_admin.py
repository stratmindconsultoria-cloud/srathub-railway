"""Admin técnico do STRAT·HUB 1.3 — teste de ponta a ponta (hub real em pasta temporária).
Uso: .venv/bin/python tests/e2e_admin.py
"""
import hashlib, hmac, json, os, subprocess, sys, tempfile, time, zipfile, io
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="strathub-adm-"))
DATA, APPS = TMP / "data", TMP / "apps"
DATA.mkdir(); APPS.mkdir()
PORT = 8812
(DATA / "hub.config.json").write_text(json.dumps({"porta": PORT, "porta_inicial_apps": 9400}))
# dois apps estáticos: um normal e um com rota pública
for app_id, extra in (("alfa", {}), ("beta", {"rotas_publicas": ["publico"]})):
    d = APPS / app_id; (d / "public" / "publico").mkdir(parents=True)
    (d / "public" / "index.html").write_text(f"<h1>{app_id}</h1>")
    (d / "public" / "publico" / "x.html").write_text("ok-publico")
    (d / "hub.json").write_text(json.dumps({"nome": app_id.title(), "tipo": "static", "pasta": "public", **extra}))
# 1.3: conector que age em nome das pessoas (sem requirements: usa o Python do hub) e ferramenta de destino
for app_id, extra in (("conector", {"agir_em_nome": True}), ("alvo", {})):
    d = APPS / app_id; d.mkdir()
    (d / "hub.json").write_text(json.dumps({"nome": app_id.title(), "tipo": "python", "health": "/health", **extra}))
    (d / "app.py").write_text("""from fastapi import FastAPI, Request
app = FastAPI()
@app.get('/health')
def h(): return {'ok': True}
@app.post('/api/acao')
async def acao(request: Request):
    return {'quem': request.headers.get('x-hub-user-email'), 'agente': request.headers.get('x-hub-agente'), 'assinado': bool(request.headers.get('x-hub-signature')), 'corpo': await request.json()}
""")
srv = subprocess.Popen([sys.executable, "-m", "hub"], cwd=ROOT, env={**os.environ, "STRATHUB_DATA": str(DATA), "STRATHUB_APPS": str(APPS)},
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
B = f"http://localhost:{PORT}"
c, anon = httpx.Client(base_url=B, timeout=60), httpx.Client(base_url=B, timeout=60)
passos = []


def ok(desc, cond, extra=""):
    passos.append(bool(cond))
    print(("  ✓ " if cond else "  ✗ ") + desc + (f"  [{extra}]" if extra and not cond else ""))


try:
    for _ in range(60):
        try:
            if c.get("/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.3)
    c.post("/api/hub/configurar", json={"senha": "senha-forte-1", "nome": "Hub Admin"})
    ok("admin exige login", anon.get("/api/admin/visao").status_code == 401)
    v = c.get("/api/admin/visao").json()
    ok("visão geral", v.get("versao", "").startswith("1.2") or "ferramentas" in json.dumps(v), str(v)[:200])
    ok("rota pública dispensa login do hub", anon.get("/apps/beta/publico/x.html").text == "ok-publico")
    ok("resto do app continua protegido", anon.get("/apps/beta/", headers={"accept": "application/json"}).status_code == 401)
    ok("app sem rota pública protegido", anon.get("/apps/alfa/publico/x.html", headers={"accept": "application/json"}).status_code == 401)
    c.put("/api/admin/ferramentas/alfa", json={"oculto": True, "nome": "Alfa Renomeado", "ordem": 1})
    ids = [a["id"] for a in c.get("/api/hub/apps").json()]
    ok("ocultar tira do portal", "alfa" not in ids and "beta" in ids)
    adm = {a["id"]: a for a in c.get("/api/admin/ferramentas").json()}
    ok("admin vê o oculto com o nome ajustado", adm["alfa"]["oculto"] and adm["alfa"]["nome"] == "Alfa Renomeado")
    c.put("/api/admin/ferramentas/alfa", json={"oculto": False})
    r = c.post("/api/admin/ferramentas/alfa/remover").json()
    lix = c.get("/api/admin/lixeira").json()
    ok("remover leva à lixeira com os dados", any(x["id"] == "alfa" for x in lix) and not (APPS / "alfa").exists())
    pasta = next(x["pasta"] for x in lix if x["id"] == "alfa")
    c.post(f"/api/admin/lixeira/{pasta}/restaurar")
    ok("restaurar devolve a ferramenta", (APPS / "alfa").exists() and "alfa" in [a["id"] for a in c.get("/api/hub/apps").json()])
    b = c.post("/api/admin/backups", json={}).json()
    nome = b.get("nome") or b.get("arquivo")
    z = c.get(f"/api/admin/backups/{nome}")
    ok("backup manual gerado e baixado", z.status_code == 200 and zipfile.ZipFile(io.BytesIO(z.content)).namelist())
    ok("modelos de ecossistema listados (com ferramentas faltando)", {m["nome"] for m in c.get("/api/admin/ecossistema/modelos").json()} >= {"Ecossistema de Projetos", "Ecossistema de Serviços"})
    eco = c.get("/api/admin/ecossistema/exportar?nome=Teste").json()
    ok("exportar ecossistema", eco["formato"] == "strathub-ecossistema/1" and {f["id"] for f in eco["ferramentas"]} >= {"alfa", "beta"})
    imp = c.post("/api/admin/ecossistema/importar?organizacao_tambem=false", json={**eco, "ferramentas": eco["ferramentas"] + [{"id": "gama", "nome": "Gama"}]}).json()
    ok("importar aponta ferramentas ausentes", [a["id"] for a in imp["ausentes"]] == ["gama"])
    csv = c.get("/api/admin/auditoria?formato=csv")
    ok("auditoria exportável em CSV", csv.status_code == 200 and "Ecossistema importado" in csv.text)
    ok("infraestrutura responde", c.get("/api/admin/infra").status_code == 200)
    mp = c.get("/api/admin/mapa").json()
    ok("mapa responde", "contratos" in mp)
    ok("mapa: lado que falta é opcional, não erro", all(x.get("situacao") in ("ok", "opcional", "atencao") for x in mp["contratos"]), str(mp["contratos"])[:200])
    # 1.3 — localidades, feriados, escala por pessoa e serviços para as ferramentas
    org = c.get("/api/hub/organizacao").json()
    org["localidades"] = [{"codigo": "SP", "nome": "São Paulo", "feriados": [{"data": "01-25", "nome": "Aniversário da cidade"}, {"data": "2026-07-09", "nome": "Revolução de 1932"}]}]
    org["unidades"] = [{"codigo": "U1", "nome": "Unidade Sul", "localidade": "SP"}]
    r = c.put("/api/hub/organizacao", json=org)
    ok("organização grava localidades e feriados locais", r.status_code == 200 and r.json()["localidades"][0]["feriados"][0]["data"] == "01-25", r.text[:200])
    fs = {f["data"]: f for f in c.get("/api/hub/feriados?ano=2026&localidade=SP").json()}
    ok("feriados: nacionais móveis + locais da localidade", fs["2026-04-03"]["conta"] and fs["2026-01-25"]["tipo"] == "local" and "2026-07-09" in fs and not fs["2026-02-16"]["conta"])
    ok("feriados por período", [f["data"] for f in c.get("/api/hub/feriados?inicio=2026-11-01&fim=2026-11-30").json() if f["conta"]] == ["2026-11-02", "2026-11-15", "2026-11-20"])
    nu = c.post("/api/hub/usuarios", json={"nome": "Téc Escala", "email": "tec@x.com", "senha": "senha-forte-9", "localidade": "SP", "escala_feriado": True})
    ok("usuário com localidade e escala de feriado", nu.status_code == 200 and nu.json()["escala_feriado"] is True and nu.json()["localidade"] == "SP", nu.text[:200])
    ed = c.put(f"/api/hub/usuarios/{nu.json()['id']}", json={"nome": "Téc Escala 2", "email": "tec2@x.com"})
    ok("editar nome e e-mail de um usuário", ed.status_code == 200 and ed.json()["email"] == "tec2@x.com" and ed.json()["nome"] == "Téc Escala 2", ed.text[:200])
    c.put(f"/api/hub/usuarios/{nu.json()['id']}", json={"nome": "Téc Escala", "email": "tec@x.com"})
    ok("e-mail do dono: inválido ou repetido é recusado", c.put("/api/hub/usuarios/dono", json={"email": "xx"}).status_code == 400
       and c.put("/api/hub/usuarios/dono", json={"email": "tec@x.com"}).status_code == 400)
    ok("produção é o padrão: MASTER TESTE não existe", c.get("/api/hub/eu").json()["ambiente"] == "producao"
       and c.put(f"/api/hub/usuarios/{nu.json()['id']}", json={"master_teste": True}).status_code == 400
       and c.put("/api/hub/usuarios/dono", json={"master_teste": True}).status_code == 400
       and not any("master_teste" in p for p in c.get("/api/hub/pessoas").json()))
    ok("troca de e-mail fica na auditoria com o anterior", "email_anterior" in c.get("/api/admin/auditoria?formato=csv").text and "tec@x.com" in c.get("/api/admin/auditoria?formato=csv").text)
    ok("e-mail do dono é salvo", c.put("/api/hub/usuarios/dono", json={"email": "dono@empresa.com"}).json()["email"] == "dono@empresa.com")
    pe = {p["email"]: p for p in c.get("/api/hub/pessoas").json()}
    ok("pessoas para as ferramentas (sem dados sensíveis)", pe["tec@x.com"]["escala_feriado"] and "senha" not in json.dumps(pe) and "hash" not in json.dumps(pe))
    ok("ferramentas instaladas (adaptação da interface)", isinstance(c.get("/api/hub/instaladas").json(), list) and anon.get("/api/hub/instaladas").status_code == 401)
    # agir em nome da pessoa (conectores): manifesto + autorização do administrador
    c.post("/api/hub/apps/alvo/iniciar"); c.post("/api/hub/apps/conector/iniciar")
    for _ in range(60):
        cfg = json.loads((DATA / "hub.config.json").read_text())
        if cfg.get("segredo_apps") and c.get("/api/hub/apps/alvo/estado").json()["status"] == "ativo":
            break
        time.sleep(0.5)
    tok = hmac.new(bytes.fromhex(cfg["segredo_apps"]), b"app:conector", hashlib.sha256).hexdigest()
    ag = lambda corpo: anon.post("/api/hub/agir", json=corpo, headers={"X-Hub-App-Token": tok})
    pedido = {"app": "alvo", "caminho": "/api/acao", "corpo": {"decisao": "aprovar"}, "pessoa": {"email": "tec@x.com"}}
    ok("agir: sem token de ferramenta é recusado", anon.post("/api/hub/agir", json=pedido).status_code == 401)
    ok("agir: conector sem autorização do administrador é recusado", ag(pedido).status_code == 403)
    c.put("/api/admin/ferramentas/conector", json={"agir_liberado": True})
    r = ag(pedido)
    ok("agir: com autorização, a ferramenta recebe a identidade assinada da pessoa", r.status_code == 200 and r.json()["resposta"] == {"quem": "tec%40x.com", "agente": "conector", "assinado": True, "corpo": {"decisao": "aprovar"}}, r.text[:300])
    ok("agir: só rotas /api/ e pessoas do hub", ag({**pedido, "caminho": "/admin"}).status_code == 400 and ag({**pedido, "pessoa": {"email": "ninguem@x.com"}}).status_code == 404)
    ok("agir: fica na auditoria", "em nome da pessoa" in c.get("/api/admin/auditoria?formato=csv").text)
finally:
    srv.terminate(); srv.wait(10)
print(f"\n{sum(passos)}/{len(passos)} verificações aprovadas")
sys.exit(0 if all(passos) else 1)
