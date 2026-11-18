"""Small settings file: data/settings.json. A missing file means F1TV downloads are on."""
import json
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent
PATH = ROOT / config.SETTINGS_FILE
VALID = ("on", "off")


def read():
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError):
        data = {}
    if data.get("f1tv_downloads") not in VALID:
        data["f1tv_downloads"] = "on"
    return data


def f1tv_enabled():
    return read()["f1tv_downloads"] == "on"


def set_f1tv(value):
    if value not in VALID:
        raise ValueError("f1tv_downloads must be on or off")
    data = read()
    data["f1tv_downloads"] = value
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, indent=1))
    return data
