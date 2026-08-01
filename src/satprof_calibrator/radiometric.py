from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor
from sklearn.metrics import mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .qc import check_satellite
from .schemas import SatelliteGranule
from .storage import Workspace

FEATURES = [
    "raw_count",
    "raw_count2",
    "scan",
    "scan2",
    "secant_zenith_minus_1",
]


@dataclass
class RadiometricFitResult:
    version: str
    status: str
    metrics: dict[str, Any]
    path: Path


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output["raw_count"] = pd.to_numeric(output["raw_count"], errors="coerce")
    output["raw_count2"] = np.square(output["raw_count"] / 65535.0)
    output["scan"] = pd.to_numeric(output.get("scan", 0.0), errors="coerce").fillna(0.0)
    output["scan2"] = np.square(output["scan"])
    if "secant_zenith_minus_1" not in output:
        zenith = pd.to_numeric(output.get("satellite_zenith_deg", 0.0), errors="coerce").fillna(0.0)
        output["secant_zenith_minus_1"] = 1.0 / np.clip(np.cos(np.deg2rad(zenith)), 0.2, None) - 1.0
    return output


def _channel_key(models: dict[Any, Any], channel: Any) -> Any | None:
    for candidate in (channel, str(channel)):
        if candidate in models:
            return candidate
    try:
        candidate = int(channel)
    except (TypeError, ValueError):
        return None
    return candidate if candidate in models else None


class CountRadiometricCalibrator:
    def __init__(self, cfg: dict[str, Any] | None = None):
        self.cfg = cfg or {}

    @staticmethod
    def _pipeline() -> Pipeline:
        return Pipeline(
            [
                ("scale", StandardScaler()),
                ("model", HuberRegressor(epsilon=1.35, max_iter=1000)),
            ]
        )

    def fit(self, workspace: Workspace, instrument: str, pairs: pd.DataFrame) -> RadiometricFitResult:
        selected = pairs.copy()
        if "measurement_kind" in selected:
            selected = selected[selected["measurement_kind"] == "raw_counts"]
        selected = _features(selected)
        selected = selected[
            np.isfinite(selected["raw_count"])
            & np.isfinite(selected["sim_tb_k"])
            & selected[FEATURES].notna().all(axis=1)
        ].copy()
        version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        model_dir = workspace.root / "models" / "radiometric" / instrument / version
        model_dir.mkdir(parents=True, exist_ok=True)
        min_pairs = int(self.cfg.get("min_pairs_per_channel", 80))
        min_soundings = int(self.cfg.get("min_independent_soundings", 40))
        validation_fraction = float(self.cfg.get("validation_fraction", 0.2))
        max_rmse = float(self.cfg.get("max_validation_rmse_k", 4.0))
        max_bias = float(self.cfg.get("max_abs_validation_bias_k", 0.5))
        min_channels = int(self.cfg.get("min_production_channels", 1))
        epsilon = float(self.cfg.get("huber_epsilon", 1.35))
        models: dict[Any, Pipeline] = {}
        metrics: dict[str, Any] = {"version": version, "channels": {}}
        eligible_channels = 0
        any_failed = False

        for channel, group in selected.groupby("channel"):
            group = group.sort_values("datetime").copy()
            soundings = int(group["sounding_id"].nunique())
            if len(group) < min_pairs or soundings < min_soundings:
                metrics["channels"][str(channel)] = {
                    "status": "insufficient",
                    "n": int(len(group)),
                    "soundings": soundings,
                }
                continue
            ordered_soundings = (
                group.groupby("sounding_id")["datetime"].min().sort_values().index.tolist()
            )
            validation_count = max(1, int(round(len(ordered_soundings) * validation_fraction)))
            validation_ids = set(ordered_soundings[-validation_count:])
            train = group[~group["sounding_id"].isin(validation_ids)]
            validation = group[group["sounding_id"].isin(validation_ids)]
            if train.empty or validation.empty:
                metrics["channels"][str(channel)] = {
                    "status": "invalid_split",
                    "n": int(len(group)),
                    "soundings": soundings,
                }
                any_failed = True
                continue
            pipeline = self._pipeline()
            pipeline.named_steps["model"].set_params(epsilon=epsilon)
            weights = 1.0 / np.square(
                np.clip(pd.to_numeric(train["uncertainty_k"], errors="coerce").fillna(1.0), 0.05, None)
            )
            pipeline.fit(train[FEATURES], train["sim_tb_k"], model__sample_weight=weights)
            prediction = pipeline.predict(validation[FEATURES])
            residual = validation["sim_tb_k"].to_numpy(float) - prediction
            rmse = float(np.sqrt(mean_squared_error(validation["sim_tb_k"], prediction)))
            bias = float(np.mean(residual))
            baseline = np.full(len(validation), float(train["sim_tb_k"].median()))
            baseline_rmse = float(np.sqrt(mean_squared_error(validation["sim_tb_k"], baseline)))
            eligible = rmse <= max_rmse and abs(bias) <= max_bias
            if eligible:
                eligible_channels += 1
            else:
                any_failed = True
            metrics["channels"][str(channel)] = {
                "status": "eligible" if eligible else "rejected",
                "n": int(len(group)),
                "soundings": soundings,
                "train_n": int(len(train)),
                "validation_n": int(len(validation)),
                "validation_rmse_k": rmse,
                "validation_bias_k": bias,
                "baseline_rmse_k": baseline_rmse,
                "raw_count_min": float(group["raw_count"].min()),
                "raw_count_max": float(group["raw_count"].max()),
            }
            models[channel] = pipeline

        status = (
            "production"
            if eligible_channels >= min_channels and not any_failed and models
            else "candidate"
        )
        metrics.update(
            {
                "eligible_channels": eligible_channels,
                "fitted_channels": len(models),
                "min_production_channels": min_channels,
                "status": status,
            }
        )
        joblib.dump(models, model_dir / "count_to_tb.joblib")
        (model_dir / "metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (model_dir / "STATUS").write_text(status + "\n", encoding="utf-8")
        workspace.register_model("radiometric", instrument, version, status, model_dir, metrics)
        return RadiometricFitResult(version, status, metrics, model_dir)


def apply_count_model_to_granule(
    granule: SatelliteGranule,
    model_dir: str | Path,
    *,
    plausible_range_k: tuple[float, float] = (50.0, 400.0),
) -> int:
    if granule.raw_counts is None:
        return 0
    model_dir = Path(model_dir)
    models: dict[Any, Pipeline] = joblib.load(model_dir / "count_to_tb.joblib")
    output = np.asarray(granule.brightness_temperature_k, dtype=float).copy()
    applied = 0
    zenith = np.asarray(granule.satellite_zenith_deg, dtype=float)
    scan = np.asarray(granule.scan_position, dtype=float)
    secant = 1.0 / np.clip(np.cos(np.deg2rad(np.nan_to_num(zenith))), 0.2, None) - 1.0
    for index, channel in enumerate(granule.channels):
        key = _channel_key(models, channel)
        if key is None:
            continue
        raw = np.asarray(granule.raw_counts[:, index], dtype=float)
        frame = pd.DataFrame(
            {
                "raw_count": raw,
                "raw_count2": np.square(raw / 65535.0),
                "scan": scan,
                "scan2": np.square(scan),
                "secant_zenith_minus_1": secant,
            }
        )
        valid = np.isfinite(raw) & frame[FEATURES].notna().all(axis=1).to_numpy()
        if not valid.any():
            continue
        prediction = np.full(len(raw), np.nan, dtype=float)
        prediction[valid] = models[key].predict(frame.loc[valid, FEATURES])
        prediction[(prediction < plausible_range_k[0]) | (prediction > plausible_range_k[1])] = np.nan
        missing = ~np.isfinite(output[:, index])
        fill = missing & np.isfinite(prediction)
        output[fill, index] = prediction[fill]
        applied += int(fill.sum())
    if applied:
        granule.brightness_temperature_k = output
        granule.metadata["calibration_state"] = "vicarious_calibrated"
        granule.metadata["radiometric_model"] = model_dir.name
        granule.metadata["valid_brightness_temperature_fraction"] = float(np.isfinite(output).mean())
        if granule.quality_flag is not None:
            granule.quality_flag = np.asarray(granule.quality_flag, dtype=np.uint16) & np.uint16(~4 & 0xFFFF)
    return applied


def apply_count_model_to_workspace(
    workspace: Workspace,
    instrument: str,
    model_dir: str | Path,
) -> dict[str, int]:
    granules = 0
    values = 0
    for row in workspace.granule_rows(instrument, include_failed=True):
        granule = workspace.load_granule_row(row)
        applied = apply_count_model_to_granule(granule, model_dir)
        if not applied:
            continue
        qc = check_satellite(granule)
        workspace.store_granule(
            granule,
            {"passed": qc.passed, "flags": qc.flags, "metrics": qc.metrics},
        )
        granules += 1
        values += applied
    return {"granules": granules, "values": values}
