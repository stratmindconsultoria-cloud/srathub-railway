"""Administração técnica: ajustes das ferramentas (sem mexer na pasta delas), lixeira, backup,
infraestrutura, dependências, mapa e exportação de ecossistemas."""
from __future__ import annotations
import io, json, os, platform, re, shutil, subprocess, sys, threading, time, zipfile
from pathlib import Path

from . import __version__, config, datacore, organizacao

OVERRIDES = config.DATA_DIR / "apps_overrides.json"
LIXEIRA = config.APPS_DIR / "_removidos"
BACKUPS = config.DATA_DIR / "backups"
CAMPOS_AJUSTE = {"oculto": bool, "nome": str, "icone": str, "categoria": str, "ordem": int, "sempre_ligado": bool, "ocioso_min": float, "descricao": str, "agir_liberado": bool}
_lock = threading.Lock()


# ── ajustes por ferramenta (gravados no hub) ───────────────
def ler_ajustes() -> dict:
    try:
        return json.loads(OVERRIDES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def gravar_ajuste(app_id: str, dados: dict) -> dict:
    with _lock:
        tudo = ler_ajustes()
        atual = tudo.get(app_id, {})
        for k, v in dados.items():
            if k not in CAMPOS_AJUSTE:
                continue
            if v is None or v == "":
                atual.pop(k, None)
                continue
            atual[k] = CAMPOS_AJUSTE[k](v)
            if k in ("nome", "categoria", "descricao"):
                atual[k] = atual[k].strip()[:200]
            if k == "icone":
                atual[k] = atual[k].strip()[:4]
        if atual:
            tudo[app_id] = atual
        else:
            tudo.pop(app_id, None)
        OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
        tmp = OVERRIDES.with_suffix(".tmp")
        tmp.write_text(json.dumps(tudo, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, OVERRIDES)
        return atual


# ── lixeira ────────────────────────────────────────────────
def _mover(origem: Path, destino: Path, tentativas: int = 12) -> None:
    """Move com novas tentativas: no Windows, antivírus/indexador seguram arquivos por alguns segundos."""
    for i in range(tentativas):
        try:
            shutil.move(str(origem), str(destino))
            return
        except PermissionError:
            if i == tentativas - 1:
                raise
            time.sleep(0.5)


def substituir_arquivos(destino: Path, novos: Path, preservar: set[str]) -> str | None:
    """Troca o conteúdo de uma ferramenta pelo de `novos` sem nunca deixá-la pela metade.

    Os arquivos antigos (exceto `preservar`: ambiente e dados) vão para a lixeira; se algo falhar no meio,
    tudo volta como estava e o erro é informado. Retorna a pasta da lixeira com a versão anterior."""
    destino.mkdir(parents=True, exist_ok=True)
    LIXEIRA.mkdir(parents=True, exist_ok=True)
    base = LIXEIRA / f"{destino.name}__versao-anterior-{time.strftime('%Y%m%d-%H%M%S')}"
    guarda, k = base, 1
    while guarda.exists():
        k += 1; guarda = base.with_name(f"{base.name}-{k}")
    guarda.mkdir()
    movidos_antigos, movidos_novos = [], []
    try:
        for item in list(destino.iterdir()):
            if item.name in preservar:
                continue
            _mover(item, guarda / item.name); movidos_antigos.append(item.name)
        for item in list(novos.iterdir()):
            if item.name in preservar and (destino / item.name).exists():
                continue  # nunca sobrescreve ambiente/dados existentes
            _mover(item, destino / item.name); movidos_novos.append(item.name)
    except Exception as e:
        for nome in movidos_novos:  # desfaz
            p = destino / nome
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        for nome in movidos_antigos:
            try:
                _mover(guarda / nome, destino / nome)
            except Exception:
                pass
        shutil.rmtree(guarda, ignore_errors=True) if not any(guarda.iterdir()) else None
        raise RuntimeError(f"Não foi possível atualizar (arquivo em uso?): {e}. A versão anterior foi mantida.") from e
    return guarda.name if movidos_antigos else (guarda.rmdir() or None)


def remover_para_lixeira(app_id: str) -> str:
    origem = config.APPS_DIR / app_id
    if not origem.is_dir():
        raise FileNotFoundError("Pasta da ferramenta não encontrada")
    LIXEIRA.mkdir(parents=True, exist_ok=True)
    destino = LIXEIRA / f"{app_id}__{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.move(str(origem), str(destino))
    return destino.name


def listar_lixeira() -> list[dict]:
    if not LIXEIRA.exists():
        return []
    out = []
    for d in sorted(LIXEIRA.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        app_id, _, quando = d.name.partition("__")
        nome = app_id
        for mf in ("hub.json", "hub.manifest.json"):
            try:
                raw = json.loads((d / mf).read_text(encoding="utf-8-sig"))
                nome = raw.get("nome") or raw.get("name") or nome
                break
            except (OSError, ValueError):
                continue
        out.append({"pasta": d.name, "id": app_id, "nome": nome, "removido_em": quando, "tem_dados": (d / "dados").exists() or (d / "data").exists(),
                    "tamanho_mb": round(_tamanho(d, ignorar={".venv", "node_modules"}) / 1048576, 1)})
    return out


def _pasta_lixeira(pasta: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,48}__\d{8}-\d{6}", pasta or ""):
        raise ValueError("Item inválido")
    d = LIXEIRA / pasta
    if not d.is_dir():
        raise FileNotFoundError("Item não está na lixeira")
    return d


def restaurar(pasta: str) -> str:
    d = _pasta_lixeira(pasta)
    app_id = pasta.split("__")[0]
    destino = config.APPS_DIR / app_id
    if destino.exists():
        raise FileExistsError(f"Já existe uma ferramenta '{app_id}' plugada")
    shutil.move(str(d), str(destino))
    return app_id


def excluir_definitivo(pasta: str) -> None:
    shutil.rmtree(_pasta_lixeira(pasta))
    app_id = pasta.split("__")[0]
    if not (config.APPS_DIR / app_id).exists():  # sem ferramenta com o mesmo id: descarta os ajustes dela
        with _lock:
            tudo = ler_ajustes()
            if tudo.pop(app_id, None) is not None:
                OVERRIDES.write_text(json.dumps(tudo, indent=2, ensure_ascii=False), encoding="utf-8")


# ── backup ─────────────────────────────────────────────────
def _tamanho(p: Path, ignorar: set[str] = frozenset()) -> int:
    total = 0
    for raiz, dirs, arqs in os.walk(p):
        dirs[:] = [x for x in dirs if x not in ignorar]
        for a in arqs:
            try:
                total += (Path(raiz) / a).stat().st_size
            except OSError:
                pass
    return total


def criar_backup(motivo: str = "manual") -> dict:
    """Copia data\\ (sem logs e backups) e os dados/configurações de cada ferramenta (dados\\, data\\, config\\)."""
    BACKUPS.mkdir(parents=True, exist_ok=True)
    nome = f"backup-{time.strftime('%Y%m%d-%H%M%S')}-{motivo}.zip"
    caminho = BACKUPS / nome
    with zipfile.ZipFile(caminho, "w", zipfile.ZIP_DEFLATED) as z:
        for raiz, dirs, arqs in os.walk(config.DATA_DIR):
            dirs[:] = [x for x in dirs if x not in ("backups", "logs")]
            for a in arqs:
                f = Path(raiz) / a
                if f.suffix in (".tmp",):
                    continue
                z.write(f, "hub_data/" + str(f.relative_to(config.DATA_DIR)).replace("\\", "/"))
        if config.APPS_DIR.exists():
            for app in config.APPS_DIR.iterdir():
                if not app.is_dir() or app.name.startswith(("_", ".")):
                    continue
                for sub in ("dados", "data", "config"):
                    base = app / sub
                    if not base.is_dir():
                        continue
                    for raiz, dirs, arqs in os.walk(base):
                        dirs[:] = [x for x in dirs if x not in ("__pycache__",)]
                        for a in arqs:
                            f = Path(raiz) / a
                            z.write(f, f"apps/{app.name}/" + str(f.relative_to(app)).replace("\\", "/"))
    return {"nome": nome, "tamanho_mb": round(caminho.stat().st_size / 1048576, 2), "motivo": motivo}


def listar_backups() -> list[dict]:
    if not BACKUPS.exists():
        return []
    return [{"nome": f.name, "tamanho_mb": round(f.stat().st_size / 1048576, 2), "em": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))}
            for f in sorted(BACKUPS.glob("backup-*.zip"), reverse=True)]


def caminho_backup(nome: str) -> Path:
    if not re.fullmatch(r"backup-\d{8}-\d{6}-[a-z]+\.zip", nome or ""):
        raise ValueError("Nome inválido")
    f = BACKUPS / nome
    if not f.is_file():
        raise FileNotFoundError("Backup não encontrado")
    return f


def manter_backups(qtd: int) -> None:
    for f in sorted(BACKUPS.glob("backup-*-diario.zip"), reverse=True)[max(1, qtd):]:
        f.unlink(missing_ok=True)


def ultimo_backup_diario() -> float:
    fs = sorted(BACKUPS.glob("backup-*-diario.zip")) if BACKUPS.exists() else []
    return fs[-1].stat().st_mtime if fs else 0.0


# ── infraestrutura ─────────────────────────────────────────
def _cmd(args: list[str], timeout: float = 4.0) -> str | None:
    try:
        kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kw)
        return (r.stdout or r.stderr).strip()
    except Exception:
        return None


def infraestrutura(cfg: dict, inicio: float) -> dict:
    disco = shutil.disk_usage(config.DATA_DIR)
    ts = shutil.which("tailscale")
    ts_status = _cmd([ts, "serve", "status"]) if ts else None
    ts_ip = _cmd([ts, "ip", "-4"]) if ts else None
    return {"hub": __version__, "python": sys.version.split()[0], "sistema": f"{platform.system()} {platform.release()}", "maquina": platform.node(),
            "endereco": f"{cfg.get('host')}:{cfg.get('porta')}", "ligado_desde": time.strftime("%Y-%m-%d %H:%M", time.localtime(inicio)),
            "pasta_hub": str(config.ROOT), "pasta_apps": str(config.APPS_DIR), "pasta_dados": str(config.DATA_DIR),
            "disco_livre_gb": round(disco.free / 1e9, 1), "disco_total_gb": round(disco.total / 1e9, 1),
            "dados_mb": round(_tamanho(config.DATA_DIR, {"backups"}) / 1048576, 1), "backups_mb": round(_tamanho(BACKUPS) / 1048576, 1) if BACKUPS.exists() else 0,
            "tailscale": {"instalado": bool(ts), "ip": (ts_ip or "").splitlines()[0] if ts_ip else None,
                          "publicado": bool(ts_status and str(cfg.get("porta")) in ts_status), "status": ts_status}}


# ── dependências ───────────────────────────────────────────
def pacotes_python(py: Path) -> list[dict]:
    out = _cmd([str(py), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"], timeout=60)
    try:
        return json.loads(out or "[]")
    except ValueError:
        return []


def checar_dependencias(apps: dict) -> list[dict]:
    """Dependências declaradas no manifesto (serviços do hub e outras ferramentas) e contratos consumidos sem produtor ativo."""
    servicos = {"hub_core", "hub_database", "hub_data_core"}
    ativos = {a.id for a in apps.values() if a.ativo and not a.oculto}
    produtores = {}
    for a in apps.values():
        if a.ativo and not a.oculto:
            for c in a.publica:
                produtores.setdefault(c, []).append(a.id)
    out = []
    for a in sorted(apps.values(), key=lambda x: x.nome):
        itens = []
        for d in a.dependencias:
            if d in servicos:
                itens.append({"item": d, "tipo": "serviço do hub", "ok": True})
            elif d.startswith("python"):
                itens.append({"item": d, "tipo": "Python", "ok": True, "obs": sys.version.split()[0]})
            elif d.startswith("app:"):
                itens.append({"item": d[4:], "tipo": "ferramenta", "ok": d[4:] in ativos})
            else:
                itens.append({"item": d, "tipo": "outro", "ok": True})
        for c in a.consome:
            prod = produtores.get(c, [])
            itens.append({"item": c, "tipo": "contrato consumido", "ok": bool(prod), "obs": ", ".join(prod) or "nenhuma ferramenta ativa publica"})
        out.append({"id": a.id, "nome": a.nome, "requisitos": a.requisitos, "itens": itens})
    return out


# ── mapa e exportação do ecossistema ───────────────────────
def mapa(apps: dict) -> dict:
    resumo = {f'{t["domain"]}/{t["entity_type"]}': t for t in datacore.resumo()["tipos"]}
    contratos: dict[str, dict] = {}
    for a in apps.values():
        if not a.ativo or a.oculto:
            continue
        for c in a.publica:
            contratos.setdefault(c, {"contrato": c, "publica": [], "consome": []})["publica"].append(a.id)
        for c in a.consome:
            contratos.setdefault(c, {"contrato": c, "publica": [], "consome": []})["consome"].append(a.id)
    # ferramentas presentes mas com problema (desativadas ou com manifesto inválido) que declaram o contrato
    com_problema: dict[str, list] = {}
    for a in apps.values():
        if (not a.ativo or a.erro) and not a.oculto:
            for c in list(a.publica) + list(a.consome):
                com_problema.setdefault(c, []).append(a.id)
    for c, d in contratos.items():
        r = resumo.get(c)
        d["registros"] = r["registros"] if r else 0
        d["ultimo"] = r["ultimo"] if r else None
        d["com_problema"] = com_problema.get(c, [])
        # integração sem dependência: lado ausente é "opcional"; alerta só quando a ponta existe e está com problema
        d["situacao"] = "atencao" if d["com_problema"] else ("ok" if d["publica"] and d["consome"] else "opcional")
    nos = [{"id": a.id, "nome": a.nome, "icone": a.icone, "categoria": a.categoria} for a in sorted(apps.values(), key=lambda x: (x.ordem, x.nome))
           if a.ativo and not a.oculto and (a.publica or a.consome)]
    return {"ferramentas": nos, "contratos": sorted(contratos.values(), key=lambda x: x["contrato"])}


def _config_app(app) -> dict:
    out = {}
    pasta = app.dir / "config"
    if pasta.is_dir():
        for f in sorted(pasta.glob("*.json")):
            if f.name.endswith(".exemplo.json") or f.name == "organizacao.json":
                continue
            try:
                out[f.name] = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                pass
    return out


def exportar_ecossistema(apps: dict, nome: str) -> dict:
    return {"formato": "strathub-ecossistema/1", "nome": nome or "Ecossistema", "exportado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "hub": __version__, "organizacao": organizacao.ler(),
            "ferramentas": [{"id": a.id, "nome": a.nome, "versao": a.versao, "publica": a.publica, "consome": a.consome, "config": _config_app(a)}
                            for a in sorted(apps.values(), key=lambda x: (x.ordem, x.nome)) if a.ativo],
            "ajustes_portal": ler_ajustes()}


def importar_ecossistema(apps: dict, eco: dict, aplicar_org: bool = True) -> dict:
    if not isinstance(eco, dict) or eco.get("formato") != "strathub-ecossistema/1":
        raise ValueError("Arquivo não é um ecossistema do STRAT·HUB")
    res = {"organizacao": False, "configuradas": [], "ausentes": [], "ajustes": 0}
    if aplicar_org and isinstance(eco.get("organizacao"), dict):
        organizacao.gravar(eco["organizacao"]); res["organizacao"] = True
    for f in eco.get("ferramentas") or []:
        a = apps.get(str(f.get("id")))
        if not a:
            res["ausentes"].append({"id": f.get("id"), "nome": f.get("nome"), "versao": f.get("versao")}); continue
        pasta = a.dir / "config"; pasta.mkdir(exist_ok=True)
        for nome_arq, conteudo in (f.get("config") or {}).items():
            if re.fullmatch(r"[\w.-]+\.json", nome_arq) and not nome_arq.endswith(".exemplo.json") and nome_arq != "organizacao.json":
                (pasta / nome_arq).write_text(json.dumps(conteudo, indent=2, ensure_ascii=False), encoding="utf-8")
        res["configuradas"].append(a.id)
    for app_id, aj in (eco.get("ajustes_portal") or {}).items():
        if app_id in apps and isinstance(aj, dict):
            gravar_ajuste(app_id, aj); res["ajustes"] += 1
    return res
