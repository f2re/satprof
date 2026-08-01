from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .physics import rh_to_specific_humidity
from .schemas import SoundingProfile, SatelliteGranule


@dataclass
class RTMResult:
    channels: np.ndarray
    brightness_temperature_k: np.ndarray
    simulation_uncertainty_k: np.ndarray
    jacobian_temperature: np.ndarray | None = None
    jacobian_logq: np.ndarray | None = None


class BaseRTM:
    def simulate(self, profile: SoundingProfile, granule: SatelliteGranule, fov_index: int, channels: np.ndarray) -> RTMResult:
        raise NotImplementedError


class DemoWeightedRTM(BaseRTM):
    """Только для тестов архитектуры. Не использовать для физических выводов."""
    def simulate(self, profile, granule, fov_index, channels):
        p=profile.pressure_hpa; t=profile.temperature_k; q=rh_to_specific_humidity(p,t,profile.relative_humidity_pct); out=[]; jt=[]; jq=[]
        for channel in channels:
            c=float(channel); center=np.exp(np.interp((c%11)/10,[0,1],[np.log(850),np.log(80)])); width=0.32+0.06*(c%3); wt=np.exp(-0.5*((np.log(p)-np.log(center))/width)**2); wt=np.where(np.isfinite(t),wt,0); wt=wt/wt.sum() if wt.sum() else np.zeros_like(wt); humid=np.exp(-0.5*((np.log(p)-np.log(max(center,250)))/0.5)**2); humid=humid/humid.sum() if humid.sum() else np.zeros_like(humid); tb=np.nansum(wt*t)-(1.5+0.1*(c%5))*np.nansum(humid*q*1000); out.append(tb); jt.append(wt); jq.append(-(1.5+0.1*(c%5))*humid*q*1000)
        return RTMResult(np.asarray(channels),np.asarray(out),np.full(len(channels),0.25),np.asarray(jt),np.asarray(jq))


class PyRttovRTM(BaseRTM):
    def __init__(self, coefficient_file: str, channels: list[int], add_interpolation: bool = True):
        self.coefficient_file=coefficient_file; self.channels=list(map(int,channels)); self.add_interpolation=add_interpolation

    def simulate(self, profile, granule, fov_index, channels):
        try: import pyrttov
        except ImportError as exc: raise RuntimeError('pyrttov не найден. Установите RTTOV NWP SAF и его Python wrapper.') from exc
        channels=list(map(int,channels)); p=np.asarray(profile.pressure_hpa,float); order=np.argsort(p); p=p[order]; t=np.asarray(profile.temperature_k,float)[order]; q=rh_to_specific_humidity(profile.pressure_hpa,profile.temperature_k,profile.relative_humidity_pct)[order]; valid=np.isfinite(p)&np.isfinite(t)&np.isfinite(q); p,t,q=p[valid],t[valid],q[valid]; rttov=pyrttov.Rttov(); rttov.FileCoef=self.coefficient_file; rttov.Options.AddInterp=self.add_interpolation; rttov.Options.StoreRad=True; rttov.loadInst(channels); profs=pyrttov.Profiles(1,len(p)); profs.GasUnits=1; profs.P=p.reshape(1,-1); profs.T=t.reshape(1,-1); profs.Q=q.reshape(1,-1); zen=float(granule.satellite_zenith_deg[fov_index]); saz=float(granule.satellite_azimuth_deg[fov_index]) if granule.satellite_azimuth_deg is not None else 0.0; solzen=float(granule.solar_zenith_deg[fov_index]) if granule.solar_zenith_deg is not None else 90.0; profs.Angles=np.array([[zen,saz,solzen,0.0]]); surface_t=float(t[-1]); profs.S2m=np.array([[p[-1],t[-1],q[-1],3.0,0.0,100000.0]]); profs.Skin=np.array([[surface_t,35.0,0.0,0.0,3.0,5.0,15.0,0.1,0.3]]); surface_type=0
        if granule.surface_type is not None:
            try: surface_type=int(granule.surface_type[fov_index])
            except Exception: surface_type=0
        profs.SurfType=np.array([[surface_type,0]]); profs.DateTimes=np.array([[1970,1,1,0,0,0]]); rttov.Profiles=profs; rttov.runDirect(); bt=np.asarray(rttov.Bt).reshape(-1); return RTMResult(np.asarray(channels),bt,np.full(len(channels),0.20))
