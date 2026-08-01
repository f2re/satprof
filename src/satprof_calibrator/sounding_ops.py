from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .config import resolve_path
from .qc import check_sounding
from .sources.dwd_bufr import download_recent as download_dwd_recent, read_bufr
from .sources.igra import download_recent_station, read_igra_file
from .storage import Workspace

Progress = Callable[[float, str], None]


def store_profiles(workspace: Workspace, profiles, qc_cfg: dict[str, Any]) -> int:
    count = 0
    for profile in profiles:
        qc = check_sounding(profile, qc_cfg)
        workspace.store_sounding(profile, {"passed": qc.passed, "flags": qc.flags, "metrics": qc.metrics})
        count += 1
    return count


def ingest_sounding_file(workspace: Workspace, source: str, path: str | Path, reader, qc_cfg: dict[str, Any]) -> dict[str, Any]:
    path = Path(path)
    if workspace.input_was_processed(source, path):
        return {"path": str(path), "status": "skipped", "records": 0}
    try:
        records = store_profiles(workspace, reader(path), qc_cfg)
        workspace.record_input(source, path, "ok", records)
        return {"path": str(path), "status": "ok", "records": records}
    except Exception as exc:
        workspace.record_input(source, path, "error", 0, str(exc))
        raise


def _safe_ingest(workspace: Workspace, source: str, path: Path, reader, qc_cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        return ingest_sounding_file(workspace, source, path, reader, qc_cfg)
    except Exception as exc:
        workspace.emit_event(
            "source.file_error",
            f"{source}: файл {path.name} не обработан",
            severity="warning",
            details={"path": str(path), "error": str(exc)},
        )
        return {"path": str(path), "status": "error", "records": 0, "error": str(exc)}


def _wis2_files(cfg: dict[str, Any]) -> list[Path]:
    wis_cfg = cfg.get("sources", {}).get("wis2", {})
    directory = resolve_path(cfg, wis_cfg.get("download_dir", "inbox/wis2"), workspace_relative=True)
    directory.mkdir(parents=True, exist_ok=True)
    found: dict[Path, None] = {}
    for pattern in wis_cfg.get("file_globs", ["*.bufr", "*.bufr4", "*.bin"]):
        for path in directory.glob(str(pattern)):
            if path.is_file() and not path.name.endswith(".part"):
                found[path.resolve()] = None
    return sorted(found, key=lambda path: (path.stat().st_mtime_ns, path.name))


def sync_soundings(workspace: Workspace, cfg: dict[str, Any], *, progress: Progress | None = None) -> dict[str, Any]:
    source_cfg = cfg.get("sources", {})
    qc_cfg = cfg.get("quality_control", {})
    results: dict[str, Any] = {"wis2": [], "dwd": [], "igra": []}

    wis_cfg = source_cfg.get("wis2", {})
    if wis_cfg.get("enabled", False):
        paths = _wis2_files(cfg)
        if progress:
            progress(0.03, f"WIS2: найдено файлов {len(paths)}")
        for index, path in enumerate(paths):
            results["wis2"].append(_safe_ingest(workspace, "wis2_bufr", path, read_bufr, qc_cfg))
            if progress:
                progress(0.04 + 0.31 * (index + 1) / max(len(paths), 1), f"WIS2: обработано {index + 1}/{len(paths)}")

    dwd_cfg = source_cfg.get("dwd", {})
    if dwd_cfg.get("enabled", True) and dwd_cfg.get("bufr_url"):
        if progress:
            progress(0.37, "Загрузка оперативных TEMP BUFR DWD")
        try:
            paths = download_dwd_recent(
                dwd_cfg["bufr_url"],
                workspace.root / "downloads" / "dwd",
                int(dwd_cfg.get("download_limit", 24)),
            )
        except Exception as exc:
            paths = []
            results["dwd"].append({"status": "error", "records": 0, "error": str(exc), "stage": "download"})
            workspace.emit_event("source.dwd_error", "Не удалось загрузить DWD TEMP BUFR", severity="warning", details={"error": str(exc)})
        for index, path in enumerate(paths):
            results["dwd"].append(_safe_ingest(workspace, "dwd_bufr", path, read_bufr, qc_cfg))
            if progress:
                progress(0.40 + 0.30 * (index + 1) / max(len(paths), 1), f"DWD: обработано {index + 1}/{len(paths)}")

    igra_cfg = source_cfg.get("igra", {})
    stations = list(igra_cfg.get("stations", []))
    if igra_cfg.get("enabled", bool(stations)) and stations:
        for index, station in enumerate(stations):
            try:
                path = download_recent_station(
                    str(station),
                    workspace.root / "downloads" / "igra",
                    igra_cfg["recent_base_url"],
                )
                results["igra"].append(_safe_ingest(workspace, "igra2", path, read_igra_file, qc_cfg))
            except Exception as exc:
                results["igra"].append({"station": str(station), "status": "error", "records": 0, "error": str(exc)})
                workspace.emit_event("source.igra_error", f"IGRA {station}: ошибка загрузки", severity="warning", details={"error": str(exc)})
            if progress:
                progress(0.72 + 0.25 * (index + 1) / max(len(stations), 1), f"IGRA: обработано {index + 1}/{len(stations)}")

    records = sum(item.get("records", 0) for group in results.values() for item in group)
    workspace.emit_event("sources.sync", f"Синхронизация зондирования завершена: {records} профилей", details=results)
    if progress:
        progress(1.0, "Зондирование обновлено")
    return {"records": records, "sources": results}
