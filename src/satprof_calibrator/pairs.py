from __future__ import annotations
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from .storage import Workspace
from .physics import rh_to_specific_humidity, total_precipitable_water_mm


def _sonde_radiance_uncertainty(profile, sim, channel_index: int, cfg: dict) -> float:
    fallback = float(cfg.get("default_sonde_radiance_uncertainty_k", 0.35))
    if sim.jacobian_temperature is None and sim.jacobian_logq is None:
        return fallback
    n = len(profile.pressure_hpa)
    sigma_t = (
        np.full(n, float(cfg.get("default_temperature_uncertainty_k", 0.5)))
        if profile.temperature_uncertainty_k is None
        else np.nan_to_num(np.asarray(profile.temperature_uncertainty_k, float), nan=float(cfg.get("default_temperature_uncertainty_k", 0.5)))
    )
    sigma_rh = (
        np.full(n, float(cfg.get("default_humidity_uncertainty_pct", 7.0)))
        if profile.humidity_uncertainty_pct is None
        else np.nan_to_num(np.asarray(profile.humidity_uncertainty_pct, float), nan=float(cfg.get("default_humidity_uncertainty_pct", 7.0)))
    )
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
    if value is None:
        return "unknown"
    try:
        number = int(value)
        return {0: "land", 1: "sea", 2: "sea_ice"}.get(number, str(number))
    except Exception:
        return str(value)


def build_pairs(workspace: Workspace, instrument: str, rtm, instrument_cfg: dict) -> pd.DataFrame:
    with workspace.connect() as database:
        matches = database.execute("SELECT * FROM matchups WHERE instrument=? ORDER BY id", (instrument,)).fetchall()
        soundings = {row["id"]: row for row in database.execute("SELECT * FROM soundings").fetchall()}
        granules = {row["id"]: row for row in database.execute("SELECT * FROM satellite_granules").fetchall()}
    rows: list[dict] = []
    selected = instrument_cfg.get("channels") or None
    for matchup in matches:
        profile = workspace.load_sounding_row(soundings[matchup["sounding_id"]])
        granule = workspace.load_granule_row(granules[matchup["granule_id"]])
        fov_index = int(matchup["fov_index"])
        if selected:
            try:
                channel_mask = np.isin(granule.channels.astype(int), np.asarray(selected, int))
            except (TypeError, ValueError):
                channel_mask = np.isin(granule.channels.astype(str), np.asarray(selected).astype(str))
        else:
            channel_mask = np.ones(len(granule.channels), dtype=bool)
        channels = granule.channels[channel_mask]
        if not len(channels):
            continue
        simulation = rtm.simulate(profile, granule, fov_index, channels)
        observed_source = granule.calibrated_brightness_temperature_k if granule.calibrated_brightness_temperature_k is not None else granule.brightness_temperature_k
        observed = np.asarray(observed_source[fov_index, channel_mask], dtype=float)
        raw = np.asarray(granule.raw_counts[fov_index, channel_mask], dtype=float) if granule.raw_counts is not None else np.full(len(channels), np.nan)
        specific_humidity = rh_to_specific_humidity(profile.pressure_hpa, profile.temperature_k, profile.relative_humidity_pct)
        tpw = total_precipitable_water_mm(profile.pressure_hpa, specific_humidity)
        epoch = float(granule.observation_time_epoch_s[fov_index])
        date_time = datetime.fromtimestamp(epoch, tz=timezone.utc)
        scan = float(granule.scan_position[fov_index])
        zenith = float(granule.satellite_zenith_deg[fov_index])
        orbit = str(granule.metadata.get("orbit_direction", "unknown"))
        surface = _surface_label(granule.surface_type[fov_index] if granule.surface_type is not None else None)
        solar_zenith = float(granule.solar_zenith_deg[fov_index]) if granule.solar_zenith_deg is not None else np.nan
        daynight = "day" if np.isfinite(solar_zenith) and solar_zenith < 90 else "night"
        collocation_uncertainty = float(instrument_cfg.get("collocation_floor_k", 0.10)) + 0.005 * float(matchup["mean_distance_km"]) + 0.0005 * abs(float(matchup["time_offset_s"]))
        for channel_index, channel in enumerate(channels):
            simulated = float(simulation.brightness_temperature_k[channel_index])
            if not np.isfinite(simulated):
                continue
            has_tb = np.isfinite(observed[channel_index])
            has_count = np.isfinite(raw[channel_index])
            if not has_tb and not has_count:
                continue
            sonde_uncertainty = _sonde_radiance_uncertainty(profile, simulation, channel_index, instrument_cfg)
            rtm_uncertainty = float(simulation.simulation_uncertainty_k[channel_index])
            measurement_uncertainty = float(instrument_cfg.get("instrument_noise_k" if has_tb else "radiometric_target_uncertainty_k", 0.35 if has_tb else 0.50))
            total_uncertainty = np.sqrt(measurement_uncertainty**2 + rtm_uncertainty**2 + collocation_uncertainty**2 + sonde_uncertainty**2)
            rows.append({
                "matchup_id": int(matchup["id"]), "sounding_id": int(matchup["sounding_id"]), "granule_id": int(matchup["granule_id"]), "fov_index": fov_index,
                "station_id": profile.station_id, "datetime": date_time.isoformat(), "date": date_time.date().isoformat(), "month": date_time.month,
                "month_sin": float(np.sin(2 * np.pi * date_time.month / 12.0)), "month_cos": float(np.cos(2 * np.pi * date_time.month / 12.0)),
                "channel": str(channel), "measurement_kind": "brightness_temperature" if has_tb else "raw_counts",
                "raw_count": float(raw[channel_index]) if has_count else np.nan, "obs_tb_k": float(observed[channel_index]) if has_tb else np.nan,
                "sim_tb_k": simulated, "innovation_k": float(observed[channel_index] - simulated) if has_tb else np.nan,
                "uncertainty_k": float(total_uncertainty), "scan": scan, "scan2": scan * scan, "scan3": scan * scan * scan,
                "secant_zenith_minus_1": float(1 / max(np.cos(np.deg2rad(zenith)), 0.2) - 1), "satellite_zenith_deg": zenith,
                "latitude": float(granule.latitude[fov_index]), "longitude": float(granule.longitude[fov_index]), "tpw_mm": tpw,
                "orbit": orbit, "surface": surface, "daynight": daynight, "mean_distance_km": float(matchup["mean_distance_km"]),
                "time_offset_s": float(matchup["time_offset_s"]), "trajectory_known": int(matchup["trajectory_known"]),
                "instrument_uncertainty_k": measurement_uncertainty, "rtm_uncertainty_k": rtm_uncertainty,
                "sonde_uncertainty_k": sonde_uncertainty, "collocation_uncertainty_k": collocation_uncertainty,
            })
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame.to_csv(workspace.root / "matchups" / f"{instrument}_radiance_pairs.csv.gz", index=False, compression="gzip")
    return frame
