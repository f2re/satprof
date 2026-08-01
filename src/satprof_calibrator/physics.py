from __future__ import annotations
import numpy as np

EARTH_RADIUS_KM = 6371.0088
RD = 287.05
RV = 461.5
EPSILON = RD / RV
G = 9.80665


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.asarray, (lat1, lon1, lat2, lon2))
    p1 = np.deg2rad(lat1)
    p2 = np.deg2rad(lat2)
    dp = np.deg2rad(lat2 - lat1)
    dl = np.deg2rad(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arctan2(np.sqrt(a), np.sqrt(np.maximum(1 - a, 0)))


def wind_components(direction_deg, speed_ms):
    direction = np.deg2rad(np.asarray(direction_deg, dtype=float))
    speed = np.asarray(speed_ms, dtype=float)
    u = -speed * np.sin(direction)
    v = -speed * np.cos(direction)
    return u, v


def dewpoint_to_rh(temp_k, dewpoint_k):
    tc = np.asarray(temp_k) - 273.15
    tdc = np.asarray(dewpoint_k) - 273.15
    es = 6.112 * np.exp(17.67 * tc / (tc + 243.5))
    e = 6.112 * np.exp(17.67 * tdc / (tdc + 243.5))
    return np.clip(100.0 * e / es, 0, 105)


def rh_to_specific_humidity(pressure_hpa, temp_k, rh_pct):
    tc = np.asarray(temp_k) - 273.15
    p = np.asarray(pressure_hpa)
    rh = np.asarray(rh_pct) / 100.0
    es = 6.112 * np.exp(17.67 * tc / (tc + 243.5))
    e = np.clip(rh * es, 0, 0.99 * p)
    return EPSILON * e / (p - (1 - EPSILON) * e)


def specific_humidity_to_rh(pressure_hpa, temp_k, q):
    p = np.asarray(pressure_hpa)
    q = np.asarray(q)
    e = q * p / (EPSILON + (1 - EPSILON) * q)
    tc = np.asarray(temp_k) - 273.15
    es = 6.112 * np.exp(17.67 * tc / (tc + 243.5))
    return np.clip(100 * e / es, 0, 105)


def integrate_balloon_trajectory(station_lat, station_lon, elapsed_s, u_ms, v_ms):
    elapsed_s = np.asarray(elapsed_s, dtype=float)
    u = np.nan_to_num(np.asarray(u_ms, dtype=float), nan=0.0)
    v = np.nan_to_num(np.asarray(v_ms, dtype=float), nan=0.0)
    order = np.argsort(np.nan_to_num(elapsed_s, nan=0.0))
    t = elapsed_s[order]
    u = u[order]
    v = v[order]
    dt = np.diff(t, prepend=t[0])
    dt = np.clip(dt, 0, 900)
    east_m = np.cumsum(u * dt)
    north_m = np.cumsum(v * dt)
    lat = station_lat + np.rad2deg(north_m / (EARTH_RADIUS_KM * 1000.0))
    coslat = max(np.cos(np.deg2rad(station_lat)), 1e-3)
    lon = station_lon + np.rad2deg(east_m / (EARTH_RADIUS_KM * 1000.0 * coslat))
    out_lat = np.empty_like(lat)
    out_lon = np.empty_like(lon)
    out_lat[order] = lat
    out_lon[order] = lon
    return out_lat, out_lon


def interpolate_log_pressure(p_src_hpa, values, p_dst_hpa):
    p = np.asarray(p_src_hpa, dtype=float)
    v = np.asarray(values, dtype=float)
    dst = np.asarray(p_dst_hpa, dtype=float)
    mask = np.isfinite(p) & np.isfinite(v) & (p > 0)
    if mask.sum() < 2:
        return np.full(dst.shape, np.nan)
    xp = np.log(p[mask])
    fp = v[mask]
    order = np.argsort(xp)
    result = np.interp(np.log(dst), xp[order], fp[order], left=np.nan, right=np.nan)
    lo, hi = p[mask].min(), p[mask].max()
    result[(dst < lo) | (dst > hi)] = np.nan
    return result


def total_precipitable_water_mm(pressure_hpa, q_kgkg):
    p = np.asarray(pressure_hpa, dtype=float) * 100.0
    q = np.asarray(q_kgkg, dtype=float)
    mask = np.isfinite(p) & np.isfinite(q)
    if mask.sum() < 2:
        return np.nan
    order = np.argsort(p[mask])
    return float(np.trapezoid(q[mask][order], p[mask][order]) / G)
