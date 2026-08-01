from __future__ import annotations
from datetime import datetime, timedelta, timezone
import numpy as np
from .schemas import SoundingProfile, SatelliteGranule
from .storage import Workspace
from .qc import check_sounding, check_satellite
from .physics import specific_humidity_to_rh, integrate_balloon_trajectory
from .rtm import DemoWeightedRTM
from .collocation import collocate
from .pairs import build_pairs
from .calibration import BiasCalibrator, apply_bias_model
from .retrieval import train_and_save
from .reporting import generate_report
from .statistics import update_statistics


def generate_demo(workspace: Workspace, n_soundings=120, seed=42):
    rng=np.random.default_rng(seed); workspace.init(); rtm=DemoWeightedRTM()
    p=np.array([1000,925,850,700,600,500,400,300,250,200,150,100,70,50,30,20,10],float)
    channels=np.arange(1,9)
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    for i in range(n_soundings):
        launch=start+timedelta(hours=12*i)
        lat0=45+8*np.sin(i/17); lon0=10+12*np.cos(i/19)
        z=8000*np.log(1000/p)
        seasonal=6*np.sin(2*np.pi*i/max(n_soundings,1))
        t=np.where(z <= 11000, 288.15-0.0065*z, np.where(z <= 20000, 216.65, 216.65+0.0010*(z-20000)))
        t=t+seasonal*np.exp(-z/9000)+rng.normal(0,0.6,len(p))
        q=0.012*np.exp(-z/2300)*(0.8+0.4*rng.random())
        rh=specific_humidity_to_rh(p,t,q)
        u=8+5*np.sin(z/5000+i/13); v=4+4*np.cos(z/4000-i/11)
        elapsed=z/5.2
        lat,lon=integrate_balloon_trajectory(lat0,lon0,elapsed,u,v)
        prof=SoundingProfile('demo','D%04d'%i,launch,lat0,lon0,p,t,rh,z,u,v,elapsed,lat,lon,metadata={'trajectory':'measured'})
        qc=check_sounding(prof); workspace.store_sounding(prof,{'passed':qc.passed,'flags':qc.flags,'metrics':qc.metrics})
        n_fov=5; obs_time=launch.timestamp()+elapsed[np.argmin(abs(p-500))]+rng.normal(0,300,n_fov)
        fov_lat=lat[np.argmin(abs(p-500))]+rng.normal(0,0.25,n_fov); fov_lon=lon[np.argmin(abs(p-500))]+rng.normal(0,0.25,n_fov)
        scan=np.linspace(-0.8,0.8,n_fov); zen=abs(scan)*55; dummy=np.zeros((n_fov,len(channels)))
        gran=SatelliteGranule('demo_sounder','DEMO-1',f'g{i:04d}',channels,obs_time,fov_lat,fov_lon,scan,zen,dummy,
                              solar_zenith_deg=np.full(n_fov,60 if i%2==0 else 110),surface_type=np.array([1,1,0,0,1]),metadata={'orbit_direction':'ascending' if i%2==0 else 'descending'})
        for f in range(n_fov):
            sim=rtm.simulate(prof,gran,f,channels).brightness_temperature_k
            drift=0.003*i
            bias=0.7+0.45*scan[f]-0.25*scan[f]**2+0.12*(1/max(np.cos(np.deg2rad(zen[f])),0.2)-1)+drift
            gran.brightness_temperature_k[f]=sim+bias+rng.normal(0,0.25,len(channels))
        qc2=check_satellite(gran); workspace.store_granule(gran,{'passed':qc2.passed,'flags':qc2.flags,'metrics':qc2.metrics})
    instrument_cfg={'max_distance_km':100,'max_time_minutes':90,'min_profile_top_hpa':100,'channels':channels.tolist(),'instrument_noise_k':0.25}
    colloc_cfg={'max_candidates_per_sounding':1,'distance_scale_km':50,'time_scale_minutes':45,'unknown_trajectory_penalty':0.3}
    c=collocate(workspace,'demo_sounder',instrument_cfg,colloc_cfg)
    pairs=build_pairs(workspace,'demo_sounder',rtm,instrument_cfg)
    cal_cfg={'min_pairs_per_channel':50,'min_independent_soundings':40,'validation_fraction':0.2,'huber_epsilon':1.35,'max_abs_residual_bias_k':0.25,'min_rmse_improvement_fraction':0.01,'kalman_process_variance_k2_per_day':0.0004}
    fit=BiasCalibrator(cal_cfg).fit(workspace,'demo_sounder',pairs)
    corrected=apply_bias_model(pairs,fit.path)
    corrected.to_csv(workspace.root/'matchups'/'demo_sounder_corrected_pairs.csv.gz',index=False,compression='gzip')
    ret_cfg={'min_soundings':40,'pca_temperature_components':5,'pca_logq_components':5,'ridge_alphas':[0.1,1,10,100]}
    retrieval=train_and_save(workspace,'demo_sounder',corrected,p,ret_cfg)
    health=update_statistics(workspace,'demo_sounder',corrected,{'drift_alarm_k':0.8})
    report=generate_report(workspace,'demo_sounder',pairs,corrected,fit.metrics,retrieval.metrics)
    return {'collocations':len(c),'pairs':len(pairs),'bias':fit,'retrieval':retrieval,'health':health,'report':report}
