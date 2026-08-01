from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .schemas import SoundingProfile, SatelliteGranule


@dataclass
class QCResult:
    passed: bool
    flags: list[str]
    metrics: dict[str, float]


def check_sounding(profile: SoundingProfile, cfg: dict | None = None) -> QCResult:
    cfg=cfg or {}; flags=[]; n=len(profile.pressure_hpa); min_levels=int(cfg.get("min_levels",12))
    if n<min_levels: flags.append("too_few_levels")
    valid=np.isfinite(profile.pressure_hpa)&np.isfinite(profile.temperature_k); missing_fraction=1-valid.mean() if n else 1.0
    if missing_fraction>float(cfg.get("max_missing_fraction",0.25)): flags.append("too_many_missing")
    trange=cfg.get("temperature_range_k",[170,330])
    if np.any((profile.temperature_k[valid]<trange[0])|(profile.temperature_k[valid]>trange[1])): flags.append("temperature_out_of_range")
    rh=profile.relative_humidity_pct; rh_valid=np.isfinite(rh); rhrange=cfg.get("relative_humidity_range_pct",[0,105])
    if np.any((rh[rh_valid]<rhrange[0])|(rh[rh_valid]>rhrange[1])): flags.append("humidity_out_of_range")
    p=profile.pressure_hpa[np.isfinite(profile.pressure_hpa)]; inversion_fraction=0.0
    if len(p)>1:
        inversion_fraction=float(np.mean(np.diff(p)>0))
        if inversion_fraction>float(cfg.get("max_pressure_inversion_fraction",0.05)): flags.append("pressure_not_monotonic")
    metrics={"n_levels":float(n),"missing_fraction":float(missing_fraction),"min_pressure_hpa":float(np.nanmin(profile.pressure_hpa)) if n else np.nan,"max_altitude_m":float(np.nanmax(profile.altitude_m)) if n else np.nan,"pressure_inversion_fraction":inversion_fraction}
    return QCResult(not flags,flags,metrics)


def check_satellite(granule: SatelliteGranule) -> QCResult:
    flags=[]; tb=granule.brightness_temperature_k; finite=np.isfinite(tb)
    if finite.mean()<0.5: flags.append("too_many_missing_tb")
    if np.any((tb[finite]<50)|(tb[finite]>400)): flags.append("tb_out_of_range")
    if np.any((granule.latitude<-90)|(granule.latitude>90)): flags.append("latitude_out_of_range")
    if np.any((granule.longitude<-180)|(granule.longitude>360)): flags.append("longitude_out_of_range")
    return QCResult(not flags,flags,{"finite_tb_fraction":float(finite.mean())})
