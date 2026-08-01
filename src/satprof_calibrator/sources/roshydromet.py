from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from ..schemas import SoundingProfile
from ..physics import wind_components, dewpoint_to_rh, integrate_balloon_trajectory

DEFAULT_COLUMNS={'station_id':'station','year':'year','month':'month','day':'day','hour':'term','pressure_hpa':'pressure','altitude_m':'height','temperature_c':'temperature','dewpoint_depression_c':'dewpoint_deficit','wind_direction_deg':'wind_direction','wind_speed_ms':'wind_speed','station_lat':'latitude','station_lon':'longitude'}


def read_csv(path: str | Path, columns: dict | None=None, sep: str | None=None) -> list[SoundingProfile]:
    c={**DEFAULT_COLUMNS,**(columns or {})}; df=pd.read_csv(path,sep=sep or None,engine='python'); profiles=[]; group_cols=[c[k] for k in ('station_id','year','month','day','hour')]
    for key,g in df.groupby(group_cols,dropna=False):
        station,year,month,day,hour=key; launch=datetime(int(year),int(month),int(day),int(hour),tzinfo=timezone.utc); p=pd.to_numeric(g[c['pressure_hpa']],errors='coerce').to_numpy(float); z=pd.to_numeric(g[c['altitude_m']],errors='coerce').to_numpy(float); t=pd.to_numeric(g[c['temperature_c']],errors='coerce').to_numpy(float)+273.15; dpd=pd.to_numeric(g[c['dewpoint_depression_c']],errors='coerce').to_numpy(float); rh=dewpoint_to_rh(t,t-dpd); wd=pd.to_numeric(g[c['wind_direction_deg']],errors='coerce').to_numpy(float); ws=pd.to_numeric(g[c['wind_speed_ms']],errors='coerce').to_numpy(float); u,v=wind_components(wd,ws); lat0=float(g[c['station_lat']].iloc[0]) if c['station_lat'] in g else np.nan; lon0=float(g[c['station_lon']].iloc[0]) if c['station_lon'] in g else np.nan; elapsed=np.maximum(z-np.nanmin(z),0)/5.0; lat,lon=integrate_balloon_trajectory(lat0,lon0,elapsed,u,v); valid=np.isfinite(p)&np.isfinite(t); profiles.append(SoundingProfile(source='roshydromet_open',station_id=str(station),launch_time=launch,station_lat=lat0,station_lon=lon0,pressure_hpa=p[valid],temperature_k=t[valid],relative_humidity_pct=rh[valid],altitude_m=z[valid],wind_u_ms=u[valid],wind_v_ms=v[valid],elapsed_s=elapsed[valid],latitude=lat[valid],longitude=lon[valid],metadata={'trajectory':'estimated_from_ascent_rate','warning':'В исходном CSV нет времени и координат по уровням'}))
    return profiles
