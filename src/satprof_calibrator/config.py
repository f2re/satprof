from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import os
import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "workspace": "./workspace",
    "web": {"host": "127.0.0.1", "port": 8088, "openlayers_cdn": "https://cdn.jsdelivr.net/npm/ol@10.6.1"},
    "worker": {
        "poll_seconds": 5,
        "stale_job_minutes": 60,
        "max_attempts": 3,
        "schedules": {
            "sync_soundings": 1800,
            "scan_satdump": 60,
            "sync_tle": 21600,
            "update_statistics": 86400,
            "train_calibration": 604800,
        },
    },
    "satdump": {
        "enabled": True,
        "root": "/opt/SatDump",
        "required_branch": "release/1.2.2",
        "repository": "https://github.com/f2re/SatDump",
        "runner": "scripts/astra/run.sh",
        "timeout_seconds": 7200,
        "inbox": "inbox/satdump",
        "output": "satdump-output",
        "manifest_glob": "*.satprof.json",
    },
    "satellites": [
        {"name": "Метеор-М №2-3", "norad_id": 57166, "family": "meteor", "color": "#0b63ce"},
        {"name": "Метеор-М №2-4", "norad_id": 59051, "family": "meteor", "color": "#0f8a71"},
        {"name": "Электро-Л №3", "norad_id": 44903, "family": "elektro", "color": "#7a4cc2"},
        {"name": "Электро-Л №4", "norad_id": 55506, "family": "elektro", "color": "#b15a00"},
    ],
    "tle": {
        "url_template": "https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=TLE",
        "timeout_seconds": 30,
        "track_minutes": 100,
        "track_step_seconds": 120,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        env_path = os.environ.get("SATPROF_CONFIG")
        path = env_path if env_path else None
    user: dict[str, Any] = {}
    config_path: Path | None = None
    if path:
        config_path = Path(path).expanduser().resolve()
        user = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    cfg = _deep_merge(DEFAULT_CONFIG, user)
    cfg["_config_path"] = str(config_path) if config_path else None
    cfg["_config_dir"] = str(config_path.parent) if config_path else str(Path.cwd())
    return cfg


def resolve_path(cfg: dict[str, Any], value: str | Path, *, workspace_relative: bool = False) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    if workspace_relative:
        workspace = Path(cfg.get("workspace", "./workspace")).expanduser()
        if not workspace.is_absolute():
            workspace = Path(cfg.get("_config_dir", Path.cwd())) / workspace
        return (workspace / path).resolve()
    return (Path(cfg.get("_config_dir", Path.cwd())) / path).resolve()


def workspace_path(cfg: dict[str, Any], explicit: str | Path | None = None) -> Path:
    value = explicit if explicit is not None else cfg.get("workspace", "./workspace")
    return resolve_path(cfg, value)
