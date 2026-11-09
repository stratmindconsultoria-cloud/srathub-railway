"""Contrato interno do Analisador: categorias de desvio, meses e o snapshot por projeto.

As categorias internas (chaves) são fixas porque os motores de análise agrupam por elas
(ex.: "calendário" = postergação + antecipação). As categorias usadas pela organização
(texto livre na ferramenta de origem) são traduzidas para estas chaves em `MAPA_CATEGORIAS`
— ajustável em config/analisador.json ("mapa_categorias").
"""
from __future__ import annotations
import json, re, unicodedata
from dataclasses import dataclass, field, asdict
from pathlib import Path

DESVIO_KEYS = {
    "postergado": "Postergação", "antecipacao": "Antecipação", "cancelado": "Cancelamento", "nao_orcado": "Não orçado",
    "poder": "Fatores externos", "engenharia": "Desempenho de engenharia", "contratacao": "Desempenho de contratação",
    "implantacao": "Desempenho de implantação", "reducao_escopo": "Redução de escopo", "economia": "Economia",
    "acrescimo_escopo": "Acréscimo de escopo", "custo_adicional": "Custo adicional", "migracao": "Migração", "outros": "Outros",
}
DESVIO_LABELS = {"postergado": "Postergação", "antecipacao": "Antecipação", "cancelado": "Cancelamento", "nao_orcado": "Não orçado",
                 "poder": "Fatores externos", "engenharia": "Desemp. engenharia", "contratacao": "Desemp. contratação",
                 "implantacao": "Desemp. implantação", "reducao_escopo": "Redução escopo", "economia": "Economia",
                 "acrescimo_escopo": "Acréscimo escopo", "custo_adicional": "Custo adicional", "migracao": "Migração", "outros": "Outros"}
MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
MES_IDX = {"Janeiro": 1, "Fevereiro": 2, "Março": 3, "Abril": 4, "Maio": 5, "Junho": 6,
           "Julho": 7, "Agosto": 8, "Setembro": 9, "Outubro": 10, "Novembro": 11, "Dezembro": 12}
MES_NOME = {v: k for k, v in MES_IDX.items()}


def _slug(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", str(s or "")) if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


MAPA_CATEGORIAS_PADRAO = {
    "postergacao": "postergado", "postergado": "postergado", "antecipacao": "antecipacao", "cancelamento": "cancelado", "cancelado": "cancelado",
    "nao orcado": "nao_orcado", "fatores externos": "poder", "fatores externos cliente": "poder", "poder concedente": "poder",
    "desempenho de engenharia": "engenharia", "performance engenharia": "engenharia", "desempenho de contratacao": "contratacao",
    "performance contratacao": "contratacao", "desempenho de implantacao": "implantacao", "performance implantacao": "implantacao",
    "reducao de escopo": "reducao_escopo", "reducao escopo": "reducao_escopo", "economia": "economia", "acrescimo de escopo": "acrescimo_escopo",
    "custo adicional": "custo_adicional", "reajuste de indices": "custo_adicional", "migracao": "migracao", "migracao entre projetos": "migracao",
}


def mapa_categorias(base_dir: Path) -> dict[str, str]:
    m = dict(MAPA_CATEGORIAS_PADRAO)
    try:
        extra = json.loads((base_dir / "config" / "analisador.json").read_text(encoding="utf-8")).get("mapa_categorias") or {}
        m.update({_slug(k): v for k, v in extra.items() if v in DESVIO_KEYS})
    except (OSError, ValueError):
        pass
    return m


def chave_categoria(texto: str, mapa: dict[str, str]) -> str:
    return mapa.get(_slug(texto), "outros")


@dataclass
class MetaRodada:
    mes_analise: str | None
    versao_declarada: str | None
    linhas_lidas: int
    linhas_validas: int
    avisos: list[str] = field(default_factory=list)
    ano: int | None = None


@dataclass
class ProjetoSnapshot:
    """Snapshot de um projeto numa rodada. Campos *_ano referem-se ao ano da rodada; *_anos = {ano: valor} (plurianual)."""
    pep: str
    projeto: str
    unidade: str | None
    nucleo: str | None
    vertical_gestora: str | None          # exibido como "Área"
    tipo_investimento: str | None
    pep_pai: str | None
    diretoria_engenharia: str | None = None   # exibido como "Gerente"
    grupo: str | None = None
    econ_orc_ano: float = 0.0
    econ_tend_ano: float = 0.0
    econ_orc_acum: float = 0.0
    econ_real_acum: float = 0.0
    econ_delta_acum: float = 0.0
    econ_desvios: dict = field(default_factory=dict)
    econ_orc_mensal: list = field(default_factory=list)
    econ_real_mensal: list = field(default_factory=list)
    econ_tend_mensal: list = field(default_factory=list)
    fin_orc_ano: float = 0.0
    fin_tend_ano: float = 0.0
    fin_orc_acum: float = 0.0
    fin_real_acum: float = 0.0
    fin_delta_acum: float = 0.0
    fin_desvios: dict = field(default_factory=dict)
    fin_orc_mensal: list = field(default_factory=list)
    fin_real_mensal: list = field(default_factory=list)
    fin_tend_mensal: list = field(default_factory=list)
    econ_orc_anos: dict = field(default_factory=dict)
    econ_tend_anos: dict = field(default_factory=dict)
    fin_orc_anos: dict = field(default_factory=dict)
    fin_tend_anos: dict = field(default_factory=dict)
    econ_orc_qq: float = 0.0
    econ_tend_qq: float = 0.0
    fin_orc_qq: float = 0.0
    fin_tend_qq: float = 0.0
    motivador: str | None = None
    natureza: str | None = None
    fis_peso: float | None = None
    fis_baseline_dez: float | None = None
    fis_baseline_atual: float | None = None
    fis_real_atual: float | None = None
    fis_baseline_mensal: list = field(default_factory=list)
    fis_real_mensal: list = field(default_factory=list)
    fis_tend_mensal: list = field(default_factory=list)
    fis_desvios: dict = field(default_factory=dict)
    fin_just_mes: str | None = None
    fin_just_acum: str | None = None
    fin_just_ano: str | None = None
    econ_just_mes: str | None = None
    econ_just_acum: str | None = None
    econ_just_ano: str | None = None
    fis_just_mes: str | None = None
    fis_just_acum: str | None = None
    fis_just_ano: str | None = None
    comp_req_ano: float | None = None
    comp_ped_ano: float | None = None
    comp_sem_ano: float | None = None
    comp_check_ano: float | None = None
    comp_req_total: float | None = None
    comp_ped_total: float | None = None
    comp_total_qq: float | None = None
    previsao_estouro: float | None = None
    comp_ped_mensal: list = field(default_factory=list)
    comp_req_mensal: list = field(default_factory=list)

    def as_row(self) -> dict:
        return asdict(self)
