"""Hierarquia (líder direto) e serviço de notificações do hub.   python tests/test_notificacoes.py"""
import os, sys, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
T = Path(tempfile.mkdtemp())
os.environ["STRATHUB_DATA"], os.environ["STRATHUB_APPS"] = str(T / "data"), str(T / "apps")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hub import datacore, notificacoes as N, usuarios  # noqa: E402

ok = 0
def check(desc, cond):
    global ok
    assert cond, desc
    ok += 1; print("  ✓", desc)

cfg = {"porta": 8800, "dono_nome": "Diretora", "dono_email": "dir@x.com", "fuso_horas": -3}
datacore.iniciar(); N.iniciar()
ger = usuarios.criar("Gerente", "ger@x.com", "senha-forte-9")
coo = usuarios.criar("Coordenadora", "coo@x.com", "senha-forte-9")
ana = usuarios.criar("Analista", "ana@x.com", "senha-forte-9")
usuarios.alterar(ger["id"], {"lider_id": usuarios.validar_lider(cfg, ger["id"], "dono")})
usuarios.alterar(coo["id"], {"lider_id": usuarios.validar_lider(cfg, coo["id"], ger["id"])})
usuarios.alterar(ana["id"], {"lider_id": usuarios.validar_lider(cfg, ana["id"], coo["id"]), "jornada_h": 6})
check("hierarquia: liderados diretos", [p["id"] for p in usuarios.liderados(cfg, ger["id"])] == [coo["id"]])
try:
    usuarios.validar_lider(cfg, ger["id"], ana["id"]); check("ciclo recusado", False)
except ValueError as e:
    check("ciclo recusado (líder abaixo da pessoa)", "ciclo" in str(e))
check("jornada por pessoa", next(p for p in usuarios.todos(cfg) if p["id"] == ana["id"])["jornada_h"] == 6.0)
afetados = usuarios.remover(coo["id"])
check("remover alguém do meio: a analista passa a responder ao gerente", afetados == [ana["id"]] and
      next(p for p in usuarios.todos(cfg) if p["id"] == ana["id"])["lider_id"] == ger["id"])

# notificações
enviados = []
N.enviar_smtp = lambda cfg_, para, assunto, txt, htm: enviados.append((para, assunto, htm))
cfg["notificacoes"] = {"smtp": {"host": "smtp.teste"}}
time.sleep(0.01)
def pend(chave, **p):
    datacore.gravar(domain="agenda", entity_type="pendencia", entity_key=chave, source_application="ordens",
                    payload={"status": "aberta", "origem_app": "ordens", "origem_nome": "Ordens", "link": "#/os/1", **p})
pend("ordens:aprovar:1", natureza="acao", tipo="aprovacao", titulo="Aprovar OS 1", destinatarios=[{"id_hub": ger["id"], "email": "ger@x.com"}],
     acoes=[{"codigo": "a", "rotulo": "Aprovar", "caminho": "/api/x"}])
pend("ordens:planejar:1", natureza="tarefa", titulo="Planejar OS 1", destinatarios=[{"id_hub": ger["id"]}])
r = N.despachar(cfg, agora=datetime(2026, 11, 4, 6, 0, tzinfo=timezone(timedelta(hours=-3))))
check("decisão vai na hora por e-mail; tarefa espera o resumo", r["imediatos"] == 1 and [e[1] for e in enviados] == ["[STRAT·HUB] Aprovar OS 1"])
check("o e-mail traz o link para a ferramenta, sem botão de decisão", "/apps/ordens/#/os/1" in enviados[0][2] and "Aprovar</button>" not in enviados[0][2])
N.despachar(cfg, agora=datetime(2026, 11, 4, 6, 1, tzinfo=timezone(timedelta(hours=-3))))
check("não repete o envio", len(enviados) == 1)
r = N.despachar(cfg, agora=datetime(2026, 11, 4, 7, 31, tzinfo=timezone(timedelta(hours=-3))))
check("resumo diário às 07:30 com a tarefa", r["resumos"] == 1 and "resumo do dia: 1" in enviados[-1][1])
N.despachar(cfg, agora=datetime(2026, 11, 4, 9, 0, tzinfo=timezone(timedelta(hours=-3))))
check("um resumo por dia", len(enviados) == 2)
itens = N.da_pessoa(cfg, next(p for p in usuarios.todos(cfg) if p["id"] == ger["id"]), app="ordens")
check("tela de notificações da pessoa, com as ações da origem", len(itens) == 2 and itens[0]["acoes"] and itens[0]["link"] == "/apps/ordens/#/os/1")
N.marcar(ger["id"], ["ordens:aprovar:1"])
ger_p = next(p for p in usuarios.todos(cfg) if p["id"] == ger["id"])
check("lida some das pendentes e aparece nas lidas", [i["chave"] for i in N.da_pessoa(cfg, ger_p)] == ["ordens:planejar:1"]
      and [i["chave"] for i in N.da_pessoa(cfg, ger_p, estado="lidas")] == ["ordens:aprovar:1"])
check("ferramentas acompanham as lidas", [x["chave"] for x in N.lidas_desde(ger["id"])] == ["ordens:aprovar:1"])
# preferências e obrigatórias
try:
    N.salvar_preferencias(cfg, ana["id"], {"categorias": {"alerta": {"email": "desligado"}}}); check("obrigatória", False)
except ValueError:
    check("alerta é obrigatória: não dá para desligar o e-mail", True)
pr = N.salvar_preferencias(cfg, ana["id"], {"categorias": {"decisao": {"email": "desligado"}, "alerta": {"email": "resumo"}}})
check("preferência própria vale", pr["efetivas"]["decisao"]["email"] == "desligado" and pr["efetivas"]["alerta"]["email"] == "resumo")
pend("ordens:aprovar:2", natureza="acao", titulo="Aprovar OS 2", destinatarios=[{"id_hub": ana["id"], "email": "ana@x.com"}], acoes=[{"rotulo": "A", "caminho": "/a"}])
n0 = len(enviados); N.despachar(cfg, agora=datetime(2026, 11, 4, 9, 5, tzinfo=timezone(timedelta(hours=-3))))
check("quem desligou não recebe", len(enviados) == n0)
# aprovação que vai pelo app Aprovações do Teams não gera e-mail nosso
datacore.gravar(domain="integracao", entity_type="microsoft", entity_key="estado", source_application="conector_microsoft",
                payload={"aprovacoes_teams": True, "teams_feed": True, "email": True})
pend("ordens:aprovar:3", natureza="acao", titulo="Aprovar OS 3", destinatarios=[{"id_hub": ger["id"], "email": "ger@x.com"}], acoes=[{"rotulo": "A", "caminho": "/a"}])
pend("ordens:sla:3", natureza="acao", tipo="alerta", titulo="SLA estourado OS 3", destinatarios=[{"id_hub": ger["id"], "email": "ger@x.com"}])
n0 = len(enviados); N.despachar(cfg, agora=datetime(2026, 11, 4, 9, 6, tzinfo=timezone(timedelta(hours=-3))))
envios = {r["entity_key"]: r["payload"] for r in datacore.listar("notificacao", "envio")}
check("aprovação no app Aprovações do Teams: sem e-mail e sem aviso nosso", not any("aprovar:3" in k for k in envios) and
      all("Aprovar OS 3" not in e[1] for e in enviados[n0:]))
check("alerta: e-mail na hora + pedido de aviso no Teams ao conector", any("SLA estourado" in e[1] for e in enviados[n0:]) and
      any(k.startswith("teams:") and "sla:3" in k for k in envios))
print(f"\n{ok}/{ok} verificações OK")
