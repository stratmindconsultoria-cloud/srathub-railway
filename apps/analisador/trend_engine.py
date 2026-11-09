"""
Motor de tendência: séries e deltas entre rodadas, em três níveis
(portfólio, Unidade/Vertical Gestora/Diretoria/Grupo, PEP) e três
dimensões (Econômico, Financeiro, Físico) — mais confrontos de
consistência, análise de compromissos e insights automáticos.

Consome só o que `database.py` expõe (snapshots já processados). Não
sabe nada sobre xlsx — por isso continua funcionando sem mudanças no
dia em que a ingestão passar a ser direto do banco do SaaS.
"""
from __future__ import annotations

import database as db
from categorias import DESVIO_KEYS, DESVIO_LABELS, MESES, MES_IDX

INDICADORES_PORTFOLIO = [
    ("econ_orc_ano", "Econ. Orçado (ano)"),
    ("econ_tend_ano", "Econ. Tendência (ano)"),
    ("econ_real_acum", "Econ. Realizado Acum."),
    ("fin_orc_ano", "Fin. Orçado (ano)"),
    ("fin_tend_ano", "Fin. Tendência (ano)"),
    ("fin_real_acum", "Fin. Realizado Acum."),
]

# dimensões disponíveis pro filtro dinâmico do cabeçalho
FILTRO_CAMPOS = {
    "unidade": "Unidade",
    "vertical_gestora": "Área",
    "diretoria_engenharia": "Gerente",
    "grupo": "Grupo",
}


def _pct(atual: float, anterior: float) -> float | None:
    if not anterior:
        return None
    return (atual - anterior) / abs(anterior) * 100


def _aplicar_filtro_geral(projetos: list[dict], filtro_geral: dict | None) -> list[dict]:
    if not filtro_geral or not filtro_geral.get("valores"):
        return projetos
    campo = filtro_geral.get("campo")
    if campo not in FILTRO_CAMPOS:
        return projetos
    valores = set(filtro_geral["valores"])
    return [p for p in projetos if p.get(campo) in valores]


def _projetos(rodada: str, filtro_geral: dict | None = None) -> list[dict]:
    return _aplicar_filtro_geral(db.projetos_da_rodada(rodada), filtro_geral)


def valores_disponiveis(campo: str) -> list[str]:
    """Valores distintos de um campo de filtro, olhando todas as rodadas —
    alimenta os checkboxes do filtro dinâmico do cabeçalho."""
    if campo not in FILTRO_CAMPOS:
        return []
    vistos: set[str] = set()
    for r in db.listar_rodadas():
        for p in db.projetos_da_rodada(r["rodada"]):
            v = p.get(campo)
            if v:
                vistos.add(v)
    return sorted(vistos)


def _agg(projetos: list[dict], filtro=None) -> dict:
    if filtro:
        projetos = [p for p in projetos if filtro(p)]
    out = {k: 0.0 for k, _ in INDICADORES_PORTFOLIO}
    out["fin_desvios"] = {k: 0.0 for k in DESVIO_KEYS}
    out["econ_desvios"] = {k: 0.0 for k in DESVIO_KEYS}
    out["n_projetos"] = len(projetos)
    out["n_com_peso_fisico"] = 0
    for p in projetos:
        for k, _ in INDICADORES_PORTFOLIO:
            out[k] += p[k] or 0.0
        for k, v in (p["fin_desvios"] or {}).items():
            out["fin_desvios"][k] += v
        for k, v in (p["econ_desvios"] or {}).items():
            out["econ_desvios"][k] += v
        if p["fis_peso"]:
            out["n_com_peso_fisico"] += 1
    return out


def _resolver_par(rodada_atual: str, rodada_anterior: str | None):
    rodadas = db.listar_rodadas()
    idx = {r["rodada"]: i for i, r in enumerate(rodadas)}
    if rodada_atual not in idx:
        raise ValueError(f"Rodada {rodada_atual} não encontrada")
    i = idx[rodada_atual]
    if rodada_anterior is None:
        rodada_anterior = rodadas[i - 1]["rodada"] if i > 0 else None
    elif rodada_anterior not in idx:
        rodada_anterior = None
    return rodada_atual, rodada_anterior


# ── nível portfólio ──────────────────────────────────────────────────────
def series_portfolio(filtro_geral: dict | None = None) -> list[dict]:
    rodadas = db.listar_rodadas()
    out = []
    for r in rodadas:
        agg = _agg(_projetos(r["rodada"], filtro_geral))
        agg["rodada"] = r["rodada"]
        agg["mes_analise"] = r["mes_analise"]
        agg["status"] = r["status"]
        out.append(agg)
    return out


def delta_portfolio(rodada_atual: str, rodada_anterior: str | None = None,
                     filtro_geral: dict | None = None) -> dict:
    serie = series_portfolio(filtro_geral)
    idx = {s["rodada"]: i for i, s in enumerate(serie)}
    rodada_atual, rodada_anterior = _resolver_par(rodada_atual, rodada_anterior)
    atual = serie[idx[rodada_atual]]
    anterior = serie[idx[rodada_anterior]] if rodada_anterior else None

    indicadores = {}
    for k, label in INDICADORES_PORTFOLIO:
        a = atual[k]
        b = anterior[k] if anterior else None
        indicadores[k] = {
            "label": label, "atual": a, "anterior": b,
            "delta_abs": (a - b) if b is not None else None,
            "delta_pct": _pct(a, b) if b is not None else None,
        }
    desvios = {}
    for dim in ("fin", "econ"):
        desvios[dim] = {}
        for k in DESVIO_KEYS:
            a = atual[f"{dim}_desvios"].get(k, 0.0)
            b = anterior[f"{dim}_desvios"].get(k, 0.0) if anterior else None
            desvios[dim][k] = {"atual": a, "anterior": b, "delta_abs": (a - b) if b is not None else None}
    return {
        "rodada_atual": rodada_atual, "rodada_anterior": rodada_anterior,
        "indicadores": indicadores, "desvios": desvios, "fin_desvios": desvios["fin"],
        "n_projetos": {"atual": atual["n_projetos"], "anterior": anterior["n_projetos"] if anterior else None},
        "n_com_peso_fisico": {"atual": atual["n_com_peso_fisico"], "anterior": anterior["n_com_peso_fisico"] if anterior else None},
        "serie": serie,
    }


# ── categorias de desvio, matriz rodada x categoria ─────────────────────
def categoria_serie(dimensao: str = "fin", filtro_geral: dict | None = None) -> list[dict]:
    rodadas = db.listar_rodadas()
    key = f"{dimensao}_desvios"
    out = []
    for r in rodadas:
        tot = {k: 0.0 for k in DESVIO_KEYS}
        for p in _projetos(r["rodada"], filtro_geral):
            for k, v in (p[key] or {}).items():
                tot[k] += v
        out.append({"rodada": r["rodada"], "cats": tot})
    return out


# ── nível Unidade/Vertical/Diretoria/Grupo ───────────────────────────────
def delta_unidades(rodada_atual: str, rodada_anterior: str | None = None,
                    indicador: str = "fin_tend_ano", agrupar_por: str = "unidade",
                    filtro_geral: dict | None = None) -> list[dict]:
    rodada_atual, rodada_anterior = _resolver_par(rodada_atual, rodada_anterior)
    proj_atual = _projetos(rodada_atual, filtro_geral)
    proj_anterior = _projetos(rodada_anterior, filtro_geral) if rodada_anterior else []

    grupos = sorted({p[agrupar_por] for p in proj_atual if p.get(agrupar_por)})
    out = []
    for g in grupos:
        a = _agg(proj_atual, filtro=lambda p, g=g: p.get(agrupar_por) == g)
        b = _agg(proj_anterior, filtro=lambda p, g=g: p.get(agrupar_por) == g) if proj_anterior else None
        av, bv = a[indicador], (b[indicador] if b else None)
        out.append({
            "unidade": g,
            "atual": av, "anterior": bv,
            "delta_abs": (av - bv) if bv is not None else None,
            "delta_pct": _pct(av, bv) if bv is not None else None,
            "n_projetos": a["n_projetos"],
        })
    out.sort(key=lambda x: abs(x["delta_abs"]) if x["delta_abs"] is not None else -1, reverse=True)
    return out


# ── nível PEP ─────────────────────────────────────────────────────────────
def series_pep(pep: str) -> list[dict]:
    rodadas = db.listar_rodadas()
    out = []
    for r in rodadas:
        projetos = db.projetos_da_rodada(r["rodada"])
        match = next((p for p in projetos if p["pep"] == pep), None)
        if match:
            match = dict(match)
            match["rodada"] = r["rodada"]
            out.append(match)
    return out


def delta_peps(rodada_atual: str, rodada_anterior: str | None = None,
               indicador: str = "fin_tend_ano", top_n: int = 20,
               filtro_geral: dict | None = None) -> list[dict]:
    rodada_atual, rodada_anterior = _resolver_par(rodada_atual, rodada_anterior)
    if rodada_anterior is None:
        return []

    proj_atual = {p["pep"]: p for p in _projetos(rodada_atual, filtro_geral)}
    proj_anterior = {p["pep"]: p for p in _projetos(rodada_anterior, filtro_geral)}

    out = []
    for pep, a in proj_atual.items():
        b = proj_anterior.get(pep)
        av = a[indicador] or 0.0
        bv = (b[indicador] or 0.0) if b else None
        delta = (av - bv) if bv is not None else None
        if delta is None or abs(delta) < 1000:
            continue
        out.append({
            "pep": pep, "projeto": a["projeto"], "unidade": a["unidade"],
            "atual": av, "anterior": bv, "delta_abs": delta,
            "delta_pct": _pct(av, bv) if bv else None, "novo": b is None,
        })
    for pep in set(proj_anterior) - set(proj_atual):
        b = proj_anterior[pep]
        out.append({
            "pep": pep, "projeto": b["projeto"], "unidade": b["unidade"],
            "atual": None, "anterior": b[indicador] or 0.0,
            "delta_abs": -(b[indicador] or 0.0), "delta_pct": None, "saiu_da_base": True,
        })
    out.sort(key=lambda x: abs(x["delta_abs"]), reverse=True)
    return out[:top_n]


def delta_peps_categoria(rodada_atual: str, rodada_anterior: str | None = None,
                          categoria: str = "postergado", dimensao: str = "fin",
                          top_n: int = 20, filtro_geral: dict | None = None) -> list[dict]:
    """Maiores movimentos numa categoria de desvio específica (não o total)."""
    rodada_atual, rodada_anterior = _resolver_par(rodada_atual, rodada_anterior)
    if rodada_anterior is None:
        return []
    key = f"{dimensao}_desvios"
    proj_atual = {p["pep"]: p for p in _projetos(rodada_atual, filtro_geral)}
    proj_anterior = {p["pep"]: p for p in _projetos(rodada_anterior, filtro_geral)}
    out = []
    for pep, a in proj_atual.items():
        b = proj_anterior.get(pep)
        av = (a[key] or {}).get(categoria, 0.0)
        bv = (b[key] or {}).get(categoria, 0.0) if b else None
        delta = (av - bv) if bv is not None else (av if av else None)
        if not delta or abs(delta) < 1000:
            continue
        out.append({"pep": pep, "projeto": a["projeto"], "unidade": a["unidade"],
                    "atual": av, "anterior": bv, "delta_abs": delta, "novo": b is None})
    out.sort(key=lambda x: abs(x["delta_abs"]), reverse=True)
    return out[:top_n]


def categoria_consecutiva(pep: str, categoria: str = "postergado", dimensao: str = "fin") -> int:
    """Nº de rodadas mais recentes, seguidas, em que a categoria teve valor != 0."""
    serie = series_pep(pep)
    count = 0
    for snap in reversed(serie):
        v = (snap[f"{dimensao}_desvios"] or {}).get(categoria, 0.0)
        if v:
            count += 1
        else:
            break
    return count


def peps_categoria_consecutiva(rodada_atual: str, categoria: str = "postergado", dimensao: str = "fin",
                                min_rodadas: int = 2, top_n: int = 15,
                                filtro_geral: dict | None = None) -> list[dict]:
    """Atenção: os valores de categoria são a leitura acumulada do ano em cada
    rodada, não eventos pontuais — tendem a persistir até a causa ser
    resolvida. Lista ordenada pelo maior valor absoluto, limitada a top_n."""
    key = f"{dimensao}_desvios"
    out = []
    for p in _projetos(rodada_atual, filtro_geral):
        v = (p[key] or {}).get(categoria, 0.0)
        if not v:
            continue
        n = categoria_consecutiva(p["pep"], categoria, dimensao)
        if n >= min_rodadas:
            out.append({"pep": p["pep"], "projeto": p["projeto"], "unidade": p["unidade"],
                        "rodadas_consecutivas": n, "valor_atual": v})
    out.sort(key=lambda x: abs(x["valor_atual"]), reverse=True)
    return out[:top_n]


# compat com chamadas antigas (usadas antes da generalização por categoria)
def postergacao_consecutiva(pep: str, dimensao: str = "fin") -> int:
    return categoria_consecutiva(pep, "postergado", dimensao)


def peps_postergando_consecutivo(rodada_atual: str, dimensao: str = "fin",
                                  min_rodadas: int = 2, top_n: int = 15) -> list[dict]:
    return peps_categoria_consecutiva(rodada_atual, "postergado", dimensao, min_rodadas, top_n)


# ── dimensão física ───────────────────────────────────────────────────────
def _fisico_agg(projetos: list[dict], filtro=None) -> dict:
    if filtro:
        projetos = [p for p in projetos if filtro(p)]
    sel = [p for p in projetos if p.get("fis_peso") and p.get("fis_baseline_dez")]
    sw = sum(p["fis_peso"] for p in sel)
    if not sw:
        return {"baseline_pct": None, "real_pct": None, "n_com_peso": 0, "n_projetos": len(projetos)}
    ref = sum(p["fis_peso"] * p["fis_baseline_dez"] for p in sel) / sw
    base_w = sum(p["fis_peso"] * (p["fis_baseline_atual"] or 0) for p in sel) / sw
    real_w = sum(p["fis_peso"] * (p["fis_real_atual"] or 0) for p in sel) / sw
    return {
        "baseline_pct": (base_w / ref * 100) if ref else None,
        "real_pct": (real_w / ref * 100) if ref else None,
        "n_com_peso": len(sel), "n_projetos": len(projetos),
    }


def series_fisico(filtro_geral: dict | None = None) -> list[dict]:
    rodadas = db.listar_rodadas()
    out = []
    for r in rodadas:
        agg = _fisico_agg(_projetos(r["rodada"], filtro_geral))
        agg["rodada"] = r["rodada"]
        out.append(agg)
    return out


def delta_fisico(rodada_atual: str, rodada_anterior: str | None = None,
                  filtro_geral: dict | None = None) -> dict:
    serie = series_fisico(filtro_geral)
    idx = {s["rodada"]: i for i, s in enumerate(serie)}
    rodada_atual, rodada_anterior = _resolver_par(rodada_atual, rodada_anterior)
    atual = serie[idx[rodada_atual]]
    anterior = serie[idx[rodada_anterior]] if rodada_anterior else None
    return {"rodada_atual": rodada_atual, "rodada_anterior": rodada_anterior,
            "atual": atual, "anterior": anterior, "serie": serie}


def fisico_por_unidade(rodada_atual: str, rodada_anterior: str | None = None,
                        agrupar_por: str = "unidade", filtro_geral: dict | None = None) -> list[dict]:
    rodada_atual, rodada_anterior = _resolver_par(rodada_atual, rodada_anterior)
    proj_atual = _projetos(rodada_atual, filtro_geral)
    proj_anterior = _projetos(rodada_anterior, filtro_geral) if rodada_anterior else []
    grupos = sorted({p[agrupar_por] for p in proj_atual if p.get(agrupar_por) and p.get("fis_peso")})
    out = []
    for g in grupos:
        a = _fisico_agg(proj_atual, lambda p, g=g: p.get(agrupar_por) == g)
        b = _fisico_agg(proj_anterior, lambda p, g=g: p.get(agrupar_por) == g) if proj_anterior else None
        delta = None
        if b and a["real_pct"] is not None and b["real_pct"] is not None:
            delta = a["real_pct"] - b["real_pct"]
        out.append({"unidade": g, "baseline_pct": a["baseline_pct"], "real_pct": a["real_pct"],
                    "n_com_peso": a["n_com_peso"], "delta_pp": delta})
    out.sort(key=lambda x: x["n_com_peso"], reverse=True)
    return out


# ── compromissos (aba COMPROMISSO — visão Econômica) ──────────────────────
def _compromisso_agg(projetos: list[dict]) -> dict:
    sel = [p for p in projetos if p.get("comp_ped_ano") is not None]
    return {
        "comp_req_ano": sum(p.get("comp_req_ano") or 0 for p in sel),
        "comp_ped_ano": sum(p.get("comp_ped_ano") or 0 for p in sel),
        "comp_sem_ano": sum(p.get("comp_sem_ano") or 0 for p in sel),
        "n_com_dado": len(sel),
        "n_check_divergente": sum(1 for p in sel if (p.get("comp_check_ano") or 0) != 0),
        "n_previsao_estouro": sum(1 for p in sel if (p.get("previsao_estouro") or 0) != 0),
    }


def series_compromisso(filtro_geral: dict | None = None) -> list[dict]:
    rodadas = db.listar_rodadas()
    out = []
    for r in rodadas:
        agg = _compromisso_agg(_projetos(r["rodada"], filtro_geral))
        agg["rodada"] = r["rodada"]
        out.append(agg)
    return out


def delta_compromisso(rodada_atual: str, rodada_anterior: str | None = None,
                       filtro_geral: dict | None = None) -> dict:
    serie = series_compromisso(filtro_geral)
    idx = {s["rodada"]: i for i, s in enumerate(serie)}
    rodada_atual, rodada_anterior = _resolver_par(rodada_atual, rodada_anterior)
    atual = serie[idx[rodada_atual]]
    anterior = serie[idx[rodada_anterior]] if rodada_anterior else None
    campos = {}
    for k in ("comp_req_ano", "comp_ped_ano", "comp_sem_ano"):
        a = atual[k]
        b = anterior[k] if anterior else None
        campos[k] = {"atual": a, "anterior": b, "delta_abs": (a - b) if b is not None else None}
    return {"rodada_atual": rodada_atual, "rodada_anterior": rodada_anterior, "campos": campos,
            "n_com_dado": atual["n_com_dado"], "n_check_divergente": atual["n_check_divergente"],
            "n_previsao_estouro": atual["n_previsao_estouro"], "serie": serie}


def sem_compromisso_lista(rodada: str, filtro_geral: dict | None = None, top_n: int = 15) -> list[dict]:
    projetos = _projetos(rodada, filtro_geral)
    out = [p for p in projetos if (p.get("comp_sem_ano") or 0) > 1000]
    out.sort(key=lambda p: p.get("comp_sem_ano") or 0, reverse=True)
    return [{"pep": p["pep"], "projeto": p["projeto"], "unidade": p["unidade"], "valor": p["comp_sem_ano"]}
            for p in out[:top_n]]


# ── confronto de consistência (meses fechados não deveriam mudar) ────────
def confronto_meses_fechados(rodada_atual: str, rodada_anterior: str | None = None,
                              dimensao: str = "fin", filtro_geral: dict | None = None) -> dict:
    """Compara Realizado e Orçado mês a mês, PEP a PEP, nos meses que já
    estavam fechados nas DUAS rodadas (mês <= mês de análise da rodada mais
    antiga do par). Separa divergência real (mesmo PEP, valor mudou) de
    efeito de escopo (PEP saiu/entrou da base) — são coisas diferentes."""
    rodada_atual, rodada_anterior = _resolver_par(rodada_atual, rodada_anterior)
    vazio = {"rodada_atual": rodada_atual, "rodada_anterior": rodada_anterior, "mes_limite": None,
             "divergencias": [], "n_divergencias": 0,
             "efeito_escopo": {"saidos": [], "entraram": []}, "ok": True, "aviso": None}
    if not rodada_anterior:
        vazio["aviso"] = f"{rodada_atual} é a primeira rodada — nada para confrontar ainda."
        return vazio

    meta = {r["rodada"]: r for r in db.listar_rodadas()}
    mi_atual = MES_IDX.get(meta[rodada_atual]["mes_analise"])
    mi_ant = MES_IDX.get(meta[rodada_anterior]["mes_analise"])
    if not mi_atual or not mi_ant:
        vazio["aviso"] = "Mês de análise não identificado em uma das rodadas — confronto não calculado."
        return vazio
    mes_limite = min(mi_atual, mi_ant)
    if mes_limite < 1:
        vazio["aviso"] = "Nenhum mês fechado nas duas rodadas ainda."
        return vazio

    proj_atual = {p["pep"]: p for p in _projetos(rodada_atual, filtro_geral)}
    proj_anterior = {p["pep"]: p for p in _projetos(rodada_anterior, filtro_geral)}
    comuns = set(proj_atual) & set(proj_anterior)

    campo_real, campo_orc = f"{dimensao}_real_mensal", f"{dimensao}_orc_mensal"
    divergencias = []
    for pep in comuns:
        a, b = proj_atual[pep], proj_anterior[pep]
        va_arr, vb_arr = a.get(campo_real) or [0.0] * 12, b.get(campo_real) or [0.0] * 12
        oa_arr, ob_arr = a.get(campo_orc) or [0.0] * 12, b.get(campo_orc) or [0.0] * 12
        for i in range(mes_limite):
            for arr_a, arr_b, label in [(va_arr, vb_arr, "Realizado"), (oa_arr, ob_arr, "Orçado")]:
                va, vb = arr_a[i], arr_b[i]
                if abs(va - vb) > 1000:
                    divergencias.append({
                        "pep": pep, "projeto": a["projeto"], "mes": MESES[i], "campo": label,
                        "valor_anterior": vb, "valor_atual": va, "delta": va - vb,
                    })
    divergencias.sort(key=lambda d: abs(d["delta"]), reverse=True)

    saidos = sorted(set(proj_anterior) - set(proj_atual))
    entraram = sorted(set(proj_atual) - set(proj_anterior))

    return {
        "rodada_atual": rodada_atual, "rodada_anterior": rodada_anterior, "mes_limite": MESES[mes_limite - 1],
        "divergencias": divergencias[:50], "n_divergencias": len(divergencias),
        "efeito_escopo": {
            "saidos": [{"pep": p, "projeto": proj_anterior[p]["projeto"]} for p in saidos],
            "entraram": [{"pep": p, "projeto": proj_atual[p]["projeto"]} for p in entraram],
        },
        "ok": len(divergencias) == 0, "aviso": None,
    }


# ── insights automáticos ─────────────────────────────────────────────────
def gerar_insights(rodada_atual: str, rodada_anterior: str | None = None,
                    filtro_geral: dict | None = None) -> list[str]:
    rodada_atual, rodada_anterior = _resolver_par(rodada_atual, rodada_anterior)
    if not rodada_anterior:
        return [f"{rodada_atual} é a primeira rodada carregada — carregue outra rodada para ver comparação e tendência."]

    ins: list[str] = []
    dp = delta_portfolio(rodada_atual, rodada_anterior, filtro_geral)

    for k, verbo in [("fin_tend_ano", "Tendência Financeira"), ("econ_tend_ano", "Tendência Econômica")]:
        ind = dp["indicadores"][k]
        if ind["delta_abs"] is not None and abs(ind["delta_abs"]) > 5e5:
            direcao = "caiu" if ind["delta_abs"] < 0 else "subiu"
            ins.append(
                f"A {verbo} {direcao} R$ {abs(ind['delta_abs'])/1e6:.1f} Mi "
                f"({ind['delta_pct']:+.1f}%) de {rodada_anterior} para {rodada_atual} "
                f"(R$ {ind['anterior']/1e6:.1f} Mi → R$ {ind['atual']/1e6:.1f} Mi)."
            )

    for dim, label in [("fin", "financeiro"), ("econ", "econômico")]:
        cats = dp["desvios"][dim]
        candidatas = [(k, v) for k, v in cats.items() if v["delta_abs"] is not None]
        if candidatas:
            k, v = max(candidatas, key=lambda kv: abs(kv[1]["delta_abs"]))
            if abs(v["delta_abs"]) > 3e5:
                ins.append(
                    f"No desvio {label}, a categoria que mais mudou foi "
                    f"'{DESVIO_LABELS.get(k, k)}': {v['delta_abs']/1e6:+.1f} Mi."
                )

    du = delta_unidades(rodada_atual, rodada_anterior, filtro_geral=filtro_geral)
    if du and du[0]["delta_abs"] is not None and abs(du[0]["delta_abs"]) > 3e5:
        top = du[0]
        ins.append(
            f"A unidade com maior variação de Tendência Financeira foi "
            f"{top['unidade']}: {top['delta_abs']/1e6:+.1f} Mi ({top['delta_pct']:+.1f}%)."
        )

    tp = delta_peps(rodada_atual, rodada_anterior, top_n=1, filtro_geral=filtro_geral)
    if tp:
        p = tp[0]
        if p.get("saiu_da_base"):
            ins.append(f"O projeto {p['projeto']} ({p['pep']}) saiu da base entre {rodada_anterior} e {rodada_atual}.")
        else:
            ins.append(
                f"O projeto com maior variação individual foi {p['projeto']} "
                f"({p['pep']}): {p['delta_abs']/1e6:+.1f} Mi."
            )

    todos = delta_peps(rodada_atual, rodada_anterior, top_n=10_000, filtro_geral=filtro_geral)
    novos = sum(1 for p in todos if p.get("novo"))
    saidos = sum(1 for p in todos if p.get("saiu_da_base"))
    if novos or saidos:
        ins.append(f"{novos} projeto(s) novo(s) na base e {saidos} saíram entre {rodada_anterior} e {rodada_atual}.")

    df = delta_fisico(rodada_atual, rodada_anterior, filtro_geral)
    if df["anterior"] and df["atual"]["real_pct"] is not None and df["anterior"]["real_pct"] is not None:
        d = df["atual"]["real_pct"] - df["anterior"]["real_pct"]
        ins.append(
            f"O avanço físico do portfólio passou de {df['anterior']['real_pct']:.1f}% "
            f"para {df['atual']['real_pct']:.1f}% ({d:+.1f} p.p.)."
        )
    if df["anterior"] and df["atual"]["n_com_peso"] != df["anterior"]["n_com_peso"]:
        ins.append(
            f"A cobertura de Peso Físico passou de {df['anterior']['n_com_peso']} "
            f"para {df['atual']['n_com_peso']} projetos rastreados."
        )

    cf = confronto_meses_fechados(rodada_atual, rodada_anterior, "fin", filtro_geral)
    if cf["mes_limite"] and cf["n_divergencias"] > 0:
        ins.append(
            f"⚠ Confronto de consistência: {cf['n_divergencias']} divergência(s) de Realizado/Orçado "
            f"em meses já fechados (até {cf['mes_limite']}) para o mesmo PEP entre {rodada_anterior} "
            f"e {rodada_atual} — vale checar antes de levar os números adiante."
        )
    elif cf["mes_limite"]:
        ins.append(f"Confronto de consistência: nenhuma divergência de Realizado/Orçado até {cf['mes_limite']} entre as duas rodadas — dado estável.")

    if not ins:
        ins.append("Nenhuma variação relevante (> R$ 0,5 Mi) detectada entre as duas rodadas.")
    return ins
