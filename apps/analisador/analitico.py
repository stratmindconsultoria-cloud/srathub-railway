"""
Motor analítico comparativo entre rodadas de tendência.

Diferença de propósito em relação ao `trend_engine`: aquele responde
"quanto mudou"; este responde "por quê, é padrão, é crível e o que
perguntar". A unidade de análise aqui não é o indicador — é o
MOVIMENTO: uma linha por (PEP × dimensão) que mexeu entre duas
rodadas, com natureza, materialidade, reversibilidade e evidência.

Todas as telas são cortes deste catálogo. Nenhum número aqui é órfão:
todo agregado sabe quais PEPs o compõem.
"""
from __future__ import annotations
import datetime as _dt

import database as db
import trend_engine as te
from categorias import DESVIO_KEYS, DESVIO_LABELS, MES_IDX, MESES

DIMENSOES = {"econ": "Econômico", "fin": "Financeiro"}
# concordância: "a tendência Financeira", não "a tendência Financeiro"
DIM_ADJ = {"econ": "Econômica", "fin": "Financeira"}

# Corte de materialidade padrão (R$). É um default, não uma verdade —
# a tela expõe isso e o usuário ajusta.
CORTE_PADRAO = 500_000.0

TIPOS_MOVIMENTO = {
    "entrada": "Entrada de escopo",
    "saida": "Saída de escopo",
    "valor": "Valor (carteira mudou de tamanho)",
    "calendario": "Calendário (dinheiro andou de ano)",
    "classificacao": "Classificação (mudou o motivo, não o valor)",
    "estavel": "Estável",
}

# Agrupamento das 13 categorias de desvio em famílias — usado na ponte
# para que a decomposição caiba em um gráfico legível.
FAMILIAS = {
    "Calendário": ["postergado", "antecipacao"],
    "Escopo": ["acrescimo_escopo", "reducao_escopo", "nao_orcado", "cancelado", "migracao"],
    "Performance": ["engenharia", "contratacao", "implantacao", "poder"],
    "Custo": ["custo_adicional", "economia"],
}
FAMILIA_DE = {k: fam for fam, ks in FAMILIAS.items() for k in ks}


# ── utilidades ────────────────────────────────────────────────────────────
def _n(v) -> float:
    return float(v or 0.0)


def _mapa(rodada: str, filtro_geral: dict | None) -> dict[str, dict]:
    return {p["pep"]: p for p in te._projetos(rodada, filtro_geral)}


def _meta(rodada: str) -> dict:
    return next((r for r in db.listar_rodadas() if r["rodada"] == rodada), {})


def _mes_idx(rodada: str) -> int | None:
    return MES_IDX.get((_meta(rodada) or {}).get("mes_analise") or "")


def tend_ano(p: dict, dim: str) -> float:
    return _n(p.get(f"{dim}_tend_ano"))


def tend_qq(p: dict, dim: str) -> float:
    """Tendência do quinquênio. Cai para a soma dos anos, e daí para o ano
    corrente, conforme o que a planilha daquela rodada tiver trazido."""
    v = _n(p.get(f"{dim}_tend_qq"))
    if v:
        return v
    anos = p.get(f"{dim}_tend_anos") or {}
    if anos:
        return sum(_n(x) for x in anos.values())
    return tend_ano(p, dim)


def tem_plurianual(rodada: str, filtro_geral: dict | None = None) -> bool:
    for p in te._projetos(rodada, filtro_geral)[:50]:
        if p.get(f"fin_tend_anos") or _n(p.get("fin_tend_qq")):
            return True
    return False


def fmt_mi(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v / 1e6:,.1f}".replace(",", "@").replace(".", ",").replace("@", ".") + " Mi"


# ── 1. catálogo de movimentos ─────────────────────────────────────────────
def catalogo_movimentos(rodada_atual: str, rodada_anterior: str | None = None,
                        dim: str = "fin", filtro_geral: dict | None = None,
                        corte: float = CORTE_PADRAO) -> dict:
    """Uma linha por PEP que mexeu entre as duas rodadas.

    Separa as quatro naturezas de movimento que hoje ficam emboladas num
    delta só: entrada/saída de escopo, mudança de valor da carteira,
    deslocamento de calendário (o ano muda mas o quinquênio não) e
    mudança apenas de classificação/narrativa.
    """
    rodada_atual, rodada_anterior = te._resolver_par(rodada_atual, rodada_anterior)
    atual = _mapa(rodada_atual, filtro_geral)
    if not rodada_anterior:
        return {"rodada_atual": rodada_atual, "rodada_anterior": None, "movimentos": [],
                "resumo": {}, "corte": corte, "plurianual": tem_plurianual(rodada_atual, filtro_geral)}
    anterior = _mapa(rodada_anterior, filtro_geral)
    plurianual = tem_plurianual(rodada_atual, filtro_geral) and tem_plurianual(rodada_anterior, filtro_geral)

    movimentos = []
    for pep in set(atual) | set(anterior):
        a, b = atual.get(pep), anterior.get(pep)
        ref = a or b

        ta, tb = (tend_ano(a, dim) if a else 0.0), (tend_ano(b, dim) if b else 0.0)
        qa, qb = (tend_qq(a, dim) if a else 0.0), (tend_qq(b, dim) if b else 0.0)
        d_ano, d_qq = ta - tb, qa - qb

        # o realizado é a âncora de fato: o que mexeu nele já aconteceu
        ra, rb = (_n(a.get(f"{dim}_real_acum")) if a else 0.0), (_n(b.get(f"{dim}_real_acum")) if b else 0.0)
        d_real = ra - rb

        cats_a = (a.get(f"{dim}_desvios") if a else {}) or {}
        cats_b = (b.get(f"{dim}_desvios") if b else {}) or {}
        d_cats = {k: _n(cats_a.get(k)) - _n(cats_b.get(k))
                  for k in set(cats_a) | set(cats_b)
                  if abs(_n(cats_a.get(k)) - _n(cats_b.get(k))) > 1.0}

        just_a = (a.get(f"{dim}_just_ano") if a else None) or ""
        just_b = (b.get(f"{dim}_just_ano") if b else None) or ""

        if a and not b:
            tipo = "entrada"
        elif b and not a:
            tipo = "saida"
        elif abs(d_qq) > corte:
            tipo = "valor"
        elif abs(d_ano) > corte:
            # o ano mudou mas o quinquênio não: o dinheiro andou de ano
            tipo = "calendario" if plurianual else "valor"
        elif d_cats and sum(abs(v) for v in d_cats.values()) > corte:
            tipo = "classificacao"
        else:
            tipo = "estavel"

        if tipo == "estavel" and just_a.strip() == just_b.strip():
            continue

        movimentos.append({
            "pep": pep,
            "projeto": (ref.get("projeto") or "")[:90],
            "unidade": ref.get("unidade"),
            "vertical_gestora": ref.get("vertical_gestora"),
            "diretoria_engenharia": ref.get("diretoria_engenharia"),
            "grupo": ref.get("grupo"),
            "motivador": ref.get("motivador"),
            "tipo": tipo,
            "tipo_label": TIPOS_MOVIMENTO[tipo],
            "tend_ant": tb, "tend_atual": ta, "delta_ano": d_ano,
            "delta_pct": (d_ano / abs(tb) * 100) if tb else None,
            "qq_ant": qb, "qq_atual": qa, "delta_qq": d_qq,
            # decomposição fato × previsão: o que já virou realizado não é
            # mais reversível; o resto ainda é decisão.
            "parte_fato": d_real,
            "parte_previsao": d_ano - d_real,
            "delta_categorias": d_cats,
            "cat_dominante": max(d_cats, key=lambda k: abs(d_cats[k])) if d_cats else None,
            "just_mudou": just_a.strip() != just_b.strip(),
            "just_atual": just_a[:400] or None,
            "just_anterior": just_b[:400] or None,
            "material": abs(d_ano) >= corte or tipo in ("entrada", "saida"),
        })

    movimentos.sort(key=lambda m: abs(m["delta_ano"]), reverse=True)

    resumo = {t: {"n": 0, "valor": 0.0} for t in TIPOS_MOVIMENTO}
    for m in movimentos:
        resumo[m["tipo"]]["n"] += 1
        resumo[m["tipo"]]["valor"] += m["delta_ano"]

    total_peps = len(set(atual) | set(anterior))
    mexeram = sum(1 for m in movimentos if m["tipo"] != "estavel")
    return {
        "rodada_atual": rodada_atual, "rodada_anterior": rodada_anterior,
        "movimentos": movimentos, "resumo": resumo, "corte": corte,
        "plurianual": plurianual,
        "n_peps": total_peps,
        "pct_carteira_mexeu": (mexeram / total_peps * 100) if total_peps else 0.0,
        "delta_total": sum(m["delta_ano"] for m in movimentos),
        "fato_total": sum(m["parte_fato"] for m in movimentos),
        "previsao_total": sum(m["parte_previsao"] for m in movimentos),
    }


# ── 2. ponte entre rodadas (o gráfico-herói) ──────────────────────────────
def ponte_rodadas(rodada_atual: str, rodada_anterior: str | None = None,
                  dim: str = "fin", filtro_geral: dict | None = None,
                  por_familia: bool = True) -> dict:
    """Waterfall Tendência(anterior) → Tendência(atual), decomposto e com
    fechamento aritmético garantido: o residual é explicitado como
    'Não classificado' em vez de ser escondido."""
    cat = catalogo_movimentos(rodada_atual, rodada_anterior, dim, filtro_geral)
    if not cat["rodada_anterior"]:
        return {"disponivel": False, "rodada_atual": cat["rodada_atual"]}

    base = sum(m["tend_ant"] for m in cat["movimentos"])
    fim = sum(m["tend_atual"] for m in cat["movimentos"])
    # os estáveis entram nos dois lados e se cancelam; base/fim do portfólio
    # completo vêm do agregado, para o gráfico bater com os KPIs
    ant = te._agg(te._projetos(cat["rodada_anterior"], filtro_geral))[f"{dim}_tend_ano"]
    atu = te._agg(te._projetos(cat["rodada_atual"], filtro_geral))[f"{dim}_tend_ano"]

    buckets: list[dict] = []
    entradas = sum(m["delta_ano"] for m in cat["movimentos"] if m["tipo"] == "entrada")
    saidas = sum(m["delta_ano"] for m in cat["movimentos"] if m["tipo"] == "saida")
    if entradas:
        buckets.append({"nome": "Entradas de escopo", "valor": entradas, "familia": "Escopo",
                        "peps": [m["pep"] for m in cat["movimentos"] if m["tipo"] == "entrada"]})
    if saidas:
        buckets.append({"nome": "Saídas de escopo", "valor": saidas, "familia": "Escopo",
                        "peps": [m["pep"] for m in cat["movimentos"] if m["tipo"] == "saida"]})

    # decomposição por categoria de desvio, só dos PEPs que existem nas duas rodadas
    comuns = [m for m in cat["movimentos"] if m["tipo"] not in ("entrada", "saida")]
    por_cat: dict[str, float] = {}
    peps_cat: dict[str, list[str]] = {}
    for m in comuns:
        for k, v in m["delta_categorias"].items():
            por_cat[k] = por_cat.get(k, 0.0) + v
            peps_cat.setdefault(k, []).append(m["pep"])

    if por_familia:
        agrup: dict[str, float] = {}
        agrup_peps: dict[str, list[str]] = {}
        for k, v in por_cat.items():
            fam = FAMILIA_DE.get(k, "Outros")
            agrup[fam] = agrup.get(fam, 0.0) + v
            agrup_peps.setdefault(fam, []).extend(peps_cat.get(k, []))
        for nome, v in sorted(agrup.items(), key=lambda kv: -abs(kv[1])):
            if abs(v) > 1.0:
                buckets.append({"nome": nome, "valor": v, "familia": nome,
                                "peps": sorted(set(agrup_peps.get(nome, [])))})
    else:
        for k, v in sorted(por_cat.items(), key=lambda kv: -abs(kv[1])):
            if abs(v) > 1.0:
                buckets.append({"nome": DESVIO_LABELS.get(k, k), "valor": v,
                                "familia": FAMILIA_DE.get(k, "Outros"),
                                "peps": sorted(set(peps_cat.get(k, [])))})

    explicado = sum(b["valor"] for b in buckets)
    residual = (atu - ant) - explicado
    if abs(residual) > 1.0:
        buckets.append({"nome": "Não classificado", "valor": residual, "familia": "Residual", "peps": []})

    # coordenadas de waterfall (barras flutuantes)
    passos = []
    acum = ant
    for b in buckets:
        passos.append({"nome": b["nome"], "valor": b["valor"], "de": acum, "para": acum + b["valor"],
                       "familia": b["familia"], "n_peps": len(b["peps"])})
        acum += b["valor"]

    return {
        "disponivel": True,
        "rodada_atual": cat["rodada_atual"], "rodada_anterior": cat["rodada_anterior"],
        "inicio": ant, "fim": atu, "delta": atu - ant,
        "delta_pct": ((atu - ant) / abs(ant) * 100) if ant else None,
        "passos": passos, "buckets": buckets,
        "residual": residual,
        "pct_explicado": (1 - abs(residual) / abs(atu - ant)) * 100 if abs(atu - ant) > 1 else 100.0,
        "plurianual": cat["plurianual"],
        "fato": cat["fato_total"], "previsao": cat["previsao_total"],
    }


# ── 3. matriz vintage: rodada × período ───────────────────────────────────
def matriz_vintage(dim: str = "fin", filtro_geral: dict | None = None) -> dict:
    """Cada linha é uma rodada, cada coluna um ano do calendário: o que
    aquela rodada dizia sobre aquele ano.

    Ler uma linha = a visão de mundo de uma rodada.
    Ler uma coluna = como a opinião sobre um mesmo ano foi mudando.
    """
    rodadas = db.listar_rodadas()
    proj_rod = {r["rodada"]: te._projetos(r["rodada"], filtro_geral) for r in rodadas}
    anos_set = {str(r["ano"]) for r in rodadas if r.get("ano")}
    for ps in proj_rod.values():
        for p in ps:
            anos_set.update(str(k) for k in (p.get(f"{dim}_tend_anos") or {}).keys())
    ANOS = sorted(anos_set) or [str(_dt.date.today().year)]
    linhas = []
    for r in rodadas:
        projetos = proj_rod[r["rodada"]]
        ano_r = str(r.get("ano") or ANOS[0])
        por_ano = {y: 0.0 for y in ANOS}
        tem_dados = False
        for p in projetos:
            anos = p.get(f"{dim}_tend_anos") or {}
            if anos:
                tem_dados = True
                for y in ANOS:
                    por_ano[y] += _n(anos.get(y))
            else:
                por_ano[ano_r] += tend_ano(p, dim)
        linhas.append({
            "rodada": r["rodada"], "mes_analise": r["mes_analise"], "status": r["status"], "ano": ano_r,
            "anos": por_ano, "total": sum(por_ano.values()), "plurianual": tem_dados,
            "real_acum": te._agg(projetos)[f"{dim}_real_acum"],
            "orc_ano": te._agg(projetos)[f"{dim}_orc_ano"],
        })

    # variação coluna a coluna: a leitura vertical, que é a que não existia
    for i, l in enumerate(linhas):
        l["delta_anos"] = {}
        if i == 0:
            continue
        for y in ANOS:
            l["delta_anos"][y] = l["anos"][y] - linhas[i - 1]["anos"][y]

    # tendência de cada coluna ao longo das rodadas (erosão x oscilação)
    colunas = {}
    for y in ANOS:
        serie = [l["anos"][y] for l in linhas]
        deltas = [b - a for a, b in zip(serie, serie[1:])]
        soma = sum(deltas)
        amplitude = sum(abs(d) for d in deltas)
        colunas[y] = {
            "serie": serie, "delta_total": soma,
            "direcao": "erosão" if soma < 0 else ("expansão" if soma > 0 else "estável"),
            # se o caminho andado é muito maior que o deslocamento líquido,
            # a opinião oscilou em vez de convergir
            "vaivem": (amplitude / abs(soma)) if abs(soma) > 1 else None,
            "n_reversoes": sum(1 for a, b in zip(deltas, deltas[1:]) if a * b < 0),
        }

    return {"linhas": linhas, "anos": ANOS, "colunas": colunas,
            "plurianual": any(l["plurianual"] for l in linhas)}


def vintage_mensal(dim: str = "fin", filtro_geral: dict | None = None) -> dict:
    """Rodada × mês do realizado declarado. A leitura de coluna aqui expõe
    mês fechado que mudou de valor entre rodadas — que, por definição, não
    deveria mudar."""
    rodadas = db.listar_rodadas()
    linhas = []
    for r in rodadas:
        soma = [0.0] * 12
        for p in te._projetos(r["rodada"], filtro_geral):
            mensal = p.get(f"{dim}_real_mensal") or []
            for i in range(min(12, len(mensal))):
                soma[i] += _n(mensal[i])
        mi = MES_IDX.get(r["mes_analise"] or "")
        linhas.append({"rodada": r["rodada"], "mes_analise": r["mes_analise"],
                       "mes_idx": mi, "valores": soma})
    return {"linhas": linhas, "meses": MESES}


# ── 4. padrões de trajetória (N rodadas, não par a par) ───────────────────
def _classificar_padrao(serie: list[float], corte: float) -> tuple[str, dict]:
    deltas = [b - a for a, b in zip(serie, serie[1:])]
    mov = [d for d in deltas if abs(d) > corte]
    liquido = serie[-1] - serie[0]
    caminho = sum(abs(d) for d in deltas)
    reversoes = sum(1 for a, b in zip(deltas, deltas[1:]) if a * b < 0 and abs(a) > corte and abs(b) > corte)
    ev = {"liquido": liquido, "caminho": caminho, "reversoes": reversoes, "n_movimentos": len(mov)}
    if not mov:
        return "estavel", ev
    if reversoes >= 1 and caminho > abs(liquido) * 2:
        return "serrote", ev
    if len(mov) == 1:
        return "degrau", ev
    if all(d < 0 for d in mov):
        return "erosao", ev
    if all(d > 0 for d in mov):
        return "escalada", ev
    return "irregular", ev


PADROES = {
    "escalada": "Escalada — revisões sucessivas para cima",
    "erosao": "Erosão — revisões sucessivas para baixo",
    "serrote": "Serrote — sobe e volta, delta líquido engana",
    "degrau": "Degrau — um movimento único, isolado",
    "irregular": "Irregular — sem padrão claro",
    "estavel": "Estável",
}


def padroes_trajetoria(dim: str = "fin", filtro_geral: dict | None = None,
                       corte: float = CORTE_PADRAO, top_n: int = 40) -> dict:
    rodadas = [r["rodada"] for r in db.listar_rodadas()]
    if len(rodadas) < 3:
        return {"suficiente": False, "n_rodadas": len(rodadas), "itens": [], "resumo": {}}

    mapas = {r: _mapa(r, filtro_geral) for r in rodadas}
    peps = {p for m in mapas.values() for p in m}
    itens = []
    for pep in peps:
        serie, presenca = [], []
        ref = None
        for r in rodadas:
            p = mapas[r].get(pep)
            presenca.append(bool(p))
            serie.append(tend_ano(p, dim) if p else 0.0)
            if p:
                ref = p
        if not ref:
            continue
        padrao, ev = _classificar_padrao(serie, corte)
        if padrao == "estavel":
            continue
        itens.append({
            "pep": pep, "projeto": (ref.get("projeto") or "")[:90],
            "unidade": ref.get("unidade"), "vertical_gestora": ref.get("vertical_gestora"),
            "serie": serie, "presenca": presenca, "padrao": padrao,
            "padrao_label": PADROES[padrao], **ev,
        })

    # ordena por relevância analítica: serrote pesa mais que delta líquido,
    # porque é exatamente o que a comparação par a par não vê
    peso = {"serrote": 1.5, "erosao": 1.2, "escalada": 1.2, "irregular": 1.0, "degrau": 0.8}
    itens.sort(key=lambda i: max(abs(i["liquido"]), i["caminho"] * 0.5) * peso.get(i["padrao"], 1), reverse=True)

    resumo = {}
    for i in itens:
        r = resumo.setdefault(i["padrao"], {"n": 0, "valor": 0.0, "label": PADROES[i["padrao"]]})
        r["n"] += 1
        r["valor"] += i["liquido"]

    return {"suficiente": True, "n_rodadas": len(rodadas), "rodadas": rodadas,
            "itens": itens[:top_n], "total_itens": len(itens), "resumo": resumo}


# ── 5. credibilidade do previsor ──────────────────────────────────────────
def credibilidade(dim: str = "fin", campo: str = "unidade",
                  filtro_geral: dict | None = None) -> dict:
    """Por unidade/vertical: como as tendências declaradas se comportaram
    ao longo das rodadas. Viés (revisa sempre para o mesmo lado),
    volatilidade e reversões."""
    rodadas = [r["rodada"] for r in db.listar_rodadas()]
    if len(rodadas) < 3:
        return {"suficiente": False, "n_rodadas": len(rodadas), "itens": []}

    series: dict[str, list[float]] = {}
    for r in rodadas:
        agrup: dict[str, float] = {}
        for p in te._projetos(r, filtro_geral):
            k = p.get(campo) or "— não informado —"
            agrup[k] = agrup.get(k, 0.0) + tend_ano(p, dim)
        for k in set(agrup) | set(series):
            series.setdefault(k, [0.0] * len(series.get(k, [])))
        for k in series:
            series[k].append(agrup.get(k, 0.0))

    itens = []
    for k, serie in series.items():
        deltas = [b - a for a, b in zip(serie, serie[1:])]
        pcts = [(b - a) / abs(a) * 100 for a, b in zip(serie, serie[1:]) if a]
        if not deltas:
            continue
        n_neg = sum(1 for d in deltas if d < 0)
        n_pos = sum(1 for d in deltas if d > 0)
        liquido = serie[-1] - serie[0]
        caminho = sum(abs(d) for d in deltas)
        itens.append({
            "chave": k, "serie": serie, "atual": serie[-1],
            "liquido": liquido, "caminho": caminho,
            "revisao_media_pct": (sum(pcts) / len(pcts)) if pcts else None,
            "n_revisoes_baixa": n_neg, "n_revisoes_alta": n_pos,
            "vies": ("baixa" if n_neg > n_pos * 1.5 else ("alta" if n_pos > n_neg * 1.5 else "equilibrado")),
            "volatilidade": (caminho / abs(liquido)) if abs(liquido) > 1 else None,
            "n_reversoes": sum(1 for a, b in zip(deltas, deltas[1:]) if a * b < 0),
        })
    itens.sort(key=lambda i: abs(i["caminho"]), reverse=True)
    return {"suficiente": True, "n_rodadas": len(rodadas), "rodadas": rodadas,
            "itens": itens, "campo": campo, "campo_label": te.FILTRO_CAMPOS.get(campo, campo)}


# ── 6. exequibilidade da tendência ────────────────────────────────────────
def exequibilidade(rodada: str, dim: str = "fin", filtro_geral: dict | None = None,
                   top_n: int = 25) -> dict:
    """A tendência declarada cabe no calendário que sobrou?

    Compara o run-rate mensal necessário para atingir a tendência com o
    run-rate realizado recente. Razão > 2 é sinal amarelo, > 3 é ficção.
    """
    projetos = te._projetos(rodada, filtro_geral)
    mi = _mes_idx(rodada)
    meses_restantes = (12 - mi) if mi else None

    tend = sum(tend_ano(p, dim) for p in projetos)
    real = sum(_n(p.get(f"{dim}_real_acum")) for p in projetos)
    falta = tend - real

    def _run_rate_recente(p: dict) -> float:
        mensal = p.get(f"{dim}_real_mensal") or []
        if not mi or not mensal:
            return 0.0
        ini = max(0, mi - 3)
        janela = [_n(v) for v in mensal[ini:mi]]
        return (sum(janela) / len(janela)) if janela else 0.0

    rr_recente = sum(_run_rate_recente(p) for p in projetos)
    rr_necessario = (falta / meses_restantes) if meses_restantes else None
    razao = (rr_necessario / rr_recente) if (rr_necessario and rr_recente) else None

    itens = []
    for p in projetos:
        t, r = tend_ano(p, dim), _n(p.get(f"{dim}_real_acum"))
        f = t - r
        rr = _run_rate_recente(p)
        nec = (f / meses_restantes) if meses_restantes else None
        raz = (nec / rr) if (nec and rr) else None
        sem_comp = _n(p.get("comp_sem_ano"))
        ped = _n(p.get("comp_ped_ano"))
        req = _n(p.get("comp_req_ano"))
        itens.append({
            "pep": p["pep"], "projeto": (p.get("projeto") or "")[:90],
            "unidade": p.get("unidade"), "vertical_gestora": p.get("vertical_gestora"),
            "tend": t, "real": r, "falta": f,
            "rr_recente": rr, "rr_necessario": nec, "razao": raz,
            "comp_ped": ped, "comp_req": req, "comp_sem": sem_comp,
            "cobertura_comp": ((ped + req) / t * 100) if t else None,
            # tendência alta, nada comprometido e nada realizado = tendência de papel
            "papel": bool(t > CORTE_PADRAO and (ped + req) == 0 and r == 0),
            "inexequivel": bool(raz and raz > 3 and f > CORTE_PADRAO),
        })

    inexequiveis = sorted([i for i in itens if i["inexequivel"]], key=lambda i: -i["falta"])
    papel = sorted([i for i in itens if i["papel"]], key=lambda i: -i["tend"])

    # descolamento físico × econômico
    descolados = []
    for p in projetos:
        peso, base, realf = p.get("fis_peso"), p.get("fis_baseline_atual"), p.get("fis_real_atual")
        t, r = tend_ano(p, dim), _n(p.get(f"{dim}_real_acum"))
        if not peso or base is None or realf is None or not t:
            continue
        avanco_fis = _n(realf)
        avanco_fin = (r / t * 100) if t else 0.0
        gap = avanco_fin - avanco_fis
        if abs(gap) >= 20 and t > CORTE_PADRAO:
            descolados.append({
                "pep": p["pep"], "projeto": (p.get("projeto") or "")[:90],
                "unidade": p.get("unidade"), "avanco_fis": avanco_fis,
                "avanco_fin": avanco_fin, "gap": gap, "tend": t,
                "sentido": "gasta sem avançar" if gap > 0 else "avança sem gastar",
            })
    descolados.sort(key=lambda d: -abs(d["gap"]) * d["tend"] / 1e6)

    return {
        "rodada": rodada, "mes_analise": (_meta(rodada) or {}).get("mes_analise"),
        "meses_restantes": meses_restantes,
        "tend": tend, "real": real, "falta": falta,
        "rr_recente": rr_recente, "rr_necessario": rr_necessario, "razao": razao,
        "veredito": ("inexequível" if (razao or 0) > 3 else
                     ("apertada" if (razao or 0) > 2 else
                      ("plausível" if razao else "sem base para avaliar"))),
        "inexequiveis": inexequiveis[:top_n], "n_inexequiveis": len(inexequiveis),
        "valor_inexequivel": sum(i["falta"] for i in inexequiveis),
        "papel": papel[:top_n], "n_papel": len(papel),
        "valor_papel": sum(i["tend"] for i in papel),
        "descolados": descolados[:top_n], "n_descolados": len(descolados),
    }


# ── 7. qualidade da rodada (a pergunta zero) ──────────────────────────────
def qualidade_rodada(rodada: str, rodada_anterior: str | None = None,
                     filtro_geral: dict | None = None, corte: float = CORTE_PADRAO) -> dict:
    rodada, rodada_anterior = te._resolver_par(rodada, rodada_anterior)
    meta = _meta(rodada)
    projetos = te._projetos(rodada, filtro_geral)
    checks = []

    def add(nome, status, detalhe, n=None, valor=None, peps=None):
        checks.append({"nome": nome, "status": status, "detalhe": detalhe,
                       "n": n, "valor": valor, "peps": peps or []})

    # cobertura de verticais
    conhecidas = set(db.todas_verticais_conhecidas())
    carregadas = set(db.verticais_da_rodada(rodada))
    faltando = sorted(conhecidas - carregadas)
    add("Cobertura de Verticais Gestoras",
        "erro" if faltando else "ok",
        (f"Faltam nesta rodada: {', '.join(faltando)}" if faltando
         else f"{len(carregadas)} verticais carregadas — cobertura completa."),
        n=len(carregadas))

    # confronto de meses fechados
    for dim in ("fin", "econ"):
        cf = te.confronto_meses_fechados(rodada, rodada_anterior, dim, filtro_geral)
        if cf.get("mes_limite"):
            add(f"Meses fechados estáveis ({DIMENSOES[dim]})",
                "erro" if cf["n_divergencias"] else "ok",
                (f"{cf['n_divergencias']} divergência(s) de Realizado/Orçado até {cf['mes_limite']} "
                 f"para o mesmo PEP entre {rodada_anterior} e {rodada}."
                 if cf["n_divergencias"] else
                 f"Nenhuma divergência até {cf['mes_limite']} — o passado não mexeu."),
                n=cf["n_divergencias"])

    # desvio material sem justificativa
    for dim in ("fin", "econ"):
        sem_just = [p for p in projetos
                    if abs(tend_ano(p, dim) - _n(p.get(f"{dim}_orc_ano"))) > corte
                    and not (p.get(f"{dim}_just_ano") or "").strip()]
        add(f"Justificativa em desvio material ({DIMENSOES[dim]})",
            "erro" if len(sem_just) > 10 else ("alerta" if sem_just else "ok"),
            (f"{len(sem_just)} projeto(s) com desvio acima de {fmt_mi(corte)} e nenhuma justificativa no ano."
             if sem_just else "Todo desvio material tem justificativa preenchida."),
            n=len(sem_just),
            valor=sum(abs(tend_ano(p, dim) - _n(p.get(f"{dim}_orc_ano"))) for p in sem_just),
            peps=[p["pep"] for p in sem_just[:30]])

    # narrativa mudou sem o número mudar (e vice-versa)
    if rodada_anterior:
        cat = catalogo_movimentos(rodada, rodada_anterior, "fin", filtro_geral, corte)
        so_narrativa = [m for m in cat["movimentos"] if m["tipo"] == "estavel" and m["just_mudou"]]
        so_numero = [m for m in cat["movimentos"]
                     if m["tipo"] in ("valor", "calendario") and not m["just_mudou"]]
        add("Narrativa × número",
            "alerta" if so_numero else "ok",
            (f"{len(so_numero)} projeto(s) mudaram de valor mantendo a mesma justificativa; "
             f"{len(so_narrativa)} mudaram só o texto sem mudar o valor."),
            n=len(so_numero), peps=[m["pep"] for m in so_numero[:30]])
        add("Reclassificação de motivo",
            "alerta" if cat["resumo"]["classificacao"]["n"] else "ok",
            (f"{cat['resumo']['classificacao']['n']} projeto(s) trocaram a categoria do desvio "
             f"sem mudar o valor — movimento de narrativa, não de carteira."),
            n=cat["resumo"]["classificacao"]["n"])
        add("Estabilidade da carteira", "info",
            f"{cat['pct_carteira_mexeu']:.0f}% dos {cat['n_peps']} projetos mexeram entre "
            f"{rodada_anterior} e {rodada}.", n=cat["n_peps"])

    # cobertura física
    com_peso = sum(1 for p in projetos if p.get("fis_peso"))
    add("Cobertura de Peso Físico",
        "alerta" if com_peso < len(projetos) * 0.5 else "ok",
        f"{com_peso} de {len(projetos)} projetos com peso físico rastreado.", n=com_peso)

    # plurianual
    plur = tem_plurianual(rodada, filtro_geral)
    add("Base plurianual (2027–2030)", "ok" if plur else "alerta",
        ("Colunas plurianuais presentes — dá para separar deslocamento de ano de saída de carteira."
         if plur else
         "Sem colunas plurianuais nesta rodada: deslocamento para 2027+ vai aparecer como redução de carteira."),
        )

    # avisos do parser
    import json as _json
    avisos = _json.loads(meta.get("avisos") or "[]") if meta else []
    if avisos:
        add("Avisos da importação", "alerta", " · ".join(avisos[:4]), n=len(avisos))

    erros = sum(1 for c in checks if c["status"] == "erro")
    alertas = sum(1 for c in checks if c["status"] == "alerta")
    return {
        "rodada": rodada, "rodada_anterior": rodada_anterior, "checks": checks,
        "n_erros": erros, "n_alertas": alertas,
        "veredito": ("não confiável" if erros else ("confiável com ressalvas" if alertas else "confiável")),
        "status": ("erro" if erros else ("alerta" if alertas else "ok")),
        "meta": meta,
    }


# ── 8. insights tipados ───────────────────────────────────────────────────
SEVERIDADES = {"critico": 3, "atencao": 2, "info": 1}


def _insight(tipo, severidade, titulo, evidencia, pergunta=None, materialidade=0.0,
             aba="movimento", link=None, escopo="portfólio"):
    return {"tipo": tipo, "severidade": severidade, "titulo": titulo, "evidencia": evidencia,
            "pergunta": pergunta, "materialidade": abs(materialidade or 0.0),
            "aba": aba, "link": link, "escopo": escopo,
            "peso": SEVERIDADES.get(severidade, 1) * (abs(materialidade or 0.0) / 1e6 + 1)}


def gerar_insights(rodada: str, rodada_anterior: str | None = None, dim: str = "fin",
                   filtro_geral: dict | None = None, corte: float = CORTE_PADRAO) -> list[dict]:
    """Achados estruturados: severidade, materialidade, evidência, e a
    pergunta que o PMO deveria levar para a reunião."""
    rodada, rodada_anterior = te._resolver_par(rodada, rodada_anterior)
    label = DIMENSOES[dim]
    out: list[dict] = []

    q = qualidade_rodada(rodada, rodada_anterior, filtro_geral, corte)
    for c in q["checks"]:
        if c["status"] == "erro":
            out.append(_insight("qualidade", "critico", f"Base: {c['nome']}", c["detalhe"],
                                "Resolver antes de levar estes números ao comitê.",
                                c.get("valor") or 0.0, aba="qualidade"))
        elif c["status"] == "alerta":
            out.append(_insight("qualidade", "atencao", f"Base: {c['nome']}", c["detalhe"],
                                None, c.get("valor") or 0.0, aba="qualidade"))

    if not rodada_anterior:
        out.append(_insight("contexto", "info", f"{rodada} é a primeira rodada carregada",
                            "Sem par de comparação: carregue outra rodada para as análises comparativas.",
                            aba="movimento"))
        return sorted(out, key=lambda i: -i["peso"])

    p = ponte_rodadas(rodada, rodada_anterior, dim, filtro_geral)
    cat = catalogo_movimentos(rodada, rodada_anterior, dim, filtro_geral, corte)

    if p["disponivel"] and abs(p["delta"]) > corte:
        direcao = "caiu" if p["delta"] < 0 else "subiu"
        maior = max(p["passos"], key=lambda b: abs(b["valor"])) if p["passos"] else None
        out.append(_insight(
            "movimento", "critico" if abs(p["delta"]) > corte * 20 else "atencao",
            f"Tendência {DIM_ADJ[dim]} {direcao} {fmt_mi(abs(p['delta']))} de {rodada_anterior} para {rodada}",
            (f"{fmt_mi(p['inicio'])} → {fmt_mi(p['fim'])} ({p['delta_pct']:+.1f}%). "
             + (f"Maior contribuição: {maior['nome']} ({fmt_mi(maior['valor'])})." if maior else "")
             + f" {p['pct_explicado']:.0f}% do movimento é explicado pela classificação de desvios."),
            (f"Pedir a abertura de '{maior['nome']}' por projeto — {maior['n_peps']} PEPs envolvidos."
             if maior else None),
            p["delta"], aba="movimento"))

    # fato × previsão: o que já aconteceu e o que ainda é decisão
    if abs(cat["previsao_total"]) > corte:
        out.append(_insight(
            "reversibilidade", "atencao",
            f"{fmt_mi(abs(cat['previsao_total']))} do movimento ainda é previsão, não fato",
            (f"Do delta total de {fmt_mi(cat['delta_total'])}, {fmt_mi(cat['fato_total'])} já virou "
             f"realizado (irreversível) e {fmt_mi(cat['previsao_total'])} é revisão de expectativa — "
             f"esta parte ainda é decisão, não constatação."),
            "Sobre a parcela de previsão: o que precisa acontecer para ela não se confirmar?",
            cat["previsao_total"], aba="movimento"))

    # calendário x carteira
    calend = cat["resumo"]["calendario"]
    if cat["plurianual"] and abs(calend["valor"]) > corte:
        out.append(_insight(
            "calendario", "atencao",
            f"{fmt_mi(abs(calend['valor']))} saíram do ano sem sair da carteira",
            (f"{calend['n']} projeto(s) deslocaram valor para 2027+ mantendo o quinquênio estável. "
             f"Isso reduz o ano, não o compromisso."),
            "O deslocamento tem respaldo contratual, ou é ajuste de expectativa de execução?",
            calend["valor"], aba="movimento"))

    esc = cat["resumo"]["entrada"]["valor"] + cat["resumo"]["saida"]["valor"]
    if abs(esc) > corte:
        out.append(_insight(
            "escopo", "atencao",
            f"Escopo mexeu {fmt_mi(abs(esc))} líquidos entre as rodadas",
            (f"{cat['resumo']['entrada']['n']} entrada(s) somando {fmt_mi(cat['resumo']['entrada']['valor'])} e "
             f"{cat['resumo']['saida']['n']} saída(s) somando {fmt_mi(cat['resumo']['saida']['valor'])}."),
            "As entradas passaram por aprovação de portfólio? As saídas foram canceladas ou migradas?",
            esc, aba="movimento"))

    if cat["resumo"]["classificacao"]["n"]:
        out.append(_insight(
            "classificacao", "info",
            f"{cat['resumo']['classificacao']['n']} projeto(s) mudaram o motivo sem mudar o valor",
            "Mesmo delta, categoria de desvio diferente entre as rodadas — movimento de narrativa.",
            "A reclassificação corrige um erro anterior ou muda a responsabilidade pelo desvio?",
            0.0, aba="qualidade"))

    # maiores movimentos individuais
    for m in [m for m in cat["movimentos"] if m["material"]][:3]:
        out.append(_insight(
            "projeto", "atencao" if abs(m["delta_ano"]) > corte * 10 else "info",
            f"{m['projeto']}: {fmt_mi(m['delta_ano'])}",
            (f"{m['tipo_label']} · {fmt_mi(m['tend_ant'])} → {fmt_mi(m['tend_atual'])}"
             + (f" · categoria dominante: {DESVIO_LABELS.get(m['cat_dominante'], m['cat_dominante'])}"
                if m["cat_dominante"] else "")
             + ("" if m["just_mudou"] else " · justificativa NÃO foi atualizada")),
            (None if m["just_mudou"] else
             f"O valor mudou e a justificativa continuou a mesma — pedir atualização ao gestor de {m['unidade'] or 'unidade'}."),
            m["delta_ano"], aba="movimento", link=f"/tendencia/pep/{m['pep']}", escopo=m["pep"]))

    # padrões
    pad = padroes_trajetoria(dim, filtro_geral, corte)
    if pad["suficiente"]:
        serrotes = [i for i in pad["itens"] if i["padrao"] == "serrote"]
        if serrotes:
            s = serrotes[0]
            out.append(_insight(
                "padrao", "atencao",
                f"{len(serrotes)} projeto(s) em padrão serrote — invisíveis na comparação par a par",
                (f"Exemplo: {s['projeto']} percorreu {fmt_mi(s['caminho'])} em revisões ao longo de "
                 f"{pad['n_rodadas']} rodadas para um delta líquido de {fmt_mi(s['liquido'])}."),
                "A tendência declarada está sendo construída ou está sendo negociada?",
                max(i["caminho"] for i in serrotes), aba="trajetoria"))
        erosoes = [i for i in pad["itens"] if i["padrao"] == "erosao"]
        if erosoes:
            out.append(_insight(
                "padrao", "critico" if len(erosoes) > 5 else "atencao",
                f"{len(erosoes)} projeto(s) em erosão contínua",
                (f"Revisões para baixo em rodadas consecutivas, somando "
                 f"{fmt_mi(sum(i['liquido'] for i in erosoes))} — não é evento, é comportamento."),
                "O que muda na próxima rodada para a queda parar?",
                sum(i["liquido"] for i in erosoes), aba="trajetoria"))

    # exequibilidade
    ex = exequibilidade(rodada, dim, filtro_geral)
    if ex["razao"]:
        sev = "critico" if ex["razao"] > 3 else ("atencao" if ex["razao"] > 2 else "info")
        out.append(_insight(
            "exequibilidade", sev,
            f"Tendência {DIM_ADJ[dim]} {ex['veredito']} para o calendário restante",
            (f"Faltam {fmt_mi(ex['falta'])} em {ex['meses_restantes']} mês(es): exige "
             f"{fmt_mi(ex['rr_necessario'])}/mês contra {fmt_mi(ex['rr_recente'])}/mês realizados "
             f"nos últimos 3 meses ({ex['razao']:.1f}× o ritmo atual)."),
            ("Qual o plano concreto para multiplicar o ritmo por " f"{ex['razao']:.1f}× até dezembro?"
             if ex["razao"] > 2 else None),
            ex["falta"], aba="exequibilidade"))
    if ex["n_papel"]:
        out.append(_insight(
            "compromisso", "critico" if ex["valor_papel"] > corte * 20 else "atencao",
            f"{fmt_mi(ex['valor_papel'])} de tendência sem nenhum compromisso nem realizado",
            (f"{ex['n_papel']} projeto(s) declaram tendência mas não têm pedido, requisição "
             f"nem realizado — não há caminho para virar caixa no ano."),
            "Estes projetos ainda são tendência ou já deveriam ter saído do ano?",
            ex["valor_papel"], aba="exequibilidade"))
    if ex["n_descolados"]:
        d = ex["descolados"][0]
        out.append(_insight(
            "fisico", "atencao",
            f"{ex['n_descolados']} projeto(s) com descolamento físico × financeiro",
            (f"Maior caso: {d['projeto']} — {d['avanco_fin']:.0f}% financeiro contra "
             f"{d['avanco_fis']:.0f}% físico ({d['sentido']})."),
            "O avanço físico está desatualizado ou o desembolso está adiantado em relação à entrega?",
            d["tend"], aba="exequibilidade"))

    return sorted(out, key=lambda i: -i["peso"])


def veredito(rodada: str, rodada_anterior: str | None = None, dim: str = "fin",
             filtro_geral: dict | None = None) -> dict:
    """A frase que resume a rodada. É o topo da pirâmide invertida: quem
    ler só isto já entendeu o essencial."""
    rodada, rodada_anterior = te._resolver_par(rodada, rodada_anterior)
    label = DIMENSOES[dim]
    p = ponte_rodadas(rodada, rodada_anterior, dim, filtro_geral)
    q = qualidade_rodada(rodada, rodada_anterior, filtro_geral)
    ex = exequibilidade(rodada, dim, filtro_geral)

    if not p.get("disponivel"):
        return {"titulo": f"{rodada} — primeira rodada carregada",
                "frase": "Sem rodada anterior para comparar. Carregue outra rodada para a análise comparativa.",
                "delta": None, "qualidade": q["veredito"], "exequibilidade": ex["veredito"],
                "status": q["status"]}

    direcao = "caiu" if p["delta"] < 0 else "subiu"
    maior = max(p["passos"], key=lambda b: abs(b["valor"])) if p["passos"] else None
    frase = (f"A tendência {DIM_ADJ[dim]} {direcao} {fmt_mi(abs(p['delta']))} ({p['delta_pct']:+.1f}%) "
             f"de {rodada_anterior} para {rodada}"
             + (f", puxada principalmente por {maior['nome']} ({fmt_mi(maior['valor'])})" if maior else "")
             + f". A base está {q['veredito']} e a tendência é {ex['veredito']} para o calendário restante.")
    return {
        "titulo": f"{rodada_anterior} → {rodada} · {label}",
        "frase": frase, "delta": p["delta"], "delta_pct": p["delta_pct"],
        "inicio": p["inicio"], "fim": p["fim"],
        "qualidade": q["veredito"], "qualidade_status": q["status"],
        "exequibilidade": ex["veredito"], "razao": ex["razao"],
        "pct_explicado": p["pct_explicado"],
        "status": q["status"],
    }
