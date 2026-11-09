"""Conector CRM — integração genérica entre CRMs e a execução operacional (Ordens de Serviço).

Não é um CRM: recebe "solicitações de serviço" num formato neutro (empurradas por webhook/automação — n8n, Make, Zapier —,
buscadas periodicamente numa URL do CRM ou carregadas por arquivo, como contingência), publica no Data Core
(servicos/solicitacao_externa) e devolve ao CRM o status de cada OS (servicos/status_os) por webhook ou consulta.
Adaptadores específicos (HubSpot, Pipedrive, RD Station, Salesforce…) ficam para versões futuras: hoje o "de-para" de
campos é configurável por integração.
"""
from __future__ import annotations
import csv, hashlib, os, hmac, io, json, re, secrets, threading, time, urllib.error, urllib.request, uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile

import hubkit
from appkit import Base, agora, dump, load

BASE_DIR = Path(__file__).resolve().parent
VERSAO = "1.1.2"
FORMATO = "strat-servicos/1"
CAMPOS = ["crm_ref", "titulo", "descricao", "tipo", "prioridade", "cliente.nome", "cliente.documento", "cliente.id", "contato.nome",
          "contato.telefone", "contato.email", "local.endereco", "local.cidade", "local.uf", "local.referencia", "unidade", "ativo",
          "codigo_projeto", "data_desejada"]
SCHEMA = """
CREATE TABLE IF NOT EXISTS integracoes(id TEXT PRIMARY KEY, nome TEXT NOT NULL, crm TEXT, ativo INTEGER NOT NULL DEFAULT 1,
  chave_hash TEXT, chave_dica TEXT, webhook_url TEXT, webhook_segredo TEXT, pull_url TEXT, pull_token TEXT, pull_intervalo_min INTEGER,
  pull_ultimo TEXT, mapeamento TEXT NOT NULL DEFAULT '{}', padroes TEXT NOT NULL DEFAULT '{}', criado_em TEXT, atualizado_em TEXT);
CREATE TABLE IF NOT EXISTS entradas(seq INTEGER PRIMARY KEY AUTOINCREMENT, integracao_id TEXT NOT NULL, canal TEXT NOT NULL, crm_ref TEXT,
  chave TEXT, status TEXT NOT NULL, erro TEXT, payload TEXT, em TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_ent_integ ON entradas(integracao_id, seq);
CREATE TABLE IF NOT EXISTS saidas(seq INTEGER PRIMARY KEY AUTOINCREMENT, integracao_id TEXT NOT NULL, chave TEXT NOT NULL, crm_ref TEXT,
  numero_os TEXT, status_os TEXT, versao INTEGER, http_status INTEGER, erro TEXT, tentativas INTEGER NOT NULL DEFAULT 0, payload TEXT,
  em TEXT NOT NULL, enviado_em TEXT);
CREATE INDEX IF NOT EXISTS ix_sai_chave ON saidas(chave, seq);
"""
PERFIS = {"integrador": "Integrador (administrador)", "leitor": "Leitura"}
DEMO = [("u-demo-int", "Integrador Demo", "integrador", None), ("u-demo-leitor", "Leitor Demo", "leitor", None)]
CONFIG = {"max_tentativas_webhook": 8, "rotulos": {}}
B = Base("conector_crm", "Conector CRM", VERSAO, BASE_DIR, SCHEMA, PERFIS, admin="integrador", padrao="leitor", demo=DEMO, config_padrao=CONFIG)


@asynccontextmanager
async def _vida(_app):
    B.iniciar()
    yield

app = FastAPI(title="Conector CRM", version=VERSAO, lifespan=_vida)
B.montar(app, lambda: {"formato": FORMATO, "campos": CAMPOS})


# ───────────────────────────────────────────────── utilidades
def _hash(chave: str) -> str:
    return hashlib.sha256(chave.encode()).hexdigest()


def _integ(c, iid: str):
    r = c.execute("SELECT * FROM integracoes WHERE id=?", (iid,)).fetchone()
    if not r:
        raise HTTPException(404, "Integração não encontrada.")
    return r


def _publico(r) -> dict:
    d = dict(r)
    d.pop("chave_hash", None)
    d["webhook_segredo"] = bool(d.get("webhook_segredo"))
    d["pull_token"] = bool(d.get("pull_token"))
    d["mapeamento"] = load(d["mapeamento"], {}); d["padroes"] = load(d["padroes"], {})
    return d


def _pegar(obj, caminho: str):
    """Lê um campo por caminho com pontos (ex.: properties.subject, contacts.0.email)."""
    cur = obj
    for p in str(caminho).split("."):
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and p.isdigit() and int(p) < len(cur):
            cur = cur[int(p)]
        else:
            return None
        if cur is None:
            return None
    return cur


def _por(obj: dict, caminho: str, valor) -> None:
    partes = caminho.split(".")
    for p in partes[:-1]:
        obj = obj.setdefault(p, {})
    obj[partes[-1]] = valor


def normalizar(bruto: dict, mapeamento: dict, padroes: dict) -> dict:
    """Converte um registro do CRM para o formato neutro. Sem de-para, espera o formato neutro (com ou sem aninhamento)."""
    if not isinstance(bruto, dict):
        raise ValueError("Cada solicitação deve ser um objeto JSON.")
    out: dict = {}
    for campo in CAMPOS:
        origem = (mapeamento or {}).get(campo)
        v = _pegar(bruto, origem) if origem else (_pegar(bruto, campo) if "." in campo else bruto.get(campo))
        if v is None and "." in campo:  # aceita também o achatado: cliente_nome
            v = bruto.get(campo.replace(".", "_"))
        if v is None and campo in (padroes or {}):
            v = padroes[campo]
        if v not in (None, ""):
            _por(out, campo, v if isinstance(v, (int, float, bool)) and campo not in ("crm_ref",) else str(v).strip())
    if isinstance(bruto.get("local"), str) and "local" not in out:
        out["local"] = {"endereco": bruto["local"]}
    if not out.get("crm_ref"):
        raise ValueError("Campo obrigatório ausente: crm_ref (identificador da solicitação no CRM).")
    if not out.get("titulo"):
        raise ValueError("Campo obrigatório ausente: titulo.")
    pri = str(out.get("prioridade") or "").lower()
    out["prioridade"] = {"low": "baixa", "medium": "media", "média": "media", "high": "alta", "urgent": "urgente", "critica": "urgente", "crítica": "urgente"}.get(pri, pri) or None
    out = {k: v for k, v in out.items() if v not in (None, "")}
    return out


def _chave(iid: str, ref: str) -> str:
    return f"crm:{iid}:{ref}"


def receber(c, integ, registros: list, canal: str) -> dict:
    """Valida, normaliza e publica cada solicitação. Retorna o resultado por item (erros não interrompem o lote)."""
    if not integ["ativo"]:
        raise HTTPException(409, "Integração desativada.")
    mp, pd = load(integ["mapeamento"], {}), load(integ["padroes"], {})
    ok, erros = [], []
    for i, bruto in enumerate(registros):
        try:
            n = normalizar(bruto, mp, pd)
            chave = _chave(integ["id"], n["crm_ref"])
            corpo = {**n, "formato": FORMATO, "crm_nome": integ["crm"] or integ["nome"], "integracao": integ["id"], "recebida_em": agora(), "canal": canal}
            hubkit.publicar("servicos", "solicitacao_externa", chave, corpo, source_application=B.app_id, source_version=B.versao)
            c.execute("INSERT INTO entradas(integracao_id,canal,crm_ref,chave,status,payload,em) VALUES (?,?,?,?,?,?,?)",
                      (integ["id"], canal, n["crm_ref"], chave, "ok", dump(bruto), agora()))
            ok.append({"crm_ref": n["crm_ref"], "chave": chave})
        except ValueError as e:
            ref = str(bruto.get("crm_ref") or bruto.get("id") or "") if isinstance(bruto, dict) else ""
            c.execute("INSERT INTO entradas(integracao_id,canal,crm_ref,status,erro,payload,em) VALUES (?,?,?,?,?,?,?)",
                      (integ["id"], canal, ref, "erro", str(e), dump(bruto), agora()))
            erros.append({"item": i + 1, "crm_ref": ref or None, "erro": str(e)})
    return {"recebidas": len(ok), "erros": erros, "itens": ok}


def _lista(corpo) -> list:
    if isinstance(corpo, list):
        return corpo
    if isinstance(corpo, dict):
        for k in ("solicitacoes", "items", "results", "data", "records"):
            if isinstance(corpo.get(k), list):
                return corpo[k]
        return [corpo]
    raise HTTPException(400, "Corpo deve ser um objeto ou uma lista de objetos JSON.")


def _http(metodo: str, url: str, corpo: bytes | None, cabecalhos: dict, timeout: float = 15) -> tuple[int, bytes]:
    """Chamada HTTP de saída (isolada para os testes)."""
    req = urllib.request.Request(url, data=corpo, method=metodo, headers=cabecalhos)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if hasattr(e, "read") else b""


# ───────────────────────────────────────────────── entrada pública (autenticada pela chave da integração)
def _por_chave(c, iid: str, chave: str | None, authorization: str | None):
    tok = chave or (authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None)
    integ = c.execute("SELECT * FROM integracoes WHERE id=?", (iid,)).fetchone()
    if not integ or not tok or not integ["chave_hash"] or not hmac.compare_digest(integ["chave_hash"], _hash(tok)):
        raise HTTPException(401, "Integração ou chave inválida.")
    return integ


@app.post("/entrada/{iid}/solicitacoes")
async def entrada(iid: str, request: Request, x_chave: str | None = Header(None), authorization: str | None = Header(None)):
    """Webhook de entrada: o CRM (ou n8n/Make/Zapier) envia uma solicitação ou uma lista."""
    try:
        corpo = json.loads((await request.body()) or b"null")
    except ValueError:
        raise HTTPException(400, "JSON inválido.")
    with B.lock, B.con() as c:
        integ = _por_chave(c, iid, x_chave, authorization)
        res = receber(c, integ, _lista(corpo), "webhook")
    return {"formato": FORMATO, **res}


@app.get("/entrada/{iid}/status/{ref:path}")
def status_publico(iid: str, ref: str, x_chave: str | None = Header(None), authorization: str | None = Header(None)):
    """Consulta (pull) do status da OS gerada para uma solicitação do CRM."""
    with B.con() as c:
        integ = _por_chave(c, iid, x_chave, authorization)
    regs = hubkit.consultar("servicos", "status_os", entity_key=_chave(integ["id"], ref), limit=1)
    if not regs:
        recebida = hubkit.consultar("servicos", "solicitacao_externa", entity_key=_chave(integ["id"], ref), limit=1)
        if not recebida:
            raise HTTPException(404, "Solicitação desconhecida.")
        return {"formato": FORMATO, "evento": "status_os", "crm_ref": ref, "status": "recebida", "status_nome": "Recebida, aguardando abertura da OS"}
    return _evento_status(regs[0])


@app.get("/entrada/{iid}/status")
def status_lote(iid: str, desde: float = 0, x_chave: str | None = Header(None), authorization: str | None = Header(None)):
    """Todos os status alterados desde `desde` (epoch) — para o CRM sincronizar em lote."""
    with B.con() as c:
        integ = _por_chave(c, iid, x_chave, authorization)
    pref = _chave(integ["id"], "")
    regs = [r for r in hubkit.consultar("servicos", "status_os", since=desde or None, limit=5000) if str(r["entity_key"]).startswith(pref)]
    return {"formato": FORMATO, "agora": time.time(), "itens": [_evento_status(r) for r in regs]}


def _evento_status(r: dict) -> dict:
    p = r.get("payload") or {}
    return {"formato": FORMATO, "evento": "status_os", "crm_ref": p.get("ref_externa") or str(r["entity_key"]).split(":", 2)[-1],
            "numero_os": p.get("numero"), "status": p.get("status"), "status_nome": p.get("status_nome"), "sla": p.get("sla"),
            "prazo_sla": p.get("prazo_sla"), "execucao_inicio": p.get("execucao_inicio"), "equipe": p.get("equipe"),
            "concluida_em": p.get("concluida_em"), "motivo": p.get("motivo"), "resumo_execucao": p.get("resumo_execucao"),
            "atualizado_em": p.get("atualizado_em") or r.get("updated_at"), "versao": r.get("version")}


# ───────────────────────────────────────────────── administração das integrações
@app.get("/api/integracoes")
def listar(x_usuario: str | None = Header(None)):
    with B.con() as c:
        B.usuario(c, x_usuario)
        out = []
        for r in c.execute("SELECT * FROM integracoes ORDER BY nome"):
            d = _publico(r)
            d["entradas_ok"] = c.execute("SELECT COUNT(*) FROM entradas WHERE integracao_id=? AND status='ok'", (r["id"],)).fetchone()[0]
            d["entradas_erro"] = c.execute("SELECT COUNT(*) FROM entradas WHERE integracao_id=? AND status='erro'", (r["id"],)).fetchone()[0]
            d["saidas_ok"] = c.execute("SELECT COUNT(*) FROM saidas WHERE integracao_id=? AND enviado_em IS NOT NULL", (r["id"],)).fetchone()[0]
            d["saidas_pendentes"] = c.execute("SELECT COUNT(*) FROM saidas WHERE integracao_id=? AND enviado_em IS NULL AND erro IS NOT NULL", (r["id"],)).fetchone()[0]
            out.append(d)
        return out


CAMPOS_INTEG = ("nome", "crm", "webhook_url", "pull_url", "pull_intervalo_min")


@app.put("/api/integracoes/{iid}")
def salvar(iid: str, corpo: dict, x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "integrador")
        nova = iid == "nova"
        if nova:
            iid = re.sub(r"[^a-z0-9]+", "-", str(corpo.get("nome") or "crm").lower()).strip("-")[:24] or "crm"
            base, k = iid, 1
            while c.execute("SELECT 1 FROM integracoes WHERE id=?", (iid,)).fetchone():
                k += 1; iid = f"{base}-{k}"
            c.execute("INSERT INTO integracoes(id,nome,criado_em) VALUES (?,?,?)", (iid, str(corpo.get("nome") or iid), agora()))
        else:
            _integ(c, iid)
        for k in CAMPOS_INTEG:
            if k in corpo:
                v = corpo[k]
                if k in ("webhook_url", "pull_url") and v and not re.match(r"^https?://", str(v)):
                    raise HTTPException(400, "URLs devem começar com http:// ou https://.")
                c.execute(f"UPDATE integracoes SET {k}=? WHERE id=?", (int(v) if k == "pull_intervalo_min" and v not in (None, "") else (v or None), iid))
        for k in ("webhook_segredo", "pull_token"):
            if corpo.get(k):
                c.execute(f"UPDATE integracoes SET {k}=? WHERE id=?", (corpo[k], iid))
            if corpo.get(k) == "":
                pass
            if corpo.get("limpar_" + k):
                c.execute(f"UPDATE integracoes SET {k}=NULL WHERE id=?", (iid,))
        for k in ("mapeamento", "padroes"):
            if k in corpo:
                v = corpo[k]
                if isinstance(v, str):
                    try:
                        v = json.loads(v or "{}")
                    except ValueError:
                        raise HTTPException(400, f"{k}: JSON inválido.")
                if not isinstance(v, dict) or any(x not in CAMPOS for x in v):
                    raise HTTPException(400, f"{k}: use as chaves do formato neutro ({', '.join(CAMPOS)}).")
                c.execute(f"UPDATE integracoes SET {k}=? WHERE id=?", (dump(v), iid))
        if "ativo" in corpo:
            c.execute("UPDATE integracoes SET ativo=? WHERE id=?", (1 if corpo["ativo"] else 0, iid))
        c.execute("UPDATE integracoes SET atualizado_em=? WHERE id=?", (agora(), iid))
        res = {}
        if nova or corpo.get("gerar_chave"):
            chave = "crm_" + secrets.token_urlsafe(24)
            c.execute("UPDATE integracoes SET chave_hash=?, chave_dica=? WHERE id=?", (_hash(chave), chave[:8] + "…" + chave[-4:], iid))
            res["chave"] = chave  # mostrada uma única vez
        B.auditar(c, u, "Integração salva" + (" (nova chave)" if res else ""), iid)
        return {**_publico(_integ(c, iid)), **res}


@app.get("/api/integracoes/{iid}")
def detalhe(iid: str, x_usuario: str | None = Header(None)):
    with B.con() as c:
        B.usuario(c, x_usuario)
        return {**_publico(_integ(c, iid)),
                "entradas": [{**dict(r), "payload": load(r["payload"], None)} for r in c.execute("SELECT * FROM entradas WHERE integracao_id=? ORDER BY seq DESC LIMIT 200", (iid,))],
                "saidas": [{**dict(r), "payload": load(r["payload"], None)} for r in c.execute("SELECT * FROM saidas WHERE integracao_id=? ORDER BY seq DESC LIMIT 200", (iid,))]}


@app.post("/api/integracoes/{iid}/testar")
def testar_mapeamento(iid: str, corpo: dict, x_usuario: str | None = Header(None)):
    """Mostra como um registro de exemplo do CRM ficaria no formato neutro (não publica nada)."""
    with B.con() as c:
        B.usuario(c, x_usuario); integ = _integ(c, iid)
        mp = corpo.get("mapeamento", load(integ["mapeamento"], {})); pd = corpo.get("padroes", load(integ["padroes"], {}))
        try:
            return {"ok": True, "resultado": normalizar(corpo.get("exemplo") or {}, mp, pd)}
        except ValueError as e:
            return {"ok": False, "erro": str(e)}


@app.post("/api/integracoes/{iid}/importar")
async def importar(iid: str, arquivo: UploadFile = File(...), x_usuario: str | None = Header(None)):
    """Contingência: JSON (objeto/lista) ou CSV com cabeçalhos do formato neutro (cliente.nome ou cliente_nome)."""
    bruto = await arquivo.read()
    nome = (arquivo.filename or "").lower()
    try:
        if nome.endswith(".csv"):
            txt = bruto.decode("utf-8-sig")
            dialeto = csv.Sniffer().sniff(txt.splitlines()[0]) if txt.strip() else csv.excel
            regs = [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(io.StringIO(txt), dialect=dialeto)]
            regs = [{**{k: v for k, v in r.items() if "." not in k}, **_aninhar({k: v for k, v in r.items() if "." in k})} for r in regs]
        else:
            regs = _lista(json.loads(bruto.decode("utf-8")))
    except (ValueError, UnicodeDecodeError, csv.Error):
        raise HTTPException(400, "Arquivo inválido: use JSON ou CSV (UTF-8).")
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "integrador")
        res = receber(c, _integ(c, iid), regs, "arquivo")
        B.auditar(c, u, "Importação por arquivo", iid, {"recebidas": res["recebidas"], "erros": len(res["erros"])})
        return res


def _aninhar(d: dict) -> dict:
    out: dict = {}
    for k, v in d.items():
        if v != "":
            _por(out, k, v)
    return out


@app.post("/api/integracoes/{iid}/webhook-teste")
def webhook_teste(iid: str, x_usuario: str | None = Header(None)):
    with B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "integrador"); integ = _integ(c, iid)
    if not integ["webhook_url"]:
        raise HTTPException(400, "Configure a URL do webhook de saída.")
    st, txt = _enviar(integ, {"formato": FORMATO, "evento": "teste", "integracao": iid, "em": agora()})
    return {"http_status": st, "resposta": txt[:500]}


def _enviar(integ, evento: dict) -> tuple[int, str]:
    corpo = json.dumps(evento, ensure_ascii=False).encode()
    cab = {"Content-Type": "application/json", "User-Agent": f"strathub-conector-crm/{VERSAO}", "X-Evento": evento.get("evento", "")}
    if integ["webhook_segredo"]:
        cab["X-Assinatura"] = "sha256=" + hmac.new(integ["webhook_segredo"].encode(), corpo, hashlib.sha256).hexdigest()
    try:
        st, resp = _http("POST", integ["webhook_url"], corpo, cab)
    except Exception as e:
        return 0, str(e)
    return st, resp.decode("utf-8", "replace") if isinstance(resp, bytes) else str(resp)


# ───────────────────────────────────────────────── laço: saída de status e busca periódica
def _sincronizar(c, u) -> dict:
    integs = {r["id"]: r for r in c.execute("SELECT * FROM integracoes")}
    enviados = falhas = guardados = buscadas = 0
    maxt = int(B.config().get("max_tentativas_webhook") or 8)
    for r in B.pendentes(c, "servicos", "status_os", filtro=lambda r: str(r["entity_key"]).startswith("crm:")):
        iid = str(r["entity_key"]).split(":")[1]
        integ = integs.get(iid)
        if not integ:
            B.marcar_recebido(c, r, "integração inexistente"); continue
        ev = _evento_status(r)
        ant = c.execute("SELECT * FROM saidas WHERE chave=? AND versao=? ORDER BY seq DESC LIMIT 1", (r["entity_key"], r.get("version"))).fetchone()
        tent = (ant["tentativas"] if ant else 0) + 1
        if not integ["webhook_url"] or not integ["ativo"]:
            c.execute("INSERT INTO saidas(integracao_id,chave,crm_ref,numero_os,status_os,versao,tentativas,payload,em,erro) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (iid, r["entity_key"], ev["crm_ref"], ev["numero_os"], ev["status"], r.get("version"), 0, dump(ev), agora(), None))
            B.marcar_recebido(c, r, "disponível para consulta"); guardados += 1; continue
        st, txt = _enviar(integ, ev)
        sucesso = 200 <= st < 300
        if ant and not ant["enviado_em"]:
            c.execute("UPDATE saidas SET http_status=?, erro=?, tentativas=?, enviado_em=? WHERE seq=?",
                      (st, None if sucesso else (txt[:300] or f"HTTP {st}"), tent, agora() if sucesso else None, ant["seq"]))
        else:
            c.execute("INSERT INTO saidas(integracao_id,chave,crm_ref,numero_os,status_os,versao,http_status,erro,tentativas,payload,em,enviado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (iid, r["entity_key"], ev["crm_ref"], ev["numero_os"], ev["status"], r.get("version"), st, None if sucesso else (txt[:300] or f"HTTP {st}"),
                       tent, dump(ev), agora(), agora() if sucesso else None))
        if sucesso or tent >= maxt:
            B.marcar_recebido(c, r, f"webhook HTTP {st}" if sucesso else f"desistiu após {tent} tentativas")
        enviados += sucesso; falhas += (not sucesso)
    # busca periódica (pull) nas integrações configuradas
    for integ in integs.values():
        if not (integ["ativo"] and integ["pull_url"] and integ["pull_intervalo_min"]):
            continue
        ult = float(integ["pull_ultimo"] or 0)
        if time.time() - ult < int(integ["pull_intervalo_min"]) * 60:
            continue
        cab = {"Accept": "application/json"}
        if integ["pull_token"]:
            cab["Authorization"] = "Bearer " + integ["pull_token"]
        try:
            st, resp = _http("GET", integ["pull_url"], None, cab)
            if 200 <= st < 300:
                buscadas += receber(c, integ, _lista(json.loads(resp or b"[]")), "busca")["recebidas"]
            else:
                c.execute("INSERT INTO entradas(integracao_id,canal,status,erro,em) VALUES (?,?,?,?,?)", (integ["id"], "busca", "erro", f"HTTP {st}", agora()))
        except Exception as e:
            c.execute("INSERT INTO entradas(integracao_id,canal,status,erro,em) VALUES (?,?,?,?,?)", (integ["id"], "busca", "erro", str(e)[:300], agora()))
        c.execute("UPDATE integracoes SET pull_ultimo=? WHERE id=?", (str(time.time()), integ["id"]))
    return {"status_enviados_ao_crm": enviados, "falhas_de_envio": falhas, "status_para_consulta": guardados, "solicitacoes_buscadas": buscadas}


def _pendencias(c) -> list[dict]:
    out = []
    for r in c.execute("SELECT integracao_id, COUNT(*) n FROM saidas WHERE enviado_em IS NULL AND erro IS NOT NULL GROUP BY integracao_id"):
        out.append({"chave": f"falha:{r['integracao_id']}", "natureza": "acao", "tipo": "integracao", "titulo": f"Conector CRM: {r['n']} status sem entrega ({r['integracao_id']})",
                    "perfis": ["integrador"], "link": f"#/integracao/{r['integracao_id']}", "prioridade": "normal"})
    return out


B.automatico(_sincronizar, _pendencias, intervalo_s=60)


@app.post("/api/sincronizar")
def sincronizar(x_usuario: str | None = Header(None)):
    with B.lock, B.con() as c:
        u = B.usuario(c, x_usuario); B.exigir(u, "integrador")
        c.execute("UPDATE integracoes SET pull_ultimo=NULL")
        return _sincronizar(c, u)


B.servir_spa(app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=os.environ.get("CONECTOR_CRM_HOST", "127.0.0.1"), port=int(os.environ.get("PORT") or 8860),
                root_path=os.environ.get("HUB_ROOT_PATH", ""))
