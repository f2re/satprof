from __future__ import annotations

from typing import Any, Callable
import pandas as pd

from .calibration import BiasCalibrator, apply_bias_model
from .collocation import collocate
from .pairs import build_pairs
from .reporting import generate_report
from .retrieval import train_and_save
from .rtm import DemoWeightedRTM, PyRttovRTM
from .statistics import update_statistics
from .storage import Workspace

Progress = Callable[[float, str], None]


def make_rtm(cfg: dict[str, Any], instrument: str):
    instrument_cfg = cfg["instruments"][instrument]
    backend = cfg.get("rttov", {}).get("backend", "pyrttov")
    if backend == "demo":
        return DemoWeightedRTM()
    return PyRttovRTM(instrument_cfg["coefficient_file"], instrument_cfg.get("channels", []), cfg.get("rttov", {}).get("add_interpolation", True))


def update_instrument(workspace: Workspace, cfg: dict[str, Any], instrument: str, *, fit_bias: bool = False, train_retrieval_model: bool = False, progress: Progress | None = None) -> dict[str, Any]:
    if instrument not in cfg.get("instruments", {}):
        raise KeyError(f"Прибор {instrument} не описан в config.instruments")
    if progress:
        progress(0.05, "Четырёхмерная коллокация")
    collocations = collocate(workspace, instrument, cfg["instruments"][instrument], cfg.get("collocation", {}))
    if collocations.empty:
        result = {"instrument": instrument, "collocations": 0, "pairs": 0, "status": "waiting_for_matchups"}
        workspace.emit_event("instrument.refresh", f"{instrument}: нет новых коллокаций", severity="warning", details=result)
        return result
    if progress:
        progress(0.30, "Расчёт радиаций по профилям")
    pairs = build_pairs(workspace, instrument, make_rtm(cfg, instrument), cfg["instruments"][instrument])
    result: dict[str, Any] = {"instrument": instrument, "collocations": len(collocations), "pairs": len(pairs)}
    if pairs.empty:
        result["status"] = "waiting_for_pairs"
        workspace.emit_event("instrument.refresh", f"{instrument}: радиационные пары не сформированы", severity="warning", details=result)
        return result
    if progress:
        progress(0.55, "Обновление статистики O−B")
    result["health"] = update_statistics(workspace, instrument, pairs, cfg.get("calibration", {}))
    if fit_bias:
        if progress:
            progress(0.70, "Оценка поправочных коэффициентов")
        bias = BiasCalibrator(cfg.get("calibration", {})).fit(workspace, instrument, pairs)
        corrected = apply_bias_model(pairs, bias.path)
        corrected_path = workspace.root / "matchups" / (f"{instrument}_corrected_pairs.csv.gz" if bias.status == "production" else f"{instrument}_candidate_{bias.version}.csv.gz")
        corrected.to_csv(corrected_path, index=False, compression="gzip")
        result.update({"bias_version": bias.version, "bias_status": bias.status, "corrected_pairs": str(corrected_path), "health": update_statistics(workspace, instrument, corrected, cfg.get("calibration", {}))})
        retrieval_path = None
        if train_retrieval_model and bias.status == "production":
            if progress:
                progress(0.88, "Обучение модели восстановления профиля")
            retrieval = train_and_save(workspace, instrument, corrected, cfg["pressure_grid_hpa"], cfg.get("retrieval", {}))
            retrieval_path = str(retrieval.path)
            result["retrieval_version"] = retrieval.version
        result.update({"retrieval": retrieval_path, "report": str(generate_report(workspace, instrument, pairs, corrected, bias.metrics, None))})
    else:
        result["report"] = str(generate_report(workspace, instrument, pairs, None))
    result["status"] = "ok"
    workspace.emit_event("instrument.refresh", f"{instrument}: обновлено {len(pairs)} радиационных пар", details=result)
    if progress:
        progress(1.0, "Обработка прибора завершена")
    return result


def update_all_statistics(workspace: Workspace, cfg: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for instrument in cfg.get("instruments", {}):
        corrected = workspace.root / "matchups" / f"{instrument}_corrected_pairs.csv.gz"
        raw = workspace.root / "matchups" / f"{instrument}_radiance_pairs.csv.gz"
        path = corrected if corrected.exists() else raw
        if path.exists():
            output[instrument] = update_statistics(workspace, instrument, pd.read_csv(path), cfg.get("calibration", {}))
    return output
