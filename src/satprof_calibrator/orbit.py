from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import math
import requests

from .storage import Workspace


def parse_tle_text(text: str, fallback_name: str) -> tuple[str, str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    line1_index = next((i for i, line in enumerate(lines) if line.startswith("1 ")), None)
    if line1_index is None or line1_index + 1 >= len(lines) or not lines[line1_index + 1].startswith("2 "):
        raise ValueError("Ответ не содержит корректную пару строк TLE")
    name = lines[line1_index - 1] if line1_index > 0 and not lines[line1_index - 1].startswith(("1 ", "2 ")) else fallback_name
    return name, lines[line1_index], lines[line1_index + 1]


def sync_tles(workspace: Workspace, cfg: dict[str, Any]) -> dict[str, Any]:
    tle_cfg = cfg.get("tle", {})
    template = tle_cfg.get("url_template", "https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=TLE")
    timeout = float(tle_cfg.get("timeout_seconds", 30)); results = []
    for satellite in cfg.get("satellites", []):
        norad_id = int(satellite["norad_id"]); url = template.format(norad_id=norad_id)
        try:
            response = requests.get(url, timeout=timeout, headers={"User-Agent":"SatProf/0.3"}); response.raise_for_status()
            name, line1, line2 = parse_tle_text(response.text, satellite.get("name", str(norad_id)))
            workspace.store_tle(norad_id,satellite.get("name", name),line1,line2,source=url,epoch_text=line1[18:32].strip() if len(line1) >= 32 else None,metadata={"provider_name":name,"family":satellite.get("family"),"color":satellite.get("color")})
            results.append({"norad_id":norad_id,"status":"ok","name":name})
        except Exception as exc:
            workspace.emit_event("tle.sync", f"Не удалось обновить TLE {satellite.get('name', norad_id)}", severity="warning", details={"error":str(exc),"url":url}); results.append({"norad_id":norad_id,"status":"error","error":str(exc)})
    return {"satellites":results,"updated":sum(item["status"] == "ok" for item in results)}


def _gmst_radians(jd: float) -> float:
    t = (jd - 2451545.0) / 36525.0
    return math.radians((280.46061837 + 360.98564736629*(jd-2451545.0) + 0.000387933*t*t - t*t*t/38710000.0) % 360.0)


def _ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    a=6378.137; f=1/298.257223563; e2=f*(2-f); lon=math.atan2(y,x); p=math.hypot(x,y); lat=math.atan2(z,p*(1-e2)); height=0.0
    for _ in range(8):
        sin_lat=math.sin(lat); n=a/math.sqrt(1-e2*sin_lat*sin_lat); height=p/max(math.cos(lat),1e-12)-n; lat_new=math.atan2(z,p*(1-e2*n/max(n+height,1e-12)))
        if abs(lat_new-lat)<1e-12: lat=lat_new; break
        lat=lat_new
    return math.degrees(lat), ((math.degrees(lon)+180)%360)-180, height


def propagate_tle(line1: str, line2: str, when: datetime) -> tuple[float, float, float]:
    try: from sgp4.api import Satrec, jday
    except ImportError as exc: raise RuntimeError("Для расчёта положения установите satprof[orbit] или пакет sgp4") from exc
    when=when.astimezone(timezone.utc); sat=Satrec.twoline2rv(line1,line2); jd,fr=jday(when.year,when.month,when.day,when.hour,when.minute,when.second+when.microsecond/1e6); error,position,_=sat.sgp4(jd,fr)
    if error != 0: raise RuntimeError(f"SGP4 вернул код ошибки {error}")
    theta=_gmst_radians(jd+fr); cos_t,sin_t=math.cos(theta),math.sin(theta); x_eci,y_eci,z_eci=position
    return _ecef_to_geodetic(cos_t*x_eci+sin_t*y_eci,-sin_t*x_eci+cos_t*y_eci,z_eci)


def _split_dateline(points: list[list[float]]) -> list[list[list[float]]]:
    if not points: return []
    segments=[[points[0]]]
    for point in points[1:]:
        if abs(point[0]-segments[-1][-1][0]) > 180: segments.append([point])
        else: segments[-1].append(point)
    return [segment for segment in segments if len(segment) >= 2]


def satellite_geojson(workspace: Workspace, cfg: dict[str, Any], when: datetime | None = None) -> dict[str, Any]:
    when=(when or datetime.now(timezone.utc)).astimezone(timezone.utc); tle_rows={int(row["norad_id"]):row for row in workspace.tle_rows()}; track_minutes=int(cfg.get("tle",{}).get("track_minutes",100)); step_seconds=int(cfg.get("tle",{}).get("track_step_seconds",120)); features=[]
    try: import sgp4; sgp4_available=True
    except ImportError: sgp4_available=False
    for configured in cfg.get("satellites", []):
        norad_id=int(configured["norad_id"]); row=tle_rows.get(norad_id); properties={"name":configured.get("name",str(norad_id)),"norad_id":norad_id,"family":configured.get("family","unknown"),"color":configured.get("color","#365f91"),"tle_available":row is not None,"sgp4_available":sgp4_available}
        if row is None or not sgp4_available: features.append({"type":"Feature","geometry":None,"properties":properties}); continue
        try:
            lat,lon,alt=propagate_tle(row["line1"],row["line2"],when); properties.update({"latitude":lat,"longitude":lon,"altitude_km":alt,"fetched_at":row["fetched_at"]}); features.append({"type":"Feature","geometry":{"type":"Point","coordinates":[lon,lat]},"properties":{**properties,"feature_type":"satellite"}})
            track=[]; current=when-timedelta(minutes=track_minutes/2); end=when+timedelta(minutes=track_minutes/2)
            while current <= end:
                tlat,tlon,_=propagate_tle(row["line1"],row["line2"],current); track.append([tlon,tlat]); current += timedelta(seconds=step_seconds)
            segments=_split_dateline(track); geometry={"type":"LineString","coordinates":segments[0]} if len(segments)==1 else {"type":"MultiLineString","coordinates":segments}; features.append({"type":"Feature","geometry":geometry,"properties":{**properties,"feature_type":"track"}})
        except Exception as exc: properties["error"]=str(exc); features.append({"type":"Feature","geometry":None,"properties":properties})
    return {"type":"FeatureCollection","generated_at":when.isoformat(),"features":features}
