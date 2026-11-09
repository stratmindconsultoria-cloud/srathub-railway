"""Registro de apps: cada pasta em apps/ com um hub.json é uma ferramenta plugada.

Formato do hub.json (todos os campos opcionais, exceto "tipo"):
{
  "nome": "Meu App", "descricao": "...", "icone": "📊", "categoria": "Finanças",
  "tipo": "static" | "python" | "node" | "comando",
  "pasta": "dist",                 # static: pasta servida (padrão: a própria pasta do app)
  "spa": true,                     # static: rotas desconhecidas devolvem index.html
  "comando": "{python} -m uvicorn app:app --port {port} --root-path {base}",
  "health": "/health",             # rota que responde quando o app está pronto
  "requisitos": "requirements.txt",# python: instalado num .venv próprio do app, automaticamente
  "ocioso_min": 30,                # para após X min sem uso (0 = nunca)
  "sempre_ligado": false,          # inicia junto com o hub
  "publico": false,                # true = acessível sem login do hub (ex.: demonstração)
  "manter_prefixo": false,         # true = repassa /apps/<id>/... sem cortar (Streamlit, Next basePath)
  "ativo": true,                   # false = oculto e desligado
  "env": {"CHAVE": "valor"}        # variáveis extras
}
Pastas que começam com "_" ou "." são ignoradas (ex.: _modelo).

Compatibilidade HUB PMO: se não houver hub.json, o hub lê hub.manifest.json
(schema_version, name, version, runtime.start_command, runtime.port, runtime.health.path,
portal.icon, auth_mode, dependencies). Um bloco opcional "strathub": {...} no manifesto
aceita qualquer campo do hub.json acima e tem prioridade.
"""
from __future__ import annotations
import json, re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")
TIPOS = {"static", "python", "node", "comando"}


@dataclass
class AppDef:
    id: str
    dir: Path
    nome: str
    tipo: str
    descricao: str = ""
    icone: str = "🧩"
    categoria: str = "Geral"
    pasta: str = ""
    spa: bool = False
    comando: str = ""
    health: str = "/"
    requisitos: str = ""
    ocioso_min: float | None = None
    sempre_ligado: bool = False
    publico: bool = False
    manter_prefixo: bool = False
    ativo: bool = True
    env: dict = field(default_factory=dict)
    versao: str = ""
    auth_mode: str = "hub"
    dependencias: list = field(default_factory=list)
    manifesto: str = "hub.json"
    publica: list = field(default_factory=list)     # contratos do Data Core que publica ("domain/entity_type")
    consome: list = field(default_factory=list)     # contratos que consome
    oculto: bool = False                            # ajuste do Admin técnico: fora do portal e parado
    ordem: int = 100
    rotas_publicas: list[str] = field(default_factory=list)
    agir_em_nome: bool = False                      # manifesto pede para executar ações em nome das pessoas (conectores)
    agir_liberado: bool = False                     # ajuste do Admin técnico: o administrador autorizou
    botao_hub: bool = True                          # o hub acrescenta o botão de navegação/notificações nas páginas
    erro: str = ""
    mtime: float = 0.0

    @property
    def base(self) -> str:
        return f"/apps/{self.id}/"

    @property
    def is_process(self) -> bool:
        return self.tipo in ("python", "node", "comando")

    def static_root(self) -> Path:
        return (self.dir / self.pasta).resolve() if self.pasta else self.dir.resolve()

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "nome": self.nome, "descricao": self.descricao, "icone": self.icone,
            "categoria": self.categoria, "tipo": self.tipo, "url": self.base, "publico": self.publico,
            "ativo": self.ativo, "erro": self.erro, "processo": self.is_process,
            "ocioso_min": self.ocioso_min, "sempre_ligado": self.sempre_ligado,
            "versao": self.versao, "auth_mode": self.auth_mode, "dependencias": self.dependencias, "manifesto": self.manifesto,
            "publica": self.publica, "consome": self.consome, "oculto": self.oculto, "ordem": self.ordem, "rotas_publicas": self.rotas_publicas,
            "agir_em_nome": self.agir_em_nome, "agir_liberado": self.agir_liberado, "botao_hub": self.botao_hub,
            "notificacoes": "agenda/pendencia" in self.publica,
        }


def traduzir_manifesto(m: dict, d: Path) -> dict:
    """Converte um hub.manifest.json (formato HUB PMO) para o formato hub.json."""
    rt = m.get("runtime") or {}
    portal = m.get("portal") or {}
    modo = str(rt.get("mode", "service")).lower()
    raw: dict[str, Any] = {
        "nome": m.get("name") or d.name, "descricao": m.get("description", ""),
        "icone": str(portal.get("icon") or "🧩"), "categoria": portal.get("category") or m.get("category") or "Ferramentas",
        "versao": str(m.get("version", "")), "auth_mode": str(m.get("auth_mode", "hub")),
        "dependencias": list(m.get("dependencies") or []),
        "publica": list((m.get("contracts") or {}).get("publishes") or []),
        "consome": list((m.get("contracts") or {}).get("consumes") or []),
        "publico": str(m.get("auth_mode", "")).lower() in ("none", "public", "publico"),
    }
    if modo == "static":
        raw.update(tipo="static", pasta=rt.get("static_dir", "public"), spa=bool(rt.get("spa", False)))
    else:
        cmd = rt.get("start_command") or []
        if isinstance(cmd, str):
            cmd = cmd.split()
        porta = str(rt.get("port", "")) if rt.get("port") else None
        partes = []
        for i, t in enumerate(cmd):
            t = str(t)
            if i == 0 and t.lower() in ("python", "python3", "py", "python.exe"):
                partes.append("{python}"); continue
            if porta and t == porta:
                t = "{port}"
            elif t.startswith("/apps/") and i > 0 and str(cmd[i - 1]) == "--root-path":
                t = "{base_noslash}"
            partes.append(f'"{t}"' if " " in t else t)
        tipo = "python" if partes[:1] == ["{python}"] else ("node" if partes[:1] in (["node"], ["npm"]) else "comando")
        raw.update(tipo=tipo, comando=" ".join(partes), health=(rt.get("health") or {}).get("path", "/health"))
        if rt.get("env"):
            raw["env"] = rt["env"]
    raw.update(m.get("strathub") or {})
    return raw


def _load_one(d: Path) -> AppDef | None:
    mf = d / "hub.json"
    manifesto = mf.is_file()
    if not manifesto:
        mf = d / "hub.manifest.json"
        if not mf.is_file():
            return None
    app_id = d.name.lower()
    try:
        raw = json.loads(mf.read_text(encoding="utf-8-sig"))
        if not manifesto:
            raw = traduzir_manifesto(raw, d)
    except Exception as e:  # manifesto inválido aparece no portal com o erro
        return AppDef(id=app_id, dir=d, nome=d.name, tipo="static", erro=f"{mf.name} inválido: {e}", ativo=False, mtime=mf.stat().st_mtime)
    if not ID_RE.match(app_id):
        return AppDef(id=re.sub(r"[^a-z0-9_-]", "-", app_id)[:48] or "app", dir=d, nome=d.name, tipo="static",
                      erro="Nome da pasta deve usar só letras minúsculas, números, - ou _", ativo=False, mtime=mf.stat().st_mtime)
    tipo = str(raw.get("tipo", "static")).lower()
    a = AppDef(
        id=app_id, dir=d, nome=str(raw.get("nome") or d.name), tipo=tipo,
        descricao=str(raw.get("descricao", "")), icone=str(raw.get("icone", "🧩"))[:4],
        categoria=str(raw.get("categoria", "Geral")), pasta=str(raw.get("pasta", "")),
        spa=bool(raw.get("spa", False)), comando=str(raw.get("comando", "")),
        health=str(raw.get("health", "/")), requisitos=str(raw.get("requisitos", "")),
        ocioso_min=raw.get("ocioso_min"), sempre_ligado=bool(raw.get("sempre_ligado", False)),
        publico=bool(raw.get("publico", False)), manter_prefixo=bool(raw.get("manter_prefixo", False)), ativo=bool(raw.get("ativo", True)),
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()}, mtime=mf.stat().st_mtime,
        versao=str(raw.get("versao", "")), auth_mode=str(raw.get("auth_mode", "hub")),
        dependencias=[str(x) for x in (raw.get("dependencias") or [])], manifesto=mf.name,
        publica=[str(x) for x in (raw.get("publica") or [])], consome=[str(x) for x in (raw.get("consome") or [])],
        ordem=int(raw.get("ordem", 100) or 100),
        rotas_publicas=[str(x).strip("/") + "/" for x in (raw.get("rotas_publicas") or []) if str(x).strip("/")],
        agir_em_nome=bool(raw.get("agir_em_nome", False)),
        botao_hub=bool(raw.get("botao_hub", True)),
    )
    if tipo not in TIPOS:
        a.erro, a.ativo = f"tipo '{tipo}' desconhecido (use static, python, node ou comando)", False
    elif a.is_process and not a.comando:
        if tipo == "python" and (d / "app.py").exists():
            a.comando = "{python} -m uvicorn app:app --host 127.0.0.1 --port {port} --root-path {base_noslash}"
        elif tipo == "node" and (d / "package.json").exists():
            a.comando = "npm start"
        else:
            a.erro, a.ativo = "informe \"comando\" no hub.json", False
    if tipo == "python" and not a.requisitos and (d / "requirements.txt").exists():
        a.requisitos = "requirements.txt"
    if tipo == "static":
        root = a.static_root()
        try:
            root.relative_to(d.resolve())
        except ValueError:
            a.erro, a.ativo = "\"pasta\" precisa ficar dentro da pasta do app", False
        if not a.erro and not (root / "index.html").exists():
            a.erro = f"index.html não encontrado em {a.pasta or '.'}"
    return a


def _aplicar_ajustes(a: AppDef, aj: dict) -> None:
    """Ajustes feitos no Admin técnico (data/apps_overrides.json) prevalecem sobre o manifesto."""
    for k in ("nome", "icone", "categoria", "descricao", "ordem", "sempre_ligado", "ocioso_min", "oculto", "agir_liberado"):
        if k in aj:
            setattr(a, k, aj[k])


def scan() -> dict[str, AppDef]:
    apps: dict[str, AppDef] = {}
    if not config.APPS_DIR.exists():
        return apps
    try:
        ajustes = json.loads((config.DATA_DIR / "apps_overrides.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ajustes = {}
    for d in sorted(config.APPS_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        a = _load_one(d)
        if a and a.id not in apps:
            _aplicar_ajustes(a, ajustes.get(a.id) or {})
            apps[a.id] = a
    return apps
