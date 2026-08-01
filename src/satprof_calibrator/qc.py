from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .schemas import SoundingProfile, SatelliteGranule


@dataclass
class QCResult:
    passed: bool
    flags: list[str]
    metrics: dict[str, float | str | bool]


def check_sounding(profile: SoundingProfile, cfg: dict | None = None) -> QCResult:
    cfg = cfg or {}
    flags: list[str] = []
    n = len(profile.pressure_hpa)
    min_levels = int(cfg.get("min_levels", 12))
    if n < min_levels:
        flags.append("too_few_levels")
    valid = np.isfinite(profile.pressure_hpa) & np.isfinite(profile.temperature_k)
    missing_fraction = 1 - valid.mean() if n else 1.0
    if missing_fraction > float(cfg.get("max_missing_fraction", 0.25)):
        flags.append("too_many_missing")
    trange = cfg.get("temperature_range_k", [170, 330])
    if np.any((profile.temperature_k[valid] < trange[0]) | (profile.temperature_k[valid] > trange[1])):
        flags.append("temperature_out_of_range")
    rh = profile.relative_humidity_pct
    rh_valid = np.isfinite(rh)
    rhrange = cfg.get("relative_humidity_range_pct", [0, 105])
    if np.any((rh[rh_valid] < rhrange[0]) | (rh[rh_valid] > rhrange[1])):
        flags.append("humidity_out_of_range")
    pressure = profile.pressure_hpa[np.isfinite(profile.pressure_hpa)]
    inversion_fraction = 0.0
    if len(pressure) > 1:
        inversion_fraction = float(np.mean(np.diff(pressure) > 0))
        if inversion_fraction > float(cfg.get("max_pressure_inversion_fraction", 0.05)):
            flags.append("pressure_not_monotonic")
    metrics = {
        "n_levels": float(n),
        "missing_fraction": float(missing_fraction),
        "min_pressure_hpa": float(np.nanmin(profile.pressure_hpa)) if n else np.nan,
        "max_altitude_m": float(np.nanmax(profile.altitude_m)) if n else np.nan,
        "pressure_inversion_fraction": inversion_fraction,
    }
    return QCResult(not flags, flags, metrics)


def check_satellite(granule: SatelliteGranule) -> QCResult:
    flags: list[str] = []
    tb = np.asarray(granule.brightness_temperature_k, dtype=float)
    finite_tb = np.isfinite(tb)
    tb_fraction = float(finite_tb.mean()) if tb.size else 0.0
    raw_fraction = 0.0
    if granule.raw_counts is not None:
        raw = np.asarray(granule.raw_counts)
        raw_fraction = float(np.isfinite(raw.astype(float)).mean()) if raw.size else 0.0
    calibration_pending = tb_fraction < 0.5 and raw_fraction >= 0.5
    if tb_fraction < 0.5 and not calibration_pending:
        flags.append("too_many_missing_measurements")
    if finite_tb.any() and np.any((tb[finite_tb] < 50) | (tb[finite_tb] > 400)):
        flags.append("tb_out_of_range")

    latitude = np.asarray(granule.latitude, dtype=float)
    longitude = np.asarray(granule.longitude, dtype=float)
    geo_valid = np.isfinite(latitude) & np.isfinite(longitude)
    geo_fraction = float(geo_valid.mean()) if latitude.size else 0.0
    if geo_fraction < 0.5:
        flags.append("too_many_missing_geolocation")
    elif np.any((latitude[geo_valid] < -90) | (latitude[geo_valid] > 90)):
        flags.append("latitude_out_of_range")
    if geo_valid.any() and np.any((longitude[geo_valid] < -180) | (longitude[geo_valid] > 180)):
        flags.append("longitude_out_of_range")

    times = np.asarray(granule.observation_time_epoch_s, dtype=float)
    time_fraction = float(np.mean(np.isfinite(times) & (times > 0))) if times.size else 0.0
    if time_fraction < 0.5:
        flags.append("too_many_missing_time")
    metrics: dict[str, float | str | bool] = {
        "finite_tb_fraction": tb_fraction,
        "finite_raw_count_fraction": raw_fraction,
        "finite_geolocation_fraction": geo_fraction,
        "finite_time_fraction": time_fraction,
        "calibration_pending": calibration_pending,
        "calibration_state": str(granule.metadata.get("calibration_state", "unknown")),
    }
    return QCResult(not flags, flags, metrics)
