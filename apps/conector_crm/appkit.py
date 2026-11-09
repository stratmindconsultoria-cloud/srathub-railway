"""appkit — base comum das ferramentas (FastAPI + SQLite): banco, identidade, perfis, organização e auditoria.

Cada ferramenta continua independente (copia este arquivo e o hubkit.py para a própria pasta).
"""
from __future__ import annotations
import contextvars, copy, hashlib, json, os, sqlite3, threading, time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse

import hubkit

ORG_PADRAO = {
    "nome": "Organização Exemplo", "sigla": "ORG", "moeda": "BRL",
    "cor_primaria": "#1e3a6e", "cor_destaque": "#0f6e56",
    "unidades": [{"codigo": "UN-NORTE", "nome": "Unidade Norte", "grupo": "Regional A"},
                 {"codigo": "UN-SUL", "nome": "Unidade Sul", "grupo": "Regional A"},
                 {"codigo": "UN-LESTE", "nome": "Unidade Leste", "grupo": "Regional B"},
                 {"codigo": "CORP", "nome": "Corporativo", "grupo": "Corporativo"}],
    "areas": ["Engenharia", "Operações", "Tecnologia", "Manutenção", "Corporativo"],
    "vocabulario": {"plano_sigla": "PPI", "plano_nome": "Plano Plurianual de Investimentos", "ciclo": "Ciclo de Planejamento",
                    "codigo_erp": "Código ERP", "termo_abertura": "TAP", "termo_abertura_nome": "Termo de Abertura do Projeto",
                    "aprovador": "Aprovador Executivo", "forecast": "Tendência", "rodada": "Rodada", "unidade": "Unidade",
                    "area": "Área", "capex": "CAPEX", "pmo": "PMO"},
    "ano_base": datetime.now().year, "horizonte_anos": 5, "taxa_desconto_aa": 0.10,
}


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def dump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def load(v: str | None, padrao: Any = None) -> Any:
    if v in (None, ""):
        return padrao
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return padrao


class Base:
    """Infraestrutura de uma ferramenta.

    perfis: {"codigo": "Rótulo"}; admin: perfil que gerencia usuários; padrao: perfil de quem chega do hub;
    demo: [(id, nome, perfil, unidade)] usados só no modo isolado (demonstração). No hub não existem: perfis se testam
    com usuários comuns do hub e, em ambiente de teste, com o MASTER TESTE (age com qualquer papel e recebe tudo)."""

    def __init__(self, app_id: str, nome: str, versao: str, base_dir: Path, schema: str,
                 perfis: dict[str, str], admin: str, padrao: str, demo: list[tuple], config_padrao: dict | None = None,
                 env_dados: str | None = None):
        self.app_id, self.nome, self.versao, self.base_dir = app_id, nome, versao, Path(base_dir)
        self.dados = Path(os.environ.get(env_dados or f"{app_id.upper()}_DADOS", self.base_dir / "dados"))
        self.arquivos = self.dados / "arquivos"
        self.db_path = self.dados / f"{app_id}.db"
        self.schema, self.perfis, self.admin, self.padrao, self.demo = schema, perfis, admin, padrao, demo
        self.config_padrao = config_padrao or {}
        self.lock = threading.RLock()
        self._sync_func: Callable | None = None
        self._pend_func: Callable | None = None
        self._sync_intervalo = 120
        self.sync = {"ultimo": None, "ok": None, "resultado": None, "erro": None, "automatico": False, "intervalo_s": 120}
        self._ident = contextvars.ContextVar(f"ident_{app_id}", default=None)
        hubkit.configurar(dados=self.dados, config=self.base_dir / "config")

    # ── banco ─────────────────────────────────────────────
    @contextmanager
    def con(self):
        c = sqlite3.connect(self.db_path, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def iniciar(self) -> None:
        self.dados.mkdir(parents=True, exist_ok=True)
        self.arquivos.mkdir(parents=True, exist_ok=True)
        with self.con() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
CREATE TABLE IF NOT EXISTS usuarios(id TEXT PRIMARY KEY, nome TEXT NOT NULL, email TEXT, perfil TEXT NOT NULL,
  unidade TEXT, origem TEXT NOT NULL DEFAULT 'demo');
CREATE TABLE IF NOT EXISTS auditoria(seq INTEGER PRIMARY KEY AUTOINCREMENT, em TEXT NOT NULL, por TEXT, perfil TEXT,
  acao TEXT NOT NULL, alvo TEXT, detalhes TEXT);
CREATE TABLE IF NOT EXISTS integracao_recebidos(chave TEXT PRIMARY KEY, versao INTEGER NOT NULL, em TEXT NOT NULL, resultado TEXT);
CREATE TABLE IF NOT EXISTS pendencias_publicadas(chave TEXT PRIMARY KEY, hash TEXT NOT NULL, em TEXT NOT NULL);
""" + self.schema)
            unis = [u["nome"] for u in self.org().get("unidades") or []] or ["Unidade A"]
            for uid, nome, perfil, uni in ([] if hubkit.no_hub() else self.demo):
                if isinstance(uni, int):
                    uni = unis[uni % len(unis)]
                c.execute("INSERT OR IGNORE INTO usuarios(id,nome,perfil,unidade,origem) VALUES (?,?,?,?,'demo')", (uid, nome, perfil, uni))
        if (self._sync_func or self._pend_func) and not getattr(self, "_laco_ativo", False):
            self.sync["automatico"] = True
            self._laco_ativo = True
            threading.Thread(target=self._laco_sync, name=f"sync-{self.app_id}", daemon=True).start()

    def rotulos_perfis(self) -> dict[str, str]:
        """Perfis são fixos no código (as regras dependem deles); os rótulos são configuráveis em config["perfis"]."""
        extra = self.config().get("perfis") or {}
        return {k: str(extra.get(k) or v) for k, v in self.perfis.items()}

    # ── fluxo automático entre ferramentas ────────────────
    def automatico(self, sincronizar: Callable[[sqlite3.Connection, dict], dict] | None = None,
                   pendencias: Callable[[sqlite3.Connection], list[dict]] | None = None, intervalo_s: int = 120) -> None:
        """Registra a sincronização automática: recebe do Data Core (sincronizar) e publica pendências para a agenda.
        No hub roda sozinha a cada `intervalo_s` e ao abrir a ferramenta; fora do hub só sob demanda."""
        intervalo_s = int(os.environ.get("SYNC_INTERVALO_S") or intervalo_s)  # ajuste por ambiente (testes, servidores grandes)
        self._sync_func, self._pend_func, self._sync_intervalo = sincronizar, pendencias, intervalo_s
        self.sync["intervalo_s"] = intervalo_s

    def usuario_sistema(self) -> dict:
        return {"id": "sistema", "nome": "Integração automática", "email": "", "perfil": self.admin, "unidade": None, "origem": "sistema"}

    def executar_sync(self) -> dict:
        res, erro = {}, None
        try:
            with self.lock, self.con() as c:
                if self._sync_func:
                    res = self._sync_func(c, self.usuario_sistema()) or {}
                if self._pend_func:
                    res["pendencias"] = self._publicar_pendencias(c, self._pend_func(c) or [])
        except Exception as e:  # nunca derruba a ferramenta; fica visível no indicador
            erro = getattr(e, "detail", None) or str(e)
        self.sync.update(ultimo=time.time(), ok=erro is None, resultado=res if erro is None else self.sync.get("resultado"), erro=erro)
        return self.sync

    def _publicar_pendencias(self, c: sqlite3.Connection, lista: list[dict]) -> dict:
        """Publica agenda/pendencia só quando muda; o que deixou de existir é publicado como resolvido."""
        atuais, pub, res = {}, 0, 0
        for p in lista:
            chave = f"{self.app_id}:{p['chave']}"
            corpo = {**{k: v for k, v in p.items() if k != "chave"}, "origem_app": self.app_id, "origem_nome": self.nome, "status": "aberta"}
            corpo.setdefault("natureza", "acao")  # acao (decidir/clicar) · tarefa (consome tempo) · operacional (execução de OS)
            if "destinatarios" not in corpo and corpo.get("perfis"):
                corpo["destinatarios"] = self.destinatarios(c, *corpo["perfis"], unidades=corpo.get("unidades") or None)
            if corpo["natureza"] in ("acao", "tarefa"):  # MASTER TESTE recebe toda decisão e tarefa (a execução de OS fica com quem executa)
                corpo["destinatarios"] = self.com_masters(list(corpo.get("destinatarios") or []))
            h = hashlib.sha1(dump(corpo).encode()).hexdigest()
            atuais[chave] = (corpo, h)
        antigos = {r["chave"]: r["hash"] for r in c.execute("SELECT * FROM pendencias_publicadas WHERE chave LIKE ?", (self.app_id + ":%",))}
        for chave, (corpo, h) in atuais.items():
            if antigos.get(chave) != h:
                hubkit.publicar("agenda", "pendencia", chave, corpo, source_application=self.app_id, source_version=self.versao)
                c.execute("INSERT OR REPLACE INTO pendencias_publicadas(chave,hash,em) VALUES (?,?,?)", (chave, h, agora()))
                pub += 1
        for chave in set(antigos) - set(atuais):
            hubkit.publicar("agenda", "pendencia", chave, {"status": "resolvida", "origem_app": self.app_id, "origem_nome": self.nome,
                            "resolvida_em": agora()}, source_application=self.app_id, source_version=self.versao)
            c.execute("DELETE FROM pendencias_publicadas WHERE chave=?", (chave,))
            res += 1
        return {"abertas": len(atuais), "publicadas": pub, "resolvidas": res}

    def destinatarios(self, c: sqlite3.Connection, *perfis: str, unidades: list | None = None) -> list[dict]:
        """Pessoas do hub que têm estes perfis nesta ferramenta (para a agenda saber a quem entregar a pendência)."""
        out = []
        for r in c.execute("SELECT * FROM usuarios WHERE origem='hub'"):
            if r["perfil"] not in perfis:
                continue
            if unidades and r["unidade"] and r["unidade"] not in unidades:
                continue
            out.append({"id_hub": r["id"][2:] if r["id"].startswith("h-") else r["id"], "email": r["email"] or "", "nome": r["nome"]})
        return self.com_masters(out)

    @staticmethod
    def com_masters(out: list[dict], excluir: set | None = None) -> list[dict]:
        """Acrescenta os MASTER TESTE do hub (ambiente de teste) a uma lista de destinatários; `excluir`: ids do hub que
        não podem receber (ex.: quem concluiu não aprova) — o MASTER é exceção e recebe mesmo assim."""
        excluir = excluir or set()
        out = [d for d in out if d["id_hub"] not in excluir]
        ids = {d["id_hub"] for d in out}
        for p in hubkit.masters():
            if p["id"] not in ids:
                out.append({"id_hub": p["id"], "email": p.get("email") or "", "nome": p.get("nome") or p["id"], "master": True})
        return sorted(out, key=lambda x: x["id_hub"])

    def acao(self, codigo: str, rotulo: str, caminho: str, metodo: str = "POST", corpo: dict | None = None,
             comentario: str = "nenhum", estilo: str = "", campo: str = "comentario") -> dict:
        """Ação executável a partir da agenda: a agenda chama /apps/<ferramenta><caminho> em nome da pessoa.
        `comentario`: nenhum | opcional | obrigatorio — o texto vai no corpo, no campo `campo` (comentario, motivo, parecer…).
        A ferramenta de origem continua dona da regra: confere o perfil de quem chama e registra a auditoria."""
        return {"codigo": codigo, "rotulo": rotulo, "metodo": metodo, "caminho": caminho, "corpo": corpo or {}, "comentario": comentario,
                "campo": campo, "estilo": estilo}

    def _laco_sync(self) -> None:
        time.sleep(3)
        while True:
            if hubkit.no_hub():
                self.executar_sync()
            time.sleep(self._sync_intervalo)

    def auditar(self, c: sqlite3.Connection, u: dict | sqlite3.Row | None, acao: str, alvo: str = "", detalhes: Any = "",
                papel: str | None = None) -> None:
        """`papel`: o papel exercido na ação (aparece para o MASTER TESTE, que age com qualquer papel)."""
        master = bool(u and isinstance(u, dict) and u.get("master"))
        por = ((u or {})["nome"] + (" (MASTER TESTE)" if master else "")) if u else ""
        perfil = (f"{papel} · exceção de teste" if papel else "MASTER TESTE · exceção de teste") if master else ((u or {})["perfil"] if u else "")
        c.execute("INSERT INTO auditoria(em,por,perfil,acao,alvo,detalhes) VALUES (?,?,?,?,?,?)",
                  (agora(), por, perfil, acao, alvo, detalhes if isinstance(detalhes, str) else dump(detalhes)))

    # ── organização e configuração ────────────────────────
    def org(self) -> dict:
        return hubkit.organizacao(ORG_PADRAO)

    def voc(self) -> dict:
        return {**ORG_PADRAO["vocabulario"], **(self.org().get("vocabulario") or {})}

    def config(self) -> dict:
        cfg = copy.deepcopy(self.config_padrao)
        arq = self.base_dir / "config" / f"{self.app_id}.json"
        try:
            local = json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else {}
        except (OSError, ValueError):
            local = {}
        for k, v in local.items():
            if k.startswith("_"):
                continue
            cfg[k] = {**cfg[k], **v} if isinstance(cfg.get(k), dict) and isinstance(v, dict) else v
        return cfg

    # ── identidade ────────────────────────────────────────
    def _usuario_hub(self, c: sqlite3.Connection, ident: dict) -> sqlite3.Row:
        uid = "h-" + ident["id"]
        u = c.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
        if not u:
            perfil = self.admin if ident.get("papel") in ("dono", "admin") else self.padrao
            c.execute("INSERT INTO usuarios(id,nome,email,perfil,unidade,origem) VALUES (?,?,?,?,NULL,'hub')",
                      (uid, ident["nome"], ident.get("email") or "", perfil))
        elif u["nome"] != ident["nome"] or (u["email"] or "") != (ident.get("email") or ""):
            c.execute("UPDATE usuarios SET nome=?, email=? WHERE id=?", (ident["nome"], ident.get("email") or "", uid))
        return c.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()

    def usuario(self, c: sqlite3.Connection, x_usuario: str | None, obrigatorio: bool = True) -> dict | None:
        """No hub a pessoa é sempre quem o hub disse (sem simulação de perfis). O MASTER TESTE (só em ambiente de teste,
        assinado pelo hub) entra com o perfil de administrador da ferramenta e passa em qualquer exigência de papel."""
        ident = self._ident.get()
        if ident:
            u = dict(self._usuario_hub(c, ident))
            if ident.get("master"):
                u.update(perfil=self.admin, unidade=None, master=True)
            return u
        if hubkit.no_hub():
            raise HTTPException(401, "Identidade do hub ausente. Acesse pela página do hub.")
        if not x_usuario:
            if obrigatorio:
                raise HTTPException(401, "Informe o usuário (cabeçalho X-Usuario).")
            return None
        u = c.execute("SELECT * FROM usuarios WHERE id=?", (x_usuario,)).fetchone()
        if not u:
            raise HTTPException(401, "Usuário desconhecido.")
        return dict(u)

    @staticmethod
    def exigir(u: dict, *perfis: str, msg: str = "Seu perfil não permite esta ação.") -> None:
        if u.get("master"):
            return
        if u["perfil"] not in perfis:
            raise HTTPException(403, msg)

    @staticmethod
    def master(u: dict | None) -> bool:
        """MASTER TESTE: só existe em ambiente de teste; pode exercer qualquer papel e é exceção à segregação de funções."""
        return bool(u and isinstance(u, dict) and u.get("master"))

    @staticmethod
    def quem(u: dict) -> str:
        return u.get("email") or u.get("nome") or u.get("id")

    # ── Data Core ─────────────────────────────────────────
    def publicar(self, domain: str, entity_type: str, key: str, payload: dict, u: dict | None, period: str = "") -> dict:
        try:
            return hubkit.publicar(domain, entity_type, key, payload, period=period, source_user=self.quem(u) if u else "",
                                   source_version=self.versao, source_application=self.app_id)
        except Exception as e:
            raise HTTPException(502, f"O Data Core não aceitou a publicação ({e}).")

    def pendentes(self, c: sqlite3.Connection, domain: str, entity_type: str, filtro: Callable[[dict], bool] | None = None) -> list[dict]:
        """Registros do Data Core ainda não processados por esta ferramenta (controle por versão)."""
        try:
            regs = hubkit.consultar(domain, entity_type, limit=5000)
        except Exception as e:
            raise HTTPException(502, f"Data Core indisponível: {e}")
        out = []
        for r in regs:
            if filtro and not filtro(r):
                continue
            chave = f"{domain}/{entity_type}/{r['entity_key']}|{r.get('period', '')}"
            rec = c.execute("SELECT versao FROM integracao_recebidos WHERE chave=?", (chave,)).fetchone()
            if rec and rec["versao"] >= int(r.get("version") or 1):
                continue
            r["_chave"] = chave
            out.append(r)
        return out

    def marcar_recebido(self, c: sqlite3.Connection, r: dict, resultado: str) -> None:
        c.execute("INSERT OR REPLACE INTO integracao_recebidos(chave,versao,em,resultado) VALUES (?,?,?,?)",
                  (r["_chave"], int(r.get("version") or 1), agora(), resultado))

    # ── rotas comuns ──────────────────────────────────────
    def montar(self, app: FastAPI, meta_extra: Callable[[], dict] | None = None) -> None:
        base = self

        @app.middleware("http")
        async def _identidade(request: Request, call_next):
            tok = base._ident.set(hubkit.identidade(request.headers))
            try:
                return await call_next(request)
            finally:
                base._ident.reset(tok)

        @app.get("/health")
        def _health():
            return {"status": "ok", "app": base.app_id, "version": base.versao}

        @app.get("/api/meta")
        def _meta():
            o, cfg = base.org(), base.config()
            m = {"app": base.app_id, "nome": base.nome, "version": base.versao, "modo": "hub" if hubkit.no_hub() else "isolado",
                 "organizacao": {k: o.get(k) for k in ("nome", "sigla", "moeda", "cor_primaria", "cor_destaque", "unidades", "areas",
                                                       "vocabulario", "ano_base", "horizonte_anos", "taxa_desconto_aa")},
                 "perfis": base.rotulos_perfis(), "perfil_admin": base.admin, "rotulos": cfg.get("rotulos") or {},
                 "ambiente": ((base._ident.get() or {}).get("ambiente") or "producao") if hubkit.no_hub() else "isolado"}
            m["organizacao"]["vocabulario"] = base.voc()
            if meta_extra:
                m.update(meta_extra())
            return m

        @app.get("/api/sessao")
        def _sessao(x_usuario: str | None = Header(None)):
            ident = base._ident.get()
            with base.con() as c:
                if ident:
                    atual = base.usuario(c, x_usuario)
                    return {"modo": "hub", "usuario": atual, "real": atual, "simulando": False, "ambiente": ident.get("ambiente") or "producao",
                            "master": bool(atual.get("master"))}
                if hubkit.no_hub():
                    raise HTTPException(401, "Identidade do hub ausente.")
                u = c.execute("SELECT * FROM usuarios WHERE id=?", (x_usuario,)).fetchone() if x_usuario else None
                return {"modo": "isolado", "usuario": dict(u) if u else None, "real": None, "simulando": False}

        @app.get("/api/usuarios")
        def _usuarios():
            with base.con() as c:
                filtro = " WHERE origem != 'demo'" if hubkit.no_hub() else ""  # no hub não há usuários de demonstração
                return [dict(r) for r in c.execute("SELECT * FROM usuarios" + filtro + " ORDER BY origem DESC, nome")]

        @app.put("/api/usuarios/{uid}")
        def _alterar_usuario(uid: str, corpo: dict, x_usuario: str | None = Header(None)):
            with base.lock, base.con() as c:
                u = base.usuario(c, x_usuario)
                base.exigir(u, base.admin, msg="Somente o administrador da ferramenta altera perfis.")
                alvo = c.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
                if not alvo or (hubkit.no_hub() and alvo["origem"] == "demo"):
                    raise HTTPException(404, "Usuário não encontrado.")
                perfil = str(corpo.get("perfil") or alvo["perfil"])
                if perfil not in base.perfis:
                    raise HTTPException(400, "Perfil inválido.")
                if alvo["id"] == u["id"] and perfil != base.admin and not u.get("master"):
                    raise HTTPException(400, "Você não pode remover o seu próprio perfil de administrador.")
                c.execute("UPDATE usuarios SET perfil=?, unidade=? WHERE id=?", (perfil, corpo.get("unidade", alvo["unidade"]) or None, uid))
                base.auditar(c, u, "Perfil alterado", uid, {"perfil": perfil, "unidade": corpo.get("unidade")})
                return dict(c.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone())

        @app.get("/api/auditoria")
        def _auditoria(limite: int = 300, x_usuario: str | None = Header(None)):
            with base.con() as c:
                u = base.usuario(c, x_usuario)
                base.exigir(u, base.admin)
                return [dict(r) for r in c.execute("SELECT * FROM auditoria ORDER BY seq DESC LIMIT ?", (min(limite, 5000),))]

        @app.get("/api/sync/status")
        def _sync_status():
            return {**base.sync, "modo": "hub" if hubkit.no_hub() else "isolado", "agora": time.time()}

        @app.post("/api/sync/agora")
        def _sync_agora(x_usuario: str | None = Header(None)):
            with base.con() as c:
                base.usuario(c, x_usuario)
            return {**base.executar_sync(), "agora": time.time()}


    def servir_spa(self, app: FastAPI) -> None:
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=self.base_dir / "static", html=True), name="static")
