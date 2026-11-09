"""Converte a rodada publicada pela PMP (Data Core: forecast/rodada, ou o JSON exportado) no snapshot do Analisador."""
from __future__ import annotations
from pathlib import Path

from categorias import MES_NOME, MetaRodada, ProjetoSnapshot, chave_categoria, mapa_categorias


class FonteInvalida(Exception):
    pass


def _m12(v) -> list[float]:
    v = [float(x or 0) for x in (v or [])][:12]
    return v + [0.0] * (12 - len(v))


def _desvios(lista, mapa) -> dict[str, float]:
    out: dict[str, float] = {}
    for d in lista or []:
        k = chave_categoria(d.get("categoria", ""), mapa)
        out[k] = out.get(k, 0.0) + float(d.get("valor") or 0)
    return out


def converter(snap: dict, base_dir: Path) -> tuple[str, MetaRodada, list[ProjetoSnapshot]]:
    try:
        rid, ano, mes = str(snap["rodada"]), int(snap["ano"]), int(snap["mes_referencia"])
        projetos = snap["projetos"]
    except (KeyError, TypeError, ValueError):
        raise FonteInvalida("Arquivo não é uma rodada publicada pela PMP (faltam rodada, ano, mes_referencia ou projetos).")
    mapa = mapa_categorias(base_dir)
    out, avisos = [], []
    for p in projetos:
        if p.get("status") == "cancelado":
            continue
        econ, fin, fis = p.get("econ") or {}, p.get("fin") or {}, p.get("fis") or {}
        def bloco(d, chave):
            orc, real = _m12(d.get("orcado")), _m12(d.get("realizado"))
            tend = _m12(d.get("tendencia")) if d.get("tendencia") else [real[i] if i < mes else orc[i] for i in range(12)]
            orc_anos = {str(ano): sum(orc), **{str(k): float(v or 0) for k, v in (d.get("orcado_anos_futuros") or {}).items()}}
            tend_anos = {str(ano): sum(tend), **{str(k): float(v or 0) for k, v in ((d.get("tendencia_anos_futuros") or d.get("orcado_anos_futuros")) or {}).items()}}
            r = {f"{chave}_orc_ano": sum(orc), f"{chave}_tend_ano": sum(tend), f"{chave}_orc_acum": sum(orc[:mes]),
                 f"{chave}_real_acum": sum(real[:mes]), f"{chave}_delta_acum": sum(real[:mes]) - sum(orc[:mes]),
                 f"{chave}_desvios": _desvios(d.get("desvios"), mapa), f"{chave}_orc_mensal": orc, f"{chave}_real_mensal": real,
                 f"{chave}_tend_mensal": tend, f"{chave}_orc_anos": orc_anos, f"{chave}_tend_anos": tend_anos,
                 f"{chave}_orc_qq": sum(orc_anos.values()), f"{chave}_tend_qq": sum(tend_anos.values()),
                 f"{chave}_just_ano": d.get("justificativa") or None}
            return r
        e, f = bloco(econ, "econ"), bloco(fin, "fin")
        fb, fr = _m12(fis.get("orcado")), _m12(fis.get("realizado"))
        ft = _m12(fis.get("tendencia")) if fis.get("tendencia") else [fr[i] if i < mes else fb[i] for i in range(12)]
        comp = p.get("compromisso") or {}
        req, ped = _m12(comp.get("requisicao")), _m12(comp.get("pedido"))
        tem_comp = any(req) or any(ped)
        extras = p.get("extras") or {}
        out.append(ProjetoSnapshot(
            pep=str(p.get("codigo")), projeto=p.get("nome") or str(p.get("codigo")), unidade=p.get("unidade"), nucleo=p.get("grupo"),
            vertical_gestora=p.get("area"), tipo_investimento=p.get("tipo_investimento"), pep_pai=extras.get("projeto_pai"),
            diretoria_engenharia=p.get("gerente"), grupo=p.get("grupo"), motivador=extras.get("motivador"), natureza=extras.get("natureza"),
            fis_peso=p.get("peso_fisico"), fis_baseline_dez=fb[11], fis_baseline_atual=fb[mes - 1], fis_real_atual=fr[mes - 1],
            fis_baseline_mensal=fb, fis_real_mensal=fr, fis_tend_mensal=ft, fis_desvios=_desvios(fis.get("desvios"), mapa),
            fis_just_ano=fis.get("justificativa") or None,
            comp_req_ano=sum(req) if tem_comp else None, comp_ped_ano=sum(ped) if tem_comp else None,
            comp_sem_ano=max(f["fin_tend_ano"] - sum(req) - sum(ped) - f["fin_real_acum"], 0.0) if tem_comp else None,
            comp_check_ano=0.0 if tem_comp else None, comp_req_mensal=req, comp_ped_mensal=ped,
            **e, **f))
    if not out:
        avisos.append("Rodada sem projetos ativos.")
    meta = MetaRodada(mes_analise=MES_NOME.get(mes), versao_declarada=snap.get("nome") or rid, linhas_lidas=len(projetos),
                      linhas_validas=len(out), avisos=avisos, ano=ano)
    return rid, meta, out
