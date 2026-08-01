from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd
import xarray as xr


def export_radiances(corrected_pairs: pd.DataFrame, output: str | Path, instrument: str, calibration_version: str):
    df=corrected_pairs.sort_values(['matchup_id','channel']); matchups=np.sort(df.matchup_id.unique()); channels=np.sort(df.channel.astype(str).unique()); index_m={v:i for i,v in enumerate(matchups)}; index_c={v:i for i,v in enumerate(channels)}; shape=(len(matchups),len(channels)); obs=np.full(shape,np.nan); corrected=np.full(shape,np.nan); error=np.full(shape,np.nan); qc=np.ones(shape,dtype=np.int16); lat=np.full(len(matchups),np.nan); lon=np.full(len(matchups),np.nan); time=np.full(len(matchups),np.nan); zen=np.full(len(matchups),np.nan); scan=np.full(len(matchups),np.nan)
    for r in df.itertuples():
        i=index_m[r.matchup_id]; j=index_c[str(r.channel)]; obs[i,j]=r.obs_tb_k; corrected[i,j]=r.corrected_tb_k; error[i,j]=r.uncertainty_k; qc[i,j]=0; time[i]=pd.Timestamp(r.datetime).timestamp(); zen[i]=r.satellite_zenith_deg; scan[i]=r.scan
        if hasattr(r,'latitude'): lat[i]=r.latitude
        if hasattr(r,'longitude'): lon[i]=r.longitude
    ds=xr.Dataset(data_vars={'brightness_temperature_raw':(('observation','channel'),obs),'brightness_temperature_corrected':(('observation','channel'),corrected),'observation_error':(('observation','channel'),error),'quality_flag':(('observation','channel'),qc),'latitude':(('observation',),lat),'longitude':(('observation',),lon),'observation_time':(('observation',),time),'satellite_zenith':(('observation',),zen),'scan_position':(('observation',),scan)},coords={'observation':np.arange(len(matchups)),'channel':channels},attrs={'instrument':instrument,'calibration_version':calibration_version,'conventions':'SatProf custom WRFDA reader schema v1'}); ds.to_netcdf(output,engine='scipy')
