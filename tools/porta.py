import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hub import config  # noqa: E402
print(config.load()["porta"])
