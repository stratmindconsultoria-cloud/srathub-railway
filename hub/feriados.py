"""Calendário de feriados do ecossistema.

- Nacionais: calculados (fixos + móveis a partir da Páscoa), sem internet nem cadastro.
- Pontos facultativos nacionais (Carnaval, Quarta-feira de Cinzas, Corpus Christi): marcados à parte; a organização
  decide se contam como feriado (organizacao.feriados_facultativos).
- Estaduais/municipais: por localidade, no perfil da organização (organizacao.localidades[].feriados), com data
  "MM-DD" (todo ano) ou "AAAA-MM-DD" (só naquele ano). Unidades podem apontar para uma localidade.
"""
from __future__ import annotations
from datetime import date, timedelta

FIXOS = [("01-01", "Confraternização Universal"), ("04-21", "Tiradentes"), ("05-01", "Dia do Trabalho"),
         ("09-07", "Independência do Brasil"), ("10-12", "Nossa Senhora Aparecida"), ("11-02", "Finados"),
         ("11-15", "Proclamação da República"), ("11-20", "Dia Nacional de Zumbi e da Consciência Negra"), ("12-25", "Natal")]


def pascoa(ano: int) -> date:
    """Algoritmo de Meeus/Jones/Butcher (calendário gregoriano)."""
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = (h + l - 7 * m + 114) % 31 + 1
    return date(ano, mes, dia)


def nacionais(ano: int) -> list[dict]:
    p = pascoa(ano)
    out = [{"data": f"{ano}-{md}", "nome": n, "tipo": "nacional"} for md, n in FIXOS]
    out.append({"data": (p - timedelta(days=2)).isoformat(), "nome": "Sexta-feira Santa", "tipo": "nacional"})
    for delta, nome in ((-48, "Carnaval (segunda-feira)"), (-47, "Carnaval (terça-feira)"), (-46, "Quarta-feira de Cinzas"),
                        (60, "Corpus Christi")):
        out.append({"data": (p + timedelta(days=delta)).isoformat(), "nome": nome, "tipo": "facultativo"})
    return sorted(out, key=lambda x: x["data"])


def do_ano(org: dict, ano: int, localidade: str | None = None) -> list[dict]:
    """Feriados do ano para uma localidade (código). `conta` diz se o dia é tratado como feriado."""
    fac = bool(org.get("feriados_facultativos", False))
    out = [{**f, "conta": f["tipo"] == "nacional" or fac} for f in nacionais(ano)]
    for loc in org.get("localidades") or []:
        if localidade and loc.get("codigo") != localidade:
            continue
        if not localidade:
            continue
        for f in loc.get("feriados") or []:
            d = str(f.get("data") or "").strip()
            if len(d) == 5:
                d = f"{ano}-{d}"
            if d.startswith(str(ano)) and len(d) == 10:
                out.append({"data": d, "nome": f.get("nome") or "Feriado local", "tipo": "local", "conta": True, "localidade": loc.get("codigo")})
    return sorted(out, key=lambda x: x["data"])


def periodo(org: dict, inicio: str, fim: str, localidade: str | None = None) -> list[dict]:
    a, b = int(inicio[:4]), int(fim[:4])
    out = []
    for ano in range(a, b + 1):
        out += [f for f in do_ano(org, ano, localidade) if inicio <= f["data"] <= fim]
    return out


def localidade_da_unidade(org: dict, unidade: str | None) -> str | None:
    for u in org.get("unidades") or []:
        if unidade and unidade in (u.get("codigo"), u.get("nome")):
            return u.get("localidade") or None
    return None
