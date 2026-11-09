"""Gerenciador de processos: instala dependências, inicia sob demanda, verifica saúde, para por ociosidade."""
from __future__ import annotations
import asyncio, hashlib, json, os, socket, subprocess, sys, time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psutil

from . import config
from .registry import AppDef

IS_WIN = os.name == "nt"
PORTS_FILE = config.DATA_DIR / "ports.json"
PIDS_FILE = config.DATA_DIR / "pids.json"
LOG_MAX = 2 * 1024 * 1024


@dataclass
class AppState:
    status: str = "parado"     # parado | instalando | iniciando | ativo | erro
    mensagem: str = ""
    pid: int | None = None
    porta: int | None = None
    iniciado_em: float | None = None
    ultimo_acesso: float = 0.0
    proc: subprocess.Popen | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Runner:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.states: dict[str, AppState] = {}
        self.ports: dict[str, int] = self._load_ports()
        self._limpar_orfaos()

    # ── processos que sobraram de uma execução anterior (ex.: queda de energia) ──
    def _limpar_orfaos(self):
        try:
            antigos = json.loads(PIDS_FILE.read_text())
        except Exception:
            antigos = {}
        apps_dir = str(config.APPS_DIR.resolve()).lower()
        for app_id, pid in antigos.items():
            try:
                p = psutil.Process(int(pid))
                if str(Path(p.cwd()).resolve()).lower().startswith(apps_dir):
                    for c in p.children(recursive=True) + [p]:
                        try:
                            c.kill()
                        except psutil.Error:
                            pass
            except (psutil.Error, ValueError):
                pass
        self._salvar_pids()

    def _salvar_pids(self):
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            PIDS_FILE.write_text(json.dumps({k: s.pid for k, s in self.states.items() if s.pid}))
        except Exception:
            pass

    # ── utilidades ──────────────────────────────────────────
    def state(self, app_id: str) -> AppState:
        if app_id not in self.states:
            self.states[app_id] = AppState()
        return self.states[app_id]

    def _load_ports(self) -> dict[str, int]:
        try:
            return {k: int(v) for k, v in json.loads(PORTS_FILE.read_text()).items()}
        except Exception:
            return {}

    def _save_ports(self):
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        PORTS_FILE.write_text(json.dumps(self.ports, indent=2))

    @staticmethod
    def _port_free(p: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) if not IS_WIN else None
            try:
                s.bind(("127.0.0.1", p))
                return True
            except OSError:
                return False

    def _port_for(self, app_id: str) -> int:
        p = self.ports.get(app_id)
        if p and self._port_free(p):
            return p
        used = set(self.ports.values())
        p = int(self.cfg.get("porta_inicial_apps", 9100))
        while p in used or not self._port_free(p) or p == int(self.cfg.get("porta", 8800)):
            p += 1
        self.ports[app_id] = p
        self._save_ports()
        return p

    def log_path(self, app_id: str) -> Path:
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        return config.LOGS_DIR / f"{app_id}.log"

    def _open_log(self, app_id: str):
        lp = self.log_path(app_id)
        if lp.exists() and lp.stat().st_size > LOG_MAX:
            bak = lp.with_suffix(".log.1")
            if bak.exists():
                bak.unlink()
            lp.rename(bak)
        return open(lp, "ab", buffering=0)

    def _log(self, app_id: str, msg: str):
        with self._open_log(app_id) as f:
            f.write(f"[hub {time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n".encode("utf-8"))

    def tail(self, app_id: str, lines: int = 200) -> str:
        lp = self.log_path(app_id)
        if not lp.exists():
            return ""
        data = lp.read_bytes()[-200_000:].decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-lines:])

    @staticmethod
    def venv_python(app: AppDef) -> Path:
        return app.dir / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")

    # ── dependências ────────────────────────────────────────
    def _run_logged(self, app: AppDef, args, shell=False, timeout=900) -> int:
        with self._open_log(app.id) as f:
            f.write(f"[hub] $ {args if isinstance(args, str) else ' '.join(map(str, args))}\n".encode())
            kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WIN else {}
            r = subprocess.run(args, cwd=app.dir, stdout=f, stderr=subprocess.STDOUT, shell=shell, timeout=timeout, **kw)
            return r.returncode

    def _install_python(self, app: AppDef) -> None:
        req = app.dir / app.requisitos if app.requisitos else None
        if not req or not req.exists():
            return
        py = self.venv_python(app)
        if not py.exists():
            self._log(app.id, "criando ambiente Python isolado (.venv)")
            if self._run_logged(app, [sys.executable, "-m", "venv", ".venv"]) != 0:
                raise RuntimeError("falha ao criar .venv (veja o log)")
        h = hashlib.sha256(req.read_bytes()).hexdigest()
        marker = app.dir / ".venv" / ".hub_requirements.sha256"
        if marker.exists() and marker.read_text().strip() == h:
            return
        self._log(app.id, f"instalando dependências de {app.requisitos}")
        if self._run_logged(app, [str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", app.requisitos]) != 0:
            raise RuntimeError("falha ao instalar dependências (veja o log)")
        marker.write_text(h)

    def _install_node(self, app: AppDef) -> None:
        pkg = app.dir / "package.json"
        if not pkg.exists():
            return
        lock = app.dir / "package-lock.json"
        h = hashlib.sha256(pkg.read_bytes() + (lock.read_bytes() if lock.exists() else b"")).hexdigest()
        marker = app.dir / "node_modules" / ".hub_package.sha256"
        if marker.exists() and marker.read_text().strip() == h:
            return
        self._log(app.id, "instalando dependências (npm install)")
        if self._run_logged(app, "npm install", shell=True) != 0:
            raise RuntimeError("falha no npm install (veja o log)")
        marker.parent.mkdir(exist_ok=True)
        marker.write_text(h)

    # ── ciclo de vida ───────────────────────────────────────
    def command_for(self, app: AppDef, port: int) -> str:
        py = self.venv_python(app)
        python = f'"{py}"' if py.exists() else f'"{sys.executable}"'
        return (app.comando.replace("{python}", python).replace("{port}", str(port))
                .replace("{base_noslash}", app.base.rstrip("/")).replace("{base}", app.base)
                .replace("{dir}", str(app.dir)).replace("{host}", "127.0.0.1"))

    def alive(self, app_id: str) -> bool:
        st = self.state(app_id)
        return bool(st.proc and st.proc.poll() is None)

    async def ensure_running(self, app: AppDef) -> AppState:
        """Garante o app ativo (inicia sob demanda). Seguro para chamadas concorrentes."""
        st = self.state(app.id)
        st.ultimo_acesso = time.time()
        if st.status == "ativo" and self.alive(app.id):
            return st
        async with st.lock:
            if st.status == "ativo" and self.alive(app.id):
                return st
            try:
                st.status, st.mensagem = "instalando", "preparando dependências"
                if app.tipo == "python":
                    await asyncio.to_thread(self._install_python, app)
                elif app.tipo == "node":
                    await asyncio.to_thread(self._install_node, app)
                st.status, st.mensagem = "iniciando", ""
                port = self._port_for(app.id)
                from .identidade import token_app
                env = {**os.environ, **app.env, "PORT": str(port), "HUB_APP_ID": app.id, "HUB_BASE_PATH": app.base,
                       "HUB_ROOT_PATH": app.base.rstrip("/"), "HUB_URL": f"http://127.0.0.1:{self.cfg.get('porta', 8800)}",
                       "HUB_APP_TOKEN": token_app(self.cfg, app.id),
                       "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
                cmd = self.command_for(app, port)
                self._log(app.id, f"iniciando na porta interna {port}: {cmd}")
                logf = self._open_log(app.id)
                kw = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if IS_WIN
                      else {"start_new_session": True})
                st.proc = subprocess.Popen(cmd, cwd=app.dir, env=env, stdout=logf, stderr=subprocess.STDOUT, shell=True, **kw)
                logf.close()
                st.pid, st.porta, st.iniciado_em = st.proc.pid, port, time.time()
                self._salvar_pids()
                await self._wait_healthy(app, st)
                st.status, st.mensagem = "ativo", ""
                self._log(app.id, "pronto")
            except Exception as e:
                st.status, st.mensagem = "erro", str(e)
                self._log(app.id, f"ERRO: {e}")
                await asyncio.to_thread(self._kill, st)
        return st

    async def _wait_healthy(self, app: AppDef, st: AppState):
        deadline = time.time() + float(self.cfg.get("inicio_timeout_s", 90))
        url = f"http://127.0.0.1:{st.porta}{app.health if app.health.startswith('/') else '/' + app.health}"
        async with httpx.AsyncClient(timeout=2.0) as c:
            while time.time() < deadline:
                if st.proc and st.proc.poll() is not None:
                    raise RuntimeError(f"o processo encerrou ao iniciar (código {st.proc.returncode}). Veja o log.")
                try:
                    r = await c.get(url)
                    if r.status_code < 500:
                        return
                except Exception:
                    pass
                await asyncio.sleep(0.4)
        raise RuntimeError("tempo esgotado aguardando o app responder (verifique 'health' e 'comando' no hub.json)")

    def _kill(self, st: AppState):
        if not st.proc:
            return
        try:
            parent = psutil.Process(st.proc.pid)
            procs = parent.children(recursive=True) + [parent]
            for p in procs:
                try:
                    p.terminate()
                except psutil.Error:
                    pass
            _, alive = psutil.wait_procs(procs, timeout=6)
            for p in alive:
                try:
                    p.kill()
                except psutil.Error:
                    pass
        except psutil.Error:
            pass
        st.proc = None
        st.pid = None
        self._salvar_pids()

    async def stop(self, app_id: str, motivo: str = "parado pelo usuário"):
        st = self.state(app_id)
        async with st.lock:
            await asyncio.to_thread(self._kill, st)
            st.status, st.mensagem, st.iniciado_em = "parado", motivo, None
            self._log(app_id, motivo)

    async def stop_all(self):
        for app_id in list(self.states):
            if self.alive(app_id):
                await self.stop(app_id, "hub encerrado")

    async def housekeeping(self, apps: dict[str, AppDef]):
        """Detecta processos que caíram e para os ociosos."""
        now = time.time()
        for app_id, st in list(self.states.items()):
            if st.status == "ativo" and not self.alive(app_id):
                code = st.proc.returncode if st.proc else "?"
                st.status, st.mensagem, st.proc, st.pid = "parado", f"encerrou sozinho (código {code})", None, None
                self._log(app_id, st.mensagem)
                continue
            app = apps.get(app_id)
            if st.status == "ativo" and app:
                idle = app.ocioso_min if app.ocioso_min is not None else self.cfg.get("ocioso_min_padrao", 30)
                if idle and not app.sempre_ligado and now - st.ultimo_acesso > float(idle) * 60:
                    await self.stop(app_id, f"parado por ociosidade ({idle} min sem uso)")

    def snapshot(self, app: AppDef) -> dict:
        if not app.is_process:
            return {"status": "disponivel" if not app.erro else "erro", "mensagem": app.erro}
        st = self.state(app.id)
        mem = None
        if st.pid and self.alive(app.id):
            try:
                p = psutil.Process(st.pid)
                mem = sum((c.memory_info().rss for c in p.children(recursive=True)), p.memory_info().rss) // (1024 * 1024)
            except psutil.Error:
                pass
        return {"status": st.status if not app.erro else "erro", "mensagem": st.mensagem or app.erro, "porta": st.porta,
                "pid": st.pid, "iniciado_em": st.iniciado_em, "ultimo_acesso": st.ultimo_acesso or None, "memoria_mb": mem}
