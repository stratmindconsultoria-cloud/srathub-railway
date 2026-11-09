"""Prepara o ambiente do hub (instala/atualiza dependências só quando requirements.txt muda) e imprime a porta."""
import hashlib, subprocess, sys
from pathlib import Path

raiz = Path(__file__).resolve().parent.parent
req = raiz / "requirements.txt"
marca = raiz / ".venv" / ".hub_requirements.sha256"
h = hashlib.sha256(req.read_bytes()).hexdigest()
if not marca.exists() or marca.read_text().strip() != h:
    print("Instalando dependências do hub (só na primeira vez ou após atualização)...", file=sys.stderr)
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(req)])
    if r.returncode != 0:
        sys.exit(r.returncode)
    marca.write_text(h)
sys.path.insert(0, str(raiz))
from hub import config  # noqa: E402
print(config.load()["porta"])
