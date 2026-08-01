from datetime import datetime, timezone
import numpy as np
from satprof_calibrator.schemas import SoundingProfile, SatelliteGranule
from satprof_calibrator.storage import Workspace
from satprof_calibrator.qc import check_sounding, check_satellite
from satprof_calibrator.collocation import collocate

def test_collocation(tmp_path):
    ws=Workspace(tmp_path); ws.init(); p=np.array([1000,850,500,100]); n=len(p)
    prof=SoundingProfile('x','1',datetime(2026,1,1,tzinfo=timezone.utc),50,10,p,np.array([290,280,255,220]),np.array([70,60,40,10]),np.array([0,1500,5500,16000]),np.zeros(n),np.zeros(n),np.array([0,300,900,2400]),np.full(n,50.),np.full(n,10.),metadata={'trajectory':'measured'})
    q=check_sounding(prof,{'min_levels':4}); ws.store_sounding(prof,{'passed':q.passed,'flags':q.flags,'metrics':q.metrics})
    g=SatelliteGranule('i','s','g',np.array([1]),np.array([datetime(2026,1,1,tzinfo=timezone.utc).timestamp()+900]),np.array([50.1]),np.array([10.1]),np.array([0.]),np.array([0.]),np.array([[250.]]))
    q=check_satellite(g); ws.store_granule(g,{'passed':q.passed,'flags':q.flags,'metrics':q.metrics})
    df=collocate(ws,'i',{'max_distance_km':100,'max_time_minutes':90,'min_profile_top_hpa':100},{'max_candidates_per_sounding':1,'distance_scale_km':50,'time_scale_minutes':45})
    assert len(df)==1
