from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import re
import numpy as np
import requests
from ..schemas import SoundingProfile
from ..physics import wind_components, dewpoint_to_rh, integrate_balloon_trajectory


def list_recent_files(index_url: str) -> list[str]:
    response=requests.get(index_url,timeout=30); response.raise_for_status(); return sorted(set(re.findall(r'href="([^"]*temp_bufr[^"]*)"',response.text)))


def download_recent(index_url: str, target_dir: str | Path, limit: int = 12) -> list[Path]:
    names=list_recent_files(index_url)[-limit:]; out=[]; target_dir=Path(target_dir); target_dir.mkdir(parents=True,exist_ok=True)
    for name in names:
        target=target_dir/Path(name).name
        if target.exists(): out.append(target); continue
        with requests.get(index_url.rstrip('/')+'/'+name,timeout=60,stream=True) as r:
            r.raise_for_status()
            with target.open('wb') as f:
                for chunk in r.iter_content(1024*1024):
                    if chunk: f.write(chunk)
        out.append(target)
    return out


def _get_first(codes,gid,names,default=np.nan):
    for name in names:
        try:
            value=codes.codes_get(gid,name)
            if value is not None: return value
        except Exception: pass
    return default


def _get_array(codes,gid,names):
    for name in names:
        try:
            arr=np.asarray(codes.codes_get_array(gid,name),dtype=float)
            if arr.size: return arr
        except Exception: pass
    return np.array([],dtype=float)


def read_bufr(path: str | Path) -> list[SoundingProfile]:
    try: import eccodes as codes
    except ImportError as exc: raise RuntimeError("Для TEMP BUFR установите ecCodes и пакет Python eccodes") from exc
    profiles=[]
    with Path(path).open('rb') as f:
        while True:
            gid=codes.codes_bufr_new_from_file(f)
            if gid is None: break
            try:
                codes.codes_set(gid,'unpack',1); block=int(_get_first(codes,gid,['blockNumber'],0)); station=int(_get_first(codes,gid,['stationNumber'],0)); wsi_series=_get_first(codes,gid,['wigosIdentifierSeries'],np.nan); wsi_issuer=_get_first(codes,gid,['wigosIssuerOfIdentifier'],np.nan); wsi_issue=_get_first(codes,gid,['wigosIssueNumber'],np.nan); wsi_local=_get_first(codes,gid,['wigosLocalIdentifierCharacter','wigosLocalIdentifier'],'')
                station_id=f"{int(wsi_series)}-{int(wsi_issuer)}-{int(wsi_issue)}-{str(wsi_local).strip()}" if np.isfinite(float(wsi_series)) and np.isfinite(float(wsi_issuer)) and np.isfinite(float(wsi_issue)) and str(wsi_local).strip() else f"{block:02d}{station:03d}"
                year=int(_get_first(codes,gid,['year'],1970)); month=int(_get_first(codes,gid,['month'],1)); day=int(_get_first(codes,gid,['day'],1)); hour=int(_get_first(codes,gid,['hour'],0)); minute=int(_get_first(codes,gid,['minute'],0)); launch=datetime(year,month,day,hour,minute,tzinfo=timezone.utc); station_lat=float(_get_first(codes,gid,['latitude'],np.nan)); station_lon=float(_get_first(codes,gid,['longitude'],np.nan)); p=_get_array(codes,gid,['pressure','nonCoordinatePressure'])/100.0; t=_get_array(codes,gid,['airTemperature']); td=_get_array(codes,gid,['dewpointTemperature']); rh=_get_array(codes,gid,['relativeHumidity']); z=_get_array(codes,gid,['nonCoordinateGeopotentialHeight','geopotentialHeight']); wdir=_get_array(codes,gid,['windDirection']); wspd=_get_array(codes,gid,['windSpeed']); elapsed=_get_array(codes,gid,['timePeriod']); lat_disp=_get_array(codes,gid,['latitudeDisplacement']); lon_disp=_get_array(codes,gid,['longitudeDisplacement']); arrays=[a for a in (p,t,td,rh,z,wdir,wspd,elapsed) if a.size]
                if not arrays: continue
                n=max(map(len,arrays))
                def fit(a,fill=np.nan):
                    if a.size==n: return a
                    out=np.full(n,fill); out[:min(n,a.size)]=a[:n]; return out
                p,t,td,rh,z,wdir,wspd,elapsed=map(fit,(p,t,td,rh,z,wdir,wspd,elapsed)); finite_elapsed=elapsed[np.isfinite(elapsed)]; elapsed_units='unknown'
                if finite_elapsed.size:
                    if np.nanmax(np.abs(finite_elapsed))<=600: elapsed=elapsed*60.0; elapsed_units='minutes_to_seconds'
                    else: elapsed_units='seconds'
                if np.nanmax(rh)<=1.5: rh=rh*100
                missing_rh=~np.isfinite(rh)&np.isfinite(t)&np.isfinite(td); rh[missing_rh]=dewpoint_to_rh(t[missing_rh],td[missing_rh]); u,v=wind_components(wdir,wspd)
                if lat_disp.size==n and lon_disp.size==n: lat=station_lat+lat_disp; lon=station_lon+lon_disp; trajectory='bufr_displacement'
                else: lat,lon=integrate_balloon_trajectory(station_lat,station_lon,elapsed,u,v); trajectory='wind_integrated'
                valid=np.isfinite(p)&np.isfinite(t)
                if valid.sum()<2: continue
                profiles.append(SoundingProfile(source='dwd_bufr',station_id=station_id,launch_time=launch,station_lat=station_lat,station_lon=station_lon,pressure_hpa=p[valid],temperature_k=t[valid],relative_humidity_pct=rh[valid],altitude_m=z[valid],wind_u_ms=u[valid],wind_v_ms=v[valid],elapsed_s=elapsed[valid],latitude=lat[valid],longitude=lon[valid],metadata={'trajectory':trajectory,'bufr_file':Path(path).name,'elapsed_normalization':elapsed_units}))
            finally: codes.codes_release(gid)
    return profiles
