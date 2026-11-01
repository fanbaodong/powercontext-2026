"""Small explicit configuration loader; no credentials in output."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, value = line.partition('=')
            if sep and key.startswith(('MINIMAX_', 'SEEKDB_')):
                os.environ.setdefault(key, value.strip())
