"""Originação de Projetos — do pedido à elegibilidade, entrada no plano de investimentos e Termo de Abertura (TAP).

Ferramenta genérica de gestão do ciclo de vida do projeto (FastAPI + SQLite).
• Sozinha:   python app.py   (usuários de demonstração, organização em config/organizacao.json)
• No STRAT·HUB: identidade, perfil da organização e Data Core vêm do hub (ver hubkit.py).
Vocabulário, etapas, perfis e modelos de EAP ficam em config/originacao.json.
"""
from __future__ import annotations

import base64
import contextvars
import copy
import io
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from xml.sax.saxutils import escape
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Header, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

import hubkit

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("ORIGINACAO_DATA", BASE_DIR / "dados"))
ARQ_DIR = DATA_DIR / "arquivos"
DB_PATH = DATA_DIR / "originacao.db"
MAX_UPLOAD = 20 * 1024 * 1024
hubkit.configurar(dados=DATA_DIR, config=BASE_DIR / "config")
EXT_PERMITIDAS = {".pdf", ".png", ".jpg", ".jpeg", ".csv", ".txt", ".md", ".json",
                  ".docx", ".xlsx", ".xlsm", ".pptx", ".zip", ".msg", ".dwg"}

ESTAGIOS = {"FSE", "BC", "FSP", "ELEG", "MADURO"}
STATUS = {"Ativo", "Suspenso", "Cancelado"}

# ---------------------------------------------------------------- organização e configuração
# Perfil da organização: vem do hub (GET /api/hub/organizacao) ou de config/organizacao.json.
ORG_PADRAO = {
    "nome": "Organização Exemplo", "sigla": "ORG", "moeda": "BRL",
    "cor_primaria": "#1e3a6e", "cor_destaque": "#0f6e56",
    "unidades": [{"codigo": "UN-NORTE", "nome": "Unidade Norte", "grupo": "Regional A"},
                 {"codigo": "UN-SUL", "nome": "Unidade Sul", "grupo": "Regional A"},
                 {"codigo": "UN-LESTE", "nome": "Unidade Leste", "grupo": "Regional B"},
                 {"codigo": "CORP", "nome": "Corporativo", "grupo": "Corporativo"}],
    "areas": ["Engenharia", "Operações", "Tecnologia", "Manutenção", "Corporativo"],
    "vocabulario": {"plano_sigla": "PPI", "plano_nome": "Plano Plurianual de Investimentos", "ciclo": "Ciclo de Planejamento",
                    "codigo_erp": "Código ERP", "termo_abertura": "TAP", "termo_abertura_nome": "Termo de Abertura do Projeto",
                    "aprovador": "Aprovador Executivo", "forecast": "Tendência", "unidade": "Unidade", "area": "Área",
                    "capex": "CAPEX", "pmo": "PMO"},
}
# Configuração própria da ferramenta (config/originacao.json sobrepõe estes valores).
CONFIG_PADRAO = {
    "etapas": {"FSE": "Ficha de Demanda", "BC": "Business Case", "FSP": "Ficha de Projeto", "ELEG": "Elegibilidade", "MADURO": "Maduro"},
    "eaps": {
        "obras": {"nome": "EAP padrão · Obras / Implantação", "atividades": ["Estudos e levantamentos", "Projeto básico", "Projeto executivo", "Licenciamento / autorizações", "Contratação", "Mobilização", "Implantação / execução", "Testes", "Comissionamento", "Encerramento"]},
        "sistemas": {"nome": "EAP padrão · Sistemas / Tecnologia", "atividades": ["Levantamento de requisitos", "Arquitetura e projeto", "Contratação / aquisição", "Desenvolvimento / configuração", "Integração", "Testes", "Implantação", "Treinamento", "Estabilização", "Encerramento"]},
        "equipamentos": {"nome": "EAP padrão · Equipamentos / Ativos", "atividades": ["Especificação técnica", "Engenharia", "Contratação", "Fabricação / aquisição", "Inspeções", "Entrega", "Instalação", "Testes", "Comissionamento", "Encerramento"]},
    },
    # Troca de termos exibidos (interface e documentos). Ex.: {"PPI": "Plano Trienal"}
    "rotulos": {},
}
PERFIS = ("solicitante", "validador", "gestor", "gerente_executivo", "pmo")


def _ler_json(caminho: Path) -> dict:
    try:
        return json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}
    except (OSError, ValueError):
        return {}


def org() -> dict:
    return hubkit.organizacao(ORG_PADRAO)


def config_app() -> dict:
    cfg = copy.deepcopy(CONFIG_PADRAO)
    for k, v in _ler_json(BASE_DIR / "config" / "originacao.json").items():
        cfg[k] = {**cfg[k], **v} if isinstance(cfg.get(k), dict) and isinstance(v, dict) else v
    return cfg


def rotulos() -> dict[str, str]:
    """Termos canônicos internos (usados no código) → termos exibidos para a organização."""
    v = {**ORG_PADRAO["vocabulario"], **(org().get("vocabulario") or {})}
    cfg = config_app()
    base = {
        "Gerente Executivo": v["aprovador"], "PMO Central": v["pmo"], "PQQ": v["plano_sigla"], "PEP": v["codigo_erp"],
        "FSE": cfg["etapas"].get("FSE", "FSE"), "FSP": cfg["etapas"].get("FSP", "FSP"),
        "quinquênio": "horizonte do ciclo", "QUINQUÊNIO": "HORIZONTE DO CICLO", "Quinquênio": "Horizonte do ciclo",
    }
    base.update(cfg.get("rotulos") or {})
    return {k: str(t) for k, t in base.items() if str(t) and str(t) != k}


def rotular(texto) -> str:
    texto = str(texto)
    for k, t in sorted(rotulos().items(), key=lambda kv: -len(kv[0])):
        texto = re.sub(rf"(?<![\wÀ-ÿ]){re.escape(k)}(?![\wÀ-ÿ])", lambda _m, t=t: t, texto)
    return texto


def usuarios_demo() -> list[tuple]:
    """Usuários de demonstração (um por perfil), ligados às duas primeiras unidades da organização."""
    unis = [u["nome"] for u in org().get("unidades") or []] or ["Unidade A", "Unidade B"]
    u1, u2 = unis[0], unis[1 % len(unis)]
    return [("u-demo-solic1", "Solicitante Demo 1", "solicitante", u1),
            ("u-demo-solic2", "Solicitante Demo 2", "solicitante", u2),
            ("u-demo-eng", "Engenharia Demo", "validador", None),
            ("u-demo-gestor1", "Gestor Demo 1", "gestor", u1),
            ("u-demo-gestor2", "Gestor Demo 2", "gestor", u2),
            ("u-demo-aprov", "Aprovador Demo", "gerente_executivo", None),
            ("u-demo-pmo", "PMO Demo", "pmo", None)]


SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios(
  id TEXT PRIMARY KEY, nome TEXT NOT NULL, perfil TEXT NOT NULL, unidade TEXT, email TEXT, origem TEXT NOT NULL DEFAULT 'demo');
CREATE TABLE IF NOT EXISTS integracao_recebidos(
  chave TEXT PRIMARY KEY, versao INTEGER NOT NULL, lote_id TEXT, resultado TEXT, em TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ciclos(
  id TEXT PRIMARY KEY, nome TEXT NOT NULL, abertura TEXT NOT NULL, corte TEXT NOT NULL,
  inicio INTEGER NOT NULL, fim INTEGER NOT NULL, atualizado_em TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projetos(
  id TEXT PRIMARY KEY, pep TEXT, nome TEXT NOT NULL, unidade TEXT, diretoria TEXT,
  estagio TEXT NOT NULL, status TEXT NOT NULL, ciclo_id TEXT REFERENCES ciclos(id),
  caminho TEXT NOT NULL DEFAULT 'regular', capex_estimado REAL,
  dados TEXT NOT NULL, versao INTEGER NOT NULL DEFAULT 1,
  criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS capex_ano(
  projeto_id TEXT NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  ano INTEGER NOT NULL, valor REAL NOT NULL, PRIMARY KEY(projeto_id, ano));
CREATE TABLE IF NOT EXISTS historico(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  projeto_id TEXT NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  em TEXT, por TEXT, perfil TEXT, acao TEXT, campo TEXT, de TEXT, para TEXT,
  justificativa TEXT, gravado_por TEXT, gravado_em TEXT);
CREATE TABLE IF NOT EXISTS validacoes(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  projeto_id TEXT NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  em TEXT, por TEXT, perfil TEXT, gate TEXT, acao TEXT, comentario TEXT);
CREATE TABLE IF NOT EXISTS documentos(
  id TEXT PRIMARY KEY,
  projeto_id TEXT NOT NULL REFERENCES projetos(id) ON DELETE CASCADE,
  tipo TEXT, nome TEXT, url TEXT, arquivo TEXT, por TEXT, em TEXT);
CREATE TABLE IF NOT EXISTS sequencia(nome TEXT PRIMARY KEY, ultimo INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS solicitacoes(
  request_uuid TEXT PRIMARY KEY, protocolo TEXT UNIQUE NOT NULL, requester_id TEXT NOT NULL,
  requester_nome TEXT NOT NULL, requester_email TEXT, area_cliente TEXT, unidade TEXT, diretoria TEXT,
  status TEXT NOT NULL, conteudo TEXT NOT NULL, capex_respostas TEXT NOT NULL, capex_resultado TEXT,
  visual_id TEXT UNIQUE, project_id TEXT UNIQUE, versao INTEGER NOT NULL DEFAULT 1,
  criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL, enviado_em TEXT, aprovado_em TEXT,
  historico TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS integracao_lotes(
  id TEXT PRIMARY KEY, tipo TEXT NOT NULL, criado_em TEXT NOT NULL, criado_por TEXT NOT NULL,
  status TEXT NOT NULL, total INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL,
  resultado TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS ix_sol_req ON solicitacoes(requester_id);
CREATE INDEX IF NOT EXISTS ix_sol_status ON solicitacoes(status);
CREATE INDEX IF NOT EXISTS ix_proj_unidade ON projetos(unidade);
CREATE INDEX IF NOT EXISTS ix_proj_estagio ON projetos(estagio);
CREATE INDEX IF NOT EXISTS ix_proj_ciclo ON projetos(ciclo_id);
CREATE INDEX IF NOT EXISTS ix_hist_proj ON historico(projeto_id);
CREATE INDEX IF NOT EXISTS ix_val_proj ON validacoes(projeto_id);
CREATE INDEX IF NOT EXISTS ix_doc_proj ON documentos(projeto_id);
"""

# Campos que viram colunas/tabelas próprias; o resto do objeto vai para `dados` (JSON).
CAMPOS_ESTRUTURADOS = {
    "id", "pep", "nome", "unidade", "diretoria", "estagio", "status", "cicloId", "caminho",
    "capexEstimado", "versao", "criadoEm", "atualizadoEm", "capexAno", "hist", "validacoes",
    "documentos", "maturidadeCalculada", "capexTotal",
}

_lock = threading.Lock()


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@contextmanager
def conexao():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def iniciar_banco() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARQ_DIR.mkdir(parents=True, exist_ok=True)
    with conexao() as c:
        c.execute("PRAGMA journal_mode=WAL")
        cols = {r[1] for r in c.execute("PRAGMA table_info(usuarios)")}
        if cols and "origem" not in cols:  # base de versão anterior
            c.execute("ALTER TABLE usuarios ADD COLUMN email TEXT")
            c.execute("ALTER TABLE usuarios ADD COLUMN origem TEXT NOT NULL DEFAULT 'demo'")
        c.executescript(SCHEMA)
        if not hubkit.no_hub():  # usuários de demonstração só no modo isolado
            c.executemany(
                "INSERT OR IGNORE INTO usuarios(id,nome,perfil,unidade,origem) VALUES (?,?,?,?,'demo')",
                usuarios_demo(),
            )
        c.execute("INSERT OR IGNORE INTO sequencia(nome,ultimo) VALUES ('projeto',0)")


@asynccontextmanager
async def _ciclo_de_vida(_app):
    iniciar_banco()
    if not getattr(_ciclo_de_vida, "laco", False):
        _ciclo_de_vida.laco = True
        threading.Thread(target=_laco_sync, name="sync-originacao", daemon=True).start()
    yield


APP_VERSION = "2.2.2"
app = FastAPI(title="Originação de Projetos", version=APP_VERSION, lifespan=_ciclo_de_vida)
_identidade = contextvars.ContextVar("identidade_hub", default=None)


@app.middleware("http")
async def _ler_identidade(request: Request, call_next):
    tok = _identidade.set(hubkit.identidade(request.headers))
    try:
        return await call_next(request)
    finally:
        _identidade.reset(tok)


# ---------------------------------------------------------------- utilidades
def _usuario_do_hub(c: sqlite3.Connection, ident: dict) -> sqlite3.Row:
    """Primeiro acesso de um usuário do hub: cadastra com perfil padrão (dono/admin do hub → PMO)."""
    uid = "h-" + ident["id"]
    u = c.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
    if not u:
        perfil = "pmo" if ident.get("papel") in ("dono", "admin") else "solicitante"
        c.execute("INSERT INTO usuarios(id,nome,perfil,unidade,email,origem) VALUES (?,?,?,?,?,'hub')",
                  (uid, ident["nome"], perfil, None, ident.get("email") or ""))
    elif u["nome"] != ident["nome"] or (u["email"] or "") != (ident.get("email") or ""):
        c.execute("UPDATE usuarios SET nome=?, email=? WHERE id=?", (ident["nome"], ident.get("email") or "", uid))
    return c.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()


def usuario_atual(c: sqlite3.Connection, x_usuario: str | None):
    """No hub a pessoa é sempre quem o hub disse (sem simulação de perfis). O MASTER TESTE (só em ambiente de teste,
    assinado pelo hub) entra como PMO, pode exercer qualquer papel e fica identificado nos registros."""
    ident = _identidade.get()
    if ident:
        u = _usuario_do_hub(c, ident)
        if ident.get("master"):
            return {**dict(u), "perfil": "pmo", "unidade": None, "master": True, "nome": f"{u['nome']} (MASTER TESTE)"}
        return u
    if hubkit.no_hub():
        raise HTTPException(401, "Identidade do hub ausente. Acesse pela página do hub.")
    if not x_usuario:
        raise HTTPException(401, "Informe o usuário (cabeçalho X-Usuario).")
    u = c.execute("SELECT * FROM usuarios WHERE id=?", (x_usuario,)).fetchone()
    if not u:
        raise HTTPException(401, "Usuário desconhecido.")
    return u


def _dump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _load(v: str | None) -> Any:
    if v is None:
        return ""
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise HTTPException(400, f"Valor numérico inválido: {v!r}")


def compor_projetos(c: sqlite3.Connection, ids: list[str] | None = None) -> list[dict]:
    if ids is None:
        rows = c.execute("SELECT * FROM projetos ORDER BY id").fetchall()
    else:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = c.execute(f"SELECT * FROM projetos WHERE id IN ({marks}) ORDER BY id", ids).fetchall()
    if not rows:
        return []
    marks = ",".join("?" * len(rows))
    pids = [r["id"] for r in rows]
    capex: dict[str, dict] = {p: {} for p in pids}
    hist: dict[str, list] = {p: [] for p in pids}
    vals: dict[str, list] = {p: [] for p in pids}
    docs: dict[str, list] = {p: [] for p in pids}
    for r in c.execute(f"SELECT * FROM capex_ano WHERE projeto_id IN ({marks})", pids):
        capex[r["projeto_id"]][str(r["ano"])] = r["valor"]
    for r in c.execute(f"SELECT * FROM historico WHERE projeto_id IN ({marks}) ORDER BY seq", pids):
        hist[r["projeto_id"]].append({
            "em": r["em"], "por": r["por"], "perfil": r["perfil"], "acao": r["acao"],
            "campo": r["campo"], "de": _load(r["de"]), "para": _load(r["para"]),
            "justificativa": r["justificativa"] or "",
        })
    for r in c.execute(f"SELECT * FROM validacoes WHERE projeto_id IN ({marks}) ORDER BY seq", pids):
        vals[r["projeto_id"]].append({
            "em": r["em"], "por": r["por"], "perfil": r["perfil"], "gate": r["gate"],
            "acao": r["acao"], "comentario": r["comentario"] or "",
        })
    for r in c.execute(f"SELECT * FROM documentos WHERE projeto_id IN ({marks}) ORDER BY em", pids):
        docs[r["projeto_id"]].append({
            "id": r["id"], "tipo": r["tipo"], "nome": r["nome"], "url": r["url"] or "",
            "arquivo": r["arquivo"] or "", "por": r["por"], "em": r["em"],
        })
    out = []
    for r in rows:
        p = json.loads(r["dados"])
        p.update({
            "id": r["id"], "pep": r["pep"] or "", "nome": r["nome"], "unidade": r["unidade"],
            "diretoria": r["diretoria"], "estagio": r["estagio"], "status": r["status"],
            "cicloId": r["ciclo_id"] or "", "caminho": r["caminho"],
            "capexEstimado": r["capex_estimado"], "versao": r["versao"],
            "criadoEm": r["criado_em"], "atualizadoEm": r["atualizado_em"],
            "capexAno": capex[r["id"]], "hist": hist[r["id"]],
            "validacoes": vals[r["id"]], "documentos": docs[r["id"]],
        })
        out.append(p)
    return out


def checar_permissao(u: sqlite3.Row, existente: sqlite3.Row | None, corpo: dict) -> None:
    """Regras mínimas no servidor. O detalhamento por Gate ainda está na interface."""
    if u["perfil"] == "pmo":
        return
    dados_exist = json.loads(existente["dados"]) if existente else {}
    if existente is None:
        if u["perfil"] != "solicitante":
            raise HTTPException(403, "Somente solicitante ou PMO cadastra projetos.")
        if corpo.get("estagio") != "FSE" or corpo.get("solicitanteId") != u["id"]:
            raise HTTPException(403, "Projeto novo deve nascer na FSE e em nome do solicitante.")
        return
    # A etapa técnica é exclusiva da Engenharia/PMO, sempre distinta do solicitante.
    estagio_atual = existente["estagio"]
    if estagio_atual in ("BC", "FSP"):
        if u["perfil"] not in ("validador", "pmo") or dados_exist.get("solicitanteId") == u["id"]:
            raise HTTPException(403, "BC/FSP: preenchimento exclusivo da Engenharia, por profissional diferente do solicitante.")
    if estagio_atual == "ELEG" and u["perfil"] not in ("validador", "pmo"):
        raise HTTPException(403, "Ciclo de Elegibilidade: alteração restrita à Engenharia/PMO.")
    if u["perfil"] == "gerente_executivo":
        raise HTTPException(403, "Gerente Executivo decide aprovações pelos fluxos próprios; não altera o cadastro diretamente.")
    if u["perfil"] == "solicitante" and dados_exist.get("solicitanteId") != u["id"]:
        raise HTTPException(403, "Solicitante só altera os próprios projetos.")
    if u["perfil"] == "gestor" and existente["unidade"] != u["unidade"]:
        raise HTTPException(403, "Gestor só altera projetos da própria unidade.")


# ---------------------------------------------------------------- rotas
# ---------------------------------------------------------------- entradas

@app.get("/health")
def health():
    return {"status": "ok", "app": "originacao", "version": APP_VERSION, "hub_ready": True}

@app.get("/central", include_in_schema=False)
def central():
    return FileResponse(BASE_DIR / "static" / "index.html")

@app.get("/solicitante", include_in_schema=False)
def solicitante():
    return FileResponse(BASE_DIR / "static" / "solicitante.html")

@app.get("/api/meta")
def meta():
    o, cfg = org(), config_app()
    return {"app": "originacao", "version": APP_VERSION, "modo": "hub" if hubkit.no_hub() else "isolado",
            "organizacao": {k: o.get(k) for k in ("nome", "sigla", "moeda", "cor_primaria", "cor_destaque", "unidades", "areas", "vocabulario")},
            "etapas": cfg["etapas"], "eaps": cfg["eaps"], "rotulos": rotulos(),
            "ambiente": ((_identidade.get() or {}).get("ambiente") or "producao") if hubkit.no_hub() else "isolado"}


@app.get("/api/sessao")
def sessao(x_usuario: str | None = Header(None)):
    """Quem está usando: identidade do hub (modo hub) ou usuário de demonstração escolhido (modo isolado)."""
    ident = _identidade.get()
    with conexao() as c:
        if ident:
            atual = dict(usuario_atual(c, x_usuario))
            return {"modo": "hub", "usuario": atual, "real": atual, "simulando": False, "ambiente": ident.get("ambiente") or "producao",
                    "master": bool(atual.get("master"))}
        if hubkit.no_hub():
            raise HTTPException(401, "Identidade do hub ausente.")
        u = c.execute("SELECT * FROM usuarios WHERE id=?", (x_usuario,)).fetchone() if x_usuario else None
        return {"modo": "isolado", "usuario": dict(u) if u else None, "real": None, "simulando": False}


@app.get("/api/usuarios")
def listar_usuarios():
    with conexao() as c:
        filtro = " WHERE origem != 'demo'" if hubkit.no_hub() else ""  # no hub não há usuários de demonstração
        return [dict(r) for r in c.execute("SELECT * FROM usuarios" + filtro + " ORDER BY origem DESC, rowid")]


@app.put("/api/usuarios/{uid}")
def alterar_usuario(uid: str, corpo: dict, x_usuario: str | None = Header(None)):
    """PMO define o perfil e a unidade de cada usuário (o hub só informa quem é a pessoa)."""
    with _lock, conexao() as c:
        u = usuario_atual(c, x_usuario)
        if u["perfil"] != "pmo":
            raise HTTPException(403, "Somente o PMO altera perfis.")
        alvo = c.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
        if not alvo:
            raise HTTPException(404, "Usuário não encontrado.")
        perfil = str(corpo.get("perfil") or alvo["perfil"])
        if perfil not in PERFIS:
            raise HTTPException(400, "Perfil inválido.")
        if alvo["id"] == u["id"] and perfil != "pmo":
            raise HTTPException(400, "Você não pode remover o seu próprio perfil PMO.")
        c.execute("UPDATE usuarios SET perfil=?, unidade=? WHERE id=?", (perfil, corpo.get("unidade", alvo["unidade"]) or None, uid))
        return dict(c.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone())


@app.get("/api/estado")
def estado():
    """Assinatura leve para a interface saber se precisa recarregar (polling)."""
    with conexao() as c:
        p = c.execute("SELECT COUNT(*) n, COALESCE(MAX(atualizado_em),'') m, COALESCE(SUM(versao),0) v FROM projetos").fetchone()
        k = c.execute("SELECT COUNT(*) n, COALESCE(MAX(atualizado_em),'') m FROM ciclos").fetchone()
    return {"assinatura": f"{p['n']}|{p['m']}|{p['v']}|{k['n']}|{k['m']}"}


def _capex_resultado(respostas: dict) -> tuple[int, str]:
    pesos = {"C01": 3, "C02": 3, "C03": 3, "C04": 2, "C05": 2, "C06": 2, "C07": -4, "C08": -4}
    score = sum(pesos[k] for k,v in respostas.items() if k in pesos and str(v).lower() == "sim")
    # Contradição simples: rotina/recorrente junto com criação de ativo/capacidade relevante
    contrad = ((str(respostas.get("C07","")).lower()=="sim" or str(respostas.get("C08","")).lower()=="sim") and
               (str(respostas.get("C01","")).lower()=="sim" or str(respostas.get("C02","")).lower()=="sim" or str(respostas.get("C03","")).lower()=="sim"))
    if contrad:
        return score, "INCONCLUSIVO"
    if score >= 3:
        return score, "POTENCIAL_CAPEX"
    if score <= -3:
        return score, "PROVAVEL_OPEX"
    return score, "INCONCLUSIVO"

def _proximo_protocolo(c: sqlite3.Connection, periodo: str) -> str:
    chave=f"solicitacao_{periodo}"
    r=c.execute("SELECT ultimo FROM sequencia WHERE nome=?", (chave,)).fetchone()
    n=(r[0] if r else 0)+1
    c.execute("INSERT INTO sequencia(nome,ultimo) VALUES (?,?) ON CONFLICT(nome) DO UPDATE SET ultimo=excluded.ultimo", (chave,n))
    return f"SOL-{periodo}-{n:04d}"

def _proximo_visual(c: sqlite3.Connection, periodo: str) -> str:
    chave=f"visual_{periodo}"
    r=c.execute("SELECT ultimo FROM sequencia WHERE nome=?", (chave,)).fetchone()
    n=(r[0] if r else 0)+1
    c.execute("INSERT INTO sequencia(nome,ultimo) VALUES (?,?) ON CONFLICT(nome) DO UPDATE SET ultimo=excluded.ultimo", (chave,n))
    return f"PMO-{periodo}-{n:05d}"

def _sol_dict(r: sqlite3.Row) -> dict:
    d=dict(r); d["conteudo"]=_load(d.pop("conteudo")); d["capex_respostas"]=_load(d.pop("capex_respostas")); d["historico"]=_load(d.pop("historico")); return d

@app.get("/api/solicitacoes")
def listar_solicitacoes(x_usuario: str | None = Header(None), requester_id: str | None = None):
    with conexao() as c:
        u=usuario_atual(c, x_usuario)
        if u["perfil"] == "solicitante":
            rows=c.execute("SELECT * FROM solicitacoes WHERE requester_id=? ORDER BY criado_em DESC", (u["id"],)).fetchall()
        elif requester_id:
            rows=c.execute("SELECT * FROM solicitacoes WHERE requester_id=? ORDER BY criado_em DESC", (requester_id,)).fetchall()
        else:
            rows=c.execute("SELECT * FROM solicitacoes ORDER BY criado_em DESC").fetchall()
        return [_sol_dict(r) for r in rows]

@app.post("/api/solicitacoes")
def criar_solicitacao(corpo: dict, x_usuario: str | None = Header(None)):
    with _lock, conexao() as c:
        u=usuario_atual(c, x_usuario)
        if u["perfil"] not in ("solicitante","pmo"):
            raise HTTPException(403, "Perfil não autorizado a criar solicitação.")
        cont=corpo.get("conteudo") or {}; respostas=corpo.get("capex_respostas") or {}
        obrig=["necessidade_descricao","contexto_evidencias","impacto_atual","impacto_nao_implementacao","beneficios_descricao","alinhamento_negocio","natureza_intervencao"]
        falt=[k for k in obrig if not str(cont.get(k) or "").strip()]
        if falt: raise HTTPException(400, "Solicitação incompleta: "+", ".join(falt))
        score,res=_capex_resultado(respostas)
        ts=agora(); periodo=datetime.now().strftime("%Y%m")
        rid=str(uuid.uuid4()); prot=_proximo_protocolo(c, periodo)
        hist=[{"em":ts,"por":u["nome"],"acao":"Solicitação criada e enviada","status":"EM_ANALISE"}]
        c.execute("""INSERT INTO solicitacoes(request_uuid,protocolo,requester_id,requester_nome,requester_email,area_cliente,unidade,diretoria,status,conteudo,capex_respostas,capex_resultado,criado_em,atualizado_em,enviado_em,historico)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (rid,prot,u["id"],u["nome"],corpo.get("requester_email") or "",corpo.get("area_cliente") or "",corpo.get("unidade") or u["unidade"],corpo.get("diretoria") or "",
                   "EM_ANALISE",_dump(cont),_dump(respostas),res,ts,ts,ts,_dump(hist)))
        r=c.execute("SELECT * FROM solicitacoes WHERE request_uuid=?", (rid,)).fetchone(); out=_sol_dict(r); out["capex_score"]=score; return out

@app.post("/api/solicitacoes/{rid}/return")
def devolver_solicitacao(rid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with _lock, conexao() as c:
        u=usuario_atual(c,x_usuario)
        if u["perfil"] not in ("validador","pmo"): raise HTTPException(403,"Somente Engenharia/PMO devolve solicitações.")
        r=c.execute("SELECT * FROM solicitacoes WHERE request_uuid=?",(rid,)).fetchone()
        if not r: raise HTTPException(404,"Solicitação não encontrada.")
        hist=_load(r["historico"]); ts=agora(); hist.append({"em":ts,"por":u["nome"],"acao":"Devolvida para complementação","comentario":corpo.get("comentario") or "","status":"COMPLEMENTACAO"})
        c.execute("UPDATE solicitacoes SET status='COMPLEMENTACAO', atualizado_em=?, historico=?, versao=versao+1 WHERE request_uuid=?",(ts,_dump(hist),rid))
        return {"ok":True}

@app.post("/api/solicitacoes/{rid}/close")
def encerrar_solicitacao(rid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with _lock, conexao() as c:
        u=usuario_atual(c,x_usuario)
        if u["perfil"] not in ("validador","pmo"): raise HTTPException(403,"Somente Engenharia/PMO encerra solicitações.")
        r=c.execute("SELECT * FROM solicitacoes WHERE request_uuid=?",(rid,)).fetchone()
        if not r: raise HTTPException(404,"Solicitação não encontrada.")
        hist=_load(r["historico"]); ts=agora(); hist.append({"em":ts,"por":u["nome"],"acao":"Encerrada","comentario":corpo.get("comentario") or "","status":"ENCERRADA"})
        c.execute("UPDATE solicitacoes SET status='ENCERRADA', atualizado_em=?, historico=?, versao=versao+1 WHERE request_uuid=?",(ts,_dump(hist),rid))
        return {"ok":True}

@app.post("/api/solicitacoes/{rid}/approve")
def aprovar_solicitacao(rid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with _lock, conexao() as c:
        u=usuario_atual(c,x_usuario)
        if u["perfil"] not in ("validador","pmo"): raise HTTPException(403,"Somente Engenharia/PMO aprova solicitações.")
        r=c.execute("SELECT * FROM solicitacoes WHERE request_uuid=?",(rid,)).fetchone()
        if not r: raise HTTPException(404,"Solicitação não encontrada.")
        if r["project_id"]: return {"ok":True,"visual_id":r["visual_id"],"project_id":r["project_id"],"idempotent":True}
        if r["status"] not in ("EM_ANALISE","COMPLEMENTACAO"): raise HTTPException(409,"Solicitação não está disponível para aprovação.")
        _segregacao(u, r["requester_id"], "abriu a solicitação")
        ts=agora(); periodo=datetime.now().strftime("%Y%m"); visual=_proximo_visual(c,periodo); cont=_load(r["conteudo"]); respostas=_load(r["capex_respostas"]); pid=visual
        dados={
          "descricao": cont.get("contexto_evidencias", ""), "responsavel": corpo.get("responsavel") or r["requester_nome"],
          "solicitante": r["requester_nome"], "solicitanteId": r["requester_id"],
          "requestUuid": rid, "protocoloSolicitacao": r["protocolo"],
          "fse": {"area": r["area_cliente"] or r["unidade"] or "", "urgencia": corpo.get("urgencia") or "Média", "problema": cont.get("necessidade_descricao",""),
                  "justificativa": cont.get("impacto_atual","")+"\n\nImpacto de não implementar: "+cont.get("impacto_nao_implementacao","")},
          "fseOrigem": cont, "preCapex": {"respostas":respostas,"resultado":r["capex_resultado"]},
          "bc":{}, "fsp":{"eapId":"","atividades":[],"distribuicaoMensal":{}},
          "gates":{"FSE":{"status":"aprovado","enviadoPor":r["requester_nome"],"enviadoEm":r["enviado_em"],"decididoPor":u["nome"],"decididoEm":ts,"comentario":corpo.get("comentario") or ""},"BC":{"status":"rascunho"},"FSP":{"status":"rascunho"},"ELEG":{"status":"rascunho"}},
          "extra":None,"maturidadeManual":""}
        nome=("Solicitação " + r["protocolo"] + " · " + (cont.get("necessidade_descricao") or "Necessidade em análise")[:95])[:160]
        c.execute("""INSERT INTO projetos(pep,nome,unidade,diretoria,estagio,status,ciclo_id,caminho,capex_estimado,dados,id,versao,criado_em,atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                  ("",nome,r["unidade"],r["diretoria"],"BC","Ativo",None,"regular",None,_dump(dados),pid,ts,ts))
        c.execute("INSERT INTO historico(projeto_id,em,por,perfil,acao,campo,de,para,justificativa,gravado_por,gravado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (pid,ts,u["nome"],u["perfil"],"Projeto originado a partir de FSE aprovada","Estágio","Solicitação","Business Case",corpo.get("comentario") or "",u["id"],ts))
        c.execute("INSERT INTO validacoes(projeto_id,em,por,perfil,gate,acao,comentario) VALUES (?,?,?,?,?,?,?)",(pid,ts,u["nome"],u["perfil"],"FSE","Aprovado",corpo.get("comentario") or ""))
        hist=_load(r["historico"]); hist.append({"em":ts,"por":u["nome"],"acao":"Aprovada para estudo","status":"APROVADA","visual_id":visual})
        c.execute("UPDATE solicitacoes SET status='APROVADA', visual_id=?, project_id=?, aprovado_em=?, atualizado_em=?, historico=?, versao=versao+1 WHERE request_uuid=?",(visual,pid,ts,ts,_dump(hist),rid))
        return {"ok":True,"visual_id":visual,"project_id":pid}

@app.get("/api/ciclos")
def listar_ciclos():
    with conexao() as c:
        return [dict(r) for r in c.execute("SELECT id,nome,abertura,corte,inicio,fim FROM ciclos ORDER BY corte")]


@app.put("/api/ciclos/{cid}")
def salvar_ciclo(cid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with conexao() as c:
        u = usuario_atual(c, x_usuario)
        if u["perfil"] != "pmo":
            raise HTTPException(403, "Somente o PMO administra ciclos.")
        try:
            nome = str(corpo["nome"]).strip()
            abertura, corte = str(corpo["abertura"]), str(corpo["corte"])
            inicio, fim = int(corpo["inicio"]), int(corpo["fim"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Ciclo incompleto: nome, abertura, corte, inicio e fim.")
        if not nome or corte < abertura or fim < inicio:
            raise HTTPException(400, "Datas ou período do ciclo inconsistentes.")
        c.execute(
            """INSERT INTO ciclos(id,nome,abertura,corte,inicio,fim,atualizado_em) VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET nome=excluded.nome, abertura=excluded.abertura,
               corte=excluded.corte, inicio=excluded.inicio, fim=excluded.fim, atualizado_em=excluded.atualizado_em""",
            (cid, nome, abertura, corte, inicio, fim, agora()),
        )
        return {"id": cid, "nome": nome, "abertura": abertura, "corte": corte, "inicio": inicio, "fim": fim}


@app.get("/api/projetos")
def listar_projetos():
    with conexao() as c:
        return compor_projetos(c)


@app.get("/api/projetos/{pid}")
def obter_projeto(pid: str):
    with conexao() as c:
        r = compor_projetos(c, [pid])
    if not r:
        raise HTTPException(404, "Projeto não encontrado.")
    return r[0]


@app.put("/api/projetos/{pid}")
def salvar_projeto(pid: str, corpo: dict, x_usuario: str | None = Header(None)):
    if corpo.get("id") not in (None, pid):
        raise HTTPException(400, "ID do corpo difere da URL.")
    nome = str(corpo.get("nome") or "").strip()
    if not nome:
        raise HTTPException(400, "Nome do projeto é obrigatório.")
    if corpo.get("estagio") not in ESTAGIOS:
        raise HTTPException(400, "Estágio inválido.")
    if corpo.get("status") not in STATUS:
        raise HTTPException(400, "Status inválido.")
    ciclo_id = corpo.get("cicloId") or None
    dados = {k: v for k, v in corpo.items() if k not in CAMPOS_ESTRUTURADOS}
    ts = agora()

    with _lock, conexao() as c:
        u = usuario_atual(c, x_usuario)
        existente = c.execute("SELECT * FROM projetos WHERE id=?", (pid,)).fetchone()
        checar_permissao(u, existente, corpo)
        if existente and u["perfil"] != "pmo":
            # Dados PQQ, TAP e PMP só mudam pelas rotas de processo próprias.
            # Um PUT geral da ficha não pode alterar orçamento aprovado nem decisões.
            dados_atuais = _load(existente["dados"])
            for protegido in ("pqq", "tap", "pmp"):
                if protegido in dados_atuais:
                    dados[protegido] = dados_atuais[protegido]
                else:
                    dados.pop(protegido, None)
        if ciclo_id and not c.execute("SELECT 1 FROM ciclos WHERE id=?", (ciclo_id,)).fetchone():
            raise HTTPException(400, "Ciclo PQQ inexistente.")
        valores = (corpo.get("pep") or "", nome, corpo.get("unidade"), corpo.get("diretoria"),
                   corpo["estagio"], corpo["status"], ciclo_id, corpo.get("caminho") or "regular",
                   _num(corpo.get("capexEstimado")), _dump(dados))
        if existente:
            if corpo.get("versao") != existente["versao"]:
                atual = compor_projetos(c, [pid])[0]
                raise HTTPException(409, {"mensagem": "Outra pessoa alterou este projeto antes de você.", "atual": atual})
            c.execute(
                """UPDATE projetos SET pep=?, nome=?, unidade=?, diretoria=?, estagio=?, status=?, ciclo_id=?,
                   caminho=?, capex_estimado=?, dados=?, versao=versao+1, atualizado_em=? WHERE id=?""",
                (*valores, ts, pid),
            )
        else:
            c.execute(
                """INSERT INTO projetos(pep,nome,unidade,diretoria,estagio,status,ciclo_id,caminho,
                   capex_estimado,dados,id,versao,criado_em,atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                (*valores, pid, corpo.get("criadoEm") or ts, ts),
            )

        # CAPEX por ano: substitui a distribuição
        c.execute("DELETE FROM capex_ano WHERE projeto_id=?", (pid,))
        for ano, valor in (corpo.get("capexAno") or {}).items():
            v = _num(valor)
            if v is None:
                continue
            if v < 0:
                raise HTTPException(400, "CAPEX não pode ser negativo.")
            c.execute("INSERT INTO capex_ano(projeto_id,ano,valor) VALUES (?,?,?)", (pid, int(ano), v))

        # Histórico e validações: somente acréscimo (trilha de auditoria)
        n_hist = c.execute("SELECT COUNT(*) FROM historico WHERE projeto_id=?", (pid,)).fetchone()[0]
        for e in (corpo.get("hist") or [])[n_hist:]:
            c.execute(
                """INSERT INTO historico(projeto_id,em,por,perfil,acao,campo,de,para,justificativa,gravado_por,gravado_em)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, e.get("em") or ts, e.get("por"), e.get("perfil"), e.get("acao"), e.get("campo"),
                 _dump(e.get("de", "")), _dump(e.get("para", "")), e.get("justificativa") or "", u["id"], ts),
            )
        n_val = c.execute("SELECT COUNT(*) FROM validacoes WHERE projeto_id=?", (pid,)).fetchone()[0]
        for e in (corpo.get("validacoes") or [])[n_val:]:
            c.execute(
                "INSERT INTO validacoes(projeto_id,em,por,perfil,gate,acao,comentario) VALUES (?,?,?,?,?,?,?)",
                (pid, e.get("em") or ts, e.get("por"), e.get("perfil"), e.get("gate"), e.get("acao"), e.get("comentario") or ""),
            )

        # Documentos: sincroniza a lista
        docs = corpo.get("documentos") or []
        ids = [d["id"] for d in docs if d.get("id")]
        if ids:
            marks = ",".join("?" * len(ids))
            c.execute(f"DELETE FROM documentos WHERE projeto_id=? AND id NOT IN ({marks})", (pid, *ids))
        else:
            c.execute("DELETE FROM documentos WHERE projeto_id=?", (pid,))
        for d in docs:
            if not d.get("id"):
                continue
            arq = d.get("arquivo") or ""
            if arq and not re.fullmatch(r"[a-f0-9]{32}\.[a-z0-9]{1,5}", arq):
                raise HTTPException(400, "Referência de arquivo inválida.")
            c.execute(
                """INSERT INTO documentos(id,projeto_id,tipo,nome,url,arquivo,por,em) VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET tipo=excluded.tipo, nome=excluded.nome, url=excluded.url""",
                (d["id"], pid, d.get("tipo"), d.get("nome"), d.get("url") or "", arq, d.get("por"), d.get("em") or ts),
            )
        return compor_projetos(c, [pid])[0]


def _gravar_evento_projeto(c: sqlite3.Connection, pid: str, dados: dict, u: sqlite3.Row,
                           acao: str, comentario: str = "", gate: str = "PQQ/TAP") -> str:
    ts = agora()
    dados.setdefault("hist", []).append({"em": ts, "por": u["nome"], "perfil": u["perfil"],
        "acao": acao, "campo": gate, "de": "", "para": "", "justificativa": comentario})
    c.execute("UPDATE projetos SET dados=?, versao=versao+1, atualizado_em=? WHERE id=?",
              (_dump(dados), ts, pid))
    c.execute("INSERT INTO historico(projeto_id,em,por,perfil,acao,campo,de,para,justificativa,gravado_por,gravado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (pid, ts, u["nome"], u["perfil"], acao, gate, _dump(""), _dump(""), comentario, u["id"], ts))
    c.execute("INSERT INTO validacoes(projeto_id,em,por,perfil,gate,acao,comentario) VALUES (?,?,?,?,?,?,?)",
              (pid, ts, u["nome"], u["perfil"], gate, acao, comentario))
    return ts


def _aprovar_processo(u: sqlite3.Row) -> None:
    if u["perfil"] not in ("gerente_executivo", "pmo"):
        raise HTTPException(403, "Esta decisão é exclusiva do Gerente Executivo ou do PMO.")


def _segregacao(u, autor_id: str | None, o_que: str) -> None:
    """Segregação de funções: quem enviou/solicitou não decide o próprio pedido (o MASTER TESTE, só em ambiente de teste, é exceção)."""
    if autor_id and autor_id == u["id"] and not (isinstance(u, dict) and u.get("master")):
        raise HTTPException(403, f"Quem {o_que} não pode decidir o próprio pedido. A decisão é de outra pessoa.")


def _gerar_tap_pdf(projeto: dict, destino: Path) -> None:
    """Emite o TAP (modelo genérico) com os dados aprovados, sem editar o orçamento. Marca e termos vêm do perfil da organização."""
    o=org(); voc={**ORG_PADRAO["vocabulario"],**(o.get("vocabulario") or {})}
    cor=str(o.get("cor_primaria") or "#1e3a6e")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}",cor): cor="#1e3a6e"
    pqq=projeto.get("pqq") or {}; ret=pqq.get("retorno") or {}; tap=projeto.get("tap") or {}
    prop=tap.get("proposta") or {}; fse=projeto.get("fse") or {}; fsp=projeto.get("fsp") or {}
    curva=ret.get("curva_mensal") or {}; inicio=int(ret.get("ciclo_inicio") or 0); fim=int(ret.get("ciclo_fim") or 0)
    years=list(range(inicio,fim+1)) if inicio and fim>=inicio else sorted({int(k[:4]) for k in curva if re.fullmatch(r"\d{4}-\d{2}",str(k))})[:5]
    if len(years)>5: years=years[:5]
    palette={"purple":colors.HexColor(cor),"ink":colors.HexColor("#25223d"),"pale":colors.HexColor("#f4f1fc"),
             "pale2":colors.HexColor("#fbfaff"),"line":colors.HexColor("#dfd9f3"),"muted":colors.HexColor("#686779"),"white":colors.white}
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TapBrand",parent=styles["Normal"],fontName="Helvetica-Bold",fontSize=12,textColor=palette["purple"],leading=14))
    styles.add(ParagraphStyle(name="TapTitle2",parent=styles["Normal"],fontName="Helvetica-Bold",fontSize=14,textColor=palette["ink"],leading=16))
    styles.add(ParagraphStyle(name="TapSub",parent=styles["Normal"],fontSize=7.5,textColor=palette["muted"],leading=9))
    styles.add(ParagraphStyle(name="TapSection",parent=styles["Normal"],fontName="Helvetica-Bold",fontSize=8,textColor=palette["ink"],leading=9.5,spaceBefore=5,spaceAfter=3))
    styles.add(ParagraphStyle(name="TapLabel",parent=styles["Normal"],fontName="Helvetica-Bold",fontSize=6.4,textColor=palette["purple"],leading=7.5))
    styles.add(ParagraphStyle(name="TapValue",parent=styles["Normal"],fontSize=7.5,textColor=palette["ink"],leading=9))
    styles.add(ParagraphStyle(name="TapTiny",parent=styles["Normal"],fontSize=6.4,textColor=palette["muted"],leading=7.6))
    styles.add(ParagraphStyle(name="TapCell",parent=styles["Normal"],fontSize=6.6,textColor=palette["ink"],leading=7.5))
    styles.add(ParagraphStyle(name="TapCellHead",parent=styles["Normal"],fontName="Helvetica-Bold",fontSize=6.8,textColor=palette["white"],leading=7.5,alignment=TA_CENTER))
    P=lambda value,style="TapValue":Paragraph(escape(str(value or "—")).replace("\n","<br/>"),styles[style])
    def money_mil(value):
        try: return "R$ "+format(float(value or 0)/1000,",.2f").replace(",","X").replace(".",",").replace("X",".")
        except (TypeError,ValueError): return "R$ 0,00"
    def date_br(value):
        try:return datetime.strptime(str(value)[:10],"%Y-%m-%d").strftime("%d/%m/%Y")
        except (TypeError,ValueError):return "—"
    total=float(ret.get("orcamento_total") or 0); by_year={y:sum(float(v or 0) for k,v in curva.items() if str(k).startswith(f"{y}-")) for y in years}
    quinquenio=sum(by_year.values()); outside=total-quinquenio
    total_by_month={str(k):float(v or 0) for k,v in curva.items()}
    months=[("01","Janeiro"),("02","Fevereiro"),("03","Março"),("04","Abril"),("05","Maio"),("06","Junho"),
            ("07","Julho"),("08","Agosto"),("09","Setembro"),("10","Outubro"),("11","Novembro"),("12","Dezembro")]
    story=[]
    brand=Table([[P(o.get("sigla") or o.get("nome") or "","TapBrand"),Paragraph(escape(str(o.get("nome") or ""))+" | "+escape(voc["pmo"])+"<br/><font color='"+cor+"'>TERMO APROVADO</font>",styles["TapTiny"])]],colWidths=[55*mm,125*mm])
    brand.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),("ALIGN",(1,0),(1,0),"RIGHT"),("LINEBELOW",(0,0),(-1,-1),1.1,palette["purple"]),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
    story += [brand,Spacer(1,2*mm),P(voc["termo_abertura_nome"].upper(),"TapTitle2"),P("Compromisso formal de escopo, prazo e investimento | "+voc["termo_abertura"],"TapSub")]
    def section(number,label): return P(f"{number:02d}  {label.upper()}","TapSection")
    def fields(rows,widths):
        groups=max(1,len(widths)//2); data=[]
        for i in range(0,len(rows),groups):
            row=[]
            for label,value in rows[i:i+groups]: row.extend([P(label,"TapLabel"),P(value,"TapValue")])
            row.extend([P("","TapLabel"),P("","TapValue")]*(groups-len(row)//2))
            data.append(row)
        tbl=Table(data,colWidths=widths)
        tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),palette["pale"]),("GRID",(0,0),(-1,-1),.25,palette["white"]),
            ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),
            ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)]));return tbl
    def stacked_fields(items,widths):
        cells=[]
        for label,value in items:
            safe_value=escape(str(value or "—")).replace("\n","<br/>")
            cells.append(Paragraph(f"<font color=\"{cor}\"><b>{escape(str(label).upper())}</b></font><br/>{safe_value}",styles["TapValue"]))
        tbl=Table([cells],colWidths=widths)
        tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),palette["pale"]),("GRID",(0,0),(-1,-1),.5,palette["white"]),
            ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
            ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
        return tbl
    story += [section(1,"Identificação e responsáveis"),
        stacked_fields([("NOME DO PROJETO",projeto.get("nome"))],[180*mm]),
        stacked_fields([("ID MESTRE",projeto.get("id")),(voc["unidade"].upper()+" / "+voc["area"].upper(),f"{projeto.get('unidade') or '—'} / {projeto.get('diretoria') or '—'}"),
                       (voc["codigo_erp"].upper(),projeto.get("pep") or "Pendente")],[54*mm,70*mm,56*mm]),
        stacked_fields([("ÁREA CLIENTE / PATROCINADOR",f"{fse.get('area') or projeto.get('unidade') or '—'} · {projeto.get('solicitante') or '—'}"),
                       ("ENGENHARIA / GESTOR DO PROJETO",projeto.get("responsavel"))],[90*mm,90*mm]),
        section(2,"Escopo do projeto"),stacked_fields([("DESCRIÇÃO DO ESCOPO ACORDADO",prop.get("escopo"))],[180*mm]),
        section(3,"Prazos de execução"),stacked_fields([("INÍCIO PLANEJADO",date_br(prop.get("inicio_previsto"))),
                       ("CONCLUSÃO COMPROMISSADA",date_br(prop.get("conclusao_compromissada")))],[90*mm,90*mm]),
        section(4,"Investimento e distribuição mensal no horizonte do ciclo"),
        stacked_fields([("INVESTIMENTO TOTAL AUTORIZADO",money_mil(total)),("PARCELA NO HORIZONTE",money_mil(quinquenio)),
                       ("PARCELA FORA DO HORIZONTE",money_mil(outside))],[60*mm,60*mm,60*mm]),
        Paragraph(f"<font color=\"{cor}\"><b>REFERÊNCIA ORÇAMENTÁRIA</b></font>  {escape(voc['plano_sigla'])} {escape(str(ret.get('pqq_id') or '—'))} · revisão {escape(str(ret.get('pqq_revisao') or '—'))} · aprovada em {date_br(ret.get('aprovado_em'))}  |  <font color=\"{cor}\"><b>ÓTICA</b></font>  {escape(str(ret.get('otica') or 'Financeira'))}  |  Valores nominais em mil ({escape(str(o.get('moeda') or 'BRL'))})",styles["TapTiny"])]
    cols=[34*mm]+([146*mm/len(years)]*len(years) if years else [146*mm])
    head=[P("MÊS","TapCellHead")]+[P(y,"TapCellHead") for y in years]
    grid=[head]
    for mm_no,label in months:
        grid.append([P(label,"TapCell")]+[P(money_mil(total_by_month.get(f"{y}-{mm_no}",0)),"TapCell") for y in years])
    grid.append([P("TOTAL ANO","TapLabel")]+[P(money_mil(by_year.get(y,0)),"TapLabel") for y in years])
    curve_table=Table(grid,colWidths=cols,repeatRows=1)
    curve_table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),palette["purple"]),("BACKGROUND",(0,1),(-1,-2),palette["pale2"]),
        ("BACKGROUND",(0,-1),(-1,-1),colors.HexColor("#e9e3ff")),("GRID",(0,0),(-1,-1),.25,palette["line"]),
        ("ALIGN",(1,1),(-1,-1),"RIGHT"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),3),
        ("RIGHTPADDING",(0,0),(-1,-1),3),("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2)]))
    story += [curve_table,P("Nota: valores e curva reproduzem a aprovação do "+voc["plano_sigla"]+". Diferenças de arredondamento são explicadas no dossiê digital.","TapTiny"),
        section(5,"Condições e compromissos"),stacked_fields([("PREMISSAS E RESTRIÇÕES ESSENCIAIS",prop.get("premissas") or fsp.get("premissas") or "Não informadas")],[180*mm]),
        section(6,"Formalização e aceite"),P("As partes formalizam concordância com o escopo, o prazo e o investimento desta TAP e com a versão vinculada do dossiê digital. Alterações seguem o processo de Gestão de Mudanças e as alçadas aplicáveis.","TapTiny")]
    signature=Table([[P("ÁREA CLIENTE / PATROCINADOR","TapLabel"),P("ENGENHARIA / EXECUÇÃO","TapLabel"),P("AUTORIDADE APROVADORA","TapLabel")],
        [P(f"{projeto.get('solicitante') or '—'}\n{fse.get('area') or '—'}\nRegistro digital: {date_br(tap.get('decidido_em'))}","TapTiny"),
         P(f"{projeto.get('responsavel') or '—'}\nEngenharia\nRevisado em: {date_br(prop.get('revisado_em'))}","TapTiny"),
         P(f"{tap.get('decidido_por') or '—'}\n{voc['aprovador']}\nAprovado em: {date_br(tap.get('decidido_em'))}","TapTiny")]],colWidths=[60*mm]*3)
    signature.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),palette["pale"]),("GRID",(0,0),(-1,-1),.35,palette["white"]),
        ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),
        ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3)]))
    story += [signature,P(f"DOSSIÊ DIGITAL  Originação {projeto.get('id','')} · versão {projeto.get('versao','—')} · documento aprovado na ferramenta","TapTiny")]
    def footer(canvas,doc):
        canvas.saveState();canvas.setFont("Helvetica",6);canvas.setFillColor(palette["muted"])
        canvas.drawString(15*mm,8*mm,f"{voc['termo_abertura']} · Valores aprovados pelo {voc['plano_sigla']} e reproduzidos sem edição pela Engenharia")
        canvas.drawRightString(A4[0]-15*mm,8*mm,str(o.get("sigla") or ""));canvas.restoreState()
    doc=SimpleDocTemplate(str(destino),pagesize=A4,rightMargin=15*mm,leftMargin=15*mm,topMargin=10*mm,bottomMargin=13*mm,
        title="TAP - "+str(projeto.get("nome","Projeto")),author=voc["pmo"]+" · Originação")
    doc.build(story,onFirstPage=footer,onLaterPages=footer)


def _fsp_monthly_curve(activities: list[dict] | None) -> dict[str, float]:
    """Rebuilds the monthly FSP curve from saved activity dates and costs."""
    curve: dict[str, float] = {}
    for activity in activities or []:
        try:
            start = datetime.fromisoformat(str(activity.get("inicio") or "")[:10])
            end = datetime.fromisoformat(str(activity.get("fim") or "")[:10])
            cost = float(activity.get("custo") or 0)
        except (TypeError, ValueError):
            continue
        if end < start or cost <= 0:
            continue
        months=[]; year, month=start.year, start.month
        while (year, month) <= (end.year, end.month):
            months.append(f"{year:04d}-{month:02d}")
            month += 1
            if month == 13: year, month = year + 1, 1
        if not months: continue
        monthly_cost=cost/len(months)
        for index, key in enumerate(months):
            amount=cost-monthly_cost*(len(months)-1) if index==len(months)-1 else monthly_cost
            curve[key]=curve.get(key,0.0)+amount
    return curve


def _json_safe_pqq(p: dict, ciclo: dict | None = None) -> dict:
    ciclo = ciclo or {}
    fsp = dict(p.get("fsp") or {})
    if not fsp.get("distribuicaoMensal") and not fsp.get("distribuicao_mensal"):
        derived = _fsp_monthly_curve(fsp.get("atividades"))
        if derived: fsp["distribuicaoMensal"] = derived
    return {
        "id_mestre": p["id"], "pep": p.get("pep", ""), "nome": p["nome"], "unidade": p["unidade"], "diretoria": p["diretoria"],
        "ciclo_pqq": p.get("cicloId", ""), "ciclo_pqq_nome": ciclo.get("nome", ""),
        "ciclo_inicio": int(ciclo["inicio"]), "ciclo_fim": int(ciclo["fim"]),
        "solicitante": p.get("solicitante", ""),
        "responsavel": p.get("responsavel", ""), "fse": p.get("fse", {}), "business_case": p.get("bc", {}),
        "fsp": fsp, "distribuicao_anual_referencia": p.get("capexAno", {}),
    }


@app.post("/api/projetos/{pid}/pqq/enviar-aprovacao")
def solicitar_aprovacao_envio_pqq(pid: str, corpo: dict | None = None, x_usuario: str | None = Header(None)):
    with _lock, conexao() as c:
        u = usuario_atual(c, x_usuario)
        if u["perfil"] not in ("validador", "pmo"):
            raise HTTPException(403, "Somente Engenharia/PMO pode encaminhar para a aprovação executiva.")
        row = c.execute("SELECT * FROM projetos WHERE id=?", (pid,)).fetchone()
        if not row: raise HTTPException(404, "Projeto não encontrado.")
        p = _load(row["dados"])
        if (p.get("gates") or {}).get("ELEG", {}).get("status") != "aprovado":
            raise HTTPException(409, "A elegibilidade precisa estar aprovada antes do envio ao PQQ.")
        if p.get("caminho") == "extraordinario":
            raise HTTPException(409, "A rota extraordinária não segue para o PQQ regular.")
        pqq = p.setdefault("pqq", {})
        if pqq.get("status") in ("em_aprovacao_envio", "liberado_para_envio", "enviado", "aprovado") or pqq.get("retorno"):
            raise HTTPException(409, "O projeto já está em aprovação ou no fluxo de envio ao PQQ.")
        pqq["status"] = "em_aprovacao_envio"
        pqq["aprovacao_envio"] = {"status":"em_aprovacao", "solicitado_por":u["nome"], "solicitado_por_id":u["id"], "solicitado_em":agora(),
                                  "comentario":(corpo or {}).get("comentario", "")}
        _gravar_evento_projeto(c, pid, p, u, "Enviado para aprovação executiva de entrada no PQQ",
                               pqq["aprovacao_envio"]["comentario"], "Aprovação para PQQ")
        return compor_projetos(c, [pid])[0]


@app.post("/api/projetos/{pid}/pqq/decidir-envio")
def decidir_envio_pqq(pid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with _lock, conexao() as c:
        u = usuario_atual(c, x_usuario); _aprovar_processo(u)
        row = c.execute("SELECT * FROM projetos WHERE id=?", (pid,)).fetchone()
        if not row: raise HTTPException(404, "Projeto não encontrado.")
        p = _load(row["dados"]); pqq = p.get("pqq") or {}; ap = pqq.get("aprovacao_envio") or {}
        if ap.get("status") != "em_aprovacao": raise HTTPException(409, "Não há envio ao PQQ aguardando aprovação.")
        _segregacao(u, ap.get("solicitado_por_id"), "enviou para aprovação")
        decisao = str(corpo.get("decisao") or "").lower()
        if decisao not in ("aprovar", "devolver"): raise HTTPException(400, "Decisão deve ser aprovar ou devolver.")
        comentario = str(corpo.get("comentario") or "").strip()
        if decisao == "devolver" and not comentario: raise HTTPException(400, "Informe o motivo da devolução.")
        ts = agora(); ap.update({"status":"aprovado" if decisao=="aprovar" else "devolvido",
            "decidido_por":u["nome"],"decidido_em":ts,"comentario":comentario})
        pqq["aprovacao_envio"] = ap; pqq["status"] = "liberado_para_envio" if decisao == "aprovar" else "devolvido_para_ajuste"
        p["pqq"] = pqq
        _gravar_evento_projeto(c, pid, p, u, "Aprovação de envio ao PQQ: " + decisao, comentario, "Aprovação para PQQ")
        return compor_projetos(c, [pid])[0]


@app.put("/api/projetos/{pid}/tap/proposta")
def salvar_proposta_tap(pid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with _lock, conexao() as c:
        u=usuario_atual(c,x_usuario)
        if u["perfil"] not in ("validador","pmo"):
            raise HTTPException(403,"Somente Engenharia/PMO pode revisar escopo e cronograma do TAP.")
        row=c.execute("SELECT * FROM projetos WHERE id=?",(pid,)).fetchone()
        if not row: raise HTTPException(404,"Projeto não encontrado.")
        p=_load(row["dados"]); retorno=((p.get("pqq") or {}).get("retorno") or {})
        if retorno.get("status")!="aprovado": raise HTTPException(409,"O orçamento aprovado do PQQ ainda não foi recebido.")
        if (p.get("tap") or {}).get("status") in ("em_aprovacao","aprovado"):
            raise HTTPException(409,"A proposta do TAP está bloqueada durante ou após a aprovação.")
        escopo=str(corpo.get("escopo") or "").strip(); cronograma=str(corpo.get("cronograma") or "").strip()
        inicio=str(corpo.get("inicio_previsto") or "").strip(); conclusao=str(corpo.get("conclusao_compromissada") or "").strip()
        if not escopo or not cronograma or not inicio or not conclusao: raise HTTPException(400,"Escopo, cronograma, início planejado e conclusão compromissada são obrigatórios.")
        try:
            data_inicio=datetime.strptime(inicio,"%Y-%m-%d").date(); data_fim=datetime.strptime(conclusao,"%Y-%m-%d").date()
        except ValueError: raise HTTPException(400,"Informe datas válidas para o início e a conclusão do projeto.")
        if data_inicio>data_fim: raise HTTPException(400,"O início planejado deve ocorrer antes da conclusão compromissada.")
        ciclo_inicio=int(retorno.get("ciclo_inicio") or 0); ciclo_fim=int(retorno.get("ciclo_fim") or 0)
        if not ciclo_inicio or not ciclo_fim or data_inicio.year<ciclo_inicio or data_fim.year>ciclo_fim:
            raise HTTPException(400,"As datas do projeto devem permanecer dentro do ciclo orçamentário aprovado pelo PQQ.")
        curva=retorno.get("curva_mensal") or {}
        for mes,valor in curva.items():
            try:
                ano,mes_num=map(int,str(mes).split("-")); valor=float(valor or 0)
                if not 1<=mes_num<=12 or valor<0: raise ValueError()
                mes_inicio=datetime(ano,mes_num,1).date()
                mes_fim=(datetime(ano+1,1,1).date() if mes_num==12 else datetime(ano,mes_num+1,1).date())
                if valor>0 and (mes_inicio>data_fim or mes_fim<=data_inicio):
                    raise HTTPException(400,f"O cronograma não cobre o mês {mes} que possui valor aprovado pelo PQQ.")
            except HTTPException: raise
            except (TypeError,ValueError): raise HTTPException(400,"A curva mensal aprovada contém um mês ou valor inválido.")
        tap=p.setdefault("tap",{}); prop=tap.setdefault("proposta",{})
        prop.update({"escopo":escopo,"cronograma":cronograma,"inicio_previsto":inicio,"conclusao_compromissada":conclusao,
                     "premissas":str(corpo.get("premissas") or "").strip(),"revisado_por":u["nome"],"revisado_em":agora()})
        tap["status"]="em_analise_engenharia"; p["tap"]=tap
        _gravar_evento_projeto(c,pid,p,u,"Proposta do TAP revisada pela Engenharia", "", "TAP")
        return compor_projetos(c,[pid])[0]


# ---------------------------------------------------------------- integração via Data Core do hub
# Contrato (ver docs/CONTRATO_INTEGRACAO.md):
#   portfolio/entrada_originacao  Originação → Plano de investimentos   (chave: id mestre, período "current")
#   portfolio/retorno_originacao  Plano → Originação                    (chave: id mestre)
#   pmp/tap_aprovado              Originação → PMP (TAP aprovado + PDF)  (chave: id mestre)
def _quem(u: sqlite3.Row) -> str:
    return (u["email"] if "email" in u.keys() and u["email"] else "") or u["nome"]


def _publicar(domain: str, entity_type: str, key: str, payload: dict, u: sqlite3.Row, period: str = "") -> dict:
    try:
        return hubkit.publicar(domain, entity_type, key, payload, period=period, source_user=_quem(u),
                               source_version=APP_VERSION, source_application="originacao")
    except Exception as e:  # hub fora do ar, recusa etc.
        raise HTTPException(502, f"O Data Core não aceitou a publicação ({e}). Nada foi alterado; tente novamente.")


@app.post("/api/integracoes/pqq/envios")
def gerar_lote_envio_pqq(corpo: dict, x_usuario: str | None = Header(None)):
    """Gera o lote e publica cada projeto no Data Core. Só marca como enviado se todos forem aceitos."""
    with _lock, conexao() as c:
        u = usuario_atual(c, x_usuario)
        if u["perfil"] != "pmo": raise HTTPException(403, "Somente PMO pode gerar pacotes de envio ao PQQ.")
        ids = list(dict.fromkeys(str(x) for x in corpo.get("projeto_ids", []) if x))
        if not ids: raise HTTPException(400, "Selecione ao menos um projeto aprovado para envio.")
        ts = agora(); batch_id = "ENV-" + datetime.now(timezone.utc).strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:8].upper()
        itens, resultados = [], []
        for pid in ids:
            row = c.execute("SELECT * FROM projetos WHERE id=?", (pid,)).fetchone()
            if not row:
                resultados.append({"id_mestre":pid,"resultado":"erro","mensagem":"Projeto não encontrado."}); continue
            p = _load(row["dados"]); pqq = p.get("pqq") or {}
            if pqq.get("status") != "liberado_para_envio":
                resultados.append({"id_mestre":pid,"resultado":"erro","mensagem":"Projeto ainda não está liberado pelo Gerente Executivo."}); continue
            ciclo_id = str(row["ciclo_id"] or "").strip()
            ciclo_row = c.execute("SELECT id,nome,inicio,fim FROM ciclos WHERE id=?", (ciclo_id,)).fetchone() if ciclo_id else None
            if not ciclo_row:
                resultados.append({"id_mestre":pid,"resultado":"erro","mensagem":"Defina um ciclo PQQ válido antes de enviar o projeto."}); continue
            try:
                ciclo = dict(ciclo_row)
                ciclo["inicio"], ciclo["fim"] = int(ciclo["inicio"]), int(ciclo["fim"])
                if ciclo["fim"] < ciclo["inicio"]: raise ValueError()
            except (TypeError, ValueError):
                resultados.append({"id_mestre":pid,"resultado":"erro","mensagem":"O ciclo PQQ precisa ter um intervalo anual válido."}); continue
            pqq["status"] = "enviado"
            pqq["envio"] = {"lote_id":batch_id,"enviado_em":ts,"enviado_por":u["nome"],"status":"publicado","publicado_em":ts}
            p["pqq"] = pqq
            _gravar_evento_projeto(c, pid, p, u, "Enviado ao PQQ pelo Data Core", batch_id, "Integração PQQ")
            itens.append(_json_safe_pqq(compor_projetos(c, [pid])[0], ciclo))
            resultados.append({"id_mestre":pid,"resultado":"incluido","mensagem":"Publicado no Data Core."})
        if not itens: raise HTTPException(409, {"mensagem":"Nenhum projeto pôde ser enviado.","resultados":resultados})
        for item in itens:  # qualquer falha aqui desfaz a transação inteira (nada fica marcado como enviado)
            _publicar("portfolio", "entrada_originacao", item["id_mestre"],
                      {"schema_version":"2.0","tipo":"originacao_para_plano","lote_id":batch_id,"decision_status":"aguardando",
                       "enviado_em":ts,"enviado_por":u["nome"],"projeto":item}, u, period="current")
        payload = {"schema_version":"2.0","tipo":"originacao_para_plano","lote_id":batch_id,"gerado_em":ts,
                   "gerado_por":u["nome"],"projetos":itens}
        c.execute("INSERT INTO integracao_lotes(id,tipo,criado_em,criado_por,status,total,payload,resultado) VALUES (?,?,?,?,?,?,?,?)",
                  (batch_id,"PQQ_ENVIO",ts,u["nome"],"publicado",len(itens),_dump(payload),_dump(resultados)))
        return {"lote_id":batch_id,"total":len(itens),"status":"publicado","payload":payload,"resultados":resultados}


def _importar_retorno(c: sqlite3.Connection, u: sqlite3.Row, corpo: dict) -> dict:
    projetos = corpo.get("projetos")
    if not isinstance(projetos, list) or not projetos: raise HTTPException(400, "O retorno do PQQ deve conter a lista de projetos do lote.")
    ts = agora(); batch_id = str(corpo.get("lote_id") or ("RET-" + datetime.now(timezone.utc).strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:8].upper()))
    if c.execute("SELECT 1 FROM integracao_lotes WHERE id=?", (batch_id,)).fetchone():
        raise HTTPException(409, "Este lote de retorno já foi processado.")
    vistos=set(); resultados=[]; recebidos=0
    for item in projetos:
        if not isinstance(item,dict):
            resultados.append({"id_mestre":"(inválido)","resultado":"erro","mensagem":"Cada projeto do lote deve ser um objeto JSON."}); continue
        item = {**item, "pqq_id": item.get("pqq_id") or item.get("plano_id"), "pqq_revisao": item.get("pqq_revisao") or item.get("plano_revisao"),
                "ciclo_pqq": item.get("ciclo_pqq") or item.get("ciclo_id")}
        pid=str(item.get("id_mestre") or "").strip(); status=str(item.get("status") or "").lower()
        if not pid or pid in vistos:
            resultados.append({"id_mestre":pid or "(vazio)","resultado":"erro","mensagem":"ID ausente ou duplicado no lote."}); continue
        vistos.add(pid)
        row=c.execute("SELECT * FROM projetos WHERE id=?",(pid,)).fetchone()
        if not row:
            resultados.append({"id_mestre":pid,"resultado":"erro","mensagem":"Projeto não encontrado na Originação."}); continue
        p=_load(row["dados"]); pqq=p.get("pqq") or {}
        if pqq.get("status") not in ("enviado","em_analise"):
            resultados.append({"id_mestre":pid,"resultado":"erro","mensagem":"Projeto não possui envio PQQ pendente."}); continue
        if status not in ("aprovado","devolvido","em_analise","cancelado"):
            resultados.append({"id_mestre":pid,"resultado":"erro","mensagem":"Status deve ser aprovado, devolvido, em_analise ou cancelado."}); continue
        retorno={"lote_id":batch_id,"recebido_em":ts,"status":status,"pqq_id":str(item.get("pqq_id") or ""),"pqq_revisao":str(item.get("pqq_revisao") or batch_id),
                 "comentario":str(item.get("comentario") or ""),"aprovado_por":str(item.get("aprovado_por") or ""),
                 "aprovado_em":str(item.get("aprovado_em") or ""),"otica":str(item.get("otica") or "Financeira")}
        if status=="cancelado":
            p["status"]="Cancelado"
            p["tap"]={"status":"encerrado_por_cancelamento","proposta":(p.get("tap") or {}).get("proposta") or {}}
            retorno["orcamento_total"]=0.0; retorno["curva_mensal"]={}
        elif status=="aprovado":
            curva=item.get("curva_mensal")
            try:
                total=float(item.get("orcamento_total"))
                ciclo_row=c.execute("SELECT id,inicio,fim FROM ciclos WHERE id=?", (row["ciclo_id"],)).fetchone() if row["ciclo_id"] else None
                if not ciclo_row: raise ValueError()
                ciclo=dict(ciclo_row); inicio,fim=int(ciclo["inicio"]),int(ciclo["fim"])
                if str(item.get("ciclo_pqq") or "") != str(ciclo["id"]): raise ValueError()
                if int(item.get("ciclo_inicio", -1)) != inicio or int(item.get("ciclo_fim", -1)) != fim: raise ValueError()
                if not str(item.get("pqq_id") or "").strip() or total<=0 or not isinstance(curva,dict) or not curva: raise ValueError()
                curva={str(k):float(v) for k,v in curva.items()}
                if any(not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])",k) or v<0 or not (inicio <= int(k[:4]) <= fim) for k,v in curva.items()): raise ValueError()
                if abs(sum(curva.values())-total)>.02: raise ValueError()
            except (TypeError,ValueError):
                resultados.append({"id_mestre":pid,"resultado":"erro","mensagem":"O retorno aprovado precisa identificar o mesmo ciclo do projeto, informar um ID PQQ, e trazer curva mensal válida dentro do ciclo cuja soma corresponda ao total."}); continue
            retorno["orcamento_total"]=round(total,2); retorno["curva_mensal"]={k:round(v,2) for k,v in sorted(curva.items())}
            retorno["ciclo_pqq"]=str(ciclo["id"]); retorno["ciclo_inicio"]=inicio; retorno["ciclo_fim"]=fim
            ats=(p.get("fsp") or {}).get("atividades") or []
            cronograma=(p.get("fsp") or {}).get("cronograma") or "\n".join(
                f"{a.get('nome','Atividade')}: {a.get('inicio') or 'data a definir'} a {a.get('fim') or 'data a definir'}" for a in ats)
            p["tap"]={"status":"em_analise_engenharia","proposta":{"escopo":str((p.get("fsp") or {}).get("escopo") or (p.get("bc") or {}).get("escopo") or ""),
                "cronograma":str(cronograma),"revisado_por":"","revisado_em":""}}
        pqq["status"]=status; pqq["retorno"]=retorno; p["pqq"]=pqq
        _gravar_evento_projeto(c,pid,p,u,"Retorno do PQQ importado",batch_id,"Integração PQQ")
        if status=="cancelado":
            c.execute("UPDATE projetos SET status='Cancelado' WHERE id=?",(pid,))
        recebidos+=1; resultados.append({"id_mestre":pid,"resultado":"importado","mensagem":"Retorno associado ao projeto."})
    status_lote="processado" if recebidos==len(projetos) else ("parcial" if recebidos else "com_erros")
    payload={"schema_version":corpo.get("schema_version","2.0"),"tipo":"plano_para_originacao","lote_id":batch_id,"projetos":projetos}
    c.execute("INSERT INTO integracao_lotes(id,tipo,criado_em,criado_por,status,total,payload,resultado) VALUES (?,?,?,?,?,?,?,?)",
              (batch_id,"PQQ_RETORNO",ts,u["nome"],status_lote,recebidos,_dump(payload),_dump(resultados)))
    return {"lote_id":batch_id,"status":status_lote,"importados":recebidos,"total_no_lote":len(projetos),"resultados":resultados}


@app.post("/api/integracoes/pqq/retornos")
def importar_lote_retorno_pqq(corpo: dict, x_usuario: str | None = Header(None)):
    """Importação manual de um retorno em arquivo JSON (contingência quando não há hub)."""
    with _lock, conexao() as c:
        u = usuario_atual(c, x_usuario)
        if u["perfil"] != "pmo": raise HTTPException(403, "Somente PMO pode importar retornos em lote do PQQ.")
        return _importar_retorno(c, u, corpo)


def _retornos_pendentes(c: sqlite3.Connection) -> list[dict]:
    try:
        regs = hubkit.consultar("portfolio", "retorno_originacao", limit=5000)
    except Exception as e:
        raise HTTPException(502, f"Data Core indisponível: {e}")
    out = []
    for r in regs:
        pl = r.get("payload") or {}
        if str(pl.get("status", "")).lower() not in ("aprovado", "cancelado", "devolvido"):
            continue
        rec = c.execute("SELECT versao FROM integracao_recebidos WHERE chave=?", (f"{r['entity_key']}|{r.get('period','')}",)).fetchone()
        if rec and rec["versao"] >= int(r.get("version") or 1):
            continue
        proj = c.execute("SELECT dados FROM projetos WHERE id=?", (r["entity_key"],)).fetchone()
        pqq = (_load(proj["dados"]) or {}).get("pqq") or {} if proj else {}
        ret = pqq.get("retorno") or {}
        if ret and ret.get("status") == str(pl.get("status", "")).lower() and ret.get("pqq_id") == (pl.get("plano_id") or pl.get("pqq_id")) \
                and abs(float(ret.get("orcamento_total") or 0) - float(pl.get("orcamento_total") or 0)) < .01:
            continue  # republicação idêntica de um retorno já aplicado
        out.append(r)
    return out


@app.get("/api/integracoes/pqq/retornos/pendentes")
def listar_retornos_pendentes(x_usuario: str | None = Header(None)):
    with conexao() as c:
        u = usuario_atual(c, x_usuario)
        if u["perfil"] != "pmo": raise HTTPException(403, "Consulta exclusiva do PMO.")
        return [{"id_mestre": r["entity_key"], "status": (r.get("payload") or {}).get("status"),
                 "lote_id": (r.get("payload") or {}).get("return_batch_id") or (r.get("payload") or {}).get("lote_id"),
                 "atualizado_em": r.get("updated_at")} for r in _retornos_pendentes(c)]


@app.post("/api/integracoes/pqq/retornos/sincronizar")
def sincronizar_retornos(x_usuario: str | None = Header(None)):
    """Recebe do Data Core os retornos do plano ainda não processados, agrupados por lote."""
    with conexao() as c:
        u = usuario_atual(c, x_usuario)
        if u["perfil"] != "pmo": raise HTTPException(403, "Somente PMO pode receber retornos do PQQ.")
        u = dict(u)
    return _sincronizar_retornos_core(u)


def _sincronizar_retornos_core(u: dict) -> dict:
    with _lock, conexao() as c:
        pend = _retornos_pendentes(c)
        grupos: dict[str, list] = {}
        for r in pend:
            pl = dict(r.get("payload") or {}); pl.setdefault("id_mestre", r["entity_key"])
            lote = str(pl.get("return_batch_id") or pl.get("lote_id") or "RET-HUB")
            grupos.setdefault(lote, []).append((r, pl))
        total, lotes = 0, []
        for lote, itens in grupos.items():
            lote_id, n = lote, 1
            while c.execute("SELECT 1 FROM integracao_lotes WHERE id=?", (lote_id,)).fetchone():
                n += 1; lote_id = f"{lote}-{n}"
            res = _importar_retorno(c, u, {"lote_id": lote_id, "projetos": [pl for _, pl in itens]})
            total += res["importados"]; lotes.append(res)
            for r, _ in itens:
                c.execute("INSERT OR REPLACE INTO integracao_recebidos(chave,versao,lote_id,resultado,em) VALUES (?,?,?,?,?)",
                          (f"{r['entity_key']}|{r.get('period','')}", int(r.get("version") or 1), lote_id, res["status"], agora()))
        return {"importados": total, "lotes": lotes}


@app.post("/api/projetos/{pid}/tap/enviar-aprovacao")
def enviar_tap_aprovacao(pid: str, corpo: dict | None = None, x_usuario: str | None = Header(None)):
    with _lock, conexao() as c:
        u=usuario_atual(c,x_usuario)
        if u["perfil"] not in ("validador","pmo"): raise HTTPException(403,"Somente Engenharia/PMO pode enviar o TAP para aprovação.")
        row=c.execute("SELECT * FROM projetos WHERE id=?",(pid,)).fetchone()
        if not row: raise HTTPException(404,"Projeto não encontrado.")
        p=_load(row["dados"]); retorno=((p.get("pqq") or {}).get("retorno") or {})
        if retorno.get("status")!="aprovado": raise HTTPException(409,"O retorno aprovado do PQQ é necessário para iniciar o TAP.")
        tap=p.get("tap") or {}; proposta=tap.get("proposta") or {}
        if not all(str(proposta.get(k) or "").strip() for k in ("escopo","cronograma","inicio_previsto","conclusao_compromissada")):
            raise HTTPException(400,"Informe escopo, cronograma, início planejado e conclusão compromissada revisados pela Engenharia.")
        if tap.get("status")=="em_aprovacao": raise HTTPException(409,"O TAP já está aguardando aprovação executiva.")
        tap["status"]="em_aprovacao"; tap["enviado_por"]=u["nome"]; tap["enviado_por_id"]=u["id"]; tap["enviado_em"]=agora(); tap["comentario_envio"]=(corpo or {}).get("comentario","")
        proposta["revisado_por"]=u["nome"]; proposta["revisado_em"]=agora(); tap["proposta"]=proposta; p["tap"]=tap
        _gravar_evento_projeto(c,pid,p,u,"TAP enviado para aprovação executiva",tap["comentario_envio"],"TAP")
        return compor_projetos(c,[pid])[0]


@app.post("/api/projetos/{pid}/tap/decidir")
def decidir_tap(pid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with _lock, conexao() as c:
        u=usuario_atual(c,x_usuario); _aprovar_processo(u)
        row=c.execute("SELECT * FROM projetos WHERE id=?",(pid,)).fetchone()
        if not row: raise HTTPException(404,"Projeto não encontrado.")
        p=_load(row["dados"]); tap=p.get("tap") or {}
        if tap.get("status")!="em_aprovacao": raise HTTPException(409,"TAP não está aguardando aprovação executiva.")
        _segregacao(u, tap.get("enviado_por_id"), "enviou o TAP para aprovação")
        decisao=str(corpo.get("decisao") or "").lower()
        if decisao not in ("aprovar","devolver"): raise HTTPException(400,"Decisão deve ser aprovar ou devolver.")
        comentario=str(corpo.get("comentario") or "").strip()
        if decisao=="devolver" and not comentario: raise HTTPException(400,"Informe o motivo da devolução.")
        ts=agora(); tap.update({"status":"aprovado" if decisao=="aprovar" else "devolvido","decidido_por":str(corpo.get("aprovador_externo") or u["nome"]),"decidido_em":str(corpo.get("decidido_em") or ts),"comentario":comentario})
        if decisao=="aprovar":
            digest=uuid.uuid4().hex; destino=ARQ_DIR/f"{digest}.pdf"; _gerar_tap_pdf(p,destino)
            doc={"id":"tap-"+digest[:12],"tipo":"TAP","nome":"TAP - "+str(p.get("nome") or pid)+".pdf","url":"","arquivo":destino.name,"por":u["nome"],"em":ts}
            c.execute("INSERT INTO documentos(id,projeto_id,tipo,nome,url,arquivo,por,em) VALUES (?,?,?,?,?,?,?,?)",
                      (doc["id"],pid,doc["tipo"],doc["nome"],"",doc["arquivo"],doc["por"],doc["em"]))
            p.setdefault("pmp",{}).update({"status":"pronto_para_publicacao","event_id":"PMP-"+uuid.uuid4().hex[:12].upper(),
                "idempotency_key":pid+"-TAP-1","aprovado_em":ts,"aprovado_por":u["nome"],"tap_documento_id":doc["id"],
                "tendencia_status":"aguardando_publicacao_pmp"})
            p["tap"]=tap
            docs_antes=p.get("documentos")
            p["documentos"]=list(docs_antes or [])+[doc]   # só para montar o PDF no payload
            publicado=_publicar_tap({**p,**{k:row[k] for k in ("id","nome","unidade","diretoria","pep")}},u)
            if docs_antes is None: p.pop("documentos",None)
            else: p["documentos"]=docs_antes
            acao=("TAP aprovado e publicado na carteira da PMP" if publicado else
                  "TAP aprovado; publicação na PMP pendente (será tentada novamente)")
        else:
            p["tap"]=tap; acao="TAP devolvido para ajuste"
        _gravar_evento_projeto(c,pid,p,u,acao,comentario,"TAP")
        return compor_projetos(c,[pid])[0]


def _payload_tap(p: dict) -> dict:
    tap=p.get("tap") or {}; pmp=p.get("pmp") or {}; ret=((p.get("pqq") or {}).get("retorno") or {}); prop=tap.get("proposta") or {}
    pdf=None
    doc=next((d for d in p.get("documentos",[]) if d.get("id")==pmp.get("tap_documento_id")),None)
    nome=(doc or {}).get("arquivo") or ""
    if nome and re.fullmatch(r"[a-f0-9]{32}\.pdf",nome) and (ARQ_DIR/nome).is_file():
        pdf={"nome":(doc or {}).get("nome"),"mime_type":"application/pdf","base64":base64.b64encode((ARQ_DIR/nome).read_bytes()).decode("ascii")}
    return {"schema_version":"2.0","tipo":"originacao_para_pmp","status":"tap_aprovada","event_id":pmp.get("event_id"),
            "idempotency_key":pmp.get("idempotency_key"),"id_mestre":p["id"],"nome":p.get("nome"),"unidade":p.get("unidade"),
            "area":p.get("diretoria"),"codigo_erp":p.get("pep") or "","solicitante":p.get("solicitante",""),"responsavel":p.get("responsavel",""),
            "escopo_aprovado":prop.get("escopo"),"cronograma_aprovado":prop.get("cronograma"),"inicio_previsto":prop.get("inicio_previsto"),
            "conclusao_compromissada":prop.get("conclusao_compromissada"),"premissas":prop.get("premissas",""),
            "orcamento_total_aprovado":ret.get("orcamento_total"),"curva_mensal_aprovada":ret.get("curva_mensal") or {},
            "plano_id":ret.get("pqq_id"),"plano_aprovado_por":ret.get("aprovado_por"),"plano_aprovado_em":ret.get("aprovado_em"),
            "ciclo_id":ret.get("ciclo_pqq"),"ciclo_inicio":ret.get("ciclo_inicio"),"ciclo_fim":ret.get("ciclo_fim"),
            "tap_aprovado_por":pmp.get("aprovado_por"),"tap_aprovado_em":pmp.get("aprovado_em"),"tap_documento_id":pmp.get("tap_documento_id"),"tap_pdf":pdf}


def _publicar_tap(p: dict, u: sqlite3.Row) -> bool:
    """Publica o TAP aprovado para a PMP. Falha não bloqueia a aprovação: fica na fila para nova tentativa."""
    pmp=p.setdefault("pmp",{})
    try:
        hubkit.publicar("pmp","tap_aprovado",p["id"],_payload_tap(p),source_user=_quem(u),source_version=APP_VERSION,source_application="originacao")
    except Exception as e:
        pmp.update({"status":"pronto_para_publicacao","erro_publicacao":str(e)[:300]}); return False
    pmp.update({"status":"publicado","publicado_em":agora(),"erro_publicacao":""}); return True


@app.post("/api/integracoes/pmp/sincronizar")
def sincronizar_pmp(x_usuario: str | None = Header(None)):
    """Publica na PMP os TAPs aprovados que ainda estão na fila (ex.: hub estava fora do ar na aprovação)."""
    with _lock, conexao() as c:
        u=usuario_atual(c,x_usuario)
        if u["perfil"] not in ("pmo","validador"): raise HTTPException(403,"Publicação na PMP exclusiva da Engenharia/PMO.")
        ok=falha=0
        for comp in compor_projetos(c):
            if (comp.get("pmp") or {}).get("status")!="pronto_para_publicacao": continue
            row=c.execute("SELECT * FROM projetos WHERE id=?",(comp["id"],)).fetchone(); d=_load(row["dados"])
            if _publicar_tap({**d,**{k:row[k] for k in ("id","nome","unidade","diretoria","pep")},"documentos":comp.get("documentos",[])},u):
                ok+=1; _gravar_evento_projeto(c,comp["id"],d,u,"TAP publicado na PMP pelo Data Core","","TAP")
            else:
                falha+=1
        return {"publicados":ok,"falhas":falha}


@app.get("/api/integracoes/pqq/lotes")
def listar_lotes_pqq(x_usuario: str | None = Header(None)):
    with conexao() as c:
        u=usuario_atual(c,x_usuario)
        if u["perfil"]!="pmo": raise HTTPException(403,"Consulta de lotes exclusiva do PMO.")
        return [{"id":r["id"],"tipo":r["tipo"],"criado_em":r["criado_em"],"criado_por":r["criado_por"],
                 "status":r["status"],"total":r["total"],"resultado":_load(r["resultado"])}
                for r in c.execute("SELECT * FROM integracao_lotes ORDER BY criado_em DESC")]


@app.get("/api/integracoes/pmp/fila")
def fila_publicacao_pmp(pdf: bool = False, x_usuario: str | None = Header(None)):
    with conexao() as c:
        u=usuario_atual(c,x_usuario)
        if u["perfil"] not in ("pmo","validador"): raise HTTPException(403,"Consulta da fila PMP exclusiva da Engenharia/PMO.")
        fila=[p for p in compor_projetos(c) if (p.get("pmp") or {}).get("status") in ("pronto_para_publicacao","publicado")]
        if pdf:
            for p in fila:
                p["pmp"]["tap_pdf"]=_payload_tap(p).get("tap_pdf")
        return fila


@app.post("/api/sequencia")
def proximo_id(x_usuario: str | None = Header(None)):
    with _lock, conexao() as c:
        usuario_atual(c, x_usuario)
        ult = c.execute("SELECT ultimo FROM sequencia WHERE nome='projeto'").fetchone()[0]
        maior = 0
        for (pid,) in c.execute("SELECT id FROM projetos"):
            dig = re.sub(r"\D", "", pid)
            if dig:
                maior = max(maior, int(dig))
        n = max(ult, maior) + 1
        c.execute("UPDATE sequencia SET ultimo=? WHERE nome='projeto'", (n,))
    return {"id": f"ORI-{n:06d}"}


@app.post("/api/projetos/{pid}/arquivos")
async def enviar_arquivo(pid: str, arquivo: UploadFile = File(...), x_usuario: str | None = Header(None)):
    with conexao() as c:
        usuario_atual(c, x_usuario)
        if not c.execute("SELECT 1 FROM projetos WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404, "Projeto não encontrado.")
    ext = Path(arquivo.filename or "").suffix.lower()
    if ext not in EXT_PERMITIDAS:
        raise HTTPException(400, f"Tipo de arquivo não aceito ({ext or 'sem extensão'}).")
    conteudo = await arquivo.read(MAX_UPLOAD + 1)
    if len(conteudo) > MAX_UPLOAD:
        raise HTTPException(413, "Arquivo acima de 20 MB.")
    nome = f"{uuid.uuid4().hex}{ext}"
    (ARQ_DIR / nome).write_bytes(conteudo)
    return {"arquivo": nome, "nomeOriginal": arquivo.filename}


@app.get("/api/arquivos/{nome}")
def baixar_arquivo(nome: str):
    if not re.fullmatch(r"[a-f0-9]{32}\.[a-z0-9]{1,5}", nome):
        raise HTTPException(404)
    caminho = ARQ_DIR / nome
    if not caminho.exists():
        raise HTTPException(404, "Arquivo não encontrado.")
    with conexao() as c:
        d = c.execute("SELECT nome FROM documentos WHERE arquivo=?", (nome,)).fetchone()
    baixa = (d["nome"] if d else nome)
    if not Path(baixa).suffix:
        baixa += caminho.suffix
    return FileResponse(caminho, filename=baixa, content_disposition_type="inline")


@app.post("/api/exportar/xlsx")
def exportar_xlsx(corpo: dict):
    """Recebe as linhas já calculadas pela interface (com maturidade) e gera o Excel."""
    wb = Workbook()
    for i, (aba, linhas) in enumerate([("Projetos", corpo.get("linhas") or []), ("Ciclos", corpo.get("ciclos") or [])]):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = aba
        if not linhas:
            ws.append(["Sem dados"])
            continue
        cols = list(linhas[0].keys())
        ws.append([rotular(col) for col in cols])
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="0E5A60")
        for ln in linhas:
            ws.append([ln.get(k) for k in cols])
        for j, col in enumerate(cols, start=1):
            largura = max(len(str(col)), *(len(str(ln.get(col) or "")) for ln in linhas[:200]))
            ws.column_dimensions[get_column_letter(j)].width = min(max(10, largura + 2), 60)
            if col.startswith("CAPEX"):
                for row in ws.iter_rows(min_row=2, min_col=j, max_col=j):
                    row[0].number_format = "#,##0"
        ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    nome = f"originacao-projetos-{datetime.now():%Y-%m-%d}.xlsx"
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@app.post("/api/admin/zerar")
def zerar_base(x_usuario: str | None = Header(None)):
    with _lock, conexao() as c:
        u = usuario_atual(c, x_usuario)
        if u["perfil"] != "pmo":
            raise HTTPException(403, "Somente o PMO zera a base de testes.")
        for t in ("documentos", "validacoes", "historico", "capex_ano", "projetos", "solicitacoes", "ciclos", "integracao_lotes"):
            c.execute(f"DELETE FROM {t}")
        c.execute("UPDATE sequencia SET ultimo=0")
    for f in ARQ_DIR.glob("*"):
        if f.is_file():
            f.unlink()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=os.environ.get("ORIGINACAO_HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT") or os.environ.get("ORIGINACAO_PORT", "8780")),
                root_path=os.environ.get("HUB_ROOT_PATH", ""))



# ---------------------------------------------------------------- fluxo automático do ecossistema
# No hub, a Originação recebe sozinha os retornos do plano e republica TAPs que ficaram na fila;
# e publica em agenda/pendencia o que depende de alguém (validações, aprovações, TAP a preparar).
SYNC = {"ultimo": None, "ok": None, "resultado": None, "erro": None, "automatico": True, "intervalo_s": int(os.environ.get("SYNC_INTERVALO_S") or 120)}
_USUARIO_SISTEMA = {"id": "sistema", "nome": "Integração automática", "perfil": "pmo", "unidade": None, "email": "", "origem": "sistema"}
ETAPA_ROTULO = {"FSE": "Ficha de solicitação", "BC": "Caso de negócio", "FSP": "Ficha do projeto", "ELEG": "Elegibilidade"}


def _acao(codigo: str, rotulo: str, caminho: str, corpo: dict | None = None, comentario: str = "nenhum", estilo: str = "", campo: str = "comentario") -> dict:
    """Ação executável pela agenda do ecossistema (mesmo formato do kit): a agenda chama esta ferramenta em nome da pessoa."""
    return {"codigo": codigo, "rotulo": rotulo, "metodo": "POST", "caminho": caminho, "corpo": corpo or {}, "comentario": comentario, "campo": campo, "estilo": estilo}


def _destinatarios(c: sqlite3.Connection, perfis: list, unidades: list | None = None) -> list[dict]:
    """Pessoas do hub com estes perfis aqui (a agenda entrega a pendência a elas)."""
    out = []
    for r in c.execute("SELECT * FROM usuarios WHERE origem='hub'"):
        if r["perfil"] in perfis and not (unidades and r["unidade"] and r["unidade"] not in unidades):
            out.append({"id_hub": r["id"][2:] if r["id"].startswith("h-") else r["id"], "email": r["email"] or "", "nome": r["nome"]})
    return sorted(out, key=lambda x: x["id_hub"])


def _pendencias_originacao(c: sqlite3.Connection) -> list[dict]:
    out = []
    voc = {**ORG_PADRAO["vocabulario"], **(org().get("vocabulario") or {})}
    etapas = config_app().get("etapas") or {}
    tap_sigla = voc.get("termo_abertura") or "TAP"
    for r in c.execute("SELECT * FROM solicitacoes WHERE status='EM_ANALISE'"):
        out.append({"chave": f"sol:{r['request_uuid']}", "natureza": "tarefa", "esforco_h": 1, "tipo": "analise", "titulo": f"Solicitação {r['protocolo']} aguardando análise",
                    "descricao": r["requester_nome"], "unidades": [r["unidade"]] if r["unidade"] else [], "perfis": ["pmo", "validador"],
                    "link": "#/solicitacoes", "prioridade": "normal"})
    for row in c.execute("SELECT * FROM projetos WHERE status='Ativo'"):
        p = _load(row["dados"]); nome = row["nome"]; uni = [row["unidade"]] if row["unidade"] else []
        g = (p.get("gates") or {}).get(row["estagio"]) or {}
        if g.get("status") == "em_validacao":
            out.append({"chave": f"gate:{row['id']}:{row['estagio']}", "natureza": "acao", "tipo": "validacao", "titulo": f"Validar {etapas.get(row['estagio']) or ETAPA_ROTULO.get(row['estagio'], row['estagio'])} — {nome}",
                        "projeto": row["id"], "unidades": uni, "perfis": ["validador", "gestor", "pmo"], "link": f"#/projeto/{row['id']}", "prioridade": "normal"})
        pqq = p.get("pqq") or {}
        if (pqq.get("aprovacao_envio") or {}).get("status") == "em_aprovacao":
            out.append({"chave": f"envio:{row['id']}", "natureza": "acao", "tipo": "aprovacao", "titulo": f"Aprovar envio ao plano — {nome}", "projeto": row["id"], "unidades": uni,
                        "perfis": ["gerente_executivo", "pmo"], "link": f"#/projeto/{row['id']}", "prioridade": "alta",
                        "excluir_ids": [(pqq.get("aprovacao_envio") or {}).get("solicitado_por_id")],
                        "acoes": [_acao("aprovar", "Aprovar envio", f"/api/projetos/{row['id']}/pqq/decidir-envio", {"decisao": "aprovar"}, "opcional", "ok"),
                                  _acao("devolver", "Devolver", f"/api/projetos/{row['id']}/pqq/decidir-envio", {"decisao": "devolver"}, "obrigatorio", "bad")]})
        tap = p.get("tap") or {}
        if (pqq.get("retorno") or {}).get("status") == "aprovado" and tap.get("status") in (None, "", "rascunho", "em_analise_engenharia", "devolvido"):
            out.append({"chave": f"tap:{row['id']}", "natureza": "tarefa", "esforco_h": 4, "tipo": "preparar", "titulo": f"Preparar o {tap_sigla} — {nome}", "projeto": row["id"], "unidades": uni,
                        "perfis": ["validador", "pmo"], "link": f"#/projeto/{row['id']}", "prioridade": "alta"})
        if tap.get("status") == "em_aprovacao":
            out.append({"chave": f"tapap:{row['id']}", "natureza": "acao", "tipo": "aprovacao", "titulo": f"Aprovar o {tap_sigla} — {nome}", "projeto": row["id"], "unidades": uni,
                        "perfis": ["gerente_executivo", "pmo"], "link": f"#/projeto/{row['id']}", "prioridade": "alta",
                        "excluir_ids": [tap.get("enviado_por_id")],
                        "acoes": [_acao("aprovar", f"Aprovar o {tap_sigla}", f"/api/projetos/{row['id']}/tap/decidir", {"decisao": "aprovar"}, "opcional", "ok"),
                                  _acao("devolver", "Devolver", f"/api/projetos/{row['id']}/tap/decidir", {"decisao": "devolver"}, "obrigatorio", "bad")]})
        if (p.get("pmp") or {}).get("status") == "pronto_para_publicacao":
            out.append({"chave": f"fila:{row['id']}", "natureza": "acao", "tipo": "integracao", "titulo": f"{tap_sigla} aprovado aguardando publicação na carteira — {nome}",
                        "projeto": row["id"], "perfis": ["pmo"], "link": "#/integracoes", "prioridade": "normal"})
    return out


def _publicar_pendencias(c: sqlite3.Connection) -> dict:
    c.execute("CREATE TABLE IF NOT EXISTS pendencias_publicadas(chave TEXT PRIMARY KEY, hash TEXT NOT NULL, em TEXT NOT NULL)")
    atuais = {}
    for p in _pendencias_originacao(c):
        chave = "originacao:" + p.pop("chave")
        corpo = {**p, "origem_app": "originacao", "origem_nome": "Originação de Projetos", "status": "aberta"}
        corpo.setdefault("natureza", "acao")
        if "destinatarios" not in corpo and corpo.get("perfis"):
            corpo["destinatarios"] = _destinatarios(c, corpo["perfis"], corpo.get("unidades") or None)
        fora = {x[2:] if x.startswith("h-") else x for x in corpo.pop("excluir_ids", None) or [] if x}  # segregação: quem pediu não decide
        if fora:
            corpo["destinatarios"] = [d for d in corpo.get("destinatarios") or [] if d["id_hub"] not in fora]
        if corpo["natureza"] in ("acao", "tarefa"):  # MASTER TESTE (ambiente de teste) recebe toda decisão e tarefa
            ids = {d["id_hub"] for d in corpo.get("destinatarios") or []}
            corpo["destinatarios"] = sorted(list(corpo.get("destinatarios") or []) + [
                {"id_hub": m["id"], "email": m.get("email") or "", "nome": m.get("nome") or m["id"], "master": True}
                for m in hubkit.masters() if m["id"] not in ids], key=lambda x: x["id_hub"])
        atuais[chave] = (corpo, hashlib.sha1(json.dumps(corpo, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
    antigos = {r["chave"]: r["hash"] for r in c.execute("SELECT * FROM pendencias_publicadas")}
    pub = res = 0
    for chave, (corpo, h) in atuais.items():
        if antigos.get(chave) != h:
            hubkit.publicar("agenda", "pendencia", chave, corpo, source_application="originacao", source_version=APP_VERSION)
            c.execute("INSERT OR REPLACE INTO pendencias_publicadas(chave,hash,em) VALUES (?,?,?)", (chave, h, agora())); pub += 1
    for chave in set(antigos) - set(atuais):
        hubkit.publicar("agenda", "pendencia", chave, {"status": "resolvida", "origem_app": "originacao", "resolvida_em": agora()},
                        source_application="originacao", source_version=APP_VERSION)
        c.execute("DELETE FROM pendencias_publicadas WHERE chave=?", (chave,)); res += 1
    return {"abertas": len(atuais), "publicadas": pub, "resolvidas": res}


def executar_sync() -> dict:
    res, erro = {}, None
    try:
        with _lock, conexao() as c:
            u = _USUARIO_SISTEMA
            pend = _retornos_pendentes(c)
            if pend:
                res["retornos_do_plano"] = 0
            c.commit()
        if pend:
            r = _sincronizar_retornos_core(u)
            res["retornos_do_plano"] = r.get("importados", 0)
        with _lock, conexao() as c:
            n = 0
            for comp in compor_projetos(c):
                if (comp.get("pmp") or {}).get("status") != "pronto_para_publicacao":
                    continue
                row = c.execute("SELECT * FROM projetos WHERE id=?", (comp["id"],)).fetchone(); d = _load(row["dados"])
                if _publicar_tap({**d, **{k: row[k] for k in ("id", "nome", "unidade", "diretoria", "pep")}, "documentos": comp.get("documentos", [])}, u):
                    n += 1; _gravar_evento_projeto(c, comp["id"], d, u, "TAP publicado na PMP pelo Data Core (automático)", "", "TAP")
            if n:
                res["taps_publicados"] = n
            res["pendencias"] = _publicar_pendencias(c)
    except Exception as e:
        erro = getattr(e, "detail", None) or str(e)
    SYNC.update(ultimo=time.time(), ok=erro is None, resultado=res if erro is None else SYNC.get("resultado"), erro=erro)
    return SYNC


def _laco_sync() -> None:
    time.sleep(3)
    while True:
        if hubkit.no_hub():
            executar_sync()
        time.sleep(SYNC["intervalo_s"])


@app.get("/api/sync/status")
def sync_status():
    return {**SYNC, "modo": "hub" if hubkit.no_hub() else "isolado", "agora": time.time()}


@app.post("/api/sync/agora")
def sync_agora(x_usuario: str | None = Header(None)):
    with conexao() as c:
        usuario_atual(c, x_usuario)
    return {**executar_sync(), "agora": time.time()}


app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")
