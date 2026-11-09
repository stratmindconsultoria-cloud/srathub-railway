"""Ordens de Serviço — execução operacional.

Cada OS é um mini projeto criado a partir de um MODELO (tipo, SLA, composição da equipe, checklist com fotos e
respostas, materiais padrão, aprovação de encerramento, orientações). O planejamento define a equipe da OS e UM
executor (função que pode executar); o executor conduz em campo (modo Execução / PWA) com cronômetro que aponta as
horas sozinho. A escala das OS é do planejamento: na agenda dos técnicos ela entra travada. Feriados exigem ciência
e de acordo do gestor quando há membro sem escala. Custos só aparecem do planejador para cima.

Plugada no STRAT·HUB, recebe solicitações externas (Conector CRM), publica o resumo das OS para a PMP e para a
agenda do ecossistema e devolve o status ao CRM. Funciona sozinha sem nenhuma dessas ferramentas.
"""
from __future__ import annotations
import json, os, re, sqlite3, unicodedata, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

import hubkit
from appkit import Base, agora, dump, load

BASE_DIR = Path(__file__).resolve().parent
VERSAO = "1.1.2"
STATUS = {"aberta": "Aberta", "planejada": "Planejada", "em_execucao": "Em execução", "pausada": "Pausada",
          "concluida": "Concluída", "encerrada": "Encerrada", "cancelada": "Cancelada"}
FINAIS = ("concluida", "encerrada", "cancelada")
PRIORIDADES = {"baixa": "Baixa", "media": "Média", "alta": "Alta", "urgente": "Urgente"}
MAT_STATUS = {"a_separar": "A separar", "separado": "Separado", "consumido": "Consumido", "devolvido": "Devolvido"}
RESPOSTAS = {"check": "Marcar feito", "simnao": "Sim / não", "numero": "Número (medição)", "texto": "Texto curto"}
MOTIVOS_PAUSA = ["Falta de peça/material", "Acesso negado ao local", "Aguardando o cliente", "Condição climática", "Risco de segurança", "Outro"]
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(chave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS funcoes(nome TEXT PRIMARY KEY, pode_executor INTEGER NOT NULL DEFAULT 1, ativo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS equipes(id TEXT PRIMARY KEY, nome TEXT NOT NULL, unidade TEXT, especialidade TEXT, ativo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS membros(id TEXT PRIMARY KEY, equipe_id TEXT NOT NULL REFERENCES equipes(id) ON DELETE CASCADE, nome TEXT NOT NULL,
  funcao TEXT, custo_hora REAL NOT NULL DEFAULT 0, usuario_id TEXT, ativo INTEGER NOT NULL DEFAULT 1, escala_feriado INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS materiais(codigo TEXT PRIMARY KEY, descricao TEXT NOT NULL, unidade_medida TEXT NOT NULL DEFAULT 'un',
  custo_unitario REAL NOT NULL DEFAULT 0, ativo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS modelos(id TEXT PRIMARY KEY, nome TEXT NOT NULL, tipo TEXT NOT NULL, descricao TEXT, prioridade TEXT NOT NULL DEFAULT 'media',
  sla_horas TEXT NOT NULL DEFAULT '{}', duracao_h REAL, composicao TEXT NOT NULL DEFAULT '[]', checklist TEXT NOT NULL DEFAULT '[]',
  fotos_antes_min INTEGER NOT NULL DEFAULT 0, fotos_depois_min INTEGER NOT NULL DEFAULT 0, materiais TEXT NOT NULL DEFAULT '[]',
  exige_aprovacao INTEGER NOT NULL DEFAULT 1, exige_aceite INTEGER NOT NULL DEFAULT 0, orientacoes TEXT, ativo INTEGER NOT NULL DEFAULT 1,
  criado_em TEXT, atualizado_em TEXT, atualizado_por TEXT);
CREATE TABLE IF NOT EXISTS modelo_anexos(id TEXT PRIMARY KEY, modelo_id TEXT NOT NULL REFERENCES modelos(id) ON DELETE CASCADE, nome TEXT, arquivo TEXT);
CREATE TABLE IF NOT EXISTS modelos_origem(origem TEXT PRIMARY KEY, modelo_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ordens(numero TEXT PRIMARY KEY, titulo TEXT NOT NULL, descricao TEXT, tipo TEXT NOT NULL, prioridade TEXT NOT NULL,
  unidade TEXT, local TEXT, ativo TEXT, solicitante TEXT, solicitante_id TEXT, contato TEXT, cliente TEXT, origem TEXT NOT NULL DEFAULT 'manual',
  ref_externa TEXT, chave_externa TEXT, codigo_projeto TEXT, status TEXT NOT NULL DEFAULT 'aberta', motivo TEXT, equipe_id TEXT,
  execucao_inicio TEXT, duracao_h REAL, aberta_em TEXT NOT NULL, prazo_sla TEXT, planejada_em TEXT, iniciada_em TEXT, concluida_em TEXT,
  encerrada_em TEXT, resumo_execucao TEXT, atualizado_em TEXT, modelo_id TEXT, modelo TEXT, cronometro_inicio TEXT, aceite_nome TEXT,
  aceite_em TEXT, aprovacao TEXT, feriado TEXT, concluida_por_id TEXT);
CREATE INDEX IF NOT EXISTS ix_os_status ON ordens(status);
CREATE INDEX IF NOT EXISTS ix_os_equipe ON ordens(equipe_id, execucao_inicio);
CREATE TABLE IF NOT EXISTS os_participantes(numero TEXT NOT NULL REFERENCES ordens(numero) ON DELETE CASCADE, membro_id TEXT NOT NULL,
  executor INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(numero, membro_id));
CREATE TABLE IF NOT EXISTS checklist(numero TEXT NOT NULL REFERENCES ordens(numero) ON DELETE CASCADE, seq INTEGER NOT NULL, item TEXT NOT NULL,
  obrigatorio INTEGER NOT NULL DEFAULT 1, feito INTEGER NOT NULL DEFAULT 0, obs TEXT, por TEXT, em TEXT, foto_min INTEGER NOT NULL DEFAULT 0,
  resposta_tipo TEXT NOT NULL DEFAULT 'check', unidade TEXT, resposta TEXT, PRIMARY KEY(numero, seq));
CREATE TABLE IF NOT EXISTS os_materiais(id TEXT PRIMARY KEY, numero TEXT NOT NULL REFERENCES ordens(numero) ON DELETE CASCADE, codigo TEXT,
  descricao TEXT NOT NULL, unidade_medida TEXT, qtd_prevista REAL NOT NULL DEFAULT 0, qtd_real REAL NOT NULL DEFAULT 0,
  custo_unitario REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'a_separar', atualizado_por TEXT, atualizado_em TEXT);
CREATE TABLE IF NOT EXISTS apontamentos(id TEXT PRIMARY KEY, numero TEXT NOT NULL REFERENCES ordens(numero) ON DELETE CASCADE, membro_id TEXT,
  nome TEXT NOT NULL, data TEXT NOT NULL, horas REAL NOT NULL, custo_hora REAL NOT NULL DEFAULT 0, obs TEXT, por TEXT, em TEXT);
CREATE TABLE IF NOT EXISTS eventos(seq INTEGER PRIMARY KEY AUTOINCREMENT, numero TEXT NOT NULL, em TEXT NOT NULL, por TEXT, acao TEXT NOT NULL,
  de TEXT, para TEXT, comentario TEXT);
CREATE TABLE IF NOT EXISTS anexos(id TEXT PRIMARY KEY, numero TEXT NOT NULL REFERENCES ordens(numero) ON DELETE CASCADE, nome TEXT, arquivo TEXT,
  por TEXT, em TEXT, etapa TEXT NOT NULL DEFAULT 'geral', checklist_seq INTEGER);
CREATE TABLE IF NOT EXISTS sequencia(nome TEXT PRIMARY KEY, ultimo INTEGER NOT NULL);
"""
# colunas acrescentadas depois da 1.0 (bancos existentes são migrados na inicialização)
MIGRACOES = {
    "membros": [("escala_feriado", "INTEGER NOT NULL DEFAULT 0")],
    "ordens": [("modelo_id", "TEXT"), ("modelo", "TEXT"), ("cronometro_inicio", "TEXT"), ("aceite_nome", "TEXT"), ("aceite_em", "TEXT"),
               ("aprovacao", "TEXT"), ("feriado", "TEXT"), ("concluida_por_id", "TEXT")],
    "checklist": [("foto_min", "INTEGER NOT NULL DEFAULT 0"), ("resposta_tipo", "TEXT NOT NULL DEFAULT 'check'"), ("unidade", "TEXT"), ("resposta", "TEXT")],
    "anexos": [("etapa", "TEXT NOT NULL DEFAULT 'geral'"), ("checklist_seq", "INTEGER")],
}
PERFIS = {"gestor": "Gestor de serviços (administrador)", "supervisor": "Supervisor", "planejador": "Planejador", "tecnico": "Técnico",
          "solicitante": "Solicitante"}
GESTAO = ("gestor", "supervisor", "planejador")  # veem custos e organizam a escala
DEMO = [("u-demo-gestor", "Gestor Demo", "gestor", None), ("u-demo-sup", "Supervisor Demo", "supervisor", None), ("u-demo-plan", "Planejador Demo", "planejador", None),
        ("u-demo-tec1", "Técnico Demo 1", "tecnico", 0), ("u-demo-tec2", "Técnico Demo 2", "tecnico", 1), ("u-demo-sol", "Solicitante Demo", "solicitante", 0)]
CHK = lambda itens, fotos=(): [{"item": t, "obrigatorio": True, "foto_min": (1 if i in fotos else 0), "resposta_tipo": "check"} for i, t in enumerate(itens)]
CONFIG = {
    "prefixo": "OS",
    # modelos iniciais: importados UMA vez para o banco; depois tudo é pela tela "Modelos de OS"
    "tipos": [
        {"codigo": "corretiva", "nome": "Manutenção corretiva", "sla_horas": {"baixa": 120, "media": 72, "alta": 24, "urgente": 4},
         "checklist": ["Isolar e sinalizar a área", "Diagnosticar a falha", "Executar o reparo", "Testar o funcionamento", "Registrar fotos e limpar o local"]},
        {"codigo": "preventiva", "nome": "Manutenção preventiva", "sla_horas": {"baixa": 240, "media": 168, "alta": 72, "urgente": 24},
         "checklist": ["Conferir o plano de manutenção", "Inspecionar componentes", "Lubrificar / ajustar", "Registrar medições"]},
        {"codigo": "instalacao", "nome": "Instalação", "sla_horas": {"baixa": 240, "media": 120, "alta": 48, "urgente": 24},
         "checklist": ["Conferir materiais", "Instalar", "Testar com o solicitante", "Entregar documentação"]},
        {"codigo": "inspecao", "nome": "Inspeção", "sla_horas": {"baixa": 168, "media": 72, "alta": 24, "urgente": 8},
         "checklist": ["Inspecionar conforme roteiro", "Registrar não conformidades"]},
        {"codigo": "atendimento", "nome": "Atendimento ao cliente", "sla_horas": {"baixa": 72, "media": 48, "alta": 24, "urgente": 6},
         "checklist": ["Contatar o cliente", "Executar o atendimento", "Obter o aceite do cliente"]},
    ],
    "funcoes": [{"nome": "Encarregado", "pode_executor": True}, {"nome": "Técnico", "pode_executor": True}, {"nome": "Eletricista", "pode_executor": True},
                {"nome": "Mecânico", "pode_executor": True}, {"nome": "Auxiliar", "pode_executor": False}, {"nome": "Ajudante", "pode_executor": False}],
    "risco_sla_pct": 0.25,  # em risco quando resta menos que esta fração do prazo
    "fuso_horas": -3,       # para saber o dia (feriados) e a hora local da execução
    "rotulos": {},
}
B = Base("ordens", "Ordens de Serviço", VERSAO, BASE_DIR, SCHEMA, PERFIS, admin="gestor", padrao="solicitante", demo=DEMO, config_padrao=CONFIG)


def _migrar(c) -> None:
    for tabela, cols in MIGRACOES.items():
        existentes = {r[1] for r in c.execute(f"PRAGMA table_info({tabela})")}
        for nome, tipo in cols:
            if nome not in existentes:
                c.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}")


def _modelo_de_tipo(t: dict) -> dict:
    ck = t.get("checklist") or []
    return {"id": t["codigo"], "nome": t["nome"], "tipo": t.get("tipo") or t["codigo"], "sla_horas": t.get("sla_horas") or {},
            "checklist": ck if ck and isinstance(ck[0], dict) else CHK(ck, fotos=(len(ck) - 1,)),
            "exige_aprovacao": t.get("exige_aprovacao", True), "fotos_depois_min": t.get("fotos_depois_min", 1),
            "prioridade": t.get("prioridade", "media"), "duracao_h": t.get("duracao_h"), "composicao": t.get("composicao") or [],
            "materiais": t.get("materiais") or [], "orientacoes": t.get("orientacoes") or "", "descricao": t.get("descricao") or ""}


def _semear(c) -> None:
    """Implantação: funções e modelos dos arquivos de configuração entram no banco e, daí em diante, tudo é pela tela.
    Cada modelo de arquivo entra UMA vez (só adiciona): editar o arquivo não sobrescreve o que foi ajustado na tela, nem
    ressuscita um modelo excluído/desativado. Um arquivo novo (ex.: modelo de ecossistema aplicado depois) traz só os novos.
    Para atualizar modelos existentes use "Importar modelos" (com confirmação e histórico)."""
    cfg, espelho = B.config(), _ler_espelho() or {}
    vistos = set(load((c.execute("SELECT valor FROM meta WHERE chave='modelos_vistos'").fetchone() or {"valor": "[]"})["valor"], []))
    primeira = not c.execute("SELECT 1 FROM meta WHERE chave='semeado'").fetchone()
    funcoes = (espelho.get("funcoes") or []) + (cfg.get("funcoes") or [])
    for f in funcoes:
        c.execute("INSERT OR IGNORE INTO funcoes(nome,pode_executor) VALUES (?,?)", (f["nome"], 1 if f.get("pode_executor", True) else 0))
    candidatos = [m for m in espelho.get("modelos") or [] if m.get("id")] + [_modelo_de_tipo(t) for t in cfg.get("tipos") or [] if t.get("codigo")]
    for m in candidatos:
        if m["id"] in vistos:
            continue
        vistos.add(m["id"])
        if not c.execute("SELECT 1 FROM modelos WHERE id=?", (m["id"],)).fetchone():
            _gravar_modelo(c, m, None, novo=True)
            for o in m.get("origens") or []:
                c.execute("INSERT OR IGNORE INTO modelos_origem(origem,modelo_id) VALUES (?,?)", (o, m["id"]))
    if primeira:
        for origem, mid in (cfg.get("modelos_origem") or {"crm": "atendimento"}).items():
            if c.execute("SELECT 1 FROM modelos WHERE id=?", (mid,)).fetchone():
                c.execute("INSERT OR IGNORE INTO modelos_origem(origem,modelo_id) VALUES (?,?)", (origem, mid))
        c.execute("INSERT INTO meta(chave,valor) VALUES ('semeado', ?)", (agora(),))
    c.execute("INSERT OR REPLACE INTO meta(chave,valor) VALUES ('modelos_vistos', ?)", (dump(sorted(vistos)),))


ESPELHO = "modelos-os.json"


def _ler_espelho() -> dict | None:
    arq = B.base_dir / "config" / ESPELHO
    try:
        return json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else None
    except (OSError, ValueError):
        return None


def _doc_modelos(c) -> dict:
    mods = [{k: v for k, v in modelo_dict(c, r).items() if k not in ("anexos", "criado_em", "atualizado_em", "atualizado_por")} for r in c.execute("SELECT * FROM modelos ORDER BY nome")]
    funcoes = [dict(r) for r in c.execute("SELECT nome, pode_executor FROM funcoes WHERE ativo=1")]
    return {"formato": "strathub-ordens-modelos/1", "exportado_em": agora(), "modelos": mods, "funcoes": funcoes}


def _espelhar(c) -> None:
    """Cópia dos modelos em config/modelos-os.json: entra na exportação do ecossistema do hub e implanta os mesmos
    modelos em outra empresa (lida só na primeira inicialização; depois tudo é pela tela)."""
    if not hubkit.no_hub() and not os.environ.get("ORDENS_ESPELHO"):
        return  # fora do hub (desenvolvimento, testes) não grava na pasta do programa
    try:
        pasta = B.base_dir / "config"; pasta.mkdir(exist_ok=True)
        (pasta / ESPELHO).write_text(json.dumps(_doc_modelos(c), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


@asynccontextmanager
async def _vida(_app):
    B.iniciar()
    with B.con() as c:
        _migrar(c)
        _semear(c)
    yield

app = FastAPI(title="Ordens de Serviço", version=VERSAO, lifespan=_vida)


def _meta_extra() -> dict:
    with B.con() as c:
        mods = [{"codigo": r["id"], "nome": r["nome"], "tipo": r["tipo"], "sla_horas": load(r["sla_horas"], {}), "prioridade": r["prioridade"]}
                for r in c.execute("SELECT * FROM modelos WHERE ativo=1 ORDER BY nome")]
    inst = hubkit.instaladas()
    return {"status": STATUS, "prioridades": PRIORIDADES, "tipos": mods, "modelos": mods, "status_material": MAT_STATUS, "respostas": RESPOSTAS,
            "motivos_pausa": MOTIVOS_PAUSA, "ecossistema": inst, "gestao": list(GESTAO)}


B.montar(app, _meta_extra)


# ───────────────────────────────────────────────── utilidades
def _agora_dt() -> datetime:
    return datetime.now(timezone.utc)


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _local(d: datetime) -> datetime:
    return d.astimezone(timezone(timedelta(hours=float(B.config().get("fuso_horas", -3)))))


def ve_custos(u: dict | None) -> bool:
    return bool(u) and u.get("perfil") in GESTAO


CAMPOS_CUSTO = {"custos", "custo_unitario", "custo_hora", "custo_previsto", "custo_real"}


def filtrar(u: dict | None, obj):
    """Técnico e solicitante não recebem custos de materiais, de mão de obra nem custo/hora (filtrado no servidor)."""
    if ve_custos(u):
        return obj
    if isinstance(obj, list):
        return [filtrar(u, x) for x in obj]
    if isinstance(obj, dict):
        return {k: filtrar(u, v) for k, v in obj.items() if k not in CAMPOS_CUSTO}
    return obj


def _os(c, numero: str) -> sqlite3.Row:
    r = c.execute("SELECT * FROM ordens WHERE numero=?", (numero,)).fetchone()
    if not r:
        raise HTTPException(404, "OS não encontrada.")
    return r


def _proximo_numero(c) -> str:
    ano = _agora_dt().year
    nome = f"os-{ano}"
    c.execute("INSERT OR IGNORE INTO sequencia(nome,ultimo) VALUES (?,0)", (nome,))
    c.execute("UPDATE sequencia SET ultimo=ultimo+1 WHERE nome=?", (nome,))
    n = c.execute("SELECT ultimo FROM sequencia WHERE nome=?", (nome,)).fetchone()[0]
    return f"{B.config().get('prefixo') or 'OS'}-{ano}-{n:05d}"


def _evento(c, numero: str, u: dict | None, acao: str, de: str = "", para: str = "", comentario: str = "") -> None:
    c.execute("INSERT INTO eventos(numero,em,por,acao,de,para,comentario) VALUES (?,?,?,?,?,?,?)",
              (numero, agora(), (u or {}).get("nome") or "sistema", acao, de, para, comentario))


def _snap(o) -> dict:
    return load(o["modelo"], {}) or {}


def sla(o, agora_dt: datetime | None = None) -> dict:
    """Situação do prazo: no_prazo | em_risco | atrasada (abertas) e cumprido | violado (finalizadas)."""
    prazo, ab = _dt(o["prazo_sla"]), _dt(o["aberta_em"])
    if not prazo or not ab:
        return {"situacao": "sem_sla", "restante_h": None}
    now = agora_dt or _agora_dt()
    if o["status"] == "cancelada":
        return {"situacao": "cancelada", "restante_h": None}
    if o["status"] in ("concluida", "encerrada"):
        fim = _dt(o["concluida_em"]) or now
        return {"situacao": "cumprido" if fim <= prazo else "violado", "restante_h": round((prazo - fim).total_seconds() / 3600, 1)}
    rest = (prazo - now).total_seconds() / 3600
    total = max((prazo - ab).total_seconds() / 3600, 0.01)
    sit = "atrasada" if rest < 0 else "em_risco" if rest < total * float(B.config().get("risco_sla_pct") or 0.25) else "no_prazo"
    return {"situacao": sit, "restante_h": round(rest, 1)}


def participantes(c, numero: str) -> list[dict]:
    return [dict(r) for r in c.execute("""SELECT m.*, p.executor FROM os_participantes p JOIN membros m ON m.id=p.membro_id
                                          WHERE p.numero=? ORDER BY p.executor DESC, m.nome""", (numero,))]


def _custo_hora_os(c, o) -> float:
    ps = participantes(c, o["numero"])
    if ps:
        return float(sum(p["custo_hora"] for p in ps))
    if not o["equipe_id"]:
        return 0.0
    return float(c.execute("SELECT COALESCE(SUM(custo_hora),0) FROM membros WHERE equipe_id=? AND ativo=1", (o["equipe_id"],)).fetchone()[0])


def custos(c, o) -> dict:
    mats = c.execute("SELECT * FROM os_materiais WHERE numero=?", (o["numero"],)).fetchall()
    mat_prev = sum(m["qtd_prevista"] * m["custo_unitario"] for m in mats)
    mat_real = sum(m["qtd_real"] * m["custo_unitario"] for m in mats if m["status"] == "consumido")
    mo_prev = float(o["duracao_h"] or 0) * _custo_hora_os(c, o)
    aps = c.execute("SELECT * FROM apontamentos WHERE numero=?", (o["numero"],)).fetchall()
    mo_real = sum(a["horas"] * a["custo_hora"] for a in aps)
    return {"material_previsto": round(mat_prev, 2), "material_real": round(mat_real, 2), "mao_obra_prevista": round(mo_prev, 2),
            "mao_obra_real": round(mo_real, 2), "previsto": round(mat_prev + mo_prev, 2), "real": round(mat_real + mo_real, 2),
            "horas_previstas": float(o["duracao_h"] or 0), "horas_reais": round(sum(a["horas"] for a in aps), 2)}


def _fim_previsto(o) -> datetime | None:
    ini = _dt(o["execucao_inicio"])
    return ini + timedelta(hours=float(o["duracao_h"] or 1)) if ini else None


def conflitos(c, o) -> list[dict]:
    """Outras OS da mesma equipe cujo intervalo de execução se sobrepõe ao desta."""
    ini, fim = _dt(o["execucao_inicio"]), _fim_previsto(o)
    if not (o["equipe_id"] and ini) or o["status"] in FINAIS:
        return []
    out = []
    for x in c.execute("SELECT * FROM ordens WHERE equipe_id=? AND numero<>? AND execucao_inicio IS NOT NULL AND status NOT IN ('concluida','encerrada','cancelada')",
                       (o["equipe_id"], o["numero"])):
        xi, xf = _dt(x["execucao_inicio"]), _fim_previsto(x)
        if xi and xf and xi < fim and ini < xf:
            out.append({"numero": x["numero"], "titulo": x["titulo"], "execucao_inicio": x["execucao_inicio"], "duracao_h": x["duracao_h"]})
    return out


def _resumo(c, o) -> dict:
    eq = c.execute("SELECT nome, unidade FROM equipes WHERE id=?", (o["equipe_id"],)).fetchone() if o["equipe_id"] else None
    ck = c.execute("SELECT COUNT(*) n, SUM(feito) f FROM checklist WHERE numero=?", (o["numero"],)).fetchone()
    ex = c.execute("SELECT m.nome FROM os_participantes p JOIN membros m ON m.id=p.membro_id WHERE p.numero=? AND p.executor=1", (o["numero"],)).fetchone()
    snap = _snap(o)
    d = {k: o[k] for k in o.keys() if k not in ("modelo",)}
    return {**d, "status_nome": STATUS.get(o["status"], o["status"]), "prioridade_nome": PRIORIDADES.get(o["prioridade"], o["prioridade"]),
            "tipo_nome": snap.get("nome") or o["tipo"], "modelo_nome": snap.get("nome"), "equipe": eq["nome"] if eq else None,
            "executor": ex["nome"] if ex else None, "sla": sla(o), "custos": custos(c, o), "checklist": {"total": ck["n"] or 0, "feitos": ck["f"] or 0},
            "feriado": load(o["feriado"], None), "cronometro_ativo": bool(o["cronometro_inicio"])}


def _pode_ver(u: dict, o) -> bool:
    return u["perfil"] != "solicitante" or o["solicitante_id"] == u["id"]


def _membro_do_usuario(c, u: dict, numero: str) -> dict | None:
    r = c.execute("""SELECT m.*, p.executor FROM os_participantes p JOIN membros m ON m.id=p.membro_id
                     WHERE p.numero=? AND m.usuario_id=?""", (numero, u["id"])).fetchone()
    return dict(r) if r else None


def _publicar_os(c, numero: str, u: dict | None = None) -> None:
    """Resumo para o ecossistema (PMP, painéis) e status de volta ao CRM. Falha do hub não bloqueia a operação."""
    o = _os(c, numero); r = _resumo(c, o)
    payload = {"numero": o["numero"], "titulo": o["titulo"], "tipo": o["tipo"], "tipo_nome": r["tipo_nome"], "prioridade": o["prioridade"],
               "status": o["status"], "status_nome": r["status_nome"], "sla": r["sla"]["situacao"], "prazo_sla": o["prazo_sla"],
               "unidade": o["unidade"], "local": o["local"], "equipe": r["equipe"], "executor": r["executor"], "execucao_inicio": o["execucao_inicio"],
               "duracao_h": o["duracao_h"], "codigo_projeto": o["codigo_projeto"], "cliente": o["cliente"], "custo_previsto": r["custos"]["previsto"],
               "custo_real": r["custos"]["real"], "aberta_em": o["aberta_em"], "concluida_em": o["concluida_em"], "origem_app": B.app_id,
               "atualizado_em": o["atualizado_em"]}
    try:
        hubkit.publicar("servicos", "os_resumo", o["numero"], payload, source_application=B.app_id, source_version=B.versao,
                        source_user=B.quem(u) if u else "")
        if o["chave_externa"]:
            hubkit.publicar("servicos", "status_os", o["chave_externa"], {**payload, "ref_externa": o["ref_externa"], "solicitacao": o["chave_externa"],
                            "motivo": o["motivo"], "resumo_execucao": o["resumo_execucao"], "aceite_nome": o["aceite_nome"]},
                            source_application=B.app_id, source_version=B.versao)
    except Exception:
        pass


# ───────────────────────────────────────────────── modelos de OS
def _modelo(c, mid: str) -> sqlite3.Row:
    r = c.execute("SELECT * FROM modelos WHERE id=?", (mid,)).fetchone()
    if not r:
        raise HTTPException(404, "Modelo não encontrado.")
    return r


def modelo_dict(c, r) -> dict:
    d = dict(r)
    for k in ("sla_horas", "composicao", "checklist", "materiais"):
        d[k] = load(d[k], {} if k == "sla_horas" else [])
    d["exige_aprovacao"], d["exige_aceite"], d["ativo"] = bool(d["exige_aprovacao"]), bool(d["exige_aceite"]), bool(d["ativo"])
    d["anexos"] = [dict(a) for a in c.execute("SELECT id,nome FROM modelo_anexos WHERE modelo_id=?", (r["id"],))]
    d["origens"] = [x["origem"] for x in c.execute("SELECT origem FROM modelos_origem WHERE modelo_id=?", (r["id"],))]
    return d


def _limpar_checklist(itens) -> list[dict]:
    out = []
    for it in itens or []:
        if isinstance(it, str):
            it = {"item": it}
        txt = str((it or {}).get("item") or "").strip()
        if not txt:
            continue
        tipo = it.get("resposta_tipo") if it.get("resposta_tipo") in RESPOSTAS else "check"
        out.append({"item": txt[:300], "obrigatorio": bool(it.get("obrigatorio", True)), "foto_min": max(0, min(20, int(it.get("foto_min") or 0))),
                    "resposta_tipo": tipo, "unidade": (str(it.get("unidade") or "").strip()[:20] or None) if tipo == "numero" else None})
    return out


def _gravar_modelo(c, d: dict, u: dict | None, novo: bool = False) -> str:
    nome = str(d.get("nome") or "").strip()
    if not nome:
        raise HTTPException(400, "Informe o nome do modelo.")
    mid = str(d.get("id") or "").strip() or re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", nome.lower()).encode("ascii", "ignore").decode()).strip("-")[:40] or uuid.uuid4().hex[:8]
    if novo:
        base, k = mid, 1
        while c.execute("SELECT 1 FROM modelos WHERE id=?", (mid,)).fetchone():
            k += 1; mid = f"{base}-{k}"
    pri = d.get("prioridade") if d.get("prioridade") in PRIORIDADES else "media"
    slah = {p: float(v) for p, v in (d.get("sla_horas") or {}).items() if p in PRIORIDADES and v not in (None, "", 0, "0")}
    comp = [{"funcao": str(x.get("funcao") or "").strip(), "qtd": max(1, int(x.get("qtd") or 1)), "executor": bool(x.get("executor"))}
            for x in d.get("composicao") or [] if str(x.get("funcao") or "").strip()]
    mats = [{"codigo": str(x.get("codigo") or "").strip(), "qtd": float(x.get("qtd") or 0)} for x in d.get("materiais") or []
            if str(x.get("codigo") or "").strip() and float(x.get("qtd") or 0) > 0]
    campos = {"nome": nome[:120], "tipo": str(d.get("tipo") or mid).strip()[:40], "descricao": d.get("descricao") or "", "prioridade": pri,
              "sla_horas": dump(slah), "duracao_h": float(d["duracao_h"]) if d.get("duracao_h") not in (None, "") else None, "composicao": dump(comp),
              "checklist": dump(_limpar_checklist(d.get("checklist"))), "fotos_antes_min": max(0, int(d.get("fotos_antes_min") or 0)),
              "fotos_depois_min": max(0, int(d.get("fotos_depois_min") or 0)), "materiais": dump(mats),
              "exige_aprovacao": 1 if d.get("exige_aprovacao", True) else 0, "exige_aceite": 1 if d.get("exige_aceite") else 0,
              "orientacoes": d.get("orientacoes") or "", "ativo": 0 if d.get("ativo") is False else 1, "atualizado_em": agora(),
              "atualizado_por": (u or {}).get("nome") or "implantação"}
    if c.execute("SELECT 1 FROM modelos WHERE id=?", (mid,)).fetchone():
        c.execute("UPDATE modelos SET " + ", ".join(f"{k}=?" for k in campos) + " WHERE id=?", (*campos.values(), mid))
    else:
        c.execute("INSERT INTO modelos(id,criado_em," + ",".join(campos) + ") VALUES (?,?," + ",".join("?" * len(campos)) + ")", (mid, agora(), *campos.values()))
    if isinstance(d.get("origens"), list):
        c.execute("DELETE FROM modelos_origem WHERE modelo_id=?", (mid,))
        for o in d["origens"]:
            if str(o).strip():
                c.execute("INSERT OR REPLACE INTO modelos_origem(origem,modelo_id) VALUES (?,?)", (str(o).strip()[:60], mid))
    return mid


def _escolher_modelo(c, d: dict, origem: str) -> sqlite3.Row:
    if d.get("modelo_id"):
        r = c.execute("SELECT * FROM modelos WHERE id=? AND ativo=1", (d["modelo_id"],)).fetchone()
        if not r:
            raise HTTPException(400, "Modelo de OS inválido ou inativo.")
        return r
    tipo = str(d.get("tipo") or "")
    por_origem = lambda chave: c.execute("SELECT m.* FROM modelos_origem o JOIN modelos m ON m.id=o.modelo_id WHERE o.origem=? AND m.ativo=1", (chave,)).fetchone()
    # 1) padrão da origem para o tipo (crm:corretiva) · 2) modelo do tipo informado · 3) padrão da origem (crm) · 4) primeiro ativo
    if tipo and (m := por_origem(f"{origem}:{tipo}")):
        return m
    if tipo:
        m = c.execute("SELECT * FROM modelos WHERE (id=? OR tipo=?) AND ativo=1 ORDER BY id=? DESC LIMIT 1", (tipo, tipo, tipo)).fetchone()
        if m:
            return m
        if origem == "manual":
            raise HTTPException(400, "Tipo de OS inválido.")
    if (m := por_origem(origem)):
        return m
    m = c.execute("SELECT * FROM modelos WHERE ativo=1 ORDER BY nome LIMIT 1").fetchone()
    if not m:
        raise HTTPException(409, "Cadastre ao menos um modelo de OS.")
    return m


def criar_os(c, u: dict | None, d: dict, origem: str = "manual") -> str:
    titulo = str(d.get("titulo") or "").strip()
    if not titulo:
        raise HTTPException(400, "Informe o título da OS.")
    m = _escolher_modelo(c, d, origem); snap = modelo_dict(c, m)
    pri = str(d.get("prioridade") or snap["prioridade"] or "media")
    if pri not in PRIORIDADES:
        raise HTTPException(400, "Prioridade inválida.")
    # ajustes da OS sobre o modelo (só para esta OS) — ex.: checklist e fotos definidos na criação
    if isinstance(d.get("checklist"), list):
        snap["checklist"] = _limpar_checklist(d["checklist"])
    for k in ("fotos_antes_min", "fotos_depois_min"):
        if d.get(k) not in (None, ""):
            snap[k] = max(0, int(d[k]))
    if "exige_aprovacao" in d:
        snap["exige_aprovacao"] = bool(d["exige_aprovacao"])
    numero, ab = _proximo_numero(c), _agora_dt()
    horas = (snap.get("sla_horas") or {}).get(pri)
    prazo = _iso(ab + timedelta(hours=float(horas))) if horas else None
    c.execute("""INSERT INTO ordens(numero,titulo,descricao,tipo,prioridade,unidade,local,ativo,solicitante,solicitante_id,contato,cliente,origem,
                 ref_externa,chave_externa,codigo_projeto,status,aberta_em,prazo_sla,atualizado_em,modelo_id,modelo,duracao_h)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (numero, titulo, d.get("descricao") or snap.get("descricao") or None, snap["tipo"], pri, d.get("unidade"), d.get("local"), d.get("ativo"),
               d.get("solicitante") or (u or {}).get("nome"), (u or {}).get("id") if origem == "manual" else None, d.get("contato"), d.get("cliente"),
               origem, d.get("ref_externa"), d.get("chave_externa"), d.get("codigo_projeto") or None, "aberta", _iso(ab), prazo, agora(),
               m["id"], dump(snap), snap.get("duracao_h")))
    for i, it in enumerate(snap.get("checklist") or [], 1):
        c.execute("INSERT INTO checklist(numero,seq,item,obrigatorio,foto_min,resposta_tipo,unidade) VALUES (?,?,?,?,?,?,?)",
                  (numero, i, it["item"], 1 if it.get("obrigatorio", True) else 0, int(it.get("foto_min") or 0), it.get("resposta_tipo") or "check", it.get("unidade")))
    for mt in snap.get("materiais") or []:
        cat = c.execute("SELECT * FROM materiais WHERE codigo=? AND ativo=1", (mt["codigo"],)).fetchone()
        if cat:
            c.execute("INSERT INTO os_materiais(id,numero,codigo,descricao,unidade_medida,qtd_prevista,custo_unitario,status,atualizado_em) VALUES (?,?,?,?,?,?,?,?,?)",
                      ("M-" + uuid.uuid4().hex[:8], numero, cat["codigo"], cat["descricao"], cat["unidade_medida"], mt["qtd"], cat["custo_unitario"], "a_separar", agora()))
    _evento(c, numero, u, "OS aberta", "", "aberta", f"Origem: {origem} · modelo: {snap['nome']}")
    return numero


@app.get("/api/modelos")
def listar_modelos(todos: bool = False, x_usuario: str | None = Header(None)):
    with B.con() as c:
        u = B.usuario(c, x_usuario)
        return filtrar(u, [modelo_dict(c, r) for r in c.execute("SELECT * FROM modelos" + ("" if todos else " WHERE ativo=1") + " ORDER BY ativo DESC, nome")])


@app.get("/api/modelos/{mid}")
def obter_modelo(mid: str, x_usuario: str | None = Header(None)):
    with B.con() as c:
        B.usuario(c, x_usuario)
        return modelo_dict(c, _modelo(c, mid))


@app.put("/api/modelos/{mid}")
def salvar_modelo(mid: str, corpo: dict, x_usuario: str | None = Header(None)):
    """Cria (mid = "novo") ou edita um modelo. OS já abertas guardam a cópia do modelo e não mudam."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "gestor", "planejador", msg="Modelos de OS são do gestor e do planejador.")
        novo = mid == "novo"
        if not novo:
            _modelo(c, mid)
        mid = _gravar_modelo(c, {**corpo, "id": None if novo else mid}, u, novo=novo)
        B.auditar(c, u, "Modelo de OS salvo", mid)
        _espelhar(c)
        return modelo_dict(c, _modelo(c, mid))


@app.post("/api/modelos/{mid}/duplicar")
def duplicar_modelo(mid: str, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "gestor", "planejador")
        d = modelo_dict(c, _modelo(c, mid))
        novo = _gravar_modelo(c, {**d, "id": None, "nome": d["nome"] + " (cópia)", "origens": []}, u, novo=True)
        _espelhar(c)
        return modelo_dict(c, _modelo(c, novo))


@app.post("/api/modelos/{mid}/anexos")
async def anexo_modelo(mid: str, arquivo: UploadFile = File(...), x_usuario: str | None = Header(None)):
    dados = await arquivo.read()
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "gestor", "planejador"); _modelo(c, mid)
        aid = uuid.uuid4().hex; ext = re.sub(r"[^a-z0-9]", "", Path(arquivo.filename or "").suffix.lower())[:8] or "bin"
        (B.arquivos / f"m-{aid}.{ext}").write_bytes(dados)
        c.execute("INSERT INTO modelo_anexos(id,modelo_id,nome,arquivo) VALUES (?,?,?,?)", (aid, mid, arquivo.filename or "anexo", f"m-{aid}.{ext}"))
        return {"id": aid}


@app.delete("/api/modelos/{mid}/anexos/{aid}")
def remover_anexo_modelo(mid: str, aid: str, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "gestor", "planejador")
        c.execute("DELETE FROM modelo_anexos WHERE id=? AND modelo_id=?", (aid, mid))
        return {"ok": True}


@app.get("/api/modelo-anexos/{aid}")
def baixar_anexo_modelo(aid: str, x_usuario: str | None = Header(None)):
    with B.con() as c:
        B.usuario(c, x_usuario)
        a = c.execute("SELECT * FROM modelo_anexos WHERE id=?", (aid,)).fetchone()
        if not a:
            raise HTTPException(404, "Anexo não encontrado.")
        return FileResponse(B.arquivos / a["arquivo"], filename=a["nome"])


@app.get("/api/modelos-exportar")
def exportar_modelos(x_usuario: str | None = Header(None)):
    """Arquivo para implantar os mesmos modelos em outra empresa/servidor."""
    with B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "gestor", "planejador")
        doc = _doc_modelos(c)
        return Response(json.dumps(doc, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="modelos-os.json"'})


@app.post("/api/modelos-importar")
async def importar_modelos(arquivo: UploadFile = File(...), modo: str = Form("adicionar"), x_usuario: str | None = Header(None)):
    """modo=adicionar: só cria os que não existem · modo=atualizar: também sobrescreve os existentes (versão anterior vai para o histórico)."""
    try:
        doc = json.loads((await arquivo.read()).decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Arquivo inválido: use o JSON exportado de Modelos de OS.")
    mods = doc.get("modelos") if isinstance(doc, dict) else None
    if mods is None and isinstance(doc, dict) and doc.get("tipos"):  # também aceita o arquivo de configuração (ordens.json)
        mods = [{"id": t["codigo"], "nome": t["nome"], "tipo": t["codigo"], "sla_horas": t.get("sla_horas") or {},
                 "checklist": t.get("checklist") or []} for t in doc["tipos"]]
    if not isinstance(mods, list):
        raise HTTPException(400, "Arquivo sem modelos.")
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "gestor")
        criados = atualizados = ignorados = 0
        for m in mods:
            mid = str(m.get("id") or "").strip()
            existe = c.execute("SELECT * FROM modelos WHERE id=?", (mid,)).fetchone() if mid else None
            if existe and modo != "atualizar":
                ignorados += 1; continue
            if existe:
                B.auditar(c, u, "Modelo substituído por importação (versão anterior)", mid, modelo_dict(c, existe))
                _gravar_modelo(c, m, u); atualizados += 1
            else:
                _gravar_modelo(c, m, u, novo=not mid); criados += 1
        for f in (doc.get("funcoes") or []) if isinstance(doc, dict) else []:
            c.execute("INSERT OR IGNORE INTO funcoes(nome,pode_executor) VALUES (?,?)", (f["nome"], 1 if f.get("pode_executor", True) else 0))
        B.auditar(c, u, "Modelos importados", "", {"criados": criados, "atualizados": atualizados, "ignorados": ignorados, "modo": modo})
        _espelhar(c)
        return {"criados": criados, "atualizados": atualizados, "ignorados": ignorados}


@app.get("/api/origens-modelo")
def origens_modelo(x_usuario: str | None = Header(None)):
    with B.con() as c:
        B.usuario(c, x_usuario)
        return [dict(r) for r in c.execute("SELECT * FROM modelos_origem ORDER BY origem")]


@app.put("/api/origens-modelo")
def salvar_origens(corpo: dict, x_usuario: str | None = Header(None)):
    """{"crm": "atendimento", "crm:corretiva": "corretiva", "orcamentos:visita": "visita"} — modelo padrão por origem."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "gestor", "planejador")
        c.execute("DELETE FROM modelos_origem")
        for o, mid in (corpo or {}).items():
            if mid and c.execute("SELECT 1 FROM modelos WHERE id=?", (mid,)).fetchone():
                c.execute("INSERT INTO modelos_origem(origem,modelo_id) VALUES (?,?)", (str(o)[:60], mid))
        _espelhar(c)
    return origens_modelo(x_usuario)


# ───────────────────────────────────────────────── ordens
@app.get("/api/ordens")
def listar(status: str = "", equipe: str = "", sla_sit: str = "", busca: str = "", minhas: bool = False, x_usuario: str | None = Header(None)):
    with B.con() as c:
        u = B.usuario(c, x_usuario)
        meus = {r["numero"] for r in c.execute("SELECT p.numero FROM os_participantes p JOIN membros m ON m.id=p.membro_id WHERE m.usuario_id=?", (u["id"],))}
        out = []
        for o in c.execute("SELECT * FROM ordens ORDER BY aberta_em DESC"):
            if not _pode_ver(u, o) or (status and o["status"] != status) or (equipe and o["equipe_id"] != equipe):
                continue
            if minhas and o["numero"] not in meus:
                continue
            if busca and busca.lower() not in " ".join(str(o[k] or "") for k in ("numero", "titulo", "local", "ativo", "cliente", "unidade")).lower():
                continue
            r = _resumo(c, o)
            if sla_sit and r["sla"]["situacao"] != sla_sit:
                continue
            out.append(r)
        return filtrar(u, out)


@app.post("/api/ordens")
def abrir(corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario)
        if u["perfil"] == "tecnico":
            raise HTTPException(403, "Técnicos executam OS; a abertura é do solicitante, do planejador ou do gestor.")
        if u["perfil"] == "solicitante":  # solicitante não ajusta checklist/fotos/aprovação
            corpo = {k: v for k, v in corpo.items() if k not in ("checklist", "fotos_antes_min", "fotos_depois_min", "exige_aprovacao")}
        numero = criar_os(c, u, corpo)
        B.auditar(c, u, "OS aberta", numero)
        _publicar_os(c, numero, u)
        return filtrar(u, _resumo(c, _os(c, numero)))


@app.get("/api/ordens/{numero}")
def obter(numero: str, x_usuario: str | None = Header(None)):
    with B.con() as c:
        u = B.usuario(c, x_usuario); o = _os(c, numero)
        if not _pode_ver(u, o):
            raise HTTPException(403, "Você só vê as OS que abriu.")
        eu_membro = _membro_do_usuario(c, u, numero)
        snap = _snap(o)
        anexos_modelo = [dict(a) for a in c.execute("SELECT id,nome FROM modelo_anexos WHERE modelo_id=?", (o["modelo_id"],))] if o["modelo_id"] else []
        return filtrar(u, {**_resumo(c, o), "conflitos": conflitos(c, o), "participantes": participantes(c, numero),
                           "membros": [dict(m) for m in c.execute("SELECT * FROM membros WHERE equipe_id=? AND ativo=1 ORDER BY nome", (o["equipe_id"] or "",))],
                           "eu": {"participante": bool(eu_membro), "executor": bool(eu_membro and eu_membro["executor"]),
                                  "pode_executar": u["perfil"] in GESTAO or bool(eu_membro and eu_membro["executor"]),
                                  "concluiu": bool(o["concluida_por_id"]) and o["concluida_por_id"] == u["id"], "master": B.master(u)},
                           "regras": {k: snap.get(k) for k in ("fotos_antes_min", "fotos_depois_min", "exige_aprovacao", "exige_aceite", "orientacoes", "composicao")},
                           "anexos_modelo": anexos_modelo,
                           "itens_checklist": [dict(r) for r in c.execute("SELECT * FROM checklist WHERE numero=? ORDER BY seq", (numero,))],
                           "materiais": [dict(r) for r in c.execute("SELECT * FROM os_materiais WHERE numero=? ORDER BY descricao", (numero,))],
                           "apontamentos": [dict(r) for r in c.execute("SELECT * FROM apontamentos WHERE numero=? ORDER BY data DESC, em DESC", (numero,))],
                           "eventos": [dict(r) for r in c.execute("SELECT * FROM eventos WHERE numero=? ORDER BY seq DESC", (numero,))],
                           "anexos": [dict(r) for r in c.execute("SELECT * FROM anexos WHERE numero=? ORDER BY em DESC", (numero,))]})


CAMPOS_EDITAVEIS = ("titulo", "descricao", "unidade", "local", "ativo", "contato", "cliente", "codigo_projeto")


@app.put("/api/ordens/{numero}")
def editar(numero: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); o = _os(c, numero)
        B.exigir(u, *GESTAO, msg="Somente planejador, supervisor ou gestor alteram os dados da OS.")
        if o["status"] in FINAIS and u["perfil"] != "gestor":
            raise HTTPException(409, "OS finalizada não pode ser alterada.")
        for k in CAMPOS_EDITAVEIS:
            if k in corpo:
                c.execute(f"UPDATE ordens SET {k}=? WHERE numero=?", (corpo[k] or None, numero))
        if "prioridade" in corpo and corpo["prioridade"] != o["prioridade"]:
            if corpo["prioridade"] not in PRIORIDADES:
                raise HTTPException(400, "Prioridade inválida.")
            horas = (_snap(o).get("sla_horas") or {}).get(corpo["prioridade"])
            prazo = _iso(_dt(o["aberta_em"]) + timedelta(hours=float(horas))) if horas else None
            c.execute("UPDATE ordens SET prioridade=?, prazo_sla=? WHERE numero=?", (corpo["prioridade"], prazo, numero))
            _evento(c, numero, u, "Prioridade alterada", o["prioridade"], corpo["prioridade"], str(corpo.get("justificativa") or ""))
        c.execute("UPDATE ordens SET atualizado_em=? WHERE numero=?", (agora(), numero))
        _evento(c, numero, u, "Dados atualizados")
        _publicar_os(c, numero, u)
        return filtrar(u, _resumo(c, _os(c, numero)))


# ── feriados (calendário do hub; fora dele, só os nacionais)
def _localidade_equipe(c, equipe_id: str | None) -> str | None:
    eq = c.execute("SELECT unidade FROM equipes WHERE id=?", (equipe_id,)).fetchone() if equipe_id else None
    if not eq or not eq["unidade"]:
        return None
    for un in B.org().get("unidades") or []:
        if eq["unidade"] in (un.get("codigo"), un.get("nome")):
            return un.get("localidade") or None
    return None


def _escala(m: dict) -> bool:
    """Membro trabalha em feriados? Herda da pessoa no hub; membro sem usuário usa a marcação do cadastro da equipe."""
    uid = m.get("usuario_id") or ""
    if uid.startswith("h-"):
        p = hubkit.pessoa(id_hub=uid[2:])
        if p is not None:
            return bool(p.get("escala_feriado"))
    return bool(m.get("escala_feriado"))


def _checar_feriado(c, equipe_id: str, inicio: datetime, membros: list[dict], ciencia: bool, u: dict) -> dict | None:
    """Retorna o registro de feriado a gravar na OS (ou None). Exige ciência quando há membro sem escala."""
    dia = _local(inicio).date().isoformat()
    f = hubkit.feriado_em(dia, _localidade_equipe(c, equipe_id))
    if not f:
        return None
    sem = [m["nome"] for m in membros if not _escala(m)]
    if not sem:
        return {"data": dia, "nome": f["nome"], "sem_escala": [], "situacao": "escala"}
    if not ciencia:
        raise HTTPException(409, {"codigo": "FERIADO", "mensagem": f"{dia[8:]}/{dia[5:7]} é feriado ({f['nome']}). Trabalho em feriado não é o padrão.",
                                  "feriado": f, "sem_escala": sem})
    return {"data": dia, "nome": f["nome"], "sem_escala": sem, "situacao": "aguardando_gestor", "ciente_por": u["nome"], "ciente_em": agora()}


def _definir_participantes(c, numero: str, equipe_id: str, ids: list | None, executor_id: str | None) -> list[dict]:
    membros = {m["id"]: dict(m) for m in c.execute("SELECT * FROM membros WHERE equipe_id=? AND ativo=1 ORDER BY rowid", (equipe_id,))}
    if not membros:
        raise HTTPException(400, "A equipe não tem membros ativos.")
    ids = [i for i in (ids or list(membros)) if i in membros]
    if not ids:
        raise HTTPException(400, "Escolha quem da equipe vai nesta OS.")
    pode = {r["nome"]: bool(r["pode_executor"]) for r in c.execute("SELECT * FROM funcoes")}
    elegivel = lambda m: pode.get(m.get("funcao") or "", True)
    if executor_id:
        if executor_id not in ids:
            raise HTTPException(400, "O executor precisa estar entre os participantes da OS.")
        if not elegivel(membros[executor_id]):
            raise HTTPException(400, f"A função {membros[executor_id].get('funcao') or '—'} não pode ser executora. Escolha outra pessoa.")
    else:
        cand = [i for i in ids if elegivel(membros[i])]
        if not cand:
            raise HTTPException(400, "Nenhum participante pode ser executor (ex.: só auxiliares). Inclua alguém com função executora.")
        executor_id = cand[0]
    c.execute("DELETE FROM os_participantes WHERE numero=?", (numero,))
    for i in ids:
        c.execute("INSERT INTO os_participantes(numero,membro_id,executor) VALUES (?,?,?)", (numero, i, 1 if i == executor_id else 0))
    return [membros[i] for i in ids]


@app.post("/api/ordens/{numero}/planejar")
def planejar(numero: str, corpo: dict, x_usuario: str | None = Header(None)):
    """Equipe, quem vai (participantes), UM executor, data/hora e duração. Conflitos avisam; feriado pede ciência."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); o = _os(c, numero)
        B.exigir(u, *GESTAO, msg="Somente planejador, supervisor ou gestor planejam OS.")
        if o["status"] not in ("aberta", "planejada", "pausada"):
            raise HTTPException(409, f"OS {STATUS[o['status']].lower()} não pode ser (re)planejada.")
        eq = c.execute("SELECT * FROM equipes WHERE id=? AND ativo=1", (corpo.get("equipe_id"),)).fetchone()
        ini = _dt(corpo.get("execucao_inicio"))
        try:
            dur = float(corpo.get("duracao_h") or 0)
        except (TypeError, ValueError):
            dur = 0
        if not eq or not ini or dur <= 0:
            raise HTTPException(400, "Informe equipe, data/hora de execução e duração estimada (horas).")
        quem = _definir_participantes(c, numero, eq["id"], corpo.get("participantes"), corpo.get("executor_id"))
        fer = _checar_feriado(c, eq["id"], ini, quem, bool(corpo.get("ciencia_feriado")), u)
        novo = "planejada" if o["status"] == "aberta" else o["status"]
        c.execute("UPDATE ordens SET equipe_id=?, execucao_inicio=?, duracao_h=?, status=?, planejada_em=COALESCE(planejada_em,?), atualizado_em=?, feriado=? WHERE numero=?",
                  (eq["id"], _iso(ini), dur, novo, agora(), agora(), dump(fer) if fer else None, numero))
        ex = next(m for m in quem if c.execute("SELECT executor FROM os_participantes WHERE numero=? AND membro_id=?", (numero, m["id"])).fetchone()[0])
        _evento(c, numero, u, "OS planejada", o["status"], novo, f"{eq['nome']} · {_local(ini).strftime('%d/%m %H:%M')} · {dur:g} h · executor {ex['nome']} · {len(quem)} pessoa(s)"
                + (f" · feriado ({fer['nome']}): aguardando de acordo do gestor" if fer and fer["situacao"] == "aguardando_gestor" else ""))
        B.auditar(c, u, "OS planejada", numero, {"equipe": eq["id"], "inicio": _iso(ini), "duracao_h": dur, "executor": ex["id"]})
        _publicar_os(c, numero, u)
        o = _os(c, numero)
        return filtrar(u, {**_resumo(c, o), "conflitos": conflitos(c, o), "participantes": participantes(c, numero)})


@app.post("/api/ordens/{numero}/replanejar")
def replanejar(numero: str, corpo: dict, x_usuario: str | None = Header(None)):
    """Arrastar no quadro das equipes: novo início (e duração). Mesma equipe; trocar de equipe abre o planejamento.
    Proteções: só OS aberta(s) planejada/pausada; SLA estourado pede confirmação; feriado segue o fluxo de ciência."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); o = _os(c, numero)
        B.exigir(u, *GESTAO, msg="Somente planejador, supervisor ou gestor replanejam.")
        if o["status"] not in ("planejada", "pausada"):
            raise HTTPException(409, {"codigo": "STATUS", "mensagem": f"OS {STATUS[o['status']].lower()} não pode ser arrastada."})
        if corpo.get("equipe_id") and corpo["equipe_id"] != o["equipe_id"]:
            raise HTTPException(409, {"codigo": "EQUIPE", "mensagem": "Trocar de equipe muda quem vai e o executor: confirme no planejamento."})
        ini = _dt(corpo.get("execucao_inicio")) or _dt(o["execucao_inicio"])
        dur = float(corpo.get("duracao_h") or o["duracao_h"] or 1)
        if not ini or dur <= 0:
            raise HTTPException(400, "Início/duração inválidos.")
        fim = ini + timedelta(hours=dur); prazo = _dt(o["prazo_sla"])
        if prazo and fim > prazo and not corpo.get("confirmar_sla"):
            raise HTTPException(409, {"codigo": "SLA", "mensagem": f"Com esta data o SLA estoura em {round((fim - prazo).total_seconds() / 3600, 1)} h.",
                                      "excede_h": round((fim - prazo).total_seconds() / 3600, 1)})
        quem = participantes(c, numero) or [dict(m) for m in c.execute("SELECT * FROM membros WHERE equipe_id=? AND ativo=1", (o["equipe_id"],))]
        fer = _checar_feriado(c, o["equipe_id"], ini, quem, bool(corpo.get("ciencia_feriado")), u)
        antes = o["execucao_inicio"]
        c.execute("UPDATE ordens SET execucao_inicio=?, duracao_h=?, feriado=?, atualizado_em=? WHERE numero=?", (_iso(ini), dur, dump(fer) if fer else None, agora(), numero))
        _evento(c, numero, u, "OS replanejada", _local(_dt(antes)).strftime("%d/%m %H:%M") if antes else "", _local(ini).strftime("%d/%m %H:%M") + f" · {dur:g} h",
                str(corpo.get("motivo") or ""))
        _publicar_os(c, numero, u)
        o = _os(c, numero)
        return filtrar(u, {**_resumo(c, o), "conflitos": conflitos(c, o), "anterior": {"execucao_inicio": antes}})


@app.post("/api/ordens/{numero}/feriado")
def decidir_feriado(numero: str, corpo: dict, x_usuario: str | None = Header(None)):
    """De acordo do gestor de serviços para OS em feriado com membro sem escala. Sem de acordo, a OS volta a planejar."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); o = _os(c, numero)
        B.exigir(u, "gestor", "supervisor", msg="O de acordo para OS em feriado é do gestor de serviços.")
        f = load(o["feriado"], None)
        if not f or f.get("situacao") != "aguardando_gestor":
            raise HTTPException(409, "Esta OS não está aguardando de acordo de feriado.")
        if corpo.get("decisao") == "de_acordo":
            f.update(situacao="de_acordo", decidido_por=u["nome"], decidido_em=agora())
            c.execute("UPDATE ordens SET feriado=?, atualizado_em=? WHERE numero=?", (dump(f), agora(), numero))
            _evento(c, numero, u, "De acordo para execução em feriado", "", "", f"{f['nome']} · {', '.join(f['sem_escala'])}")
        else:
            c.execute("UPDATE ordens SET status='aberta', execucao_inicio=NULL, feriado=NULL, motivo=?, atualizado_em=? WHERE numero=?",
                      (f"Sem de acordo para feriado ({f['nome']})" + (f": {corpo.get('comentario')}" if corpo.get("comentario") else ""), agora(), numero))
            _evento(c, numero, u, "Sem de acordo para feriado: OS volta para planejamento", o["status"], "aberta", str(corpo.get("comentario") or ""))
        _publicar_os(c, numero, u)
        return filtrar(u, _resumo(c, _os(c, numero)))


TRANS = {"iniciar": ({"planejada", "pausada"}, "em_execucao"), "pausar": ({"em_execucao"}, "pausada"),
         "concluir": ({"em_execucao"}, "concluida"), "encerrar": ({"concluida"}, "encerrada"), "devolver": ({"concluida"}, "pausada"),
         "reabrir": ({"concluida", "encerrada"}, "pausada"), "cancelar": ({"aberta", "planejada", "em_execucao", "pausada"}, "cancelada")}
QUEM_TRANS = {"iniciar": ("gestor", "supervisor", "planejador", "tecnico"), "pausar": ("gestor", "supervisor", "planejador", "tecnico"),
              "concluir": ("gestor", "supervisor", "tecnico"), "encerrar": ("gestor", "supervisor"), "devolver": ("gestor", "supervisor"),
              "reabrir": ("gestor",), "cancelar": ("gestor", "supervisor", "planejador")}
ROTULO = {"iniciar": "Execução iniciada", "pausar": "OS pausada", "concluir": "OS concluída", "encerrar": "OS encerrada (aprovada)",
          "devolver": "Encerramento devolvido", "reabrir": "OS reaberta", "cancelar": "OS cancelada"}


def _quando(corpo: dict) -> datetime:
    """Momento real da ação. O modo execução funciona sem sinal e envia depois a hora em que o técnico tocou no botão
    ("em"); aceita até 72 h para trás e nunca no futuro."""
    agora_ = _agora_dt(); d = _dt(corpo.get("em"))
    return d if d and agora_ - timedelta(hours=72) <= d <= agora_ + timedelta(minutes=5) else agora_


def _apontar_cronometro(c, o, u, fim: datetime | None = None) -> float:
    """Fecha o cronômetro: aponta o tempo decorrido para cada participante (horas automáticas)."""
    ini = _dt(o["cronometro_inicio"])
    if not ini:
        return 0.0
    horas = round(max(0.0, ((fim or _agora_dt()) - ini).total_seconds() / 3600), 2)
    if horas >= 0.01:
        for m in participantes(c, o["numero"]) or []:
            c.execute("INSERT INTO apontamentos(id,numero,membro_id,nome,data,horas,custo_hora,obs,por,em) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      ("H-" + uuid.uuid4().hex[:8], o["numero"], m["id"], m["nome"], _local(ini).date().isoformat(), horas, m["custo_hora"],
                       "automático (cronômetro)", u["nome"], agora()))
    c.execute("UPDATE ordens SET cronometro_inicio=NULL WHERE numero=?", (o["numero"],))
    return horas


def _fotos(c, numero: str, etapa: str | None = None, seq: int | None = None) -> int:
    sql, a = "SELECT COUNT(*) FROM anexos WHERE numero=?", [numero]
    if etapa:
        sql += " AND etapa=?"; a.append(etapa)
    if seq is not None:
        sql += " AND checklist_seq=?"; a.append(seq)
    return c.execute(sql, a).fetchone()[0]


def _pode_executar(c, u: dict, o) -> None:
    if u["perfil"] in GESTAO:
        return
    if u["perfil"] != "tecnico":
        raise HTTPException(403, "Seu perfil não registra execução.")
    m = _membro_do_usuario(c, u, o["numero"])
    if not m or not m["executor"]:
        raise HTTPException(403, "Só o executor desta OS registra a execução; os demais participantes acompanham.")
    if o["status"] in ("encerrada", "cancelada"):
        raise HTTPException(409, "OS finalizada.")


@app.post("/api/ordens/{numero}/acoes/{acao}")
def transicao(numero: str, acao: str, corpo: dict | None = None, x_usuario: str | None = Header(None)):
    corpo = corpo or {}
    if acao not in TRANS:
        raise HTTPException(404, "Ação desconhecida.")
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); o = _os(c, numero)
        B.exigir(u, *QUEM_TRANS[acao], msg="Seu perfil não permite esta ação.")
        de, para = TRANS[acao]
        if o["status"] not in de:
            raise HTTPException(409, f"Não é possível {acao} uma OS {STATUS[o['status']].lower()}.")
        if acao in ("iniciar", "pausar", "concluir"):
            _pode_executar(c, u, o)
        excecao = None
        if acao in ("encerrar", "devolver") and o["concluida_por_id"] and o["concluida_por_id"] == u["id"]:
            if not B.master(u):  # segregação de funções: quem concluiu não aprova (nem devolve) o próprio encerramento
                raise HTTPException(403, "Quem concluiu a OS não aprova o próprio encerramento. A aprovação é de outro supervisor ou do gestor.")
            excecao = "aprovou o que ele mesmo concluiu"
        motivo = str(corpo.get("motivo") or corpo.get("comentario") or "").strip()
        if acao in ("pausar", "cancelar", "reabrir", "devolver") and not motivo:
            raise HTTPException(400, "Informe o motivo.")
        f = load(o["feriado"], None)
        if acao == "iniciar":
            if f and f.get("situacao") == "aguardando_gestor":
                raise HTTPException(409, "Esta OS em feriado aguarda o de acordo do gestor de serviços.")
            if not participantes(c, numero):
                raise HTTPException(409, "Planeje a OS (equipe e executor) antes de iniciar.")
        snap = _snap(o)
        if acao == "concluir":
            falta = []
            for r in c.execute("SELECT * FROM checklist WHERE numero=? AND obrigatorio=1", (numero,)):
                if not r["feito"]:
                    falta.append(r["item"])
                elif r["foto_min"] and _fotos(c, numero, seq=r["seq"]) < r["foto_min"]:
                    falta.append(f"{r['item']} (foto)")
            if falta:
                raise HTTPException(409, "Checklist incompleto: " + "; ".join(falta[:5]) + ("…" if len(falta) > 5 else ""))
            if c.execute("SELECT 1 FROM os_materiais WHERE numero=? AND status IN ('a_separar','separado')", (numero,)).fetchone():
                raise HTTPException(409, "Há materiais sem baixa: marque como consumido ou devolvido antes de concluir.")
            for etapa, k in (("antes", "fotos_antes_min"), ("depois", "fotos_depois_min")):
                if int(snap.get(k) or 0) > _fotos(c, numero, etapa=etapa):
                    raise HTTPException(409, f"Faltam fotos do {etapa} ({snap.get(k)} obrigatória(s)).")
            if snap.get("exige_aceite") and not str(corpo.get("aceite_nome") or "").strip():
                raise HTTPException(409, "Este tipo de OS exige o aceite do cliente (nome de quem recebeu).")
        momento = _quando(corpo)
        if acao in ("pausar", "concluir"):
            _apontar_cronometro(c, o, u, momento)
        if acao == "concluir" and not snap.get("exige_aprovacao", True):
            para = "encerrada"
        sets = ["status=?", "motivo=?", "atualizado_em=?"]
        args = [para, motivo or o["motivo"], agora()]
        if acao == "iniciar":
            sets += ["iniciada_em=COALESCE(iniciada_em,?)", "cronometro_inicio=?"]; args += [_iso(momento), _iso(momento)]
        if acao == "concluir":
            sets += ["concluida_em=?", "resumo_execucao=?", "aceite_nome=?", "aceite_em=?", "aprovacao=?", "concluida_por_id=?"]
            nome_aceite = str(corpo.get("aceite_nome") or "").strip() or None
            args += [_iso(momento), str(corpo.get("resumo") or "").strip() or None, nome_aceite, _iso(momento) if nome_aceite else None,
                     "pendente" if para == "concluida" else None, u["id"]]
            if para == "encerrada":
                sets.append("encerrada_em=?"); args.append(agora())
        if acao == "encerrar":
            sets += ["encerrada_em=?", "aprovacao=?"]; args += [agora(), "aprovada"]
        if acao == "devolver":
            sets += ["concluida_em=NULL", "aprovacao=?", "concluida_por_id=NULL"]; args += ["devolvida"]
        if acao == "reabrir":
            sets += ["concluida_em=NULL", "encerrada_em=NULL", "aprovacao=NULL", "concluida_por_id=NULL"]
        c.execute(f"UPDATE ordens SET {', '.join(sets)} WHERE numero=?", (*args, numero))
        _evento(c, numero, u, ROTULO[acao] if not (acao == "concluir" and para == "encerrada") else "OS concluída e encerrada (tipo sem aprovação)",
                o["status"], para, motivo or str(corpo.get("resumo") or ""))
        papel = {"iniciar": "executor", "pausar": "executor", "concluir": "executor", "encerrar": "supervisor", "devolver": "supervisor",
                 "reabrir": "gestor", "cancelar": "planejamento"}.get(acao)
        B.auditar(c, u, f"OS {acao}", numero, motivo + (f" [exceção de teste: {excecao}]" if excecao else ""), papel=papel)
        _publicar_os(c, numero, u)
        return filtrar(u, _resumo(c, _os(c, numero)))


@app.put("/api/ordens/{numero}/checklist")
def salvar_checklist(numero: str, corpo: dict, x_usuario: str | None = Header(None)):
    """corpo: {itens: [{seq?, item, obrigatorio, feito, obs, resposta, foto_min, resposta_tipo, unidade}], parcial?, redefinir?}
    — planejamento edita itens; executor marca feito/resposta/obs (exige resposta e fotos mínimas do item)."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); o = _os(c, numero); _pode_executar(c, u, o)
        plan = u["perfil"] in GESTAO
        atuais = {r["seq"]: r for r in c.execute("SELECT * FROM checklist WHERE numero=?", (numero,))}
        if plan and corpo.get("redefinir"):
            c.execute("DELETE FROM checklist WHERE numero=?", (numero,)); atuais = {}
        prox = max(atuais) + 1 if atuais else 1
        for it in corpo.get("itens") or []:
            seq = it.get("seq")
            if seq in atuais:
                a = atuais[seq]; feito = 1 if it.get("feito") else 0
                resposta = it.get("resposta", a["resposta"])
                if feito and not a["feito"]:
                    if a["resposta_tipo"] != "check" and resposta in (None, ""):
                        raise HTTPException(409, f"Responda o item “{a['item']}” antes de marcar como feito.")
                    if a["resposta_tipo"] == "numero" and resposta not in (None, ""):
                        try:
                            float(str(resposta).replace(",", "."))
                        except ValueError:
                            raise HTTPException(400, f"“{a['item']}” pede um número.")
                    if a["foto_min"] and _fotos(c, numero, seq=seq) < a["foto_min"]:
                        raise HTTPException(409, f"“{a['item']}” exige {a['foto_min']} foto(s).")
                mudou = feito != a["feito"]
                c.execute("UPDATE checklist SET feito=?, obs=?, resposta=?, por=CASE WHEN ? THEN ? ELSE por END, em=CASE WHEN ? THEN ? ELSE em END WHERE numero=? AND seq=?",
                          (feito, it.get("obs", a["obs"]), resposta, mudou, u["nome"], mudou, agora(), numero, seq))
                if plan and str(it.get("item") or "").strip():
                    t = it.get("resposta_tipo") if it.get("resposta_tipo") in RESPOSTAS else a["resposta_tipo"]
                    c.execute("UPDATE checklist SET item=?, obrigatorio=?, foto_min=?, resposta_tipo=?, unidade=? WHERE numero=? AND seq=?",
                              (it["item"], 1 if it.get("obrigatorio", True) else 0, int(it.get("foto_min", a["foto_min"]) or 0), t,
                               it.get("unidade", a["unidade"]), numero, seq))
            elif plan and str(it.get("item") or "").strip():
                lim = _limpar_checklist([it])[0]
                c.execute("INSERT INTO checklist(numero,seq,item,obrigatorio,feito,obs,foto_min,resposta_tipo,unidade) VALUES (?,?,?,?,?,?,?,?,?)",
                          (numero, prox, lim["item"], 1 if lim["obrigatorio"] else 0, 1 if it.get("feito") else 0, it.get("obs"), lim["foto_min"],
                           lim["resposta_tipo"], lim["unidade"])); prox += 1
        if plan and corpo.get("itens") is not None and not corpo.get("parcial"):
            manter = {it.get("seq") for it in corpo["itens"] if it.get("seq")}
            for seq in set(atuais) - manter:
                c.execute("DELETE FROM checklist WHERE numero=? AND seq=?", (numero, seq))
        c.execute("UPDATE ordens SET atualizado_em=? WHERE numero=?", (agora(), numero))
        return [dict(r) for r in c.execute("SELECT * FROM checklist WHERE numero=? ORDER BY seq", (numero,))]


@app.post("/api/ordens/{numero}/materiais")
def adicionar_material(numero: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); o = _os(c, numero)
        B.exigir(u, *GESTAO, msg="A lista de materiais é do planejamento.")
        if o["status"] in FINAIS:
            raise HTTPException(409, "OS finalizada.")
        cat = c.execute("SELECT * FROM materiais WHERE codigo=?", (corpo.get("codigo"),)).fetchone() if corpo.get("codigo") else None
        desc = (cat["descricao"] if cat else str(corpo.get("descricao") or "").strip())
        if not desc:
            raise HTTPException(400, "Escolha um material do catálogo ou descreva o item.")
        try:
            qtd = float(corpo.get("qtd_prevista") or 0); custo = float(corpo.get("custo_unitario") if corpo.get("custo_unitario") not in (None, "") else (cat["custo_unitario"] if cat else 0))
        except (TypeError, ValueError):
            raise HTTPException(400, "Quantidade/custo inválidos.")
        if qtd <= 0:
            raise HTTPException(400, "Informe a quantidade prevista.")
        mid = "M-" + uuid.uuid4().hex[:8]
        c.execute("INSERT INTO os_materiais(id,numero,codigo,descricao,unidade_medida,qtd_prevista,custo_unitario,status,atualizado_por,atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (mid, numero, cat["codigo"] if cat else None, desc, cat["unidade_medida"] if cat else (corpo.get("unidade_medida") or "un"), qtd, custo, "a_separar", u["nome"], agora()))
        _evento(c, numero, u, "Material incluído", "", desc, f"{qtd:g}")
        _publicar_os(c, numero, u)
        return filtrar(u, dict(c.execute("SELECT * FROM os_materiais WHERE id=?", (mid,)).fetchone()))


@app.put("/api/ordens/{numero}/materiais/{mid}")
def atualizar_material(numero: str, mid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); o = _os(c, numero); _pode_executar(c, u, o)
        m = c.execute("SELECT * FROM os_materiais WHERE id=? AND numero=?", (mid, numero)).fetchone()
        if not m:
            raise HTTPException(404, "Material não encontrado nesta OS.")
        st = corpo.get("status", m["status"])
        if st not in MAT_STATUS:
            raise HTTPException(400, "Status de material inválido.")
        try:
            qreal = float(corpo.get("qtd_real", m["qtd_real"] if m["qtd_real"] else (m["qtd_prevista"] if st == "consumido" else 0)) or 0)
            qprev = float(corpo.get("qtd_prevista", m["qtd_prevista"]))
        except (TypeError, ValueError):
            raise HTTPException(400, "Quantidade inválida.")
        if u["perfil"] not in GESTAO:
            qprev = m["qtd_prevista"]
        if corpo.get("remover") and u["perfil"] in GESTAO and m["status"] == "a_separar":
            c.execute("DELETE FROM os_materiais WHERE id=?", (mid,))
            _evento(c, numero, u, "Material removido", m["descricao"])
            _publicar_os(c, numero, u)
            return {"ok": True}
        c.execute("UPDATE os_materiais SET status=?, qtd_real=?, qtd_prevista=?, atualizado_por=?, atualizado_em=? WHERE id=?",
                  (st, qreal if st == "consumido" else (0 if st == "devolvido" else qreal), qprev, u["nome"], agora(), mid))
        if st != m["status"]:
            _evento(c, numero, u, "Material " + MAT_STATUS[st].lower(), MAT_STATUS[m["status"]], MAT_STATUS[st], m["descricao"])
        _publicar_os(c, numero, u)
        return filtrar(u, dict(c.execute("SELECT * FROM os_materiais WHERE id=?", (mid,)).fetchone()))


@app.post("/api/ordens/{numero}/apontamentos")
def apontar(numero: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); o = _os(c, numero); _pode_executar(c, u, o)
        if o["status"] not in ("em_execucao", "pausada", "concluida"):
            raise HTTPException(409, "Horas são apontadas a partir do início da execução.")
        mb = c.execute("SELECT * FROM membros WHERE id=?", (corpo.get("membro_id"),)).fetchone() if corpo.get("membro_id") else None
        nome = mb["nome"] if mb else str(corpo.get("nome") or u["nome"])
        try:
            horas = float(corpo.get("horas") or 0)
        except (TypeError, ValueError):
            horas = 0
        if horas <= 0 or horas > 24:
            raise HTTPException(400, "Informe as horas trabalhadas (até 24 por apontamento).")
        aid = "H-" + uuid.uuid4().hex[:8]
        c.execute("INSERT INTO apontamentos(id,numero,membro_id,nome,data,horas,custo_hora,obs,por,em) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (aid, numero, mb["id"] if mb else None, nome, str(corpo.get("data") or agora()[:10])[:10], horas,
                   float(mb["custo_hora"]) if mb else (float(corpo.get("custo_hora") or 0) if u["perfil"] in GESTAO else 0), corpo.get("obs"), u["nome"], agora()))
        _evento(c, numero, u, "Horas apontadas", "", nome, f"{horas:g} h")
        _publicar_os(c, numero, u)
        return filtrar(u, dict(c.execute("SELECT * FROM apontamentos WHERE id=?", (aid,)).fetchone()))


@app.delete("/api/ordens/{numero}/apontamentos/{aid}")
def remover_apontamento(numero: str, aid: str, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, *GESTAO)
        if not c.execute("DELETE FROM apontamentos WHERE id=? AND numero=?", (aid, numero)).rowcount:
            raise HTTPException(404, "Apontamento não encontrado.")
        _evento(c, numero, u, "Apontamento removido", aid)
        _publicar_os(c, numero, u)
        return {"ok": True}


@app.post("/api/ordens/{numero}/anexos")
async def anexar(numero: str, arquivo: UploadFile = File(...), etapa: str = Form("geral"), checklist_seq: str = Form(""),
                 x_usuario: str | None = Header(None)):
    """Fotos e documentos. etapa: antes | depois | item (com checklist_seq) | geral."""
    dados = await arquivo.read()
    if len(dados) > 20 * 1024 * 1024:
        raise HTTPException(413, "Arquivo acima de 20 MB.")
    if etapa not in ("antes", "depois", "item", "geral"):
        etapa = "geral"
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); o = _os(c, numero)
        if not _pode_ver(u, o):
            raise HTTPException(403, "Sem acesso a esta OS.")
        if etapa != "geral" or u["perfil"] == "tecnico":
            _pode_executar(c, u, o)
        seq = int(checklist_seq) if str(checklist_seq).isdigit() else None
        aid = uuid.uuid4().hex
        ext = re.sub(r"[^a-z0-9]", "", Path(arquivo.filename or "").suffix.lower())[:8]
        (B.arquivos / f"{aid}.{ext or 'bin'}").write_bytes(dados)
        c.execute("INSERT INTO anexos(id,numero,nome,arquivo,por,em,etapa,checklist_seq) VALUES (?,?,?,?,?,?,?,?)",
                  (aid, numero, arquivo.filename or "anexo", f"{aid}.{ext or 'bin'}", u["nome"], agora(), etapa if seq is None else "item", seq))
        _evento(c, numero, u, "Anexo incluído", "", arquivo.filename or "", etapa + (f" · item {seq}" if seq else ""))
        return {"id": aid, "etapa": etapa if seq is None else "item", "checklist_seq": seq}


@app.get("/api/anexos/{aid}")
def baixar_anexo(aid: str, x_usuario: str | None = Header(None)):
    with B.con() as c:
        u = B.usuario(c, x_usuario)
        a = c.execute("SELECT * FROM anexos WHERE id=?", (aid,)).fetchone()
        if not a or not _pode_ver(u, _os(c, a["numero"])):
            raise HTTPException(404, "Anexo não encontrado.")
        return FileResponse(B.arquivos / a["arquivo"], filename=a["nome"])


# ───────────────────────────────────────────────── quadro das equipes e painel
@app.get("/api/agenda")
def agenda(inicio: str = "", dias: int = 7, equipe: str = "", x_usuario: str | None = Header(None)):
    """OS com execução no período por equipe, conflitos de horário, OS a planejar e feriados de cada equipe."""
    with B.con() as c:
        u = B.usuario(c, x_usuario)
        if u["perfil"] == "solicitante":
            raise HTTPException(403, "A agenda das equipes é da operação.")
        try:
            ini = datetime.fromisoformat(inicio).replace(tzinfo=timezone.utc) if inicio else None
        except ValueError:
            raise HTTPException(400, "Data inicial inválida (AAAA-MM-DD).")
        if not ini:
            hoje = _local(_agora_dt()).replace(hour=0, minute=0, second=0, microsecond=0)
            ini = (hoje - timedelta(days=hoje.weekday())).replace(tzinfo=timezone.utc)
        fim = ini + timedelta(days=max(1, min(dias, 42)))
        equipes = [dict(e) for e in c.execute("SELECT * FROM equipes WHERE ativo=1 ORDER BY nome") if not equipe or e["id"] == equipe]
        itens = []
        for o in c.execute("SELECT * FROM ordens WHERE execucao_inicio IS NOT NULL AND status<>'cancelada' ORDER BY execucao_inicio"):
            oi, of = _dt(o["execucao_inicio"]), _fim_previsto(o)
            if oi and of and oi < fim + timedelta(days=1) and ini - timedelta(days=1) < of and (not equipe or o["equipe_id"] == equipe):
                itens.append({**_resumo(c, o), "fim_previsto": _iso(of), "conflitos": [x["numero"] for x in conflitos(c, o)]})
        cap = {e["id"]: len(c.execute("SELECT 1 FROM membros WHERE equipe_id=? AND ativo=1", (e["id"],)).fetchall()) for e in equipes}
        a_planejar = [_resumo(c, o) for o in c.execute("SELECT * FROM ordens WHERE status='aberta' ORDER BY prazo_sla")]
        d0, d1 = ini.date().isoformat(), (fim - timedelta(days=1)).date().isoformat()
        fer = {e["id"]: [f for f in hubkit.feriados(d0, d1, _localidade_equipe(c, e["id"])) if f.get("conta")] for e in equipes}
        return filtrar(u, {"inicio": d0, "fim": fim.date().isoformat(), "fuso_horas": B.config().get("fuso_horas", -3),
                           "equipes": [{**e, "membros": cap[e["id"]]} for e in equipes], "itens": itens, "a_planejar": a_planejar, "feriados": fer})


@app.get("/api/painel")
def painel(x_usuario: str | None = Header(None)):
    with B.con() as c:
        u = B.usuario(c, x_usuario)
        ordens = [_resumo(c, o) for o in c.execute("SELECT * FROM ordens") if _pode_ver(u, o)]
        abertas = [o for o in ordens if o["status"] not in FINAIS]
        por = lambda chave, lista: {k: sum(1 for o in lista if o[chave] == k) for k in sorted({o[chave] for o in lista if o[chave]})}
        fin = [o for o in ordens if o["status"] in ("concluida", "encerrada")]
        tempos = [(_dt(o["concluida_em"]) - _dt(o["aberta_em"])).total_seconds() / 3600 for o in fin if o["concluida_em"]]
        return filtrar(u, {"total": len(ordens), "abertas": len(abertas),
                           "por_status": {s: sum(1 for o in ordens if o["status"] == s) for s in STATUS},
                           "sla": {s: sum(1 for o in abertas if o["sla"]["situacao"] == s) for s in ("no_prazo", "em_risco", "atrasada")},
                           "sla_cumprido_pct": round(100 * sum(1 for o in fin if o["sla"]["situacao"] == "cumprido") / len(fin), 1) if fin else None,
                           "tempo_medio_h": round(sum(tempos) / len(tempos), 1) if tempos else None,
                           "aguardando_aprovacao": sum(1 for o in ordens if o["status"] == "concluida" and o.get("aprovacao") == "pendente"),
                           "por_tipo": por("tipo_nome", abertas), "por_prioridade": por("prioridade_nome", abertas), "por_unidade": por("unidade", abertas),
                           "custo_previsto": round(sum(o["custos"]["previsto"] for o in ordens if o["status"] != "cancelada"), 2),
                           "custo_real": round(sum(o["custos"]["real"] for o in ordens), 2),
                           "criticas": sorted([o for o in abertas if o["sla"]["situacao"] in ("atrasada", "em_risco")], key=lambda o: o["sla"]["restante_h"] or 0)[:15]})


# ───────────────────────────────────────────────── cadastros: funções, equipes, membros, materiais
@app.get("/api/funcoes")
def listar_funcoes(x_usuario: str | None = Header(None)):
    with B.con() as c:
        B.usuario(c, x_usuario)
        return [dict(r) for r in c.execute("SELECT * FROM funcoes ORDER BY ativo DESC, nome")]


@app.put("/api/funcoes")
def salvar_funcoes(corpo: dict, x_usuario: str | None = Header(None)):
    """{"funcoes": [{"nome": "Auxiliar", "pode_executor": false}, ...]}"""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "gestor", "planejador")
        for f in corpo.get("funcoes") or []:
            nome = str(f.get("nome") or "").strip()
            if nome:
                c.execute("INSERT INTO funcoes(nome,pode_executor,ativo) VALUES (?,?,?) ON CONFLICT(nome) DO UPDATE SET pode_executor=excluded.pode_executor, ativo=excluded.ativo",
                          (nome[:60], 1 if f.get("pode_executor", True) else 0, 0 if f.get("ativo") is False else 1))
        B.auditar(c, u, "Funções salvas", "")
        return listar_funcoes(x_usuario)


def _equipes(c) -> list[dict]:
    pode = {r["nome"]: bool(r["pode_executor"]) for r in c.execute("SELECT * FROM funcoes")}
    return [{**dict(e), "membros": [{**dict(m), "pode_executor": pode.get(m["funcao"] or "", True)} for m in c.execute("SELECT * FROM membros WHERE equipe_id=? ORDER BY nome", (e["id"],))],
             "custo_hora": float(c.execute("SELECT COALESCE(SUM(custo_hora),0) FROM membros WHERE equipe_id=? AND ativo=1", (e["id"],)).fetchone()[0])}
            for e in c.execute("SELECT * FROM equipes ORDER BY ativo DESC, nome").fetchall()]


@app.get("/api/equipes")
def listar_equipes(x_usuario: str | None = Header(None)):
    with B.con() as c:
        u = B.usuario(c, x_usuario)
        return filtrar(u, _equipes(c))


@app.put("/api/equipes/{eid}")
def salvar_equipe(eid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, *GESTAO)
        nome = str(corpo.get("nome") or "").strip()
        if not nome:
            raise HTTPException(400, "Informe o nome da equipe.")
        if eid == "nova":
            eid = "EQ-" + uuid.uuid4().hex[:6].upper()
        c.execute("INSERT INTO equipes(id,nome,unidade,especialidade,ativo) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET nome=excluded.nome, unidade=excluded.unidade, especialidade=excluded.especialidade, ativo=excluded.ativo",
                  (eid, nome, corpo.get("unidade") or None, corpo.get("especialidade") or None, 0 if corpo.get("ativo") is False else 1))
        if isinstance(corpo.get("membros"), list):
            ids = set()
            for m in corpo["membros"]:
                if not str(m.get("nome") or "").strip():
                    continue
                mid = m.get("id") or "MB-" + uuid.uuid4().hex[:6].upper(); ids.add(mid)
                func = str(m.get("funcao") or "").strip() or None
                if func and not c.execute("SELECT 1 FROM funcoes WHERE nome=?", (func,)).fetchone():
                    c.execute("INSERT INTO funcoes(nome,pode_executor) VALUES (?,?)", (func, 0 if re.search(r"aux|ajud", func, re.I) else 1))
                atual = c.execute("SELECT custo_hora FROM membros WHERE id=?", (mid,)).fetchone()
                custo = float(m.get("custo_hora")) if m.get("custo_hora") not in (None, "") else (atual["custo_hora"] if atual else 0)
                c.execute("""INSERT INTO membros(id,equipe_id,nome,funcao,custo_hora,usuario_id,ativo,escala_feriado) VALUES (?,?,?,?,?,?,?,?)
                             ON CONFLICT(id) DO UPDATE SET equipe_id=excluded.equipe_id, nome=excluded.nome, funcao=excluded.funcao, custo_hora=excluded.custo_hora,
                             usuario_id=excluded.usuario_id, ativo=excluded.ativo, escala_feriado=excluded.escala_feriado""",
                          (mid, eid, m["nome"].strip(), func, custo, m.get("usuario_id") or None, 0 if m.get("ativo") is False else 1, 1 if m.get("escala_feriado") else 0))
            for r in c.execute("SELECT id FROM membros WHERE equipe_id=?", (eid,)).fetchall():
                if r["id"] not in ids:
                    c.execute("UPDATE membros SET ativo=0 WHERE id=?", (r["id"],))
        B.auditar(c, u, "Equipe salva", eid)
        return filtrar(u, next(e for e in _equipes(c) if e["id"] == eid))


@app.get("/api/materiais")
def listar_materiais(x_usuario: str | None = Header(None)):
    with B.con() as c:
        u = B.usuario(c, x_usuario)
        return filtrar(u, [dict(r) for r in c.execute("SELECT * FROM materiais ORDER BY ativo DESC, descricao")])


@app.put("/api/materiais/{codigo}")
def salvar_material(codigo: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, *GESTAO)
        codigo = codigo.strip()
        if not re.fullmatch(r"[\w.\-/]{1,40}", codigo) or not str(corpo.get("descricao") or "").strip():
            raise HTTPException(400, "Informe código (sem espaços) e descrição.")
        c.execute("INSERT INTO materiais(codigo,descricao,unidade_medida,custo_unitario,ativo) VALUES (?,?,?,?,?) ON CONFLICT(codigo) DO UPDATE SET descricao=excluded.descricao, unidade_medida=excluded.unidade_medida, custo_unitario=excluded.custo_unitario, ativo=excluded.ativo",
                  (codigo, corpo["descricao"].strip(), corpo.get("unidade_medida") or "un", float(corpo.get("custo_unitario") or 0), 0 if corpo.get("ativo") is False else 1))
        return dict(c.execute("SELECT * FROM materiais WHERE codigo=?", (codigo,)).fetchone())


@app.get("/api/projetos")
def projetos_do_ecossistema():
    """Projetos da carteira (PMP) para vincular OS — lidos do Data Core; vazio fora do hub ou sem PMP."""
    try:
        regs = hubkit.consultar("pmp", "tap_aprovado", limit=2000) + hubkit.consultar("pmp", "plano_aprovado", limit=2000)
    except Exception:
        return []
    vistos, out = set(), []
    for r in regs:
        p = r.get("payload") or {}
        cod = (p.get("codigo_erp") or "").strip() or p.get("codigo") or r["entity_key"]
        if cod not in vistos:
            vistos.add(cod); out.append({"codigo": cod, "nome": p.get("nome") or cod, "unidade": p.get("unidade")})
    return sorted(out, key=lambda x: x["nome"])


@app.get("/api/pessoas-hub")
def pessoas_hub(x_usuario: str | None = Header(None)):
    """Pessoas do hub para vincular membros de equipe (id da ferramenta = "h-<id do hub>")."""
    with B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, *GESTAO)
        locais = {r["id"]: r["nome"] for r in c.execute("SELECT id,nome FROM usuarios WHERE perfil='tecnico'")}
    out = [{"id": "h-" + p["id"], "nome": p["nome"], "email": p.get("email"), "escala_feriado": p.get("escala_feriado")} for p in hubkit.pessoas()]
    vistos = {p["id"] for p in out}
    out += [{"id": i, "nome": n, "email": None, "escala_feriado": None} for i, n in locais.items() if i not in vistos]
    return out


# ───────────────────────────────────────────────── integrações
def _receber_solicitacao(c, r) -> str:
    """servicos/solicitacao_externa (Conector CRM) → nova OS (ou atualiza a descrição se ainda estiver aberta)."""
    p = r.get("payload") or {}
    existe = c.execute("SELECT * FROM ordens WHERE chave_externa=?", (r["entity_key"],)).fetchone()
    if existe:
        if existe["status"] == "aberta":
            c.execute("UPDATE ordens SET titulo=?, descricao=?, local=?, contato=?, atualizado_em=? WHERE numero=?",
                      (p.get("titulo") or existe["titulo"], p.get("descricao"), p.get("local") if isinstance(p.get("local"), str) else ((p.get("local") or {}).get("endereco") or existe["local"]),
                       existe["contato"], agora(), existe["numero"]))
            _evento(c, existe["numero"], None, "Solicitação atualizada pelo CRM")
        return existe["numero"]
    pri = p.get("prioridade") if p.get("prioridade") in PRIORIDADES else None
    cli, cont = p.get("cliente") or {}, p.get("contato") or {}
    return criar_os(c, None, {"titulo": p.get("titulo") or "Solicitação de serviço", "descricao": p.get("descricao"), "tipo": p.get("tipo"), "prioridade": pri,
                              "unidade": p.get("unidade"), "local": p.get("local") if isinstance(p.get("local"), str) else (p.get("local") or {}).get("endereco"),
                              "ativo": p.get("ativo"), "cliente": cli.get("nome") if isinstance(cli, dict) else cli,
                              "contato": " · ".join(x for x in ((cont.get("nome"), cont.get("telefone"), cont.get("email")) if isinstance(cont, dict) else (cont,)) if x),
                              "solicitante": (cli.get("nome") if isinstance(cli, dict) else cli) or p.get("crm_nome") or "CRM",
                              "ref_externa": p.get("crm_ref") or p.get("ref_externa"), "chave_externa": r["entity_key"], "codigo_projeto": p.get("codigo_projeto")}, "crm")


def _sincronizar(c, u) -> dict:
    n = []
    for r in B.pendentes(c, "servicos", "solicitacao_externa"):
        numero = _receber_solicitacao(c, r); B.marcar_recebido(c, r, numero); n.append(numero)
        _publicar_os(c, numero, u)
    # o tempo muda a situação do SLA: republica as OS cuja situação mudou desde a última publicação
    for o in c.execute("SELECT * FROM ordens WHERE status NOT IN ('encerrada','cancelada')").fetchall():
        sit = sla(o)["situacao"]
        rec = c.execute("SELECT resultado FROM integracao_recebidos WHERE chave=?", ("sla/" + o["numero"],)).fetchone()
        if not rec or rec["resultado"] != sit:
            _publicar_os(c, o["numero"], u)
            c.execute("INSERT OR REPLACE INTO integracao_recebidos(chave,versao,em,resultado) VALUES (?,?,?,?)", ("sla/" + o["numero"], 1, agora(), sit))
    return {"solicitacoes_do_crm": len(n)}


def _pendencias(c) -> list[dict]:
    """Agenda do ecossistema:
    - operacional: execução da OS na agenda de cada participante (bloco travado, definido pelo planejamento);
    - tarefa: planejar OS aberta;
    - ação: aprovar encerramento, de acordo para feriado, alerta de SLA."""
    out = []
    for o in c.execute("SELECT * FROM ordens WHERE status NOT IN ('encerrada','cancelada')").fetchall():
        r = _resumo(c, o); sit = r["sla"]["situacao"]; uni = [o["unidade"]] if o["unidade"] else []
        if o["status"] in ("planejada", "pausada", "em_execucao") and o["execucao_inicio"]:
            for m in participantes(c, o["numero"]):
                uid = m.get("usuario_id") or ""
                if not uid.startswith("h-"):
                    continue
                out.append({"chave": f"exec:{o['numero']}:{uid}", "natureza": "operacional", "tipo": "execucao", "titulo": f"{o['numero']} · {o['titulo']}",
                            "descricao": " · ".join(x for x in (r["equipe"], o["local"] or o["unidade"], "executor" if m["executor"] else m.get("funcao")) if x),
                            "inicio": o["execucao_inicio"], "fim": _iso(_fim_previsto(o)), "equipe": r["equipe"], "unidades": uni,
                            "destinatarios": [{"id_hub": uid[2:], "email": "", "nome": m["nome"]}], "executor": bool(m["executor"]),
                            "link": f"campo.html#/os/{o['numero']}", "projeto": o["codigo_projeto"],
                            "prioridade": "alta" if sit in ("atrasada", "em_risco") or o["prioridade"] == "urgente" else "normal"})
        if o["status"] == "aberta":
            out.append({"chave": f"planejar:{o['numero']}", "natureza": "tarefa", "esforco_h": 0.5, "tipo": "planejamento",
                        "titulo": f"Planejar {o['numero']} · {o['titulo']}", "prazo": o["prazo_sla"], "unidades": uni, "perfis": ["planejador", "supervisor"],
                        "link": f"#/os/{o['numero']}", "prioridade": "alta" if o["prioridade"] in ("alta", "urgente") else "normal"})
        if sit in ("atrasada", "em_risco"):
            out.append({"chave": f"sla:{o['numero']}", "natureza": "acao", "tipo": "alerta",
                        "titulo": f"{'SLA estourado' if sit == 'atrasada' else 'SLA em risco'}: {o['numero']} · {o['titulo']}",
                        "prazo": o["prazo_sla"], "unidades": uni, "perfis": ["gestor", "supervisor", "planejador"], "link": f"#/os/{o['numero']}", "prioridade": "alta"})
        if o["status"] == "concluida" and o["aprovacao"] == "pendente":
            out.append({"chave": f"aprovar:{o['numero']}", "natureza": "acao", "tipo": "aprovacao",
                        "titulo": f"Aprovar encerramento: {o['numero']} · {o['titulo']}", "descricao": (o["resumo_execucao"] or "")[:200],
                        "unidades": uni, "link": f"#/os/{o['numero']}", "prioridade": "normal",
                        # segregação: quem concluiu não recebe a própria aprovação (o MASTER TESTE recebe mesmo assim)
                        "destinatarios": B.com_masters(B.destinatarios(c, "supervisor", "gestor", unidades=uni or None),
                                                       excluir={str(o["concluida_por_id"] or "")[2:]} if str(o["concluida_por_id"] or "").startswith("h-") else set()),
                        "acoes": [B.acao("encerrar", "Aprovar e encerrar", f"/api/ordens/{o['numero']}/acoes/encerrar", estilo="ok"),
                                  B.acao("devolver", "Devolver ao executor", f"/api/ordens/{o['numero']}/acoes/devolver", comentario="obrigatorio", estilo="bad", campo="motivo")]})
        f = load(o["feriado"], None)
        if f and f.get("situacao") == "aguardando_gestor":
            out.append({"chave": f"feriado:{o['numero']}", "natureza": "acao", "tipo": "aprovacao",
                        "titulo": f"OS em feriado ({f['nome']}): {o['numero']} · {o['titulo']}",
                        "descricao": f"Sem escala: {', '.join(f['sem_escala'])} · ciente: {f.get('ciente_por')}", "unidades": uni, "perfis": ["gestor", "supervisor"],
                        "link": f"#/os/{o['numero']}", "prioridade": "alta",
                        "acoes": [B.acao("de_acordo", "De acordo", f"/api/ordens/{o['numero']}/feriado", corpo={"decisao": "de_acordo"}, estilo="ok"),
                                  B.acao("nao", "Não de acordo", f"/api/ordens/{o['numero']}/feriado", corpo={"decisao": "nao"}, comentario="opcional", estilo="bad")]})
    return out


B.automatico(_sincronizar, _pendencias, intervalo_s=120)


@app.get("/api/integracoes")
def integracoes(x_usuario: str | None = Header(None)):
    with B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, *GESTAO)
        pend = B.pendentes(c, "servicos", "solicitacao_externa")
        return {"modo": "hub" if hubkit.no_hub() else "isolado",
                "pendentes": [{"chave": r["entity_key"], "titulo": (r.get("payload") or {}).get("titulo"), "crm": (r.get("payload") or {}).get("crm_nome"),
                               "em": r.get("updated_at")} for r in pend],
                "recebidas": [dict(o) for o in c.execute("SELECT numero,titulo,status,ref_externa,chave_externa,aberta_em FROM ordens WHERE origem='crm' ORDER BY aberta_em DESC LIMIT 200")]}


@app.post("/api/integracoes/sincronizar")
def sincronizar(x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, *GESTAO)
        return _sincronizar(c, u)


@app.post("/api/demo")
def demo(x_usuario: str | None = Header(None)):
    """Equipes, catálogo e OS de exemplo (genéricos) para conhecer a ferramenta."""
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "gestor")
        if c.execute("SELECT 1 FROM ordens").fetchone():
            raise HTTPException(409, "Já existem OS cadastradas.")
        unis = [x["nome"] for x in B.org().get("unidades") or []] or ["Unidade A"]
        for i, (nome, esp) in enumerate([("Equipe Elétrica", "Elétrica"), ("Equipe Civil", "Civil"), ("Equipe Mecânica", "Mecânica")]):
            eid = f"EQ-DEMO{i + 1}"
            c.execute("INSERT INTO equipes(id,nome,unidade,especialidade) VALUES (?,?,?,?)", (eid, nome, unis[i % len(unis)], esp))
            for j, (fn, ch) in enumerate([("Encarregado", 95.0), ("Técnico", 70.0), ("Auxiliar", 45.0)]):
                c.execute("INSERT INTO membros(id,equipe_id,nome,funcao,custo_hora,usuario_id) VALUES (?,?,?,?,?,?)",
                          (f"MB-D{i}{j}", eid, f"{fn} {esp}", fn, ch, "u-demo-tec1" if (i, j) == (0, 1) else ("u-demo-tec2" if (i, j) == (1, 1) else None)))
        for cod, desc, um, cu in [("CAB-25", "Cabo flexível 2,5 mm²", "m", 4.2), ("DISJ-32", "Disjuntor 32 A", "un", 58.0), ("TOM-10", "Tomada 10 A", "un", 14.5),
                                  ("CIM-50", "Cimento 50 kg", "sc", 38.0), ("TIN-18", "Tinta acrílica 18 L", "gl", 320.0), ("ROL-6205", "Rolamento 6205", "un", 42.0),
                                  ("GRX-1", "Graxa 1 kg", "kg", 36.0), ("LAMP-LED", "Lâmpada LED 20 W", "un", 22.0)]:
            c.execute("INSERT OR IGNORE INTO materiais(codigo,descricao,unidade_medida,custo_unitario) VALUES (?,?,?,?)", (cod, desc, um, cu))
        base = _agora_dt().replace(minute=0, second=0, microsecond=0)
        exemplos = [("Troca de disjuntor do quadro geral", "corretiva", "urgente", 0, 2, "em_execucao", [("DISJ-32", 2)]),
                    ("Instalação de tomadas na sala de reuniões", "instalacao", "media", 0, 26, "planejada", [("TOM-10", 6), ("CAB-25", 40)]),
                    ("Reparo de trinca em parede externa", "corretiva", "alta", 1, 30, "planejada", [("CIM-50", 3)]),
                    ("Pintura da recepção", "preventiva", "baixa", 1, 74, "aberta", [("TIN-18", 2)]),
                    ("Lubrificação de bombas", "preventiva", "media", 2, 50, "planejada", [("GRX-1", 2)]),
                    ("Ruído em motor do exaustor", "corretiva", "alta", 2, None, "aberta", [("ROL-6205", 2)]),
                    ("Inspeção de iluminação de emergência", "inspecao", "media", 0, 28, "planejada", [("LAMP-LED", 10)]),
                    ("Atendimento: falta de energia em bloco B", "atendimento", "urgente", 0, None, "aberta", [])]
        for k, (tit, tipo, pri, eq, h, stt, mats) in enumerate(exemplos):
            numero = criar_os(c, u, {"titulo": tit, "tipo": tipo, "prioridade": pri, "unidade": unis[k % len(unis)], "local": f"Prédio {chr(65 + k % 3)}",
                                     "solicitante": "Solicitante Demo"})
            if k in (0, 5):  # simula abertura antiga para mostrar SLA em risco/estourado
                c.execute("UPDATE ordens SET aberta_em=?, prazo_sla=? WHERE numero=?", (_iso(base - timedelta(hours=6)), _iso(base - timedelta(hours=2) if k == 0 else base + timedelta(hours=3)), numero))
            for cod, q in mats:
                m = c.execute("SELECT * FROM materiais WHERE codigo=?", (cod,)).fetchone()
                c.execute("INSERT INTO os_materiais(id,numero,codigo,descricao,unidade_medida,qtd_prevista,custo_unitario,status) VALUES (?,?,?,?,?,?,?,?)",
                          ("M-" + uuid.uuid4().hex[:8], numero, cod, m["descricao"], m["unidade_medida"], q, m["custo_unitario"], "separado" if stt != "aberta" else "a_separar"))
            if h is not None:
                c.execute("UPDATE ordens SET equipe_id=?, execucao_inicio=?, duracao_h=?, status=?, planejada_em=? WHERE numero=?",
                          (f"EQ-DEMO{eq + 1}", _iso(base + timedelta(hours=h - 3 if k == 0 else h)), 4.0, stt, agora(), numero))
                _definir_participantes(c, numero, f"EQ-DEMO{eq + 1}", None, f"MB-D{eq}0" if k % 2 else f"MB-D{eq}1")
            if stt == "em_execucao":
                c.execute("UPDATE ordens SET iniciada_em=?, cronometro_inicio=? WHERE numero=?", (agora(), _iso(base - timedelta(hours=1)), numero))
                c.execute("UPDATE checklist SET feito=1, por=?, em=? WHERE numero=? AND seq<=2", ("Técnico Demo 1", agora(), numero))
        # conflito proposital na agenda da Equipe Civil
        pintura = c.execute("SELECT numero FROM ordens WHERE titulo LIKE 'Pintura%'").fetchone()["numero"]
        c.execute("UPDATE ordens SET execucao_inicio=(SELECT execucao_inicio FROM ordens WHERE titulo LIKE 'Reparo de trinca%'), equipe_id='EQ-DEMO2', duracao_h=6, status='planejada' WHERE numero=?", (pintura,))
        _definir_participantes(c, pintura, "EQ-DEMO2", None, None)
        B.auditar(c, u, "Exemplo carregado", "")
        for (numero,) in c.execute("SELECT numero FROM ordens").fetchall():
            _publicar_os(c, numero, u)
        return {"ok": True}


B.servir_spa(app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=os.environ.get("ORDENS_HOST", "127.0.0.1"), port=int(os.environ.get("PORT") or 8850),
                root_path=os.environ.get("HUB_ROOT_PATH", ""))
