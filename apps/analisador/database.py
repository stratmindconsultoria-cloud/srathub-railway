"""
Persistência do Analisador de Tendências.

Cada rodada é gravada inteira (snapshot por projeto) a partir da rodada publicada pela
ferramenta de gestão da carteira (PMP) — pelo Data Core do hub ou por arquivo JSON.
Reenviar a mesma rodada substitui o snapshot anterior dela.
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from categorias import DESVIO_KEYS, MESES, MetaRodada, ProjetoSnapshot

import os
DADOS = Path(os.environ.get("ANALISADOR_DADOS", Path(__file__).parent / "dados"))
DB_PATH = DADOS / "analisador.db"
XLSX_DIR = DADOS / "rodadas"

# campos do ProjetoSnapshot que são listas (mensais) e precisam de JSON
_CAMPOS_JSON_LISTA = ["econ_orc_mensal", "econ_real_mensal", "econ_tend_mensal",
                       "fin_orc_mensal", "fin_real_mensal", "fin_tend_mensal",
                       "fis_baseline_mensal", "fis_real_mensal", "fis_tend_mensal",
                       "comp_ped_mensal", "comp_req_mensal"]
_CAMPOS_JSON_DICT = ["econ_desvios", "fin_desvios", "fis_desvios",
                      "econ_orc_anos", "econ_tend_anos", "fin_orc_anos", "fin_tend_anos"]

_COLUNAS_SNAPSHOT = [
    "rodada", "pep", "projeto", "unidade", "nucleo", "vertical_gestora", "tipo_investimento",
    "pep_pai", "diretoria_engenharia", "grupo",
    "econ_orc_ano", "econ_tend_ano", "econ_orc_acum", "econ_real_acum", "econ_delta_acum",
    "econ_desvios", "econ_orc_mensal", "econ_real_mensal", "econ_tend_mensal",
    "fin_orc_ano", "fin_tend_ano", "fin_orc_acum", "fin_real_acum", "fin_delta_acum",
    "fin_desvios", "fin_orc_mensal", "fin_real_mensal", "fin_tend_mensal",
    "econ_orc_anos", "econ_tend_anos", "fin_orc_anos", "fin_tend_anos",
    "econ_orc_qq", "econ_tend_qq", "fin_orc_qq", "fin_tend_qq",
    "motivador", "natureza",
    "fis_peso", "fis_baseline_dez", "fis_baseline_atual", "fis_real_atual",
    "fis_baseline_mensal", "fis_real_mensal", "fis_tend_mensal", "fis_desvios",
    "fin_just_mes", "fin_just_acum", "fin_just_ano",
    "econ_just_mes", "econ_just_acum", "econ_just_ano",
    "fis_just_mes", "fis_just_acum", "fis_just_ano",
    "comp_req_ano", "comp_ped_ano", "comp_sem_ano", "comp_check_ano",
    "comp_req_total", "comp_ped_total", "comp_total_qq", "previsao_estouro",
    "comp_ped_mensal", "comp_req_mensal",
]


def caminho_fonte(rodada: str) -> Path:
    """Cópia bruta do que foi recebido (JSON da rodada publicada pela PMP)."""
    return XLSX_DIR / f"{rodada}.json"


SCHEMA = """
CREATE TABLE IF NOT EXISTS tendencias (
    rodada TEXT PRIMARY KEY,
    mes_analise TEXT,
    versao_declarada TEXT,
    status TEXT NOT NULL DEFAULT 'PRÉVIA',
    data_import TEXT NOT NULL,
    arquivo_nome TEXT,
    linhas_lidas INTEGER,
    linhas_validas INTEGER,
    avisos TEXT,
    verticais_ultimo_upload TEXT,
    ano INTEGER
);

CREATE TABLE IF NOT EXISTS snapshot_projetos (
    rodada TEXT NOT NULL, pep TEXT NOT NULL,
    projeto TEXT, unidade TEXT, nucleo TEXT, vertical_gestora TEXT, tipo_investimento TEXT,
    pep_pai TEXT, diretoria_engenharia TEXT, grupo TEXT,

    econ_orc_ano REAL, econ_tend_ano REAL, econ_orc_acum REAL,
    econ_real_acum REAL, econ_delta_acum REAL, econ_desvios TEXT,
    econ_orc_mensal TEXT, econ_real_mensal TEXT, econ_tend_mensal TEXT,

    fin_orc_ano REAL, fin_tend_ano REAL, fin_orc_acum REAL,
    fin_real_acum REAL, fin_delta_acum REAL, fin_desvios TEXT,
    fin_orc_mensal TEXT, fin_real_mensal TEXT, fin_tend_mensal TEXT,

    econ_orc_anos TEXT, econ_tend_anos TEXT, fin_orc_anos TEXT, fin_tend_anos TEXT,
    econ_orc_qq REAL, econ_tend_qq REAL, fin_orc_qq REAL, fin_tend_qq REAL,
    motivador TEXT, natureza TEXT,

    fis_peso REAL, fis_baseline_dez REAL, fis_baseline_atual REAL, fis_real_atual REAL,
    fis_baseline_mensal TEXT, fis_real_mensal TEXT, fis_tend_mensal TEXT, fis_desvios TEXT,

    fin_just_mes TEXT, fin_just_acum TEXT, fin_just_ano TEXT,
    econ_just_mes TEXT, econ_just_acum TEXT, econ_just_ano TEXT,
    fis_just_mes TEXT, fis_just_acum TEXT, fis_just_ano TEXT,

    comp_req_ano REAL, comp_ped_ano REAL, comp_sem_ano REAL, comp_check_ano REAL,
    comp_req_total REAL, comp_ped_total REAL, comp_total_qq REAL, previsao_estouro REAL,
    comp_ped_mensal TEXT, comp_req_mensal TEXT,

    PRIMARY KEY (rodada, pep),
    FOREIGN KEY (rodada) REFERENCES tendencias(rodada) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_snapshot_pep ON snapshot_projetos(pep);
CREATE INDEX IF NOT EXISTS idx_snapshot_unidade ON snapshot_projetos(unidade);
CREATE INDEX IF NOT EXISTS idx_snapshot_vertical ON snapshot_projetos(vertical_gestora);



"""


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # migração leve: bases criadas antes desta versão não tinham essas colunas
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(snapshot_projetos)")}
        faltando = [c for c in _COLUNAS_SNAPSHOT if c not in cols]
        for c in faltando:
            tipo = "TEXT" if c in (_CAMPOS_JSON_LISTA + _CAMPOS_JSON_DICT + [
                "diretoria_engenharia", "grupo", "motivador", "natureza"]) else "REAL"
            conn.execute(f"ALTER TABLE snapshot_projetos ADD COLUMN {c} {tipo}")
        tcols = {r["name"] for r in conn.execute("PRAGMA table_info(tendencias)")}
        if "verticais_ultimo_upload" not in tcols:
            conn.execute("ALTER TABLE tendencias ADD COLUMN verticais_ultimo_upload TEXT")
        if "ano" not in tcols:
            conn.execute("ALTER TABLE tendencias ADD COLUMN ano INTEGER")


def _linha_para_tupla(rodada: str, p: ProjetoSnapshot) -> tuple:
    d = p.as_row()
    vals = []
    for c in _COLUNAS_SNAPSHOT:
        if c == "rodada":
            vals.append(rodada)
            continue
        v = d.get(c)
        if c in _CAMPOS_JSON_LISTA or c in _CAMPOS_JSON_DICT:
            v = json.dumps(v if v is not None else ({} if c in _CAMPOS_JSON_DICT else []), ensure_ascii=False)
        vals.append(v)
    return tuple(vals)


def salvar_rodada(rodada: str, arquivo_nome: str, meta: MetaRodada,
                   projetos: list[ProjetoSnapshot], fonte_bytes: bytes,
                   status: str = "PRÉVIA", substituir_tudo: bool = True) -> dict:
    """Grava os projetos do upload, substituindo só o escopo de Vertical
    Gestora presente no arquivo — preserva o resto da rodada já carregado.

    Retorna um resumo: {"verticais_arquivo": [...], "verticais_rodada": [...]}
    """
    verticais_arquivo = sorted({p.vertical_gestora for p in projetos if p.vertical_gestora})

    with get_conn() as conn:
        existe = conn.execute("SELECT 1 FROM tendencias WHERE rodada=?", (rodada,)).fetchone()

        if substituir_tudo:  # rodada publicada inteira pela ferramenta de origem
            conn.execute("DELETE FROM snapshot_projetos WHERE rodada=?", (rodada,))
        elif verticais_arquivo:
            placeholders = ",".join("?" * len(verticais_arquivo))
            clause = f"vertical_gestora IN ({placeholders})"
            params = list(verticais_arquivo)
            # projetos sem Vertical Gestora preenchida também precisam ser
            # limpos quando o arquivo atual tem algum — senão um reenvio do
            # mesmo arquivo colide em PK (rodada, pep) nesses projetos, que
            # nunca são alcançados pelo IN(...) acima.
            if any(p.vertical_gestora is None for p in projetos):
                clause = f"({clause} OR vertical_gestora IS NULL)"
            conn.execute(f"DELETE FROM snapshot_projetos WHERE rodada=? AND {clause}", (rodada, *params))
        else:
            # arquivo sem Vertical Gestora identificável em nenhuma linha — não deveria
            # acontecer com o template padrão, mas por segurança substitui a rodada toda
            # em vez de silenciosamente não apagar nada.
            conn.execute("DELETE FROM snapshot_projetos WHERE rodada=?", (rodada,))

        agora = datetime.datetime.now().isoformat(timespec="seconds")
        if existe:
            conn.execute(
                """UPDATE tendencias SET mes_analise=?, versao_declarada=?, status=?,
                   data_import=?, arquivo_nome=?, linhas_lidas=?, linhas_validas=?,
                   avisos=?, verticais_ultimo_upload=?, ano=? WHERE rodada=?""",
                (meta.mes_analise, meta.versao_declarada, status, agora, arquivo_nome,
                 meta.linhas_lidas, meta.linhas_validas, json.dumps(meta.avisos, ensure_ascii=False),
                 json.dumps(verticais_arquivo, ensure_ascii=False), meta.ano, rodada),
            )
        else:
            conn.execute(
                """INSERT INTO tendencias
                   (rodada, mes_analise, versao_declarada, status, data_import, arquivo_nome,
                    linhas_lidas, linhas_validas, avisos, verticais_ultimo_upload, ano)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rodada, meta.mes_analise, meta.versao_declarada, status, agora, arquivo_nome,
                 meta.linhas_lidas, meta.linhas_validas, json.dumps(meta.avisos, ensure_ascii=False),
                 json.dumps(verticais_arquivo, ensure_ascii=False), meta.ano),
            )

        rows = [_linha_para_tupla(rodada, p) for p in projetos]
        placeholders = ",".join("?" * len(_COLUNAS_SNAPSHOT))
        # Sempre explicita as colunas. Em bancos migrados por ALTER TABLE, as novas
        # colunas ficam fisicamente no fim da tabela e a ordem do schema pode divergir
        # de _COLUNAS_SNAPSHOT; INSERT ... VALUES sem lista de colunas corromperia
        # silenciosamente os campos.
        col_sql = ",".join(_COLUNAS_SNAPSHOT)
        conn.executemany(f"INSERT INTO snapshot_projetos ({col_sql}) VALUES ({placeholders})", rows)

        verticais_rodada = sorted({
            r["vertical_gestora"] for r in
            conn.execute("SELECT DISTINCT vertical_gestora FROM snapshot_projetos WHERE rodada=?", (rodada,))
            if r["vertical_gestora"]
        })

    XLSX_DIR.mkdir(parents=True, exist_ok=True)
    caminho_fonte(rodada).write_bytes(fonte_bytes)

    return {"verticais_arquivo": verticais_arquivo, "verticais_rodada": verticais_rodada}


def rodada_sort_key(rodada: str) -> tuple:
    """Ordem cronológica: AAAA-MM (rodadas da PMP) ou T06 < T07 < … (rótulos numerados)."""
    m = re.match(r"^(\d{4})-(\d{2})$", rodada)
    if m:
        return (int(m.group(1)) * 100 + int(m.group(2)), rodada)
    m = re.match(r"^T(\d+)$", rodada)
    return (int(m.group(1)) if m else 0, rodada)


def listar_rodadas() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM tendencias").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["verticais_ultimo_upload"] = json.loads(d.get("verticais_ultimo_upload") or "[]")
            out.append(d)
        return sorted(out, key=lambda r: rodada_sort_key(r["rodada"]))


def tendencia_existe(rodada: str) -> bool:
    """True somente quando a fonte Tendência daquela rodada foi efetivamente carregada."""
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM tendencias WHERE rodada=? LIMIT 1", (rodada,)).fetchone() is not None


def excluir_rodada(rodada: str) -> None:
    """Exclui a rodada (snapshot e cópia da fonte)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM snapshot_projetos WHERE rodada = ?", (rodada,))
        conn.execute("DELETE FROM tendencias WHERE rodada = ?", (rodada,))
    caminho_fonte(rodada).unlink(missing_ok=True)


def todas_verticais_conhecidas() -> list[str]:
    """União de todas as Verticais Gestoras já vistas em qualquer rodada —
    usada pra saber o que 'falta' carregar numa rodada parcial."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT vertical_gestora FROM snapshot_projetos WHERE vertical_gestora IS NOT NULL"
        ).fetchall()
        return sorted({r["vertical_gestora"] for r in rows})


def verticais_da_rodada(rodada: str) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT vertical_gestora FROM snapshot_projetos WHERE rodada=? AND vertical_gestora IS NOT NULL",
            (rodada,),
        ).fetchall()
        return sorted({r["vertical_gestora"] for r in rows})


def projetos_da_rodada(rodada: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM snapshot_projetos WHERE rodada = ?", (rodada,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for c in _CAMPOS_JSON_DICT:
                d[c] = json.loads(d.get(c) or "{}")
            for c in _CAMPOS_JSON_LISTA:
                v = d.get(c)
                d[c] = json.loads(v) if v else [0.0] * 12
            out.append(d)
        return out


def totais_rodada(rodada: str) -> dict:
    """Totais agregados de portfólio para relatório de validação do import."""
    projetos = projetos_da_rodada(rodada)
    tot = {
        "econ_orc_ano": 0.0, "econ_tend_ano": 0.0, "econ_real_acum": 0.0,
        "fin_orc_ano": 0.0, "fin_tend_ano": 0.0, "fin_real_acum": 0.0,
        "fin_desvios": {k: 0.0 for k in DESVIO_KEYS},
        "n_projetos": len(projetos),
        "n_com_peso_fisico": 0,
    }
    for p in projetos:
        for k in ("econ_orc_ano", "econ_tend_ano", "econ_real_acum",
                   "fin_orc_ano", "fin_tend_ano", "fin_real_acum"):
            tot[k] += p[k] or 0.0
        for k, v in (p["fin_desvios"] or {}).items():
            tot["fin_desvios"][k] = tot["fin_desvios"].get(k, 0.0) + v
        if p["fis_peso"]:
            tot["n_com_peso_fisico"] += 1
    return tot
