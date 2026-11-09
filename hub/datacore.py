"""Data Core: dados de negócio compartilhados entre apps (compatível com a API /api/data-core do HUB PMO).

Cada registro é identificado por (domain, entity_type, entity_key, period) e guarda um payload JSON,
a aplicação de origem, o usuário e uma versão. Toda gravação entra no histórico.
Os apps continuam com seus próprios bancos: o Data Core é a fronteira de troca entre eles.
"""
from __future__ import annotations
import json, sqlite3, threading, time
from contextlib import contextmanager

from . import config

DB = config.DATA_DIR / "data_core.db"
_lock = threading.Lock()
SCHEMA = """
CREATE TABLE IF NOT EXISTS registros(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain TEXT NOT NULL, entity_type TEXT NOT NULL, entity_key TEXT NOT NULL, period TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '{}', source_application TEXT NOT NULL DEFAULT '', source_user TEXT NOT NULL DEFAULT '',
  source_version TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, updated_at REAL NOT NULL,
  UNIQUE(domain, entity_type, entity_key, period));
CREATE INDEX IF NOT EXISTS ix_reg_tipo ON registros(domain, entity_type, updated_at);
CREATE TABLE IF NOT EXISTS historico(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER, domain TEXT, entity_type TEXT, entity_key TEXT, period TEXT,
  payload TEXT, source_application TEXT, source_user TEXT, source_version TEXT, version INTEGER, operation TEXT, at REAL);
CREATE INDEX IF NOT EXISTS ix_hist_reg ON historico(record_id);
"""
LIMITE_PAYLOAD = 25 * 1024 * 1024


class Conflito(Exception):
    pass


@contextmanager
def _con():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def iniciar() -> None:
    with _con() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)


def _ser(r: sqlite3.Row, com_payload: bool = True) -> dict:
    d = {"id": r["id"], "domain": r["domain"], "entity_type": r["entity_type"], "entity_key": r["entity_key"],
         "period": r["period"], "source_application": r["source_application"], "source_user": r["source_user"],
         "source_version": r["source_version"], "version": r["version"], "deleted": bool(r["deleted"]),
         "created_at": _iso(r["created_at"]), "updated_at": _iso(r["updated_at"]), "updated_ts": r["updated_at"], "created_ts": r["created_at"]}
    if com_payload:
        try:
            d["payload"] = json.loads(r["payload"] or "{}")
        except Exception:
            d["payload"] = {}
    return d


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + f".{int((ts % 1) * 1000):03d}Z"


def listar(domain=None, entity_type=None, entity_key=None, period=None, desde: float | None = None,
           incluir_excluidos=False, limit=200, com_payload=True) -> list[dict]:
    sql, args = "SELECT * FROM registros WHERE 1=1", []
    for col, v in (("domain", domain), ("entity_type", entity_type), ("entity_key", entity_key), ("period", period)):
        if v is not None and v != "":
            sql += f" AND {col}=?"; args.append(v)
    if desde:
        sql += " AND updated_at>?"; args.append(float(desde))
    if not incluir_excluidos:
        sql += " AND deleted=0"
    sql += " ORDER BY updated_at DESC LIMIT ?"; args.append(max(1, min(int(limit or 200), 5000)))
    with _con() as c:
        return [_ser(r, com_payload) for r in c.execute(sql, args)]


def obter(rid: int) -> dict | None:
    with _con() as c:
        r = c.execute("SELECT * FROM registros WHERE id=?", (rid,)).fetchone()
        return _ser(r) if r else None


def gravar(*, domain: str, entity_type: str, entity_key: str, period: str = "", payload: dict,
           source_application: str, source_user: str = "", source_version: str = "",
           expected_version: int | None = None, excluir: bool = False) -> dict:
    for nome, v, m in (("domain", domain, 120), ("entity_type", entity_type, 120), ("entity_key", entity_key, 255), ("period", period, 80)):
        if not isinstance(v, str) or (nome != "period" and not v.strip()) or len(v) > m:
            raise ValueError(f"{nome} inválido")
    if not isinstance(payload, dict):
        raise ValueError("payload precisa ser um objeto JSON")
    txt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(txt) > LIMITE_PAYLOAD:
        raise ValueError("payload maior que 25 MB")
    agora = time.time()
    with _lock, _con() as c:
        r = c.execute("SELECT * FROM registros WHERE domain=? AND entity_type=? AND entity_key=? AND period=?",
                      (domain, entity_type, entity_key, period)).fetchone()
        if expected_version is not None:
            atual = r["version"] if r else 0
            if int(expected_version) != atual:
                raise Conflito(f"versão esperada {expected_version}, atual {atual}")
        if r:
            ver, op = r["version"] + 1, ("DELETE" if excluir else "UPDATE")
            c.execute("UPDATE registros SET payload=?, source_application=?, source_user=?, source_version=?, version=?, deleted=?, updated_at=? WHERE id=?",
                      (txt, source_application, source_user, source_version, ver, int(excluir), agora, r["id"]))
            rid = r["id"]
        else:
            if excluir:
                raise KeyError("registro não encontrado")
            ver, op = 1, "INSERT"
            rid = c.execute("INSERT INTO registros(domain,entity_type,entity_key,period,payload,source_application,source_user,source_version,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (domain, entity_type, entity_key, period, txt, source_application, source_user, source_version, 1, agora, agora)).lastrowid
        c.execute("INSERT INTO historico(record_id,domain,entity_type,entity_key,period,payload,source_application,source_user,source_version,version,operation,at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (rid, domain, entity_type, entity_key, period, txt, source_application, source_user, source_version, ver, op, agora))
        return _ser(c.execute("SELECT * FROM registros WHERE id=?", (rid,)).fetchone())


def historico(rid: int, limit: int = 100) -> list[dict]:
    with _con() as c:
        rows = c.execute("SELECT * FROM historico WHERE record_id=? ORDER BY seq DESC LIMIT ?", (rid, min(limit, 1000))).fetchall()
    out = []
    for r in rows:
        try:
            p = json.loads(r["payload"] or "{}")
        except Exception:
            p = {}
        out.append({"seq": r["seq"], "version": r["version"], "operation": r["operation"], "at": _iso(r["at"]),
                    "source_application": r["source_application"], "source_user": r["source_user"], "payload": p})
    return out


def resumo() -> dict:
    with _con() as c:
        tipos = [dict(r) for r in c.execute(
            "SELECT domain, entity_type, COUNT(*) AS registros, MAX(updated_at) AS ultimo FROM registros WHERE deleted=0 GROUP BY domain, entity_type ORDER BY domain, entity_type")]
        total = c.execute("SELECT COUNT(*) FROM registros WHERE deleted=0").fetchone()[0]
        hist = c.execute("SELECT COUNT(*) FROM historico").fetchone()[0]
    for t in tipos:
        t["ultimo"] = _iso(t["ultimo"]) if t["ultimo"] else None
    return {"status": "online", "records": total, "history": hist, "tipos": tipos, "engine": "sqlite"}
