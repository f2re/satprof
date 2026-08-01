from __future__ import annotations
from datetime import datetime, timezone
import json
import numpy as np
import pandas as pd
from .physics import haversine_km
from .storage import Workspace


def _layer_weights(pressure_hpa):
    p = np.asarray(pressure_hpa, dtype=float)
    w = np.zeros_like(p)
    mask = np.isfinite(p) & (p >= 100) & (p <= 1000)
    if mask.any():
        # Равномерный вес в log(p), с умеренным акцентом на тропосферу.
        w[mask] = np.exp(-((np.log(p[mask]) - np.log(500)) / 1.1) ** 2)
        w[mask] /= w[mask].sum()
    return w


def collocate(workspace: Workspace, instrument: str, instrument_cfg: dict, colloc_cfg: dict) -> pd.DataFrame:
    max_distance = float(instrument_cfg.get('max_distance_km', 100))
    max_time_s = float(instrument_cfg.get('max_time_minutes', 90)) * 60
    min_top = float(instrument_cfg.get('min_profile_top_hpa', 100))
    require_clear = bool(instrument_cfg.get('require_clear', False))
    max_cloud_fraction = float(instrument_cfg.get('max_cloud_fraction', 0.05))
    edge_fraction = float(instrument_cfg.get('exclude_scan_edge_fraction', 0.0))
    scan_limit = max(0.0, 1.0 - 2.0 * edge_fraction)
    max_candidates = int(colloc_cfg.get('max_candidates_per_sounding', 3))
    dscale = float(colloc_cfg.get('distance_scale_km', 50))
    tscale = float(colloc_cfg.get('time_scale_minutes', 45)) * 60
    unknown_penalty = float(colloc_cfg.get('unknown_trajectory_penalty', 0.3))
    rows = []
    sound_rows = workspace.sounding_rows()
    gran_rows = workspace.granule_rows(instrument)
    for srow in sound_rows:
        profile = workspace.load_sounding_row(srow)
        if np.nanmin(profile.pressure_hpa) > min_top:
            continue
        w = _layer_weights(profile.pressure_hpa)
        if w.sum() == 0:
            continue
        level_lat = profile.latitude if profile.latitude is not None and len(profile.latitude) == len(w) else np.full(len(w), profile.station_lat)
        level_lon = profile.longitude if profile.longitude is not None and len(profile.longitude) == len(w) else np.full(len(w), profile.station_lon)
        trajectory_known = profile.metadata.get('trajectory') in ('measured', 'bufr_displacement')
        representative_epoch = float(np.sum(profile.level_times_epoch_s * w))
        candidates = []
        for grow in gran_rows:
            start = datetime.fromisoformat(grow['start_time']).timestamp()
            end = datetime.fromisoformat(grow['end_time']).timestamp()
            if representative_epoch < start - max_time_s or representative_epoch > end + max_time_s:
                continue
            gran = workspace.load_granule_row(grow)
            # Быстрый предварительный отбор по центру траектории.
            center_lat = float(np.sum(level_lat * w)); center_lon = float(np.sum(level_lon * w))
            dist_center = haversine_km(center_lat, center_lon, gran.latitude, gran.longitude)
            time_diff = np.abs(gran.observation_time_epoch_s - representative_epoch)
            preliminary = (dist_center <= max_distance * 1.5) & (time_diff <= max_time_s)
            if gran.quality_flag is not None:
                preliminary &= np.asarray(gran.quality_flag).reshape(-1) == 0
            if edge_fraction > 0:
                preliminary &= np.abs(gran.scan_position) <= scan_limit
            if require_clear:
                if gran.cloud_fraction is None:
                    # Без признака облачности ИК-наблюдение нельзя считать заведомо ясным.
                    preliminary &= False
                else:
                    preliminary &= np.asarray(gran.cloud_fraction).reshape(-1) <= max_cloud_fraction
            idxs = np.where(preliminary)[0]
            for idx in idxs:
                layer_dist = haversine_km(level_lat, level_lon, gran.latitude[idx], gran.longitude[idx])
                mean_dist = float(np.sum(layer_dist * w))
                max_dist = float(np.nanquantile(layer_dist[w > 0], 0.95))
                dt = float(gran.observation_time_epoch_s[idx] - representative_epoch)
                score = np.sqrt((mean_dist / dscale) ** 2 + (dt / tscale) ** 2)
                if not trajectory_known:
                    score += unknown_penalty
                if mean_dist <= max_distance and abs(dt) <= max_time_s:
                    candidates.append((score, grow, idx, mean_dist, max_dist, dt, trajectory_known))
        for score, grow, idx, mean_dist, max_dist, dt, known in sorted(candidates, key=lambda x: x[0])[:max_candidates]:
            rows.append({
                'instrument': instrument, 'sounding_id': int(srow['id']), 'granule_id': int(grow['id']), 'fov_index': int(idx),
                'mean_distance_km': mean_dist, 'max_distance_km': max_dist, 'time_offset_s': dt,
                'normalized_score': score, 'trajectory_known': int(known),
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        now = datetime.now(timezone.utc).isoformat()
        with workspace.connect() as db:
            for r in df.to_dict('records'):
                db.execute(
                    """INSERT OR REPLACE INTO matchups(instrument,sounding_id,granule_id,fov_index,mean_distance_km,max_distance_km,time_offset_s,normalized_score,trajectory_known,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (r['instrument'], r['sounding_id'], r['granule_id'], r['fov_index'], r['mean_distance_km'], r['max_distance_km'], r['time_offset_s'], r['normalized_score'], r['trajectory_known'], now)
                )
        out = workspace.root / 'matchups' / f'{instrument}_collocations.csv.gz'
        df.to_csv(out, index=False, compression='gzip')
    return df
