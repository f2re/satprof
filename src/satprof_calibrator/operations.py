from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .calibration import BiasCalibrator, apply_bias_model
from .collocation import collocate
from .pairs import build_pairs
from .qc import check_sounding
from .reporting import generate_report
from .retrieval import train_and_save
from .rtm import DemoWeightedRTM, PyRttovRTM
from .sources.dwd_bufr import download_recent as download_dwd_recent, read_bufr
from .sources.igra import download_recent_station, read_igra_file
from .statistics import update_statistics
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


def sync_soundings(workspace: Workspace, cfg: dict[str, Any], *, progress: Progress | None = None) -> dict[str, Any]:
    source_cfg = cfg.get("sources", {})
    qc_cfg = cfg.get("quality_control", {})
    results: dict[str, Any] = {"dwd": [], "igra": []}
    dwd_cfg = source_cfg.get("dwd", {})
    if dwd_cfg.get("enabled", True) and dwd_cfg.get("bufr_url"):
        if progress: progress(0.05, "Загрузка оперативных TEMP BUFR DWD")
        limit = int(dwd_cfg.get("download_limit", 24))
        paths = download_dwd_recent(dwd_cfg["bufr_url"], workspace.root / "downloads" / "dwd", limit)
        for index, path in enumerate(paths):
            results["dwd"].append(ingest_sounding_file(workspace, "dwd_bufr", path, read_bufr, qc_cfg))
            if progress: progress(0.1 + 0.45 * (index + 1) / max(len(paths), 1), f"DWD: обработано {index + 1}/{len(paths)}")
    igra_cfg = source_cfg.get("igra", {})
    stations = list(igra_cfg.get("stations", []))
    if igra_cfg.get("enabled", bool(stations)) and stations:
        base_url = igra_cfg["recent_base_url"]
        for index, station in enumerate(stations):
            path = download_recent_station(str(station), workspace.root / "downloads" / "igra", base_url)
            results["igra"].append(ingest_sounding_file(workspace, "igra2", path, read_igra_file, qc_cfg))
            if progress: progress(0.6 + 0.35 * (index + 1) / max(len(stations), 1), f"IGRA: обработано {index + 1}/{len(stations)}")
    records = sum(item.get("records", 0) for group in results.values() for item in group)
    workspace.emit_event("sources.sync", f"Синхронизация зондирования завершена: {records} профилей", details=results)
    if progress: progress(1.0, "Зондирование обновлено")
    return {"records": records, "sources": results}


def make_rtm(cfg: dict[str, Any], instrument: str):
    instrument_cfg = cfg["instruments"][instrument]
    backend = cfg.get("rttov", {}).get("backend", "pyrttov")
    if backend == "demo": return DemoWeightedRTM()
    return PyRttovRTM(instrument_cfg["coefficient_file"], instrument_cfg.get("channels", []), cfg.get("rttov", {}).get("add_interpolation", True))


def update_instrument(workspace: Workspace, cfg: dict[str, Any], instrument: str, *, fit_bias: bool = False, train_retrieval_model: bool = False, progress: Progress | None = None) -> dict[str, Any]:
    if instrument not in cfg.get("instruments", {}): raise KeyError(f"Прибор {instrument} не описан в config.instruments")
    if progress: progress(0.05, "Четырёхмерная коллокация")
    collocations = collocate(workspace, instrument, cfg["instruments"][instrument], cfg.get("collocation", {}))
    if collocations.empty:
        result = {"instrument": instrument, "collocations": 0, "pairs": 0, "status": "waiting_for_matchups"}
        workspace.emit_event("instrument.refresh", f"{instrument}: нет новых коллокаций", severity="warning", details=result)
        return result
    if progress: progress(0.30, "Расчёт радиаций по профилям")
    pairs = build_pairs(workspace, instrument, make_rtm(cfg, instrument), cfg["instruments"][instrument])
    result: dict[str, Any] = {"instrument": instrument, "collocations": len(collocations), "pairs": len(pairs)}
    if pairs.empty:
        result["status"] = "waiting_for_pairs"; workspace.emit_event("instrument.refresh", f"{instrument}: радиационные пары не сформированы", severity="warning", details=result); return result
    if progress: progress(0.55, "Обновление статистики O−B")
    result["health"] = update_statistics(workspace, instrument, pairs, cfg.get("calibration", {}))
    if fit_bias:
        if progress: progress(0.70, "Оценка поправочных коэффициентов")
        bias = BiasCalibrator(cfg.get("calibration", {})).fit(workspace, instrument, pairs)
        corrected = apply_bias_model(pairs, bias.path)
        corrected_path = workspace.root / "matchups" / (f"{instrument}_corrected_pairs.csv.gz" if bias.status == "production" else f"{instrument}_candidate_{bias.version}.csv.gz")
        corrected.to_csv(corrected_path, index=False, compression="gzip")
        result.update({"bias_version": bias.version, "bias_status": bias.status, "corrected_pairs": str(corrected_path), "health": update_statistics(workspace, instrument, corrected, cfg.get("calibration", {}))})
        retrieval_path = None
        if train_retrieval_model and bias.status == "production":
            if progress: progress(0.88, "Обучение модели восстановления профиля")
            retrieval = train_and_save(workspace, instrument, corrected, cfg["pressure_grid_hpa"], cfg.get("retrieval", {})); retrieval_path = str(retrieval.path); result["retrieval_version"] = retrieval.version
        result.update({"retrieval": retrieval_path, "report": str(generate_report(workspace, instrument, pairs, corrected, bias.metrics, None))})
    else:
        result["report"] = str(generate_report(workspace, instrument, pairs, None))
    result["status"] = "ok"
    workspace.emit_event("instrument.refresh", f"{instrument}: обновлено {len(pairs)} радиационных пар", details=result)
    if progress: progress(1.0, "Обработка прибора завершена")
    return result


def update_all_statistics(workspace: Workspace, cfg: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for instrument in cfg.get("instruments", {}):
        corrected = workspace.root / "matchups" / f"{instrument}_corrected_pairs.csv.gz"
        raw = workspace.root / "matchups" / f"{instrument}_radiance_pairs.csv.gz"
        path = corrected if corrected.exists() else raw
        if path.exists(): output[instrument] = update_statistics(workspace, instrument, pd.read_csv(path), cfg.get("calibration", {}))
    return output
