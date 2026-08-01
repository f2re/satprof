from __future__ import annotations
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from .storage import Workspace
from .physics import rh_to_specific_humidity, total_precipitable_water_mm


def _sonde_radiance_uncertainty(profile, sim, channel_index: int, cfg: dict) -> float:
    """Линеаризованное распространение неопределённости зонда в пространство TB."""
    fallback = float(cfg.get('default_sonde_radiance_uncertainty_k', 0.35))
    if sim.jacobian_temperature is None and sim.jacobian_logq is None:
        return fallback
    n = len(profile.pressure_hpa)
    if profile.temperature_uncertainty_k is None:
        sigma_t = np.full(n, float(cfg.get('default_temperature_uncertainty_k', 0.5)))
    else:
        sigma_t = np.nan_to_num(np.asarray(profile.temperature_uncertainty_k, float), nan=float(cfg.get('default_temperature_uncertainty_k', 0.5)))
    if profile.humidity_uncertainty_pct is None:
        sigma_rh = np.full(n, float(cfg.get('default_humidity_uncertainty_pct', 7.0)))
    else:
        sigma_rh = np.nan_to_num(np.asarray(profile.humidity_uncertainty_pct, float), nan=float(cfg.get('default_humidity_uncertainty_pct', 7.0)))
    rh = np.clip(np.asarray(profile.relative_humidity_pct, float), 5.0, 105.0)
    sigma_logq = np.clip(sigma_rh / rh, 0.0, 2.0)
    variance = 0.0
    if sim.jacobian_temperature is not None:
        jt = np.asarray(sim.jacobian_temperature[channel_index], float)
        if jt.size == sigma_t.size:
            variance += float(np.nansum(np.square(jt * sigma_t)))
    if sim.jacobian_logq is not None:
        jq = np.asarray(sim.jacobian_logq[channel_index], float)
        if jq.size == sigma_logq.size:
            variance += float(np.nansum(np.square(jq * sigma_logq)))
    return float(np.sqrt(max(variance, fallback**2 * 0.04))) if variance > 0 else fallback


def _surface_label(value):
    if value is None: return 'unknown'
    try:
        v = int(value)
        return {0:'land',1:'sea',2:'sea_ice'}.get(v, str(v))
    except Exception:
        return str(value)


def build_pairs(workspace: Workspace, instrument: str, rtm, instrument_cfg: dict) -> pd.DataFrame:
    with workspace.connect() as db:
        matches = db.execute("SELECT * FROM matchups WHERE instrument=? ORDER BY id", (instrument,)).fetchall()
        sound = {r['id']: r for r in db.execute("SELECT * FROM soundings").fetchall()}
        granules = {r['id']: r for r in db.execute("SELECT * FROM satellite_granules").fetchall()}
    rows = []
    selected = instrument_cfg.get('channels') or None
    for m in matches:
        profile = workspace.load_sounding_row(sound[m['sounding_id']])
        gran = workspace.load_granule_row(granules[m['granule_id']])
        idx = int(m['fov_index'])
        if selected:
            channel_mask = np.isin(gran.channels.astype(int), np.asarray(selected, int))
        else:
            channel_mask = np.ones(len(gran.channels), dtype=bool)
        channels = gran.channels[channel_mask]
        if not len(channels): continue
        sim = rtm.simulate(profile, gran, idx, channels)
        obs = gran.brightness_temperature_k[idx, channel_mask]
        q = rh_to_specific_humidity(profile.pressure_hpa, profile.temperature_k, profile.relative_humidity_pct)
        tpw = total_precipitable_water_mm(profile.pressure_hpa, q)
        epoch = float(gran.observation_time_epoch_s[idx])
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        scan = float(gran.scan_position[idx]); zen = float(gran.satellite_zenith_deg[idx])
        orbit = str(gran.metadata.get('orbit_direction', 'unknown'))
        surface = _surface_label(gran.surface_type[idx] if gran.surface_type is not None else None)
        solar_zen = float(gran.solar_zenith_deg[idx]) if gran.solar_zenith_deg is not None else np.nan
        daynight = 'day' if np.isfinite(solar_zen) and solar_zen < 90 else 'night'
        instrument_noise = float(instrument_cfg.get('instrument_noise_k', 0.35))
        colloc_unc = float(instrument_cfg.get('collocation_floor_k', 0.10)) + 0.005 * float(m['mean_distance_km']) + 0.0005 * abs(float(m['time_offset_s']))
        for j, ch in enumerate(channels):
            if not np.isfinite(obs[j]) or not np.isfinite(sim.brightness_temperature_k[j]): continue
            sonde_unc = _sonde_radiance_uncertainty(profile, sim, j, instrument_cfg)
            rtm_unc = float(sim.simulation_uncertainty_k[j])
            total_unc = np.sqrt(instrument_noise**2 + rtm_unc**2 + colloc_unc**2 + sonde_unc**2)
            rows.append({'matchup_id':int(m['id']),'sounding_id':int(m['sounding_id']),'granule_id':int(m['granule_id']),'fov_index':idx,'station_id':profile.station_id,'datetime':dt.isoformat(),'date':dt.date().isoformat(),'month':dt.month,'month_sin':float(np.sin(2*np.pi*dt.month/12.0)),'month_cos':float(np.cos(2*np.pi*dt.month/12.0)),'channel':str(ch),'obs_tb_k':float(obs[j]),'sim_tb_k':float(sim.brightness_temperature_k[j]),'innovation_k':float(obs[j]-sim.brightness_temperature_k[j]),'uncertainty_k':float(total_unc),'scan':scan,'scan2':scan*scan,'scan3':scan*scan*scan,'secant_zenith_minus_1':float(1/max(np.cos(np.deg2rad(zen)),0.2)-1),'satellite_zenith_deg':zen,'latitude':float(gran.latitude[idx]),'longitude':float(gran.longitude[idx]),'tpw_mm':tpw,'orbit':orbit,'surface':surface,'daynight':daynight,'mean_distance_km':float(m['mean_distance_km']),'time_offset_s':float(m['time_offset_s']),'trajectory_known':int(m['trajectory_known']),'instrument_uncertainty_k':instrument_noise,'rtm_uncertainty_k':rtm_unc,'sonde_uncertainty_k':sonde_unc,'collocation_uncertainty_k':colloc_unc})
    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv(workspace.root/'matchups'/f'{instrument}_radiance_pairs.csv.gz', index=False, compression='gzip')
    return df
