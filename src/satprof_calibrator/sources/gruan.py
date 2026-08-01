from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import xarray as xr
from ..schemas import SoundingProfile
from ..physics import wind_components, dewpoint_to_rh, integrate_balloon_trajectory


def _find(ds,names,required=True):
    for name in names:
        if name in ds: return ds[name]
    if required: raise KeyError(f"Не найдена переменная: {names}")
    return None


def _to_k(da):
    x=np.asarray(da.values,dtype=float).squeeze(); units=str(da.attrs.get('units','')).lower()
    if ('c' in units and 'k' not in units) or np.nanmedian(x)<150: x=x+273.15
    return x


def _to_hpa(da):
    x=np.asarray(da.values,dtype=float).squeeze(); units=str(da.attrs.get('units','')).lower()
    if ('pa' in units and 'hpa' not in units) or np.nanmedian(x)>2000: x=x/100.0
    return x


def read_gruan_netcdf(path: str | Path) -> SoundingProfile:
    ds=xr.open_dataset(path)
    try:
        p_da=_find(ds,['press','pressure','p']); t_da=_find(ds,['temp','temperature','ta']); rh_da=_find(ds,['rh','relative_humidity','hur'],required=False); td_da=_find(ds,['dewpoint','dew_point_temperature'],required=False); z_da=_find(ds,['alt','altitude','geopotential_height','height']); time_da=_find(ds,['time','flight_time','elapsed_time']); lat_da=_find(ds,['lat','latitude'],required=False); lon_da=_find(ds,['lon','longitude'],required=False); ws_da=_find(ds,['wspd','wind_speed'],required=False); wd_da=_find(ds,['wdir','wind_direction'],required=False); u_da=_find(ds,['u_wind','u'],required=False); v_da=_find(ds,['v_wind','v'],required=False); p=_to_hpa(p_da); t=_to_k(t_da); z=np.asarray(z_da.values,dtype=float).squeeze()
        if rh_da is not None:
            rh=np.asarray(rh_da.values,dtype=float).squeeze()
            if np.nanmax(rh)<=1.5: rh*=100
        elif td_da is not None: rh=dewpoint_to_rh(t,_to_k(td_da))
        else: rh=np.full_like(t,np.nan)
        time_values=np.asarray(time_da.values).squeeze()
        if np.issubdtype(time_values.dtype,np.datetime64): epoch=time_values.astype('datetime64[s]').astype(np.int64); launch_epoch=int(np.nanmin(epoch)); elapsed=epoch-launch_epoch; launch=datetime.fromtimestamp(launch_epoch,tz=timezone.utc)
        else:
            elapsed=np.asarray(time_values,dtype=float); units=str(time_da.attrs.get('units','')).lower()
            if 'min' in units: elapsed*=60
            launch_text=ds.attrs.get('launch_time') or ds.attrs.get('datetime'); launch=datetime.fromisoformat(str(launch_text).replace('Z','+00:00')) if launch_text else datetime.fromtimestamp(Path(path).stat().st_mtime,tz=timezone.utc)
        if u_da is not None and v_da is not None: u=np.asarray(u_da.values,dtype=float).squeeze(); v=np.asarray(v_da.values,dtype=float).squeeze()
        elif ws_da is not None and wd_da is not None: u,v=wind_components(np.asarray(wd_da.values).squeeze(),np.asarray(ws_da.values).squeeze())
        else: u=np.full_like(t,np.nan); v=np.full_like(t,np.nan)
        station_lat=float(ds.attrs.get('latitude',np.asarray(lat_da.values).flat[0] if lat_da is not None else np.nan)); station_lon=float(ds.attrs.get('longitude',np.asarray(lon_da.values).flat[0] if lon_da is not None else np.nan))
        if lat_da is not None and np.asarray(lat_da).size==p.size: lat=np.asarray(lat_da.values,dtype=float).squeeze(); lon=np.asarray(lon_da.values,dtype=float).squeeze(); trajectory='measured'
        else: lat,lon=integrate_balloon_trajectory(station_lat,station_lon,elapsed,u,v); trajectory='wind_integrated'
        tu_da=_find(ds,['temp_uncertainty','temperature_uncertainty','u_temperature'],required=False); hu_da=_find(ds,['rh_uncertainty','relative_humidity_uncertainty','u_relative_humidity'],required=False); station_id=str(ds.attrs.get('site_code') or ds.attrs.get('station_id') or Path(path).stem); valid=np.isfinite(p)&np.isfinite(t)
        return SoundingProfile(source='gruan',station_id=station_id,launch_time=launch,station_lat=station_lat,station_lon=station_lon,pressure_hpa=p[valid],temperature_k=t[valid],relative_humidity_pct=rh[valid],altitude_m=z[valid],wind_u_ms=u[valid],wind_v_ms=v[valid],elapsed_s=elapsed[valid],latitude=lat[valid],longitude=lon[valid],temperature_uncertainty_k=None if tu_da is None else np.asarray(tu_da.values,dtype=float).squeeze()[valid],humidity_uncertainty_pct=None if hu_da is None else np.asarray(hu_da.values,dtype=float).squeeze()[valid],metadata={'trajectory':trajectory,'gruan_product':ds.attrs.get('data_product_version','')})
    finally: ds.close()
