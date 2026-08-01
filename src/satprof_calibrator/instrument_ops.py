from __future__ import annotations

from typing import Any, Callable
import numpy as np
import pandas as pd

from .calibration import BiasCalibrator, apply_bias_model
from .collocation import collocate
from .pairs import build_pairs
from .radiometric import CountRadiometricCalibrator, apply_count_model_to_workspace
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
    return PyRttovRTM(
        instrument_cfg["coefficient_file"],
        instrument_cfg.get("channels", []),
        cfg.get("rttov", {}).get("add_interpolation", True),
    )


def _calibrated_pairs(pairs: pd.DataFrame) -> pd.DataFrame:
    if pairs.empty:
        return pairs
    return pairs[np.isfinite(pd.to_numeric(pairs["innovation_k"], errors="coerce"))].copy()


def _raw_count_pairs(pairs: pd.DataFrame) -> pd.DataFrame:
    if pairs.empty or "measurement_kind" not in pairs:
        return pairs.iloc[0:0].copy()
    return pairs[pairs["measurement_kind"] == "raw_counts"].copy()


def update_instrument(
    workspace: Workspace,
    cfg: dict[str, Any],
    instrument: str,
    *,
    fit_bias: bool = False,
    train_retrieval_model: bool = False,
    progress: Progress | None = None,
) -> dict[str, Any]:
    if instrument not in cfg.get("instruments", {}):
        raise KeyError(f"Прибор {instrument} не описан в config.instruments")
    instrument_cfg = cfg["instruments"][instrument]
    if progress:
        progress(0.05, "Четырёхмерная коллокация")
    collocations = collocate(
        workspace, instrument, instrument_cfg, cfg.get("collocation", {})
    )
    if collocations.empty:
        result = {
            "instrument": instrument,
            "collocations": 0,
            "pairs": 0,
            "status": "waiting_for_matchups",
        }
        workspace.emit_event(
            "instrument.refresh",
            f"{instrument}: нет новых коллокаций",
            severity="warning",
            details=result,
        )
        return result

    if progress:
        progress(0.25, "Расчёт радиаций по профилям")
    rtm = make_rtm(cfg, instrument)
    pairs = build_pairs(workspace, instrument, rtm, instrument_cfg)
    result: dict[str, Any] = {
        "instrument": instrument,
        "collocations": len(collocations),
        "pairs": len(pairs),
    }
    if pairs.empty:
        result["status"] = "waiting_for_pairs"
        workspace.emit_event(
            "instrument.refresh",
            f"{instrument}: радиационные пары не сформированы",
            severity="warning",
            details=result,
        )
        return result

    raw_pairs = _raw_count_pairs(pairs)
    calibrated = _calibrated_pairs(pairs)
    result["raw_count_pairs"] = int(len(raw_pairs))
    result["brightness_temperature_pairs"] = int(len(calibrated))

    if not raw_pairs.empty and fit_bias:
        if progress:
            progress(0.46, "Викарная калибровка счётов в яркостную температуру")
        radiometric = CountRadiometricCalibrator(cfg.get("radiometric", {})).fit(
            workspace, instrument, raw_pairs
        )
        result.update(
            {
                "radiometric_version": radiometric.version,
                "radiometric_status": radiometric.status,
                "radiometric_metrics": radiometric.metrics,
            }
        )
        if radiometric.status == "production":
            applied = apply_count_model_to_workspace(
                workspace, instrument, radiometric.path
            )
            result["radiometric_applied"] = applied
            pairs = build_pairs(workspace, instrument, rtm, instrument_cfg)
            raw_pairs = _raw_count_pairs(pairs)
            calibrated = _calibrated_pairs(pairs)
            result["raw_count_pairs_after_calibration"] = int(len(raw_pairs))
            result["brightness_temperature_pairs_after_calibration"] = int(len(calibrated))

    if calibrated.empty:
        result["status"] = (
            "radiometric_candidate"
            if result.get("radiometric_status") == "candidate"
            else "waiting_for_radiometric_calibration"
        )
        workspace.emit_event(
            "instrument.refresh",
            f"{instrument}: имеются только исходные счёты; требуется обучение count→TB",
            severity="warning",
            details=result,
        )
        if progress:
            progress(1.0, "Ожидание викарной калибровки")
        return result

    if progress:
        progress(0.60, "Обновление статистики O−B")
    result["health"] = update_statistics(
        workspace, instrument, calibrated, cfg.get("calibration", {})
    )

    if fit_bias:
        if progress:
            progress(0.72, "Оценка остаточных поправочных коэффициентов")
        bias = BiasCalibrator(cfg.get("calibration", {})).fit(
            workspace, instrument, calibrated
        )
        corrected = apply_bias_model(calibrated, bias.path)
        corrected_path = workspace.root / "matchups" / (
            f"{instrument}_corrected_pairs.csv.gz"
            if bias.status == "production"
            else f"{instrument}_candidate_{bias.version}.csv.gz"
        )
        corrected.to_csv(corrected_path, index=False, compression="gzip")
        result.update(
            {
                "bias_version": bias.version,
                "bias_status": bias.status,
                "corrected_pairs": str(corrected_path),
                "health": update_statistics(
                    workspace, instrument, corrected, cfg.get("calibration", {})
                ),
            }
        )
        retrieval_path = None
        if train_retrieval_model and bias.status == "production":
            if progress:
                progress(0.90, "Обучение модели восстановления профиля")
            retrieval = train_and_save(
                workspace,
                instrument,
                corrected,
                cfg["pressure_grid_hpa"],
                cfg.get("retrieval", {}),
            )
            retrieval_path = str(retrieval.path)
            result["retrieval_version"] = retrieval.version
        result.update(
            {
                "retrieval": retrieval_path,
                "report": str(
                    generate_report(
                        workspace,
                        instrument,
                        calibrated,
                        corrected,
                        bias.metrics,
                        None,
                    )
                ),
            }
        )
    else:
        result["report"] = str(
            generate_report(workspace, instrument, calibrated, None)
        )
        if not raw_pairs.empty:
            result["warning"] = "Часть каналов ожидает викарной калибровки count→TB"

    result["status"] = "ok"
    workspace.emit_event(
        "instrument.refresh",
        f"{instrument}: обновлено {len(calibrated)} пар яркостной температуры",
        details=result,
    )
    if progress:
        progress(1.0, "Обработка прибора завершена")
    return result


def update_all_statistics(workspace: Workspace, cfg: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for instrument in cfg.get("instruments", {}):
        corrected = workspace.root / "matchups" / f"{instrument}_corrected_pairs.csv.gz"
        raw = workspace.root / "matchups" / f"{instrument}_radiance_pairs.csv.gz"
        path = corrected if corrected.exists() else raw
        if not path.exists():
            continue
        frame = pd.read_csv(path)
        frame = _calibrated_pairs(frame)
        if not frame.empty:
            output[instrument] = update_statistics(
                workspace, instrument, frame, cfg.get("calibration", {})
            )
    return output
