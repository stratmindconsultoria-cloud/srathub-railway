"""Perfil da organização: um único lugar para nome, unidades, áreas, vocabulário e marca.
Todos os apps leem daqui (GET /api/hub/organizacao) — trocar de organização é trocar este arquivo."""
from __future__ import annotations
import json, os, threading

from . import config

ARQ = config.DATA_DIR / "organizacao.json"
_lock = threading.Lock()

PADRAO = {
    "nome": "Organização Exemplo",
    "sigla": "ORG",
    "moeda": "BRL",
    "cor_primaria": "#1e3a6e",
    "cor_destaque": "#0f6e56",
    "unidades": [
        {"codigo": "UN-NORTE", "nome": "Unidade Norte", "grupo": "Regional A"},
        {"codigo": "UN-SUL", "nome": "Unidade Sul", "grupo": "Regional A"},
        {"codigo": "UN-LESTE", "nome": "Unidade Leste", "grupo": "Regional B"},
        {"codigo": "CORP", "nome": "Corporativo", "grupo": "Corporativo"},
    ],
    "areas": ["Engenharia", "Operações", "Tecnologia", "Manutenção", "Corporativo"],
    "vocabulario": {
        "plano_sigla": "PPI",
        "plano_nome": "Plano Plurianual de Investimentos",
        "ciclo": "Ciclo de Planejamento",
        "codigo_erp": "Código ERP",
        "termo_abertura": "TAP",
        "termo_abertura_nome": "Termo de Abertura do Projeto",
        "aprovador": "Aprovador Executivo",
        "forecast": "Tendência",
        "rodada": "Rodada",
        "unidade": "Unidade",
        "area": "Área",
        "capex": "CAPEX",
        "pmo": "PMO",
    },
    "localidades": [],            # [{"codigo": "SP-CAPITAL", "nome": "São Paulo/SP", "feriados": [{"data": "01-25", "nome": "Aniversário da cidade"}]}]
    "feriados_facultativos": False,  # Carnaval, Cinzas e Corpus Christi contam como feriado?
    "ano_base": 2026,
    "horizonte_anos": 5,
    "taxa_desconto_aa": 0.10,
}


def ler() -> dict:
    d = json.loads(json.dumps(PADRAO))
    if ARQ.exists():
        try:
            salvo = json.loads(ARQ.read_text(encoding="utf-8"))
            voc = {**d["vocabulario"], **(salvo.get("vocabulario") or {})}
            d.update(salvo)
            d["vocabulario"] = voc
        except Exception:
            pass
    return d


def gravar(novo: dict) -> dict:
    if not isinstance(novo, dict):
        raise ValueError("Formato inválido")
    d = ler()
    for k in ("nome", "sigla", "moeda", "cor_primaria", "cor_destaque"):
        if k in novo:
            d[k] = str(novo[k] or "").strip()[:120] or d[k]
    if "unidades" in novo:
        unis = []
        for u in novo["unidades"] or []:
            if isinstance(u, dict) and str(u.get("codigo", "")).strip():
                unis.append({"codigo": str(u["codigo"]).strip()[:40], "nome": str(u.get("nome") or u["codigo"]).strip()[:120],
                             "grupo": str(u.get("grupo") or "").strip()[:120], "localidade": str(u.get("localidade") or "").strip()[:40] or None})
        if not unis:
            raise ValueError("Cadastre ao menos uma unidade")
        d["unidades"] = unis
    if "areas" in novo:
        d["areas"] = [str(a).strip()[:120] for a in novo["areas"] or [] if str(a).strip()]
    if "vocabulario" in novo and isinstance(novo["vocabulario"], dict):
        d["vocabulario"].update({str(k)[:60]: str(v)[:120] for k, v in novo["vocabulario"].items() if str(v).strip()})
    if "localidades" in novo:
        locs = []
        for l in novo["localidades"] or []:
            if not isinstance(l, dict) or not str(l.get("codigo", "")).strip():
                continue
            fer = []
            for f in l.get("feriados") or []:
                dia = str((f or {}).get("data") or "").strip()
                if len(dia) in (5, 10) and dia[-3] == "-":
                    fer.append({"data": dia, "nome": str(f.get("nome") or "Feriado local").strip()[:120]})
            locs.append({"codigo": str(l["codigo"]).strip()[:40], "nome": str(l.get("nome") or l["codigo"]).strip()[:120], "feriados": fer})
        d["localidades"] = locs
    if "feriados_facultativos" in novo:
        d["feriados_facultativos"] = bool(novo["feriados_facultativos"])
    for k, t in (("ano_base", int), ("horizonte_anos", int), ("taxa_desconto_aa", float)):
        if k in novo and novo[k] not in (None, ""):
            d[k] = t(novo[k])
    with _lock:
        ARQ.parent.mkdir(parents=True, exist_ok=True)
        tmp = ARQ.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, ARQ)
    return d
