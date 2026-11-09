"""Plano de Investimentos — ciclo de planejamento plurianual do portfólio (genérico).

Base imutável → pleitos por rodada (com justificativa) → cenários de comitê (congelados) → aprovação → publicação.
Fontes: projetos novos da Originação, projetos em execução da PMP (tendência) e importação por planilha.
Integração exclusivamente pelo Data Core do hub (ver docs/CONTRATO_INTEGRACAO.md).
"""
from __future__ import annotations
import io, math, os, random, re, sqlite3, uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import Response
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

import hubkit
from appkit import Base, agora, dump, load

BASE_DIR = Path(__file__).resolve().parent
VERSAO = "1.2.2"
FASES = ["preparacao", "pleitos", "comite", "aprovado", "encerrado"]
SCHEMA = """
CREATE TABLE IF NOT EXISTS ciclos(id TEXT PRIMARY KEY, nome TEXT NOT NULL, ano_inicio INTEGER NOT NULL, ano_fim INTEGER NOT NULL,
  fase TEXT NOT NULL DEFAULT 'preparacao', taxa_desconto REAL, rodada INTEGER NOT NULL DEFAULT 0, cenario_aprovado TEXT,
  criado_em TEXT, atualizado_em TEXT);
CREATE TABLE IF NOT EXISTS projetos(ciclo_id TEXT NOT NULL REFERENCES ciclos(id) ON DELETE CASCADE, plano_id TEXT NOT NULL,
  codigo TEXT, id_mestre TEXT, nome TEXT NOT NULL, unidade TEXT, area TEXT, origem TEXT NOT NULL, categoria TEXT,
  prioridade INTEGER, status TEXT NOT NULL DEFAULT 'proposto', curva_ref TEXT NOT NULL DEFAULT '{}', dados TEXT NOT NULL DEFAULT '{}',
  criado_em TEXT, atualizado_em TEXT, PRIMARY KEY(ciclo_id, plano_id));
CREATE INDEX IF NOT EXISTS ix_proj_mestre ON projetos(ciclo_id, id_mestre);
CREATE INDEX IF NOT EXISTS ix_proj_codigo ON projetos(ciclo_id, codigo);
CREATE TABLE IF NOT EXISTS valores(ciclo_id TEXT NOT NULL, plano_id TEXT NOT NULL, camada TEXT NOT NULL, ano INTEGER NOT NULL,
  valor REAL NOT NULL, PRIMARY KEY(ciclo_id, plano_id, camada, ano));
CREATE TABLE IF NOT EXISTS alteracoes(seq INTEGER PRIMARY KEY AUTOINCREMENT, ciclo_id TEXT, plano_id TEXT, camada TEXT, ano INTEGER,
  de REAL, para REAL, justificativa TEXT, por TEXT, perfil TEXT, em TEXT, rodada INTEGER, status TEXT NOT NULL DEFAULT 'registrada',
  revisado_por TEXT, revisado_em TEXT);
CREATE TABLE IF NOT EXISTS cenarios(id TEXT PRIMARY KEY, ciclo_id TEXT NOT NULL REFERENCES ciclos(id) ON DELETE CASCADE, nome TEXT NOT NULL,
  descricao TEXT, origem TEXT, criado_por TEXT, criado_em TEXT, congelado INTEGER NOT NULL DEFAULT 0, congelado_em TEXT,
  aprovado INTEGER NOT NULL DEFAULT 0, aprovado_por TEXT, aprovado_em TEXT, decisoes TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS metas(ciclo_id TEXT NOT NULL, escopo TEXT NOT NULL, chave TEXT NOT NULL, percentual REAL, observacao TEXT,
  por TEXT, em TEXT, PRIMARY KEY(ciclo_id, escopo, chave));
CREATE TABLE IF NOT EXISTS indices(ano INTEGER PRIMARY KEY, inflacao REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sequencia(nome TEXT PRIMARY KEY, ultimo INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS publicacoes(seq INTEGER PRIMARY KEY AUTOINCREMENT, ciclo_id TEXT, destino TEXT, chave TEXT, status TEXT,
  em TEXT, por TEXT, detalhe TEXT);
"""
PERFIS = {"pmo": "PMO (administrador)", "comite": "Comitê / Diretoria", "gestor": "Gestor de unidade", "leitor": "Leitura"}
DEMO = [("u-demo-pmo", "PMO Demo", "pmo", None), ("u-demo-comite", "Comitê Demo", "comite", None),
        ("u-demo-gestor1", "Gestor Demo 1", "gestor", 0), ("u-demo-gestor2", "Gestor Demo 2", "gestor", 1),
        ("u-demo-leitor", "Leitor Demo", "leitor", None)]
CONFIG = {"categorias": ["Expansão", "Renovação de ativos", "Obrigatório / regulatório", "Segurança", "Tecnologia", "Eficiência"],
          "rotulos": {}, "simulacoes_monte_carlo": 5000}

B = Base("plano", "Plano de Investimentos", VERSAO, BASE_DIR, SCHEMA, PERFIS, admin="pmo", padrao="leitor", demo=DEMO, config_padrao=CONFIG)


@asynccontextmanager
async def _vida(_app):
    B.iniciar()
    yield

app = FastAPI(title="Plano de Investimentos", version=VERSAO, lifespan=_vida)
B.montar(app, lambda: {"fases": FASES, "categorias": B.config().get("categorias", [])})


# ───────────────────────────────────────────────── utilidades
def _ciclo(c, cid: str) -> sqlite3.Row:
    r = c.execute("SELECT * FROM ciclos WHERE id=?", (cid,)).fetchone()
    if not r:
        raise HTTPException(404, "Ciclo não encontrado.")
    return r


def _anos(ci) -> list[int]:
    return list(range(int(ci["ano_inicio"]), int(ci["ano_fim"]) + 1))


def _proximo_plano_id(c) -> str:
    c.execute("INSERT OR IGNORE INTO sequencia(nome,ultimo) VALUES ('plano',0)")
    c.execute("UPDATE sequencia SET ultimo=ultimo+1 WHERE nome='plano'")
    n = c.execute("SELECT ultimo FROM sequencia WHERE nome='plano'").fetchone()[0]
    return f"{B.voc()['plano_sigla'].upper().replace(' ', '')[:6]}-{n:04d}"


def _valores(c, cid: str, camada: str) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = {}
    for r in c.execute("SELECT plano_id, ano, valor FROM valores WHERE ciclo_id=? AND camada=?", (cid, camada)):
        out.setdefault(r["plano_id"], {})[r["ano"]] = r["valor"]
    return out


def _gravar_valores(c, cid: str, pid: str, camada: str, valores: dict, anos: list[int]) -> None:
    for a in anos:
        v = float(valores.get(a, valores.get(str(a), 0)) or 0)
        if v < 0:
            raise HTTPException(400, "Valores não podem ser negativos.")
        c.execute("INSERT OR REPLACE INTO valores(ciclo_id,plano_id,camada,ano,valor) VALUES (?,?,?,?,?)", (cid, pid, camada, a, round(v, 2)))


def _copiar_camada(c, cid: str, de: str, para: str) -> None:
    c.execute("DELETE FROM valores WHERE ciclo_id=? AND camada=?", (cid, para))
    c.execute("INSERT INTO valores(ciclo_id,plano_id,camada,ano,valor) SELECT ciclo_id,plano_id,?,ano,valor FROM valores WHERE ciclo_id=? AND camada=?",
              (para, cid, de))


def _camada_valida(c, ci, camada: str) -> str:
    if camada in ("base", "pleito"):
        return camada
    if camada.startswith("c:") and c.execute("SELECT 1 FROM cenarios WHERE id=? AND ciclo_id=?", (camada[2:], ci["id"])).fetchone():
        return camada
    raise HTTPException(400, "Camada inválida.")


def _projeto_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["dados"] = load(d.get("dados"), {})
    d["curva_ref"] = load(d.get("curva_ref"), {})
    return d


def _deflator(c, ano_base: int) -> dict[int, float]:
    idx = {r["ano"]: r["inflacao"] for r in c.execute("SELECT * FROM indices")}
    out, acum = {}, 1.0
    for a in range(ano_base, ano_base + 80):
        out[a] = acum
        acum *= 1 + float(idx.get(a, 0) or 0)
    return out


def _vpl(valores: dict[int, float], ano_inicio: int, taxa: float, defl: dict[int, float] | None = None) -> float:
    return sum((v / (defl.get(a, 1.0) if defl else 1.0)) / (1 + taxa) ** (a - ano_inicio) for a, v in valores.items())


def _curva_mensal(total_ano: dict[int, float], ref: dict) -> dict[str, float]:
    """Distribui valores anuais nos meses usando a curva de referência (FSP/tendência); sem referência, 1/12."""
    out: dict[str, float] = {}
    for ano, v in sorted(total_ano.items()):
        if not v:
            continue
        pesos = [float(ref.get(f"{ano}-{m:02d}", 0) or 0) for m in range(1, 13)]
        s = sum(pesos)
        pesos = [p / s for p in pesos] if s > 0 else [1 / 12] * 12
        vals = [round(v * p, 2) for p in pesos]
        idx = max(range(12), key=lambda i: pesos[i])
        vals[idx] = round(vals[idx] + (round(v, 2) - sum(vals)), 2)
        for m, x in enumerate(vals, 1):
            if x:
                out[f"{ano}-{m:02d}"] = x
    return out


# ───────────────────────────────────────────────── ciclos
@app.get("/api/ciclos")
def listar_ciclos():
    with B.con() as c:
        out = []
        for r in c.execute("SELECT * FROM ciclos ORDER BY ano_inicio DESC, criado_em DESC"):
            d = dict(r)
            d["projetos"] = c.execute("SELECT COUNT(*) FROM projetos WHERE ciclo_id=? AND status!='devolvido'", (r["id"],)).fetchone()[0]
            out.append(d)
        return out


@app.post("/api/ciclos")
def criar_ciclo(corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo")
        try:
            ini, fim = int(corpo["ano_inicio"]), int(corpo["ano_fim"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Informe ano de início e fim.")
        if fim < ini or fim - ini > 30:
            raise HTTPException(400, "Intervalo de anos inválido.")
        cid = str(corpo.get("id") or f"c-{ini}-{fim}").strip()[:40]
        if c.execute("SELECT 1 FROM ciclos WHERE id=?", (cid,)).fetchone():
            raise HTTPException(409, "Já existe um ciclo com esse identificador.")
        nome = str(corpo.get("nome") or f"{B.voc()['plano_sigla']} {ini}–{fim}").strip()[:80]
        taxa = float(corpo.get("taxa_desconto") if corpo.get("taxa_desconto") not in (None, "") else B.org().get("taxa_desconto_aa", 0.1))
        c.execute("INSERT INTO ciclos(id,nome,ano_inicio,ano_fim,fase,taxa_desconto,criado_em,atualizado_em) VALUES (?,?,?,?,?,?,?,?)",
                  (cid, nome, ini, fim, "preparacao", taxa, agora(), agora()))
        B.auditar(c, u, "Ciclo criado", cid, {"nome": nome})
        return dict(_ciclo(c, cid))


@app.put("/api/ciclos/{cid}")
def alterar_ciclo(cid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo"); _ciclo(c, cid)
        if "nome" in corpo:
            c.execute("UPDATE ciclos SET nome=? WHERE id=?", (str(corpo["nome"]).strip()[:80], cid))
        if "taxa_desconto" in corpo:
            c.execute("UPDATE ciclos SET taxa_desconto=? WHERE id=?", (float(corpo["taxa_desconto"]), cid))
        c.execute("UPDATE ciclos SET atualizado_em=? WHERE id=?", (agora(), cid))
        return dict(_ciclo(c, cid))


@app.post("/api/ciclos/{cid}/fase")
def mudar_fase(cid: str, corpo: dict, x_usuario: str | None = Header(None)):
    """preparacao → pleitos (congela a base e abre a rodada 1) → comite → (aprovado pela aprovação de cenário) → encerrado."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo"); ci = _ciclo(c, cid)
        nova = str(corpo.get("fase") or "")
        atual = ci["fase"]
        validas = {"preparacao": {"pleitos"}, "pleitos": {"comite", "preparacao"}, "comite": {"pleitos"}, "aprovado": {"encerrado"}}
        if nova not in validas.get(atual, set()):
            raise HTTPException(409, f"Transição {atual} → {nova} não permitida.")
        if nova == "pleitos" and atual == "preparacao":
            if not c.execute("SELECT 1 FROM projetos WHERE ciclo_id=?", (cid,)).fetchone():
                raise HTTPException(409, "Inclua projetos na base antes de abrir os pleitos.")
            if not c.execute("SELECT 1 FROM valores WHERE ciclo_id=? AND camada='pleito'", (cid,)).fetchone():
                _copiar_camada(c, cid, "base", "pleito")
            c.execute("UPDATE ciclos SET rodada=rodada+1 WHERE id=?", (cid,))
        if nova == "pleitos" and atual == "comite":  # retorno com metas de corte → nova rodada
            c.execute("UPDATE ciclos SET rodada=rodada+1 WHERE id=?", (cid,))
        if nova == "preparacao" and c.execute("SELECT 1 FROM alteracoes WHERE ciclo_id=? AND camada='pleito'", (cid,)).fetchone():
            raise HTTPException(409, "Já existem pleitos registrados; não é possível voltar à preparação.")
        c.execute("UPDATE ciclos SET fase=?, atualizado_em=? WHERE id=?", (nova, agora(), cid))
        B.auditar(c, u, "Fase do ciclo", cid, {"de": atual, "para": nova})
        return dict(_ciclo(c, cid))


# ───────────────────────────────────────────────── carteira e valores
@app.get("/api/ciclos/{cid}/carteira")
def carteira(cid: str, camadas: str = "base,pleito"):
    with B.con() as c:
        ci = _ciclo(c, cid)
        cams = [_camada_valida(c, ci, x.strip()) for x in camadas.split(",") if x.strip()]
        vals = {k: _valores(c, cid, k) for k in cams}
        out = []
        for r in c.execute("SELECT * FROM projetos WHERE ciclo_id=? ORDER BY unidade, nome", (cid,)):
            d = _projeto_dict(r)
            d["valores"] = {k: {str(a): vals[k].get(r["plano_id"], {}).get(a, 0.0) for a in _anos(ci)} for k in cams}
            out.append(d)
        return {"ciclo": dict(ci), "anos": _anos(ci), "camadas": cams, "projetos": out}


def _pode_editar(c, u: dict, ci, camada: str, proj) -> None:
    fase = ci["fase"]
    if camada == "base":
        if fase != "preparacao" or u["perfil"] != "pmo":
            raise HTTPException(403, "A base só pode ser alterada pelo PMO na preparação; depois disso ela é imutável.")
    elif camada == "pleito":
        if fase != "pleitos":
            raise HTTPException(409, "Pleitos só podem ser alterados com a rodada de pleitos aberta.")
        if u["perfil"] == "gestor" and u.get("unidade") and proj["unidade"] != u["unidade"]:
            raise HTTPException(403, "Gestor só altera pleitos da própria unidade.")
        if u["perfil"] not in ("pmo", "gestor"):
            raise HTTPException(403, "Seu perfil não registra pleitos.")
    else:
        cen = c.execute("SELECT * FROM cenarios WHERE id=?", (camada[2:],)).fetchone()
        if fase != "comite" or u["perfil"] != "pmo":
            raise HTTPException(403, "Cenários são editados pelo PMO com o ciclo em comitê.")
        if cen["congelado"]:
            raise HTTPException(409, "Cenário congelado não pode ser alterado; crie um novo a partir dele.")


@app.put("/api/ciclos/{cid}/valores")
def alterar_valores(cid: str, corpo: dict, x_usuario: str | None = Header(None)):
    """Altera valores anuais de um projeto numa camada. Pleitos exigem justificativa e entram no log da rodada."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); ci = _ciclo(c, cid)
        camada = _camada_valida(c, ci, str(corpo.get("camada") or ""))
        pid = str(corpo.get("plano_id") or "")
        proj = c.execute("SELECT * FROM projetos WHERE ciclo_id=? AND plano_id=?", (cid, pid)).fetchone()
        if not proj:
            raise HTTPException(404, "Projeto não encontrado no ciclo.")
        _pode_editar(c, u, ci, camada, proj)
        novos = {int(k): float(v or 0) for k, v in (corpo.get("valores") or {}).items() if int(k) in _anos(ci)}
        atuais = _valores(c, cid, camada).get(pid, {})
        mudou = {a: (atuais.get(a, 0.0), v) for a, v in novos.items() if round(atuais.get(a, 0.0), 2) != round(v, 2)}
        just = str(corpo.get("justificativa") or "").strip()
        if mudou and camada == "pleito" and not just:
            raise HTTPException(400, "Informe a justificativa do pleito.")
        _gravar_valores(c, cid, pid, camada, {**atuais, **novos}, _anos(ci))
        for a, (de, para) in mudou.items():
            c.execute("INSERT INTO alteracoes(ciclo_id,plano_id,camada,ano,de,para,justificativa,por,perfil,em,rodada) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (cid, pid, camada, a, de, para, just, u["nome"], u["perfil"], agora(), ci["rodada"]))
        c.execute("UPDATE projetos SET atualizado_em=? WHERE ciclo_id=? AND plano_id=?", (agora(), cid, pid))
        return {"plano_id": pid, "camada": camada, "alterados": len(mudou)}


@app.post("/api/ciclos/{cid}/projetos")
def incluir_projeto(cid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo"); ci = _ciclo(c, cid)
        if ci["fase"] in ("aprovado", "encerrado"):
            raise HTTPException(409, "Ciclo já aprovado.")
        nome = str(corpo.get("nome") or "").strip()
        if not nome:
            raise HTTPException(400, "Informe o nome do projeto.")
        pid = _proximo_plano_id(c)
        c.execute("INSERT INTO projetos(ciclo_id,plano_id,codigo,nome,unidade,area,origem,categoria,prioridade,status,criado_em,atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (cid, pid, str(corpo.get("codigo") or ""), nome, corpo.get("unidade"), corpo.get("area"), "manual",
                   corpo.get("categoria"), corpo.get("prioridade"), "proposto", agora(), agora()))
        vals = corpo.get("valores") or {}
        camada_ini = "base" if ci["fase"] == "preparacao" else "pleito"
        _gravar_valores(c, cid, pid, "base", vals if camada_ini == "base" else {}, _anos(ci))
        if ci["fase"] != "preparacao":
            _gravar_valores(c, cid, pid, "pleito", vals, _anos(ci))
        B.auditar(c, u, "Projeto incluído", pid, {"nome": nome, "fase": ci["fase"]})
        return {"plano_id": pid}


@app.put("/api/ciclos/{cid}/projetos/{pid}")
def alterar_projeto(cid: str, pid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo"); _ciclo(c, cid)
        if not c.execute("SELECT 1 FROM projetos WHERE ciclo_id=? AND plano_id=?", (cid, pid)).fetchone():
            raise HTTPException(404, "Projeto não encontrado.")
        for campo in ("codigo", "nome", "unidade", "area", "categoria", "prioridade"):
            if campo in corpo:
                c.execute(f"UPDATE projetos SET {campo}=?, atualizado_em=? WHERE ciclo_id=? AND plano_id=?", (corpo[campo], agora(), cid, pid))
        return _projeto_dict(c.execute("SELECT * FROM projetos WHERE ciclo_id=? AND plano_id=?", (cid, pid)).fetchone())


@app.get("/api/ciclos/{cid}/alteracoes")
def listar_alteracoes(cid: str, plano_id: str | None = None):
    with B.con() as c:
        _ciclo(c, cid)
        sql, args = "SELECT a.*, p.nome AS projeto, p.unidade FROM alteracoes a LEFT JOIN projetos p ON p.ciclo_id=a.ciclo_id AND p.plano_id=a.plano_id WHERE a.ciclo_id=?", [cid]
        if plano_id:
            sql += " AND a.plano_id=?"; args.append(plano_id)
        return [dict(r) for r in c.execute(sql + " ORDER BY a.seq DESC LIMIT 3000", args)]


@app.post("/api/ciclos/{cid}/alteracoes/{seq}/revisar")
def revisar_alteracao(cid: str, seq: int, corpo: dict, x_usuario: str | None = Header(None)):
    """PMO aceita, pede revisão ou rejeita um pleito. Rejeitar devolve o valor anterior."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo"); ci = _ciclo(c, cid)
        a = c.execute("SELECT * FROM alteracoes WHERE seq=? AND ciclo_id=?", (seq, cid)).fetchone()
        if not a or a["camada"] != "pleito":
            raise HTTPException(404, "Pleito não encontrado.")
        dec = str(corpo.get("decisao") or "")
        if dec not in ("aceito", "rejeitado", "revisar"):
            raise HTTPException(400, "Decisão inválida.")
        if dec == "rejeitado":
            if ci["fase"] != "pleitos":
                raise HTTPException(409, "Rejeição só com a rodada de pleitos aberta.")
            c.execute("UPDATE valores SET valor=? WHERE ciclo_id=? AND plano_id=? AND camada='pleito' AND ano=?", (a["de"], cid, a["plano_id"], a["ano"]))
        c.execute("UPDATE alteracoes SET status=?, revisado_por=?, revisado_em=? WHERE seq=?", (dec, u["nome"], agora(), seq))
        return dict(c.execute("SELECT * FROM alteracoes WHERE seq=?", (seq,)).fetchone())


# ───────────────────────────────────────────────── metas de corte
@app.get("/api/ciclos/{cid}/metas")
def listar_metas(cid: str):
    with B.con() as c:
        return [dict(r) for r in c.execute("SELECT * FROM metas WHERE ciclo_id=? ORDER BY escopo, chave", (cid,))]


@app.put("/api/ciclos/{cid}/metas")
def gravar_metas(cid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo", "comite"); _ciclo(c, cid)
        for m in corpo.get("metas") or []:
            esc, ch = str(m.get("escopo") or "unidade"), str(m.get("chave") or "")
            if not ch:
                continue
            if m.get("percentual") in (None, ""):
                c.execute("DELETE FROM metas WHERE ciclo_id=? AND escopo=? AND chave=?", (cid, esc, ch)); continue
            c.execute("INSERT OR REPLACE INTO metas(ciclo_id,escopo,chave,percentual,observacao,por,em) VALUES (?,?,?,?,?,?,?)",
                      (cid, esc, ch, float(m["percentual"]), str(m.get("observacao") or ""), u["nome"], agora()))
        return listar_metas(cid)


# ───────────────────────────────────────────────── cenários
@app.get("/api/ciclos/{cid}/cenarios")
def listar_cenarios(cid: str):
    with B.con() as c:
        out = []
        for r in c.execute("SELECT * FROM cenarios WHERE ciclo_id=? ORDER BY criado_em", (cid,)):
            d = dict(r); d["decisoes"] = load(d["decisoes"], {})
            d["total"] = c.execute("SELECT COALESCE(SUM(valor),0) FROM valores WHERE ciclo_id=? AND camada=?", (cid, "c:" + r["id"])).fetchone()[0]
            out.append(d)
        return out


@app.post("/api/ciclos/{cid}/cenarios")
def criar_cenario(cid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo"); ci = _ciclo(c, cid)
        if ci["fase"] != "comite":
            raise HTTPException(409, "Abra a fase de comitê para criar cenários.")
        origem = _camada_valida(c, ci, str(corpo.get("origem") or "pleito"))
        sid = "s" + uuid.uuid4().hex[:8]
        nome = str(corpo.get("nome") or f"Cenário {c.execute('SELECT COUNT(*) FROM cenarios WHERE ciclo_id=?', (cid,)).fetchone()[0] + 1}")[:80]
        dec = {}
        if origem.startswith("c:"):
            dec = load(c.execute("SELECT decisoes FROM cenarios WHERE id=?", (origem[2:],)).fetchone()[0], {})
        c.execute("INSERT INTO cenarios(id,ciclo_id,nome,descricao,origem,criado_por,criado_em,decisoes) VALUES (?,?,?,?,?,?,?,?)",
                  (sid, cid, nome, str(corpo.get("descricao") or ""), origem, u["nome"], agora(), dump(dec)))
        _copiar_camada(c, cid, origem, "c:" + sid)
        B.auditar(c, u, "Cenário criado", sid, {"nome": nome, "origem": origem})
        return {"id": sid, "nome": nome}


def _cenario(c, cid: str, sid: str) -> sqlite3.Row:
    r = c.execute("SELECT * FROM cenarios WHERE id=? AND ciclo_id=?", (sid, cid)).fetchone()
    if not r:
        raise HTTPException(404, "Cenário não encontrado.")
    return r


@app.post("/api/ciclos/{cid}/cenarios/{sid}/decisao")
def decidir_projeto(cid: str, sid: str, corpo: dict, x_usuario: str | None = Header(None)):
    """Decisão do comitê por projeto: manter, cortar (zera) ou postergar N anos (desloca a curva)."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); ci = _ciclo(c, cid); cen = _cenario(c, cid, sid)
        pid = str(corpo.get("plano_id") or "")
        proj = c.execute("SELECT * FROM projetos WHERE ciclo_id=? AND plano_id=?", (cid, pid)).fetchone()
        if not proj:
            raise HTTPException(404, "Projeto não encontrado.")
        _pode_editar(c, u, ci, "c:" + sid, proj)
        dec = str(corpo.get("decisao") or "")
        anos, cam = _anos(ci), "c:" + sid
        atual = _valores(c, cid, cam).get(pid, {})
        decs = load(cen["decisoes"], {})
        if dec == "cortar":
            novo = {a: 0.0 for a in anos}
        elif dec == "postergar":
            n = max(1, min(int(corpo.get("anos") or 1), len(anos)))
            novo = {a: 0.0 for a in anos}
            for a in anos:
                destino = min(a + n, anos[-1])
                novo[destino] += atual.get(a, 0.0)
        elif dec == "manter":
            novo = atual
        else:
            raise HTTPException(400, "Decisão deve ser manter, cortar ou postergar.")
        _gravar_valores(c, cid, pid, cam, novo, anos)
        decs[pid] = {"decisao": dec, "anos": corpo.get("anos"), "por": u["nome"], "em": agora(), "obs": str(corpo.get("obs") or "")}
        if dec == "manter":
            decs.pop(pid, None)
        c.execute("UPDATE cenarios SET decisoes=? WHERE id=?", (dump(decs), sid))
        for a in anos:
            if round(atual.get(a, 0.0), 2) != round(novo.get(a, 0.0), 2):
                c.execute("INSERT INTO alteracoes(ciclo_id,plano_id,camada,ano,de,para,justificativa,por,perfil,em,rodada) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (cid, pid, cam, a, atual.get(a, 0.0), novo.get(a, 0.0), f"Comitê: {dec}", u["nome"], u["perfil"], agora(), ci["rodada"]))
        return {"plano_id": pid, "decisao": dec}


@app.post("/api/ciclos/{cid}/cenarios/{sid}/congelar")
def congelar_cenario(cid: str, sid: str, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo"); _cenario(c, cid, sid)
        c.execute("UPDATE cenarios SET congelado=1, congelado_em=? WHERE id=?", (agora(), sid))
        B.auditar(c, u, "Cenário congelado", sid)
        return {"ok": True}


@app.delete("/api/ciclos/{cid}/cenarios/{sid}")
def excluir_cenario(cid: str, sid: str, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo"); cen = _cenario(c, cid, sid)
        if cen["aprovado"]:
            raise HTTPException(409, "Cenário aprovado não pode ser excluído.")
        c.execute("DELETE FROM valores WHERE ciclo_id=? AND camada=?", (cid, "c:" + sid))
        c.execute("DELETE FROM cenarios WHERE id=?", (sid,))
        return {"ok": True}


@app.post("/api/ciclos/{cid}/cenarios/{sid}/aprovar")
def aprovar_cenario(cid: str, sid: str, corpo: dict | None = None, x_usuario: str | None = Header(None)):
    """Aprovação do comitê: cenário vira o plano oficial do ciclo e é publicado (Originação e PMP)."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "comite", "pmo", msg="Aprovação exclusiva do comitê ou do PMO.")
        ci = _ciclo(c, cid); cen = _cenario(c, cid, sid)
        if ci["fase"] != "comite":
            raise HTTPException(409, "O ciclo precisa estar em comitê.")
        if not cen["congelado"]:
            raise HTTPException(409, "Congele o cenário antes de aprovar.")
        _copiar_camada(c, cid, "c:" + sid, "aprovado")
        vals = _valores(c, cid, "aprovado")
        decs = load(cen["decisoes"], {})
        for r in c.execute("SELECT plano_id FROM projetos WHERE ciclo_id=? AND status!='devolvido'", (cid,)).fetchall():
            total = sum(vals.get(r["plano_id"], {}).values())
            st = "cortado" if total <= 0 else ("postergado" if (decs.get(r["plano_id"]) or {}).get("decisao") == "postergar" else "aprovado")
            c.execute("UPDATE projetos SET status=?, atualizado_em=? WHERE ciclo_id=? AND plano_id=?", (st, agora(), cid, r["plano_id"]))
        ts = agora()
        c.execute("UPDATE cenarios SET aprovado=1, aprovado_por=?, aprovado_em=? WHERE id=?", (u["nome"], ts, sid))
        c.execute("UPDATE ciclos SET fase='aprovado', cenario_aprovado=?, atualizado_em=? WHERE id=?", (sid, ts, cid))
        B.auditar(c, u, "Cenário aprovado", sid, {"ciclo": cid})
        res = _publicar_aprovacao(c, u, cid)
        return {"ok": True, "publicacao": res}


# ───────────────────────────────────────────────── integração (Data Core)
def _publicar_aprovacao(c, u: dict, cid: str) -> dict:
    """Publica retorno_originacao (projetos vindos da Originação) e plano_aprovado (carteira em execução)."""
    ci = _ciclo(c, cid)
    vals = _valores(c, cid, "aprovado")
    lote = "RET-" + agora()[:10].replace("-", "") + "-" + uuid.uuid4().hex[:6].upper()
    ok = falhas = 0
    for p in c.execute("SELECT * FROM projetos WHERE ciclo_id=? AND status!='devolvido'", (cid,)).fetchall():
        v = {a: vals.get(p["plano_id"], {}).get(a, 0.0) for a in _anos(ci)}
        total = round(sum(v.values()), 2)
        curva = _curva_mensal(v, load(p["curva_ref"], {}))
        base = {"plano_id": p["plano_id"], "plano_revisao": ci["cenario_aprovado"], "ciclo_id": cid, "ciclo_nome": ci["nome"],
                "ciclo_inicio": ci["ano_inicio"], "ciclo_fim": ci["ano_fim"], "orcamento_total": total, "valores_ano": {str(a): x for a, x in v.items()},
                "curva_mensal": curva, "aprovado_por": u["nome"], "aprovado_em": agora(), "status_plano": p["status"]}
        try:
            if p["origem"] == "originacao" and p["id_mestre"]:
                d = load(p["dados"], {})
                payload = {**base, "return_batch_id": lote, "id_mestre": p["id_mestre"],
                           "status": "cancelado" if p["status"] == "cortado" else "aprovado",
                           "ciclo_id": d.get("ciclo_origem") or cid, "ciclo_inicio": d.get("ciclo_inicio_origem") or ci["ano_inicio"],
                           "ciclo_fim": d.get("ciclo_fim_origem") or ci["ano_fim"], "comentario": "Decisão do comitê: " + p["status"]}
                if p["status"] == "cortado":
                    payload["curva_mensal"], payload["orcamento_total"] = {}, 0
                B.publicar("portfolio", "retorno_originacao", p["id_mestre"], payload, u)
                destino, chave = "originacao", p["id_mestre"]
            else:
                chave = p["codigo"] or p["plano_id"]
                B.publicar("pmp", "plano_aprovado", chave, {**base, "codigo": chave, "nome": p["nome"], "unidade": p["unidade"], "area": p["area"],
                                                             "status": p["status"]}, u)
                destino = "pmp"
            ok += 1
            c.execute("INSERT INTO publicacoes(ciclo_id,destino,chave,status,em,por,detalhe) VALUES (?,?,?,?,?,?,?)", (cid, destino, chave, "publicado", agora(), u["nome"], p["status"]))
        except HTTPException as e:
            falhas += 1
            c.execute("INSERT INTO publicacoes(ciclo_id,destino,chave,status,em,por,detalhe) VALUES (?,?,?,?,?,?,?)", (cid, "?", p["plano_id"], "falha", agora(), u["nome"], str(e.detail)[:300]))
    return {"publicados": ok, "falhas": falhas, "lote": lote}


@app.post("/api/ciclos/{cid}/republicar")
def republicar(cid: str, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo")
        if _ciclo(c, cid)["fase"] not in ("aprovado", "encerrado"):
            raise HTTPException(409, "Ciclo ainda não aprovado.")
        return _publicar_aprovacao(c, u, cid)


def _ciclo_destino(c, ini: int | None, fim: int | None, preferido: str | None = None):
    if preferido:
        r = c.execute("SELECT * FROM ciclos WHERE id=? AND fase IN ('preparacao','pleitos','comite')", (preferido,)).fetchone()
        if r:
            return r
    if ini and fim:
        r = c.execute("SELECT * FROM ciclos WHERE ano_inicio=? AND ano_fim=? AND fase IN ('preparacao','pleitos','comite') ORDER BY criado_em DESC", (ini, fim)).fetchone()
        if r:
            return r
    return c.execute("SELECT * FROM ciclos WHERE fase IN ('preparacao','pleitos','comite') ORDER BY ano_inicio DESC, criado_em DESC").fetchone()


def _anual_de_mensal(m: dict) -> dict[int, float]:
    out: dict[int, float] = {}
    for k, v in (m or {}).items():
        if re.fullmatch(r"\d{4}-\d{2}", str(k)):
            out[int(str(k)[:4])] = out.get(int(str(k)[:4]), 0.0) + float(v or 0)
    return out


def _entrar_no_ciclo(c, u, ci, *, origem: str, id_mestre: str | None, codigo: str | None, nome: str, unidade, area,
                     anual: dict[int, float], curva_ref: dict, dados: dict) -> tuple[str, str]:
    cid = ci["id"]
    proj = None
    if id_mestre:
        proj = c.execute("SELECT * FROM projetos WHERE ciclo_id=? AND id_mestre=?", (cid, id_mestre)).fetchone()
    if not proj and codigo:
        proj = c.execute("SELECT * FROM projetos WHERE ciclo_id=? AND codigo=?", (cid, codigo)).fetchone()
    anos = _anos(ci)
    if proj:
        pid, acao = proj["plano_id"], "atualizado"
        c.execute("UPDATE projetos SET nome=?, unidade=?, area=?, curva_ref=?, dados=?, status=CASE WHEN status='devolvido' THEN 'proposto' ELSE status END, atualizado_em=? WHERE ciclo_id=? AND plano_id=?",
                  (nome, unidade, area, dump(curva_ref), dump(dados), agora(), cid, pid))
    else:
        pid, acao = _proximo_plano_id(c), "incluido"
        c.execute("INSERT INTO projetos(ciclo_id,plano_id,codigo,id_mestre,nome,unidade,area,origem,status,curva_ref,dados,criado_em,atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (cid, pid, codigo or "", id_mestre, nome, unidade, area, origem, "proposto", dump(curva_ref), dump(dados), agora(), agora()))
    if ci["fase"] == "preparacao":
        _gravar_valores(c, cid, pid, "base", anual, anos)
    else:  # entrada tardia: base zerada, valores entram como pleito (aparecem como "novo" na comparação)
        if not c.execute("SELECT 1 FROM valores WHERE ciclo_id=? AND plano_id=? AND camada='base'", (cid, pid)).fetchone():
            _gravar_valores(c, cid, pid, "base", {}, anos)
        _gravar_valores(c, cid, pid, "pleito", anual, anos)
        for a in anos:
            if anual.get(a):
                c.execute("INSERT INTO alteracoes(ciclo_id,plano_id,camada,ano,de,para,justificativa,por,perfil,em,rodada) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (cid, pid, "pleito", a, 0, anual[a], f"Entrada pela integração ({origem})", u["nome"], u["perfil"], agora(), ci["rodada"]))
    return pid, acao


@app.get("/api/integracoes/pendentes")
def integracoes_pendentes(x_usuario: str | None = Header(None)):
    with B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo")
        ent = B.pendentes(c, "portfolio", "entrada_originacao")
        exe = B.pendentes(c, "portfolio", "execucao")
        pub = [dict(r) for r in c.execute("SELECT * FROM publicacoes ORDER BY seq DESC LIMIT 200")]
        return {"originacao": [{"id_mestre": r["entity_key"], "nome": ((r.get("payload") or {}).get("projeto") or {}).get("nome"),
                                "unidade": ((r.get("payload") or {}).get("projeto") or {}).get("unidade"), "em": r.get("updated_at")} for r in ent],
                "execucao": [{"codigo": r["entity_key"], "nome": (r.get("payload") or {}).get("nome"), "rodada": (r.get("payload") or {}).get("rodada"),
                              "em": r.get("updated_at")} for r in exe],
                "publicacoes": pub, "modo": "hub" if hubkit.no_hub() else "isolado"}


@app.post("/api/integracoes/sincronizar")
def sincronizar(corpo: dict | None = None, x_usuario: str | None = Header(None)):
    """Recebe do Data Core: novos projetos da Originação e projetos em execução (tendência) da PMP."""
    corpo = corpo or {}
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo")
        return _sincronizar(c, u, corpo.get("ciclo_id"))


def _sincronizar(c, u, ciclo_id=None) -> dict:
    corpo = {"ciclo_id": ciclo_id}
    res = {"originacao": [], "execucao": []}
    for r in B.pendentes(c, "portfolio", "entrada_originacao"):
        p = (r.get("payload") or {}).get("projeto") or {}
        ci = _ciclo_destino(c, p.get("ciclo_inicio"), p.get("ciclo_fim"), corpo.get("ciclo_id"))
        if not ci:
            res["originacao"].append({"id_mestre": r["entity_key"], "resultado": "sem ciclo aberto"}); continue
        fsp = p.get("fsp") or {}
        ref = fsp.get("distribuicaoMensal") or fsp.get("distribuicao_mensal") or {}
        anual = _anual_de_mensal(ref) or {int(k): float(v or 0) for k, v in (p.get("distribuicao_anual_referencia") or {}).items() if str(k).isdigit()}
        pid, acao = _entrar_no_ciclo(c, u, ci, origem="originacao", id_mestre=r["entity_key"], codigo=p.get("pep") or "",
                                     nome=p.get("nome") or r["entity_key"], unidade=p.get("unidade"), area=p.get("diretoria"),
                                     anual=anual, curva_ref=ref, dados={"ciclo_origem": p.get("ciclo_pqq"), "ciclo_inicio_origem": p.get("ciclo_inicio"), "ciclo_fim_origem": p.get("ciclo_fim"), "responsavel": p.get("responsavel"),
                                                                       "solicitante": p.get("solicitante"), "lote_id": (r.get("payload") or {}).get("lote_id")})
        B.marcar_recebido(c, r, acao)
        res["originacao"].append({"id_mestre": r["entity_key"], "plano_id": pid, "ciclo": ci["id"], "resultado": acao})
    for r in B.pendentes(c, "portfolio", "execucao"):
        p = r.get("payload") or {}
        ci = _ciclo_destino(c, None, None, corpo.get("ciclo_id"))
        if not ci:
            res["execucao"].append({"codigo": r["entity_key"], "resultado": "sem ciclo aberto"}); continue
        anual = {int(k): float(v or 0) for k, v in (p.get("tendencia_ano") or {}).items() if str(k).isdigit()}
        anual = {a: v for a, v in anual.items() if ci["ano_inicio"] <= a <= ci["ano_fim"]}
        pid, acao = _entrar_no_ciclo(c, u, ci, origem="carteira", id_mestre=p.get("id_mestre"), codigo=r["entity_key"],
                                     nome=p.get("nome") or r["entity_key"], unidade=p.get("unidade"), area=p.get("area"),
                                     anual=anual, curva_ref=p.get("curva_mensal_tendencia") or {},
                                     dados={"rodada": p.get("rodada"), "realizado_ano": p.get("realizado_ano"), "orcado_ano": p.get("orcado_ano")})
        B.marcar_recebido(c, r, acao)
        res["execucao"].append({"codigo": r["entity_key"], "plano_id": pid, "ciclo": ci["id"], "resultado": acao})
    if res["originacao"] or res["execucao"]:
        B.auditar(c, u, "Sincronização", "", {k: len(v) for k, v in res.items()})
    return res


def _sync_auto(c, u) -> dict:
    r = _sincronizar(c, u)
    return {"projetos_originacao": sum(1 for x in r["originacao"] if x.get("plano_id")),
            "projetos_em_execucao": sum(1 for x in r["execucao"] if x.get("plano_id")),
            "aguardando_ciclo": sum(1 for v in r.values() for x in v if x.get("resultado") == "sem ciclo aberto")}


def _pendencias(c) -> list[dict]:
    """O que o Plano coloca na agenda do ecossistema."""
    out, voc = [], B.voc()
    for ci in c.execute("SELECT * FROM ciclos WHERE fase NOT IN ('aprovado','encerrado')"):
        rotulo = {"preparacao": "em preparação", "pleitos": "recebendo pleitos", "comite": "em comitê"}.get(ci["fase"], ci["fase"])
        out.append({"chave": f"ciclo:{ci['id']}:{ci['fase']}", "natureza": "acao", "tipo": "ciclo", "titulo": f"{voc['plano_sigla']} {ci['nome']} {rotulo}",
                    "descricao": f"Fase atual: {ci['fase']}.", "perfis": ["pmo", "comite"] if ci["fase"] == "comite" else ["pmo", "gestor"],
                    "link": "#/ciclos", "prioridade": "alta" if ci["fase"] == "comite" else "normal"})
        n = c.execute("SELECT COUNT(*) FROM alteracoes WHERE ciclo_id=? AND status='registrada' AND camada='pleito'", (ci["id"],)).fetchone()[0]
        if n and ci["fase"] in ("pleitos", "comite"):
            out.append({"chave": f"revisar:{ci['id']}", "natureza": "tarefa", "esforco_h": max(0.5, round(0.25 * n, 1)), "tipo": "revisao", "titulo": f"{n} pleito(s) aguardando revisão — {ci['nome']}",
                        "perfis": ["pmo"], "link": "#/pleitos", "prioridade": "normal"})
        for ce in c.execute("SELECT * FROM cenarios WHERE ciclo_id=? AND congelado=1 AND aprovado=0", (ci["id"],)):
            out.append({"chave": f"cenario:{ce['id']}", "natureza": "acao", "tipo": "aprovacao", "titulo": f"Cenário “{ce['nome']}” aguardando aprovação",
                        "perfis": ["comite", "pmo"], "link": "#/cenarios", "prioridade": "alta",
                        "acoes": [B.acao("aprovar", "Aprovar como plano oficial", f"/api/ciclos/{ci['id']}/cenarios/{ce['id']}/aprovar", estilo="ok")]})
    return out


B.automatico(_sync_auto, _pendencias)


@app.post("/api/ciclos/{cid}/projetos/{pid}/devolver")
def devolver_projeto(cid: str, pid: str, corpo: dict, x_usuario: str | None = Header(None)):
    """Devolve à Originação um projeto que não deve entrar no ciclo (ex.: imaturo)."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo"); ci = _ciclo(c, cid)
        p = c.execute("SELECT * FROM projetos WHERE ciclo_id=? AND plano_id=?", (cid, pid)).fetchone()
        if not p or p["origem"] != "originacao":
            raise HTTPException(404, "Projeto da Originação não encontrado neste ciclo.")
        motivo = str(corpo.get("comentario") or "").strip()
        if not motivo:
            raise HTTPException(400, "Informe o motivo da devolução.")
        B.publicar("portfolio", "retorno_originacao", p["id_mestre"], {"return_batch_id": "DEV-" + uuid.uuid4().hex[:6].upper(),
                   "id_mestre": p["id_mestre"], "status": "devolvido", "plano_id": pid, "ciclo_id": load(p["dados"], {}).get("ciclo_origem") or cid,
                   "comentario": motivo, "aprovado_por": u["nome"], "aprovado_em": agora()}, u)
        c.execute("UPDATE projetos SET status='devolvido', atualizado_em=? WHERE ciclo_id=? AND plano_id=?", (agora(), cid, pid))
        c.execute("DELETE FROM valores WHERE ciclo_id=? AND plano_id=? AND camada NOT IN ('base')", (cid, pid))
        c.execute("UPDATE valores SET valor=0 WHERE ciclo_id=? AND plano_id=?", (cid, pid))
        B.auditar(c, u, "Projeto devolvido à Originação", pid, motivo)
        return {"ok": True}


# ───────────────────────────────────────────────── análises
def _totais(c, ci, camada: str) -> dict:
    cid = ci["id"]
    vals = _valores(c, cid, camada)
    projs = {r["plano_id"]: dict(r) for r in c.execute("SELECT * FROM projetos WHERE ciclo_id=? AND status!='devolvido'", (cid,))}
    por_ano = {a: 0.0 for a in _anos(ci)}
    por_unidade: dict[str, dict[int, float]] = {}
    por_area: dict[str, float] = {}
    por_origem: dict[str, float] = {}
    for pid, p in projs.items():
        v = vals.get(pid, {})
        for a in _anos(ci):
            x = v.get(a, 0.0)
            por_ano[a] += x
            por_unidade.setdefault(p["unidade"] or "—", {y: 0.0 for y in _anos(ci)})[a] += x
            por_area[p["area"] or "—"] = por_area.get(p["area"] or "—", 0.0) + x
            por_origem[p["origem"]] = por_origem.get(p["origem"], 0.0) + x
    taxa = float(ci["taxa_desconto"] if ci["taxa_desconto"] is not None else 0.1)
    defl = _deflator(c, int(ci["ano_inicio"]))
    return {"camada": camada, "total": sum(por_ano.values()), "por_ano": por_ano, "por_unidade": por_unidade, "por_area": por_area,
            "por_origem": por_origem, "vpl": _vpl(por_ano, int(ci["ano_inicio"]), taxa), "real": sum(v / defl.get(a, 1) for a, v in por_ano.items()),
            "projetos": len([p for p in projs if sum(vals.get(p, {}).values()) > 0])}


@app.get("/api/ciclos/{cid}/painel")
def painel(cid: str, camada: str = "pleito"):
    with B.con() as c:
        ci = _ciclo(c, cid)
        cam = _camada_valida(c, ci, camada) if camada != "aprovado" else "aprovado"
        t = _totais(c, ci, cam)
        t["base"] = _totais(c, ci, "base")
        t["status"] = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM projetos WHERE ciclo_id=? GROUP BY status", (cid,))}
        t["pleitos_pendentes"] = c.execute("SELECT COUNT(*) FROM alteracoes WHERE ciclo_id=? AND camada='pleito' AND status='registrada'", (cid,)).fetchone()[0]
        return {"ciclo": dict(ci), "anos": _anos(ci), **t}


@app.get("/api/ciclos/{cid}/comparar")
def comparar(cid: str, a: str = "base", b: str = "pleito"):
    """Comparação A × B por unidade e ano, ponte (novos, cortes, postergações, aumentos, reduções) e metas de corte."""
    with B.con() as c:
        ci = _ciclo(c, cid)
        ca = a if a == "aprovado" else _camada_valida(c, ci, a)
        cb = b if b == "aprovado" else _camada_valida(c, ci, b)
        va, vb = _valores(c, cid, ca), _valores(c, cid, cb)
        anos = _anos(ci)
        ponte = {"novos": 0.0, "cortes": 0.0, "postergacoes": 0.0, "aumentos": 0.0, "reducoes": 0.0}
        linhas, unid = [], {}
        for p in c.execute("SELECT * FROM projetos WHERE ciclo_id=? AND status!='devolvido' ORDER BY unidade, nome", (cid,)):
            xa, xb = va.get(p["plano_id"], {}), vb.get(p["plano_id"], {})
            ta, tb = sum(xa.values()), sum(xb.values())
            if ta == 0 and tb > 0:
                tipo = "novos"
            elif ta > 0 and tb == 0:
                tipo = "cortes"
            elif abs(ta - tb) < 0.01 and any(abs(xa.get(y, 0) - xb.get(y, 0)) > 0.01 for y in anos):
                tipo = "postergacoes"
            elif tb > ta:
                tipo = "aumentos"
            elif tb < ta:
                tipo = "reducoes"
            else:
                tipo = None
            if tipo:
                ponte[tipo] += (tb - ta) if tipo != "postergacoes" else sum(xb.get(y, 0) - xa.get(y, 0) for y in anos[:1])
            u = p["unidade"] or "—"
            un = unid.setdefault(u, {"unidade": u, "a": 0.0, "b": 0.0, "a_ano": {y: 0.0 for y in anos}, "b_ano": {y: 0.0 for y in anos}})
            un["a"] += ta; un["b"] += tb
            for y in anos:
                un["a_ano"][y] += xa.get(y, 0); un["b_ano"][y] += xb.get(y, 0)
            if tipo:
                linhas.append({"plano_id": p["plano_id"], "nome": p["nome"], "unidade": u, "a": ta, "b": tb, "dif": tb - ta, "tipo": tipo})
        metas = {(m["escopo"], m["chave"]): m["percentual"] for m in c.execute("SELECT * FROM metas WHERE ciclo_id=?", (cid,))}
        for un in unid.values():
            m = metas.get(("unidade", un["unidade"]))
            un["corte_pct"] = (un["a"] - un["b"]) / un["a"] if un["a"] else None
            un["meta_pct"] = m
            un["meta_ok"] = None if m is None else (un["corte_pct"] or 0) >= m - 1e-9
        tot_a, tot_b = sum(x["a"] for x in unid.values()), sum(x["b"] for x in unid.values())
        return {"a": ca, "b": cb, "anos": anos, "total_a": tot_a, "total_b": tot_b, "ponte": ponte,
                "unidades": sorted(unid.values(), key=lambda x: x["unidade"]), "projetos": sorted(linhas, key=lambda x: x["dif"])}


@app.get("/api/ciclos/{cid}/risco-execucao")
def risco_execucao(cid: str, camada: str = "pleito", ano: int | None = None):
    """Monte Carlo (bootstrap do histórico real de execução da PMP): probabilidade de executar o planejado."""
    with B.con() as c:
        ci = _ciclo(c, cid)
        cam = camada if camada == "aprovado" else _camada_valida(c, ci, camada)
        ano = ano or int(ci["ano_inicio"])
        try:
            hist = hubkit.consultar("portfolio", "execucao", limit=5000)
        except Exception:
            hist = []
        razoes = []
        for r in hist:
            p = r.get("payload") or {}
            orc, real = p.get("orcado_ano") or {}, p.get("realizado_ano") or {}
            for a, o in orc.items():
                try:
                    o, x = float(o or 0), float(real.get(a) or 0)
                except (TypeError, ValueError):
                    continue
                if o > 0 and str(a).isdigit() and int(a) < int(p.get("ano_rodada") or 9999):
                    razoes.append(min(x / o, 3.0))
        plano = [v.get(ano, 0.0) for v in _valores(c, ci["id"], cam).values() if v.get(ano, 0.0) > 0]
        total = sum(plano)
        if len(razoes) < 10 or not plano:
            return {"ano": ano, "planejado": total, "suficiente": False, "amostras": len(razoes),
                    "mensagem": "Histórico insuficiente: são necessárias ao menos 10 observações de execução (orçado × realizado) publicadas pela PMP."}
        n = int(B.config().get("simulacoes_monte_carlo", 5000))
        rnd = random.Random(42)
        sims = sorted(sum(v * rnd.choice(razoes) for v in plano) for _ in range(n))
        pc = lambda q: sims[min(n - 1, int(q * n))]
        faixas = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        return {"ano": ano, "planejado": total, "suficiente": True, "amostras": len(razoes), "simulacoes": n,
                "p10": pc(.1), "p50": pc(.5), "p90": pc(.9), "media": sum(sims) / n,
                "prob_atingir": {str(f): sum(1 for s in sims if s >= f * total) / n for f in faixas},
                "histograma": _histograma(sims, 20)}


def _histograma(xs: list[float], k: int) -> list[dict]:
    lo, hi = xs[0], xs[-1]
    if hi <= lo:
        return [{"de": lo, "ate": hi, "n": len(xs)}]
    w = (hi - lo) / k
    out = [{"de": lo + i * w, "ate": lo + (i + 1) * w, "n": 0} for i in range(k)]
    for x in xs:
        out[min(k - 1, int((x - lo) / w))]["n"] += 1
    return out


# ───────────────────────────────────────────────── índices
@app.get("/api/indices")
def listar_indices():
    with B.con() as c:
        return [dict(r) for r in c.execute("SELECT * FROM indices ORDER BY ano")]


@app.put("/api/indices")
def gravar_indices(corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo")
        c.execute("DELETE FROM indices")
        for i in corpo.get("indices") or []:
            if str(i.get("ano", "")).isdigit() and i.get("inflacao") not in (None, ""):
                c.execute("INSERT INTO indices(ano,inflacao) VALUES (?,?)", (int(i["ano"]), float(i["inflacao"])))
        B.auditar(c, u, "Índices de inflação atualizados")
    return listar_indices()


# ───────────────────────────────────────────────── planilhas
COLS_FIXAS = ["Código", "Nome", "Unidade", "Área", "Categoria", "Prioridade"]


def _xlsx(wb) -> bytes:
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def _cabecalho(ws, cols):
    ws.append(cols)
    for cel in ws[1]:
        cel.font = Font(bold=True, color="FFFFFF"); cel.fill = PatternFill("solid", fgColor="1E3A6E")
    for i, col in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = max(12, min(48, len(str(col)) + 4))
    ws.freeze_panes = "C2"


@app.get("/api/ciclos/{cid}/modelo.xlsx")
def modelo_xlsx(cid: str):
    with B.con() as c:
        ci = _ciclo(c, cid)
    wb = Workbook(); ws = wb.active; ws.title = "Base"
    _cabecalho(ws, COLS_FIXAS + [str(a) for a in _anos(ci)])
    org = B.org()
    ws.append(["", "Exemplo de projeto (apague esta linha)", (org.get("unidades") or [{}])[0].get("nome", ""), (org.get("areas") or [""])[0], "Expansão", 1] + [0 for _ in _anos(ci)])
    return Response(_xlsx(wb), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="modelo-base-{cid}.xlsx"'})


@app.post("/api/ciclos/{cid}/importar")
async def importar_xlsx(cid: str, arquivo: UploadFile = File(...), x_usuario: str | None = Header(None)):
    """Importa a base do ciclo a partir do modelo (colunas fixas + uma coluna por ano). Só na preparação."""
    conteudo = await arquivo.read()
    try:
        wb = load_workbook(io.BytesIO(conteudo), data_only=True, read_only=True)
    except Exception:
        raise HTTPException(400, "Arquivo .xlsx inválido.")
    ws = wb.worksheets[0]
    linhas = list(ws.iter_rows(values_only=True))
    if not linhas:
        raise HTTPException(400, "Planilha vazia.")
    cab = [str(x or "").strip() for x in linhas[0]]
    idx = {nome.lower(): i for i, nome in enumerate(cab)}
    if "nome" not in idx:
        raise HTTPException(400, "A planilha precisa ter a coluna 'Nome'.")
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo"); ci = _ciclo(c, cid)
        if ci["fase"] != "preparacao":
            raise HTTPException(409, "Importação de base só na fase de preparação.")
        anos = _anos(ci)
        col_ano = {a: idx[str(a)] for a in anos if str(a) in idx}
        g = lambda ln, k: ln[idx[k]] if k in idx and idx[k] < len(ln) else None
        inc = atu = ign = 0
        for ln in linhas[1:]:
            nome = str(g(ln, "nome") or "").strip()
            if not nome or nome.lower().startswith("exemplo de projeto"):
                ign += 1; continue
            codigo = str(g(ln, "código") or g(ln, "codigo") or "").strip()
            vals = {}
            for a, i in col_ano.items():
                try:
                    vals[a] = float(ln[i] or 0) if i < len(ln) else 0.0
                except (TypeError, ValueError):
                    raise HTTPException(400, f"Valor inválido em '{nome}', ano {a}.")
            proj = c.execute("SELECT * FROM projetos WHERE ciclo_id=? AND codigo=? AND codigo!=''", (cid, codigo)).fetchone() if codigo else None
            if not proj:
                proj = c.execute("SELECT * FROM projetos WHERE ciclo_id=? AND nome=?", (cid, nome)).fetchone()
            campos = (codigo, nome, g(ln, "unidade"), g(ln, "área") or g(ln, "area"), g(ln, "categoria"), g(ln, "prioridade"))
            if proj:
                pid = proj["plano_id"]; atu += 1
                c.execute("UPDATE projetos SET codigo=?, nome=?, unidade=?, area=?, categoria=?, prioridade=?, atualizado_em=? WHERE ciclo_id=? AND plano_id=?",
                          (*campos, agora(), cid, pid))
            else:
                pid = _proximo_plano_id(c); inc += 1
                c.execute("INSERT INTO projetos(ciclo_id,plano_id,codigo,nome,unidade,area,categoria,prioridade,origem,status,criado_em,atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (cid, pid, *campos, "importacao", "proposto", agora(), agora()))
            _gravar_valores(c, cid, pid, "base", vals, anos)
        B.auditar(c, u, "Base importada", cid, {"arquivo": arquivo.filename, "incluidos": inc, "atualizados": atu})
        return {"incluidos": inc, "atualizados": atu, "ignorados": ign}


@app.get("/api/ciclos/{cid}/exportar.xlsx")
def exportar_xlsx(cid: str, camadas: str = "base,pleito"):
    with B.con() as c:
        ci = _ciclo(c, cid)
        cams = [x for x in camadas.split(",") if x]
        cams = [x if x == "aprovado" else _camada_valida(c, ci, x) for x in cams]
        nomes_cen = {"c:" + r["id"]: r["nome"] for r in c.execute("SELECT id, nome FROM cenarios WHERE ciclo_id=?", (cid,))}
        vals = {k: _valores(c, cid, k) for k in cams}
        projs = c.execute("SELECT * FROM projetos WHERE ciclo_id=? ORDER BY unidade, nome", (cid,)).fetchall()
        alts = c.execute("SELECT a.*, p.nome AS projeto FROM alteracoes a LEFT JOIN projetos p ON p.ciclo_id=a.ciclo_id AND p.plano_id=a.plano_id WHERE a.ciclo_id=? ORDER BY seq", (cid,)).fetchall()
    anos = _anos(ci)
    wb = Workbook(); wb.remove(wb.active)
    for cam in cams:
        ws = wb.create_sheet(re.sub(r"[\[\]\*\?/\\:]", "", nomes_cen.get(cam, cam))[:31] or "camada")
        _cabecalho(ws, [B.voc()["plano_sigla"] + " ID", "Origem", "Status"] + COLS_FIXAS + [str(a) for a in anos] + ["Total"])
        for p in projs:
            v = vals[cam].get(p["plano_id"], {})
            ws.append([p["plano_id"], p["origem"], p["status"], p["codigo"], p["nome"], p["unidade"], p["area"], p["categoria"], p["prioridade"]]
                      + [v.get(a, 0.0) for a in anos] + [sum(v.get(a, 0.0) for a in anos)])
        for row in ws.iter_rows(min_row=2, min_col=10, max_col=10 + len(anos)):
            for cel in row:
                cel.number_format = "#,##0"
    ws = wb.create_sheet("Alterações")
    _cabecalho(ws, ["Seq", "Rodada", "Camada", "Projeto", "Ano", "De", "Para", "Justificativa", "Por", "Em", "Status"])
    for a in alts:
        ws.append([a["seq"], a["rodada"], nomes_cen.get(a["camada"], a["camada"]), a["projeto"], a["ano"], a["de"], a["para"], a["justificativa"], a["por"], a["em"], a["status"]])
    return Response(_xlsx(wb), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="plano-{cid}.xlsx"'})


@app.post("/api/demo")
def carregar_demo(x_usuario: str | None = Header(None)):
    """Cria um ciclo de exemplo com projetos genéricos (para conhecer a ferramenta)."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "pmo")
        org = B.org(); unis = [x["nome"] for x in org.get("unidades") or []] or ["Unidade A"]
        areas = org.get("areas") or ["Engenharia"]
        ini = int(org.get("ano_base") or 2026) + 1; fim = ini + int(org.get("horizonte_anos") or 5) - 1
        cid = f"c-{ini}-{fim}-demo"
        if c.execute("SELECT 1 FROM ciclos WHERE id=?", (cid,)).fetchone():
            raise HTTPException(409, "O ciclo de exemplo já existe.")
        c.execute("INSERT INTO ciclos(id,nome,ano_inicio,ano_fim,fase,taxa_desconto,criado_em,atualizado_em) VALUES (?,?,?,?,?,?,?,?)",
                  (cid, f"{B.voc()['plano_sigla']} {ini}–{fim} (exemplo)", ini, fim, "preparacao", float(org.get("taxa_desconto_aa") or 0.1), agora(), agora()))
        nomes = ["Renovação de subestação", "Modernização de sistemas de controle", "Reforma de oficina", "Ampliação de pátio", "Troca de cabos de média tensão",
                 "Sistema de gestão da manutenção", "Climatização de ambientes técnicos", "Acessibilidade em pontos de atendimento", "Videomonitoramento analítico",
                 "Recuperação de taludes", "Frota de veículos de apoio", "Automação do controle de acesso", "Eficiência energética", "Data center secundário"]
        rnd = random.Random(7); cats = B.config()["categorias"]
        for i, n in enumerate(nomes):
            pid = _proximo_plano_id(c)
            c.execute("INSERT INTO projetos(ciclo_id,plano_id,codigo,nome,unidade,area,origem,categoria,prioridade,status,criado_em,atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (cid, pid, f"WBS-{ini % 100}-{i + 1:03d}", n, unis[i % len(unis)], areas[i % len(areas)], "importacao", cats[i % len(cats)], 1 + i % 3, "proposto", agora(), agora()))
            tot = rnd.choice([3, 6, 12, 18, 25, 40]) * 1e6
            pesos = [.15, .3, .3, .15, .1][: fim - ini + 1]
            _gravar_valores(c, cid, pid, "base", {ini + k: round(tot * w, -3) for k, w in enumerate(pesos)}, list(range(ini, fim + 1)))
        B.auditar(c, u, "Ciclo de exemplo criado", cid)
        return {"ciclo_id": cid}


B.servir_spa(app)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=os.environ.get("PLANO_HOST", "127.0.0.1"), port=int(os.environ.get("PORT") or 8820),
                root_path=os.environ.get("HUB_ROOT_PATH", ""))
