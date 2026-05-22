import json
import os
from pathlib import Path


APP_NAME = "NussmannMediaForge"
CONFIG_FILE = "settings.json"


DEFAULT_SETTINGS = {
    "makemkv_path": "",
    "mkvtoolnix_path": "",
    "handbrake_path": "",
    "tmdb_api_key": "",
    "encoder": "nvidia",
    "output_dir": "",
    "default_language": "ger",
}


def get_config_dir():
    base_dir = os.environ.get("APPDATA")
    if not base_dir:
        base_dir = str(Path.home() / ".config")
    return Path(base_dir) / APP_NAME


def get_config_path():
    return get_config_dir() / CONFIG_FILE


def load_settings():
    path = get_config_path()
    settings = DEFAULT_SETTINGS.copy()
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as handle:
                stored = json.load(handle)
            if isinstance(stored, dict):
                settings.update({k: v for k, v in stored.items() if k in settings})
        except (OSError, json.JSONDecodeError):
            pass
    return settings


def save_settings(settings):
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    clean_settings = DEFAULT_SETTINGS.copy()
    clean_settings.update({k: v for k, v in settings.items() if k in clean_settings})
    with path.open("w", encoding="utf-8") as handle:
        json.dump(clean_settings, handle, indent=2)
    return clean_settings
