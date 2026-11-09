"""PMP — Gestão da Carteira de Projetos em execução (genérica).

Carteira (a partir do TAP aprovado, do plano aprovado ou de planilha) · curvas mensais de orçado, realizado e compromisso
(econômico, financeiro e físico) · rodadas de tendência com classificação de desvios e aprovação do PMO ·
mudanças · atas e ações · comentários e documentos. Ao fechar uma rodada, publica no Data Core a tendência
para o Analisador e a execução para o Plano de Investimentos.
"""
from __future__ import annotations
import base64, hashlib, io, os, re, sqlite3, uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

import hubkit
from appkit import Base, agora, dump, load

BASE_DIR = Path(__file__).resolve().parent
VERSAO = "1.2.2"
DIMS = {"econ": "Econômico", "fin": "Financeiro", "fis": "Físico"}
TIPOS = {"orcado": "Orçado", "realizado": "Realizado", "comp_req": "Compromisso — requisição", "comp_ped": "Compromisso — pedido"}
MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
SCHEMA = """
CREATE TABLE IF NOT EXISTS projetos(codigo TEXT PRIMARY KEY, id_mestre TEXT, plano_id TEXT, nome TEXT NOT NULL, unidade TEXT, area TEXT,
  grupo TEXT, gerente TEXT, etapa TEXT, status TEXT NOT NULL DEFAULT 'ativo', tipo_investimento TEXT, inicio_previsto TEXT,
  conclusao_prevista TEXT, escopo TEXT, peso_fisico REAL, origem TEXT NOT NULL DEFAULT 'manual', extras TEXT NOT NULL DEFAULT '{}',
  criado_em TEXT, atualizado_em TEXT);
CREATE TABLE IF NOT EXISTS curvas(codigo TEXT NOT NULL REFERENCES projetos(codigo) ON DELETE CASCADE, ano INTEGER NOT NULL, dimensao TEXT NOT NULL,
  tipo TEXT NOT NULL, meses TEXT NOT NULL, origem TEXT, atualizado_em TEXT, PRIMARY KEY(codigo, ano, dimensao, tipo));
CREATE TABLE IF NOT EXISTS anos_futuros(codigo TEXT NOT NULL REFERENCES projetos(codigo) ON DELETE CASCADE, tipo TEXT NOT NULL, dimensao TEXT NOT NULL,
  valores TEXT NOT NULL, PRIMARY KEY(codigo, tipo, dimensao));
CREATE TABLE IF NOT EXISTS rodadas(id TEXT PRIMARY KEY, ano INTEGER NOT NULL, mes_ref INTEGER NOT NULL, nome TEXT NOT NULL, status TEXT NOT NULL,
  aberta_em TEXT, aberta_por TEXT, fechada_em TEXT, fechada_por TEXT, publicacao TEXT);
CREATE TABLE IF NOT EXISTS tendencias(rodada TEXT NOT NULL REFERENCES rodadas(id) ON DELETE CASCADE, codigo TEXT NOT NULL, dimensao TEXT NOT NULL,
  meses TEXT NOT NULL, anos_futuros TEXT NOT NULL DEFAULT '{}', desvios TEXT NOT NULL DEFAULT '[]', justificativa TEXT, status TEXT NOT NULL,
  por TEXT, em TEXT, decidido_por TEXT, decidido_em TEXT, parecer TEXT, PRIMARY KEY(rodada, codigo, dimensao));
CREATE TABLE IF NOT EXISTS mudancas(id TEXT PRIMARY KEY, codigo TEXT NOT NULL REFERENCES projetos(codigo) ON DELETE CASCADE, titulo TEXT NOT NULL,
  tipo TEXT, descricao TEXT, impacto_custo REAL, impacto_prazo_dias INTEGER, status TEXT NOT NULL, solicitado_por TEXT, solicitado_em TEXT,
  decidido_por TEXT, decidido_em TEXT, parecer TEXT);
CREATE TABLE IF NOT EXISTS atas(id TEXT PRIMARY KEY, codigo TEXT, data TEXT, tipo TEXT, titulo TEXT NOT NULL, participantes TEXT, pauta TEXT,
  deliberacoes TEXT, status TEXT NOT NULL DEFAULT 'rascunho', criado_por TEXT, criado_em TEXT, aprovado_por TEXT, aprovado_em TEXT);
CREATE TABLE IF NOT EXISTS acoes(id TEXT PRIMARY KEY, ata_id TEXT REFERENCES atas(id) ON DELETE CASCADE, codigo TEXT, descricao TEXT NOT NULL,
  responsavel TEXT, prazo TEXT, status TEXT NOT NULL DEFAULT 'aberta', concluida_em TEXT);
CREATE TABLE IF NOT EXISTS comentarios(seq INTEGER PRIMARY KEY AUTOINCREMENT, codigo TEXT NOT NULL, texto TEXT NOT NULL, por TEXT, em TEXT);
CREATE TABLE IF NOT EXISTS documentos(id TEXT PRIMARY KEY, codigo TEXT NOT NULL, tipo TEXT, nome TEXT, arquivo TEXT, por TEXT, em TEXT);
CREATE TABLE IF NOT EXISTS servicos_vinculados(chave TEXT PRIMARY KEY, codigo TEXT, payload TEXT NOT NULL, em TEXT);
CREATE TABLE IF NOT EXISTS cargas(seq INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT, arquivo TEXT, linhas INTEGER, resultado TEXT, por TEXT, em TEXT);
"""
PERFIS = {"pmo": "PMO (administrador)", "gerente": "Gerente de projeto", "leitor": "Leitura"}
DEMO = [("u-demo-pmo", "PMO Demo", "pmo", None), ("u-demo-ger1", "Gerente Demo 1", "gerente", 0), ("u-demo-ger2", "Gerente Demo 2", "gerente", 1),
        ("u-demo-leitor", "Leitor Demo", "leitor", None)]
CONFIG = {
    "categorias_desvio": {
        "econ": ["Postergação", "Antecipação", "Cancelamento", "Redução de escopo", "Economia", "Acréscimo de escopo", "Custo adicional", "Não orçado", "Migração entre projetos"],
        "fin": ["Postergação", "Antecipação", "Cancelamento", "Redução de escopo", "Economia", "Acréscimo de escopo", "Custo adicional", "Não orçado", "Migração entre projetos", "Reajuste de índices"],
        "fis": ["Postergação", "Antecipação", "Cancelamento", "Fatores externos / cliente", "Desempenho de engenharia", "Desempenho de contratação", "Desempenho de implantação", "Não orçado"]},
    "tolerancia": {"econ": 1000.0, "fin": 1000.0, "fis": 0.5},
    "etapas": ["Planejamento", "Contratação", "Execução", "Comissionamento", "Encerramento"],
    "tipos_mudanca": ["Escopo", "Prazo", "Custo", "Escopo e custo", "Outro"],
    "tipos_ata": ["Reunião de status", "Comitê", "Kick-off", "Lições aprendidas", "Outro"],
    "campos_extras": [],  # ex.: [{"chave": "contrato", "rotulo": "Contrato", "tipo": "texto"}, {"chave": "risco", "rotulo": "Risco", "tipo": "lista", "opcoes": ["Baixo", "Médio", "Alto"]}]
    "rotulos": {},
}
B = Base("pmp", "PMP · Carteira de Projetos", VERSAO, BASE_DIR, SCHEMA, PERFIS, admin="pmo", padrao="leitor", demo=DEMO, config_padrao=CONFIG)


@asynccontextmanager
async def _vida(_app):
    B.iniciar()
    yield

app = FastAPI(title="PMP · Carteira de Projetos", version=VERSAO, lifespan=_vida)
B.montar(app, lambda: {"dimensoes": DIMS, "tipos_curva": TIPOS, "meses": MESES, **{k: B.config().get(k) or [] for k in ("categorias_desvio", "tolerancia", "etapas", "tipos_mudanca", "tipos_ata", "campos_extras")}})


# ───────────────────────────────────────────────── utilidades
def _proj(c, codigo: str) -> sqlite3.Row:
    r = c.execute("SELECT * FROM projetos WHERE codigo=?", (codigo,)).fetchone()
    if not r:
        raise HTTPException(404, "Projeto não encontrado.")
    return r


def _pode_editar(u: dict, p) -> None:
    if u["perfil"] == "pmo":
        return
    if u["perfil"] != "gerente":
        raise HTTPException(403, "Seu perfil é somente leitura.")
    if u.get("unidade") and p["unidade"] and p["unidade"] != u["unidade"]:
        raise HTTPException(403, "Gerente só altera projetos da própria unidade.")


def _m12(v) -> list[float]:
    v = list(v or [])[:12]
    return [round(float(x or 0), 4) for x in v] + [0.0] * (12 - len(v))


def _curva(c, codigo: str, ano: int, dim: str, tipo: str) -> list[float]:
    r = c.execute("SELECT meses FROM curvas WHERE codigo=? AND ano=? AND dimensao=? AND tipo=?", (codigo, ano, dim, tipo)).fetchone()
    return _m12(load(r["meses"], [])) if r else [0.0] * 12


def _gravar_curva(c, codigo: str, ano: int, dim: str, tipo: str, meses, origem: str) -> None:
    c.execute("INSERT OR REPLACE INTO curvas(codigo,ano,dimensao,tipo,meses,origem,atualizado_em) VALUES (?,?,?,?,?,?,?)",
              (codigo, int(ano), dim, tipo, dump(_m12(meses)), origem, agora()))


def _anos_curvas(c, codigo: str) -> list[int]:
    return [r[0] for r in c.execute("SELECT DISTINCT ano FROM curvas WHERE codigo=? ORDER BY ano", (codigo,))]


def _rodada(c, rid: str) -> sqlite3.Row:
    r = c.execute("SELECT * FROM rodadas WHERE id=?", (rid,)).fetchone()
    if not r:
        raise HTTPException(404, "Rodada não encontrada.")
    return r


def _rodada_aberta(c):
    return c.execute("SELECT * FROM rodadas WHERE status='aberta' ORDER BY id DESC").fetchone()


def _ultima_fechada(c, antes_de: str | None = None):
    sql, a = "SELECT * FROM rodadas WHERE status='fechada'", []
    if antes_de:
        sql += " AND id<?"; a.append(antes_de)
    return c.execute(sql + " ORDER BY id DESC", a).fetchone()


def _acum(meses: list[float], ate: int) -> float:
    return sum(meses[:ate])


def _tend_proposta(c, rod, codigo: str, dim: str) -> tuple[list[float], dict]:
    """Tendência inicial da rodada: meses fechados = realizado (econ/fin) ou % real (físico); meses futuros = última tendência oficial ou orçado."""
    ano, m = int(rod["ano"]), int(rod["mes_ref"])
    real = _curva(c, codigo, ano, dim, "realizado")
    orc = _curva(c, codigo, ano, dim, "orcado")
    ant = _ultima_fechada(c, rod["id"])
    prev, fut = None, {}
    if ant and int(ant["ano"]) == ano:
        t = c.execute("SELECT * FROM tendencias WHERE rodada=? AND codigo=? AND dimensao=?", (ant["id"], codigo, dim)).fetchone()
        if t:
            prev, fut = _m12(load(t["meses"], [])), load(t["anos_futuros"], {})
    if not fut:
        r = c.execute("SELECT valores FROM anos_futuros WHERE codigo=? AND tipo='orcado' AND dimensao=?", (codigo, dim)).fetchone()
        fut = load(r["valores"], {}) if r else {}
    base = prev or orc
    meses = [real[i] if i < m else base[i] for i in range(12)]
    return meses, fut


def _resumo_proj(c, p, ano: int, mes_ref: int, rodada_id: str | None) -> dict:
    d = dict(p)
    for dim in ("econ", "fin", "fis"):
        orc, real = _curva(c, p["codigo"], ano, dim, "orcado"), _curva(c, p["codigo"], ano, dim, "realizado")
        t = c.execute("SELECT * FROM tendencias WHERE rodada=? AND codigo=? AND dimensao=?", (rodada_id, p["codigo"], dim)).fetchone() if rodada_id else None
        tend = _m12(load(t["meses"], [])) if t else [real[i] if i < mes_ref else orc[i] for i in range(12)]
        if dim == "fis":
            d["fis"] = {"plan": orc[mes_ref - 1] if mes_ref else 0, "real": real[mes_ref - 1] if mes_ref else 0, "dez_plan": orc[11], "dez_tend": tend[11]}
        else:
            d[dim] = {"orcado_ano": sum(orc), "realizado_acum": _acum(real, mes_ref), "orcado_acum": _acum(orc, mes_ref), "tendencia_ano": sum(tend),
                      "status_tend": t["status"] if t else None}
    return d


# ───────────────────────────────────────────────── carteira
@app.get("/api/carteira")
def carteira(ano: int | None = None):
    with B.con() as c:
        rod = _rodada_aberta(c) or _ultima_fechada(c)
        ano = ano or (int(rod["ano"]) if rod else int(B.org().get("ano_base") or 2026))
        mes = int(rod["mes_ref"]) if rod and int(rod["ano"]) == ano else 12
        rid = rod["id"] if rod and int(rod["ano"]) == ano else None
        ps = [_resumo_proj(c, p, ano, mes, rid) for p in c.execute("SELECT * FROM projetos ORDER BY unidade, nome")]
        serie = {k: [0.0] * 12 for k in ("orc", "real", "tend")}
        for p in c.execute("SELECT codigo FROM projetos WHERE status!='cancelado'"):
            orc, real = _curva(c, p[0], ano, "fin", "orcado"), _curva(c, p[0], ano, "fin", "realizado")
            t = c.execute("SELECT meses FROM tendencias WHERE rodada=? AND codigo=? AND dimensao='fin'", (rid, p[0])).fetchone() if rid else None
            tend = _m12(load(t["meses"], [])) if t else [real[i] if i < mes else orc[i] for i in range(12)]
            for i in range(12):
                serie["orc"][i] += orc[i]; serie["real"][i] += real[i]; serie["tend"][i] += tend[i]
        anos = [r[0] for r in c.execute("SELECT DISTINCT ano FROM curvas ORDER BY ano")]
        return {"ano": ano, "mes_ref": mes, "rodada": dict(rod) if rod else None, "anos": anos, "projetos": ps, "serie_fin": serie}


@app.get("/api/projetos/{codigo}")
def obter_projeto(codigo: str, ano: int | None = None):
    with B.con() as c:
        p = _proj(c, codigo)
        rod = _rodada_aberta(c)
        anos = _anos_curvas(c, codigo)
        ano = ano or (int(rod["ano"]) if rod else (anos[-1] if anos else int(B.org().get("ano_base") or 2026)))
        curvas = {dim: {t: _curva(c, codigo, ano, dim, t) for t in TIPOS} for dim in DIMS}
        futuros = {f"{r['tipo']}|{r['dimensao']}": load(r["valores"], {}) for r in c.execute("SELECT * FROM anos_futuros WHERE codigo=?", (codigo,))}
        tend = {}
        if rod and int(rod["ano"]) == ano:
            for dim in DIMS:
                t = c.execute("SELECT * FROM tendencias WHERE rodada=? AND codigo=? AND dimensao=?", (rod["id"], codigo, dim)).fetchone()
                if t:
                    tend[dim] = {**dict(t), "meses": _m12(load(t["meses"], [])), "anos_futuros": load(t["anos_futuros"], {}), "desvios": load(t["desvios"], [])}
                else:
                    meses, fut = _tend_proposta(c, rod, codigo, dim)
                    tend[dim] = {"status": "nova", "meses": meses, "anos_futuros": fut, "desvios": []}
                tend[dim]["referencia"], tend[dim]["referencia_nome"] = _ref_meses(c, rod, codigo, dim)
        hist = []
        for r in c.execute("SELECT t.*, r.nome AS rodada_nome FROM tendencias t JOIN rodadas r ON r.id=t.rodada WHERE t.codigo=? AND r.status='fechada' ORDER BY t.rodada", (codigo,)):
            hist.append({"rodada": r["rodada"], "nome": r["rodada_nome"], "dimensao": r["dimensao"], "total": sum(_m12(load(r["meses"], []))) if r["dimensao"] != "fis" else _m12(load(r["meses"], []))[11],
                         "desvios": load(r["desvios"], []), "justificativa": r["justificativa"]})
        return {"projeto": {**dict(p), "extras": load(p["extras"], {})}, "ano": ano, "anos": anos or [ano], "curvas": curvas, "futuros": futuros,
                "rodada": dict(rod) if rod else None, "tendencia": tend, "historico_tendencia": hist,
                "mudancas": [dict(r) for r in c.execute("SELECT * FROM mudancas WHERE codigo=? ORDER BY solicitado_em DESC", (codigo,))],
                "atas": [dict(r) for r in c.execute("SELECT * FROM atas WHERE codigo=? ORDER BY data DESC", (codigo,))],
                "acoes": [dict(r) for r in c.execute("SELECT * FROM acoes WHERE codigo=? ORDER BY prazo", (codigo,))],
                "comentarios": [dict(r) for r in c.execute("SELECT * FROM comentarios WHERE codigo=? ORDER BY seq DESC", (codigo,))],
                "documentos": [dict(r) for r in c.execute("SELECT * FROM documentos WHERE codigo=? ORDER BY em DESC", (codigo,))],
                "ordens": [{"chave": r["chave"], **load(r["payload"], {})} for r in c.execute("SELECT * FROM servicos_vinculados WHERE codigo=? ORDER BY em DESC", (codigo,))]}


CAMPOS_PROJ = ("id_mestre", "plano_id", "nome", "unidade", "area", "grupo", "gerente", "etapa", "status", "tipo_investimento",
               "inicio_previsto", "conclusao_prevista", "escopo", "peso_fisico")


@app.put("/api/projetos/{codigo}")
def salvar_projeto(codigo: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario)
        codigo = codigo.strip()
        if not re.fullmatch(r"[\w.\-/]{1,60}", codigo):
            raise HTTPException(400, "Código do projeto inválido.")
        p = c.execute("SELECT * FROM projetos WHERE codigo=?", (codigo,)).fetchone()
        if p:
            _pode_editar(u, p)
        else:
            B.exigir(u, "pmo", msg="Somente o PMO cadastra projetos.")
            if not str(corpo.get("nome") or "").strip():
                raise HTTPException(400, "Informe o nome.")
            c.execute("INSERT INTO projetos(codigo,nome,origem,criado_em,atualizado_em) VALUES (?,?,?,?,?)", (codigo, corpo["nome"], "manual", agora(), agora()))
        for k in CAMPOS_PROJ:
            if k in corpo:
                c.execute(f"UPDATE projetos SET {k}=? WHERE codigo=?", (corpo[k], codigo))
        if isinstance(corpo.get("extras"), dict):
            atuais = load((c.execute("SELECT extras FROM projetos WHERE codigo=?", (codigo,)).fetchone() or {"extras": "{}"})["extras"], {})
            c.execute("UPDATE projetos SET extras=? WHERE codigo=?", (dump({**atuais, **corpo["extras"]}), codigo))
        c.execute("UPDATE projetos SET atualizado_em=? WHERE codigo=?", (agora(), codigo))
        B.auditar(c, u, "Projeto salvo", codigo, {k: corpo[k] for k in CAMPOS_PROJ if k in corpo})
        return dict(_proj(c, codigo))


@app.put("/api/projetos/{codigo}/curvas")
def salvar_curva(codigo: str, corpo: dict, x_usuario: str | None = Header(None)):
    """PMO ajusta uma curva (orçado/realizado/compromisso) manualmente."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo"); _proj(c, codigo)
        dim, tipo = corpo.get("dimensao"), corpo.get("tipo")
        if dim not in DIMS or tipo not in TIPOS:
            raise HTTPException(400, "Dimensão ou tipo inválido.")
        _gravar_curva(c, codigo, int(corpo["ano"]), dim, tipo, corpo.get("meses"), "manual")
        B.auditar(c, u, "Curva ajustada", codigo, {"ano": corpo["ano"], "dim": dim, "tipo": tipo})
        return {"ok": True}


# ───────────────────────────────────────────────── rodadas de tendência
@app.get("/api/rodadas")
def listar_rodadas():
    with B.con() as c:
        out = []
        n_proj = c.execute("SELECT COUNT(*) FROM projetos WHERE status IN ('ativo','suspenso')").fetchone()[0]
        for r in c.execute("SELECT * FROM rodadas ORDER BY id DESC"):
            d = dict(r); d["publicacao"] = load(d["publicacao"], None)
            d["situacao"] = {s["status"]: s["n"] for s in c.execute("SELECT status, COUNT(DISTINCT codigo) n FROM tendencias WHERE rodada=? GROUP BY status", (r["id"],))}
            d["projetos"] = n_proj
            out.append(d)
        return out


@app.post("/api/rodadas")
def abrir_rodada(corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo")
        if _rodada_aberta(c):
            raise HTTPException(409, "Já existe uma rodada aberta. Feche-a antes de abrir outra.")
        try:
            ano, mes = int(corpo["ano"]), int(corpo["mes_ref"])
            assert 1 <= mes <= 12
        except Exception:
            raise HTTPException(400, "Informe ano e mês de referência (1–12).")
        rid = f"{ano}-{mes:02d}"
        if c.execute("SELECT 1 FROM rodadas WHERE id=?", (rid,)).fetchone():
            raise HTTPException(409, "Já existe rodada para este mês.")
        ult = _ultima_fechada(c)
        if ult and rid < ult["id"]:
            raise HTTPException(409, "A nova rodada deve ser posterior à última fechada.")
        nome = str(corpo.get("nome") or f"{B.voc()['rodada']} {MESES[mes - 1]}/{ano}")[:60]
        c.execute("INSERT INTO rodadas(id,ano,mes_ref,nome,status,aberta_em,aberta_por) VALUES (?,?,?,?,?,?,?)", (rid, ano, mes, nome, "aberta", agora(), u["nome"]))
        B.auditar(c, u, "Rodada aberta", rid)
        return dict(_rodada(c, rid))


def _ref_meses(c, rod, codigo: str, dim: str) -> tuple[list[float], str]:
    """Referência do desvio: última tendência oficial do mesmo ano; sem ela, o orçado."""
    ant = _ultima_fechada(c, rod["id"])
    if ant and int(ant["ano"]) == int(rod["ano"]):
        t = c.execute("SELECT meses FROM tendencias WHERE rodada=? AND codigo=? AND dimensao=?", (ant["id"], codigo, dim)).fetchone()
        if t:
            return _m12(load(t["meses"], [])), ant["nome"]
    return _curva(c, codigo, int(rod["ano"]), dim, "orcado"), "Orçado"


def _desvio_ref(c, rod, codigo: str, dim: str, meses: list[float]) -> float:
    """Desvio que precisa ser explicado: total do ano (físico: dezembro) contra a referência."""
    ref, _ = _ref_meses(c, rod, codigo, dim)
    return (meses[11] - ref[11]) if dim == "fis" else (sum(meses) - sum(ref))


@app.put("/api/rodadas/{rid}/tendencias/{codigo}")
def salvar_tendencia(rid: str, codigo: str, corpo: dict, x_usuario: str | None = Header(None)):
    """Grava a tendência de uma dimensão. enviar=true submete ao PMO (exige classificar desvios acima da tolerância)."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); rod = _rodada(c, rid); p = _proj(c, codigo); _pode_editar(u, p)
        if rod["status"] != "aberta":
            raise HTTPException(409, "Rodada fechada.")
        dim = corpo.get("dimensao")
        if dim not in DIMS:
            raise HTTPException(400, "Dimensão inválida.")
        atual = c.execute("SELECT * FROM tendencias WHERE rodada=? AND codigo=? AND dimensao=?", (rid, codigo, dim)).fetchone()
        if atual and atual["status"] in ("enviada", "aprovada") and u["perfil"] != "pmo":
            raise HTTPException(409, "Tendência já enviada; aguarde a análise do PMO.")
        m = int(rod["mes_ref"])
        real = _curva(c, codigo, int(rod["ano"]), dim, "realizado")
        meses = _m12(corpo.get("meses"))
        meses = [real[i] if i < m else meses[i] for i in range(12)]  # meses fechados = realizado
        if dim == "fis" and any(not 0 <= x <= 100 for x in meses):
            raise HTTPException(400, "Físico deve ficar entre 0 e 100%.")
        fut = {str(k): float(v or 0) for k, v in (corpo.get("anos_futuros") or {}).items() if str(k).isdigit() and int(k) > int(rod["ano"])}
        desvios = [d for d in (corpo.get("desvios") or []) if isinstance(d, dict) and str(d.get("categoria") or "").strip()]
        enviar = bool(corpo.get("enviar"))
        dev = _desvio_ref(c, rod, codigo, dim, meses)
        tol = float(B.config()["tolerancia"].get(dim, 0))
        if enviar and abs(dev) > tol:
            cats = set(B.config()["categorias_desvio"][dim])
            if not desvios or any(d["categoria"] not in cats for d in desvios):
                raise HTTPException(400, {"mensagem": "Classifique o desvio nas categorias da lista antes de enviar.", "desvio": dev})
            soma = sum(float(d.get("valor") or 0) for d in desvios)
            if abs(soma - dev) > max(tol, 0.01):
                raise HTTPException(400, {"mensagem": f"A soma das categorias ({soma:,.2f}) deve explicar o desvio ({dev:,.2f}).", "desvio": dev})
            if not str(corpo.get("justificativa") or "").strip():
                raise HTTPException(400, {"mensagem": "Informe a justificativa do desvio.", "desvio": dev})
        status = "enviada" if enviar else "rascunho"
        if u["perfil"] == "pmo" and enviar and corpo.get("aprovar"):
            status = "aprovada"
        c.execute("INSERT OR REPLACE INTO tendencias(rodada,codigo,dimensao,meses,anos_futuros,desvios,justificativa,status,por,em,decidido_por,decidido_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (rid, codigo, dim, dump(meses), dump(fut), dump(desvios), str(corpo.get("justificativa") or ""), status, u["nome"], agora(),
                   u["nome"] if status == "aprovada" else None, agora() if status == "aprovada" else None))
        B.auditar(c, u, f"Tendência {status}", codigo, {"rodada": rid, "dim": dim, "desvio": dev})
        return {"status": status, "desvio": dev, "total": sum(meses) if dim != "fis" else meses[11]}


@app.post("/api/rodadas/{rid}/tendencias/{codigo}/decidir")
def decidir_tendencia(rid: str, codigo: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo"); rod = _rodada(c, rid)
        if rod["status"] != "aberta":
            raise HTTPException(409, "Rodada fechada.")
        dec = corpo.get("decisao")
        if dec not in ("aprovar", "devolver"):
            raise HTTPException(400, "Decisão deve ser aprovar ou devolver.")
        if dec == "devolver" and not str(corpo.get("parecer") or "").strip():
            raise HTTPException(400, "Informe o motivo da devolução.")
        dims = [corpo["dimensao"]] if corpo.get("dimensao") in DIMS else list(DIMS)
        n = 0
        for dim in dims:
            n += c.execute("UPDATE tendencias SET status=?, decidido_por=?, decidido_em=?, parecer=? WHERE rodada=? AND codigo=? AND dimensao=? AND status='enviada'",
                           ("aprovada" if dec == "aprovar" else "devolvida", u["nome"], agora(), str(corpo.get("parecer") or ""), rid, codigo, dim)).rowcount
        if not n:
            raise HTTPException(409, "Nenhuma tendência enviada para decidir.")
        B.auditar(c, u, f"Tendência {dec}", codigo, {"rodada": rid, "dims": dims})
        return {"ok": True, "decididas": n}


@app.get("/api/rodadas/{rid}/situacao")
def situacao_rodada(rid: str):
    with B.con() as c:
        rod = _rodada(c, rid)
        out = []
        for p in c.execute("SELECT * FROM projetos WHERE status IN ('ativo','suspenso') ORDER BY unidade, nome"):
            ts = {r["dimensao"]: dict(r) for r in c.execute("SELECT dimensao,status,por,em,parecer FROM tendencias WHERE rodada=? AND codigo=?", (rid, p["codigo"]))}
            out.append({"codigo": p["codigo"], "nome": p["nome"], "unidade": p["unidade"], "gerente": p["gerente"], "dims": ts})
        return {"rodada": dict(rod), "projetos": out}


@app.post("/api/rodadas/{rid}/fechar")
def fechar_rodada(rid: str, x_usuario: str | None = Header(None)):
    """Fecha a rodada: projetos sem tendência aprovada mantêm a proposta (última oficial/orçado) e ficam marcados.
    Publica forecast/rodada (Analisador) e portfolio/execucao (Plano)."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo"); rod = _rodada(c, rid)
        if rod["status"] != "aberta":
            raise HTTPException(409, "Rodada já fechada.")
        pend = c.execute("SELECT COUNT(*) FROM tendencias WHERE rodada=? AND status='enviada'", (rid,)).fetchone()[0]
        if pend:
            raise HTTPException(409, f"Há {pend} tendência(s) enviada(s) aguardando decisão do PMO.")
        sem = 0
        for p in c.execute("SELECT codigo FROM projetos WHERE status IN ('ativo','suspenso')").fetchall():
            for dim in DIMS:
                t = c.execute("SELECT status FROM tendencias WHERE rodada=? AND codigo=? AND dimensao=?", (rid, p[0], dim)).fetchone()
                if not t or t["status"] != "aprovada":  # rascunho/devolvida não vira oficial: mantém a referência anterior
                    meses, fut = _tend_proposta(c, rod, p[0], dim)
                    sem += 1
                    c.execute("INSERT OR REPLACE INTO tendencias(rodada,codigo,dimensao,meses,anos_futuros,desvios,justificativa,status,por,em) VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (rid, p[0], dim, dump(meses), dump(fut), "[]", "Sem atualização na rodada (mantida a referência anterior).", "sem_atualizacao", "sistema", agora()))
        c.execute("UPDATE rodadas SET status='fechada', fechada_em=?, fechada_por=? WHERE id=?", (agora(), u["nome"], rid))
        pub = _publicar_rodada(c, u, rid)
        c.execute("UPDATE rodadas SET publicacao=? WHERE id=?", (dump(pub), rid))
        B.auditar(c, u, "Rodada fechada", rid, {"sem_atualizacao": sem, **pub})
        return {"ok": True, "sem_atualizacao": sem, "publicacao": pub}


def _snapshot(c, rid: str) -> dict:
    rod = _rodada(c, rid); ano, m = int(rod["ano"]), int(rod["mes_ref"])
    projs = []
    for p in c.execute("SELECT * FROM projetos ORDER BY codigo"):
        item = {"codigo": p["codigo"], "id_mestre": p["id_mestre"], "plano_id": p["plano_id"], "nome": p["nome"], "unidade": p["unidade"],
                "area": p["area"], "grupo": p["grupo"], "gerente": p["gerente"], "etapa": p["etapa"], "status": p["status"],
                "tipo_investimento": p["tipo_investimento"], "peso_fisico": p["peso_fisico"], "extras": load(p["extras"], {})}
        for dim in DIMS:
            t = c.execute("SELECT * FROM tendencias WHERE rodada=? AND codigo=? AND dimensao=?", (rid, p["codigo"], dim)).fetchone()
            orc_fut = c.execute("SELECT valores FROM anos_futuros WHERE codigo=? AND tipo='orcado' AND dimensao=?", (p["codigo"], dim)).fetchone()
            item[dim] = {"orcado": _curva(c, p["codigo"], ano, dim, "orcado"), "realizado": _curva(c, p["codigo"], ano, dim, "realizado"),
                         "tendencia": _m12(load(t["meses"], [])) if t else None, "orcado_anos_futuros": load(orc_fut["valores"], {}) if orc_fut else {},
                         "tendencia_anos_futuros": load(t["anos_futuros"], {}) if t else {}, "desvios": load(t["desvios"], []) if t else [],
                         "justificativa": t["justificativa"] if t else "", "status": t["status"] if t else None}
        item["compromisso"] = {"requisicao": _curva(c, p["codigo"], ano, "fin", "comp_req"), "pedido": _curva(c, p["codigo"], ano, "fin", "comp_ped")}
        projs.append(item)
    return {"schema_version": "1.0", "rodada": rid, "nome": rod["nome"], "ano": ano, "mes_referencia": m, "fechada_em": rod["fechada_em"],
            "moeda": B.org().get("moeda", "BRL"), "projetos": projs}


def _publicar_rodada(c, u: dict, rid: str) -> dict:
    snap = _snapshot(c, rid)
    out = {"rodada": False, "execucao": 0, "erros": []}
    try:
        B.publicar("forecast", "rodada", rid, snap, u)
        out["rodada"] = True
    except HTTPException as e:
        out["erros"].append(str(e.detail))
    ano = snap["ano"]
    for p in snap["projetos"]:
        if p["status"] == "cancelado":
            continue
        fin = p["fin"]
        anos_prev = {}
        for r in c.execute("SELECT ano FROM curvas WHERE codigo=? AND dimensao='fin' AND ano<? GROUP BY ano", (p["codigo"], ano)):
            anos_prev[r[0]] = (sum(_curva(c, p["codigo"], r[0], "fin", "orcado")), sum(_curva(c, p["codigo"], r[0], "fin", "realizado")))
        tend_ano = {str(ano): sum(fin["tendencia"] or fin["orcado"]), **{k: v for k, v in (fin["tendencia_anos_futuros"] or fin["orcado_anos_futuros"]).items()}}
        orc_ano = {str(a): o for a, (o, _) in anos_prev.items()}; orc_ano[str(ano)] = sum(fin["orcado"]); orc_ano.update(fin["orcado_anos_futuros"])
        real_ano = {str(a): r for a, (_, r) in anos_prev.items()}; real_ano[str(ano)] = sum(fin["realizado"][: snap["mes_referencia"]])
        curva_t = {f"{ano}-{i + 1:02d}": v for i, v in enumerate(fin["tendencia"] or fin["orcado"]) if v}
        try:
            B.publicar("portfolio", "execucao", p["codigo"], {"codigo": p["codigo"], "id_mestre": p["id_mestre"], "plano_id": p["plano_id"], "nome": p["nome"],
                       "unidade": p["unidade"], "area": p["area"], "rodada": rid, "ano_rodada": ano, "mes_referencia": snap["mes_referencia"],
                       "orcado_ano": orc_ano, "realizado_ano": real_ano, "tendencia_ano": tend_ano, "curva_mensal_tendencia": curva_t}, u)
            out["execucao"] += 1
        except HTTPException as e:
            out["erros"].append(f"{p['codigo']}: {e.detail}")
    return out


@app.post("/api/rodadas/{rid}/republicar")
def republicar(rid: str, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo")
        if _rodada(c, rid)["status"] != "fechada":
            raise HTTPException(409, "Só rodadas fechadas são publicadas.")
        pub = _publicar_rodada(c, u, rid)
        c.execute("UPDATE rodadas SET publicacao=? WHERE id=?", (dump(pub), rid))
        return pub


@app.get("/api/rodadas/{rid}/snapshot")
def snapshot(rid: str):
    with B.con() as c:
        return _snapshot(c, rid)


# ───────────────────────────────────────────────── mudanças, atas, comentários, documentos
@app.get("/api/mudancas")
def listar_mudancas(status: str | None = None):
    with B.con() as c:
        sql, a = "SELECT m.*, p.nome AS projeto, p.unidade FROM mudancas m JOIN projetos p ON p.codigo=m.codigo", []
        if status:
            sql += " WHERE m.status=?"; a.append(status)
        return [dict(r) for r in c.execute(sql + " ORDER BY m.solicitado_em DESC", a)]


@app.post("/api/projetos/{codigo}/mudancas")
def criar_mudanca(codigo: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); p = _proj(c, codigo); _pode_editar(u, p)
        titulo = str(corpo.get("titulo") or "").strip()
        if not titulo:
            raise HTTPException(400, "Informe o título da mudança.")
        mid = "MUD-" + uuid.uuid4().hex[:8].upper()
        c.execute("INSERT INTO mudancas(id,codigo,titulo,tipo,descricao,impacto_custo,impacto_prazo_dias,status,solicitado_por,solicitado_em) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (mid, codigo, titulo, corpo.get("tipo"), corpo.get("descricao"), float(corpo.get("impacto_custo") or 0), int(corpo.get("impacto_prazo_dias") or 0),
                   "proposta", u["nome"], agora()))
        B.auditar(c, u, "Mudança registrada", codigo, mid)
        return dict(c.execute("SELECT * FROM mudancas WHERE id=?", (mid,)).fetchone())


@app.post("/api/mudancas/{mid}/decidir")
def decidir_mudanca(mid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo")
        m = c.execute("SELECT * FROM mudancas WHERE id=?", (mid,)).fetchone()
        if not m:
            raise HTTPException(404, "Mudança não encontrada.")
        st = corpo.get("status")
        trans = {"proposta": {"aprovada", "rejeitada", "em_analise"}, "em_analise": {"aprovada", "rejeitada"}, "aprovada": {"implementada"}}
        if st not in trans.get(m["status"], set()):
            raise HTTPException(409, f"Transição {m['status']} → {st} não permitida.")
        c.execute("UPDATE mudancas SET status=?, decidido_por=?, decidido_em=?, parecer=? WHERE id=?", (st, u["nome"], agora(), str(corpo.get("parecer") or ""), mid))
        B.auditar(c, u, f"Mudança {st}", m["codigo"], mid)
        return dict(c.execute("SELECT * FROM mudancas WHERE id=?", (mid,)).fetchone())


@app.get("/api/atas")
def listar_atas():
    with B.con() as c:
        out = []
        for r in c.execute("SELECT a.*, p.nome AS projeto FROM atas a LEFT JOIN projetos p ON p.codigo=a.codigo ORDER BY a.data DESC, a.criado_em DESC"):
            d = dict(r); d["acoes"] = [dict(x) for x in c.execute("SELECT * FROM acoes WHERE ata_id=? ORDER BY prazo", (r["id"],))]
            out.append(d)
        return out


@app.get("/api/acoes")
def listar_acoes(status: str = "aberta"):
    with B.con() as c:
        return [dict(r) for r in c.execute("SELECT x.*, a.titulo AS ata, p.nome AS projeto FROM acoes x LEFT JOIN atas a ON a.id=x.ata_id LEFT JOIN projetos p ON p.codigo=x.codigo WHERE x.status=? ORDER BY x.prazo", (status,))]


@app.post("/api/atas")
def salvar_ata(corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario)
        if u["perfil"] not in ("pmo", "gerente"):
            raise HTTPException(403, "Seu perfil é somente leitura.")
        aid = str(corpo.get("id") or "")
        codigo = corpo.get("codigo") or None
        if codigo:
            _pode_editar(u, _proj(c, codigo))
        if aid:
            a = c.execute("SELECT * FROM atas WHERE id=?", (aid,)).fetchone()
            if not a:
                raise HTTPException(404, "Ata não encontrada.")
            if a["status"] == "aprovada":
                raise HTTPException(409, "Ata aprovada não pode ser alterada.")
        else:
            aid = "ATA-" + uuid.uuid4().hex[:8].upper()
            c.execute("INSERT INTO atas(id,titulo,criado_por,criado_em) VALUES (?,?,?,?)", (aid, "(sem título)", u["nome"], agora()))
        titulo = str(corpo.get("titulo") or "").strip()
        if not titulo:
            raise HTTPException(400, "Informe o título da ata.")
        c.execute("UPDATE atas SET codigo=?, data=?, tipo=?, titulo=?, participantes=?, pauta=?, deliberacoes=? WHERE id=?",
                  (codigo, corpo.get("data"), corpo.get("tipo"), titulo, corpo.get("participantes"), corpo.get("pauta"), corpo.get("deliberacoes"), aid))
        if isinstance(corpo.get("acoes"), list):
            c.execute("DELETE FROM acoes WHERE ata_id=? AND status='aberta'", (aid,))
            for x in corpo["acoes"]:
                if str(x.get("descricao") or "").strip() and x.get("status", "aberta") == "aberta":
                    c.execute("INSERT INTO acoes(id,ata_id,codigo,descricao,responsavel,prazo,status) VALUES (?,?,?,?,?,?,?)",
                              (x.get("id") or "AC-" + uuid.uuid4().hex[:8].upper(), aid, codigo, x["descricao"], x.get("responsavel"), x.get("prazo"), "aberta"))
        B.auditar(c, u, "Ata salva", codigo or "", aid)
        return {"id": aid}


@app.post("/api/atas/{aid}/aprovar")
def aprovar_ata(aid: str, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo")
        if not c.execute("UPDATE atas SET status='aprovada', aprovado_por=?, aprovado_em=? WHERE id=? AND status='rascunho'", (u["nome"], agora(), aid)).rowcount:
            raise HTTPException(409, "Ata inexistente ou já aprovada.")
        return {"ok": True}


@app.post("/api/acoes/{xid}")
def atualizar_acao(xid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario)
        if u["perfil"] not in ("pmo", "gerente"):
            raise HTTPException(403, "Seu perfil é somente leitura.")
        st = corpo.get("status")
        if st not in ("aberta", "concluida", "cancelada"):
            raise HTTPException(400, "Status inválido.")
        if not c.execute("UPDATE acoes SET status=?, concluida_em=? WHERE id=?", (st, agora() if st == "concluida" else None, xid)).rowcount:
            raise HTTPException(404, "Ação não encontrada.")
        return {"ok": True}


@app.post("/api/projetos/{codigo}/comentarios")
def comentar(codigo: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); _proj(c, codigo)
        if u["perfil"] == "leitor":
            raise HTTPException(403, "Seu perfil é somente leitura.")
        t = str(corpo.get("texto") or "").strip()
        if not t:
            raise HTTPException(400, "Comentário vazio.")
        c.execute("INSERT INTO comentarios(codigo,texto,por,em) VALUES (?,?,?,?)", (codigo, t[:4000], u["nome"], agora()))
        return {"ok": True}


@app.post("/api/projetos/{codigo}/documentos")
async def enviar_documento(codigo: str, arquivo: UploadFile = File(...), x_usuario: str | None = Header(None)):
    dados = await arquivo.read()
    if len(dados) > 25 * 1024 * 1024:
        raise HTTPException(413, "Arquivo maior que 25 MB.")
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); _pode_editar(u, _proj(c, codigo))
        did = uuid.uuid4().hex
        ext = Path(arquivo.filename or "").suffix.lower()[:8]
        (B.arquivos / f"{did}{ext}").write_bytes(dados)
        c.execute("INSERT INTO documentos(id,codigo,tipo,nome,arquivo,por,em) VALUES (?,?,?,?,?,?,?)", (did, codigo, "Anexo", arquivo.filename, f"{did}{ext}", u["nome"], agora()))
        return {"id": did}


@app.get("/api/documentos/{did}")
def baixar_documento(did: str):
    with B.con() as c:
        d = c.execute("SELECT * FROM documentos WHERE id=?", (did,)).fetchone()
    if not d or not re.fullmatch(r"[a-f0-9]{32}(\.[a-z0-9]{1,7})?", d["arquivo"] or "") or not (B.arquivos / d["arquivo"]).is_file():
        raise HTTPException(404, "Documento não encontrado.")
    return FileResponse(B.arquivos / d["arquivo"], filename=d["nome"])


# ───────────────────────────────────────────────── importação por modelo genérico
def _xlsx(wb) -> bytes:
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def _cab(ws, cols):
    ws.append(cols)
    for cel in ws[1]:
        cel.font = Font(bold=True, color="FFFFFF"); cel.fill = PatternFill("solid", fgColor="1E3A6E")
    for i, col in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = max(10, min(40, len(str(col)) + 4))
    ws.freeze_panes = "B2"


MODELOS = {
    "projetos": ["Código", "Nome", "Unidade", "Área", "Grupo", "Gerente", "Etapa", "Status", "Tipo de investimento", "Início previsto", "Conclusão prevista", "Peso físico"],
    "curvas": ["Código", "Ano", "Dimensão", "Tipo"] + MESES,
    "anos_futuros": ["Código", "Dimensão", "Tipo", "Ano", "Valor"],
}


@app.get("/api/modelos/{tipo}.xlsx")
def modelo(tipo: str):
    if tipo not in MODELOS:
        raise HTTPException(404, "Modelo inexistente.")
    wb = Workbook(); ws = wb.active; ws.title = tipo; _cab(ws, MODELOS[tipo])
    org = B.org(); uni = ((org.get("unidades") or [{}])[0]).get("nome", ""); ano = int(org.get("ano_base") or 2026)
    if tipo == "projetos":
        ws.append(["PRJ-001", "Exemplo (apague esta linha)", uni, (org.get("areas") or [""])[0], "", "", "Execução", "ativo", "", f"{ano}-01-01", f"{ano + 1}-12-31", 1])
    elif tipo == "curvas":
        ws.append(["PRJ-001", ano, "Financeiro", "Orçado"] + [0] * 12)
        ws2 = wb.create_sheet("Valores aceitos")
        ws2.append(["Dimensão", "Econômico | Financeiro | Físico (físico em % acumulado, 0–100)"])
        ws2.append(["Tipo", "Orçado | Realizado | Compromisso — requisição | Compromisso — pedido"])
    else:
        ws.append(["PRJ-001", "Financeiro", "Orçado", ano + 1, 0])
    return Response(_xlsx(wb), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="modelo-{tipo}.xlsx"'})


def _norm(s) -> str:
    import unicodedata
    s = "".join(ch for ch in unicodedata.normalize("NFD", str(s or "")) if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


_DIM_TXT = {"economico": "econ", "econ": "econ", "financeiro": "fin", "fin": "fin", "fisico": "fis", "fis": "fis"}
_TIPO_TXT = {"orcado": "orcado", "orcamento": "orcado", "baseline": "orcado", "realizado": "realizado", "real": "realizado",
             "compromisso requisicao": "comp_req", "requisicao": "comp_req", "compromisso pedido": "comp_ped", "pedido": "comp_ped"}


@app.post("/api/importar/{tipo}")
async def importar(tipo: str, arquivo: UploadFile = File(...), x_usuario: str | None = Header(None)):
    """Importa um dos modelos genéricos. Sistemas de origem (ERP, cronogramas) entram convertendo para estes modelos."""
    if tipo not in MODELOS:
        raise HTTPException(404, "Tipo de importação inexistente.")
    try:
        wb = load_workbook(io.BytesIO(await arquivo.read()), data_only=True, read_only=True)
    except Exception:
        raise HTTPException(400, "Arquivo .xlsx inválido.")
    linhas = list(wb.worksheets[0].iter_rows(values_only=True))
    if not linhas:
        raise HTTPException(400, "Planilha vazia.")
    idx = {_norm(x): i for i, x in enumerate(linhas[0])}
    faltando = [c for c in MODELOS[tipo][:4 if tipo == "curvas" else 2] if _norm(c) not in idx]
    if faltando:
        raise HTTPException(400, f"Colunas obrigatórias ausentes: {', '.join(faltando)}.")
    g = lambda ln, nome: ln[idx[_norm(nome)]] if _norm(nome) in idx and idx[_norm(nome)] < len(ln) else None
    erros, ok = [], 0
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo")
        for n, ln in enumerate(linhas[1:], 2):
            cod = str(g(ln, "Código") or "").strip()
            if not cod or str(g(ln, "Nome") or "").startswith("Exemplo"):
                continue
            try:
                if tipo == "projetos":
                    nome = str(g(ln, "Nome") or "").strip()
                    if not nome:
                        raise ValueError("nome vazio")
                    if not c.execute("SELECT 1 FROM projetos WHERE codigo=?", (cod,)).fetchone():
                        c.execute("INSERT INTO projetos(codigo,nome,origem,criado_em) VALUES (?,?,?,?)", (cod, nome, "importacao", agora()))
                    vals = {"nome": nome, "unidade": g(ln, "Unidade"), "area": g(ln, "Área"), "grupo": g(ln, "Grupo"), "gerente": g(ln, "Gerente"),
                            "etapa": g(ln, "Etapa"), "status": str(g(ln, "Status") or "ativo").lower(), "tipo_investimento": g(ln, "Tipo de investimento"),
                            "inicio_previsto": str(g(ln, "Início previsto") or "")[:10] or None, "conclusao_prevista": str(g(ln, "Conclusão prevista") or "")[:10] or None,
                            "peso_fisico": float(g(ln, "Peso físico") or 0) or None}
                    for k, v in vals.items():
                        c.execute(f"UPDATE projetos SET {k}=?, atualizado_em=? WHERE codigo=?", (v, agora(), cod))
                elif tipo == "curvas":
                    if not c.execute("SELECT 1 FROM projetos WHERE codigo=?", (cod,)).fetchone():
                        raise ValueError(f"projeto {cod} não cadastrado")
                    dim, tp = _DIM_TXT.get(_norm(g(ln, "Dimensão"))), _TIPO_TXT.get(_norm(g(ln, "Tipo")))
                    if not dim or not tp:
                        raise ValueError("dimensão ou tipo inválido")
                    meses = [float(g(ln, mm) or 0) for mm in MESES]
                    _gravar_curva(c, cod, int(g(ln, "Ano")), dim, tp, meses, "importacao")
                else:
                    if not c.execute("SELECT 1 FROM projetos WHERE codigo=?", (cod,)).fetchone():
                        raise ValueError(f"projeto {cod} não cadastrado")
                    dim, tp = _DIM_TXT.get(_norm(g(ln, "Dimensão"))), _TIPO_TXT.get(_norm(g(ln, "Tipo")))
                    if not dim or tp != "orcado":
                        raise ValueError("só orçado de anos futuros é aceito")
                    r = c.execute("SELECT valores FROM anos_futuros WHERE codigo=? AND tipo=? AND dimensao=?", (cod, tp, dim)).fetchone()
                    v = load(r["valores"], {}) if r else {}
                    v[str(int(g(ln, "Ano")))] = float(g(ln, "Valor") or 0)
                    c.execute("INSERT OR REPLACE INTO anos_futuros(codigo,tipo,dimensao,valores) VALUES (?,?,?,?)", (cod, tp, dim, dump(v)))
                ok += 1
            except (ValueError, TypeError) as e:
                erros.append(f"linha {n}: {e}")
        c.execute("INSERT INTO cargas(tipo,arquivo,linhas,resultado,por,em) VALUES (?,?,?,?,?,?)", (tipo, arquivo.filename, ok, dump(erros[:200]), u["nome"], agora()))
        B.auditar(c, u, "Importação", tipo, {"arquivo": arquivo.filename, "linhas": ok, "erros": len(erros)})
    return {"importadas": ok, "erros": erros[:200]}


@app.get("/api/cargas")
def listar_cargas():
    with B.con() as c:
        return [{**dict(r), "resultado": load(r["resultado"], [])} for r in c.execute("SELECT * FROM cargas ORDER BY seq DESC LIMIT 200")]


@app.get("/api/exportar/tendencia.xlsx")
def exportar_tendencia(rodada: str | None = None):
    with B.con() as c:
        rod = _rodada(c, rodada) if rodada else (_rodada_aberta(c) or _ultima_fechada(c))
        if not rod:
            raise HTTPException(404, "Nenhuma rodada.")
        snap = _snapshot(c, rod["id"])
    wb = Workbook(); wb.remove(wb.active)
    for dim, nome in DIMS.items():
        ws = wb.create_sheet(nome)
        _cab(ws, ["Código", "Projeto", "Unidade", "Área", "Série"] + MESES + ["Total / Dez"])
        for p in snap["projetos"]:
            for serie in ("orcado", "realizado", "tendencia"):
                v = p[dim][serie] or [0] * 12
                ws.append([p["codigo"], p["nome"], p["unidade"], p["area"], serie.capitalize()] + v + [v[11] if dim == "fis" else sum(v)])
    ws = wb.create_sheet("Desvios")
    _cab(ws, ["Código", "Projeto", "Dimensão", "Categoria", "Valor", "Justificativa"])
    for p in snap["projetos"]:
        for dim in DIMS:
            for d in p[dim]["desvios"]:
                ws.append([p["codigo"], p["nome"], DIMS[dim], d.get("categoria"), d.get("valor"), p[dim]["justificativa"]])
    return Response(_xlsx(wb), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="tendencia-{rod["id"]}.xlsx"'})


# ───────────────────────────────────────────────── integrações (Data Core)
def _mensal_por_ano(curva: dict) -> dict[int, list[float]]:
    out: dict[int, list[float]] = {}
    for k, v in (curva or {}).items():
        mm = re.fullmatch(r"(\d{4})-(\d{2})", str(k))
        if mm:
            out.setdefault(int(mm.group(1)), [0.0] * 12)[int(mm.group(2)) - 1] += float(v or 0)
    return out


def _receber_tap(c, u, r) -> str:
    p = r.get("payload") or {}
    cod = (p.get("codigo_erp") or "").strip() or r["entity_key"]
    existe = c.execute("SELECT * FROM projetos WHERE codigo=? OR id_mestre=?", (cod, r["entity_key"])).fetchone()
    if existe:
        cod = existe["codigo"]
    else:
        c.execute("INSERT INTO projetos(codigo,nome,origem,status,criado_em) VALUES (?,?,?,?,?)", (cod, p.get("nome") or cod, "tap", "ativo", agora()))
    c.execute("UPDATE projetos SET id_mestre=?, plano_id=?, nome=?, unidade=?, area=?, gerente=COALESCE(gerente,?), etapa=COALESCE(etapa,?), inicio_previsto=?, conclusao_prevista=?, escopo=?, atualizado_em=? WHERE codigo=?",
              (r["entity_key"], p.get("plano_id"), p.get("nome") or cod, p.get("unidade"), p.get("area"), p.get("responsavel"), "Planejamento",
               p.get("inicio_previsto"), p.get("conclusao_compromissada"), p.get("escopo_aprovado"), agora(), cod))
    for ano, meses in _mensal_por_ano(p.get("curva_mensal_aprovada")).items():
        for dim in ("econ", "fin"):
            _gravar_curva(c, cod, ano, dim, "orcado", meses, "tap")
    pdf = p.get("tap_pdf") or {}
    if pdf.get("base64"):
        did = uuid.uuid4().hex
        (B.arquivos / f"{did}.pdf").write_bytes(base64.b64decode(pdf["base64"]))
        c.execute("DELETE FROM documentos WHERE codigo=? AND tipo='TAP'", (cod,))
        c.execute("INSERT INTO documentos(id,codigo,tipo,nome,arquivo,por,em) VALUES (?,?,?,?,?,?,?)", (did, cod, "TAP", pdf.get("nome") or "TAP.pdf", f"{did}.pdf", "integração", agora()))
    c.execute("INSERT INTO comentarios(codigo,texto,por,em) VALUES (?,?,?,?)", (cod, f"Projeto recebido do {B.voc()['termo_abertura']} aprovado em {str(p.get('tap_aprovado_em') or '')[:10]} por {p.get('tap_aprovado_por') or '—'}.", "integração", agora()))
    return cod


def _receber_plano(c, u, r) -> str:
    p = r.get("payload") or {}
    cod = r["entity_key"]
    if not c.execute("SELECT 1 FROM projetos WHERE codigo=?", (cod,)).fetchone():
        if p.get("status") == "cortado":
            return "ignorado (cortado e inexistente na carteira)"
        c.execute("INSERT INTO projetos(codigo,nome,unidade,area,origem,status,criado_em) VALUES (?,?,?,?,?,?,?)", (cod, p.get("nome") or cod, p.get("unidade"), p.get("area"), "plano", "ativo", agora()))
    c.execute("UPDATE projetos SET plano_id=?, atualizado_em=? WHERE codigo=?", (p.get("plano_id"), agora(), cod))
    por_ano = _mensal_por_ano(p.get("curva_mensal"))
    ini, fim = int(p.get("ciclo_inicio") or 0), int(p.get("ciclo_fim") or 0)
    for ano in range(ini, fim + 1):
        meses = por_ano.get(ano, [0.0] * 12)
        for dim in ("econ", "fin"):
            atual = c.execute("SELECT 1 FROM curvas WHERE codigo=? AND ano=? AND dimensao=? AND tipo='orcado'", (cod, ano, dim)).fetchone()
            if ano == ini or atual or any(meses):  # orçado mensal de cada ano do ciclo aprovado
                _gravar_curva(c, cod, ano, dim, "orcado", meses, "plano")
    for dim in ("econ", "fin"):
        fut = {str(a): round(sum(m), 2) for a, m in por_ano.items() if a > ini}
        c.execute("INSERT OR REPLACE INTO anos_futuros(codigo,tipo,dimensao,valores) VALUES (?,?,?,?)", (cod, "orcado", dim, dump(fut)))
    if p.get("status") == "cortado":
        c.execute("UPDATE projetos SET status='cancelado' WHERE codigo=?", (cod,))
    c.execute("INSERT INTO comentarios(codigo,texto,por,em) VALUES (?,?,?,?)", (cod, f"Orçamento do {B.voc()['plano_sigla']} {p.get('ciclo_nome') or ''} recebido: {p.get('status')} · total {float(p.get('orcamento_total') or 0):,.0f}.", "integração", agora()))
    return cod


@app.get("/api/integracoes/pendentes")
def integracoes_pendentes(x_usuario: str | None = Header(None)):
    with B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo")
        tap = B.pendentes(c, "pmp", "tap_aprovado")
        pla = B.pendentes(c, "pmp", "plano_aprovado")
        return {"modo": "hub" if hubkit.no_hub() else "isolado",
                "tap": [{"chave": r["entity_key"], "nome": (r.get("payload") or {}).get("nome"), "em": r.get("updated_at")} for r in tap],
                "plano": [{"chave": r["entity_key"], "nome": (r.get("payload") or {}).get("nome"), "status": (r.get("payload") or {}).get("status"), "em": r.get("updated_at")} for r in pla]}


@app.post("/api/integracoes/sincronizar")
def sincronizar(x_usuario: str | None = Header(None)):
    """Recebe TAPs aprovados (Originação), orçamentos aprovados (Plano), rascunhos de ata (agenda) e ordens de serviço vinculadas. Seguro para repetir."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario)
        if u["perfil"] not in ("pmo", "gerente"):
            raise HTTPException(403, "Seu perfil não sincroniza integrações.")
        return _sincronizar(c, u)


def _receber_ata(c, r) -> str:
    p = r.get("payload") or {}
    codigo = p.get("codigo_projeto") or None
    if codigo and not c.execute("SELECT 1 FROM projetos WHERE codigo=?", (codigo,)).fetchone():
        codigo = None
    aid = "ATA-" + hashlib.sha1(r["entity_key"].encode()).hexdigest()[:8].upper()
    a = c.execute("SELECT status FROM atas WHERE id=?", (aid,)).fetchone()
    if a and a["status"] == "aprovada":
        return aid + " (já aprovada)"
    pauta = p.get("pauta") or p.get("descricao") or ""
    if p.get("cancelada"):
        if a:
            c.execute("UPDATE atas SET titulo=? WHERE id=? AND titulo NOT LIKE '[Cancelada]%'", ("[Cancelada] " + (p.get("titulo") or "Reunião"), aid))
        return aid + " (cancelada)"
    if a:
        c.execute("UPDATE atas SET codigo=?, data=?, titulo=?, participantes=?, pauta=? WHERE id=?",
                  (codigo, (p.get("data") or "")[:10], p.get("titulo") or "Reunião", ", ".join(p.get("participantes") or []), pauta, aid))
    else:
        c.execute("INSERT INTO atas(id,codigo,data,tipo,titulo,participantes,pauta,status,criado_por,criado_em) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (aid, codigo, (p.get("data") or "")[:10], p.get("tipo") or (B.config().get("tipos_ata") or ["Reunião de status"])[0], p.get("titulo") or "Reunião",
                   ", ".join(p.get("participantes") or []), pauta, "rascunho", p.get("criado_por") or "agenda", agora()))
    if codigo:
        c.execute("INSERT INTO comentarios(codigo,texto,por,em) VALUES (?,?,?,?)", (codigo, f"Reunião “{p.get('titulo') or ''}” agendada para {(p.get('data') or '')[:16].replace('T', ' ')}; rascunho de ata criado.", "agenda", agora()))
    return aid


def _receber_os(c, r) -> str:
    p = r.get("payload") or {}
    codigo = p.get("codigo_projeto") or None
    if codigo and not c.execute("SELECT 1 FROM projetos WHERE codigo=?", (codigo,)).fetchone():
        codigo = None
    c.execute("INSERT OR REPLACE INTO servicos_vinculados(chave,codigo,payload,em) VALUES (?,?,?,?)", (r["entity_key"], codigo, dump(p), agora()))
    return codigo or "sem projeto"


def _sincronizar(c, u) -> dict:
    res = {"tap": [], "plano": [], "atas": [], "ordens": []}
    for r in B.pendentes(c, "pmp", "tap_aprovado"):
        cod = _receber_tap(c, u, r); B.marcar_recebido(c, r, cod); res["tap"].append(cod)
    for r in B.pendentes(c, "pmp", "plano_aprovado"):
        cod = _receber_plano(c, u, r); B.marcar_recebido(c, r, cod); res["plano"].append(cod)
    for r in B.pendentes(c, "pmp", "ata_rascunho"):
        aid = _receber_ata(c, r); B.marcar_recebido(c, r, aid); res["atas"].append(aid)
    for r in B.pendentes(c, "servicos", "os_resumo"):
        cod = _receber_os(c, r); B.marcar_recebido(c, r, cod); res["ordens"].append(r["entity_key"])
    if res["tap"] or res["plano"] or res["atas"]:
        B.auditar(c, u, "Sincronização", "", res)
    return res


def _sync_auto(c, u) -> dict:
    r = _sincronizar(c, u)
    return {"projetos_por_" + B.voc()["termo_abertura"].lower(): len(r["tap"]), "orcamentos_do_plano": len(r["plano"]),
            "rascunhos_de_ata": len(r["atas"]), "ordens_de_servico": len(r["ordens"])}


def _pendencias(c) -> list[dict]:
    """O que a PMP coloca na agenda do ecossistema."""
    out, voc = [], B.voc()
    rod = _rodada_aberta(c)
    if rod:
        ger: dict[str, list] = {}
        for p in c.execute("SELECT * FROM projetos WHERE status IN ('ativo','suspenso')"):
            ts = {r["dimensao"]: r["status"] for r in c.execute("SELECT dimensao,status FROM tendencias WHERE rodada=? AND codigo=?", (rod["id"], p["codigo"]))}
            if any(ts.get(d) not in ("enviada", "aprovada") for d in DIMS):
                ger.setdefault(p["gerente"] or "", []).append(p)
        for g, ps in ger.items():
            out.append({"chave": f"rodada:{rod['id']}:{g or '-'}", "natureza": "tarefa", "esforco_h": round(0.5 * len(ps), 1), "tipo": "rodada", "titulo": f"{rod['nome']}: {len(ps)} projeto(s) sem {voc['forecast'].lower()} enviada",
                        "descricao": ", ".join(p["codigo"] for p in ps[:12]) + ("…" if len(ps) > 12 else ""), "responsavel": g or None,
                        "unidades": sorted({p["unidade"] for p in ps if p["unidade"]}), "perfis": ["gerente"], "link": "#/rodadas", "prioridade": "alta"})
        n = c.execute("SELECT COUNT(DISTINCT codigo) FROM tendencias WHERE rodada=? AND status='enviada'", (rod["id"],)).fetchone()[0]
        if n:
            out.append({"chave": f"aprovar:{rod['id']}", "natureza": "acao", "tipo": "aprovacao", "titulo": f"{rod['nome']}: {n} projeto(s) aguardando aprovação do {voc['pmo']}",
                        "perfis": ["pmo"], "link": "#/rodadas", "prioridade": "alta"})
    for m in c.execute("SELECT m.*, p.nome AS projeto, p.unidade FROM mudancas m JOIN projetos p ON p.codigo=m.codigo WHERE m.status IN ('proposta','em_analise')"):
        out.append({"chave": f"mudanca:{m['id']}", "natureza": "acao", "tipo": "aprovacao", "titulo": f"Mudança “{m['titulo']}” — {m['projeto']}", "projeto": m["codigo"],
                    "unidades": [m["unidade"]] if m["unidade"] else [], "perfis": ["pmo"], "link": f"#/projeto/{m['codigo']}/mudancas", "prioridade": "normal",
                    "acoes": [B.acao("aprovar", "Aprovar", f"/api/mudancas/{m['id']}/decidir", corpo={"status": "aprovada"}, comentario="opcional", estilo="ok", campo="parecer"),
                              B.acao("rejeitar", "Rejeitar", f"/api/mudancas/{m['id']}/decidir", corpo={"status": "rejeitada"}, comentario="obrigatorio", estilo="bad", campo="parecer")]})
    for x in c.execute("SELECT x.*, p.nome AS projeto, p.unidade FROM acoes x LEFT JOIN projetos p ON p.codigo=x.codigo WHERE x.status='aberta'"):
        out.append({"chave": f"acao:{x['id']}", "natureza": "tarefa", "esforco_h": 1, "tipo": "acao", "titulo": x["descricao"], "descricao": f"Ação de ata{' — ' + x['projeto'] if x['projeto'] else ''}",
                    "prazo": x["prazo"], "responsavel": x["responsavel"], "projeto": x["codigo"], "unidades": [x["unidade"]] if x["unidade"] else [],
                    "perfis": ["pmo", "gerente"], "link": "#/atas", "prioridade": "normal",
                    "acoes": [B.acao("concluir", "Marcar como concluída", f"/api/acoes/{x['id']}", corpo={"status": "concluida"}, estilo="ok")]})
    for a in c.execute("SELECT * FROM atas WHERE status='rascunho' AND titulo NOT LIKE '[Cancelada]%'"):
        out.append({"chave": f"ata:{a['id']}", "natureza": "tarefa", "esforco_h": 0.5, "tipo": "ata", "titulo": f"Ata em rascunho: {a['titulo']}", "prazo": a["data"], "projeto": a["codigo"],
                    "perfis": ["pmo", "gerente"], "link": f"#/projeto/{a['codigo']}/atas" if a["codigo"] else "#/atas", "prioridade": "baixa"})
    return out


B.automatico(_sync_auto, _pendencias)


@app.post("/api/demo")
def demo(x_usuario: str | None = Header(None)):
    """Carteira de exemplo genérica com orçado e realizado (para conhecer a ferramenta)."""
    import random
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo")
        if c.execute("SELECT 1 FROM projetos").fetchone():
            raise HTTPException(409, "A carteira já tem projetos.")
        org = B.org(); unis = [x["nome"] for x in org.get("unidades") or []] or ["Unidade A"]; areas = org.get("areas") or ["Engenharia"]
        ano = int(org.get("ano_base") or 2026); rnd = random.Random(3)
        nomes = ["Renovação de subestação", "Modernização do sistema de controle", "Reforma de oficina", "Ampliação de pátio", "Troca de cabos de média tensão",
                 "Sistema de gestão da manutenção", "Climatização de salas técnicas", "Acessibilidade em pontos de atendimento", "Videomonitoramento analítico", "Recuperação de taludes"]
        etapas = B.config()["etapas"]
        for i, n in enumerate(nomes):
            cod = f"PRJ-{i + 1:03d}"
            c.execute("INSERT INTO projetos(codigo,nome,unidade,area,gerente,etapa,status,origem,peso_fisico,inicio_previsto,conclusao_prevista,criado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (cod, n, unis[i % len(unis)], areas[i % len(areas)], f"Gerente Demo {1 + i % 2}", etapas[min(2, i % len(etapas))], "ativo", "importacao", 1.0,
                       f"{ano - 1}-0{1 + i % 9}-01", f"{ano + 1 + i % 2}-12-31", agora()))
            tot = rnd.choice([2, 4, 6, 9, 14]) * 1e6
            pesos = [rnd.uniform(.5, 1.5) for _ in range(12)]; s = sum(pesos)
            orc = [round(tot * p / s, -2) for p in pesos]
            real = [round(v * rnd.uniform(.55, 1.15), -2) for v in orc]
            for dim in ("econ", "fin"):
                _gravar_curva(c, cod, ano, dim, "orcado", orc, "demo")
                _gravar_curva(c, cod, ano, dim, "realizado", [real[k] if k < 8 else 0 for k in range(12)], "demo")
                _gravar_curva(c, cod, ano - 1, dim, "orcado", [v * .6 for v in orc], "demo")
                _gravar_curva(c, cod, ano - 1, dim, "realizado", [v * .6 * rnd.uniform(.5, 1.05) for v in orc], "demo")
                c.execute("INSERT OR REPLACE INTO anos_futuros(codigo,tipo,dimensao,valores) VALUES (?,?,?,?)", (cod, "orcado", dim, dump({str(ano + 1): round(tot * .5, -3)})))
            plan = [round(min(100, 100 * (k + 1) / 12 * rnd.uniform(.9, 1)), 1) for k in range(12)]
            _gravar_curva(c, cod, ano, "fis", "orcado", plan, "demo")
            _gravar_curva(c, cod, ano, "fis", "realizado", [round(plan[k] * rnd.uniform(.75, 1), 1) if k < 8 else 0 for k in range(12)], "demo")
            _gravar_curva(c, cod, ano, "fin", "comp_ped", [round(v * .8, -2) for v in orc], "demo")
        c.execute("INSERT INTO rodadas(id,ano,mes_ref,nome,status,aberta_em,aberta_por) VALUES (?,?,?,?,?,?,?)", (f"{ano}-08", ano, 8, f"{B.voc()['rodada']} Ago/{ano}", "aberta", agora(), u["nome"]))
        B.auditar(c, u, "Carteira de exemplo criada")
        return {"projetos": len(nomes), "rodada": f"{ano}-08"}


B.servir_spa(app)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=os.environ.get("PMP_HOST", "127.0.0.1"), port=int(os.environ.get("PORT") or 8830), root_path=os.environ.get("HUB_ROOT_PATH", ""))
