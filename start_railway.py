"""Bootstrap seguro e persistente para Railway. Não inclua senhas no repositório."""
import json, os, shutil, sys
from pathlib import Path

mount=Path(os.environ.get("STRATHUB_VOLUME", "/data"))
if not mount.is_dir():
    sys.exit("ERRO: monte um volume persistente em /data antes de iniciar")
if not os.environ.get("HUB_INITIAL_PASSWORD") or len(os.environ["HUB_INITIAL_PASSWORD"])<12:
    sys.exit("ERRO: configure HUB_INITIAL_PASSWORD com 12+ caracteres em Railway Variables")
root=Path(__file__).resolve().parent
apps=mount/"apps"
data=mount/"hub"
apps.mkdir(parents=True,exist_ok=True)
data.mkdir(parents=True,exist_ok=True)
# Não sobrescreva apps nem dados já criados. Publicações futuras pedem migração consciente.
for src in (root/"seed_apps" if (root/"seed_apps").exists() else root/"apps").iterdir():
    if src.is_dir() and not (apps/src.name).exists():
        shutil.copytree(src,apps/src.name)
os.environ.update(STRATHUB_HOME=str(root),STRATHUB_APPS=str(apps),STRATHUB_DATA=str(data))
from hub import auth,config
cfg=config.load()
# Railway: a PORT informada deve coincidir com a porta escutada e repassada internamente.
port=int(os.environ.get("PORT", "8800"))
if port<1 or port>65535:sys.exit("PORT inválida")
cfg.update(host="0.0.0.0",porta=port)
if not auth.senha_definida(cfg):
    auth.definir_senha(cfg,os.environ["HUB_INITIAL_PASSWORD"])
    print("Senha inicial configurada a partir de variável privada",flush=True)
config.save(cfg)
import uvicorn
uvicorn.run("hub.main:app",host="0.0.0.0",port=port,proxy_headers=True,forwarded_allow_ips="*",log_level="info")
