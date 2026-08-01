import numpy as np
import pandas as pd
from satprof_calibrator.calibration import BiasCalibrator, apply_bias_model
from satprof_calibrator.storage import Workspace

def test_bias_reduces_rmse(tmp_path):
    rng=np.random.default_rng(1); n=300; scan=rng.uniform(-1,1,n); sim=250+rng.normal(0,8,n); bias=0.8+0.5*scan-0.2*scan**2
    obs=sim+bias+rng.normal(0,0.2,n)
    dates=pd.date_range('2026-01-01',periods=n,freq='12h')
    df=pd.DataFrame({'channel':'1','sounding_id':np.arange(n),'date':dates.date.astype(str),'datetime':dates.astype(str),'innovation_k':obs-sim,'obs_tb_k':obs,'sim_tb_k':sim,'uncertainty_k':0.4,'scan':scan,'scan2':scan**2,'scan3':scan**3,'secant_zenith_minus_1':0.1,'tpw_mm':20,'orbit':'ascending','surface':'sea','daynight':'day'})
    ws=Workspace(tmp_path); ws.init(); result=BiasCalibrator({'min_pairs_per_channel':50,'min_independent_soundings':40,'validation_fraction':0.2,'min_rmse_improvement_fraction':0.01,'max_abs_residual_bias_k':0.25}).fit(ws,'i',df)
    out=apply_bias_model(df,result.path)
    assert np.sqrt(np.mean(out.corrected_innovation_k**2)) < np.sqrt(np.mean(df.innovation_k**2))*0.5
