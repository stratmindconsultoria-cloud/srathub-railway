"""Analisador de Tendências — análise comparativa entre rodadas de tendência (forecast) da carteira.

Fonte: rodadas publicadas pela PMP (Data Core do hub: forecast/rodada) ou o JSON da rodada exportado por ela.
Telas: tendência (geral, econômico, financeiro, físico, projeto) e análise (movimento, trajetória,
exequibilidade, qualidade). Unidades, áreas, vocabulário e cores vêm do perfil da organização.
"""
from __future__ import annotations

import datetime as dt
import json, threading, time
import os
import re
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import database as db
import hubkit
import trend_engine as te
from categorias import DESVIO_KEYS, DESVIO_LABELS
from fonte_pmp import FonteInvalida, converter

BASE_DIR = Path(__file__).resolve().parent
APP_ID = "analisador"
APP_NAME = "Analisador de Tendências"
APP_VERSION = "4.1.2"
hubkit.configurar(dados=db.DADOS, config=BASE_DIR / "config")
ORG_PADRAO = {"nome": "Organização Exemplo", "sigla": "ORG", "cor_primaria": "#1e3a6e", "cor_destaque": "#0f6e56",
              "vocabulario": {"codigo_erp": "Código ERP", "forecast": "Tendência", "rodada": "Rodada", "unidade": "Unidade",
                              "area": "Área", "pmo": "PMO", "plano_sigla": "PPI"}}

from contextlib import asynccontextmanager


@asynccontextmanager
async def _vida(_app):
    db.init_db()
    if not getattr(_vida, "laco", False):
        _vida.laco = True
        threading.Thread(target=_laco_sync, name="sync-analisador", daemon=True).start()
    yield

app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=_vida)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


def _org() -> dict:
    return hubkit.organizacao(ORG_PADRAO)


def _rotulos(o: dict) -> dict:
    """Termos internos herdados (snapshot) → termos exibidos para a organização."""
    v = {**ORG_PADRAO["vocabulario"], **(o.get("vocabulario") or {})}
    base = {"Vertical Gestora": v["area"], "Verticais Gestoras": v["area"] + "s", "Vertical": v["area"], "Verticais": v["area"] + "s",
            "Diretoria Engenharia": "Gerente", "Diretoria de Engenharia": "Gerente", "Núcleo": "Grupo", "PEP Pai": "Projeto pai",
            "PEPs": "projetos", "PEP": "Projeto", "Unidade": v["unidade"], "Unidades": v["unidade"] + "s",
            "Tendência": v["forecast"], "Tendências": v["forecast"] + "s", "QQ": "horizonte", "quinquênio": "horizonte", "Quinquênio": "Horizonte"}
    try:
        base.update(json.loads((BASE_DIR / "config" / "analisador.json").read_text(encoding="utf-8")).get("rotulos") or {})
    except (OSError, ValueError):
        pass
    return {k: t for k, t in base.items() if t and t != k}


def _template_context(request: Request) -> dict:
    o = _org()
    cor1 = o.get("cor_primaria") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(o.get("cor_primaria") or "")) else "#1e3a6e"
    cor2 = o.get("cor_destaque") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(o.get("cor_destaque") or "")) else "#0f6e56"
    return {"hub_base": (request.scope.get("root_path") or "").rstrip("/"), "org_nome": o.get("nome") or "", "org_sigla": o.get("sigla") or "",
            "cor1": cor1, "cor2": cor2, "rotulos_json": json.dumps(_rotulos(o), ensure_ascii=False), "modo_hub": hubkit.no_hub()}


templates = Jinja2Templates(directory=str(BASE_DIR / "templates"), context_processors=[_template_context])


@app.get("/health")
def health():
    return {"status": "ok", "app_id": APP_ID, "name": APP_NAME, "version": APP_VERSION}


def _pode_gerenciar(request: Request) -> bool:
    """No hub, importar/excluir rodadas é de administradores do hub; sozinha, a ferramenta é local e livre."""
    if not hubkit.no_hub():
        return True
    u = hubkit.identidade(request.headers)
    return bool(u and u.get("papel") in ("dono", "admin"))


# ── carregador ────────────────────────────────────────────────────────────
def _pendentes() -> list[dict]:
    feitas = {r["rodada"]: r for r in db.listar_rodadas()}
    try:
        regs = hubkit.consultar("forecast", "rodada", limit=200)
    except Exception:
        return []
    out = []
    for r in regs:
        f = feitas.get(r["entity_key"])
        if not f or (f.get("versao_declarada") or "") != f"v{r.get('version')}·{(r.get('payload') or {}).get('nome', '')}":
            out.append({"rodada": r["entity_key"], "nome": (r.get("payload") or {}).get("nome"), "versao": r.get("version"),
                        "atualizado": r.get("updated_at"), "ja_carregada": bool(f)})
    return out


def _tela_inicial(request: Request, resultado=None):
    hub = hubkit.no_hub()
    return templates.TemplateResponse(request, "upload.html", {"rodadas": db.listar_rodadas(), "resultado": resultado,
                                                                "pendentes": _pendentes(), "pode_gerenciar": _pode_gerenciar(request),
                                                                "sync": SYNC, "rodada_aberta": _rodada_aberta_pmp() if hub else None,
                                                                "sync_ha": _ha(SYNC.get("ultimo"))})


def _ha(t) -> str:
    if not t:
        return "ainda não sincronizado"
    s = max(0, time.time() - t)
    return "sincronizado agora há pouco" if s < 60 else f"sincronizado há {round(s / 60)} min" if s < 3600 else f"sincronizado há {round(s / 3600)} h"


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    resultado = None
    if hubkit.no_hub() and (SYNC["ultimo"] is None or _pendentes()):
        r = receber_rodadas()  # abriu a ferramenta: o que a PMP publicou já entra
        resultado = r if r["recebidas"] or r["erro"] else None
    return _tela_inicial(request, resultado)


def _gravar(snap: dict, bruto: bytes, origem: str, versao: int | None = None) -> dict:
    rid, meta, projetos = converter(snap, BASE_DIR)
    if versao is not None:
        meta.versao_declarada = f"v{versao}·{snap.get('nome', '')}"
    existia = any(r["rodada"] == rid for r in db.listar_rodadas())
    db.salvar_rodada(rid, origem, meta, projetos, bruto, "FINAL")
    return {"ok": True, "rodada": rid, "substituiu": existia, "meta": meta, "totais": db.totais_rodada(rid)}


@app.post("/importar", response_class=HTMLResponse)
async def importar(request: Request, arquivo: UploadFile = File(...)):
    """Modo sem hub: JSON da rodada exportado pela PMP (Rodadas › Exportar JSON)."""
    if not _pode_gerenciar(request):
        raise HTTPException(403, "Apenas administradores do hub importam rodadas.")
    bruto = await arquivo.read()
    try:
        snap = json.loads(bruto.decode("utf-8"))
        resultado = _gravar(snap.get("payload", snap), bruto, arquivo.filename or "arquivo.json")
    except (ValueError, UnicodeDecodeError):
        resultado = {"ok": False, "erro": "O arquivo não é um JSON válido."}
    except FonteInvalida as e:
        resultado = {"ok": False, "erro": str(e)}
    return _tela_inicial(request, resultado)


SYNC = {"ultimo": None, "ok": None, "resultado": None, "erro": None, "automatico": True, "intervalo_s": int(os.environ.get("SYNC_INTERVALO_S") or 120)}
_sync_lock = threading.Lock()


def receber_rodadas() -> dict:
    """Fluxo do ecossistema: toda rodada fechada na PMP entra sozinha no Analisador (sem carga de arquivo)."""
    recebidas, erros = [], []
    with _sync_lock:
        try:
            for pend in _pendentes():
                try:
                    r = hubkit.consultar("forecast", "rodada", entity_key=pend["rodada"], limit=1)[0]
                    res = _gravar(r["payload"], json.dumps(r["payload"], ensure_ascii=False).encode(), "Rodada publicada pela PMP", r.get("version"))
                    recebidas.append(res["rodada"])
                except (FonteInvalida, IndexError, KeyError) as e:
                    erros.append(f"{pend['rodada']}: {e}")
            erro = "; ".join(erros) if erros else None
        except Exception as e:
            erro = str(e)
        SYNC.update(ultimo=time.time(), ok=erro is None, erro=erro, resultado={"rodadas": len(recebidas)})
    return {"ok": not erros, "sincronizacao": True, "recebidas": recebidas, "erro": SYNC["erro"]}


def _laco_sync() -> None:
    time.sleep(3)
    while True:
        if hubkit.no_hub():
            receber_rodadas()
        time.sleep(SYNC["intervalo_s"])


def _rodada_aberta_pmp() -> str | None:
    """A PMP publica na agenda as rodadas em andamento; o Analisador avisa que a próxima ainda vai chegar."""
    try:
        for r in hubkit.consultar("agenda", "pendencia", limit=500):
            p = r.get("payload") or {}
            if p.get("origem_app") == "pmp" and p.get("status") == "aberta" and p.get("tipo") in ("rodada", "aprovacao") and str(r["entity_key"]).startswith("pmp:"):
                return str(p.get("titulo") or "").split(":")[0]
    except Exception:
        return None
    return None


@app.post("/sincronizar", response_class=HTMLResponse)
def sincronizar(request: Request):
    """Recebe agora (o recebimento também é automático no hub)."""
    return _tela_inicial(request, receber_rodadas())


@app.post("/api/sincronizar")
def api_sincronizar():
    return receber_rodadas()


@app.get("/api/sync/status")
def sync_status():
    return {**SYNC, "modo": "hub" if hubkit.no_hub() else "isolado", "agora": time.time()}


@app.post("/rodadas/{rodada}/excluir")
def excluir(request: Request, rodada: str):
    if not _pode_gerenciar(request):
        raise HTTPException(403, "Apenas administradores do hub excluem rodadas.")
    db.excluir_rodada(rodada)
    base = (request.scope.get("root_path") or "").rstrip("/")
    return RedirectResponse(url=f"{base}/", status_code=303)


# ── filtro dinâmico do cabeçalho ─────────────────────────────────────────
def _ler_filtro(filtro_campo: str | None, filtro_valores: str | None) -> dict | None:
    if not filtro_campo or filtro_campo not in te.FILTRO_CAMPOS or not filtro_valores:
        return None
    valores = [v for v in filtro_valores.split("|") if v]
    if not valores:
        return None
    return {"campo": filtro_campo, "valores": valores}


def _qs_filtro(filtro_campo: str | None, filtro_valores: str | None) -> str:
    if not filtro_campo or not filtro_valores:
        return ""
    return f"&filtro_campo={filtro_campo}&filtro_valores={filtro_valores}"


# ── telas de tendência ───────────────────────────────────────────────────
def _resolver_rodadas(rodada: str | None, comparar: str | None):
    rodadas = db.listar_rodadas()
    if not rodadas:
        return rodadas, None, None
    labels = [r["rodada"] for r in rodadas]
    rodada = rodada if rodada in labels else labels[-1]
    comparar = comparar if comparar in labels and comparar != rodada else None
    return rodadas, rodada, comparar


def _ctx_filtro_ui(filtro_campo, filtro_valores_raw):
    """Contexto pro widget de filtro dinâmico: dimensão + checkboxes."""
    valores_selecionados = set((filtro_valores_raw or "").split("|")) if filtro_valores_raw else set()
    opcoes = te.valores_disponiveis(filtro_campo) if filtro_campo in te.FILTRO_CAMPOS else []
    return {
        "filtro_campos": te.FILTRO_CAMPOS,
        "filtro_campo": filtro_campo or "",
        "filtro_opcoes": opcoes,
        "filtro_selecionados": valores_selecionados,
        "filtro_valores_raw": filtro_valores_raw or "",
        "filtro_ativo": bool(filtro_campo and valores_selecionados),
    }


@app.get("/tendencia")
def tendencia_root(request: Request, rodada: str | None = None, comparar: str | None = None):
    qs = ""
    if rodada:
        qs += f"?rodada={rodada}"
        if comparar:
            qs += f"&comparar={comparar}"
    base = (request.scope.get("root_path") or "").rstrip("/")
    return RedirectResponse(url=f"{base}/tendencia/geral{qs}")


@app.get("/tendencia/geral", response_class=HTMLResponse)
def tendencia_geral(request: Request, rodada: str | None = None, comparar: str | None = None,
                     filtro_campo: str | None = None, filtro_valores: str | None = None):
    rodadas, rodada, comparar = _resolver_rodadas(rodada, comparar)
    if not rodadas:
        return templates.TemplateResponse(request, "tendencia_vazio.html", {"aba": "geral"})
    fg = _ler_filtro(filtro_campo, filtro_valores)

    dp = te.delta_portfolio(rodada, comparar, filtro_geral=fg)
    df = te.delta_fisico(rodada, comparar, filtro_geral=fg)
    insights = te.gerar_insights(rodada, comparar, filtro_geral=fg)
    du = te.delta_unidades(rodada, comparar, filtro_geral=fg)[:6]
    top_peps = te.delta_peps(rodada, comparar, top_n=6, filtro_geral=fg)

    chart_data = {
        "labels": [s["rodada"] for s in dp["serie"]],
        "econ": {"orc": [s["econ_orc_ano"] for s in dp["serie"]], "tend": [s["econ_tend_ano"] for s in dp["serie"]], "real": [s["econ_real_acum"] for s in dp["serie"]]},
        "fin": {"orc": [s["fin_orc_ano"] for s in dp["serie"]], "tend": [s["fin_tend_ano"] for s in dp["serie"]], "real": [s["fin_real_acum"] for s in dp["serie"]]},
        "fisico": {"baseline": [s["baseline_pct"] for s in df["serie"]], "real": [s["real_pct"] for s in df["serie"]]},
    }

    ctx = {
        "aba": "geral", "rodadas": rodadas, "rodada_atual": rodada, "rodada_comparada": comparar or dp["rodada_anterior"],
        "dp": dp, "df": df, "insights": insights, "du": du, "top_peps": top_peps,
        "chart_data_json": json.dumps(chart_data),
    }
    ctx.update(_ctx_filtro_ui(filtro_campo, filtro_valores))
    ctx["qs_filtro"] = _qs_filtro(filtro_campo, filtro_valores)
    return templates.TemplateResponse(request, "tendencia_geral.html", ctx)


@app.get("/tendencia/fisico", response_class=HTMLResponse)
def tendencia_fisico(request: Request, rodada: str | None = None, comparar: str | None = None,
                      filtro_campo: str | None = None, filtro_valores: str | None = None):
    rodadas, rodada, comparar = _resolver_rodadas(rodada, comparar)
    if not rodadas:
        return templates.TemplateResponse(request, "tendencia_vazio.html", {"aba": "fisico"})
    fg = _ler_filtro(filtro_campo, filtro_valores)

    df = te.delta_fisico(rodada, comparar, filtro_geral=fg)
    fu = te.fisico_por_unidade(rodada, comparar, filtro_geral=fg)
    insights = [i for i in te.gerar_insights(rodada, comparar, filtro_geral=fg) if "físic" in i.lower() or "peso" in i.lower()]
    if not insights:
        insights = ["Sem variação física relevante detectada entre as rodadas comparadas."]

    chart_data = {
        "labels": [s["rodada"] for s in df["serie"]],
        "baseline": [s["baseline_pct"] for s in df["serie"]],
        "real": [s["real_pct"] for s in df["serie"]],
        "n_com_peso": [s["n_com_peso"] for s in df["serie"]],
    }

    ctx = {
        "aba": "fisico", "rodadas": rodadas, "rodada_atual": rodada, "rodada_comparada": comparar or df["rodada_anterior"],
        "df": df, "fu": fu, "insights": insights, "chart_data_json": json.dumps(chart_data),
    }
    ctx.update(_ctx_filtro_ui(filtro_campo, filtro_valores))
    ctx["qs_filtro"] = _qs_filtro(filtro_campo, filtro_valores)
    return templates.TemplateResponse(request, "tendencia_fisico.html", ctx)


def _tendencia_dimensao(request: Request, dimensao: str, rodada, comparar, filtro_campo, filtro_valores, categoria):
    rodadas, rodada, comparar = _resolver_rodadas(rodada, comparar)
    if not rodadas:
        return templates.TemplateResponse(request, "tendencia_vazio.html", {"aba": dimensao})
    fg = _ler_filtro(filtro_campo, filtro_valores)

    dp = te.delta_portfolio(rodada, comparar, filtro_geral=fg)
    cs = te.categoria_serie(dimensao, filtro_geral=fg)
    du = te.delta_unidades(rodada, comparar, indicador=f"{dimensao}_tend_ano", filtro_geral=fg)
    top_peps = te.delta_peps(rodada, comparar, indicador=f"{dimensao}_tend_ano", top_n=12, filtro_geral=fg)

    categoria = categoria if categoria in DESVIO_KEYS else "postergado"
    top_peps_categoria = te.delta_peps_categoria(rodada, comparar, categoria=categoria, dimensao=dimensao, top_n=12, filtro_geral=fg)
    consecutivos = te.peps_categoria_consecutiva(rodada, categoria=categoria, dimensao=dimensao, filtro_geral=fg)

    label_dim = "Financeiro" if dimensao == "fin" else "Econômico"
    insights = [i for i in te.gerar_insights(rodada, comparar, filtro_geral=fg) if label_dim.lower() in i.lower() or "desvio" in i.lower() or "confronto" in i.lower()]
    if not insights:
        insights = [f"Sem variação {label_dim.lower()} relevante detectada entre as rodadas comparadas."]

    cats_ordenadas = sorted(DESVIO_KEYS.keys(), key=lambda k: abs(cs[-1]["cats"].get(k, 0.0)), reverse=True)
    cats_ordenadas = [k for k in cats_ordenadas if any(abs(r["cats"].get(k, 0)) > 1e4 for r in cs)]

    confronto = te.confronto_meses_fechados(rodada, comparar, dimensao=dimensao, filtro_geral=fg)

    chart_data = {
        "labels": [s["rodada"] for s in dp["serie"]],
        "orc": [s[f"{dimensao}_orc_ano"] for s in dp["serie"]],
        "tend": [s[f"{dimensao}_tend_ano"] for s in dp["serie"]],
        "real": [s[f"{dimensao}_real_acum"] for s in dp["serie"]],
        "cats_labels": [c["rodada"] for c in cs],
        "cats_series": {DESVIO_LABELS[k]: [c["cats"].get(k, 0.0) for c in cs] for k in cats_ordenadas[:8]},
    }

    ctx = {
        "aba": dimensao, "dimensao": dimensao, "label_dim": label_dim,
        "rodadas": rodadas, "rodada_atual": rodada, "rodada_comparada": comparar or dp["rodada_anterior"],
        "dp": dp, "cs": cs, "cats_ordenadas": cats_ordenadas, "du": du, "top_peps": top_peps,
        "categoria": categoria, "categoria_label": DESVIO_LABELS.get(categoria, categoria),
        "categorias_disponiveis": DESVIO_KEYS, "top_peps_categoria": top_peps_categoria,
        "consecutivos": consecutivos, "insights": insights, "desvio_labels": DESVIO_LABELS,
        "confronto": confronto,
        "chart_data_json": json.dumps(chart_data),
    }
    if dimensao == "econ":
        dc = te.delta_compromisso(rodada, comparar, filtro_geral=fg)
        pct_comprometido = None
        orc = dp["indicadores"]["econ_orc_ano"]["atual"]
        if orc and dc["campos"]["comp_ped_ano"]["atual"] is not None:
            pct_comprometido = dc["campos"]["comp_ped_ano"]["atual"] / orc * 100
        ctx["compromisso"] = dc
        ctx["compromisso_pct"] = pct_comprometido
        ctx["sem_compromisso"] = te.sem_compromisso_lista(rodada, filtro_geral=fg)

    ctx.update(_ctx_filtro_ui(filtro_campo, filtro_valores))
    ctx["qs_filtro"] = _qs_filtro(filtro_campo, filtro_valores)
    return templates.TemplateResponse(request, "tendencia_dimensao.html", ctx)


@app.get("/tendencia/economico", response_class=HTMLResponse)
def tendencia_economico(request: Request, rodada: str | None = None, comparar: str | None = None,
                         filtro_campo: str | None = None, filtro_valores: str | None = None,
                         categoria: str | None = None):
    return _tendencia_dimensao(request, "econ", rodada, comparar, filtro_campo, filtro_valores, categoria)


@app.get("/tendencia/financeiro", response_class=HTMLResponse)
def tendencia_financeiro(request: Request, rodada: str | None = None, comparar: str | None = None,
                          filtro_campo: str | None = None, filtro_valores: str | None = None,
                          categoria: str | None = None):
    return _tendencia_dimensao(request, "fin", rodada, comparar, filtro_campo, filtro_valores, categoria)


@app.get("/tendencia/pep/{pep:path}", response_class=HTMLResponse)
def pep_detail(request: Request, pep: str):
    serie = te.series_pep(pep)
    if not serie:
        raise HTTPException(404, f"PEP {pep} não encontrado em nenhuma rodada carregada")
    chart_data = {
        "labels": [s["rodada"] for s in serie],
        "fin_orc": [s["fin_orc_ano"] for s in serie],
        "fin_tend": [s["fin_tend_ano"] for s in serie],
    }
    return templates.TemplateResponse(request, "pep_detail.html", {
        "pep": pep, "serie": serie, "desvio_labels": DESVIO_LABELS,
        "rodadas_postergando": te.postergacao_consecutiva(pep),
        "chart_data_json": json.dumps(chart_data),
    })


# ══════════════════════════════════════════════════════════════════════════
# TELAS ANALÍTICAS — organizadas por pergunta, não por dimensão.
#
#   /analise/qualidade      → posso confiar nesta base?   (pergunta zero)
#   /analise/movimento      → o que mudou e por quê?
#   /analise/trajetoria     → é padrão ou é evento?
#   /analise/exequibilidade → a tendência declarada é crível?
#   /analise/painel         → estado da carteira na rodada (painel embutido)
#
# A dimensão (Econômico/Financeiro) deixou de ser aba e virou toggle
# transversal: a mesma análise deixa de ser escrita duas vezes.
# ══════════════════════════════════════════════════════════════════════════
import analitico as an

templates.env.filters["mi"] = an.fmt_mi


def _heat(delta):
    """Cor de fundo da célula da matriz vintage, proporcional ao movimento."""
    if not delta:
        return "transparent"
    escala = min(abs(delta) / 5e7, 1.0)
    alpha = 0.08 + escala * 0.28
    return (f"rgba(185,28,28,{alpha:.2f})" if delta < 0 else f"rgba(37,99,235,{alpha:.2f})")


def _spark(serie: list[float], w: int = 96, h: int = 24) -> str:
    """Sparkline SVG inline — sem dependência, sem requisição extra."""
    if not serie or len(serie) < 2:
        return ""
    lo, hi = min(serie), max(serie)
    span = (hi - lo) or 1.0
    pts = " ".join(
        f"{i / (len(serie) - 1) * (w - 2) + 1:.1f},{h - 2 - (v - lo) / span * (h - 4):.1f}"
        for i, v in enumerate(serie))
    cor = "#B91C1C" if serie[-1] < serie[0] else "#0F6E56"
    return (f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<polyline points="{pts}" fill="none" stroke="{cor}" stroke-width="1.6"/></svg>')


def _ctx_analise(request, aba, rodada, comparar, dim, corte, filtro_campo, filtro_valores):
    """Contexto comum das telas analíticas."""
    rodadas, rodada, comparar = _resolver_rodadas(rodada, comparar)
    if not rodadas:
        return None
    dim = dim if dim in an.DIMENSOES else "fin"
    try:
        corte_mi = float(str(corte).replace(",", ".")) if corte is not None else 0.5
    except ValueError:
        corte_mi = 0.5
    corte_abs = max(corte_mi, 0.0) * 1e6
    fg = _ler_filtro(filtro_campo, filtro_valores)
    qs_filtro = _qs_filtro(filtro_campo, filtro_valores)

    ctx = {
        "aba": aba, "rodadas": rodadas, "rodada_atual": rodada, "rodada_comparada": comparar,
        "dim": dim, "label_dim": an.DIMENSOES[dim],
        "corte": corte_abs, "corte_mi": corte_mi,
        "filtro_geral": fg, "qs_filtro": qs_filtro,
        "qs_base": (f"?rodada={rodada}&comparar={comparar or ''}&dim={dim}"
                    f"&corte={corte_mi}{qs_filtro}"),
        "desvio_labels": DESVIO_LABELS, "tipos": an.TIPOS_MOVIMENTO,
        "heat": _heat, "spark": _spark,
    }
    ctx.update(_ctx_filtro_ui(filtro_campo, filtro_valores))
    return ctx


@app.get("/analise")
def analise_root(request: Request, rodada: str | None = None, comparar: str | None = None, dim: str = "fin"):
    qs = f"?rodada={rodada or ''}&comparar={comparar or ''}&dim={dim}"
    base = (request.scope.get("root_path") or "").rstrip("/")
    return RedirectResponse(url=f"{base}/analise/movimento{qs}")


@app.get("/analise/movimento", response_class=HTMLResponse)
def analise_movimento(request: Request, rodada: str | None = None, comparar: str | None = None,
                      dim: str = "fin", corte: str | None = None,
                      filtro_campo: str | None = None, filtro_valores: str | None = None):
    ctx = _ctx_analise(request, "movimento", rodada, comparar, dim, corte, filtro_campo, filtro_valores)
    if ctx is None:
        return templates.TemplateResponse(request, "tendencia_vazio.html", {"aba": "movimento"})
    r, c, d, fg, corte_abs = (ctx["rodada_atual"], ctx["rodada_comparada"], ctx["dim"],
                              ctx["filtro_geral"], ctx["corte"])

    cat = an.catalogo_movimentos(r, c, d, fg, corte_abs)
    ponte = an.ponte_rodadas(r, c, d, fg)
    ctx.update({
        "cat": cat, "ponte": ponte,
        "vd": an.veredito(r, c, d, fg),
        "insights": [i for i in an.gerar_insights(r, c, d, fg, corte_abs)
                     if i["aba"] in ("movimento", "qualidade")][:8],
        "ponte_json": json.dumps(ponte if ponte.get("disponivel") else {"passos": [], "inicio": 0, "fim": 0}),
        "rodada_comparada": cat["rodada_anterior"],
    })
    return templates.TemplateResponse(request, "analise_movimento.html", ctx)


@app.get("/analise/trajetoria", response_class=HTMLResponse)
def analise_trajetoria(request: Request, rodada: str | None = None, comparar: str | None = None,
                       dim: str = "fin", corte: str | None = None,
                       filtro_campo: str | None = None, filtro_valores: str | None = None,
                       agrupar: str = "unidade"):
    ctx = _ctx_analise(request, "trajetoria", rodada, comparar, dim, corte, filtro_campo, filtro_valores)
    if ctx is None:
        return templates.TemplateResponse(request, "tendencia_vazio.html", {"aba": "trajetoria"})
    r, c, d, fg, corte_abs = (ctx["rodada_atual"], ctx["rodada_comparada"], ctx["dim"],
                              ctx["filtro_geral"], ctx["corte"])

    vintage = an.matriz_vintage(d, fg)
    padroes = an.padroes_trajetoria(d, fg, corte_abs)
    cred = an.credibilidade(d, agrupar if agrupar in te.FILTRO_CAMPOS else "unidade", fg)

    serie = {
        "labels": [l["rodada"] for l in vintage["linhas"]],
        "orc": [l["orc_ano"] for l in vintage["linhas"]],
        "tend": [l["anos"].get(str(l.get("ano") or ""), 0.0) for l in vintage["linhas"]],
        "real": [l["real_acum"] for l in vintage["linhas"]],
    }

    if padroes["suficiente"]:
        n_serrote = sum(1 for i in padroes["itens"] if i["padrao"] == "serrote")
        n_erosao = sum(1 for i in padroes["itens"] if i["padrao"] == "erosao")
        frase = (f"Ao longo de {padroes['n_rodadas']} rodadas, {padroes['total_itens']} projeto(s) tiveram "
                 f"movimento acima do corte — {n_erosao} em erosão contínua e {n_serrote} em padrão serrote, "
                 f"que a comparação entre duas rodadas não enxerga.")
    else:
        frase = (f"Há {padroes['n_rodadas']} rodada(s) na base. A partir de 3 rodadas esta tela passa a "
                 f"classificar padrão de comportamento e viés de previsão por unidade.")

    ctx.update({
        "vintage": vintage, "padroes": padroes, "cred": cred,
        "frase_trajetoria": frase, "serie_json": json.dumps(serie),
        "vd": an.veredito(r, c, d, fg),
        "insights": [i for i in an.gerar_insights(r, c, d, fg, corte_abs)
                     if i["aba"] == "trajetoria"][:6],
    })
    return templates.TemplateResponse(request, "analise_trajetoria.html", ctx)


@app.get("/analise/exequibilidade", response_class=HTMLResponse)
def analise_exequibilidade(request: Request, rodada: str | None = None, comparar: str | None = None,
                           dim: str = "fin", corte: str | None = None,
                           filtro_campo: str | None = None, filtro_valores: str | None = None):
    ctx = _ctx_analise(request, "exequibilidade", rodada, comparar, dim, corte, filtro_campo, filtro_valores)
    if ctx is None:
        return templates.TemplateResponse(request, "tendencia_vazio.html", {"aba": "exequibilidade"})
    r, c, d, fg, corte_abs = (ctx["rodada_atual"], ctx["rodada_comparada"], ctx["dim"],
                              ctx["filtro_geral"], ctx["corte"])

    ex = an.exequibilidade(r, d, fg)
    projetos = te._projetos(r, fg)
    comp_ped = sum(float(p.get("comp_ped_ano") or 0) for p in projetos)
    comp_req = sum(float(p.get("comp_req_ano") or 0) for p in projetos)

    if ex["razao"]:
        frase = (f"Faltam {an.fmt_mi(ex['falta'])} para atingir a tendência declarada, com "
                 f"{ex['meses_restantes']} mês(es) pela frente: são {an.fmt_mi(ex['rr_necessario'])} por mês "
                 f"contra {an.fmt_mi(ex['rr_recente'])} de ritmo realizado — {ex['razao']:.1f}× o atual. "
                 f"A tendência é {ex['veredito']}.")
    else:
        frase = (f"Faltam {an.fmt_mi(ex['falta'])} para atingir a tendência declarada. Sem mês de análise ou "
                 f"sem realizado mensal nesta rodada, não dá para avaliar o ritmo necessário.")

    ctx.update({
        "ex": ex, "frase_ex": frase,
        "ex_json": json.dumps({
            "rr_recente": ex["rr_recente"], "rr_necessario": ex["rr_necessario"] or 0,
            "comp_ped": comp_ped, "comp_req": comp_req,
            "descoberto": max(ex["tend"] - comp_ped - comp_req, 0),
        }),
        "insights": [i for i in an.gerar_insights(r, c, d, fg, corte_abs)
                     if i["aba"] == "exequibilidade"][:6],
    })
    return templates.TemplateResponse(request, "analise_exequibilidade.html", ctx)


@app.get("/analise/qualidade", response_class=HTMLResponse)
def analise_qualidade(request: Request, rodada: str | None = None, comparar: str | None = None,
                      dim: str = "fin", corte: str | None = None,
                      filtro_campo: str | None = None, filtro_valores: str | None = None):
    ctx = _ctx_analise(request, "qualidade", rodada, comparar, dim, corte, filtro_campo, filtro_valores)
    if ctx is None:
        return templates.TemplateResponse(request, "tendencia_vazio.html", {"aba": "qualidade"})
    r, c, d, fg, corte_abs = (ctx["rodada_atual"], ctx["rodada_comparada"], ctx["dim"],
                              ctx["filtro_geral"], ctx["corte"])

    q = an.qualidade_rodada(r, c, fg, corte_abs)
    carregadas = db.verticais_da_rodada(r)
    ctx.update({
        "q": q,
        "confronto": te.confronto_meses_fechados(r, c, d, fg),
        "verticais_carregadas": carregadas,
        "verticais_faltando": sorted(set(db.todas_verticais_conhecidas()) - set(carregadas)),
        "insights": [i for i in an.gerar_insights(r, c, d, fg, corte_abs)
                     if i["aba"] == "qualidade"][:8],
    })
    return templates.TemplateResponse(request, "analise_qualidade.html", ctx)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=os.environ.get("ANALISADOR_HOST", "127.0.0.1"), port=int(os.environ.get("PORT") or 8840),
                root_path=os.environ.get("HUB_ROOT_PATH", ""))
