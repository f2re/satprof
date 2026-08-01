from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import zipfile
import numpy as np
import requests
from ..schemas import SoundingProfile
from ..physics import wind_components, dewpoint_to_rh, integrate_balloon_trajectory

MISSING={-9999,-8888}


def _ival(text: str, scale: float=1.0) -> float:
    text=text.strip()
    if not text: return np.nan
    try: value=int(text)
    except ValueError: return np.nan
    return np.nan if value in MISSING else value/scale


def _release_datetime(year,month,day,nominal_hour,release: str) -> datetime:
    release=release.strip()
    if release and release not in ("9999","-999") and release.lstrip('-').isdigit():
        release=release.zfill(4); hh,mm=int(release[:2]),int(release[2:])
        if 0<=hh<=23 and 0<=mm<=59: return datetime(year,month,day,hh,mm,tzinfo=timezone.utc)
    return datetime(year,month,day,nominal_hour if 0<=nominal_hour<=23 else 0,tzinfo=timezone.utc)


def parse_igra_lines(lines) -> list[SoundingProfile]:
    profiles=[]; current_header=None; current_levels=[]
    def flush():
        nonlocal current_header,current_levels
        if not current_header or not current_levels: current_header,current_levels=None,[]; return
        a={k:np.asarray([row[k] for row in current_levels],dtype=float) for k in current_levels[0]}; valid=np.isfinite(a['pressure_hpa'])&np.isfinite(a['temperature_k'])
        if valid.sum()<2: current_header,current_levels=None,[]; return
        for k in a: a[k]=a[k][valid]
        order=np.argsort(-a['pressure_hpa'])
        for k in a: a[k]=a[k][order]
        lat,lon=integrate_balloon_trajectory(current_header['lat'],current_header['lon'],a['elapsed_s'],a['u'],a['v']); profiles.append(SoundingProfile(source='igra2',station_id=current_header['station_id'],launch_time=current_header['launch_time'],station_lat=current_header['lat'],station_lon=current_header['lon'],pressure_hpa=a['pressure_hpa'],temperature_k=a['temperature_k'],relative_humidity_pct=a['rh'],altitude_m=a['altitude_m'],wind_u_ms=a['u'],wind_v_ms=a['v'],elapsed_s=a['elapsed_s'],latitude=lat,longitude=lon,metadata={'pressure_source':current_header['pressure_source'],'non_pressure_source':current_header['non_pressure_source'],'trajectory':'wind_integrated'})); current_header,current_levels=None,[]
    for raw in lines:
        line=raw.decode('ascii',errors='replace') if isinstance(raw,bytes) else raw; line=line.rstrip('\r\n')
        if not line: continue
        if line.startswith('#'):
            flush()
            try:
                station_id=line[1:12].strip(); year=int(line[13:17]); month=int(line[18:20]); day=int(line[21:23]); hour=int(line[24:26]); release=line[27:31]; current_header={'station_id':station_id,'launch_time':_release_datetime(year,month,day,hour,release),'lat':_ival(line[55:62],10000.0),'lon':_ival(line[63:71],10000.0),'pressure_source':line[37:45].strip(),'non_pressure_source':line[46:54].strip()}
            except Exception: current_header=None
            continue
        if current_header is None: continue
        pressure=_ival(line[9:15],100.0); altitude=_ival(line[16:21],1.0); temp_c=_ival(line[22:27],10.0); rh=_ival(line[28:33],10.0); dpdp=_ival(line[34:39],10.0); wdir=_ival(line[40:45],1.0); wspd=_ival(line[46:51],10.0); etime_min=_ival(line[3:8],1.0); temp_k=temp_c+273.15 if np.isfinite(temp_c) else np.nan
        if not np.isfinite(rh) and np.isfinite(temp_k) and np.isfinite(dpdp): rh=float(dewpoint_to_rh(temp_k,temp_k-dpdp))
        u,v=wind_components(wdir,wspd); current_levels.append({'pressure_hpa':pressure,'altitude_m':altitude,'temperature_k':temp_k,'rh':rh,'u':float(u) if np.isfinite(u) else np.nan,'v':float(v) if np.isfinite(v) else np.nan,'elapsed_s':etime_min*60.0 if np.isfinite(etime_min) else np.nan})
    flush(); return profiles


def read_igra_file(path: str | Path) -> list[SoundingProfile]:
    path=Path(path)
    if path.suffix.lower()=='.zip':
        with zipfile.ZipFile(path) as z:
            names=[n for n in z.namelist() if not n.endswith('/')]
            if not names: return []
            with z.open(names[0]) as f: return parse_igra_lines(f)
    with path.open('rt',encoding='ascii',errors='replace') as f: return parse_igra_lines(f)


def download_recent_station(station_id: str, target_dir: str | Path, base_url: str, year: int | None=None) -> Path:
    year=year or datetime.now(timezone.utc).year; filename=f"{station_id}-data-beg{year}.txt.zip"; url=f"{base_url.rstrip('/')}/{filename}"; target=Path(target_dir)/filename; target.parent.mkdir(parents=True,exist_ok=True)
    with requests.get(url,timeout=60,stream=True) as response:
        response.raise_for_status()
        with target.open('wb') as f:
            for chunk in response.iter_content(1024*1024):
                if chunk: f.write(chunk)
    return target
