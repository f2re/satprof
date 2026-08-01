from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import json
import joblib
import numpy as np
import pandas as pd

from .physics import haversine_km, specific_humidity_to_rh
from .schemas import SatelliteGranule
from .storage import Workspace


class ProfileRetrievalError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message); self.code=code; self.details=details or {}


def _unwrap_longitudes(longitudes: np.ndarray) -> np.ndarray:
    return np.rad2deg(np.unwrap(np.deg2rad(np.asarray(longitudes,dtype=float))))


def granule_footprint(granule: SatelliteGranule, *, max_points: int = 5000) -> dict[str, Any] | None:
    mask=np.isfinite(granule.latitude)&np.isfinite(granule.longitude); lat=granule.latitude[mask]; lon=granule.longitude[mask]
    if len(lat)<3: return None
    if len(lat)>max_points:
        indices=np.linspace(0,len(lat)-1,max_points).round().astype(int); lat,lon=lat[indices],lon[indices]
    unwrapped=_unwrap_longitudes(lon); points=np.column_stack([unwrapped,lat])
    try:
        from scipy.spatial import ConvexHull
        ring=points[ConvexHull(points).vertices]
    except Exception:
        min_lon,max_lon=float(np.nanmin(unwrapped)),float(np.nanmax(unwrapped)); min_lat,max_lat=float(np.nanmin(lat)),float(np.nanmax(lat)); ring=np.asarray([[min_lon,min_lat],[max_lon,min_lat],[max_lon,max_lat],[min_lon,max_lat]])
    ring_lon=((ring[:,0]+180.0)%360.0)-180.0; coordinates=[[float(x),float(y)] for x,y in zip(ring_lon,ring[:,1])]
    if coordinates[0]!=coordinates[-1]: coordinates.append(coordinates[0])
    return {"type":"Polygon","coordinates":[coordinates]}


def nearest_fov(granule: SatelliteGranule, latitude: float, longitude: float) -> tuple[int,float]:
    distances=haversine_km(latitude,longitude,granule.latitude,granule.longitude)
    if not np.isfinite(distances).any(): raise ProfileRetrievalError("NO_VALID_GEOLOCATION","В грануле нет корректной геопривязки")
    index=int(np.nanargmin(distances)); return index,float(distances[index])


def _candidate_granules(workspace: Workspace, instrument: str | None, granule_db_id: int | None) -> list[Any]:
    if granule_db_id is not None:
        row=workspace.get_granule_row(granule_db_id)
        if row is None: raise ProfileRetrievalError("GRANULE_NOT_FOUND",f"Гранула {granule_db_id} не найдена")
        if instrument and row["instrument"]!=instrument: raise ProfileRetrievalError("INSTRUMENT_MISMATCH","Выбранная гранула относится к другому прибору")
        return [row]
    return list(workspace.all_granule_rows(instrument=instrument,include_failed=False,limit=100))


def _channel_value(granule: SatelliteGranule, fov: int, feature: str, values: np.ndarray) -> float | None:
    text=str(feature)
    for index,channel in enumerate(granule.channels):
        if text==str(channel) or text.lower() in {f"ch{str(channel).lower()}",f"channel_{str(channel).lower()}"}: return float(values[fov,index])
    return None


def _feature_frame(model: Any, granule: SatelliteGranule, fov: int, cfg: dict[str, Any]) -> tuple[pd.DataFrame,list[str]]:
    values=granule.calibrated_brightness_temperature_k; warnings=[]
    if values is None: values=granule.brightness_temperature_k; warnings.append("В грануле нет отдельного массива bias-corrected TB; модель получает исходный Level-1C продукт")
    defaults=cfg.get("retrieval",{}).get("feature_defaults",{}); row={}
    for feature in model.feature_columns:
        text=str(feature); value=_channel_value(granule,fov,text,values)
        if value is not None: row[text]=value
        elif text=="scan": row[text]=float(granule.scan_position[fov])
        elif text in {"zenith","satellite_zenith_deg"}: row[text]=float(granule.satellite_zenith_deg[fov])
        elif text in {"tpw","tpw_mm"}: row[text]=float(granule.metadata.get("tpw_mm",defaults.get(text,defaults.get("tpw_mm",25.0)))); warnings.append("TPW для точки отсутствовал и заменён настроечным значением")
        elif text in defaults: row[text]=float(defaults[text])
        else: row[text]=0.0; warnings.append(f"Признак {text} отсутствовал и заменён нулём")
    return pd.DataFrame([row],columns=[str(value) for value in model.feature_columns]),sorted(set(warnings))


def retrieve_profile(workspace: Workspace, cfg: dict[str, Any], *, latitude: float, longitude: float, instrument: str | None = None, granule_db_id: int | None = None, max_distance_km: float | None = None) -> dict[str, Any]:
    candidates=_candidate_granules(workspace,instrument,granule_db_id)
    if not candidates: raise ProfileRetrievalError("NO_GRANULES","Нет пригодных спутниковых гранул")
    selected=None
    for row in candidates:
        granule=workspace.load_granule_row(row); index,distance=nearest_fov(granule,latitude,longitude)
        if selected is None or distance<selected[3]: selected=(row,granule,index,distance)
    row,granule,fov,distance=selected; limit=float(max_distance_km or cfg.get("web",{}).get("profile_max_distance_km",150.0))
    if distance>limit: raise ProfileRetrievalError("POINT_OUTSIDE_SWATH",f"Ближайшее поле зрения находится в {distance:.1f} км, допустимо {limit:.1f} км",details={"distance_km":distance,"max_distance_km":limit,"granule_id":int(row["id"])})
    model_row=workspace.latest_model("retrieval",granule.instrument,prefer_production=True)
    if model_row is None: raise ProfileRetrievalError("MODEL_NOT_AVAILABLE",f"Для прибора {granule.instrument} ещё не обучена модель восстановления",details={"instrument":granule.instrument})
    model_path=workspace.root/model_row["path"]/"retriever.joblib"
    if not model_path.exists(): raise ProfileRetrievalError("MODEL_FILE_MISSING",f"Файл модели отсутствует: {model_path}")
    model=joblib.load(model_path); features,warnings=_feature_frame(model,granule,fov,cfg); temperature,logq=model.predict(features); pressure=np.asarray(model.pressure_grid_hpa,dtype=float); temperature=np.asarray(temperature[0],dtype=float); specific_humidity=np.exp(np.asarray(logq[0],dtype=float)); rh=specific_humidity_to_rh(pressure,temperature,specific_humidity); tb_source=granule.calibrated_brightness_temperature_k if granule.calibrated_brightness_temperature_k is not None else granule.brightness_temperature_k; observed_channels=[{"channel":str(channel),"brightness_temperature_k":float(tb_source[fov,index])} for index,channel in enumerate(granule.channels)]; metrics=json.loads(model_row["metrics_json"] or "{}")
    if model_row["status"]!="production": warnings.append("Используется кандидат модели, не прошедший перевод в production")
    observation_time=datetime.fromtimestamp(float(granule.observation_time_epoch_s[fov]),tz=timezone.utc)
    return {"query":{"latitude":float(latitude),"longitude":float(longitude)},"observation":{"granule_db_id":int(row["id"]),"granule_id":granule.granule_id,"instrument":granule.instrument,"satellite":granule.satellite,"fov_index":fov,"latitude":float(granule.latitude[fov]),"longitude":float(granule.longitude[fov]),"distance_km":distance,"time":observation_time.isoformat(),"scan_position":float(granule.scan_position[fov]),"satellite_zenith_deg":float(granule.satellite_zenith_deg[fov]),"calibration_state":granule.metadata.get("calibration_state","unknown"),"channels":observed_channels},"model":{"instrument":granule.instrument,"version":model_row["version"],"status":model_row["status"],"temperature_validation_rmse_k":metrics.get("temperature_rmse_k"),"logq_validation_rmse":metrics.get("logq_rmse")},"profile":[{"pressure_hpa":float(p),"temperature_k":float(t),"temperature_c":float(t-273.15),"specific_humidity_gkg":float(q*1000.0),"relative_humidity_pct":float(r)} for p,t,q,r in zip(pressure,temperature,specific_humidity,rh)],"warnings":sorted(set(warnings))}
