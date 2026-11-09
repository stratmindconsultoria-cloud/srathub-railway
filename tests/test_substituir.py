"""Troca de arquivos de uma ferramenta ao atualizar por .zip: nunca fica pela metade.  python tests/test_substituir.py"""
import os, sys, tempfile
from pathlib import Path
T = Path(tempfile.mkdtemp())
os.environ["STRATHUB_DATA"], os.environ["STRATHUB_APPS"] = str(T / "data"), str(T / "apps")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hub import admin  # noqa: E402

def montar(pasta, arquivos):
    for n, c in arquivos.items():
        (pasta / n).parent.mkdir(parents=True, exist_ok=True); (pasta / n).write_text(c)

apps = T / "apps"; d = apps / "x"; d.mkdir(parents=True)
montar(d, {"app.py": "v1", "static/i.html": "v1", "dados/db": "DADOS", ".venv/ok": "venv"})
novo = T / "novo"; novo.mkdir(); montar(novo, {"app.py": "v2", "static/i.html": "v2", "LEIA-ME.md": "v2"})
ant = admin.substituir_arquivos(d, novo, {".venv", "dados"})
assert (d / "app.py").read_text() == "v2" and (d / "dados/db").read_text() == "DADOS" and (d / ".venv/ok").exists(), "troca"
assert (admin.LIXEIRA / ant / "app.py").read_text() == "v1", "versão anterior na lixeira"
# falha no meio (arquivo em uso): tudo volta como estava
novo2 = T / "novo2"; novo2.mkdir(); montar(novo2, {"app.py": "v3", "static/i.html": "v3", "LEIA-ME.md": "v3"})
orig = admin._mover
def falha(o, dst, tentativas=1):
    if Path(o).name == "static" and "novo2" in str(o):
        raise PermissionError("em uso")
    return orig(o, dst, 1)
admin._mover = falha
try:
    admin.substituir_arquivos(d, novo2, {".venv", "dados"}); raise SystemExit("deveria falhar")
except RuntimeError as e:
    assert "versão anterior foi mantida" in str(e)
admin._mover = orig
assert (d / "app.py").read_text() == "v2" and (d / "static/i.html").read_text() == "v2" and (d / "dados/db").exists(), "rollback"
print("ok: troca atômica e rollback")
