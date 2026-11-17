"""Configuracion local del ejecutable; no almacena contrasenas."""
import json
import os
from pathlib import Path
import tempfile

APP_NAME = "GTNHClientUpdater"

def settings_path():
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / APP_NAME / "settings.json"
    return Path.home() / ".config" / APP_NAME / "settings.json"

def load():
    path = settings_path()
    if not path.exists():
        return {"manifest_url": "", "instance_dir": "", "automatic_prelaunch": False,
                "approved_gtnh_version": ""}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("settings.json invalido")
    return data

def save(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=2, ensure_ascii=False)
            out.write("\n")
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
